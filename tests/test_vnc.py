"""Keyboard injection tests for the minimal RFB client (no network needed)."""

from __future__ import annotations

import struct
import sys
import types
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from fnos_vm.vnc import RfbClient, RfbError  # noqa: E402

SHIFT = 0xFFE1
KEY_TAP = 4  # RFB ClientMessage: KeyEvent


class FakeWebSocket:
    """Records the binary frames the client writes."""

    def __init__(self) -> None:
        self.sock = types.SimpleNamespace(settimeout=lambda *_args: None)
        self.frames: list[tuple[int, bytes]] = []

    def _send_frame(self, opcode: int, data: bytes) -> None:
        self.frames.append((opcode, bytes(data)))


def key_events(client: RfbClient) -> list[tuple[bool, int]]:
    """Decode recorded frames into (is_down, keysym) pairs."""
    events: list[tuple[bool, int]] = []
    for opcode, payload in client.ws.frames:
        assert opcode == 0x2, "keyboard events must be binary frames"
        kind, down, _pad, keysym = struct.unpack(">BBHI", payload)
        assert kind == KEY_TAP
        events.append((bool(down), keysym))
    return events


class TypeTextTests(unittest.TestCase):
    def setUp(self) -> None:
        self.client = RfbClient(FakeWebSocket())

    def type_text(self, text: str) -> list[tuple[bool, int]]:
        self.client.type_text(text, delay=0)
        return key_events(self.client)

    def test_lowercase_has_no_shift(self) -> None:
        events = self.type_text("abc")
        self.assertEqual(
            events,
            [
                (True, ord("a")),
                (False, ord("a")),
                (True, ord("b")),
                (False, ord("b")),
                (True, ord("c")),
                (False, ord("c")),
            ],
        )

    def test_uppercase_presses_shift(self) -> None:
        events = self.type_text("A")
        self.assertEqual(
            events,
            [
                (True, SHIFT),
                (True, ord("a")),
                (False, ord("a")),
                (False, SHIFT),
            ],
        )

    def test_mixed_case_password_round_trip(self) -> None:
        events = self.type_text("Ubuntu2604")
        # Every uppercase letter must be wrapped in a shift press.
        shift_down = False
        typed: list[str] = []
        for down, keysym in events:
            if keysym == SHIFT:
                shift_down = down
                continue
            if down:
                typed.append(chr(keysym).upper() if shift_down else chr(keysym))
        self.assertEqual("".join(typed), "Ubuntu2604")
        self.assertFalse(shift_down, "shift must be released after typing")

    def test_shifted_symbol_still_works(self) -> None:
        events = self.type_text("!")
        self.assertEqual(
            events,
            [
                (True, SHIFT),
                (True, ord("1")),
                (False, ord("1")),
                (False, SHIFT),
            ],
        )

    def test_digits_and_space(self) -> None:
        events = self.type_text("1 ")
        self.assertEqual(
            events,
            [
                (True, ord("1")),
                (False, ord("1")),
                (True, 0x20),
                (False, 0x20),
            ],
        )

    def test_non_ascii_letter_is_rejected(self) -> None:
        with self.assertRaises(RfbError):
            self.type_text("中")

    def test_unknown_character_is_rejected(self) -> None:
        with self.assertRaises(RfbError):
            self.type_text("\t")


class KeySequenceParserTests(unittest.TestCase):
    def parse(self, spec: str):
        from fnos_vm.cli import _parse_key_sequence

        return _parse_key_sequence(spec)

    def test_text_keeps_trailing_space(self) -> None:
        self.assertEqual(self.parse("text:echo "), [("text", "echo ")])

    def test_text_keeps_leading_space(self) -> None:
        self.assertEqual(self.parse("text: hi"), [("text", " hi")])

    def test_text_keeps_case(self) -> None:
        self.assertEqual(self.parse("text:Ubuntu2604"), [("text", "Ubuntu2604")])

    def test_mixed_sequence(self) -> None:
        self.assertEqual(
            self.parse("text:abc ,key:enter,sleep:0.5,combo:ctrl-x"),
            [
                ("text", "abc "),
                ("key", "enter"),
                ("sleep", 0.5),
                ("combo", ["ctrl", "x"]),
            ],
        )

    def test_empty_tokens_are_ignored(self) -> None:
        self.assertEqual(self.parse(",key:enter,,"), [("key", "enter")])


if __name__ == "__main__":
    unittest.main()