"""Inspect local processes and listeners by identity; nothing here signals anything."""
from __future__ import annotations

import os
import subprocess

from . import transport

BRIDGE_SHAPES = (("iproxy", f" {transport.SSH_PORT}:22", "USB forward"),
                 ("ssh", "device-session.sh", "device session"))


def process_identity(pid):
    """Start time plus command line; a reused pid never matches an old identity."""
    if type(pid) is not int or pid <= 1:
        return ""
    try:
        # One locale for every instance, so a record written anywhere compares equal here.
        return transport.run(["/bin/ps", "-p", str(pid), "-o", "lstart=,command="],
                             env={**os.environ, "LC_ALL": "C"})
    except subprocess.CalledProcessError:
        return ""


def process_cwd(pid):
    """Working directory of a process, used only to name the instance that started it."""
    if type(pid) is not int or pid <= 1:
        return ""
    try:
        result = subprocess.run(["/usr/sbin/lsof", "-a", "-p", str(pid), "-d", "cwd", "-Fn"],
                                capture_output=True, text=True, timeout=5)
    except (OSError, subprocess.SubprocessError):
        return ""
    return next((line[1:] for line in result.stdout.splitlines() if line.startswith("n")), "")


def listeners(port):
    """Map each pid listening on a loopback TCP port to its bound addresses."""
    result = subprocess.run(["/usr/sbin/lsof", "-nP", f"-iTCP:{port}", "-sTCP:LISTEN", "-Fpn"],
                            capture_output=True, text=True, timeout=5)
    if result.returncode == 1 and not result.stdout.strip():
        return {}
    if result.returncode != 0:
        raise RuntimeError(f"Cannot inspect ownership of local port {port}")
    found = {}
    pid = None
    for line in result.stdout.splitlines():
        if line.startswith("p"):
            pid = int(line[1:])
        elif line.startswith("n") and pid:
            found.setdefault(pid, set()).add(line[1:])
    return found


def describe_instance(path):
    if not path:
        return "an unknown bridge instance"
    if path.endswith(".app") or "/Contents/Resources/bridge" in path:
        return f"the iPhoneBridge app at {path.split('/Contents/', 1)[0]}"
    return f"the bridge checkout at {path}"


def foreign_bridge_process(pid):
    """Name a listener another bridge instance spawned; the caller never signals it."""
    identity = process_identity(pid)
    command = identity[24:].strip()
    for name, marker, role in BRIDGE_SHAPES:
        if name in command and marker in command:
            cwd = process_cwd(pid)
            hint = ("quit that app or use its Reconnect" if ".app/Contents/" in cwd
                    else "run ./bridge stop in that checkout")
            return (f"an iPhoneBridge {role} (pid {pid}, started {identity[:24].strip()}) run from "
                    f"{describe_instance(cwd)}; {hint}")
    return ""


def port_held_message(port, held, fallback, label="Local port"):
    """Explain who holds a port when it is a bridge process this instance did not record."""
    for pid in sorted(held):
        description = foreign_bridge_process(pid)
        if description:
            return f"{label} {port} is held by {description}; nothing was stopped"
    return fallback
