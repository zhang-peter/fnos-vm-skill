"""Minimal RFB (VNC) client over the fnOS VNC WebSocket.

Only the parts needed for console automation are implemented: no-auth
handshake, Raw encoding, framebuffer screenshots and keyboard events.  VNC
password authentication is intentionally unsupported.
"""

from __future__ import annotations

import os
import struct
import time
import zlib
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .ws import WebSocket


class RfbError(RuntimeError):
    pass


KEY_NAMES = {
    "backspace": 0xFF08,
    "tab": 0xFF09,
    "enter": 0xFF0D,
    "return": 0xFF0D,
    "esc": 0xFF1B,
    "escape": 0xFF1B,
    "home": 0xFF50,
    "left": 0xFF51,
    "up": 0xFF52,
    "right": 0xFF53,
    "down": 0xFF54,
    "pageup": 0xFF55,
    "pagedown": 0xFF56,
    "end": 0xFF57,
    "insert": 0xFF63,
    "delete": 0xFFFF,
    "ctrl": 0xFFE3,
    "control": 0xFFE3,
    "shift": 0xFFE1,
    "alt": 0xFFE9,
    "super": 0xFFEB,
    "space": 0x20,
    "f1": 0xFFBE,
    "f2": 0xFFBF,
    "f3": 0xFFC0,
    "f4": 0xFFC1,
    "f5": 0xFFC2,
    "f6": 0xFFC3,
    "f7": 0xFFC4,
    "f8": 0xFFC5,
    "f9": 0xFFC6,
    "f10": 0xFFC7,
    "f11": 0xFFC8,
    "f12": 0xFFC9,
}

# Characters that need Shift on a US keyboard.
SHIFT_CHARS = {
    "!": "1",
    "@": "2",
    "#": "3",
    "$": "4",
    "%": "5",
    "^": "6",
    "&": "7",
    "*": "8",
    "(": "9",
    ")": "0",
    "_": "-",
    "+": "=",
    "{": "[",
    "}": "]",
    "|": "\\",
    ":": ";",
    '"': "'",
    "<": ",",
    ">": ".",
    "?": "/",
    "~": "`",
    '"': "'",
}

CHAR_KEYSYMS = {
    "-": 0x2D,
    "=": 0x3D,
    "[": 0x5B,
    "]": 0x5D,
    "\\": 0x5C,
    ";": 0x3B,
    "'": 0x27,
    ",": 0x2C,
    ".": 0x2E,
    "/": 0x2F,
    "`": 0x60,
}


@dataclass
class Framebuffer:
    width: int
    height: int
    rgb: bytes


def _png_chunk(kind: bytes, data: bytes) -> bytes:
    return (
        struct.pack(">I", len(data))
        + kind
        + data
        + struct.pack(">I", zlib.crc32(kind + data) & 0xFFFFFFFF)
    )


def write_png(path: str | os.PathLike[str], width: int, height: int, rgb: bytes) -> Path:
    if len(rgb) != width * height * 3:
        raise RfbError("framebuffer size does not match dimensions")
    stride = width * 3
    raw = b"".join(b"\x00" + rgb[y * stride : (y + 1) * stride] for y in range(height))
    png = [
        b"\x89PNG\r\n\x1a\n",
        _png_chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0)),
        _png_chunk(b"IDAT", zlib.compress(raw, 6)),
        _png_chunk(b"IEND", b""),
    ]
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(b"".join(png))
    return target


