"""Resolve immutable resources and user-owned state for source and bundled runs.

Resources are read from the checkout or the app bundle. Runtime state lives in one
per-user data directory shared by the app and every source checkout, so a bridge
session started anywhere is visible to every other instance. IPHONEBRIDGE_DATA_DIR
selects a private directory for tests and benchmarks and isolates its records too.
"""
from __future__ import annotations

from dataclasses import dataclass
import json
import os
from pathlib import Path
import shutil
import sys

USB_TOOLS = frozenset({"iproxy", "idevice_id", "ideviceinfo", "idevicepair", "idevicescreenshot"})


@dataclass(frozen=True)
class Paths:
    root: Path
    contents: Path | None
    data: Path
    # Where bridge code before 0.2.2 kept this checkout's records; only a source run on
    # the shared data directory may adopt them, so a sandbox never moves a live record.
    legacy_data: Path | None = None

    @classmethod
    def discover(cls, root=None, home=None, environ=None):
        root = Path(root or Path(__file__).resolve().parents[1]).resolve()
        home = Path(home or Path.home())
        environ = os.environ if environ is None else environ
        contents = root.parent.parent if (root.name == "bridge" and root.parent.name == "Resources"
                                          and root.parent.parent.name == "Contents") else None
        override = environ.get("IPHONEBRIDGE_DATA_DIR")
        shared = home / "Library/Application Support/iPhoneBridge"
        data = Path(override).expanduser() if override else shared
        if not data.is_absolute():
            raise RuntimeError("IPHONEBRIDGE_DATA_DIR must be an absolute directory")
        legacy = root / "work" if contents is None and data == shared else None
        return cls(root, contents, data, legacy)

    @property
    def logs(self):
        return self.data / "logs"

    @property
    def instance(self):
        """The path people know this instance by: the app bundle or the source checkout."""
        return self.contents.parent if self.contents else self.root

    @property
    def screenshots(self):
        return self.data / "screenshots" if self.contents else self.root / "outputs/screenshots"

    @property
    def device(self):
        return self.contents / "Resources/device" if self.contents else self.root / "work/device"

    @property
    def novnc(self):
        if self.contents:
            return self.root / "novnc"
        spec = json.loads((self.root / "dependency-lock.json").read_text())
        path = Path(os.environ.get("IPHONEBRIDGE_NOVNC_SOURCE", spec["novnc"]["path"])).expanduser()
        return path if path.is_absolute() else self.root / path

    def tool(self, name):
        if name not in USB_TOOLS:
            raise ValueError("Unknown USB utility")
        if self.contents:
            path = self.contents / "Helpers/usb/bin" / name
            if not path.is_file() or not os.access(path, os.X_OK):
                raise RuntimeError(f"The app is missing its bundled {name} utility; rebuild or reinstall the app")
            return str(path)
        found = shutil.which(name)
        if not found:
            raise RuntimeError(f"Missing {name}; use the standalone app or install source-build USB dependencies")
        return found

    def python_module(self, module, *arguments):
        if self.contents:
            return [sys.executable, "-I", "-B", str(self.root / "iphonebridge/entry.py"),
                    "--module", module, *map(str, arguments)]
        return [sys.executable, "-m", module, *map(str, arguments)]

    def ensure_data(self):
        self.data.mkdir(parents=True, exist_ok=True, mode=0o700)
        self.logs.mkdir(parents=True, exist_ok=True, mode=0o700)


PATHS = Paths.discover()
