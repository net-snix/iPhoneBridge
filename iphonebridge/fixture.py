"""Serve a disposable browser test surface exclusively through USB forwarding."""
from . import lifecycle as life


def start():
    with life.locked():
        info = life.state()
        if not life.owned_process(info.get("ssh")):
            raise RuntimeError("Start the bridge before starting its test surface")
        if info.get("fixture_server") or info.get("fixture_ssh"):
            if all(life.owned_process(info.get(name)) for name in ("fixture_server", "fixture_ssh")):
                return {"phone_url": "http://127.0.0.1:15802/test.html"}
            raise RuntimeError("Stale test surface: stop and start the bridge to clean up")
        life.available(15802)
        try:
            life.spawn(life.PATHS.python_module("iphonebridge.fixture_server"),
                       "fixture", info, "fixture_server")
            life.spawn([*life.ssh_args(info), "-N", "-o", "ExitOnForwardFailure=yes", "-R",
                        "127.0.0.1:15802:127.0.0.1:15802", life.PEER], "fixture-usb", info, "fixture_ssh")
        except BaseException:
            life.stop_unlocked()
            raise
        return {"phone_url": "http://127.0.0.1:15802/test.html", "transport": "USB SSH reverse forward"}
