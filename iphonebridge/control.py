"""Serialized, bounded RFB actions over the bridge's USB SSH forward."""
from __future__ import annotations

from contextlib import contextmanager
from datetime import datetime, timezone
import fcntl
import io
import logging
import math
import os
from pathlib import Path
import socket
import time
from uuid import uuid4

from PIL import Image
from twisted.internet import error as twisted_error
from twisted.internet.defer import Deferred
from vncdotool import api
from vncdotool.client import VNCDoException, VNCDoToolClient, VNCDoToolFactory
from .runtime import PATHS

ROOT = PATHS.root
WORK = PATHS.data
SCREENSHOTS = PATHS.screenshots
ADDRESS = "127.0.0.1::15901"  # vncdotool's double colon means an absolute port.
CALL_TIMEOUT = 5.0
ACTION_TIMEOUT = 20.0
LOCK_TIMEOUT = 25.0
KEYS = {"enter": "enter", "tab": "tab", "escape": "esc", "backspace": "bsp",
        "delete": "delete", "left": "left", "right": "right", "up": "up",
        "down": "down", "home": "home", "end": "end", "pageup": "pgup",
        "pagedown": "pgdn"}
SHIFTED_US = dict(zip('~!@#$%^&*()_+{}|:"<>?', '`1234567890-=[]\\;\',./'))

# vncdotool debug logging includes keystrokes; never enable it in this bridge.
logging.getLogger("vncdotool").setLevel(logging.WARNING)
logging.getLogger("vncdotool.client").setLevel(logging.WARNING)


class BridgeError(Exception):
    """An expected failure with a deliberately safe, user-facing message."""


class BridgeInputError(BridgeError, ValueError):
    pass


class BridgeActionError(BridgeError, RuntimeError):
    pass


class BridgeTimeoutError(BridgeError, TimeoutError):
    pass


CONNECTION_ERRORS = (twisted_error.ConnectError, twisted_error.ConnectionClosed,
                     twisted_error.ConnectingCancelledError, VNCDoException)
EXPECTED_ACTION_ERRORS = (BridgeError, OSError, *CONNECTION_ERRORS)


class _Client(VNCDoToolClient):
    def probeSize(self):
        """Resolve the current framebuffer size without a full RAW frame transfer.

        A non-incremental 1x1 request completes with the server's current size,
        including a pending DesktopSize change, instead of moving the whole
        uncompressed frame (about 12 MB, roughly 300 ms) over the USB tunnel.
        """
        d = self.deferred = Deferred()
        self.framebufferUpdateRequest(0, 0, 1, 1, incremental=False)

        def size(_):
            self.screen = None  # The later full capture starts from a clean image.
            # vncdotool feeds each result into the next proxy call as its protocol.
            return self

        return d.addCallback(size)


class _Factory(VNCDoToolFactory):
    protocol = _Client


@contextmanager
def _locked():
    WORK.mkdir(parents=True, exist_ok=True, mode=0o700)
    fd = os.open(WORK / "control.lock", os.O_CREAT | os.O_RDWR, 0o600)
    with os.fdopen(fd, "r+") as lock:
        deadline = time.monotonic() + LOCK_TIMEOUT
        while True:
            try:
                fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
                break
            except BlockingIOError:
                if time.monotonic() >= deadline:
                    raise BridgeTimeoutError("Another bridge action is still running") from None
                time.sleep(0.05)
        try:
            yield
        finally:
            fcntl.flock(lock, fcntl.LOCK_UN)


class _Session:
    def __init__(self, client):
        self.client = client
        self.deadline = time.monotonic() + ACTION_TIMEOUT

    def call(self, method, *args, **kwargs):
        remaining = self.deadline - time.monotonic()
        if remaining <= 0:
            raise BridgeTimeoutError("Bridge action exceeded its time limit")
        self.client.timeout = min(CALL_TIMEOUT, remaining)
        return getattr(self.client, method)(*args, **kwargs)

    def frame(self):
        data = io.BytesIO()
        self.call("captureScreen", data, incremental=False, format="PNG")
        data.seek(0)
        with Image.open(data) as image:
            size = image.size
        return data.getvalue(), size

    def size(self):
        protocol = self.call("probeSize")
        return protocol.width, protocol.height

    def release(self, method, *args):
        # Still attempt release after timeout; the connection then closes as well.
        self.client.timeout = 2.0
        getattr(self.client, method)(*args)


@contextmanager
def _session():
    with _locked():
        client = api.connect(ADDRESS, factory_class=_Factory, timeout=CALL_TIMEOUT)
        try:
            yield _Session(client)
        finally:
            client.timeout = 2.0
            client.disconnect()


def _dimensions(width, height):
    if any(type(value) is not int or not 1 <= value <= 16384 for value in (width, height)):
        raise BridgeInputError("width and height must be integer framebuffer dimensions from screenshot")


def _point(x, y, width, height):
    if type(x) is not int or type(y) is not int or not (0 <= x < width and 0 <= y < height):
        raise BridgeInputError("Coordinates must be integer pixels inside the expected framebuffer")


def _check_frame(session, width, height):
    actual = session.size()
    if actual != (width, height):
        raise BridgeInputError(f"Framebuffer changed: expected {width}x{height}, actual {actual[0]}x{actual[1]}; take a new screenshot")


def _save(session, action):
    data, (width, height) = session.frame()
    SCREENSHOTS.mkdir(parents=True, exist_ok=True, mode=0o700)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S.%fZ")
    path = SCREENSHOTS / f"{stamp}-{uuid4().hex[:8]}-{action}.png"
    with path.open("xb") as out:
        os.chmod(path, 0o600)
        out.write(data)
    return {"action": action, "path": str(path), "width": width, "height": height,
            "coordinate_space": "framebuffer_pixels", "captured_at": stamp}


