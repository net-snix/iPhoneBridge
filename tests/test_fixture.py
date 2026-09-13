"""Native benchmark reports are local, explicit and actually persisted."""
import http.client
import json
from pathlib import Path
import socket
import tempfile
import threading
import time
import unittest
from unittest.mock import patch

from iphonebridge import fixture_server


class FixtureTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.work = Path(self.temp.name)
        patcher = patch.object(fixture_server, 'WORK', self.work)
        patcher.start()
        self.addCleanup(patcher.stop)
        self.server = fixture_server.FixtureServer(('127.0.0.1', 0), fixture_server.Handler)
        self.thread = threading.Thread(target=self.server.serve_forever)
        self.thread.start()
        self.addCleanup(self.close_server)

    def close_server(self):
        self.server.shutdown()
        self.thread.join(2)
        self.server.server_close()

    def post(self, headers):
        connection = http.client.HTTPConnection(*self.server.server_address, timeout=2)
        try:
            connection.request('POST', '/metrics', json.dumps({'id': 'native-test', 'p50': 25}), headers)
            response = connection.getresponse()
            response.read()
            return response.status
        finally:
            connection.close()

    def test_native_report_is_saved_and_acknowledged(self):
        self.assertEqual(self.post({'X-iPhoneBridge-Protocol': 'IPBM/1'}), 204)
        report = json.loads((self.work / 'latency-results.ndjson').read_text())
        self.assertEqual((report['id'], report['p50']), ('native-test', 25))
        self.assertIn('received_at', report)

    def test_browser_or_unmarked_report_is_rejected(self):
        for headers in [{}, {'Origin': 'https://example.com'},
                        {'Origin': 'http://127.0.0.1:15802', 'X-iPhoneBridge-Protocol': 'IPBM/1'}]:
            with self.subTest(headers=headers):
                self.assertEqual(self.post(headers), 403)
        self.assertFalse((self.work / 'latency-results.ndjson').exists())

    def test_incomplete_post_expires_and_valid_report_still_succeeds(self):
        with patch.object(fixture_server, 'BODY_TIMEOUT', 0.1), \
                socket.create_connection(self.server.server_address, timeout=2) as connection:
            connection.sendall(b'POST /metrics HTTP/1.1\r\nHost: localhost\r\n'
                               b'X-iPhoneBridge-Protocol: IPBM/1\r\nContent-Length: 100\r\n\r\n{')
            response = http.client.HTTPResponse(connection)
            response.begin()
            self.assertEqual(response.status, 408)
            response.read()
        self.assertFalse((self.work / 'latency-results.ndjson').exists())
        self.assertEqual(self.post({'X-iPhoneBridge-Protocol': 'IPBM/1'}), 204)

    def test_trickled_body_cannot_extend_the_whole_body_deadline(self):
        stop = threading.Event()
        with patch.object(fixture_server, 'BODY_TIMEOUT', 0.15), \
                socket.create_connection(self.server.server_address, timeout=2) as connection:
            connection.sendall(b'POST /metrics HTTP/1.1\r\nHost: localhost\r\n'
                               b'X-iPhoneBridge-Protocol: IPBM/1\r\nContent-Length: 1000\r\n\r\n{')
            def trickle():
                while not stop.wait(0.02):
                    try:
                        connection.sendall(b' ')
                    except OSError:
                        return
            thread = threading.Thread(target=trickle)
            thread.start()
            started = time.monotonic()
            try:
                response = http.client.HTTPResponse(connection)
                response.begin()
                self.assertEqual(response.status, 408)
                response.read()
                self.assertLess(time.monotonic() - started, 1)
            finally:
                stop.set()
                thread.join(1)
        self.assertFalse((self.work / 'latency-results.ndjson').exists())

    def test_stalled_connections_cannot_create_unbounded_workers(self):
        entered = threading.Event()
        finished = threading.Event()

        class OneWorkerServer(fixture_server.FixtureServer):
            max_workers = 1

            def process_request_thread(self, request, client_address):
                entered.set()
                try:
                    super().process_request_thread(request, client_address)
                finally:
                    finished.set()

        server = OneWorkerServer(('127.0.0.1', 0), fixture_server.Handler)
        thread = threading.Thread(target=server.serve_forever)
        thread.start()
        try:
            with socket.create_connection(server.server_address, timeout=2) as stalled:
                self.assertTrue(entered.wait(1))
                with socket.create_connection(server.server_address, timeout=2) as excess:
                    self.assertEqual(excess.recv(1), b'')
                stalled.shutdown(socket.SHUT_WR)
                self.assertTrue(finished.wait(1))
            connection = http.client.HTTPConnection(*server.server_address, timeout=2)
            try:
                connection.request('POST', '/metrics', '{}', {'X-iPhoneBridge-Protocol': 'IPBM/1'})
                response = connection.getresponse()
                self.assertEqual(response.status, 204)
                response.read()
            finally:
                connection.close()
        finally:
            server.shutdown()
            server.server_close()
            thread.join(2)


if __name__ == '__main__':
    unittest.main()
