"""Real socket framing, golden bytes and malformed-peer failure boundaries."""
import json
from pathlib import Path
import socket
import struct
import threading
import time
import unittest
from unittest.mock import patch

from iphonebridge import mirror_protocol as p

ROOT = Path(__file__).resolve().parents[1]
HELLO = bytes.fromhex("0000002001000000000000004950424d0001000700000492000009e40000000000000001")


class SocketPairTest(unittest.TestCase):
    def setUp(self):
        self.client_socket, self.server = socket.socketpair()
        self.addCleanup(self.client_socket.close)
        self.addCleanup(self.server.close)

    def read(self):
        return p.receive(self.client_socket, time.monotonic() + 0.2)

    def client(self):
        # AF_UNIX socketpair cannot set TCP_NODELAY; a transparent proxy keeps
        # actual reads/writes while allowing the TCP-specific option.
        class Proxy:
            def __init__(self, sock):
                self.sock = sock

            def setsockopt(self, *args):
                pass

            def __getattr__(self, name):
                return getattr(self.sock, name)

        self.server.sendall(HELLO)
        with patch.object(p.socket, "create_connection", return_value=Proxy(self.client_socket)):
            return p.Client(timeout=0.2)

    def test_golden_bytes_both_directions(self):
        vectors = json.loads((ROOT / "docs/mirror-protocol-vectors.json").read_text())
        for vector in vectors:
            with self.subTest(vector=vector["name"]):
                payload = bytes.fromhex(vector["payload_hex"])
                wire = bytes.fromhex(vector["frame_hex"])
                self.assertEqual(p.encode(vector["type"], vector["request_id"], payload), wire)
                self.server.sendall(wire)
                self.assertEqual(self.read(), p.Message(p.Kind(vector["type"]), vector["request_id"], payload))

    def test_fragmented_greeting_is_assembled(self):
        def write():
            for byte in HELLO:
                self.server.sendall(bytes([byte]))
                time.sleep(0.0005)
        thread = threading.Thread(target=write)
        thread.start()
        self.assertEqual(p.hello(self.read()), p.Geometry(1170, 2532, 0, 1))
        thread.join(1)

    def test_header_rejects_before_reading_or_allocating_payload(self):
        for body, kind, flags, reserved in [(7, 1, 0, 0), (p.MAX_BODY + 1, 1, 0, 0),
                                             (32, 1, 1, 0), (32, 1, 0, 1), (8, 255, 0, 0)]:
            with self.subTest(body=body, kind=kind, flags=flags, reserved=reserved):
                self.server.sendall(p.HEADER.pack(body, kind, flags, reserved, 0))
                with self.assertRaises(p.ProtocolError):
                    self.read()

    def test_eof_mid_message_fails(self):
        self.server.sendall(HELLO[:-1])
        self.server.shutdown(socket.SHUT_WR)
        with self.assertRaises(p.ProtocolError):
            self.read()

    def test_whole_response_has_a_deadline(self):
        self.server.sendall(HELLO[:1])
        started = time.monotonic()
        with self.assertRaises(TimeoutError):
            self.read()
        self.assertLess(time.monotonic() - started, 0.5)

    def test_old_rfb_endpoint_rejected(self):
        self.server.sendall(b"RFB 003.008\n")
        with self.assertRaises(p.ProtocolError):
            self.read()

    def test_request_skips_only_unsolicited_geometry_and_correlates_ack(self):
        client = self.client()
        geometry = struct.pack("!4I", 1170, 2532, 1, 2)
        self.server.sendall(p.encode(p.Kind.GEOMETRY, 0, geometry) + p.encode(p.Kind.ACK, 1))
        client.acquire()
        self.assertEqual(client.geometry.size, (2532, 1170))
        self.assertEqual(p.receive(self.server, time.monotonic() + 0.2).kind, p.Kind.ACQUIRE_INPUT)

    def test_wrong_request_id_fails(self):
        client = self.client()
        self.server.sendall(p.encode(p.Kind.ACK, 2))
        with self.assertRaisesRegex(p.ProtocolError, "request ID"):
            client.acquire()

    def test_remote_diagnostics_not_echoed(self):
        client = self.client()
        self.server.sendall(p.encode(p.Kind.ERROR, 1, struct.pack("!I", 2) + b"private detail"))
        with self.assertRaises(p.RemoteError) as error:
            client.acquire()
        self.assertEqual(error.exception.code, 2)
        self.assertNotIn("private detail", str(error.exception))

    def test_lossless_still_has_exact_packed_pixel_length(self):
        client = self.client()
        metadata = struct.pack("!4IQ", 2, 2, 3, 1, 123456)
        pixels = bytes(range(24))
        self.server.sendall(p.encode(p.Kind.STILL, 1, metadata + pixels))
        geometry, pts, actual = client.still()
        self.assertEqual((geometry.size, pts, actual), ((3, 2), 123456, pixels))
        self.server.sendall(p.encode(p.Kind.STILL, 2, metadata + pixels + b"padding"))
        with self.assertRaises(p.ProtocolError):
            client.still()

    def test_read_only_health_greeting_sends_nothing(self):
        client = self.client()
        self.server.settimeout(0.01)
        with self.assertRaises(TimeoutError):
            self.server.recv(1)
        client.close()


class PayloadTests(unittest.TestCase):
    def test_geometry_is_bounded_and_dimensions_follow_rotation(self):
        for turn in range(4):
            self.assertEqual(p.Geometry(1170, 2532, turn, 1).size,
                             (2532, 1170) if turn % 2 else (1170, 2532))
        for values in [(0, 2, 0, 1), (16384, 16384, 0, 1), (2, 3, 4, 1), (2, 3, 0, 0)]:
            with self.assertRaises(p.ProtocolError):
                p.Geometry(*values)

    def test_format_and_access_unit_enforce_nal_lengths(self):
        sets = [bytes([kind << 1, 1]) for kind in (32, 33, 34)]
        format_payload = struct.pack("!6I", 1, int.from_bytes(b"hvc1", "big"), 1170, 2532, 0, 3)
        format_payload += b"".join(struct.pack("!I", len(item)) + item for item in sets)
        self.assertEqual(p.parameter_sets(format_payload)[1], sets)
        unit = struct.pack("!IQII", 1, 123456, 1, 2) + b"\x26\x01"
        self.assertEqual(p.video(unit), (1, 123456, True, [b"\x26\x01"]))
        for malformed in [unit[:-1], unit + b"\x00", unit[:16] + b"\xff\xff\xff\xff"]:
            with self.assertRaises(p.ProtocolError):
                p.video(malformed)
        with self.assertRaises(p.ProtocolError):
            p.parameter_sets(format_payload + b"trailing")

    def test_access_unit_limits_tiny_nal_amplification(self):
        header = struct.pack("!IQI", 1, 123456, 1)
        nal = struct.pack("!I", 2) + b"\x26\x01"
        valid = header + nal * p.MAX_VIDEO_NALS
        self.assertEqual(len(p.video(valid)[3]), p.MAX_VIDEO_NALS)
        with self.assertRaisesRegex(p.ProtocolError, "Excessive HEVC NAL units"):
            p.video(valid + nal * 100_000)

    def test_access_unit_rejects_oversized_direct_payload(self):
        with self.assertRaisesRegex(p.ProtocolError, "exceeds protocol bounds"):
            p.video(bytes(p.MAX_BODY))


if __name__ == "__main__":
    unittest.main()
