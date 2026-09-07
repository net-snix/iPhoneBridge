"""Local-only disposable fixtures and explicit performance-test coordination."""
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
import json
from pathlib import Path
import time
from .runtime import PATHS

ROOT = PATHS.root
WORK = PATHS.data


class Handler(SimpleHTTPRequestHandler):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, directory=str(ROOT / 'fixtures'), **kwargs)

    def end_headers(self):
        self.send_header('Access-Control-Allow-Origin', 'http://127.0.0.1:15801')
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
        if self.path != '/metrics' or self.headers.get('Origin') != 'http://127.0.0.1:15801':
            self.send_error(403)
            return
        try:
            length = int(self.headers.get('Content-Length', '0'))
            if not 1 <= length <= 128_000:
                raise ValueError()
            report = json.loads(self.rfile.read(length))
            if not isinstance(report, dict):
                raise ValueError()
        except (ValueError, json.JSONDecodeError):
            self.send_error(400)
            return
        report['received_at'] = time.time()
        with (WORK / 'latency-results.ndjson').open('a') as output:
            output.write(json.dumps(report) + '\n')
        self.send_response(204)
        self.end_headers()

    def do_OPTIONS(self):
        self.send_response(204)
        self.send_header('Access-Control-Allow-Methods', 'GET, POST, OPTIONS')
        self.send_header('Access-Control-Allow-Headers', 'Content-Type')
        self.end_headers()


if __name__ == '__main__':
    PATHS.ensure_data()
    ThreadingHTTPServer(('127.0.0.1', 15802), Handler).serve_forever()
