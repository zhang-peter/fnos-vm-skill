"""End-to-end CLI tests against a local mock API."""

from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "fnos-vm.py"

GIB = 1024**3


INFO = {
    "name": "mockvm01",
    "uuid": "uuid-1",
    "title": "Tiny11",
    "desc": "",
    "state": "SHUTOFF",
    "osType": "windows",
    "osVersion": "11",
    "autostart": False,
    "chipset": "q35",
    "firmware": "efi",
    "hypervisor": "kvm",
    "cpuMode": "host-passthrough",
    "cpuCoreCnt": 4,
    "memCapacityUnit": "GiB",
    "memCapacityInBytes": 4 * GIB,
    "videoType": "vga",
    "bootType": "",
    "bootIndex": 0,
    "networks": [],
    "disks": [
        {
            "pool": 1,
            "poolName": "vol1",
            "volumePath": "/vol1/vm/pool/x.qcow2",
            "busType": "sata",
            "capacityInBytes": 40 * GIB,
            "capacityUnit": "GiB",
        }
    ],
    "cdroms": {"osFilePath": "", "driverFilePath": ""},
    "hostpcis": [],
    "hostusbs": [],
    "clipboard": True,
}


class Handler(BaseHTTPRequestHandler):
    requests = []

    def log_message(self, format, *args):  # noqa: A003
        pass

    def _send(self, payload, status=200):
        data = json.dumps(payload).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def _handle(self, method):
        parsed = urlsplit(self.path)
        body = None
        length = int(self.headers.get("Content-Length") or 0)
        if length:
            body = json.loads(self.rfile.read(length).decode("utf-8"))
        self.requests.append((method, parsed.path, parse_qs(parsed.query), body))
        if self.headers.get("Authorization") == "trim bad-token":
            self._send({"code": 401, "message": "unauthorized"}, status=401)
            return
        path = parsed.path
        query = parse_qs(parsed.query)
        if path == "/vm/api/v1/domain/list":
            self._send({"code": 0, "data": [{"name": "mockvm01", "title": "Tiny11", "state": "SHUTOFF"}]})
        elif path == "/vm/api/v1/domain/info":
            name = (query.get("name") or [""])[0]
            info = dict(INFO)
            info["name"] = name or INFO["name"]
            self._send({"code": 0, "data": info})
        elif path == "/vm/api/v1/domain/availableTitle":
            self._send({"code": 0, "data": True})
        elif path == "/vm/api/v1/system/hardwareInfo":
            self._send(
                {
                    "code": 0,
                    "data": {
                        "cpu": {"name": "test", "core": 8, "thread": 16},
                        "mem": {"total": 32 * GIB},
                        "gpu": [],
                        "bios": {},
                    },
                }
            )
        elif path == "/vm/api/v1/system/allVolsInfo":
            self._send(
                {
                    "code": 0,
                    "data": {
                        "count": 1,
                        "vols": [{"index": 1, "sysname": "dm-0", "size": 100 * GIB, "used": 10 * GIB}],
                    },
                }
            )
        elif path == "/vm/api/v1/hostdev/support":
            self._send({"code": 0, "data": {"kvm": True, "iommu": True, "sriov": False}})
        elif path == "/vm/api/v1/ovs/isInited":
            self._send({"code": 0, "data": True})
        elif path == "/vm/api/v1/ovs/listDetail":
            self._send({"code": 0, "data": [{"name": "eno1-ovs", "type": 0, "state": "connected"}]})
        elif path == "/vm/api/v1/snapshot/list":
            self._send({"code": 0, "data": []})
        elif path == "/vm/api/v1/domain/start":
            self._send({"code": 0, "msg": "success"})
        elif path == "/vm/api/v1/domain/stop":
            self._send({"code": 0, "msg": "success"})
        elif path == "/vm/api/v1/domain/create":
            self._send({"code": 0, "data": {"name": "newvm"}})
        else:
            self._send({"code": 404, "msg": "not found"}, status=404)

    def do_GET(self):  # noqa: N802
        self._handle("GET")

    def do_POST(self):  # noqa: N802
        self._handle("POST")


class CliTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        cls.port = cls.server.server_address[1]
        cls.thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.thread.start()
        cls.tmp = tempfile.TemporaryDirectory()
        cls.config = Path(cls.tmp.name) / "config.json"
        cls.config.write_text(
            json.dumps(
                {
                    "version": 1,
                    "host": "127.0.0.1",
                    "port": cls.port,
                    "scheme": "http",
                    "token": "test-token",
                    "savedAt": "2026-11-09T00:00:00+08:00",
                    "vms": {},
                }
            ),
            encoding="utf-8",
        )

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()
        cls.tmp.cleanup()

    def setUp(self):
        Handler.requests.clear()

    def run_cli(self, *args, config=None):
        env = os.environ.copy()
        env["FNOS_VM_CONFIG"] = str(config or self.config)
        env["PYTHONIOENCODING"] = "utf-8"
        result = subprocess.run(
            [sys.executable, str(SCRIPT), *args],
            capture_output=True,
            text=True,
            env=env,
            timeout=30,
        )
        return result

    def test_list_json(self):
        result = self.run_cli("--json", "list")
        self.assertEqual(result.returncode, 0, result.stderr)
        payload = json.loads(result.stdout)
        self.assertTrue(payload["ok"])
        self.assertEqual(payload["data"][0]["name"], "mockvm01")
        self.assertEqual(payload["data"][0]["cpuCoreCnt"], 4)

    def test_host_json(self):
        result = self.run_cli("--json", "host")
        self.assertEqual(result.returncode, 0, result.stderr)
        payload = json.loads(result.stdout)
        self.assertTrue(payload["data"]["kvm"])
        self.assertEqual(payload["data"]["memory"]["totalBytes"], 32 * GIB)

    def test_info_json(self):
        result = self.run_cli("--json", "info", "Tiny11")
        self.assertEqual(result.returncode, 0, result.stderr)
        payload = json.loads(result.stdout)
        self.assertEqual(payload["data"]["name"], "mockvm01")
        self.assertFalse(payload["data"]["managedByThisTool"])

    def test_create_dry_run(self):
        result = self.run_cli(
            "--json",
            "create",
            "--name",
            "newvm",
            "--disk",
            "1G",
            "--mem",
            "1G",
            "--no-network",
            "--dry-run",
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        payload = json.loads(result.stdout)
        self.assertTrue(payload["data"]["dryRun"])
        self.assertEqual(payload["data"]["payload"]["networks"], [])
        self.assertEqual(payload["data"]["payload"]["disks"][0]["capacityInBytes"], GIB)

    def test_create_file_with_bom(self):
        path = Path(self.tmp.name) / "vm.json"
        path.write_text(
            "\ufeff" + json.dumps(
                {
                    "name": "from-file",
                    "cpu": 2,
                    "memory": "4G",
                    "disks": [{"size": "2G", "pool": "vol1"}],
                    "networks": ["eno1-ovs"],
                }
            ),
            encoding="utf-8",
        )
        result = self.run_cli("--json", "create", "--file", str(path), "--dry-run")
        self.assertEqual(result.returncode, 0, result.stderr)
        payload = json.loads(result.stdout)["data"]["payload"]
        self.assertEqual(payload["title"], "from-file")
        self.assertEqual(payload["cpuCoreCnt"], 2)
        self.assertEqual(payload["memCapacityInBytes"], 4 * GIB)

    def test_ownership_guard(self):
        result = self.run_cli("--json", "stop", "Tiny11")
        self.assertEqual(result.returncode, 1)
        payload = json.loads(result.stdout)
        self.assertFalse(payload["ok"])
        self.assertEqual(payload["error"]["kind"], "safety")
        self.assertFalse(any(method == "POST" for method, *_ in Handler.requests))

    def test_json_table_conflict(self):
        result = self.run_cli("--json", "--table", "list")
        self.assertEqual(result.returncode, 2)
        self.assertIn("mutually exclusive", result.stderr)

    def test_auth_exit_code(self):
        bad = Path(self.tmp.name) / "bad-config.json"
        bad.write_text(
            json.dumps(
                {
                    "version": 1,
                    "host": "127.0.0.1",
                    "port": self.port,
                    "scheme": "http",
                    "token": "bad-token",
                    "vms": {},
                }
            ),
            encoding="utf-8",
        )
        result = self.run_cli("--json", "list", config=bad)
        self.assertEqual(result.returncode, 3)
        payload = json.loads(result.stdout)
        self.assertEqual(payload["error"]["kind"], "auth")

    def test_table_output(self):
        result = self.run_cli("--table", "list")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("Tiny11", result.stdout)
        self.assertNotIn('"ok"', result.stdout)

    @unittest.skipUnless(os.name == "nt", "Windows .cmd launcher")
    def test_windows_launcher_exit_codes(self):
        """The .cmd wrapper must propagate the CLI exit code, not swallow it."""
        cmd = ROOT / "scripts" / "fnos-vm.cmd"
        env = os.environ.copy()
        env["FNOS_VM_CONFIG"] = str(self.config)
        env["PYTHONIOENCODING"] = "utf-8"
        env["FNOS_VM_PYTHON"] = sys.executable
        for args, expected in (
            ("--json list", 0),
            ("--json --table list", 2),
        ):
            result = subprocess.run(
                # A raw command line keeps cmd.exe quoting predictable:
                # cmd /c ""<launcher>" <args>".
                f'cmd /c ""{cmd}" {args}"',
                capture_output=True,
                text=True,
                env=env,
                timeout=60,
            )
            self.assertEqual(result.returncode, expected, result.stdout + result.stderr)


if __name__ == "__main__":
    unittest.main()
