"""Tests for autoinstall seed generation and console helpers."""

from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from fnos_vm.cli import _parse_key_sequence  # noqa: E402
from fnos_vm.errors import UsageError  # noqa: E402
from fnos_vm.images import (  # noqa: E402
    autoinstall_user_data,
    network_config_static,
    sha512_crypt,
)
from fnos_vm.vnc import write_png  # noqa: E402


class AutoinstallTests(unittest.TestCase):
    def test_yaml_shape(self):
        network = network_config_static(
            mac="4e:cd:ff:aa:bb:01",
            address="192.168.1.250",
            gateway="192.168.1.1",
            dns=["192.168.1.1"],
        )
        text = autoinstall_user_data(
            user="ubuntu",
            password_hash="$6$salt$hash",
            ssh_keys=["ssh-ed25519 AAAATEST test@example"],
            hostname="ubuntu2604",
            network_config=network,
            storage_layout="direct",
            timezone="Asia/Shanghai",
            shutdown="poweroff",
        )
        self.assertTrue(text.startswith("#cloud-config\n"))
        self.assertIn("autoinstall:\n  version: 1", text)
        self.assertIn("name: direct", text)
        self.assertIn("    version: 2", text)
        self.assertIn("      macaddress: \"4e:cd:ff:aa:bb:01\"", text)
        self.assertIn("shutdown: poweroff", text)
        self.assertIn("ssh-ed25519 AAAATEST test@example", text)

    def test_password_hash(self):
        try:
            hashed = sha512_crypt("test-password")
        except UsageError as exc:
            self.skipTest(str(exc))
        self.assertTrue(hashed.startswith("$6$"))
        self.assertGreater(len(hashed), 20)

    def test_key_sequence(self):
        actions = _parse_key_sequence(
            'sleep:0.1,key:down,down,combo:ctrl-x,text: autoinstall "ds=nocloud-net;s=http://x/"'
        )
        self.assertEqual(actions[0], ("sleep", 0.1))
        self.assertEqual(actions[1], ("key", "down"))
        self.assertEqual(actions[2], ("key", "down"))
        self.assertEqual(actions[3], ("combo", ["ctrl", "x"]))
        self.assertEqual(actions[4][0], "text")
        self.assertEqual(actions[4][1], ' autoinstall "ds=nocloud-net;s=http://x/"')

    def test_png_writer(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "x.png"
            write_png(path, 2, 2, b"\xff\x00\x00" * 4)
            self.assertEqual(path.read_bytes()[:8], b"\x89PNG\r\n\x1a\n")


if __name__ == "__main__":
    unittest.main()
