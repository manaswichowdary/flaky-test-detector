import json
import threading
import time
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from socket import timeout as SocketTimeout
from urllib.error import URLError

import pytest


class _Handler(BaseHTTPRequestHandler):
    delay = 0.0

    def do_GET(self):
        time.sleep(self.delay)
        body = json.dumps({"ok": True}).encode()
        self.send_response(200)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *args):
        pass


def _serve(delay):
    handler = type("H", (_Handler,), {"delay": delay})
    server = ThreadingHTTPServer(("127.0.0.1", 0), handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return server, f"http://127.0.0.1:{server.server_address[1]}"


@pytest.fixture
def server():
    srv, url = _serve(0.0)
    yield url
    srv.shutdown()


@pytest.fixture
def slow_server():
    srv, url = _serve(1.0)
    yield url
    srv.shutdown()


# FIXES: network (demo/tests/test_network.py::test_slow_endpoint_within_timeout)
def test_slow_endpoint_within_timeout(server, slow_server):
    """ROOT_CAUSE
    Cause: the test called an endpoint with random 0-500 ms latency using a 400 ms client
    timeout, so whether it passed depended on the network that day.
    Symptom: intermittent TimeoutError (about 1 run in 5).
    Fix: make latency a fixed property of the test double. One server answers immediately
    (the happy path must succeed); another is deliberately slower than the timeout (the
    timeout path must raise). Both outcomes are now asserted deterministically.
    """
    with urllib.request.urlopen(server + "/slow", timeout=5) as resp:
        assert resp.status == 200
    with pytest.raises((SocketTimeout, URLError, TimeoutError)):
        urllib.request.urlopen(slow_server + "/slow", timeout=0.2)
