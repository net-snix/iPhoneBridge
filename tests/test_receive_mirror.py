"""Validate receiver completion with real framed peers, including static screens."""
from pathlib import Path
import runpy
import socketserver
import struct
import tempfile
import threading
import time
import unittest

from iphonebridge import mirror_protocol as p

ROOT = Path(__file__).resolve().parents[1]
MEASURE = runpy.run_path(str(ROOT / 'scripts/receive-mirror'))['measure']
PARAMETERS = [bytes([kind << 1, 1]) for kind in (32, 33, 34)]
FORMAT = struct.pack('!6I', 1, int.from_bytes(b'hvc1', 'big'), 2, 3, 0, 3)
FORMAT += b''.join(struct.pack('!I', len(item)) + item for item in PARAMETERS)
NAL = b'\x26\x01'
VIDEO = struct.pack('!IQII', 1, 123456, 1, len(NAL)) + NAL


class ReceiverTests(unittest.TestCase):
    def measure_peer(self, messages):
        stop = threading.Event()

        class Peer(socketserver.BaseRequestHandler):
            def handle(self):
                self.request.sendall(p.encode(p.Kind.HELLO, 0,
                                     b'IPBM' + struct.pack('!HH4I', 1, 7, 2, 3, 0, 1)))
                request = p.receive(self.request, time.monotonic() + 2)
                if request.kind == p.Kind.GET_STATS:
                    self.request.sendall(p.encode(p.Kind.STATS, request.request_id, b'{"thermal":0}'))
                    return
                if request.kind != p.Kind.SUBSCRIBE:
                    raise AssertionError('Receiver sent an unexpected request')
                self.request.sendall(p.encode(p.Kind.ACK, request.request_id) + b''.join(messages))
                stop.wait(2)

        class Server(socketserver.ThreadingTCPServer):
            daemon_threads = True

        with tempfile.TemporaryDirectory() as temporary, Server(('127.0.0.1', 0), Peer) as server:
            thread = threading.Thread(target=server.serve_forever, kwargs={'poll_interval': 0.01})
            thread.start()
            try:
                output = Path(temporary) / 'stream.h265'
                report = MEASURE(0.1, output, server.server_address)
                return report, output.read_bytes()
            finally:
                stop.set()
                server.shutdown()
                thread.join(2)

    def test_acknowledged_but_silent_subscription_fails(self):
        report, output = self.measure_peer([])
        self.assertEqual((report['status'], report['frames'], report['formats']), ('failed', 0, 0))
        self.assertIn('no complete FORMAT and keyframe', report['error'])
        self.assertEqual(output, b'')

    def test_format_without_first_keyframe_fails(self):
        report, _ = self.measure_peer([p.encode(p.Kind.FORMAT, 0, FORMAT)])
        self.assertEqual((report['status'], report['frames'], report['formats']), ('failed', 0, 1))

    def test_incomplete_first_keyframe_cannot_qualify_a_stream(self):
        report, output = self.measure_peer([p.encode(p.Kind.FORMAT, 0, FORMAT),
                                           p.encode(p.Kind.VIDEO, 0, VIDEO)[:-1]])
        self.assertEqual((report['status'], report['frames']), ('failed', 0))
        self.assertEqual(output, b''.join(b'\x00\x00\x00\x01' + unit for unit in PARAMETERS))

    def test_complete_keyframe_then_static_screen_succeeds(self):
        report, output = self.measure_peer([p.encode(p.Kind.FORMAT, 0, FORMAT),
                                           p.encode(p.Kind.VIDEO, 0, VIDEO)])
        self.assertEqual((report['status'], report['frames'], report['formats']), ('complete', 1, 1))
        self.assertEqual(output, b''.join(b'\x00\x00\x00\x01' + unit for unit in [*PARAMETERS, NAL]))
        self.assertIn('independent decoder validation required', report['measurement'])

    def test_partial_packet_at_end_does_not_discard_prior_complete_video(self):
        report, output = self.measure_peer([p.encode(p.Kind.FORMAT, 0, FORMAT),
                                           p.encode(p.Kind.VIDEO, 0, VIDEO),
                                           p.encode(p.Kind.VIDEO, 0, VIDEO)[:-1]])
        self.assertEqual((report['status'], report['frames']), ('complete', 1))
        self.assertEqual(output, b''.join(b'\x00\x00\x00\x01' + unit for unit in [*PARAMETERS, NAL]))


if __name__ == '__main__':
    unittest.main()
