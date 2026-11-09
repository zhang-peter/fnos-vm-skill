"""Chrome DevTools Protocol helpers for the one-time ``ost`` cookie bootstrap."""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import time
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any

from .errors import NetworkError, UsageError
from .ws import CdpClient, WebSocketError


DEFAULT_CDP = "http://127.0.0.1:9222"


def _http_json(url: str, *, method: str = "GET", timeout: float = 3.0) -> Any:
    request = urllib.request.Request(url, method=method)
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return json.loads(response.read().decode("utf-8"))
    except Exception as exc:
        raise NetworkError(f"cannot reach CDP endpoint {url}: {exc}") from exc


def cdp_available(cdp_url: str) -> bool:
    try:
        _http_json(cdp_url.rstrip("/") + "/json/version", timeout=1.5)
        return True
    except Exception:
        return False


def list_targets(cdp_url: str) -> list[dict[str, Any]]:
    data = _http_json(cdp_url.rstrip("/") + "/json/list")
    return data if isinstance(data, list) else []


def _cookies_from_browser(cdp_url: str) -> list[dict[str, Any]]:
    version = _http_json(cdp_url.rstrip("/") + "/json/version")
    ws_url = version.get("webSocketDebuggerUrl")
    if not ws_url:
        raise NetworkError("CDP /json/version did not return webSocketDebuggerUrl")
    with CdpClient(ws_url) as client:
        result = client.call("Storage.getCookies")
    cookies = result.get("cookies") if isinstance(result, dict) else None
    return cookies if isinstance(cookies, list) else []


def _cookies_from_page(cdp_url: str, host: str | None) -> list[dict[str, Any]]:
    targets = list_targets(cdp_url)
    target = None
    if host:
        for item in targets:
            url = str(item.get("url") or "")
            if host in url:
                target = item
                break
    if target is None:
        pages = [item for item in targets if item.get("type") == "page"]
        target = pages[0] if pages else None
    if target is None:
        # Chrome requires PUT for /json/new.
        url = urllib.parse.quote(f"http://{host}:5666/vm" if host else "about:blank", safe="")
        target = _http_json(cdp_url.rstrip("/") + f"/json/new?{url}", method="PUT")
    ws_url = target.get("webSocketDebuggerUrl") if isinstance(target, dict) else None
    if not ws_url:
        raise NetworkError("no page target with a WebSocket debugger URL")
    with CdpClient(ws_url) as client:
        try:
            client.call("Network.enable")
        except WebSocketError:
            pass
        result = client.call("Network.getAllCookies")
    cookies = result.get("cookies") if isinstance(result, dict) else None
    return cookies if isinstance(cookies, list) else []


def get_cookies(cdp_url: str = DEFAULT_CDP, *, host: str | None = None) -> list[dict[str, Any]]:
    cookies: list[dict[str, Any]] = []
    try:
        cookies = _cookies_from_browser(cdp_url)
    except Exception:
        cookies = []
    if cookies:
        return cookies
    # Some Chromium builds expose no browser-level cookie jar; fall back to a
    # page target's Network.getAllCookies.
    return _cookies_from_page(cdp_url, host)


def find_cookie(
    cookies: list[dict[str, Any]],
    *,
    host: str | None = None,
    names: tuple[str, ...] = ("ost", "fnos-token"),
) -> dict[str, Any] | None:
    candidates: list[dict[str, Any]] = []
    for cookie in cookies:
        if cookie.get("name") not in names:
            continue
        value = cookie.get("value")
        if not value:
            continue
        domain = str(cookie.get("domain") or "").lstrip(".")
        if host and domain and host not in domain and domain not in host:
            continue
        candidates.append(cookie)
    if not candidates:
        return None
    # Prefer ost over legacy fnos-token, then longer values.
    candidates.sort(key=lambda item: (item.get("name") != "ost", -len(str(item.get("value") or ""))))
    return candidates[0]


def wait_for_cookie(
    cdp_url: str = DEFAULT_CDP,
    *,
    host: str | None = None,
    timeout: float = 180.0,
    interval: float = 2.0,
) -> dict[str, Any]:
    deadline = time.monotonic() + timeout
    last_error: Exception | None = None
    while True:
        try:
            cookie = find_cookie(get_cookies(cdp_url, host=host), host=host)
            if cookie:
                return cookie
        except Exception as exc:
            last_error = exc
        if time.monotonic() >= deadline:
            detail = f" (last error: {last_error})" if last_error else ""
            raise NetworkError(
                f"no ost/fnos-token cookie found via CDP after {int(timeout)}s{detail}"
            )
        time.sleep(interval)


def find_edge() -> str | None:
    candidates: list[str] = []
    if os.name == "nt":
        for root in (
            os.environ.get("PROGRAMFILES(X86)"),
            os.environ.get("PROGRAMFILES"),
            os.environ.get("LOCALAPPDATA"),
        ):
            if not root:
                continue
            candidates.append(str(Path(root) / "Microsoft" / "Edge" / "Application" / "msedge.exe"))
        candidates.append(r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe")
    elif os.name == "darwin":
        candidates.append("/Applications/Microsoft Edge.app/Contents/MacOS/Microsoft Edge")
    else:
        for name in ("microsoft-edge", "microsoft-edge-stable", "google-chrome", "chromium", "chromium-browser"):
            found = shutil.which(name)
            if found:
                candidates.append(found)
    for candidate in candidates:
        if candidate and Path(candidate).exists():
            return candidate
    return None


def launch_edge(
    url: str,
    *,
    port: int = 9222,
    user_data_dir: str | None = None,
    edge_path: str | None = None,
) -> subprocess.Popen[Any]:
    executable = edge_path or find_edge()
    if not executable:
        raise UsageError(
            "Microsoft Edge was not found; start a Chromium browser with "
            "--remote-debugging-port=9222 and rerun `fnos-vm login --from-cdp`"
        )
    if not user_data_dir:
        user_data_dir = str(Path.home() / ".fnos-vm" / "edge-cdp-profile")
    Path(user_data_dir).mkdir(parents=True, exist_ok=True)
    args = [
        executable,
        f"--remote-debugging-port={port}",
        f"--user-data-dir={user_data_dir}",
        "--no-first-run",
        "--no-default-browser-check",
        url,
    ]
    return subprocess.Popen(args, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


def host_from_targets(cdp_url: str, *, port: int = 5666) -> str | None:
    try:
        for target in list_targets(cdp_url):
            parsed = urllib.parse.urlsplit(str(target.get("url") or ""))
            if parsed.hostname and parsed.port == port:
                return parsed.hostname
    except Exception:
        pass
    return None
