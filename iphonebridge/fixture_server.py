"""Local-only disposable fixtures and explicit performance-test coordination."""
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
import json
import threading
import time
from .runtime import PATHS

ROOT = PATHS.root
WORK = PATHS.data
BODY_TIMEOUT = 5.0
MAX_BODY = 1_048_576


class FixtureServer(ThreadingHTTPServer):
    """Limit accepted fixture handlers, including clients stalled before a body."""
    max_workers = 16

    def __init__(self, *args, **kwargs):
        self._workers = threading.BoundedSemaphore(self.max_workers)
        super().__init__(*args, **kwargs)

    def process_request(self, request, client_address):
        if not self._workers.acquire(blocking=False):
            self.shutdown_request(request)
            return
        try:
            super().process_request(request, client_address)
        except BaseException:
            self._workers.release()
            raise

    def process_request_thread(self, request, client_address):
        try:
            super().process_request_thread(request, client_address)
        finally:
            self._workers.release()


class Handler(SimpleHTTPRequestHandler):
    timeout = 5.0

    def __init__(self, *args, **kwargs):
        super().__init__(*args, directory=str(ROOT / 'fixtures'), **kwargs)

    def end_headers(self):
        self.send_header('Cache-Control', 'no-store')
        super().end_headers()

    def do_GET(self):
        if self.path == '/benchmark-run':
            request = WORK / 'benchmark-request.json'
            data = request.read_bytes() if request.exists() else b'{}'
            self.send_response(200)
            self.send_header('Content-Type', 'application/json')
            self.send_header('Content-Length', str(len(data)))
            self.end_headers()
            self.wfile.write(data)
            return
        super().do_GET()

    def do_POST(self):
        if (self.path != '/metrics' or self.headers.get('Origin') is not None
                or self.headers.get('X-iPhoneBridge-Protocol') != 'IPBM/1'):
            self.send_error(403)
            return
        try:
            length = int(self.headers.get('Content-Length', '0'))
            if not 1 <= length <= MAX_BODY:
                raise ValueError()
            report = json.loads(self.read_body(length))
            if not isinstance(report, dict):
                raise ValueError()
        except TimeoutError:
            self.send_error(408)
            return
        except (ValueError, RecursionError):
            self.send_error(400)
            return
        report['received_at'] = time.time()
        with (WORK / 'latency-results.ndjson').open('a') as output:
            output.write(json.dumps(report) + '\n')
        self.send_response(204)
        self.end_headers()

    def read_body(self, length):
        deadline = time.monotonic() + BODY_TIMEOUT
        original_timeout = self.connection.gettimeout()
        body = bytearray()
        try:
            while len(body) < length:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise TimeoutError("Fixture request body exceeded its deadline")
                self.connection.settimeout(remaining)
                chunk = self.rfile.read1(min(65536, length - len(body)))
                if not chunk:
                    raise ValueError("Incomplete fixture request body")
                body.extend(chunk)
            return body
        finally:
            self.connection.settimeout(original_timeout)

if __name__ == '__main__':
    PATHS.ensure_data()
    FixtureServer(('127.0.0.1', 15802), Handler).serve_forever()
