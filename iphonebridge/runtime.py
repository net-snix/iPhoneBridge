"""Resolve immutable resources and user-owned state for source and bundled runs."""
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

    @classmethod
    def discover(cls, root=None, home=None, environ=None):
        root = Path(root or Path(__file__).resolve().parents[1]).resolve()
        home = Path(home or Path.home())
        environ = os.environ if environ is None else environ
        contents = root.parent.parent if (root.name == "bridge" and root.parent.name == "Resources"
                                          and root.parent.parent.name == "Contents") else None
        override = environ.get("IPHONEBRIDGE_DATA_DIR")
        data = Path(override).expanduser() if override else (
            home / "Library/Application Support/iPhoneBridge" if contents else root / "work")
        if not data.is_absolute():
            raise RuntimeError("IPHONEBRIDGE_DATA_DIR must be an absolute directory")
        return cls(root, contents, data)

    @property
    def logs(self):
        return self.data / "logs"

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
