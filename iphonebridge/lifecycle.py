"""Own one USB tunnel, device session and local viewer; never shared forwards."""
from __future__ import annotations

from contextlib import contextmanager
import fcntl
import http.client
import json
import os
from pathlib import Path
import shlex
import socket
import subprocess
import time
import uuid

from . import deployment, settings, transport
from .runtime import PATHS

ROOT = PATHS.root
WORK = PATHS.data
STATE = WORK / "state.json"
REMOTE = deployment.REMOTE
VNC_PORT, VIEW_PORT = 15901, 15801
DEVICE_PORT = 15901
PEER = transport.PEER
run = transport.run


@contextmanager
def locked():
    WORK.mkdir(parents=True, exist_ok=True, mode=0o700)
    with (WORK / "lifecycle.lock").open("a+") as lock:
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
            yield
        finally:
            fcntl.flock(lock, fcntl.LOCK_UN)


def state():
    return json.loads(STATE.read_text()) if STATE.exists() else {}


def process_identity(pid):
    if type(pid) is not int or pid <= 1:
        return ""
    try:
        return run(["/bin/ps", "-p", str(pid), "-o", "lstart=,command="])
    except subprocess.CalledProcessError:
        return ""


def owned_process(entry):
    return bool(isinstance(entry, dict) and entry.get("identity")
                and process_identity(entry.get("pid")) == entry["identity"])


def save_state(info):
    WORK.mkdir(parents=True, exist_ok=True, mode=0o700)
    temporary = STATE.with_suffix(".new")
    temporary.write_text(json.dumps(info, indent=2) + "\n")
    temporary.chmod(0o600)
    temporary.replace(STATE)


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
            raise RuntimeError(f"Local port {port} is already in use; no existing process was changed") from None


def usb_listeners():
    result = subprocess.run(["/usr/sbin/lsof", "-nP", "-iTCP:" + str(transport.SSH_PORT),
                             "-sTCP:LISTEN", "-Fpn"], capture_output=True, text=True, timeout=5)
    if result.returncode == 1 and not result.stdout.strip():
        return {}
    if result.returncode != 0:
        raise RuntimeError("Cannot inspect ownership of the local USB port")
    listeners = {}
    pid = None
    for line in result.stdout.splitlines():
        if line.startswith("p"):
            pid = int(line[1:])
        elif line.startswith("n") and pid:
            listeners.setdefault(pid, set()).add(line[1:])
    return listeners


def usb_owned(info):
    entry = info.get("usb")
    return owned_process(entry) and usb_listeners() == {
        entry["pid"]: {f"127.0.0.1:{transport.SSH_PORT}"}}


def ensure_usb(info):
    if usb_owned(info):
        return
    if usb_listeners():
        raise RuntimeError("USB port 15422 belongs to an unverified process; nothing was stopped")
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
    with socket.create_connection(("127.0.0.1", VNC_PORT), timeout=3) as sock:
        data = b""
        while len(data) < 12:
            chunk = sock.recv(12 - len(data))
            if not chunk:
                raise RuntimeError("VNC endpoint closed before its greeting")
            data += chunk
    if not data.startswith(b"RFB "):
        raise RuntimeError("Unexpected VNC endpoint")
    return data.decode("ascii").strip()


