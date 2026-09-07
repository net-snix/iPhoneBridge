"""Own only our SSH session, viewer, and device daemon; never shared iproxy."""
from __future__ import annotations

import fcntl
import hashlib
import json
import os
from pathlib import Path
import shlex
import socket
import subprocess
import sys
import time
import uuid
from contextlib import contextmanager

ROOT = Path(__file__).resolve().parent.parent
WORK = ROOT / "work"
SOURCE = Path("/Users/espenmac/Code/oss/TrollVNC")
NOVNC = Path("/Users/espenmac/Code/oss/noVNC-iPhoneBridge")
UDID = "00008110-001914103EB8801E"
REMOTE = "/var/mobile/Media/iPhoneBridge"
VNC_PORT, VIEW_PORT = 15901, 15801
DEVICE_PORT = 15901
SSH = ["/usr/bin/ssh", "-T", "-o", "BatchMode=yes", "-o", "ConnectTimeout=5",
       "-o", "ServerAliveInterval=10", "-o", "ServerAliveCountMax=2", "-p", "2222"]
PEER = "mobile@127.0.0.1"
STATE = WORK / "state.json"
SESSION_SCRIPT = ROOT / "iphonebridge/device-session.sh"


def run(args, **kwargs):
    return subprocess.run(args, check=True, capture_output=True, text=True,
                          timeout=kwargs.pop("timeout", 15), **kwargs).stdout.strip()


def remote(command):
    return run([*SSH, PEER, command])


@contextmanager
def locked():
    WORK.mkdir(exist_ok=True, mode=0o700)
    with (WORK / "lifecycle.lock").open("w") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        yield


def state():
    return json.loads(STATE.read_text()) if STATE.exists() else {}


def process_identity(pid):
    try:
        return run(["/bin/ps", "-p", str(pid), "-o", "lstart=,command="])
    except subprocess.CalledProcessError:
        return ""


def owned_process(entry):
    return bool(entry and process_identity(entry["pid"]) == entry["identity"])


def save_state(info):
    temporary = STATE.with_suffix(".new")
    temporary.write_text(json.dumps(info, indent=2) + "\n")
    temporary.chmod(0o600)
    temporary.replace(STATE)


def spawn(args, name, info, field):
    with (WORK / f"{name}.log").open("ab") as log:
        process = subprocess.Popen(args, stdin=subprocess.DEVNULL, stdout=log,
                                   stderr=log, start_new_session=True)
    entry = {"pid": process.pid, "identity": process_identity(process.pid)}
    info[field] = entry
    save_state(info)
    time.sleep(0.2)
    if process.poll() is not None:
        raise RuntimeError(f"{name} exited: inspect work/{name}.log")
    return entry


def available(port):
    with socket.socket() as sock:
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        sock.bind(("127.0.0.1", port))


def handshake():
    with socket.create_connection(("127.0.0.1", VNC_PORT), timeout=3) as sock:
        data = b""
        while len(data) < 12:
            chunk = sock.recv(12 - len(data))
            if not chunk:
                raise RuntimeError("VNC endpoint closed before its greeting")
            data += chunk
        banner = data.decode("ascii")
    if not banner.startswith("RFB "):
        raise RuntimeError("Unexpected VNC endpoint")
    return banner.strip()


def verify_phone():
    version = run(["/opt/homebrew/bin/ideviceinfo", "-u", UDID, "-k", "ProductVersion"])
    if version != "15.1.1":
        raise RuntimeError(f"Device version changed to {version}; revalidate compatibility")
    # Verify the shared forward explicitly selects this USB UDID. Do not claim
    # host serial-number sysctls are available on iOS.
    listeners = run(["/usr/sbin/lsof", "-nP", "-t", "-iTCP:2222", "-sTCP:LISTEN"]).splitlines()
    if len(listeners) != 1:
        raise RuntimeError("Expected one existing USB SSH forward on port 2222")
    command = run(["/bin/ps", "-p", listeners[0], "-o", "command="])
    words = shlex.split(command)
    if not (words and Path(words[0]).name == "iproxy" and words[1:] == ["-u", UDID, "2222:22"]):
        raise RuntimeError("Existing SSH listener is not the expected device-specific iproxy")


def transfer(source, target):
    run(["/usr/bin/scp", "-O", "-q", "-o", "BatchMode=yes", "-P", "2222",
         str(source), f"{PEER}:{target}.new"], timeout=30)
    remote(f"chmod 700 {target}.new && mv {target}.new {target}")
    received = subprocess.run([*SSH, PEER, f"cat {target}"], check=True,
                              capture_output=True, timeout=20).stdout
    digest = hashlib.sha256(source.read_bytes()).hexdigest()
    if hashlib.sha256(received).hexdigest() != digest:
        raise RuntimeError("Transferred file checksum mismatch")
    return digest


