"""Serialized native mirror actions with lossless, post-action screenshots."""
from __future__ import annotations

from contextlib import contextmanager
from datetime import datetime, timezone
import fcntl
import io
import math
import os
import time
from uuid import uuid4

from PIL import Image
from PIL.PngImagePlugin import PngInfo

from . import keyboard
from .mirror_protocol import Client, ProtocolError, RemoteError
from .runtime import PATHS

WORK = PATHS.data
SCREENSHOTS = PATHS.screenshots
CALL_TIMEOUT = 5.0
ACTION_TIMEOUT = 20.0
LOCK_TIMEOUT = 25.0
KEYS = keyboard.NAMED_KEYS


class BridgeError(Exception):
    """An expected failure with a deliberately safe, user-facing message."""


class BridgeInputError(BridgeError, ValueError):
    pass


class BridgeActionError(BridgeError, RuntimeError):
    pass


class BridgeTimeoutError(BridgeError, TimeoutError):
    pass


CONNECTION_ERRORS = (ProtocolError,)
EXPECTED_ACTION_ERRORS = (BridgeError, OSError)


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
        self.generation = client.geometry.generation

    def call(self, method, *args):
        remaining = self.deadline - time.monotonic()
        if remaining <= 0:
            raise BridgeTimeoutError("Bridge action exceeded its time limit")
        self.client.timeout = min(CALL_TIMEOUT, remaining)
        return getattr(self.client, method)(*args)

    def frame(self):
        geometry, pts_ns, pixels = self.call("still")
        image = Image.frombytes("RGB", (geometry.width, geometry.height), pixels, "raw", "BGRX")
        rotations = {1: Image.Transpose.ROTATE_270, 2: Image.Transpose.ROTATE_180,
                     3: Image.Transpose.ROTATE_90}
        if geometry.turns:
            image = image.transpose(rotations[geometry.turns])
        output = io.BytesIO()
        # The retained capture API renders sRGB, including on Display P3 phones.
        # Declare the actual colour space without converting or quantizing pixels.
        metadata = PngInfo()
        metadata.add(b"sRGB", b"\x00")
        image.save(output, format="PNG", pnginfo=metadata)
        return output.getvalue(), geometry, pts_ns

    def size(self):
        return self.call("probe").size

    def release(self, method, *args):
        # Attempt release even after the action deadline. Disconnect is a second
        # server-enforced cleanup barrier if transport failure prevents the ACK.
        self.client.timeout = 2.0
        return getattr(self.client, method)(*args)

    def pointer(self, action, x, y):
        return self.call("pointer", self.generation, action, x, y)

    def key(self, usage, down):
        return self.call("key", self.generation, usage, down)


@contextmanager
def _session():
    with _locked():
        client = Client(timeout=CALL_TIMEOUT)
        try:
            yield _Session(client)
        finally:
            client.close()


def _dimensions(width, height):
    if any(type(value) is not int or not 1 <= value <= 16384 for value in (width, height)):
        raise BridgeInputError("width and height must be integer framebuffer dimensions from screenshot")


def _point(x, y, width, height):
    if type(x) is not int or type(y) is not int or not (0 <= x < width and 0 <= y < height):
        raise BridgeInputError("Coordinates must be integer pixels inside the expected framebuffer")


def _check_frame(session, width, height, generation):
    geometry = session.call("probe")
    actual = geometry.size
    if actual != (width, height):
        raise BridgeInputError(f"Framebuffer changed: expected {width}x{height}, actual {actual[0]}x{actual[1]}; take a new screenshot")
    if geometry.generation != generation:
        raise BridgeInputError(
            f"Framebuffer generation changed: expected {generation}, actual {geometry.generation}; take a new screenshot"
        )
    # Keep the screenshot's generation pinned, including after asynchronous
    # geometry updates; the daemon rejects rotation during the operation.
    session.generation = generation


def _save(session, action):
    data, geometry, pts_ns = session.frame()
    SCREENSHOTS.mkdir(parents=True, exist_ok=True, mode=0o700)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S.%fZ")
    path = SCREENSHOTS / f"{stamp}-{uuid4().hex[:8]}-{action}.png"
    with path.open("xb") as out:
        os.chmod(path, 0o600)
        out.write(data)
    width, height = geometry.size
    return {"action": action, "path": str(path), "width": width, "height": height,
            "coordinate_space": "framebuffer_pixels", "captured_at": stamp,
            "generation": geometry.generation, "capture_pts_ns": pts_ns, "lossless": True,
            "color_space": "sRGB"}


