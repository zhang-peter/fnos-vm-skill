"""Tests for image download/resize/prepare orchestration."""

from __future__ import annotations

import functools
import http.server
import struct
import sys
import tempfile
import threading
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from fnos_vm.errors import BusinessError  # noqa: E402
from fnos_vm.images import download, prepare_image, sha256_file  # noqa: E402
from fnos_vm.payload import read_qcow2_size  # noqa: E402


def make_qcow2(path: Path, virtual_size: int = 64 * 1024 * 1024) -> None:
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
        virtual_size,
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
    path.write_bytes(data + b"\x00" * (cluster * 3))


class QuietHandler(http.server.SimpleHTTPRequestHandler):
    def log_message(self, format, *args):  # noqa: A003
        pass


class ImageTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        cls.root = Path(cls.tmp.name)
        cls.image = cls.root / "source.qcow2"
        make_qcow2(cls.image, 64 * 1024 * 1024)
        handler = functools.partial(QuietHandler, directory=str(cls.root))
        cls.server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), handler)
        cls.port = cls.server.server_address[1]
        cls.thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.thread.start()

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()
        cls.tmp.cleanup()

    def test_download_and_hash(self):
        dest = self.root / "download.qcow2"
        url = f"http://127.0.0.1:{self.port}/source.qcow2"
        path = download(url, dest, force=True)
        self.assertEqual(path, dest)
        self.assertEqual(path.stat().st_size, 4 * 65536)
        self.assertEqual(sha256_file(path), sha256_file(self.image))

    def test_prepare_resize(self):
        dest = self.root / "prepared.qcow2"
        result = prepare_image(
            source=f"http://127.0.0.1:{self.port}/source.qcow2",
            dest=dest,
            disk_size=20 * 1024**3,
            force=True,
        )
        self.assertEqual(result["virtualSizeBytes"], 20 * 1024**3)
        self.assertEqual(read_qcow2_size(str(dest)), 20 * 1024**3)

    def test_download_404(self):
        with self.assertRaises(BusinessError):
            download(f"http://127.0.0.1:{self.port}/missing.qcow2", self.root / "missing.qcow2")


if __name__ == "__main__":
    unittest.main()