class RfbClient:
    def __init__(self, ws: WebSocket, *, timeout: float = 20.0) -> None:
        self.ws = ws
        self.timeout = timeout
        self.ws.sock.settimeout(timeout)
        self._buf = b""
        self.width = 0
        self.height = 0
        self.name = ""
        self.bpp = 32
        self.depth = 24
        self.big_endian = False
        self.true_color = True
        self.rgb = b""
        self._last_update = 0.0

    @classmethod
    def connect(
        cls,
        url: str,
        *,
        headers: dict[str, str] | None = None,
        timeout: float = 20.0,
        insecure: bool = False,
    ) -> "RfbClient":
        ws = WebSocket.connect(url, headers=headers, timeout=timeout, insecure=insecure)
        client = cls(ws, timeout=timeout)
        client.handshake()
        return client

    # ------------------------------------------------------------------ wire
    def read_exact(self, count: int) -> bytes:
        while len(self._buf) < count:
            chunk = self.ws.recv_binary()
            if not chunk:
                raise RfbError("VNC stream closed")
            self._buf += chunk
        data, self._buf = self._buf[:count], self._buf[count:]
        return data

    def send(self, data: bytes) -> None:
        # The fnOS WebSocket bridge expects binary frames.  WebSocket.send_binary
        # is not exposed separately in our tiny client, so use a text frame with
        # latin-1 mapping (the bridge forwards the bytes unchanged).
        self.ws._send_frame(0x2, data)  # noqa: SLF001 - intentional internal use

    # --------------------------------------------------------------- handshake
    def handshake(self) -> None:
        version = self.read_exact(12)
        if not version.startswith(b"RFB "):
            raise RfbError(f"not an RFB server: {version!r}")
        # Reply with the protocol version we implement.
        self.send(b"RFB 003.008\n")
        count = self.read_exact(1)[0]
        if count == 0:
            reason_len = struct.unpack(">I", self.read_exact(4))[0]
            reason = self.read_exact(reason_len).decode("utf-8", errors="replace")
            raise RfbError(f"VNC server refused connection: {reason}")
        types = self.read_exact(count)
        if 1 not in types:
            raise RfbError(
                "VNC server requires authentication (security types "
                + ", ".join(str(item) for item in types)
                + "); this client only supports None"
            )
        self.send(b"\x01")
        result = struct.unpack(">I", self.read_exact(4))[0]
        if result != 0:
            reason_len = struct.unpack(">I", self.read_exact(4))[0]
            reason = self.read_exact(reason_len).decode("utf-8", errors="replace")
            raise RfbError(f"VNC authentication failed: {reason}")
        self.send(b"\x01")  # shared desktop
        init = self.read_exact(24)
        self.width, self.height = struct.unpack(">HH", init[:4])
        (
            self.bpp,
            self.depth,
            self.big_endian,
            self.true_color,
        ) = struct.unpack(">BBBB", init[4:8])
        name_len = struct.unpack(">I", init[20:24])[0]
        self.name = self.read_exact(name_len).decode("utf-8", errors="replace")
        self.rgb = b"\x00" * (self.width * self.height * 3)
        self.set_pixel_format()

    def set_pixel_format(self) -> None:
        pixel_format = struct.pack(
            ">BBBBHHHBBB3x",
            32,
            24,
            0,
            1,
            255,
            255,
            255,
            16,
            8,
            0,
        )
        self.send(b"\x00\x00\x00\x00" + pixel_format)

    def set_encodings(self, encodings: list[int] | None = None) -> None:
        encodings = encodings if encodings is not None else [0]
        payload = struct.pack(">BBH", 2, 0, len(encodings))
        payload += b"".join(struct.pack(">i", item) for item in encodings)
        self.send(payload)

    def request_update(self, *, incremental: bool = False, x: int = 0, y: int = 0, w: int | None = None, h: int | None = None) -> None:
        w = self.width if w is None else w
        h = self.height if h is None else h
        self.send(struct.pack(">BBHHHH", 3, 1 if incremental else 0, x, y, w, h))

    # ------------------------------------------------------------------ read
    def read_message(self) -> str:
        kind = self.read_exact(1)[0]
        if kind == 0:
            self._read_framebuffer_update()
            return "framebuffer"
        if kind == 1:
            self.read_exact(3)
            _first, count = struct.unpack(">HH", self.read_exact(4))
            self.read_exact(count * 6)
            return "colourmap"
        if kind == 2:
            return "bell"
        if kind == 3:
            self.read_exact(3)
            length = struct.unpack(">I", self.read_exact(4))[0]
            self.read_exact(length)
            return "cuttext"
        raise RfbError(f"unsupported RFB message type {kind}")

    def _read_framebuffer_update(self) -> None:
        self.read_exact(1)  # padding
        count = struct.unpack(">H", self.read_exact(2))[0]
        for _ in range(count):
            x, y, w, h, encoding = struct.unpack(">HHHHi", self.read_exact(12))
            if encoding == 0:
                data = self.read_exact(w * h * 4)
                self._blit_rgb32(x, y, w, h, data)
            elif encoding == -223:  # DesktopSize pseudo-encoding
                self.width, self.height = w, h
                self.rgb = b"\x00" * (w * h * 3)
            elif encoding == -239:  # Cursor pseudo-encoding
                self.read_exact(w * h * 4)
            else:
                raise RfbError(f"unsupported RFB rectangle encoding {encoding}")
        self._last_update = time.monotonic()

    def _blit_rgb32(self, x: int, y: int, w: int, h: int, data: bytes) -> None:
        if len(data) != w * h * 4:
            raise RfbError("short RFB framebuffer rectangle")
        target = bytearray(self.rgb)
        stride = self.width * 3
        for row in range(h):
            src = row * w * 4
            dst = (y + row) * stride + x * 3
            for col in range(w):
                base = src + col * 4
                target[dst + col * 3] = data[base + 2]
                target[dst + col * 3 + 1] = data[base + 1]
                target[dst + col * 3 + 2] = data[base]
        self.rgb = bytes(target)

    def wait_for_update(self, *, timeout: float | None = None) -> Framebuffer:
        timeout = self.timeout if timeout is None else timeout
        deadline = time.monotonic() + timeout
        self.set_encodings()
        self.request_update()
        while time.monotonic() < deadline:
            message = self.read_message()
            if message == "framebuffer":
                return Framebuffer(self.width, self.height, self.rgb)
        raise RfbError("timed out waiting for a VNC framebuffer update")

    def screenshot(self, path: str | os.PathLike[str], *, timeout: float | None = None) -> Path:
        frame = self.wait_for_update(timeout=timeout)
        return write_png(path, frame.width, frame.height, frame.rgb)

    # --------------------------------------------------------------- keyboard
    def key(self, name_or_keysym: str | int, down: bool) -> None:
        if isinstance(name_or_keysym, int):
            keysym = name_or_keysym
        else:
            key = name_or_keysym.lower()
            if key in KEY_NAMES:
                keysym = KEY_NAMES[key]
            elif len(key) == 1:
                keysym = ord(key)
            else:
                raise RfbError(f"unknown key name {name_or_keysym!r}")
        self.send(struct.pack(">BBHI", 4, 1 if down else 0, 0, keysym))

    def tap(self, name_or_keysym: str | int) -> None:
        self.key(name_or_keysym, True)
        self.key(name_or_keysym, False)

    def combo(self, *keys: str) -> None:
        for key in keys:
            self.key(key, True)
        for key in reversed(keys):
            self.key(key, False)

    def type_text(self, text: str, *, delay: float = 0.02) -> None:
        for char in text:
            if char in SHIFT_CHARS:
                self.combo("shift", SHIFT_CHARS[char])
            elif char.isalpha():
                self.tap(char.lower())
            elif char.isdigit():
                self.tap(char)
            elif char == " ":
                self.tap("space")
            elif char in CHAR_KEYSYMS:
                self.tap(CHAR_KEYSYMS[char])
            else:
                raise RfbError(f"cannot type character {char!r}")
            if delay:
                time.sleep(delay)

    def close(self) -> None:
        self.ws.close()

    def __enter__(self) -> "RfbClient":
        return self

    def __exit__(self, *exc: Any) -> None:
        self.close()
