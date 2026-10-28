import json
import random
import threading
import time
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest


class _Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        if self.path == "/slow":
            # Simulates an upstream with variable latency.
            time.sleep(random.uniform(0.0, 0.5))
        body = json.dumps({"path": self.path, "ok": True}).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *args):
        pass


@pytest.fixture(scope="module")
def server_url():
    server = ThreadingHTTPServer(("127.0.0.1", 0), _Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield f"http://127.0.0.1:{server.server_address[1]}"
    server.shutdown()


def _get(url, timeout):
    with urllib.request.urlopen(url, timeout=timeout) as resp:
        return resp.status, json.loads(resp.read())


def test_ok_endpoint(server_url):
    status, body = _get(server_url + "/ok", timeout=5)
    assert status == 200 and body["ok"]


def test_echoes_path(server_url):
    _, body = _get(server_url + "/items/7", timeout=5)
    assert body["path"] == "/items/7"


def test_json_content_type(server_url):
    with urllib.request.urlopen(server_url + "/ok", timeout=5) as resp:
        assert resp.headers["Content-Type"] == "application/json"


# PLANTED-FLAKY: network
def test_slow_endpoint_within_timeout(server_url):
    status, _ = _get(server_url + "/slow", timeout=0.4)
    assert status == 200