def screenshot():
    """Return a fresh, lossless PNG in oriented native framebuffer pixels."""
    with _session() as session:
        return _save(session, "screenshot")


def _mutate(action, width, height, generation, operation):
    _dimensions(width, height)
    if type(generation) is not int or not 1 <= generation <= 0xFFFFFFFF:
        raise BridgeInputError("generation must be an integer from 1 to 4294967295 from the latest screenshot")
    with _session() as session:
        try:
            session.call("acquire")
        except RemoteError as error:
            if error.code == 2:
                raise BridgeActionError("Phone input is in use; wait for the current gesture to finish") from None
            raise
        try:
            _check_frame(session, width, height, generation)
            try:
                operation(session)
                time.sleep(0.25)
                return _save(session, action)
            except EXPECTED_ACTION_ERRORS:
                raise BridgeActionError(
                    f"{action} may have been applied but verification failed; take a screenshot before retrying"
                ) from None
        finally:
            try:
                session.release("release")
            except OSError:
                # Closing the connection also releases the lease. Preserve the
                # original action error if a transport failure prevents an ACK.
                pass


def tap(x: int, y: int, width: int, height: int, *, generation: int):
    _dimensions(width, height)
    _point(x, y, width, height)

    def operation(session):
        try:
            session.pointer(1, x, y)
            time.sleep(0.08)
        finally:
            session.release("pointer", session.generation, 0, x, y)

    return _mutate("tap", width, height, generation, operation)


def drag(x1: int, y1: int, x2: int, y2: int, width: int, height: int, duration: float = 0.5,
         *, generation: int):
    _dimensions(width, height)
    _point(x1, y1, width, height)
    _point(x2, y2, width, height)
    if isinstance(duration, bool) or not isinstance(duration, (int, float)) or not math.isfinite(duration) or not 0.1 <= duration <= 5:
        raise BridgeInputError("duration must be between 0.1 and 5 seconds")

    def operation(session):
        x, y = x1, y1
        try:
            session.pointer(1, x, y)
            steps = max(2, math.ceil(duration * 30))
            start = time.monotonic()
            for step in range(1, steps + 1):
                time.sleep(max(0, start + duration * step / steps - time.monotonic()))
                x, y = (round(x1 + (x2 - x1) * step / steps),
                        round(y1 + (y2 - y1) * step / steps))
                session.pointer(2, x, y)
        finally:
            session.release("pointer", session.generation, 0, x, y)

    return _mutate("drag", width, height, generation, operation)


def _press(session, usage):
    try:
        session.key(usage, True)
        time.sleep(0.015)
    finally:
        session.release("key", session.generation, usage, False)


def _type_character(session, char):
    usage, shifted = keyboard.character(char)
    if not shifted:
        return _press(session, usage)
    try:
        session.key(keyboard.SHIFT, True)
        _press(session, usage)
    finally:
        session.release("key", session.generation, keyboard.SHIFT, False)


def type_text(text: str, width: int, height: int, *, generation: int):
    """Type ASCII with the US layout. Text is never written to action metadata."""
    if not isinstance(text, str) or not 1 <= len(text) <= 256:
        raise BridgeInputError("text must contain 1–256 characters")
    if any(not (32 <= ord(char) <= 126 or char in "\n\t") for char in text):
        raise BridgeInputError("Only printable ASCII, tab and newline are supported by the server")

    def operation(session):
        for char in text:
            _type_character(session, char)

    return _mutate("type", width, height, generation, operation)


def key(name: str, width: int, height: int, *, generation: int):
    if not isinstance(name, str) or name.lower() not in KEYS:
        raise BridgeInputError("Supported keys: " + ", ".join(KEYS))
    return _mutate("key", width, height, generation, lambda session: _press(session, KEYS[name.lower()]))


def navigate(name: str, width: int, height: int, *, generation: int):
    if name not in ("home", "app-switcher"):
        raise BridgeInputError("Supported navigation actions: home, app-switcher")

    def operation(session):
        session.call("button", session.generation, 1 if name == "home" else 2)
        time.sleep(0.5)

    return _mutate(name, width, height, generation, operation)


def health():
    """Read native HELLO without subscribing, sending input or starting services."""
    try:
        client = Client(timeout=3)
        try:
            return {"ready": True, "endpoint": "127.0.0.1:15901", "protocol": "IPBM/1",
                    "width": client.geometry.size[0], "height": client.geometry.size[1]}
        finally:
            client.close()
    except OSError:
        return {"ready": False, "endpoint": "127.0.0.1:15901", "protocol": None}
