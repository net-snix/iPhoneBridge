"""Read-only packaging checks: no USB connection, server or phone input."""
import importlib
import platform
import sys

from . import deployment
from .runtime import PATHS, USB_TOOLS


def self_test():
    modules = ["PIL.Image", "_tkinter", "vncdotool.api", "twisted.internet.reactor", "websockify",
               "mcp.server.mcpserver", "iphonebridge.mcp_server", "iphonebridge.fixture_server"]
    for name in modules:
        importlib.import_module(name)
    binary, script, manifest = deployment.artifacts(PATHS)
    return {"ok": True, "bundled": PATHS.contents is not None,
            "python": sys.version.split()[0], "architecture": platform.machine(),
            "python_executable": sys.executable,
            "resources": str(PATHS.root), "data_directory": str(PATHS.data),
            "imports": modules, "usb_tools": {name: PATHS.tool(name) for name in sorted(USB_TOOLS)},
            "device_sha256": manifest["binary"]["sha256"]}
