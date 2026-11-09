"""Output helpers: machine-readable JSON vs. human tables.

In JSON mode stdout contains exactly one JSON document.  All progress and
human-readable notices go to stderr.
"""

from __future__ import annotations

import json
import sys
import unicodedata
from datetime import datetime, timezone
from typing import Any, Iterable, Sequence


def _width(text: str) -> int:
    width = 0
    for char in text:
        width += 2 if unicodedata.east_asian_width(char) in ("W", "F") else 1
    return width


def _pad(text: str, width: int) -> str:
    return text + " " * max(0, width - _width(text))


def format_bytes(value: Any) -> str:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return "-"
    units = ["B", "KiB", "MiB", "GiB", "TiB", "PiB"]
    index = 0
    while abs(number) >= 1024 and index + 1 < len(units):
        number /= 1024.0
        index += 1
    if index == 0:
        return f"{int(number)} {units[index]}"
    return f"{number:.1f} {units[index]}"


def format_epoch(value: Any) -> str:
    try:
        stamp = int(value)
    except (TypeError, ValueError):
        return "-"
    if stamp <= 0:
        return "-"
    try:
        return datetime.fromtimestamp(stamp, tz=timezone.utc).astimezone().strftime("%Y-%m-%d %H:%M")
    except (OverflowError, OSError, ValueError):
        return str(value)


def state_text(value: Any) -> str:
    mapping = {
        "RUNNING": "running",
        "SHUTOFF": "stopped",
        "PAUSED": "paused",
        "SHUTTING_DOWN": "shutting down",
        "BLOCKED": "blocked",
        "CRASHED": "crashed",
        "OVA_EXPORT": "exporting",
    }
    text = str(value or "-")
    return mapping.get(text, text.lower())


class Output:
    def __init__(self, *, json_mode: bool, table_mode: bool = False, stream=None) -> None:
        if json_mode and table_mode:
            raise ValueError("--json and --table are mutually exclusive")
        self.json_mode = bool(json_mode and not table_mode)
        self.stream = stream or sys.stdout
        self.err = sys.stderr

    def log(self, message: str) -> None:
        print(message, file=self.err)

    def emit(self, data: Any, *, meta: dict[str, Any] | None = None) -> None:
        if not self.json_mode:
            # Human commands render their own tables/key-values after calling
            # emit(); emitting JSON here would duplicate or pollute stdout.
            if isinstance(data, str):
                print(data, file=self.stream)
            return
        document: dict[str, Any] = {"ok": True, "data": data}
        if meta:
            document["meta"] = meta
        json.dump(document, self.stream, ensure_ascii=False, indent=2)
        self.stream.write("\n")
        self.stream.flush()

    def table(self, headers: Sequence[str], rows: Iterable[Sequence[Any]], *, empty: str = "-") -> None:
        rows = [[empty if cell is None else str(cell) for cell in row] for row in rows]
        if not rows:
            print(empty, file=self.stream)
            return
        widths = [_width(str(header)) for header in headers]
        for row in rows:
            for index, cell in enumerate(row):
                if index < len(widths):
                    widths[index] = max(widths[index], _width(cell))
        line = "  ".join(_pad(str(header), widths[i]) for i, header in enumerate(headers))
        print(line.rstrip(), file=self.stream)
        print("  ".join("-" * width for width in widths), file=self.stream)
        for row in rows:
            print("  ".join(_pad(cell, widths[i]) for i, cell in enumerate(row)).rstrip(), file=self.stream)

    def kv(self, rows: Iterable[tuple[str, Any]], *, indent: int = 0) -> None:
        pairs = [(str(key), "-" if value is None else str(value)) for key, value in rows]
        if not pairs:
            return
        width = max(_width(key) for key, _ in pairs)
        prefix = " " * indent
        for key, value in pairs:
            print(prefix + _pad(key, width) + "  " + value, file=self.stream)

    def error(self, payload: dict[str, Any]) -> None:
        if self.json_mode:
            json.dump(payload, self.stream, ensure_ascii=False, indent=2)
            self.stream.write("\n")
            self.stream.flush()
        else:
            error = payload.get("error", {})
            message = error.get("msg") or "error"
            code = error.get("code")
            suffix = f" (code {code})" if code is not None else ""
            print(f"error: {message}{suffix}", file=self.err)


def default_json_mode(stdout=None) -> bool:
    """Agents capture stdout, humans usually have a TTY."""
    stream = stdout or sys.stdout
    try:
        return not stream.isatty()
    except Exception:
        return True
