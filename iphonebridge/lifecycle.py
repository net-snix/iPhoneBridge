"""Own the USB tunnel and native device session; never shared forwards.

Every instance (the app and each source checkout) keeps its records in the shared
data directory, so a session started anywhere is seen, attributed and recovered by
every other instance. Only recorded processes are ever signalled.
"""
from __future__ import annotations

from contextlib import contextmanager
import fcntl
import json
import os
from pathlib import Path
import re
import shlex
import socket
import subprocess
import time
import uuid

from . import deployment, settings, transport
from .mirror_protocol import Client
from .processes import describe_instance, listeners, port_held_message, process_identity
from .runtime import PATHS

ROOT = PATHS.root
WORK = PATHS.data
STATE = WORK / "state.json"
REMOTE = deployment.REMOTE
MIRROR_PORT = 15901
DEVICE_PORT = 15901
PEER = transport.PEER
TOKEN = re.compile(r"[0-9a-f]{32}")


@contextmanager
def locked():
    WORK.mkdir(parents=True, exist_ok=True, mode=0o700)
    fd = os.open(WORK / "lifecycle.lock", os.O_CREAT | os.O_RDWR, 0o600)
    with os.fdopen(fd, "r+") as lock:
        deadline = time.monotonic() + 40
        while True:
            try:
                fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
                break
            except BlockingIOError:
                if time.monotonic() >= deadline:
                    raise RuntimeError("Another bridge lifecycle command is still running") from None
                time.sleep(0.05)
        try:
            _adopt_legacy_record()
            yield
        finally:
            fcntl.flock(lock, fcntl.LOCK_UN)


def state():
    try:
        return json.loads(STATE.read_text())
    except FileNotFoundError:
        return {}


def owned_process(entry):
    return bool(isinstance(entry, dict) and entry.get("identity")
                and process_identity(entry.get("pid")) == entry["identity"])


def recorded_processes(info):
    """Every entry shaped like a spawned process, in reverse start order: the USB forward goes last."""
    order = {"fixture_ssh": -2, "fixture_server": -1, "viewer": 1, "ssh": 2, "usb": 3}
    names = [name for name, entry in info.items()
             if isinstance(entry, dict) and type(entry.get("pid")) is int and entry.get("identity")]
    return sorted(names, key=lambda name: (order.get(name, 0), name))


def instance_root():
    """The path people know this instance by: the app bundle or the source checkout."""
    return str(PATHS.instance)


def describe_session(info):
    owner = info.get("owner") if isinstance(info.get("owner"), dict) else {}
    if owner.get("root"):
        return f"the bridge session started {owner.get('started')} from {describe_instance(owner['root'])}"
    try:
        written = time.strftime(" (record written %Y-%m-%d %H:%M:%S)", time.localtime(STATE.stat().st_mtime))
    except OSError:
        written = ""
    return "the bridge session recorded by an older bridge instance" + written


def started_here(info):
    owner = info.get("owner")
    return isinstance(owner, dict) and owner.get("root") == instance_root()


def save_state(info):
    WORK.mkdir(parents=True, exist_ok=True, mode=0o700)
    temporary = STATE.with_suffix(".new")
    temporary.write_text(json.dumps(info, indent=2) + "\n")
    temporary.chmod(0o600)
    temporary.replace(STATE)


def legacy_record():
    """A record that bridge code before 0.2.2 left in this checkout's work/, if one remains."""
    legacy = PATHS.legacy_data
    path = legacy / "state.json" if legacy else None
    return path if path is not None and path.exists() else None


def refuse_unadopted_legacy(info):
    path = legacy_record()
    if path:
        raise RuntimeError(f"{describe_session(info)} is recorded in {STATE}, and {path} still holds a "
                           "record left by older bridge code in this checkout; run stop twice first")


def _adopt_legacy_record():
    """Move an older checkout record into the shared directory, never while old code may be using it."""
    path = legacy_record()
    if not path:
        return
    with path.with_name("lifecycle.lock").open("a+") as held:
        try:
            fcntl.flock(held, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise RuntimeError(f"A bridge command from older code is still running in {PATHS.root}; "
                               "wait and retry") from None
        try:
            if STATE.exists():
                return  # Clearing the shared record (stop) comes first; the next command adopts this one.
            try:
                written = path.stat().st_mtime
                info = json.loads(path.read_text())
            except FileNotFoundError:
                return  # Older code finished its stop while this command waited for its lock.
            except (OSError, ValueError):
                info = None
            if not (isinstance(info, dict) and info.get("schema_version") == 2
                    and isinstance(info.get("token"), str) and TOKEN.fullmatch(info["token"])):
                raise RuntimeError(f"Unrecognized bridge record at {path}; stop it with the bridge code that wrote it")
            info["owner"] = {"root": instance_root(), "adopted": True,
                             "started": time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(written))}
            save_state(info)
            path.unlink()
        finally:
            fcntl.flock(held, fcntl.LOCK_UN)


