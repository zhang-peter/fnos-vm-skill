"""Tests for the HTTP client against a local mock API."""

from __future__ import annotations

import json
import sys
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from fnos_vm.client import ApiClient  # noqa: E402
from fnos_vm.errors import AuthError, BusinessError, NetworkError  # noqa: E402


class MockState:
    def __init__(self):
        self.routes = {}
        self.requests = []


class Handler(BaseHTTPRequestHandler):
    state: MockState

    def log_message(self, format, *args):  # noqa: A003
        pass

    def _handle(self, method: str) -> None:
        parsed = urlsplit(self.path)
        body = None
        length = int(self.headers.get("Content-Length") or 0)
        if length:
            raw = self.rfile.read(length)
            try:
                body = json.loads(raw.decode("utf-8"))
            except Exception:
                body = raw.decode("utf-8", errors="replace")
        self.state.requests.append(
            {
                "method": method,
                "path": parsed.path,
                "query": parse_qs(parsed.query),
                "body": body,
                "authorization": self.headers.get("Authorization"),
            }
        )
        route = self.state.routes.get(parsed.path)
        if route is None:
            self.send_response(404)
            self.end_headers()
            return
        status, payload = route
        data = json.dumps(payload).encode("utf-8") if not isinstance(payload, bytes) else payload
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self):  # noqa: N802
        self._handle("GET")

    def do_POST(self):  # noqa: N802
        self._handle("POST")


class ClientTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.state = MockState()
        Handler.state = cls.state
        cls.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        cls.port = cls.server.server_address[1]
        cls.thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.thread.start()

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()

    def setUp(self):
        self.state.routes.clear()
        self.state.requests.clear()

    def client(self, token="token") -> ApiClient:
        return ApiClient("127.0.0.1", self.port, "http", token, timeout=5)

    def test_list_and_auth_header(self):
        self.state.routes["/vm/api/v1/domain/list"] = (200, {"code": 0, "data": [{"name": "a"}]})
        data = self.client().list_vms()
        self.assertEqual(data, [{"name": "a"}])
        self.assertEqual(self.state.requests[0]["authorization"], "trim token")

    def test_query_params(self):
        self.state.routes["/vm/api/v1/domain/info"] = (
            200,
            {"code": 0, "data": {"name": "abc", "state": "SHUTOFF"}},
        )
        self.client().info("abc")
        self.assertEqual(self.state.requests[0]["query"]["name"], ["abc"])

    def test_auth_error_401(self):
        self.state.routes["/vm/api/v1/domain/list"] = (401, {"code": 401, "message": "unauthorized"})
        with self.assertRaises(AuthError):
            self.client().list_vms()

    def test_auth_error_code_10001(self):
        self.state.routes["/vm/api/v1/domain/list"] = (200, {"code": 10001, "msg": "unlogin"})
        with self.assertRaises(AuthError):
            self.client().list_vms()

    def test_business_error(self):
        self.state.routes["/vm/api/v1/domain/info"] = (200, {"code": 12021, "msg": "vm not found"})
        with self.assertRaises(BusinessError) as ctx:
            self.client().info("missing")
        self.assertEqual(ctx.exception.code, 12021)

    def test_post_body(self):
        self.state.routes["/vm/api/v1/domain/start"] = (200, {"code": 0, "msg": "success"})
        self.client().start("abc")
        request = self.state.requests[0]
        self.assertEqual(request["method"], "POST")
        self.assertEqual(request["body"], {"name": "abc"})
        self.assertEqual(request["authorization"], "trim token")

    def test_available_title(self):
        self.state.routes["/vm/api/v1/domain/availableTitle"] = (200, {"code": 0, "data": True})
        self.assertTrue(self.client().available_title("new"))
        self.assertEqual(self.state.requests[0]["query"]["title"], ["new"])

    def test_vm_by_ref(self):
        self.state.routes["/vm/api/v1/domain/list"] = (
            200,
            {
                "code": 0,
                "data": [
                    {"name": "n1", "title": "Ubuntu"},
                    {"name": "n2", "title": "Tiny11"},
                ],
            },
        )
        client = self.client()
        self.assertEqual(client.vm_by_ref("n1")["name"], "n1")
        self.assertEqual(client.vm_by_ref("ubuntu")["name"], "n1")
        self.assertEqual(client.vm_by_ref("Ubu")["name"], "n1")
        with self.assertRaises(BusinessError):
            client.vm_by_ref("missing")

    def test_network_error(self):
        client = ApiClient("127.0.0.1", 1, "http", "token", timeout=0.2)
        with self.assertRaises(NetworkError):
            client.list_vms()

    def test_snapshot_payloads(self):
        self.state.routes["/vm/api/v1/snapshot/create"] = (200, {"code": 0, "data": {"name": "s1"}})
        self.state.routes["/vm/api/v1/snapshot/delete"] = (200, {"code": 0})
        self.client().snapshot_create("vm", "s1", "desc")
        self.assertEqual(
            self.state.requests[0]["body"],
            {"name": "vm", "snapshotName": "s1", "snapshotDesc": "desc"},
        )
        self.client().snapshot_delete("vm", "s1")
        self.assertEqual(self.state.requests[1]["body"], {"name": "vm", "snapshotName": "s1"})


if __name__ == "__main__":
    unittest.main()
