"""Offline unit tests for payload construction and validation."""

from __future__ import annotations

import struct
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from fnos_vm.errors import PreflightError, UsageError  # noqa: E402
from fnos_vm.payload import (  # noqa: E402
    build_create_payload,
    build_modify_payload,
    memory_fields,
    parse_disk_spec,
    parse_network_spec,
    parse_size,
    patch_qcow2_size,
    preflight_create,
    read_qcow2_size,
    validate_remote_path,
    validate_title,
)

GIB = 1024**3
MIB = 1024**2


def host_context():
    hardware = {"cpu": {"name": "test", "core": 8, "thread": 16}, "mem": {"total": 32 * GIB}}
    volumes = {"vols": [{"index": 1, "sysname": "dm-0", "size": 100 * GIB, "used": 10 * GIB}]}
    ovs = [{"name": "eno1-ovs", "type": 0, "state": "connected"}]
    support = {"kvm": True, "iommu": True, "sriov": False}
    return hardware, volumes, ovs, support


class SizeTests(unittest.TestCase):
    def test_parse_size(self):
        self.assertEqual(parse_size("1G"), GIB)
        self.assertEqual(parse_size("1GiB"), GIB)
        self.assertEqual(parse_size("512M"), 512 * MIB)
        self.assertEqual(parse_size("4096"), 4096)
        self.assertEqual(parse_size("1.5G"), int(1.5 * GIB))

    def test_parse_size_rejects_bad(self):
        for value in ("", "abc", "-1G", "0"):
            with self.assertRaises(UsageError):
                parse_size(value)

    def test_memory_fields(self):
        self.assertEqual(memory_fields(2 * GIB), (2, "GB", 2 * GIB))
        self.assertEqual(memory_fields(512 * MIB), (512, "MB", 512 * MIB))
        with self.assertRaises(UsageError):
            memory_fields(1000)

    def test_title(self):
        self.assertEqual(validate_title("  ubuntu  "), "ubuntu")
        for value in ("", ".", "..", "a" * 65):
            with self.assertRaises(UsageError):
                validate_title(value)


class PathTests(unittest.TestCase):
    def test_valid(self):
        self.assertEqual(validate_remote_path("/vol1/1000/iso/x.iso"), "/vol1/1000/iso/x.iso")
        self.assertEqual(validate_remote_path("/vol12/vm/pool/x.qcow2"), "/vol12/vm/pool/x.qcow2")

    def test_invalid(self):
        for value in ("/tmp/x.iso", "relative.iso", "/vol1/../x.iso", "C:\\x.iso", "/vol1/x\n.iso", ""):
            with self.assertRaises(UsageError):
                validate_remote_path(value)

    def test_extension(self):
        with self.assertRaises(UsageError):
            validate_remote_path("/vol1/x.qcow2", extensions=(".iso",))


class DiskNetworkTests(unittest.TestCase):
    def test_disk_spec(self):
        aliases = {"1": 1, "vol1": 1, "/vol1": 1}
        self.assertEqual(parse_disk_spec("20G@vol1", aliases), {"capacityInBytes": 20 * GIB, "pool": 1})

    def test_network_spec(self):
        network = parse_network_spec("eno1-ovs", ["eno1-ovs"])
        self.assertEqual(network["source"], "eno1-ovs")
        self.assertEqual(network["nicModel"], "e1000")
        self.assertRegex(network["macAddr"], r"^[0-9a-f:]{17}$")
        network = parse_network_spec("eno1-ovs:virtio@4e:cd:ff:aa:bb:01", ["eno1-ovs"])
        self.assertEqual(network["nicModel"], "virtio")
        self.assertEqual(network["macAddr"], "4e:cd:ff:aa:bb:01")
        with self.assertRaises(UsageError):
            parse_network_spec("missing", ["eno1-ovs"])


