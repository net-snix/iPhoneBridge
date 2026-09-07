"""USB discovery and key-only SSH using a device-specific host-key identity."""
from __future__ import annotations

from pathlib import Path
import subprocess

from . import settings
from .runtime import PATHS

SSH_PORT = 15422
PEER = "mobile@127.0.0.1"


def run(args, **kwargs):
    return subprocess.run(args, check=True, capture_output=True, text=True,
                          timeout=kwargs.pop("timeout", 15), **kwargs).stdout.strip()


def identifiers(paths=PATHS):
    output = run([paths.tool("idevice_id"), "-l"])
    ids = sorted(set(line.strip() for line in output.splitlines() if line.strip()))
    if len(ids) > 32 or any(not settings.UDID_PATTERN.fullmatch(item) for item in ids):
        raise RuntimeError("USB discovery returned an invalid device list")
    return ids


def devices(paths=PATHS):
    config = settings.load(paths)
    entries = []
    ids = identifiers(paths)
    for udid in ids:
        try:
            name = run([paths.tool("ideviceinfo"), "-u", udid, "-k", "DeviceName"], timeout=5)
            ios = run([paths.tool("ideviceinfo"), "-u", udid, "-k", "ProductVersion"], timeout=5)
            entries.append({"udid": udid, "name": name, "ios": ios, "compatible": ios == settings.SUPPORTED_IOS})
        except (OSError, subprocess.SubprocessError):
            entries.append({"udid": udid, "name": "iPhone", "ios": None, "compatible": False,
                            "error": "Unlock the phone and trust this Mac to read device details"})
    selected = config["udid"] if config["udid"] in ids else (ids[0] if not config["udid"] and len(ids) == 1 else None)
    return {"devices": entries, "selected_udid": selected,
            "selection_required": len(ids) > 1 and selected is None, "settings": config}


def connection_snapshot(config, udid, paths=PATHS):
    values = settings.validate(config, check_identity=True)
    settings.select_device([udid], udid)
    return {"udid": udid, "identity": values["identity"], "ssh_port": SSH_PORT,
            "known_hosts": str(paths.data / "known_hosts")}


def ssh_args(connection):
    if not isinstance(connection, dict):
        raise RuntimeError("Missing saved USB connection identity")
    udid = connection.get("udid")
    if not isinstance(udid, str) or not settings.UDID_PATTERN.fullmatch(udid):
        raise RuntimeError("Invalid saved device identity")
    if connection.get("ssh_port") != SSH_PORT:
        raise RuntimeError("Saved connection does not use the bridge-owned SSH port")
    hosts = Path(connection.get("known_hosts", ""))
    if not hosts.is_absolute() or any(value in str(hosts) for value in ("\x00", "\n", "\r")):
        raise RuntimeError("Invalid saved host-key path")
    # OpenSSH accepts several known-hosts paths; quote spaces inside its -o syntax.
    hosts_option = str(hosts).replace("\\", "\\\\").replace('"', '\\"')
    args = ["/usr/bin/ssh", "-T", "-F", "/dev/null", "-o", "BatchMode=yes",
            "-o", "ConnectTimeout=5", "-o", "ServerAliveInterval=10", "-o", "ServerAliveCountMax=2",
            "-o", "StrictHostKeyChecking=accept-new", "-o", f'UserKnownHostsFile="{hosts_option}"',
            "-o", f"HostKeyAlias=iphonebridge-{udid}", "-p", str(SSH_PORT)]
    identity = connection.get("identity")
    if identity:
        settings.validate({"identity": identity})
        args += ["-i", identity, "-o", "IdentitiesOnly=yes"]
    return args


def remote(command, connection, **kwargs):
    return run([*ssh_args(connection), PEER, command], **kwargs)


def verify_phone(connection, paths=PATHS):
    udid = connection["udid"]
    if udid not in identifiers(paths):
        raise RuntimeError("The selected iPhone is no longer connected over USB")
    run([paths.tool("idevicepair"), "-u", udid, "validate"])
    version = run([paths.tool("ideviceinfo"), "-u", udid, "-k", "ProductVersion"])
    if version != settings.SUPPORTED_IOS:
        raise RuntimeError(f"iOS {version} is not supported by this build; this release is verified for iOS {settings.SUPPORTED_IOS}")
    remote("id -u; test -d /var/jb", connection)
    return version