def _new_session():
    config = settings.load(PATHS)
    udid = settings.select_device(transport.identifiers(PATHS), config["udid"])
    info = {"schema_version": 2, "token": uuid.uuid4().hex, "remote_started": False,
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
    for name in ("fixture_ssh", "fixture_server", "viewer", "ssh", "usb"):
        entry = info.get(name)
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
        raise RuntimeError("Local services stopped; reconnect USB and run stop to finish remote cleanup") from error
    STATE.unlink()
    return {"stopped": True, "shared_tunnels_touched": False}


def stop():
    with locked():
        return stop_unlocked()


def _cleanup_failed_start():
    try:
        stop_unlocked()
    except (OSError, subprocess.SubprocessError, RuntimeError) as error:
        raise RuntimeError("Connection failed and cleanup is incomplete; reconnect USB and run stop") from error


def deploy():
    """Deploy verified artifacts with a temporary, bridge-owned USB connection."""
    with locked():
        if state():
            raise RuntimeError("Stop the bridge before deploying")
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
    """Select USB device, validate, deploy, and start the local viewer."""
    with locked():
        existing = state()
        if existing:
            result = status()
            if result["connected"]:
                return result
            raise RuntimeError("Previous bridge ownership is still recorded; run stop before connecting again")
        deployment.artifacts(PATHS)
        for port in (VNC_PORT, VIEW_PORT):
            available(port)
        try:
            info = _new_session()
            info.update(deployment.deploy(info["connection"], PATHS))
            info["remote_started"] = True
            save_state(info)  # Record remote intent before SSH can launch the daemon.
            command = shlex.join([f"{REMOTE}/device-session.sh", "run", info["token"], info["binary"]])
            spawn([*ssh_args(info), "-o", "ExitOnForwardFailure=yes", "-L",
                   f"127.0.0.1:{VNC_PORT}:127.0.0.1:{DEVICE_PORT}", PEER, "exec " + command],
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
                raise RuntimeError("Device VNC did not become ready")
            webroot = prepare_viewer()
            spawn(PATHS.python_module("websockify", "--web", str(webroot),
                                      f"127.0.0.1:{VIEW_PORT}", f"127.0.0.1:{VNC_PORT}"),
                  "viewer", info, "viewer")
            deadline = time.monotonic() + 8
            while not viewer_responding(timeout=1):
                if not owned_process(info["viewer"]) or time.monotonic() >= deadline:
                    raise RuntimeError("Viewer did not serve its page; inspect the app's viewer log")
                time.sleep(0.1)
        except BaseException:
            _cleanup_failed_start()
            raise
        return {**status(), "deployment": {key: info[key] for key in ("sha256", "source_commit", "script_sha256")}}


start = connect


def viewer_url():
    return f"http://127.0.0.1:{VIEW_PORT}/"


def viewer_responding(timeout=2):
    """Prove a request handler serves the viewer, rather than just accepting TCP."""
    connection = http.client.HTTPConnection("127.0.0.1", VIEW_PORT, timeout=timeout)
    try:
        connection.request("GET", "/", headers={"Connection": "close"})
        response = connection.getresponse()
        return response.status == 200 and b"<title>iPhoneBridge</title>" in response.read(4096)
    except (OSError, http.client.HTTPException):
        return False
    finally:
        connection.close()


def prepare_viewer():
    """Serve viewer assets only, with no absolute checkout dependencies in bundles."""
    webroot = WORK / "webroot"
    webroot.mkdir(parents=True, exist_ok=True, mode=0o700)
    assets = {path.name: path for path in (ROOT / "viewer").iterdir() if path.is_file()}
    assets["novnc"] = PATHS.novnc
    for name, target in assets.items():
        if not target.exists():
            raise RuntimeError(f"Missing viewer resource: {name}")
        link = webroot / name
        if link.is_symlink():
            if link.resolve() == target.resolve():
                continue
            link.unlink()  # Only this generated link, never its target.
        elif link.exists():
            raise RuntimeError(f"Unexpected file in generated viewer assets: {name}")
        link.symlink_to(target)
    return webroot


def status():
    info = state()
    config = settings.load(PATHS)
    services = {name: owned_process(info.get(name)) for name in ("usb", "ssh", "viewer")}
    running = all(services.values())
    viewer_ready = services["viewer"] and viewer_responding()
    connected = False
    if running:
        try:
            connected = viewer_ready and usb_owned(info) and bool(handshake())
        except (OSError, RuntimeError, subprocess.SubprocessError):
            pass
    return {"running": running, "connected": connected, "viewer": viewer_url(),
            "viewer_ready": viewer_ready,
            "selected_udid": info.get("connection", {}).get("udid") or config["udid"],
            "services": services, "settings": config, "data_directory": str(WORK),
            "config_path": str(WORK / "config.json")}


def devices():
    return transport.devices(PATHS)


def configure(**values):
    with locked():
        return settings.configure(paths=PATHS, **values)


def health():
    result = status()
    result["checks"] = {"viewer": {"ok": result["viewer_ready"]}}
    try:
        discovered = devices()
        result["devices"] = discovered["devices"]
        result["checks"]["usb"] = {"ok": bool(discovered["devices"])}
    except (OSError, subprocess.SubprocessError, RuntimeError) as error:
        result["checks"]["usb"] = {"ok": False, "detail": str(error)}
    if state().get("connection") and result["services"]["usb"]:
        try:
            version = transport.verify_phone(state()["connection"], PATHS)
            result["checks"]["phone"] = {"ok": True, "ios": version}
        except (OSError, subprocess.SubprocessError, RuntimeError) as error:
            result["checks"]["phone"] = {"ok": False, "detail": str(error)}
    result["healthy"] = result["connected"] and all(item["ok"] for item in result["checks"].values())
    return result
