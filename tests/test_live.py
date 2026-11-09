"""Optional live tests against a real fnOS host.

Enable read-only checks with FNOS_VM_LIVE=1.  Destructive lifecycle checks
require FNOS_VM_LIVE_WRITE=1 and create/delete a temporary VM.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
import unittest
import uuid
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from fnos_vm.client import ApiClient  # noqa: E402
from fnos_vm.config import load_config  # noqa: E402

LIVE = os.environ.get("FNOS_VM_LIVE") == "1"
WRITE = LIVE and os.environ.get("FNOS_VM_LIVE_WRITE") == "1"
SCRIPT = ROOT / "scripts" / "fnos-vm.py"


def make_client() -> ApiClient:
    config = load_config()
    assert config is not None
    return ApiClient(config.host, config.port, config.scheme, config.token, timeout=30)


@unittest.skipUnless(LIVE, "set FNOS_VM_LIVE=1 to run live read-only checks")
class LiveReadTests(unittest.TestCase):
    def test_host_and_list(self):
        client = make_client()
        self.assertTrue(client.hostdev_support().get("kvm"))
        vms = client.list_vms()
        self.assertIsInstance(vms, list)
        for vm in vms:
            info = client.info(str(vm["name"]))
            self.assertEqual(info["name"], vm["name"])
            self.assertIn("state", info)

    def test_first_vm_read_only(self):
        client = make_client()
        vms = client.list_vms()
        if not vms:
            self.skipTest("no VMs on host")
        name = str(vms[0]["name"])
        info = client.info(name)
        self.assertEqual(info["name"], name)
        self.assertIsInstance(client.snapshot_list(name), list)


@unittest.skipUnless(WRITE, "set FNOS_VM_LIVE_WRITE=1 to run destructive lifecycle checks")
class LiveWriteTests(unittest.TestCase):
    def test_lifecycle(self):
        config = load_config()
        assert config is not None
        with tempfile.TemporaryDirectory() as tmp:
            cfg_path = Path(tmp) / "config.json"
            cfg_path.write_text(
                json.dumps(
                    {
                        "version": 1,
                        "host": config.host,
                        "port": config.port,
                        "scheme": config.scheme,
                        "token": config.token,
                        "savedAt": config.savedAt,
                        "vms": {},
                    }
                ),
                encoding="utf-8",
            )
            env = os.environ.copy()
            env["FNOS_VM_CONFIG"] = str(cfg_path)
            env["PYTHONIOENCODING"] = "utf-8"
            name = "dsh-live-" + uuid.uuid4().hex[:6]

            def run(*args):
                result = subprocess.run(
                    [sys.executable, str(SCRIPT), "--json", *args],
                    capture_output=True,
                    text=True,
                    env=env,
                    timeout=180,
                )
                self.assertEqual(result.returncode, 0, result.stderr)
                return json.loads(result.stdout)

            created_name = None
            try:
                created = run(
                    "create",
                    "--name",
                    name,
                    "--cpu",
                    "1",
                    "--mem",
                    "1G",
                    "--disk",
                    "1G",
                    "--no-network",
                    "--desc",
                    "fnos-vm live test",
                )
                created_name = created["data"]["after"]["name"]
                run("start", name, "--wait", "30")
                run("wait", name, "--state", "running", "--timeout", "20")
                run("stop", name, "--force", "--wait", "30")
                run("snapshot", "create", name, "--desc", "live test")
                snaps = run("snapshot", "list", name)["data"]
                self.assertTrue(snaps)
                run("snapshot", "revert", name, snaps[0]["name"], "--yes")
                run("snapshot", "delete", name, snaps[0]["name"], "--yes")
                run("delete", name, "--yes")
                created_name = None
            finally:
                if created_name:
                    subprocess.run(
                        [sys.executable, str(SCRIPT), "--json", "delete", created_name, "--yes"],
                        capture_output=True,
                        text=True,
                        env=env,
                        timeout=180,
                    )


if __name__ == "__main__":
    unittest.main()
