"""Local configuration, credentials and local ownership registry."""

from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .errors import UsageError


CONFIG_ENV = "FNOS_VM_CONFIG"
DEFAULT_PORT = 5666


def config_path() -> Path:
    override = os.environ.get(CONFIG_ENV)
    if override:
        return Path(override).expanduser()
    return Path.home() / ".fnos-vm" / "config.json"


def _secure_directory(path: Path) -> None:
    path.mkdir(parents=True, exist_ok=True)
    try:
        if os.name == "nt":
            user = os.environ.get("USERNAME") or ""
            if user:
                subprocess.run(
                    ["icacls", str(path), "/inheritance:r", "/grant:r", f"{user}:(OI)(CI)F"],
                    capture_output=True,
                    text=True,
                    timeout=15,
                )
        else:
            os.chmod(path, 0o700)
    except Exception:
        # Permission tightening is best-effort; the file itself is still secured.
        pass


def _secure_file(path: Path) -> None:
    try:
        if os.name == "nt":
            user = os.environ.get("USERNAME") or ""
            if user:
                subprocess.run(
                    ["icacls", str(path), "/inheritance:r", "/grant:r", f"{user}:F"],
                    capture_output=True,
                    text=True,
                    timeout=15,
                )
        else:
            os.chmod(path, 0o600)
    except Exception:
        pass


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


@dataclass
class Config:
    host: str
    port: int = DEFAULT_PORT
    scheme: str = "http"
    token: str = ""
    savedAt: str = ""
    vms: dict[str, dict[str, Any]] = field(default_factory=dict)

    @property
    def base_url(self) -> str:
        return f"{self.scheme}://{self.host}:{self.port}"

    def is_owned(self, name: str) -> bool:
        return name in self.vms

    def record_vm(self, info: dict[str, Any], *, source: str = "create") -> None:
        name = info.get("name")
        if not name:
            return
        self.vms[name] = {
            "name": name,
            "title": info.get("title") or "",
            "uuid": info.get("uuid") or "",
            "createdAt": self.vms.get(name, {}).get("createdAt") or _now_iso(),
            "source": source,
        }

    def adopt_vm(self, info: dict[str, Any]) -> None:
        name = info.get("name")
        if not name:
            return
        self.vms[name] = {
            "name": name,
            "title": info.get("title") or "",
            "uuid": info.get("uuid") or "",
            "createdAt": _now_iso(),
            "source": "adopt",
        }

    def forget_vm(self, name: str) -> None:
        self.vms.pop(name, None)


def load_config(*, required: bool = True) -> Config | None:
    path = config_path()
    if not path.exists():
        if not required:
            return None
        raise UsageError(
            "not logged in: run `fnos-vm login --host <host> --token <ost>` "
            "or `fnos-vm login --host <host> --from-cdp` first"
        )
    try:
        raw = json.loads(path.read_text(encoding="utf-8-sig"))
    except Exception as exc:
        raise UsageError(f"cannot read config {path}: {exc}") from exc
    if not isinstance(raw, dict):
        raise UsageError(f"invalid config {path}: expected a JSON object")
    host = str(raw.get("host") or "").strip()
    token = str(raw.get("token") or "").strip()
    if not host or not token:
        raise UsageError(f"invalid config {path}: host and token are required")
    try:
        port = int(raw.get("port") or DEFAULT_PORT)
    except (TypeError, ValueError) as exc:
        raise UsageError(f"invalid config {path}: port must be an integer") from exc
    scheme = str(raw.get("scheme") or "http").lower()
    if scheme not in ("http", "https"):
        raise UsageError(f"invalid config {path}: scheme must be http or https")
    vms = raw.get("vms")
    if not isinstance(vms, dict):
        vms = {}
    clean_vms = {str(k): v for k, v in vms.items() if isinstance(v, dict)}
    return Config(
        host=host,
        port=port,
        scheme=scheme,
        token=token,
        savedAt=str(raw.get("savedAt") or ""),
        vms=clean_vms,
    )


def save_config(config: Config) -> Path:
    path = config_path()
    _secure_directory(path.parent)
    if not config.savedAt:
        config.savedAt = _now_iso()
    payload = {
        "version": 1,
        "host": config.host,
        "port": config.port,
        "scheme": config.scheme,
        "token": config.token,
        "savedAt": config.savedAt,
        "vms": config.vms,
    }
    data = json.dumps(payload, ensure_ascii=False, indent=2) + "\n"
    fd, tmp_name = tempfile.mkstemp(prefix=".config-", suffix=".tmp", dir=str(path.parent))
    tmp = Path(tmp_name)
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as handle:
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
        _secure_file(tmp)
        os.replace(tmp, path)
        _secure_file(path)
    finally:
        try:
            if tmp.exists():
                tmp.unlink()
        except Exception:
            pass
    return path


def delete_config() -> bool:
    path = config_path()
    try:
        path.unlink()
        return True
    except FileNotFoundError:
        return False


def describe_config_path() -> str:
    return str(config_path())


def warn_if_weak_permissions() -> str | None:
    """Return a warning string when the config is readable by other users."""
    path = config_path()
    if not path.exists():
        return None
    if os.name != "nt":
        try:
            mode = path.stat().st_mode & 0o777
            if mode & 0o077:
                return f"config {path} is readable by other users (mode {oct(mode)})"
        except OSError:
            return None
    return None


if __name__ == "__main__":  # pragma: no cover - manual diagnostics
    cfg = load_config(required=False)
    if cfg is None:
        print("no config")
        sys.exit(1)
    print(json.dumps({"host": cfg.host, "port": cfg.port, "scheme": cfg.scheme, "vms": list(cfg.vms)}))
