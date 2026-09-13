"""Bounded, synchronous IPBM v1 framing used by control and diagnostic clients."""
from __future__ import annotations

from dataclasses import dataclass
from enum import IntEnum
import socket
import struct
import time

ENDPOINT = ("127.0.0.1", 15901)
MAX_BODY = 16 * 1024 * 1024
MAX_VIDEO_NALS = 1024
HEADER = struct.Struct("!IBBHI")


class Kind(IntEnum):
    HELLO = 1
    FORMAT = 2
    VIDEO = 3
    STILL = 4
    GEOMETRY = 5
    ACK = 6
    ERROR = 7
    PONG = 8
    STATS = 9
    SUBSCRIBE = 16
    REQ_KEYFRAME = 17
    REQ_STILL = 18
    GET_GEOMETRY = 19
    ACQUIRE_INPUT = 20
    RELEASE_INPUT = 21
    POINTER = 22
    KEY = 23
    BUTTON = 24
    PING = 25
    GET_STATS = 26


class ProtocolError(OSError):
    """The peer violated the native mirror contract."""


class RemoteError(ProtocolError):
    def __init__(self, code):
        self.code = code
        # Never echo arbitrary server text into MCP or logs.
        super().__init__({1: "Malformed mirror request", 2: "Phone input or video is busy",
                          3: "Framebuffer changed; take a new screenshot",
                          4: "Phone capture is unavailable"}.get(code, "Mirror request failed"))


@dataclass(frozen=True)
class Message:
    kind: Kind
    request_id: int
    payload: bytes = b""


@dataclass(frozen=True)
class Geometry:
    width: int
    height: int
    turns: int
    generation: int

    def __post_init__(self):
        if (not 1 <= self.width <= 16384 or not 1 <= self.height <= 16384
                or self.width * self.height * 4 > MAX_BODY - 32
                or self.turns not in range(4) or self.generation == 0):
            raise ProtocolError("Invalid native framebuffer geometry")

    @property
    def size(self):
        return (self.height, self.width) if self.turns % 2 else (self.width, self.height)

    @classmethod
    def decode(cls, data):
        if len(data) != 16:
            raise ProtocolError("Invalid geometry payload")
        return cls(*struct.unpack("!4I", data))


def encode(kind, request_id=0, payload=b""):
    if len(payload) > MAX_BODY - 8 or not 0 <= request_id <= 0xFFFFFFFF:
        raise ValueError("Message exceeds the protocol bounds")
    return HEADER.pack(len(payload) + 8, Kind(kind), 0, 0, request_id) + payload


def _read_exact(sock, size, deadline):
    result = bytearray(size)
    view = memoryview(result)
    offset = 0
    while offset < size:
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise TimeoutError("Mirror response exceeded its deadline")
        sock.settimeout(remaining)
        count = sock.recv_into(view[offset:])
        if count == 0:
            raise ProtocolError("Mirror connection closed before a complete message")
        offset += count
    return bytes(result)


def receive(sock, deadline):
    body, kind, flags, reserved, request_id = HEADER.unpack(_read_exact(sock, HEADER.size, deadline))
    if not 8 <= body <= MAX_BODY or flags or reserved:
        raise ProtocolError("Invalid native message header")
    try:
        kind = Kind(kind)
    except ValueError:
        raise ProtocolError("Unknown native message type") from None
    return Message(kind, request_id, _read_exact(sock, body - 8, deadline))


def hello(message):
    if (message.kind != Kind.HELLO or message.request_id != 0 or len(message.payload) != 24
            or message.payload[:4] != b"IPBM"):
        raise ProtocolError("Endpoint is not an IPBM mirror")
    version, capabilities = struct.unpack_from("!HH", message.payload, 4)
    if version != 1 or capabilities & 7 != 7:
        raise ProtocolError("Unsupported native mirror version or capabilities")
    return Geometry.decode(message.payload[8:])


