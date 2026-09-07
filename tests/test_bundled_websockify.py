"""Exercise real spawned HTTP and WebSocket handlers without a phone."""
import base64
import hashlib
import http.client
import os
from pathlib import Path
import shutil
import socket
import socketserver
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from unittest.mock import patch

from iphonebridge import lifecycle


class EchoHandler(socketserver.BaseRequestHandler):
    def handle(self):
        self.request.settimeout(5)
        while data := self.request.recv(4096):
            self.request.sendall(data)


class EchoServer(socketserver.ThreadingTCPServer):
    daemon_threads = True


def read_exact(connection, length):
    data = b""
    while len(data) < length:
        chunk = connection.recv(length - len(data))
        if not chunk:
            raise AssertionError("WebSocket closed before returning its echo")
        data += chunk
    return data


def check_websocket_echo(port):
    with socket.create_connection(("127.0.0.1", port), timeout=5) as connection:
        key = base64.b64encode(os.urandom(16)).decode()
        connection.sendall((f"GET /websockify HTTP/1.1\r\nHost: 127.0.0.1:{port}\r\n"
                            "Upgrade: websocket\r\nConnection: Upgrade\r\n"
                            f"Sec-WebSocket-Key: {key}\r\nSec-WebSocket-Version: 13\r\n"
                            "Sec-WebSocket-Protocol: binary\r\n\r\n").encode())
        header = b""
        while not header.endswith(b"\r\n\r\n"):
            header += read_exact(connection, 1)
            if len(header) > 8192:
                raise AssertionError("Oversized WebSocket handshake")
        assert header.startswith(b"HTTP/1.1 101 "), header
        accept = base64.b64encode(hashlib.sha1(
            (key + "258EAFA5-E914-47DA-95CA-C5AB0DC85B11").encode()).digest())
        assert accept in header, header
        payload = b"iPhoneBridge spawned binary echo\x00\xff"
        mask = os.urandom(4)
        connection.sendall(bytes([0x82, 0x80 | len(payload)]) + mask + bytes(
            value ^ mask[index % 4] for index, value in enumerate(payload)))
        frame = read_exact(connection, 2)
        assert frame[0] == 0x82 and frame[1] == len(payload), frame
        assert read_exact(connection, frame[1]) == payload


class BundledWebsockifyTests(unittest.TestCase):
    def test_spawned_handlers_serve_http_and_websocket_from_bundle_path(self):
        with tempfile.TemporaryDirectory(prefix="bridge bundle with spaces ") as temporary:
            base = Path(temporary)
            package = base / "Example App.app/Contents/Resources/bridge/iphonebridge"
            shutil.copytree(Path(__file__).resolve().parents[1] / "iphonebridge", package,
                            ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))
            webroot = base / "Application Support/web root"
            webroot.mkdir(parents=True)
            expected = b"<!doctype html><title>iPhoneBridge</title>spawned HTTP is working"
            (webroot / "index.html").write_bytes(expected)
            with EchoServer(("127.0.0.1", 0), EchoHandler) as echo:
                thread = threading.Thread(target=echo.serve_forever, daemon=True)
                thread.start()
                try:
                    with socket.socket() as reservation:
                        reservation.bind(("127.0.0.1", 0))
                        port = reservation.getsockname()[1]
                    with (base / "websockify.log").open("w+") as log:
                        # -I matches the real embedded launcher; macOS uses spawn by default.
                        process = subprocess.Popen([
                            sys.executable, "-I", "-B", str(package / "entry.py"), "--module",
                            "websockify", "--web", str(webroot), f"127.0.0.1:{port}",
                            f"127.0.0.1:{echo.server_address[1]}"], cwd=base,
                            stdin=subprocess.DEVNULL, stdout=log, stderr=log, start_new_session=True)
                        try:
                            deadline = time.monotonic() + 10
                            while True:
                                if process.poll() is not None:
                                    log.seek(0)
                                    self.fail(log.read())
                                try:
                                    with socket.create_connection(("127.0.0.1", port), timeout=0.2):
                                        break
                                except OSError:
                                    if time.monotonic() > deadline:
                                        self.fail("websockify did not start")
                                    time.sleep(0.05)
                            for _ in range(2):
                                connection = http.client.HTTPConnection("127.0.0.1", port, timeout=5)
                                try:
                                    connection.request("GET", "/", headers={"Connection": "close"})
                                    response = connection.getresponse()
                                    self.assertEqual(response.status, 200)
                                    self.assertEqual(response.read(), expected)
                                finally:
                                    connection.close()
                            check_websocket_echo(port)
                            with patch.object(lifecycle, "VIEW_PORT", port):
                                self.assertTrue(lifecycle.viewer_responding())
                            self.assertIsNone(process.poll())
                        finally:
                            process.terminate()
                            try:
                                process.wait(timeout=5)
                            except subprocess.TimeoutExpired:
                                process.kill()
                                process.wait(timeout=5)
                finally:
                    echo.shutdown()
                    thread.join(timeout=2)


if __name__ == "__main__":
    unittest.main()