def spawn(args, name, info, field):
    logs = WORK / "logs"
    logs.mkdir(exist_ok=True, mode=0o700)
    fd = os.open(logs / f"{name}.log", os.O_WRONLY | os.O_APPEND | os.O_CREAT, 0o600)
    with os.fdopen(fd, "ab") as log:
        process = subprocess.Popen(args, stdin=subprocess.DEVNULL, stdout=log,
                                   stderr=log, start_new_session=True, cwd=ROOT)
    entry = {"pid": process.pid, "identity": process_identity(process.pid)}
    info[field] = entry
    save_state(info)
    time.sleep(0.2)
    if process.poll() is not None:
        raise RuntimeError(f"{name} exited; inspect {logs / (name + '.log')}")
    if not entry["identity"]:
        raise RuntimeError(f"Cannot verify ownership of {name}; state retained")
    return entry


def available(port):
    with socket.socket() as sock:
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        try:
            sock.bind(("127.0.0.1", port))
        except OSError:
            try:
                held = listeners(port)
            except (RuntimeError, subprocess.SubprocessError):
                held = {}
            raise RuntimeError(port_held_message(
                port, held, f"Local port {port} is already in use; no existing process was changed")) from None


def usb_listeners():
    return listeners(transport.SSH_PORT)


def usb_owned(info):
    entry = info.get("usb")
    return owned_process(entry) and usb_listeners() == {
        entry["pid"]: {f"127.0.0.1:{transport.SSH_PORT}"}}


def ensure_usb(info):
    if usb_owned(info):
        return
    held = usb_listeners()
    if held:
        raise RuntimeError(port_held_message(
            transport.SSH_PORT, held,
            f"USB port {transport.SSH_PORT} belongs to an unverified process; nothing was stopped", "USB port"))
    connection = info["connection"]
    settings.select_device(transport.identifiers(PATHS), connection["udid"])
    available(transport.SSH_PORT)
    spawn([PATHS.tool("iproxy"), "-l", "-s", "127.0.0.1", "-u", connection["udid"],
           f"{transport.SSH_PORT}:22"], "usb", info, "usb")
    for _ in range(30):
        if usb_owned(info):
            return
        if not owned_process(info["usb"]):
            break
        time.sleep(0.1)
    raise RuntimeError("Bridge USB forward did not bind its owned loopback port")


def ssh_args(info=None):
    return transport.ssh_args((state() if info is None else info)["connection"])


def remote(command, connection=None):
    connection = state().get("connection") if connection is None else connection
    return transport.remote(command, connection)


def handshake():
    client = Client(timeout=3)
    client.close()
    return "IPBM/1"


def _new_session():
    config = settings.load(PATHS)
    udid = settings.select_device(transport.identifiers(PATHS), config["udid"])
    info = {"schema_version": 2, "token": uuid.uuid4().hex, "remote_started": False,
            "owner": {"root": instance_root(), "started": time.strftime("%Y-%m-%d %H:%M:%S")},
            "connection": transport.connection_snapshot(config, udid, PATHS)}
    save_state(info)
    ensure_usb(info)
    transport.verify_phone(info["connection"], PATHS)
    return info


def stop_unlocked():
    # Lock order is lifecycle then control; let in-flight input release first.
    from .control import _locked as control_locked
    with control_locked():
        return _stop_services()


def _stop_services():
    info = state()
    if not info:
        return {"stopped": True, "shared_tunnels_touched": False}
    if info.get("schema_version") != 2:
        raise RuntimeError("Legacy bridge state requires its original cleanup command; ownership retained")
    error = None
    if info.get("remote_started"):
        try:
            # Recover only the recorded physical-device route after disconnection.
            ensure_usb(info)
            remote(shlex.join([f"{REMOTE}/device-session.sh", "stop", info["token"]]), info["connection"])
        except (OSError, subprocess.SubprocessError, RuntimeError) as exc:
            error = exc
    local_errors = []
    for name in recorded_processes(info):
        entry = info[name]
        if not owned_process(entry):
            continue
        try:
            os.kill(entry["pid"], 15)
        except ProcessLookupError:
            continue
        for _ in range(30):
            if not owned_process(entry):
                break
            time.sleep(0.1)
        else:
            local_errors.append(name)
    if local_errors:
        raise RuntimeError(f"Owned processes did not exit: {', '.join(local_errors)}; state preserved")
    if error:
        detail = f": {error}" if isinstance(error, RuntimeError) else ""  # Bridge-authored text only.
        raise RuntimeError("Local services stopped; remote cleanup is pending" + detail
                           + "; reconnect USB and run stop to finish it") from error
    STATE.unlink()
    return {"stopped": True, "shared_tunnels_touched": False}