class CreatePayloadTests(unittest.TestCase):
    def test_minimal(self):
        hardware, volumes, ovs, support = host_context()
        payload = build_create_payload(
            {
                "title": "test",
                "osType": "linux",
                "cpuCoreCnt": 2,
                "memCapacityInBytes": 2 * GIB,
                "disks": ["10G"],
                "networks": ["eno1-ovs"],
            },
            hardware=hardware,
            volumes=volumes,
            ovs_list=ovs,
            support=support,
        )
        self.assertEqual(payload["title"], "test")
        self.assertEqual(payload["memCapacity"], 2)
        self.assertEqual(payload["memCapacityUnit"], "GB")
        self.assertEqual(payload["disks"][0]["pool"], 1)
        self.assertEqual(payload["disks"][0]["busType"], "virtio")
        self.assertEqual(payload["networks"][0]["source"], "eno1-ovs")
        self.assertEqual(payload["bootType"], "default")

    def test_no_network(self):
        hardware, volumes, ovs, support = host_context()
        payload = build_create_payload(
            {"title": "test", "disks": ["1G"], "networks": []},
            hardware=hardware,
            volumes=volumes,
            ovs_list=ovs,
            support=support,
        )
        self.assertEqual(payload["networks"], [])

    def test_import_image(self):
        hardware, volumes, ovs, support = host_context()
        payload = build_create_payload(
            {
                "title": "img",
                "osType": "linux",
                "image": "/vol1/1000/Temp/u.qcow2",
                "imageSize": "20G",
                "networks": [],
            },
            hardware=hardware,
            volumes=volumes,
            ovs_list=ovs,
            support=support,
        )
        self.assertEqual(payload["disks"][0]["actionType"], "import")
        self.assertEqual(payload["disks"][0]["sourcePath"], "/vol1/1000/Temp/u.qcow2")
        self.assertEqual(payload["disks"][0]["capacityInBytes"], 20 * GIB)

    def test_file_convenience_fields(self):
        hardware, volumes, ovs, support = host_context()
        payload = build_create_payload(
            {
                "name": "fromfile",
                "cpu": 4,
                "memory": "8G",
                "iso": "/vol1/1000/iso/u.iso",
                "disks": [{"size": "20G", "pool": "vol1"}],
                "networks": ["eno1-ovs"],
            },
            hardware=hardware,
            volumes=volumes,
            ovs_list=ovs,
            support=support,
        )
        self.assertEqual(payload["title"], "fromfile")
        self.assertEqual(payload["cpuCoreCnt"], 4)
        self.assertEqual(payload["memCapacityInBytes"], 8 * GIB)
        self.assertEqual(payload["cdroms"]["osFilePath"], "/vol1/1000/iso/u.iso")
        self.assertEqual(payload["bootType"], "cdrom")

    def test_preflight_memory(self):
        hardware, volumes, ovs, support = host_context()
        payload = build_create_payload(
            {"title": "big", "memCapacityInBytes": 40 * GIB, "disks": ["1G"], "networks": []},
            hardware=hardware,
            volumes=volumes,
            ovs_list=ovs,
            support=support,
        )
        with self.assertRaises(PreflightError):
            preflight_create(
                payload,
                hardware=hardware,
                volumes=volumes,
                ovs_inited=True,
                ovs_list=ovs,
                support=support,
                vms=[],
            )

    def test_preflight_disk(self):
        hardware, volumes, ovs, support = host_context()
        payload = build_create_payload(
            {"title": "bigdisk", "disks": ["95G"], "networks": []},
            hardware=hardware,
            volumes=volumes,
            ovs_list=ovs,
            support=support,
        )
        with self.assertRaises(PreflightError):
            preflight_create(
                payload,
                hardware=hardware,
                volumes=volumes,
                ovs_inited=True,
                ovs_list=ovs,
                support=support,
                vms=[],
            )


class ModifyTests(unittest.TestCase):
    def test_modify(self):
        info = {
            "name": "n1",
            "uuid": "u1",
            "title": "old",
            "cpuCoreCnt": 1,
            "memCapacityUnit": "GiB",
            "memCapacityInBytes": GIB,
            "desc": "",
            "autostart": False,
            "clipboard": True,
            "disks": [],
            "networks": [],
        }
        payload = build_modify_payload(info, {"cpu": 2, "mem": "4G", "desc": "new", "autostart": True})
        self.assertEqual(payload["cpuCoreCnt"], 2)
        self.assertEqual(payload["memCapacityInBytes"], 4 * GIB)
        self.assertEqual(payload["desc"], "new")
        self.assertTrue(payload["autostart"])
        self.assertNotIn("memCapacityUnit", payload)
        self.assertNotIn("memCapacity", payload)
        self.assertEqual(payload["name"], "n1")
        self.assertEqual(payload["uuid"], "u1")


class Qcow2Tests(unittest.TestCase):
    def _make_qcow2(self, path: Path, size: int) -> None:
        cluster = 65536
        data = bytearray(cluster)
        struct.pack_into(
            ">IIQIIQIIQQIIQQQQII",
            data,
            0,
            0x514649FB,
            3,
            0,
            0,
            16,
            size,
            0,
            1,
            3 * cluster,
            1 * cluster,
            1,
            0,
            0,
            0,
            0,
            0,
            4,
            104,
        )
        path.write_bytes(data + b"\x00" * (cluster * 3 - len(data)))

    def test_read_and_patch(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "x.qcow2"
            self._make_qcow2(path, 64 * MIB)
            self.assertEqual(read_qcow2_size(str(path)), 64 * MIB)
            old, new = patch_qcow2_size(str(path), 20 * GIB)
            self.assertEqual((old, new), (64 * MIB, 20 * GIB))
            self.assertEqual(read_qcow2_size(str(path)), 20 * GIB)
            with self.assertRaises(UsageError):
                patch_qcow2_size(str(path), 10 * GIB)


if __name__ == "__main__":
    unittest.main()
