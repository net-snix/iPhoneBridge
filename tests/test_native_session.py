"""Run qualification only against an isolated fake IPBM daemon on an ephemeral port."""
import json
from pathlib import Path
import runpy
import socket
import struct
import threading
import time
import unittest

from iphonebridge import mirror_protocol as p

ROOT = Path(__file__).resolve().parents[1]
QUALIFY = runpy.run_path(str(ROOT / 'scripts/check-native-session'))['qualify']


class FakeDaemon:
    def __init__(self, fault=None):
        self.fault = fault
        self.lock = threading.RLock()
        self.listener = socket.socket()
        self.listener.bind(('127.0.0.1', 0))
        self.listener.listen(8)
        self.listener.settimeout(0.1)
        self.endpoint = self.listener.getsockname()
        self.owner = self.subscriber = None
        self.generation, self.pts, self.next_peer = 1, 1000, 0
        self.stopping = False
        self.commands, self.clients, self.threads = [], [], []
        self.thread = threading.Thread(target=self.accept, daemon=True)
        self.thread.start()

    def close(self):
        self.stopping = True
        self.listener.close()
        for client in self.clients:
            try:
                client.shutdown(socket.SHUT_RDWR)
            except OSError:
                pass
            client.close()
        self.thread.join(1)
        for thread in self.threads:
            thread.join(1)

    def accept(self):
        while not self.stopping:
            try:
                client, _ = self.listener.accept()
            except TimeoutError:
                continue
            except OSError:
                return
            self.next_peer += 1
            self.clients.append(client)
            thread = threading.Thread(target=self.serve, args=(client, self.next_peer), daemon=True)
            self.threads.append(thread)
            thread.start()

    def send(self, client, kind, request=0, payload=b''):
        client.sendall(p.encode(kind, request, payload))

    def error(self, client, request, code):
        self.send(client, p.Kind.ERROR, request, struct.pack('!I', code))

    def frame(self, client, *, recovery=False):
        if not (self.fault == 'missing_initial_format' and not recovery
                or self.fault == 'recovery_without_format' and recovery):
            params = [bytes([kind << 1, 1]) for kind in (32, 33, 34)]
            payload = struct.pack('!6I', self.generation, int.from_bytes(b'hvc1', 'big'), 2, 3, 0, 3)
            payload += b''.join(struct.pack('!I', len(item)) + item for item in params)
            self.send(client, p.Kind.FORMAT, payload=payload)
        self.pts += 1000
        dependent = self.fault == 'dependent_first' and not recovery
        nal_type = 1 if dependent else 21 if self.fault == 'not_idr' else 19
        payload = struct.pack('!IQII', self.generation, self.pts, int(not dependent), 2)
        self.send(client, p.Kind.VIDEO, payload=payload + bytes([nal_type << 1, 1]))

    def serve(self, client, identity):
        try:
            with self.lock:
                self.send(client, p.Kind.HELLO, payload=b'IPBM' + struct.pack('!HH4I', 1, 7, 2, 3, 0, self.generation))
            while not self.stopping:
                request = p.receive(client, time.monotonic() + 2)
                with self.lock:
                    self.commands.append((request.kind, request.payload))
                    kind, rid = request.kind, request.request_id
                    if kind == p.Kind.GET_GEOMETRY:
                        self.send(client, p.Kind.GEOMETRY, rid, struct.pack('!4I', 2, 3, 0, self.generation))
                    elif kind == p.Kind.PING:
                        self.send(client, p.Kind.PONG, rid, request.payload)
                    elif kind == p.Kind.ACQUIRE_INPUT:
                        if self.owner not in (None, identity) and self.fault != 'competing_lease':
                            self.error(client, rid, 2)
                        else:
                            self.owner = identity
                            self.send(client, p.Kind.ACK, rid)
                    elif kind == p.Kind.RELEASE_INPUT:
                        if self.owner == identity:
                            self.owner = None
                        self.send(client, p.Kind.ACK, rid)
                    elif kind == p.Kind.KEY:
                        if self.fault == 'accept_stale':
                            self.send(client, p.Kind.ACK, rid)
                        else:
                            self.error(client, rid, 3)
                    elif kind == p.Kind.SUBSCRIBE:
                        if self.subscriber not in (None, identity):
                            self.error(client, rid, 2)
                        else:
                            self.subscriber = identity
                            self.generation += 1
                            self.send(client, p.Kind.ACK, rid)
                            self.frame(client)
                    elif kind == p.Kind.REQ_KEYFRAME:
                        self.send(client, p.Kind.ACK, rid)
                        self.frame(client, recovery=True)
                    else:
                        self.error(client, rid, 1)
        except (OSError, ValueError):
            pass
        finally:
            with self.lock:
                if self.owner == identity and self.fault != 'retain_disconnected_lease':
                    self.owner = None
                if self.subscriber == identity:
                    self.subscriber = None
            client.close()


class NativeSessionQualificationTests(unittest.TestCase):
    def exercise(self, fault=None):
        server = FakeDaemon(fault)
        self.addCleanup(server.close)
        started = time.monotonic()
        report = QUALIFY(server.endpoint, timeout=0.3, total_timeout=3)
        self.assertLess(time.monotonic() - started, 3.5)
        # JSON serialization is part of the user-facing artifact contract.
        json.dumps(report)
        return server, report

    def test_real_socket_qualification_and_harmless_input_scope(self):
        server, report = self.exercise()
        self.assertEqual(report['status'], 'complete', report)
        self.assertEqual(len(report['checks']), 9)
        self.assertTrue(all(check['status'] == 'passed' for check in report['checks']))
        inputs = [(kind, payload) for kind, payload in server.commands
                  if kind in (p.Kind.KEY, p.Kind.POINTER, p.Kind.BUTTON)]
        self.assertEqual(inputs, [(p.Kind.KEY, struct.pack('!3I', 0, 0, 0))])
        self.assertIn('physical USB cable pull not tested', report['coverage_limits']['disconnect'])
        self.assertIn('not tested', report['coverage_limits']['held_touch_or_key_release'])

    def test_competing_lease_and_stale_generation_regressions_fail(self):
        for fault, check in [('competing_lease', 'competing_input_lease_denied'),
                             ('accept_stale', 'stale_generation_rejected'),
                             ('retain_disconnected_lease', 'input_lease_released_on_tcp_disconnect')]:
            with self.subTest(fault=fault):
                _, report = self.exercise(fault)
                self.assertEqual(report['status'], 'failed')
                self.assertEqual(report['checks'][-1]['name'], check)

    def test_format_dependency_and_keyframe_recovery_regressions_fail(self):
        for fault, check in [('missing_initial_format', 'video_join_format_and_idr'),
                             ('dependent_first', 'video_join_format_and_idr'),
                             ('not_idr', 'video_join_format_and_idr'),
                             ('recovery_without_format', 'request_keyframe_followed_by_format_and_new_idr')]:
            with self.subTest(fault=fault):
                _, report = self.exercise(fault)
                self.assertEqual(report['status'], 'failed')
                self.assertEqual(report['checks'][-1]['name'], check)

    def test_absent_endpoint_fails_with_bounded_report(self):
        sock = socket.socket()
        sock.bind(('127.0.0.1', 0))
        endpoint = sock.getsockname()
        sock.close()
        report = QUALIFY(endpoint, timeout=0.1, total_timeout=1)
        self.assertEqual(report['status'], 'failed')
        self.assertIn('error', report)


if __name__ == '__main__':
    unittest.main()
