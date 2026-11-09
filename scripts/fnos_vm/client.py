"""Minimal synchronous client for the fnOS VM HTTP API.

The VM application is authenticated with ``Authorization: trim <ost>`` where
``ost`` is the HttpOnly web-session cookie.  The API envelope is::

    {"code": 0, "msg": "success", "data": ...}

Everything in this module is standard-library only.
"""

from __future__ import annotations

import json
import socket
import ssl
import time
import urllib.error
import urllib.parse
import urllib.request
from typing import Any, Iterable, Mapping

from . import __version__
from .errors import AuthError, BusinessError, NetworkError


API_PREFIX = "/vm/api/v1"
AUTH_CODES = {401, 10001}


def _int_code(value: Any) -> int | None:
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


class ApiClient:
    def __init__(
        self,
        host: str,
        port: int = 5666,
        scheme: str = "http",
        token: str = "",
        *,
        timeout: float = 30.0,
        insecure: bool = False,
        user_agent: str | None = None,
    ) -> None:
        self.host = host.strip().strip("/")
        self.port = int(port)
        self.scheme = scheme.lower()
        self.token = token.strip()
        self.timeout = float(timeout)
        self.insecure = insecure
        self.user_agent = user_agent or f"fnos-vm/{__version__}"

    @property
    def base_url(self) -> str:
        return f"{self.scheme}://{self.host}:{self.port}{API_PREFIX}"

    @property
    def origin(self) -> str:
        return f"{self.scheme}://{self.host}:{self.port}"

    def _url(self, path: str, params: Mapping[str, Any] | None = None) -> str:
        if not path.startswith("/"):
            path = "/" + path
        url = self.base_url + path
        if params:
            encoded = urllib.parse.urlencode(params, doseq=True)
            if encoded:
                url += ("&" if "?" in url else "?") + encoded
        return url

    def _open(self, request: urllib.request.Request):
        context = None
        if self.scheme == "https":
            context = ssl.create_default_context()
            if self.insecure:
                context.check_hostname = False
                context.verify_mode = ssl.CERT_NONE
        return urllib.request.urlopen(request, timeout=self.timeout, context=context)

    def request(
        self,
        method: str,
        path: str,
        *,
        params: Mapping[str, Any] | None = None,
        body: Any = None,
        retries: int = 0,
        timeout: float | None = None,
    ) -> Any:
        method = method.upper()
        data = None
        headers = {
            "Accept": "application/json",
            "Authorization": f"trim {self.token}",
            "User-Agent": self.user_agent,
        }
        if body is not None:
            data = json.dumps(body, ensure_ascii=False).encode("utf-8")
            headers["Content-Type"] = "application/json"
        url = self._url(path, params)
        last_exc: Exception | None = None
        attempts = max(0, int(retries)) + 1
        for attempt in range(attempts):
            request = urllib.request.Request(url, data=data, headers=headers, method=method)
            try:
                with self._open(request) as response:
                    raw = response.read()
                return self._decode(raw, response.status, path)
            except urllib.error.HTTPError as exc:
                raw = exc.read()
                payload = self._decode_optional(raw)
                code = _int_code(payload.get("code")) if isinstance(payload, dict) else None
                message = ""
                if isinstance(payload, dict):
                    message = str(payload.get("msg") or payload.get("message") or "")
                if exc.code == 401 or code in AUTH_CODES:
                    raise AuthError(
                        "session expired or invalid: run `fnos-vm login --from-cdp` again",
                        code=code or 401,
                    ) from exc
                if not message:
                    snippet = raw.decode("utf-8", errors="replace").strip()[:300]
                    message = f"HTTP {exc.code}" + (f": {snippet}" if snippet else "")
                raise BusinessError(message, code=code or exc.code) from exc
            except (urllib.error.URLError, TimeoutError, socket.timeout, ConnectionError) as exc:
                last_exc = exc
                if attempt + 1 < attempts:
                    time.sleep(0.5 * (attempt + 1))
                    continue
                reason = getattr(exc, "reason", exc)
                raise NetworkError(f"cannot reach {self.origin}: {reason}") from exc
        raise NetworkError(f"cannot reach {self.origin}: {last_exc}")

    @staticmethod
    def _decode_optional(raw: bytes) -> Any:
        if not raw:
            return None
        try:
            return json.loads(raw.decode("utf-8"))
        except Exception:
            return None

    def _decode(self, raw: bytes, status: int, path: str) -> Any:
        payload = self._decode_optional(raw)
        if not isinstance(payload, dict):
            snippet = raw.decode("utf-8", errors="replace").strip()[:300]
            raise BusinessError(
                f"unexpected response from {path}" + (f": {snippet}" if snippet else ""),
                code=status,
            )
        code = _int_code(payload.get("code"))
        if code in AUTH_CODES:
            raise AuthError(
                "session expired or invalid: run `fnos-vm login --from-cdp` again",
                code=code,
            )
        if code != 0:
            message = str(payload.get("msg") or payload.get("message") or f"API error {code}")
            raise BusinessError(message, code=code)
        return payload.get("data")

    # ------------------------------------------------------------------ read
    def list_vms(self) -> list[dict[str, Any]]:
        data = self.request("GET", "/domain/list", retries=1)
        return data if isinstance(data, list) else []

    def info(self, name: str) -> dict[str, Any]:
        data = self.request("GET", "/domain/info", params={"name": name}, retries=1)
        if not isinstance(data, dict):
            raise BusinessError(f"unexpected VM info for {name}")
        return data

    def status(self, names: Iterable[str]) -> list[dict[str, Any]]:
        joined = ",".join(names)
        data = self.request("GET", "/domain/status", params={"names": joined}, retries=1)
        return data if isinstance(data, list) else []

    def available_title(self, title: str, name: str = "") -> bool:
        data = self.request(
            "GET", "/domain/availableTitle", params={"name": name, "title": title}, retries=1
        )
        return bool(data)

    def hardware(self) -> dict[str, Any]:
        data = self.request("GET", "/system/hardwareInfo", retries=1)
        return data if isinstance(data, dict) else {}

    def volumes(self) -> dict[str, Any]:
        data = self.request("GET", "/system/allVolsInfo", retries=1)
        return data if isinstance(data, dict) else {}

    def hostdev_support(self) -> dict[str, Any]:
        data = self.request("GET", "/hostdev/support", retries=1)
        return data if isinstance(data, dict) else {}

    def ovs_inited(self) -> bool:
        return bool(self.request("GET", "/ovs/isInited", retries=1))

    def ovs_list(self) -> list[dict[str, Any]]:
        data = self.request("GET", "/ovs/listDetail", retries=1)
        return data if isinstance(data, list) else []

    def snapshot_list(self, name: str) -> list[dict[str, Any]]:
        data = self.request("GET", "/snapshot/list", params={"name": name}, retries=1)
        return data if isinstance(data, list) else []

    # ----------------------------------------------------------------- write
    def _write(self, path: str, body: Mapping[str, Any], *, retries: int = 0) -> Any:
        return self.request("POST", path, body=dict(body), retries=retries)

    def create(self, payload: Mapping[str, Any]) -> dict[str, Any]:
        data = self._write("/domain/create", payload)
        return data if isinstance(data, dict) else {}

    def modify(self, payload: Mapping[str, Any]) -> dict[str, Any]:
        data = self._write("/domain/modify", payload)
        return data if isinstance(data, dict) else {}

    def start(self, name: str) -> None:
        self._write("/domain/start", {"name": name})

    def shutdown(self, name: str) -> None:
        self._write("/domain/shutdown", {"name": name})

    def force_shutdown(self, name: str) -> None:
        self._write("/domain/forceshutdown", {"name": name})

    def reboot(self, name: str) -> None:
        self._write("/domain/reboot", {"name": name})

    def pause(self, name: str) -> None:
        self._write("/domain/pause", {"name": name})

    def resume(self, name: str) -> None:
        self._write("/domain/resume", {"name": name})

    def delete(self, name: str) -> None:
        self._write("/domain/delete", {"name": name})

    def snapshot_create(
        self, name: str, snapshot_name: str, description: str = ""
    ) -> dict[str, Any]:
        data = self._write(
            "/snapshot/create",
            {"name": name, "snapshotName": snapshot_name, "snapshotDesc": description},
        )
        return data if isinstance(data, dict) else {}

    def snapshot_delete(self, name: str, snapshot_name: str) -> None:
        self._write("/snapshot/delete", {"name": name, "snapshotName": snapshot_name})

    def snapshot_revert(self, name: str, snapshot_name: str) -> None:
        self._write("/snapshot/revert", {"name": name, "snapshotName": snapshot_name})

    # --------------------------------------------------------------- helpers
    def ping(self) -> int:
        return len(self.list_vms())

    def vm_by_ref(self, ref: str) -> dict[str, Any]:
        """Resolve an internal name, exact title or unique title prefix."""
        ref = (ref or "").strip()
        if not ref:
            raise BusinessError("VM name is required")
        vms = self.list_vms()
        for vm in vms:
            if vm.get("name") == ref:
                return vm
        exact = [vm for vm in vms if str(vm.get("title", "")) == ref]
        if len(exact) == 1:
            return exact[0]
        ci = [vm for vm in vms if str(vm.get("title", "")).casefold() == ref.casefold()]
        if len(ci) == 1:
            return ci[0]
        prefix = [vm for vm in vms if str(vm.get("title", "")).casefold().startswith(ref.casefold())]
        if len(prefix) == 1:
            return prefix[0]
        if len(prefix) > 1:
            names = ", ".join(str(vm.get("title")) for vm in prefix)
            raise BusinessError(f"ambiguous VM reference {ref!r}: {names}")
        raise BusinessError(f"VM not found: {ref}", code=12021)