def deploy():
    """Copy only our CLI artifact; no package manager, launchd, or app changes."""
    with locked():
        if state():
            raise RuntimeError("Stop the bridge before deploying")
        verify_phone()
        remote(f"test ! -d {REMOTE}/active")
        binary = SOURCE / ".theos/obj/trollvncserver"
        digest = hashlib.sha256(binary.read_bytes()).hexdigest()
        manifest = json.loads((WORK / "build-manifest.json").read_text())
        if manifest["binary"]["sha256"] != digest:
            raise RuntimeError("Build manifest does not match binary; rerun setup")
        target = f"{REMOTE}/trollvncserver-{digest[:16]}"
        remote(f"umask 077; mkdir -p {REMOTE}")
        transfer(binary, target)
        script_hash = transfer(SESSION_SCRIPT, f"{REMOTE}/device-session.sh")
        help_text = remote(f"DISABLE_TWEAKS=1 {target} -h 2>&1")
        if "TrollVNC" not in help_text:
            raise RuntimeError("Device executable did not run")
        info = {"binary": target, "sha256": digest,
                "script_sha256": script_hash,
                "source_commit": run(["git", "-C", str(SOURCE), "rev-parse", "HEAD"])}
        (WORK / "deployment.json").write_text(json.dumps(info, indent=2) + "\n")
        return info


def stop_unlocked():
    # Lifecycle callers already hold lifecycle.lock. Input only holds control.lock.
    # Taking locks in this order lets an in-flight gesture release before teardown.
    from .control import _locked as control_locked
    with control_locked():
        return _stop_services()


def _stop_services():
    info = state()
    if not info:
        return {"stopped": True, "detail": "No bridge-owned services recorded"}
    error = None
    try:
        remote(shlex.join([f"{REMOTE}/device-session.sh", "stop", info["token"]]))
    except (OSError, subprocess.SubprocessError) as exc:
        error = exc
    # Close only verified local processes, even if USB is disconnected. Keep the
    # state on remote failure so a later stop can finish cleanup after reconnect.
    local_errors = []
    for name in ("fixture_ssh", "fixture_server", "viewer", "ssh"):
        entry = info.get(name)
        if owned_process(entry):
            os.kill(entry["pid"], 15)
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


def start():
    with locked():
        info = state()
        if info:
            if owned_process(info.get("ssh")) and owned_process(info.get("viewer")):
                return {"running": True, "vnc": handshake(), "viewer": viewer_url()}
            raise RuntimeError("Stale bridge state: run stop before start")
        verify_phone()
        for port in (VNC_PORT, VIEW_PORT):
            available(port)
        webroot = prepare_viewer()
        info = json.loads((WORK / "deployment.json").read_text())
        if hashlib.sha256(SESSION_SCRIPT.read_bytes()).hexdigest() != info["script_sha256"]:
            raise RuntimeError("Session script changed; deploy again before starting")
        binary = info["binary"]
        remote(f"test ! -d {REMOTE}/active")
        info["token"] = uuid.uuid4().hex
        script = shlex.join([f"{REMOTE}/device-session.sh", "run", info["token"], binary])
        args = [*SSH, "-o", "ExitOnForwardFailure=yes", "-L",
                f"127.0.0.1:{VNC_PORT}:127.0.0.1:{DEVICE_PORT}", PEER,
                "exec " + script]
        save_state(info)
        try:
            spawn(args, "device", info, "ssh")
            for attempt in range(30):
                try:
                    handshake()
                    break
                except (OSError, RuntimeError):
                    if not owned_process(info["ssh"]):
                        raise RuntimeError("Device daemon exited; inspect work/device.log")
                    time.sleep(0.2)
            else:
                raise RuntimeError("Device VNC failed to start")
            spawn([str(ROOT / ".venv/bin/websockify"), "--web", str(webroot),
                   f"127.0.0.1:{VIEW_PORT}", f"127.0.0.1:{VNC_PORT}"], "viewer", info, "viewer")
        except BaseException:
            stop_unlocked()
            raise
        return {"running": True, "viewer": viewer_url(), "vnc": handshake()}


def viewer_url():
    return f"http://127.0.0.1:{VIEW_PORT}/"


def prepare_viewer():
    """Serve only our viewer and pinned noVNC assets, never the repository root."""
    webroot = WORK / "webroot"
    webroot.mkdir(exist_ok=True, mode=0o700)
    assets = {path.name: path for path in (ROOT / "viewer").iterdir() if path.is_file()}
    assets["novnc"] = NOVNC
    for name, target in assets.items():
        link = webroot / name
        if link.is_symlink() and link.resolve() == target.resolve():
            continue
        if link.exists() or link.is_symlink():
            raise RuntimeError(f"Unexpected viewer asset at {link}")
        link.symlink_to(target)
    return webroot


def health():
    result = {"udid": UDID, "viewer": viewer_url(), "services": {}, "checks": {}}
    for name, args in {
        "usb": ["/opt/homebrew/bin/idevice_id", "-l"],
        "pairing": ["/opt/homebrew/bin/idevicepair", "-u", UDID, "validate"],
        "ios": ["/opt/homebrew/bin/ideviceinfo", "-u", UDID, "-k", "ProductVersion"],
        "ssh": [*SSH, PEER, "id"],
    }.items():
        try:
            result["checks"][name] = {"ok": True, "detail": run(args)}
        except (OSError, subprocess.SubprocessError) as exc:
            result["checks"][name] = {"ok": False, "detail": str(exc)}
    for name in ("ssh", "viewer"):
        result["services"][name] = owned_process(state().get(name))
    try:
        result["checks"]["vnc"] = {"ok": True, "detail": handshake()}
    except (OSError, RuntimeError) as exc:
        result["checks"]["vnc"] = {"ok": False, "detail": str(exc)}
    result["healthy"] = all(c["ok"] for c in result["checks"].values()) and all(result["services"].values())
    return result
