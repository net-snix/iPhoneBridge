"""Portable user-owned configuration. Only key paths, never key material."""
from __future__ import annotations

import json
from pathlib import Path
import re

from .runtime import PATHS

SUPPORTED_IOS = "15.1.1"
DEFAULT_SSH_PORT = 15422
# Loopback ports other bridge services bind (native mirror, test fixture); the USB
# forward may not take them.
OTHER_BRIDGE_PORTS = frozenset({15901, 15802})
DEFAULTS = {"schema_version": 1, "udid": None, "identity": None, "ssh_port": None}
UDID_PATTERN = re.compile(r"[0-9A-Fa-f][0-9A-Fa-f-]{15,63}\Z")


def validate(values, check_identity=False):
    if not isinstance(values, dict) or set(values) - set(DEFAULTS):
        raise RuntimeError("Invalid configuration fields; use the configure command")
    result = {**DEFAULTS, **values}
    if result["schema_version"] != 1:
        raise RuntimeError("Unsupported configuration version")
    udid = result["udid"]
    if udid is not None and (not isinstance(udid, str) or not UDID_PATTERN.fullmatch(udid)):
        raise RuntimeError("UDID must be a connected USB device identifier")
    identity = result["identity"]
    if identity is not None:
        if not isinstance(identity, str) or not identity or "\x00" in identity or "\n" in identity:
            raise RuntimeError("SSH identity must be a local key-file path")
        path = Path(identity).expanduser()
        if not path.is_absolute():
            raise RuntimeError("SSH identity must be an absolute path")
        if check_identity and not path.is_file():
            raise RuntimeError("The selected SSH identity file does not exist")
        result["identity"] = str(path)
    port = result["ssh_port"]
    if port is not None and not valid_ssh_port(port):
        raise RuntimeError("USB SSH port must be an integer from 1024 to 65535 other than "
                           "the bridge's mirror (15901) and fixture (15802) ports")
    return result


def valid_ssh_port(port):
    return type(port) is int and 1024 <= port <= 65535 and port not in OTHER_BRIDGE_PORTS


def ssh_port(values):
    """The local USB SSH port a configuration or recorded connection uses."""
    port = values.get("ssh_port") if isinstance(values, dict) else None
    return DEFAULT_SSH_PORT if port is None else port


def load(paths=PATHS):
    path = paths.data / "config.json"
    if not path.exists():
        return dict(DEFAULTS)
    try:
        return validate(json.loads(path.read_text()))
    except (ValueError, OSError) as error:
        raise RuntimeError("Cannot read configuration; check the app's config.json") from error


def configure(udid=None, identity=None, ssh_port=None, clear_udid=False, clear_identity=False,
              clear_ssh_port=False, paths=PATHS):
    changes = (udid is not None or identity is not None or ssh_port is not None
               or clear_udid or clear_identity or clear_ssh_port)
    if changes and (paths.data / "state.json").exists():
        raise RuntimeError("Stop the bridge before changing its device, SSH identity or USB port")
    values = load(paths)
    if clear_udid:
        values["udid"] = None
    elif udid is not None:
        values["udid"] = udid
    if clear_identity:
        values["identity"] = None
    elif identity is not None:
        values["identity"] = identity
    if clear_ssh_port:
        values["ssh_port"] = None
    elif ssh_port is not None:
        values["ssh_port"] = ssh_port
    values = validate(values, check_identity=True)
    if changes:
        paths.ensure_data()
        path = paths.data / "config.json"
        temporary = path.with_suffix(".new")
        # Older bridge builds reject unknown fields, so the port is stored only when chosen.
        stored = {key: value for key, value in values.items() if key != "ssh_port" or value is not None}
        temporary.write_text(json.dumps(stored, indent=2) + "\n")
        temporary.chmod(0o600)
        temporary.replace(path)
    return {"settings": values, "config_path": str(paths.data / "config.json"),
            "data_directory": str(paths.data), "supported_ios": [SUPPORTED_IOS]}


def select_device(identifiers, configured=None):
    identifiers = sorted(set(identifiers))
    if any(not UDID_PATTERN.fullmatch(identifier) for identifier in identifiers):
        raise RuntimeError("USB discovery returned an invalid device identifier")
    if configured:
        if configured not in identifiers:
            raise RuntimeError("The configured iPhone is not connected over USB")
        return configured
    if not identifiers:
        raise RuntimeError("No iPhone connected over USB; connect and trust this Mac on the phone")
    if len(identifiers) != 1:
        raise RuntimeError("Several iPhones are connected; select a device in Settings first")
    return identifiers[0]
