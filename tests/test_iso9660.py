"""Tests for the minimal ISO 9660 + Joliet seed writer."""

from __future__ import annotations

import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from fnos_vm.images import (  # noqa: E402
    build_seed,
    cloud_init_meta_data,
    cloud_init_user_data,
    network_config_dhcp,
    network_config_static,
)
from fnos_vm.iso9660 import read_iso_files  # noqa: E402


class IsoTests(unittest.TestCase):
    def test_round_trip(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "seed.iso"
            user_data = cloud_init_user_data(
                user="ubuntu",
                password="secret",
                ssh_keys=["ssh-ed25519 AAAATEST test@example"],
                hostname="test-host",
            )
            meta = cloud_init_meta_data(hostname="test-host", instance_id="i-test")
            network = network_config_static(
                mac="4e:cd:ff:aa:bb:01",
                address="192.168.1.250",
                prefix=24,
                gateway="192.168.1.1",
                dns=["192.168.1.1"],
            )
            build_seed(path, user_data=user_data, meta_data=meta, network_config=network)
            self.assertGreater(path.stat().st_size, 18 * 2048)
            files = read_iso_files(path)
            self.assertEqual(files["user-data"].decode(), user_data)
            self.assertEqual(files["meta-data"].decode(), meta)
            self.assertEqual(files["network-config"].decode(), network)
            raw = path.read_bytes()
            self.assertEqual(raw[16 * 2048 + 1 : 16 * 2048 + 6], b"CD001")
            self.assertEqual(raw[17 * 2048 + 1 : 17 * 2048 + 6], b"CD001")
            # Joliet SVD volume ID is UCS-2BE, otherwise udev reports a
            # garbled label and NoCloud label discovery fails.
            self.assertEqual(
                raw[17 * 2048 + 40 : 17 * 2048 + 52],
                "CIDATA".encode("utf-16-be"),
            )

    def test_dhcp_config(self):
        text = network_config_dhcp(mac="4e:cd:ff:aa:bb:01")
        self.assertIn("dhcp4: true", text)
        self.assertIn("4e:cd:ff:aa:bb:01", text)

    @unittest.skipUnless(shutil.which("tar"), "tar not available")
    def test_bsdtar_can_list(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "seed.iso"
            build_seed(
                path,
                user_data="#cloud-config\nhostname: x\n",
                meta_data="instance-id: x\n",
            )
            result = subprocess.run(["tar", "-tf", str(path)], capture_output=True, text=True)
            self.assertEqual(result.returncode, 0, result.stderr)
            names = {line.strip().lstrip("./") for line in result.stdout.splitlines()}
            self.assertIn("user-data", names)
            self.assertIn("meta-data", names)


if __name__ == "__main__":
    unittest.main()