def screenshot():
    """Return an absolute PNG path and the current raw framebuffer dimensions."""
    with _session() as session:
        return _save(session, "screenshot")


def _mutate(action, width, height, operation):
    _dimensions(width, height)
    with _session() as session:
        _check_frame(session, width, height)
        try:
            operation(session)
            time.sleep(0.25)
            return _save(session, action)
        except EXPECTED_ACTION_ERRORS:
            raise BridgeActionError(
                f"{action} may have been applied but verification failed; take a screenshot before retrying"
            ) from None


def tap(x: int, y: int, width: int, height: int):
    """Single-finger tap in screenshot pixels, followed by a fresh screenshot."""
    _dimensions(width, height)
    _point(x, y, width, height)

    def operation(session):
        session.call("mouseMove", x, y)
        try:
            session.call("mouseDown", 1)
            time.sleep(0.08)
        finally:
            session.release("mouseUp", 1)

    return _mutate("tap", width, height, operation)


def drag(x1: int, y1: int, x2: int, y2: int, width: int, height: int, duration: float = 0.5):
    """One-finger linear drag/swipe, duration 0.1–5 seconds, then screenshot."""
    _dimensions(width, height)
    _point(x1, y1, width, height)
    _point(x2, y2, width, height)
    if isinstance(duration, bool) or not isinstance(duration, (int, float)) or not math.isfinite(duration) or not 0.1 <= duration <= 5:
        raise BridgeInputError("duration must be between 0.1 and 5 seconds")

    def operation(session):
        session.call("mouseMove", x1, y1)
        steps = max(2, math.ceil(duration * 30))
        try:
            session.call("mouseDown", 1)
            start = time.monotonic()
            for step in range(1, steps + 1):
                time.sleep(max(0, start + duration * step / steps - time.monotonic()))
                session.call("mouseMove", round(x1 + (x2 - x1) * step / steps),
                             round(y1 + (y2 - y1) * step / steps))
        finally:
            session.release("mouseUp", 1)

    return _mutate("drag", width, height, operation)


def _press(session, name):
    try:
        session.call("keyDown", name)
        time.sleep(0.015)
    finally:
        session.release("keyUp", name)


def _type_character(session, char):
    # TrollVNC's RFB handler calls keyDown/keyUp, which emit HID usage codes
    # without keyPress's automatic shift wrapping. Send US base keys ourselves.
    shifted = "A" <= char <= "Z" or char in SHIFTED_US
    name = SHIFTED_US.get(char, char.lower() if shifted else char)
    name = {"\n": "enter", "\t": "tab"}.get(name, name)
    if not shifted:
        return _press(session, name)
    try:
        session.call("keyDown", "shift")
        _press(session, name)
    finally:
        # This also runs if shift-down or base-key release times out.
        session.release("keyUp", "shift")


def type_text(text: str, width: int, height: int):
    """Type up to 256 ASCII characters (plus tab/newline) into the focused field.

    Uses the US hardware keyboard layout, with explicit Shift for capitals and
    symbols. TrollVNC's keyboard mapper does not support arbitrary Unicode. Text is never
    written to action metadata or bridge logs; screenshots can show typed text.
    """
    if not isinstance(text, str) or not 1 <= len(text) <= 256:
        raise BridgeInputError("text must contain 1–256 characters")
    if any(not (32 <= ord(char) <= 126 or char in "\n\t") for char in text):
        raise BridgeInputError("Only printable ASCII, tab and newline are supported by the server")

    def operation(session):
        for char in text:
            _type_character(session, char)

    return _mutate("type", width, height, operation)


def key(name: str, width: int, height: int):
    """Press a named navigation key, then screenshot. Home is a keyboard key."""
    if not isinstance(name, str) or name.lower() not in KEYS:
        raise BridgeInputError("Supported keys: " + ", ".join(KEYS))
    return _mutate("key", width, height, lambda session: _press(session, KEYS[name.lower()]))


def _home_press(session):
    # TrollVNC maps RFB button 3 (mask 4) to Consumer Menu down/up. Its
    # keyboard Home keysym is a different key and does not perform this action.
    try:
        session.call("mouseDown", 3)
        time.sleep(0.05)  # Upstream STHIDEventGenerator fingerLiftDelay.
    finally:
        session.release("mouseUp", 3)


def navigate(name: str, width: int, height: int):
    """Open Home or App Switcher using native Home button events, then screenshot."""
    if name not in ("home", "app-switcher"):
        raise BridgeInputError("Supported navigation actions: home, app-switcher")

    def operation(session):
        _home_press(session)
        if name == "app-switcher":
            time.sleep(0.15)  # Upstream menuDoublePress multiTapInterval.
            _home_press(session)
        time.sleep(0.5)  # Let SpringBoard finish Home/App Switcher transition.

    return _mutate(name, width, height, operation)


def health():
    """Read the local RFB banner without sending input or starting services."""
    try:
        with socket.create_connection(("127.0.0.1", 15901), timeout=3) as sock:
            banner = bytearray()
            while len(banner) < 12:
                part = sock.recv(12 - len(banner))
                if not part:
                    break
                banner.extend(part)
        ready = len(banner) == 12 and banner.startswith(b"RFB ")
        return {"ready": ready, "endpoint": "127.0.0.1:15901",
                "protocol": banner.decode("ascii", errors="replace") if ready else None}
    except OSError:
        return {"ready": False, "endpoint": "127.0.0.1:15901", "protocol": None}