def parameter_sets(payload):
    if len(payload) < 24:
        raise ProtocolError("Incomplete HEVC format")
    generation, codec, width, height, turns, count = struct.unpack_from("!6I", payload)
    if codec != int.from_bytes(b"hvc1", "big") or count != 3:
        raise ProtocolError("Unsupported HEVC format")
    geometry = Geometry(width, height, turns, generation)
    offset, parameters = 24, []
    for index in range(count):
        if offset + 4 > len(payload):
            raise ProtocolError("Incomplete HEVC parameter size")
        size, = struct.unpack_from("!I", payload, offset)
        offset += 4
        if not 2 <= size <= 65536 or offset + size > len(payload):
            raise ProtocolError("Invalid HEVC parameter size")
        parameter = payload[offset:offset + size]
        if (parameter[0] >> 1) & 0x3F != 32 + index:
            raise ProtocolError("Expected VPS, SPS and PPS in order")
        parameters.append(parameter)
        offset += size
    if offset != len(payload):
        raise ProtocolError("Trailing HEVC format data")
    return geometry, parameters


def video(payload):
    if len(payload) < 22:
        raise ProtocolError("Incomplete HEVC access unit")
    if len(payload) > MAX_BODY - 8:
        raise ProtocolError("HEVC access unit exceeds protocol bounds")
    generation, pts_ns, keyframe = struct.unpack_from("!IQI", payload)
    if not generation or keyframe not in (0, 1):
        raise ProtocolError("Invalid HEVC access unit metadata")
    offset, units = 16, []
    while offset < len(payload):
        # Tiny NALs must not amplify a bounded message into millions of objects.
        if len(units) >= MAX_VIDEO_NALS:
            raise ProtocolError("Excessive HEVC NAL units in one access unit")
        if offset + 4 > len(payload):
            raise ProtocolError("Incomplete HEVC NAL length")
        size, = struct.unpack_from("!I", payload, offset)
        offset += 4
        if size < 2 or offset + size > len(payload):
            raise ProtocolError("Invalid HEVC NAL length")
        units.append(payload[offset:offset + size])
        offset += size
    return generation, pts_ns, bool(keyframe), units


class Client:
    """One control session; request/response order and whole-call deadlines are strict."""

    def __init__(self, *, timeout=5.0, endpoint=ENDPOINT):
        self.timeout = timeout
        self.request_id = 0
        self.sock = socket.create_connection(endpoint, timeout=timeout)
        self.sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
        try:
            self.geometry = hello(receive(self.sock, time.monotonic() + timeout))
        except BaseException:
            self.sock.close()
            raise

    def close(self):
        self.sock.close()

    def request(self, kind, payload=b"", response=Kind.ACK):
        self.request_id = self.request_id % 0xFFFFFFFF + 1
        request_id = self.request_id
        deadline = time.monotonic() + self.timeout
        self.sock.settimeout(self.timeout)
        self.sock.sendall(encode(kind, request_id, payload))
        while True:
            message = receive(self.sock, deadline)
            if message.kind == Kind.GEOMETRY:
                self.geometry = Geometry.decode(message.payload)
                if message.request_id == 0:
                    continue
            if message.request_id != request_id:
                raise ProtocolError("Mirror response has an unexpected request ID")
            if message.kind == Kind.ERROR:
                if not 4 <= len(message.payload) <= 4096:
                    raise ProtocolError("Invalid mirror error")
                raise RemoteError(struct.unpack_from("!I", message.payload)[0])
            if message.kind != response:
                raise ProtocolError("Mirror returned an unexpected response")
            if response == Kind.ACK and message.payload:
                raise ProtocolError("ACK must have an empty payload")
            return message.payload

    def probe(self):
        self.request(Kind.GET_GEOMETRY, response=Kind.GEOMETRY)
        return self.geometry

    def still(self):
        payload = self.request(Kind.REQ_STILL, response=Kind.STILL)
        if len(payload) < 24:
            raise ProtocolError("Incomplete lossless still")
        generation, width, height, turns, pts_ns = struct.unpack_from("!4IQ", payload)
        geometry = Geometry(width, height, turns, generation)
        if len(payload) != 24 + width * height * 4:
            raise ProtocolError("Lossless still has incorrect pixel length")
        self.geometry = geometry
        return geometry, pts_ns, payload[24:]

    def acquire(self):
        self.request(Kind.ACQUIRE_INPUT)

    def release(self):
        self.request(Kind.RELEASE_INPUT)

    def pointer(self, generation, action, x, y):
        self.request(Kind.POINTER, struct.pack("!4I", generation, action, x, y))

    def key(self, generation, usage, down):
        self.request(Kind.KEY, struct.pack("!3I", generation, usage, int(down)))

    def button(self, generation, button):
        self.request(Kind.BUTTON, struct.pack("!2I", generation, button))
