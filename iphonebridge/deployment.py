"""Deploy only the bundled verified daemon into its private mobile directory."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
import shlex
import subprocess

from .runtime import PATHS
from . import transport

REMOTE = "/var/mobile/Media/iPhoneBridge"


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def artifacts(paths=PATHS):
    manifest_path = paths.device / "manifest.json"
    if manifest_path.is_file():
        manifest = json.loads(manifest_path.read_text())
        binary = paths.device / "trollvncserver"
        script = paths.device / "device-session.sh"
    elif not paths.contents:
        # Source developer workflow may use its pre-existing setup manifest.
        manifest = json.loads((paths.root / "work/build-manifest.json").read_text())
        binary = Path(manifest["binary"]["path"])
        script = paths.root / "iphonebridge/device-session.sh"
        manifest = {**manifest, "script_sha256": digest(script)}
    else:
        raise RuntimeError("App is missing its verified device artifact; rebuild or reinstall it")
    if manifest.get("schema_version") != 1:
        raise RuntimeError("Unsupported device-artifact manifest version")
    if not binary.is_file() or digest(binary) != manifest["binary"]["sha256"]:
        raise RuntimeError("Device artifact does not match its build manifest")
    if not script.is_file() or digest(script) != manifest["script_sha256"]:
        raise RuntimeError("Device session script does not match its build manifest")
    if not manifest.get("source", {}).get("commit"):
        raise RuntimeError("Device artifact has no source provenance")
    return binary, script, manifest


def _remote_bytes(path, connection):
    return subprocess.run([*transport.ssh_args(connection), transport.PEER, "cat " + shlex.quote(path)],
                          check=True, capture_output=True, timeout=20).stdout


def _transfer(source, target, connection):
    # SSH stdin avoids scp's separate configuration and preserves all SSH checks.
    command = f"umask 077; cat > {shlex.quote(target + '.new')}"
    with source.open("rb") as data:
        subprocess.run([*transport.ssh_args(connection), transport.PEER, command], stdin=data,
                       check=True, capture_output=True, timeout=30)
    received = _remote_bytes(target + ".new", connection)
    if hashlib.sha256(received).hexdigest() != digest(source):
        raise RuntimeError("Transferred device file checksum mismatch")
    transport.remote(f"chmod 700 {shlex.quote(target + '.new')} && "
                     f"mv {shlex.quote(target + '.new')} {shlex.quote(target)}", connection)


def deploy(connection, paths=PATHS):
    binary, script, manifest = artifacts(paths)
    sha = manifest["binary"]["sha256"]
    target = f"{REMOTE}/trollvncserver-{sha[:16]}"
    # Never replace files while another session owns the daemon directory.
    transport.remote(f"test ! -d {REMOTE}/active && umask 077 && mkdir -p {REMOTE}", connection)
    _transfer(binary, target, connection)
    _transfer(script, f"{REMOTE}/device-session.sh", connection)
    info = {"binary": target, "sha256": sha, "script_sha256": manifest["script_sha256"],
            "source_commit": manifest["source"]["commit"], "udid": connection["udid"]}
    paths.ensure_data()
    path = paths.data / "deployment.json"
    temporary = path.with_suffix(".new")
    temporary.write_text(json.dumps(info, indent=2) + "\n")
    temporary.chmod(0o600)
    temporary.replace(path)
    return info