def stop(own_only=False):
    """End the recorded session; with own_only, only one this app or checkout started."""
    with locked():
        info = state()
        if own_only and info and not started_here(info):
            return {"stopped": False, "shared_tunnels_touched": False,
                    "kept": f"{describe_session(info)} was not started by {describe_instance(instance_root())}"}
        result = stop_unlocked()
        remaining = legacy_record()
        if remaining:
            result["remaining_record"] = str(remaining)
            result["next"] = "run stop again to clean the record left by older bridge code in this checkout"
        return result


def _recover(existing):
    """The recorded device session has ended; finish its cleanup before starting a new one."""
    try:
        stop_unlocked()
    except (OSError, subprocess.SubprocessError, RuntimeError) as error:
        raise RuntimeError(f"Recovering {describe_session(existing)} failed: {error}; its record was kept, "
                           "so run stop and connect again") from error


def _cleanup_failed_start():
    try:
        stop_unlocked()
    except (OSError, subprocess.SubprocessError, RuntimeError) as error:
        raise RuntimeError("Connection failed and cleanup is incomplete; reconnect USB and run stop") from error


def deploy():
    """Deploy verified artifacts with a temporary, bridge-owned USB connection."""
    with locked():
        existing = state()
        if existing:
            raise RuntimeError(f"Stop the bridge before deploying; {describe_session(existing)} is recorded")
        deployment.artifacts(PATHS)
        try:
            info = _new_session()
            result = deployment.deploy(info["connection"], PATHS)
        except BaseException:
            _cleanup_failed_start()
            raise
        stop_unlocked()
        return result


def connect():
    """Select USB device, validate, deploy and start the native mirror endpoint."""
    with locked():
        existing = state()
        if existing:
            result = status()
            if result["connected"]:
                return result
            refuse_unadopted_legacy(existing)
            if owned_process(existing.get("ssh")):
                raise RuntimeError(f"{describe_session(existing)} is still running; "
                                   "run stop (Reconnect in the app) to replace it")
            _recover(existing)
        deployment.artifacts(PATHS)
        available(MIRROR_PORT)
        try:
            info = _new_session()
            info.update(deployment.deploy(info["connection"], PATHS))
            info["remote_started"] = True
            save_state(info)  # Record remote intent before SSH can launch the daemon.
            command = shlex.join([f"{REMOTE}/device-session.sh", "run", info["token"], info["binary"]])
            spawn([*ssh_args(info), "-o", "ExitOnForwardFailure=yes", "-L",
                   f"127.0.0.1:{MIRROR_PORT}:127.0.0.1:{DEVICE_PORT}", PEER, "exec " + command],
                  "device", info, "ssh")
            for _ in range(30):
                try:
                    handshake()
                    break
                except (OSError, RuntimeError):
                    if not owned_process(info["ssh"]):
                        raise RuntimeError("Device daemon exited; inspect the app's device log")
                    time.sleep(0.2)
            else:
                raise RuntimeError("Native device mirror did not become ready")
        except BaseException:
            _cleanup_failed_start()
            raise
        return {**status(), "deployment": {key: info[key] for key in ("sha256", "source_commit", "script_sha256")}}


start = connect


def viewer_url():
    return "native://127.0.0.1:15901"


def status():
    info = state()
    config = settings.load(PATHS)
    services = {name: owned_process(info.get(name)) for name in ("usb", "ssh")}
    running = all(services.values())
    connected = False
    if running:
        try:
            connected = usb_owned(info) and bool(handshake())
        except (OSError, RuntimeError, subprocess.SubprocessError):
            pass
    remaining = legacy_record()
    return {"running": running, "connected": connected, "viewer": "native",
            "endpoint": "127.0.0.1:15901", "protocol": "IPBM/1" if connected else None,
            "owner": info.get("owner"),
            "selected_udid": info.get("connection", {}).get("udid") or config["udid"],
            "services": services, "settings": config, "data_directory": str(WORK),
            "config_path": str(WORK / "config.json"),
            **({"legacy_record": str(remaining)} if remaining else {})}


def devices():
    return transport.devices(PATHS)


def configure(**values):
    with locked():
        return settings.configure(paths=PATHS, **values)


def health():
    result = status()
    result["checks"] = {"mirror": {"ok": result["connected"]}}
    try:
        discovered = devices()
        result["devices"] = discovered["devices"]
        result["checks"]["usb"] = {"ok": bool(discovered["devices"])}
    except (OSError, subprocess.SubprocessError, RuntimeError) as error:
        result["checks"]["usb"] = {"ok": False, "detail": str(error)}
    connection = state().get("connection")
    if connection and result["services"]["usb"]:
        try:
            version = transport.verify_phone(connection, PATHS)
            result["checks"]["phone"] = {"ok": True, "ios": version}
        except (OSError, subprocess.SubprocessError, RuntimeError) as error:
            result["checks"]["phone"] = {"ok": False, "detail": str(error)}
    result["healthy"] = result["connected"] and all(item["ok"] for item in result["checks"].values())
    return result
