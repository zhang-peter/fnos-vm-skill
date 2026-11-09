"""Tiny RFC 6455 WebSocket client used for CDP and VNC checks.

No third-party dependencies.  It supports text/binary frames, fragmentation,
ping/pong and close, which is enough for Chrome DevTools Protocol.
"""

from __future__ import annotations

import base64
import json
import os
import socket
import ssl
import struct
import urllib.parse
from typing import Any, Mapping


class WebSocketError(RuntimeError):
    pass


class WebSocket:
    def __init__(self, sock: socket.socket) -> None:
        self.sock = sock
        self._closed = False
        self._buffer = b""

    @classmethod
    def connect(
        cls,
        url: str,
        *,
        headers: Mapping[str, str] | None = None,
        timeout: float = 10.0,
        insecure: bool = False,
    ) -> "WebSocket":
        parsed = urllib.parse.urlsplit(url)
        if parsed.scheme not in ("ws", "wss"):
            raise WebSocketError(f"unsupported WebSocket scheme: {parsed.scheme}")
        host = parsed.hostname
        if not host:
            raise WebSocketError("WebSocket URL has no host")
        port = parsed.port or (443 if parsed.scheme == "wss" else 80)
        path = parsed.path or "/"
        if parsed.query:
            path += "?" + parsed.query
        sock = socket.create_connection((host, port), timeout=timeout)
        if parsed.scheme == "wss":
            context = ssl.create_default_context()
            if insecure:
                context.check_hostname = False
                context.verify_mode = ssl.CERT_NONE
            sock = context.wrap_socket(sock, server_hostname=host)
        key = base64.b64encode(os.urandom(16)).decode("ascii")
        host_header = host if port in (80, 443) else f"{host}:{port}"
        lines = [
            f"GET {path} HTTP/1.1",
            f"Host: {host_header}",
            "Upgrade: websocket",
            "Connection: Upgrade",
            f"Sec-WebSocket-Key: {key}",
            "Sec-WebSocket-Version: 13",
        ]
        for name, value in (headers or {}).items():
            lines.append(f"{name}: {value}")
        request = "\r\n".join(lines) + "\r\n\r\n"
        sock.sendall(request.encode("latin-1"))
        response = b""
        while b"\r\n\r\n" not in response:
            chunk = sock.recv(4096)
            if not chunk:
                raise WebSocketError("connection closed during WebSocket handshake")
            response += chunk
            if len(response) > 65536:
                raise WebSocketError("WebSocket handshake response too large")
        head, _, rest = response.partition(b"\r\n\r\n")
        status_line = head.split(b"\r\n", 1)[0].decode("latin-1", errors="replace")
        if " 101 " not in status_line:
            sock.close()
            raise WebSocketError(f"WebSocket handshake failed: {status_line}")
        ws = cls(sock)
        ws._buffer = rest
        return ws

    # ------------------------------------------------------------------ send
    def _send_frame(self, opcode: int, payload: bytes) -> None:
        if self._closed:
            raise WebSocketError("WebSocket is closed")
        header = bytearray()
        header.append(0x80 | (opcode & 0x0F))  # FIN + opcode
        length = len(payload)
        if length < 126:
            header.append(0x80 | length)
        elif length < 65536:
            header.append(0x80 | 126)
            header.extend(struct.pack(">H", length))
        else:
            header.append(0x80 | 127)
            header.extend(struct.pack(">Q", length))
        mask = os.urandom(4)
        header.extend(mask)
        masked = bytes(byte ^ mask[index % 4] for index, byte in enumerate(payload))
        self.sock.sendall(bytes(header) + masked)

    def send_text(self, text: str) -> None:
        self._send_frame(0x1, text.encode("utf-8"))

    def send_json(self, value: Any) -> None:
        self.send_text(json.dumps(value, ensure_ascii=False))

    # ------------------------------------------------------------------- recv
    def _read_exact(self, count: int) -> bytes:
        while len(self._buffer) < count:
            chunk = self.sock.recv(65536)
            if not chunk:
                raise WebSocketError("WebSocket closed by peer")
            self._buffer += chunk
        data, self._buffer = self._buffer[:count], self._buffer[count:]
        return data

    def _recv_frame(self) -> tuple[int, bytes]:
        first, second = self._read_exact(2)
        fin = bool(first & 0x80)
        opcode = first & 0x0F
        masked = bool(second & 0x80)
        length = second & 0x7F
        if length == 126:
            length = struct.unpack(">H", self._read_exact(2))[0]
        elif length == 127:
            length = struct.unpack(">Q", self._read_exact(8))[0]
        mask = self._read_exact(4) if masked else None
        payload = self._read_exact(length)
        if mask:
            payload = bytes(byte ^ mask[index % 4] for index, byte in enumerate(payload))
        if not fin:
            # Accumulate continuation frames.
            fragments = [payload]
            while True:
                next_first, next_second = self._read_exact(2)
                next_opcode = next_first & 0x0F
                next_length = next_second & 0x7F
                next_masked = bool(next_second & 0x80)
                if next_length == 126:
                    next_length = struct.unpack(">H", self._read_exact(2))[0]
                elif next_length == 127:
                    next_length = struct.unpack(">Q", self._read_exact(8))[0]
                next_mask = self._read_exact(4) if next_masked else None
                fragment = self._read_exact(next_length)
                if next_mask:
                    fragment = bytes(
                        byte ^ next_mask[index % 4] for index, byte in enumerate(fragment)
                    )
                fragments.append(fragment)
                if next_first & 0x80:
                    break
            payload = b"".join(fragments)
        return opcode, payload

    def recv_text(self) -> str:
        while True:
            opcode, payload = self._recv_frame()
            if opcode == 0x1:
                return payload.decode("utf-8")
            if opcode == 0x2:
                return payload.decode("utf-8", errors="replace")
            if opcode == 0x8:
                self._closed = True
                raise WebSocketError("WebSocket closed by peer")
            if opcode == 0x9:
                self._send_frame(0xA, payload)
                continue
            if opcode == 0xA:
                continue

    def recv_binary(self) -> bytes:
        while True:
            opcode, payload = self._recv_frame()
            if opcode in (0x1, 0x2):
                return payload
            if opcode == 0x8:
                self._closed = True
                raise WebSocketError("WebSocket closed by peer")
            if opcode == 0x9:
                self._send_frame(0xA, payload)
                continue
            if opcode == 0xA:
                continue

    def recv_json(self) -> Any:
        return json.loads(self.recv_text())

    def close(self) -> None:
        if self._closed:
            return
        try:
            self._send_frame(0x8, b"")
        except Exception:
            pass
        self._closed = True
        try:
            self.sock.close()
        except Exception:
            pass


class CdpClient:
    """Minimal CDP client over a browser or page WebSocket."""

    def __init__(self, ws_url: str, *, timeout: float = 15.0) -> None:
        self.timeout = timeout
        self.ws = WebSocket.connect(ws_url, timeout=timeout)
        self.ws.sock.settimeout(timeout)
        self._next_id = 0

    def call(self, method: str, params: Mapping[str, Any] | None = None) -> Any:
        self._next_id += 1
        call_id = self._next_id
        self.ws.send_json({"id": call_id, "method": method, "params": dict(params or {})})
        while True:
            message = self.ws.recv_json()
            if message.get("id") != call_id:
                continue
            if "error" in message:
                error = message["error"]
                raise WebSocketError(f"CDP {method} failed: {error.get('message') or error}")
            return message.get("result")

    def close(self) -> None:
        self.ws.close()

    def __enter__(self) -> "CdpClient":
        return self

    def __exit__(self, *exc: Any) -> None:
        self.close()
