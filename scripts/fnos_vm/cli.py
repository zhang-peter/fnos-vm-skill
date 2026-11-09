"""Command-line interface for fnos-vm."""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
import time
import traceback
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence

from . import __version__
from . import cdp
from .client import ApiClient
from .config import Config, describe_config_path, load_config, save_config, delete_config
from .errors import (
    AuthError,
    BusinessError,
    FnosVmError,
    NetworkError,
    PreflightError,
    SafetyError,
    UsageError,
    EXIT_OK,
)
from .images import (
    IMAGE_ALIASES,
    autoinstall_user_data,
    build_autoinstall_seed,
    build_seed,
    cloud_init_meta_data,
    cloud_init_user_data,
    download,
    network_config_dhcp,
    network_config_static,
    prepare_image,
    push_with_fnos_cli,
    resolve_image_url,
    sha256_file,
    sha512_crypt,
)
from .output import Output, default_json_mode, format_bytes, format_epoch, state_text
from .payload import (
    build_create_payload,
    build_modify_payload,
    format_size,
    parse_size,
    patch_qcow2_size,
    preflight_create,
)
from .ws import WebSocket, WebSocketError
from .vnc import RfbClient, RfbError


STATE_TARGETS = {
    "running": "RUNNING",
    "run": "RUNNING",
    "up": "RUNNING",
    "stopped": "SHUTOFF",
    "stop": "SHUTOFF",
    "shutoff": "SHUTOFF",
    "off": "SHUTOFF",
    "paused": "PAUSED",
    "pause": "PAUSED",
    "crashed": "CRASHED",
    "blocked": "BLOCKED",
}


# ---------------------------------------------------------------------------
# Argument parsing
# ---------------------------------------------------------------------------
def _add_common(parser: argparse.ArgumentParser, *, suppress: bool = False, include_timeout: bool = True) -> None:
    parser.add_argument("--host", default=argparse.SUPPRESS, help="NAS host or host:port / URL")
    parser.add_argument("--port", type=int, default=argparse.SUPPRESS)
    parser.add_argument(
        "--scheme",
        choices=("http", "https"),
        default=argparse.SUPPRESS,
    )
    parser.add_argument("--config", default=argparse.SUPPRESS, help="config file path")
    if include_timeout:
        parser.add_argument("--timeout", type=float, default=argparse.SUPPRESS)
    parser.add_argument("--insecure", action="store_true", default=argparse.SUPPRESS)
    parser.add_argument("--json", action="store_true", default=argparse.SUPPRESS)
    parser.add_argument("--table", action="store_true", default=argparse.SUPPRESS)
    parser.add_argument("--debug", action="store_true", default=argparse.SUPPRESS)


def _disable_abbreviations(parser: argparse.ArgumentParser) -> None:
    """Reject ambiguous prefix options such as --ssh-key -> --ssh-key-file."""
    parser.allow_abbrev = False
    for action in parser._actions:  # noqa: SLF001 - argparse has no public tree API
        if isinstance(action, argparse._SubParsersAction):  # noqa: SLF001
            for child in action.choices.values():
                _disable_abbreviations(child)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="fnos-vm",
        description="Manage the fnOS Virtual Machine app (trim.vm) over its HTTP API.",
        epilog="Run `fnos-vm <command> --help` for command-specific options.",
    )
    parser.add_argument("--version", action="version", version=f"fnos-vm {__version__}")
    _add_common(parser)
    sub = parser.add_subparsers(dest="command", required=True)

    def leaf(name: str, help_text: str, **kwargs) -> argparse.ArgumentParser:
        item = sub.add_parser(name, help=help_text, **kwargs)
        _add_common(item, suppress=True)
        return item

    login = leaf("login", "save a VM session token (ost) or read it from Edge via CDP")
    login.add_argument("--token", help="ost token copied from the browser (or use --from-cdp)")
    login.add_argument("--from-cdp", action="store_true", help="read the HttpOnly ost cookie via CDP")
    login.add_argument("--cdp", default=cdp.DEFAULT_CDP, help="CDP endpoint (default http://127.0.0.1:9222)")
    login.add_argument("--launch", action="store_true", help="launch a controlled Edge if CDP is absent")
    login.add_argument("--edge-path", help="path to msedge.exe/chromium")
    login.add_argument("--wait-timeout", type=float, default=180.0, help="seconds to wait for login/cookie")
    login.add_argument("--no-validate", action="store_true", help="save without calling the API")

    leaf("logout", "remove the saved session and local ownership registry")

    leaf("host", "show KVM/IOMMU/OVS/memory/volume capabilities")
    leaf("list", "list virtual machines")
    info = leaf("info", "show the full device model for one VM")
    info.add_argument("name", help="internal name, title, or unique title prefix")

    create = leaf("create", "create a virtual machine")
    create.add_argument("--file", help="JSON file with create fields")
    create.add_argument("--name", "--title", dest="name", help="VM display name/title")
    create.add_argument("--os-type", choices=("linux", "windows", "others"), default=None)
    create.add_argument("--os-version", default=None)
    create.add_argument("--cpu", type=int, default=None, help="vCPU count")
    create.add_argument("--mem", help="memory size, for example 4G or 4096M")
    create.add_argument("--disk", action="append", default=None, help="SIZE[@POOL], repeatable")
    create.add_argument("--image", help="remote qcow2 path to import as the system disk")
    create.add_argument("--disk-size", help="capacity for --image import (for example 20G)")
    create.add_argument("--iso", help="remote ISO for the OS cdrom slot")
    create.add_argument("--driver-iso", help="remote ISO for the driver cdrom slot")
    create.add_argument("--seed", help="remote cloud-init seed ISO (second cdrom slot)")
    create.add_argument("--network", action="append", default=None, help="SOURCE[:MODEL][@MAC], repeatable")
    create.add_argument("--no-network", action="store_true", help="create without a NIC")
    create.add_argument("--bus", choices=("virtio", "scsi", "sata", "ide"), default=None)
    create.add_argument("--hypervisor", choices=("kvm", "qemu"), default=None)
    create.add_argument("--chipset", choices=("q35", "i440fx"), default=None)
    create.add_argument("--firmware", choices=("efi", "bios"), default=None)
    create.add_argument("--video", choices=("vga", "virtio", "vmvga", "qxl"), default=None)
    create.add_argument("--boot", default=None, help="default, cdrom, or disk-<n>")
    create.add_argument("--autostart", action="store_true", default=None)
    create.add_argument("--no-autostart", action="store_false", dest="autostart")
    create.add_argument("--clipboard", action="store_true", default=None)
    create.add_argument("--no-clipboard", action="store_false", dest="clipboard")
    create.add_argument("--desc", default=None)
    create.add_argument("--dry-run", action="store_true", help="print the payload and preflight without writing")

    for name, text in (
        ("start", "start a VM"),
        ("stop", "shut down a VM (use --force for force shutdown)"),
        ("reboot", "reboot a VM"),
        ("pause", "pause a VM"),
        ("resume", "resume a paused VM"),
    ):
        item = leaf(name, text)
        item.add_argument("name", help="internal name, title, or unique title prefix")
        item.add_argument("--force-other", action="store_true", help="allow a VM not created by this tool")
        item.add_argument("--wait", type=float, default=0.0, help="seconds to wait for the expected state")
        item.add_argument("--interval", type=float, default=2.0, help="polling interval for --wait")
    stop = sub.choices["stop"]
    stop.add_argument("--force", action="store_true", help="use forceshutdown")

    delete = leaf("delete", "delete a VM and its disks (irreversible)")
    delete.add_argument("name")
    delete.add_argument("--yes", action="store_true", required=True)
    delete.add_argument("--force-other", action="store_true")

    wait = sub.add_parser("wait", help="wait until a VM reaches a state")
    _add_common(wait, suppress=True, include_timeout=False)
    wait.add_argument("name")
    wait.add_argument("--state", required=True, help="running, stopped, paused, crashed, blocked")
    wait.add_argument("--timeout", type=float, default=120.0)
    wait.add_argument("--interval", type=float, default=2.0)

    snap = leaf("snapshot", "list/create/revert/delete snapshots")
    snap_sub = snap.add_subparsers(dest="snapshot_command", required=True)
    snap_list = snap_sub.add_parser("list", help="list snapshots")
    _add_common(snap_list, suppress=True)
    snap_list.add_argument("name")
    snap_create = snap_sub.add_parser("create", help="create a snapshot")
    _add_common(snap_create, suppress=True)
    snap_create.add_argument("name")
    snap_create.add_argument("--desc", default="")
    snap_create.add_argument("--snap-name", default=None)
    snap_create.add_argument("--force-other", action="store_true")
    for action, text in (("revert", "revert to a snapshot"), ("delete", "delete a snapshot")):
        item = snap_sub.add_parser(action, help=text)
        _add_common(item, suppress=True)
        item.add_argument("name")
        item.add_argument("snap")
        item.add_argument("--yes", action="store_true", required=True)
        item.add_argument("--force-other", action="store_true")

    vnc = leaf("vnc", "print the VNC WebSocket URL or drive the console")
    vnc.add_argument("name")
    vnc.add_argument("--check", action="store_true", help="perform a WebSocket handshake")
    vnc.add_argument("--screenshot", default=None, help="capture the framebuffer to a PNG")
    vnc.add_argument("--keys", default=None, help="comma-separated key sequence (see docs)")
    vnc.add_argument("--wait", type=float, default=0.0, help="seconds to wait before keys/screenshot")
    vnc.add_argument("--vnc-timeout", type=float, default=20.0, help="RFB socket timeout")

    adopt = leaf("adopt", "mark an existing VM as managed by this tool")
    adopt.add_argument("name")
    adopt.add_argument("--yes", action="store_true", required=True)

    modify = leaf("modify", "modify CPU/memory/description/title/autostart")
    modify.add_argument("name")
    modify.add_argument("--file", help="JSON file with raw API fields to merge")
    modify.add_argument("--title", default=None)
    modify.add_argument("--cpu", type=int, default=None)
    modify.add_argument("--mem", default=None)
    modify.add_argument("--desc", default=None)
    modify.add_argument("--autostart", default=None)
    modify.add_argument("--clipboard", default=None)
    modify.add_argument("--boot", default=None)
    modify.add_argument("--force-other", action="store_true")

    image = leaf("image", "download, resize and prepare cloud images / seed ISOs")
    image_sub = image.add_subparsers(dest="image_command", required=True)
    image_download = image_sub.add_parser("download", help="download a cloud image")
    _add_common(image_download, suppress=True)
    image_download.add_argument("source", nargs="?", default=None, help="alias from `image list` or an https URL")
    image_download.add_argument("--dest", default=None)
    image_download.add_argument("--sha256", default=None)
    image_download.add_argument("--force", action="store_true")
    image_download.add_argument("--list", action="store_true", help="list aliases and exit")

    image_list = image_sub.add_parser("list", help="list built-in image aliases")
    _add_common(image_list, suppress=True)

    image_resize = image_sub.add_parser("resize", help="grow a qcow2 virtual size in place")
    _add_common(image_resize, suppress=True)
    image_resize.add_argument("input")
    image_resize.add_argument("--size", required=True)
    image_resize.add_argument("--output", default=None)

    image_seed = image_sub.add_parser("seed", help="build a cloud-init NoCloud seed ISO")
    _add_common(image_seed, suppress=True)
    image_seed.add_argument("--output", required=True)
    image_seed.add_argument("--user", default="ubuntu")
    image_seed.add_argument("--password", default=None)
    image_seed.add_argument("--ssh-key-file", action="append", default=None)
    image_seed.add_argument("--ssh-key", action="append", default=None)
    image_seed.add_argument("--hostname", default="fnos-vm")
    image_seed.add_argument("--meta", default=None)
    image_seed.add_argument("--static-ip", default=None)
    image_seed.add_argument("--prefix", type=int, default=24)
    image_seed.add_argument("--gateway", default=None)
    image_seed.add_argument("--dns", action="append", default=None)
    image_seed.add_argument("--mac", default=None)
    image_seed.add_argument("--network-config", default=None)

    image_auto = image_sub.add_parser("autoinstall", help="build an Ubuntu autoinstall NoCloud seed ISO")
    _add_common(image_auto, suppress=True)
    image_auto.add_argument("--output", required=True)
    image_auto.add_argument("--user", default="ubuntu")
    image_auto.add_argument("--password", default=None)
    image_auto.add_argument("--password-hash", default=None)
    image_auto.add_argument("--ssh-key-file", action="append", default=None)
    image_auto.add_argument("--ssh-key", action="append", default=None)
    image_auto.add_argument("--hostname", default="ubuntu")
    image_auto.add_argument("--storage-layout", choices=("direct", "lvm", "zfs"), default="direct")
    image_auto.add_argument("--package", action="append", default=None)
    image_auto.add_argument("--late-command", action="append", default=None)
    image_auto.add_argument("--timezone", default="Etc/UTC")
    image_auto.add_argument("--shutdown", choices=("poweroff", "reboot"), default="poweroff")
    image_auto.add_argument("--allow-pw", action="store_true")
    image_auto.add_argument("--static-ip", default=None)
    image_auto.add_argument("--prefix", type=int, default=24)
    image_auto.add_argument("--gateway", default=None)
    image_auto.add_argument("--dns", action="append", default=None)
    image_auto.add_argument("--mac", default=None)
    image_auto.add_argument("--network-config", default=None)

    image_push = image_sub.add_parser("push", help="upload a file with fnos-cli")
    _add_common(image_push, suppress=True)
    image_push.add_argument("file")
    image_push.add_argument("--remote-dir", required=True)
    image_push.add_argument("--profile", default="fnos")
    image_push.add_argument("--fnos-cli", default=None)

    image_prepare = image_sub.add_parser("prepare", help="download + resize + seed (+ optional upload)")
    _add_common(image_prepare, suppress=True)
    image_prepare.add_argument("source")
    image_prepare.add_argument("--dest", required=True)
    image_prepare.add_argument("--disk-size", default="20G")
    image_prepare.add_argument("--seed-output", default=None)
    image_prepare.add_argument("--user", default="ubuntu")
    image_prepare.add_argument("--password", default=None)
    image_prepare.add_argument("--ssh-key-file", action="append", default=None)
    image_prepare.add_argument("--ssh-key", action="append", default=None)
    image_prepare.add_argument("--hostname", default="fnos-vm")
    image_prepare.add_argument("--remote-dir", default=None)
    image_prepare.add_argument("--profile", default="fnos")
    image_prepare.add_argument("--fnos-cli", default=None)
    image_prepare.add_argument("--force", action="store_true")
    _disable_abbreviations(parser)
    return parser


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def _arg(args: argparse.Namespace, name: str, default: Any = None) -> Any:
    value = getattr(args, name, None)
    return default if value is None else value


def _set_config_env(args: argparse.Namespace) -> None:
    config = getattr(args, "config", None)
    if config:
        os.environ["FNOS_VM_CONFIG"] = str(Path(config).expanduser())


def _parse_host(value: str, *, default_port: int = 5666, default_scheme: str = "http") -> tuple[str, int, str]:
    text = str(value or "").strip()
    if not text:
        raise UsageError("host is required")
    scheme = default_scheme
    port = default_port
    if "://" in text:
        from urllib.parse import urlsplit

        parsed = urlsplit(text)
        if parsed.scheme not in ("http", "https"):
            raise UsageError("host URL scheme must be http or https")
        scheme = parsed.scheme
        host = parsed.hostname
        if not host:
            raise UsageError(f"invalid host URL: {value!r}")
        if parsed.port:
            port = parsed.port
        return host, port, scheme
    text = text.strip("/")
    if text.count(":") == 1:
        host, maybe_port = text.split(":", 1)
        if maybe_port.isdigit():
            port = int(maybe_port)
            text = host
    if not text:
        raise UsageError("host is required")
    return text, port, scheme


def _make_client(args: argparse.Namespace, config: Config | None = None) -> ApiClient:
    host = _arg(args, "host")
    port = _arg(args, "port")
    scheme = _arg(args, "scheme")
    default_port = int(port or (config.port if config else 5666))
    default_scheme = str(scheme or (config.scheme if config else "http"))
    if host:
        host, parsed_port, parsed_scheme = _parse_host(
            str(host), default_port=default_port, default_scheme=default_scheme
        )
        port = parsed_port
        scheme = parsed_scheme
    if config is not None:
        host = host or config.host
        port = port or config.port
        scheme = scheme or config.scheme
        token = config.token
    else:
        token = ""
    if not host:
        raise UsageError("host is required: use --host or run `fnos-vm login --host ...`")
    return ApiClient(
        str(host),
        int(port or 5666),
        str(scheme or "http"),
        token,
        timeout=float(_arg(args, "timeout", 30.0)),
        insecure=bool(_arg(args, "insecure", False)),
    )


def _client_and_config(args: argparse.Namespace) -> tuple[ApiClient, Config]:
    config = load_config()
    assert config is not None
    return _make_client(args, config), config


def _fetch_infos(client: ApiClient, vms: Iterable[Mapping[str, Any]]) -> dict[str, dict[str, Any]]:
    names = [str(vm.get("name")) for vm in vms if vm.get("name")]
    result: dict[str, dict[str, Any]] = {}

    def fetch(name: str) -> tuple[str, dict[str, Any] | None]:
        try:
            return name, client.info(name)
        except FnosVmError:
            return name, None

    if len(names) <= 1:
        for name in names:
            key, value = fetch(name)
            if value:
                result[key] = value
        return result
    with ThreadPoolExecutor(max_workers=min(4, len(names))) as pool:
        for key, value in pool.map(fetch, names):
            if value:
                result[key] = value
    return result


def _resolve(client: ApiClient, ref: str) -> tuple[dict[str, Any], dict[str, Any]]:
    vm = client.vm_by_ref(ref)
    name = str(vm.get("name") or "")
    if not name:
        raise BusinessError(f"VM not found: {ref}", code=12021)
    return vm, client.info(name)


def _require_owned(config: Config, info: Mapping[str, Any], *, force_other: bool, action: str) -> None:
    name = str(info.get("name") or "")
    if config.is_owned(name):
        return
    if force_other:
        return
    raise SafetyError(
        f"refusing to {action} VM {info.get('title') or name!r}: it was not created by fnos-vm. "
        "Use --force-other to override, or `fnos-vm adopt <name> --yes` to manage it."
    )


def _snapshot_by_name(client: ApiClient, name: str, snapshot: str) -> dict[str, Any]:
    snapshots = client.snapshot_list(name)
    for item in snapshots:
        if item.get("name") == snapshot:
            return item
    raise BusinessError(f"snapshot not found: {snapshot}", code=12021)


def _wait_for_state(
    client: ApiClient,
    name: str,
    target: str,
    *,
    timeout: float,
    interval: float,
    log=None,
) -> dict[str, Any]:
    target = STATE_TARGETS.get(target.lower(), target.upper())
    deadline = time.monotonic() + max(0.0, timeout)
    last: dict[str, Any] = {}
    while True:
        last = client.info(name)
        state = str(last.get("state") or "")
        if state == target:
            return last
        if time.monotonic() >= deadline:
            raise BusinessError(
                f"timed out waiting for {name} to reach {target}; current state is {state or 'unknown'}",
                code="timeout",
                details={"target": target, "current": state},
            )
        if log:
            log(f"  {state or 'unknown'} ... waiting for {target}")
        time.sleep(max(0.2, interval))


def _vnc_url(client: ApiClient, info: Mapping[str, Any]) -> str:
    name = info.get("vncName") or info.get("name")
    if not name:
        raise BusinessError("VM has no VNC name")
    scheme = "wss" if client.scheme == "https" else "ws"
    return f"{scheme}://{client.host}:{client.port}/vm/api/v1/vnc/{name}"


def _load_json_file(path: str) -> Any:
    try:
        return json.loads(Path(path).read_text(encoding="utf-8-sig"))
    except Exception as exc:
        raise UsageError(f"cannot read JSON file {path}: {exc}") from exc


def _read_keys(paths: Iterable[str] | None) -> list[str]:
    keys: list[str] = []
    for path in paths or []:
        try:
            keys.append(Path(path).read_text(encoding="utf-8").strip())
        except Exception as exc:
            raise UsageError(f"cannot read SSH key file {path}: {exc}") from exc
    return [key for key in keys if key]


# ---------------------------------------------------------------------------
# Commands
# ---------------------------------------------------------------------------
def cmd_login(args: argparse.Namespace) -> int:
    out = Output(json_mode=json_mode(args), table_mode=bool(_arg(args, "table", False)))
    config = load_config(required=False)
    host_arg = _arg(args, "host")
    port_arg = _arg(args, "port")
    scheme_arg = _arg(args, "scheme")
    host = None
    port = int(port_arg or (config.port if config else 5666))
    scheme = str(scheme_arg or (config.scheme if config else "http"))
    if host_arg:
        host, port, scheme = _parse_host(str(host_arg), default_port=port, default_scheme=scheme)
    elif config:
        host = config.host
        port = int(port_arg or config.port)
        scheme = str(scheme_arg or config.scheme)

    token: str | None = str(args.token).strip() if args.token else None
    if args.token and args.from_cdp:
        raise UsageError("use either --token or --from-cdp, not both")
    if not token and not args.from_cdp:
        raise UsageError(
            "no session token supplied. Use one of:\n"
            "  fnos-vm login --host <host> --token '<ost>'\n"
            "  fnos-vm login --host <host> --from-cdp      (Edge on port 9222)\n"
            "  fnos-vm login --host <host> --from-cdp --launch"
        )

    launched = None
    if args.from_cdp:
        cdp_url = str(args.cdp or cdp.DEFAULT_CDP).rstrip("/")
        if not host:
            host = cdp.host_from_targets(cdp_url, port=port)
        if not host:
            raise UsageError("cannot infer the NAS host from CDP; pass --host <host>")
        if not cdp.cdp_available(cdp_url) and args.launch:
            out.log(f"launching Edge with CDP on {cdp_url}")
            launched = cdp.launch_edge(
                f"{scheme}://{host}:{port}/vm",
                port=int(cdp_url.rsplit(":", 1)[-1]) if cdp_url.rsplit(":", 1)[-1].isdigit() else 9222,
                edge_path=args.edge_path,
            )
            deadline = time.monotonic() + 20
            while not cdp.cdp_available(cdp_url) and time.monotonic() < deadline:
                time.sleep(0.5)
        if not cdp.cdp_available(cdp_url):
            raise UsageError(
                f"CDP endpoint {cdp_url} is not reachable. Start Edge with "
                "`msedge.exe --remote-debugging-port=9222`, log in to the NAS, then retry; "
                "or use --launch to open a controlled profile."
            )
        out.log(f"waiting for the ost cookie via {cdp_url} ...")
        cookie = cdp.wait_for_cookie(
            cdp_url, host=host, timeout=float(args.wait_timeout), interval=2.0
        )
        token = str(cookie.get("value") or "")
        if not token:
            raise AuthError("CDP returned an empty cookie value")
        domain = str(cookie.get("domain") or "").lstrip(".")
        if domain and domain not in {"localhost", "127.0.0.1"}:
            host = host or domain

    if not host:
        raise UsageError("host is required: pass --host <host> or save a config with --host first")
    if not token:
        raise AuthError("no session token was obtained")
    assert host and token
    if any(char.isspace() for char in token) or "\n" in token:
        raise UsageError("token must not contain whitespace")
    client = ApiClient(
        host,
        port,
        scheme,
        token,
        timeout=float(_arg(args, "timeout", 30.0)),
        insecure=bool(_arg(args, "insecure", False)),
    )
    count = 0
    if not args.no_validate:
        count = client.ping()
    new_config = config or Config(host=host, port=port, scheme=scheme)
    new_config.host = host
    new_config.port = port
    new_config.scheme = scheme
    new_config.token = token
    new_config.savedAt = datetime.now().astimezone().isoformat(timespec="seconds")
    path = save_config(new_config)
    data = {
        "host": host,
        "port": port,
        "scheme": scheme,
        "authenticated": not args.no_validate,
        "vmCount": count,
        "configPath": str(path),
    }
    out.emit(data)
    if not out.json_mode:
        out.kv(
            [
                ("Host", f"{scheme}://{host}:{port}"),
                ("Authenticated", "yes" if not args.no_validate else "not checked"),
                ("VMs", count),
                ("Config", str(path)),
            ]
        )
    if launched is not None:
        out.log("controlled Edge was launched; close it after login if you no longer need it")
    return EXIT_OK


def cmd_logout(args: argparse.Namespace) -> int:
    out = Output(json_mode=json_mode(args), table_mode=bool(_arg(args, "table", False)))
    removed = delete_config()
    out.emit({"removed": removed, "configPath": describe_config_path()})
    if not out.json_mode:
        print("logged out" if removed else "no saved session", file=sys.stdout)
    return EXIT_OK


def cmd_host(args: argparse.Namespace) -> int:
    out = Output(json_mode=json_mode(args), table_mode=bool(_arg(args, "table", False)))
    client, _config = _client_and_config(args)
    hardware = client.hardware()
    support = client.hostdev_support()
    ovs_initialized = client.ovs_inited()
    ovs = client.ovs_list()
    volumes = client.volumes()
    vms = client.list_vms()
    infos = _fetch_infos(client, vms)
    allocated = sum(int(info.get("memCapacityInBytes") or 0) for info in infos.values())
    mem_total = int((hardware.get("mem") or {}).get("total") or 0)
    volume_rows = []
    for volume in volumes.get("vols") or []:
        index = volume.get("index")
        size = int(volume.get("size") or 0)
        used = int(volume.get("used") or 0)
        free = max(0, size - used)
        volume_rows.append(
            {
                "index": index,
                "path": f"/vol{index}",
                "sysname": volume.get("sysname"),
                "sizeBytes": size,
                "usedBytes": used,
                "freeBytes": free,
                "usedPercent": round(used * 100.0 / size, 1) if size else 0,
            }
        )
    data = {
        "cpu": hardware.get("cpu") or {},
        "memory": {
            "totalBytes": mem_total,
            "allocatedBytes": allocated,
            "freeBytes": max(0, mem_total - allocated),
        },
        "gpu": hardware.get("gpu") or [],
        "bios": hardware.get("bios") or {},
        "kvm": bool(support.get("kvm")),
        "iommu": bool(support.get("iommu")),
        "sriov": bool(support.get("sriov")),
        "ovs": {"initialized": ovs_initialized, "networks": ovs},
        "volumes": volume_rows,
        "vms": {"count": len(vms), "allocatedMemoryBytes": allocated},
    }
    out.emit(data)
    if out.json_mode:
        return EXIT_OK
    cpu = data["cpu"]
    out.kv(
        [
            ("CPU", f"{cpu.get('name') or '-'} ({cpu.get('core') or '?'}C/{cpu.get('thread') or '?'}T)"),
            ("KVM", "yes" if data["kvm"] else "no"),
            ("IOMMU", "yes" if data["iommu"] else "no"),
            ("SR-IOV", "yes" if data["sriov"] else "no"),
            ("OVS", "initialized" if ovs_initialized else "not initialized"),
            ("Memory", f"{format_bytes(mem_total)} total, {format_bytes(allocated)} allocated, {format_bytes(data['memory']['freeBytes'])} free"),
            ("VMs", str(len(vms))),
        ]
    )
    print("", file=sys.stdout)
    out.table(
        ["VOL", "PATH", "SIZE", "USED", "FREE", "USED%"],
        [
            [
                row["index"],
                row["path"],
                format_bytes(row["sizeBytes"]),
                format_bytes(row["usedBytes"]),
                format_bytes(row["freeBytes"]),
                f"{row['usedPercent']:.1f}%",
            ]
            for row in volume_rows
        ],
    )
    print("", file=sys.stdout)
    out.table(
        ["NETWORK", "TYPE", "STATE", "INDEX"],
        [[item.get("name"), item.get("type"), item.get("state"), item.get("index")] for item in ovs],
    )
    return EXIT_OK


def cmd_list(args: argparse.Namespace) -> int:
    out = Output(json_mode=json_mode(args), table_mode=bool(_arg(args, "table", False)))
    client, _config = _client_and_config(args)
    vms = client.list_vms()
    infos = _fetch_infos(client, vms)
    merged: list[dict[str, Any]] = []
    for vm in vms:
        info = infos.get(str(vm.get("name"))) or {}
        row = {**vm, **info}
        row["stateText"] = state_text(row.get("state"))
        merged.append(row)
    out.emit(merged)
    if out.json_mode:
        return EXIT_OK
    out.table(
        ["NAME", "TITLE", "OS", "STATE", "CPU", "MEM", "DISK"],
        [
            [
                row.get("name"),
                row.get("title") or "-",
                f"{row.get('osType') or '-'}/{row.get('osVersion') or '-'}",
                state_text(row.get("state")),
                row.get("cpuCoreCnt") or "-",
                format_bytes(row.get("memCapacityInBytes")) if row.get("memCapacityInBytes") else "-",
                format_bytes(row.get("totalDisksCapacityInBytes")) if row.get("totalDisksCapacityInBytes") else "-",
            ]
            for row in merged
        ],
        empty="no virtual machines",
    )
    return EXIT_OK


def _render_info(out: Output, info: Mapping[str, Any], *, owned: bool | None = None, vnc_url: str | None = None) -> None:
    out.kv(
        [
            ("Name", info.get("name")),
            ("Title", info.get("title")),
            ("UUID", info.get("uuid")),
            ("State", f"{state_text(info.get('state'))} ({info.get('state')})"),
            ("OS", f"{info.get('osType')}/{info.get('osVersion')}"),
            ("CPU", f"{info.get('cpuCoreCnt')} vCPU ({info.get('cpuMode')})"),
            ("Memory", format_bytes(info.get("memCapacityInBytes"))),
            ("Chipset", info.get("chipset")),
            ("Firmware", info.get("firmware")),
            ("Hypervisor", info.get("hypervisor")),
            ("Video", info.get("videoType")),
            ("Boot", f"{info.get('bootType') or 'default'} #{info.get('bootIndex')}"),
            ("Autostart", "yes" if info.get("autostart") else "no"),
            ("Clipboard", "yes" if info.get("clipboard") else "no"),
            ("Managed by fnos-vm", "yes" if owned else "no"),
        ]
    )
    if vnc_url:
        print("", file=sys.stdout)
        out.kv([("VNC", vnc_url)])
    disks = info.get("disks") or []
    if disks:
        print("", file=sys.stdout)
        out.table(
            ["#", "POOL", "BUS", "CAPACITY", "PATH"],
            [
                [
                    index + 1,
                    disk.get("poolName") or disk.get("pool"),
                    disk.get("busType"),
                    format_bytes(disk.get("capacityInBytes")),
                    disk.get("volumePath"),
                ]
                for index, disk in enumerate(disks)
            ],
        )
    networks = info.get("networks") or []
    if networks:
        print("", file=sys.stdout)
        out.table(
            ["#", "TYPE", "SOURCE", "MODEL", "MAC"],
            [
                [
                    index + 1,
                    item.get("type"),
                    item.get("source"),
                    item.get("nicModel"),
                    item.get("macAddr"),
                ]
                for index, item in enumerate(networks)
            ],
        )
    cdroms = info.get("cdroms") or {}
    if cdroms.get("osFilePath") or cdroms.get("driverFilePath"):
        print("", file=sys.stdout)
        out.kv(
            [
                ("OS ISO", cdroms.get("osFilePath") or "-"),
                ("Driver/seed ISO", cdroms.get("driverFilePath") or "-"),
            ]
        )


def cmd_info(args: argparse.Namespace) -> int:
    out = Output(json_mode=json_mode(args), table_mode=bool(_arg(args, "table", False)))
    client, config = _client_and_config(args)
    _vm, info = _resolve(client, args.name)
    owned = config.is_owned(str(info.get("name")))
    vnc = None
    try:
        vnc = _vnc_url(client, info)
    except FnosVmError:
        pass
    data = {**info, "managedByThisTool": owned, "vncUrl": vnc}
    out.emit(data)
    if not out.json_mode:
        _render_info(out, info, owned=owned, vnc_url=vnc)
    return EXIT_OK


def cmd_wait(args: argparse.Namespace) -> int:
    out = Output(json_mode=json_mode(args), table_mode=bool(_arg(args, "table", False)))
    client, _config = _client_and_config(args)
    state_key = str(args.state).strip().lower()
    if state_key not in STATE_TARGETS:
        raise UsageError(
            "state must be one of running, stopped, paused, crashed, blocked "
            "or the raw uppercase state"
        )
    _vm, info = _resolve(client, args.name)
    started = time.monotonic()
    final = _wait_for_state(
        client,
        str(info["name"]),
        args.state,
        timeout=float(args.timeout),
        interval=float(args.interval),
        log=out.log if not out.json_mode else None,
    )
    elapsed = round(time.monotonic() - started, 1)
    out.emit({"target": STATE_TARGETS.get(args.state.lower(), args.state.upper()), "reached": True, "waitedSeconds": elapsed, "vm": final})
    if not out.json_mode:
        print(f"{final.get('title') or final.get('name')} reached {final.get('state')} in {elapsed}s", file=sys.stdout)
    return EXIT_OK


def _parse_key_sequence(spec: str) -> list[tuple[str, Any]]:
    actions: list[tuple[str, Any]] = []
    for raw in str(spec or "").split(","):
        token = raw.strip()
        if not token:
            continue
        lower = token.lower()
        if lower.startswith("sleep:"):
            actions.append(("sleep", float(token.split(":", 1)[1])))
        elif lower.startswith("text:") or lower.startswith("type:"):
            actions.append(("text", token.split(":", 1)[1]))
        elif lower.startswith("combo:"):
            parts = token.split(":", 1)[1].replace("-", "+").split("+")
            actions.append(("combo", [part.strip().lower() for part in parts if part.strip()]))
        elif lower.startswith("key:"):
            actions.append(("key", token.split(":", 1)[1].strip().lower()))
        else:
            actions.append(("key", lower))
    return actions


def cmd_vnc(args: argparse.Namespace) -> int:
    out = Output(json_mode=json_mode(args), table_mode=bool(_arg(args, "table", False)))
    client, _config = _client_and_config(args)
    _vm, info = _resolve(client, args.name)
    url = _vnc_url(client, info)
    checked = False
    status = None
    screenshot_path = None
    framebuffer = None
    actions = _parse_key_sequence(args.keys) if args.keys else []
    if args.check and not args.screenshot and not actions:
        try:
            ws = WebSocket.connect(
                url,
                headers={"Authorization": f"trim {client.token}"},
                timeout=10.0,
                insecure=client.insecure,
            )
            ws.close()
            checked = True
            status = "101 Switching Protocols"
        except WebSocketError as exc:
            raise BusinessError(f"VNC handshake failed: {exc}") from exc
    if args.screenshot or actions:
        if args.wait and args.wait > 0:
            time.sleep(float(args.wait))
        try:
            rfb = RfbClient.connect(
                url,
                headers={"Authorization": f"trim {client.token}"},
                timeout=float(args.vnc_timeout),
                insecure=client.insecure,
            )
            framebuffer = {"width": rfb.width, "height": rfb.height}
            for action, value in actions:
                if action == "sleep":
                    time.sleep(float(value))
                elif action == "text":
                    rfb.type_text(str(value))
                elif action == "key":
                    rfb.tap(str(value))
                elif action == "combo":
                    rfb.combo(*value)
            if args.screenshot:
                screenshot_path = str(rfb.screenshot(args.screenshot, timeout=float(args.vnc_timeout)))
            rfb.close()
            checked = True
            status = "RFB session"
        except (RfbError, WebSocketError, OSError) as exc:
            raise BusinessError(f"VNC session failed: {exc}") from exc
    data = {
        "url": url,
        "vncName": info.get("vncName") or info.get("name"),
        "checked": checked,
        "status": status,
        "screenshot": screenshot_path,
        "framebuffer": framebuffer,
        "keys": [action for action, _value in actions],
    }
    out.emit(data)
    if not out.json_mode:
        print(url, file=sys.stdout)
        if checked:
            out.log(f"VNC: {status}")
        if screenshot_path:
            print(screenshot_path, file=sys.stdout)
    return EXIT_OK


def _lifecycle(args: argparse.Namespace, action: str) -> int:
    out = Output(json_mode=json_mode(args), table_mode=bool(_arg(args, "table", False)))
    client, config = _client_and_config(args)
    _vm, info = _resolve(client, args.name)
    _require_owned(config, info, force_other=bool(args.force_other), action=action)
    before = info
    name = str(info["name"])
    if action == "start":
        client.start(name)
        target = "RUNNING"
    elif action == "stop":
        if args.force:
            client.force_shutdown(name)
        else:
            client.shutdown(name)
        target = "SHUTOFF"
    elif action == "reboot":
        client.reboot(name)
        target = "RUNNING"
    elif action == "pause":
        client.pause(name)
        target = "PAUSED"
    elif action == "resume":
        client.resume(name)
        target = "RUNNING"
    else:
        raise UsageError(f"unknown lifecycle action {action}")
    if float(getattr(args, "wait", 0.0) or 0.0) > 0:
        after = _wait_for_state(
            client,
            name,
            target,
            timeout=float(args.wait),
            interval=float(getattr(args, "interval", 2.0)),
            log=out.log if not out.json_mode else None,
        )
    else:
        time.sleep(0.7)
        after = client.info(name)
    data = {
        "action": action,
        "target": {"name": name, "title": info.get("title")},
        "before": before,
        "after": after,
    }
    out.emit(data)
    if not out.json_mode:
        print(f"{action}: {info.get('title') or name}: {before.get('state')} -> {after.get('state')}", file=sys.stdout)
    return EXIT_OK


def cmd_start(args: argparse.Namespace) -> int:
    return _lifecycle(args, "start")


def cmd_stop(args: argparse.Namespace) -> int:
    return _lifecycle(args, "stop")


def cmd_reboot(args: argparse.Namespace) -> int:
    return _lifecycle(args, "reboot")


def cmd_pause(args: argparse.Namespace) -> int:
    return _lifecycle(args, "pause")


def cmd_resume(args: argparse.Namespace) -> int:
    return _lifecycle(args, "resume")


def cmd_delete(args: argparse.Namespace) -> int:
    out = Output(json_mode=json_mode(args), table_mode=bool(_arg(args, "table", False)))
    client, config = _client_and_config(args)
    _vm, info = _resolve(client, args.name)
    _require_owned(config, info, force_other=bool(args.force_other), action="delete")
    if info.get("state") == "RUNNING":
        raise BusinessError("VM is running; stop it before deleting")
    if not args.yes:
        raise UsageError("delete is irreversible; pass --yes after verifying the target")
    out.log(
        f"deleting {info.get('title') or info.get('name')} "
        f"({info.get('name')}), {len(info.get('disks') or [])} disk(s)"
    )
    client.delete(str(info["name"]))
    config.forget_vm(str(info["name"]))
    save_config(config)
    data = {
        "action": "delete",
        "target": {"name": info.get("name"), "title": info.get("title")},
        "before": info,
        "after": None,
    }
    out.emit(data)
    if not out.json_mode:
        print(f"deleted {info.get('title') or info.get('name')}", file=sys.stdout)
    return EXIT_OK


def cmd_adopt(args: argparse.Namespace) -> int:
    out = Output(json_mode=json_mode(args), table_mode=bool(_arg(args, "table", False)))
    client, config = _client_and_config(args)
    _vm, info = _resolve(client, args.name)
    config.adopt_vm(info)
    save_config(config)
    out.emit({"adopted": True, "vm": info, "configPath": describe_config_path()})
    if not out.json_mode:
        print(f"adopted {info.get('title') or info.get('name')} ({info.get('name')})", file=sys.stdout)
    return EXIT_OK


def cmd_snapshot(args: argparse.Namespace) -> int:
    out = Output(json_mode=json_mode(args), table_mode=bool(_arg(args, "table", False)))
    client, config = _client_and_config(args)
    command = args.snapshot_command
    _vm, info = _resolve(client, args.name)
    name = str(info["name"])
    if command == "list":
        snapshots = client.snapshot_list(name)
        out.emit(snapshots)
        if not out.json_mode:
            out.table(
                ["NAME", "DESCRIPTION", "CREATED", "VM STATE"],
                [
                    [
                        item.get("name"),
                        item.get("description") or "-",
                        format_epoch(item.get("createdTime")),
                        item.get("vmState") or "-",
                    ]
                    for item in snapshots
                ],
                empty="no snapshots",
            )
        return EXIT_OK
    _require_owned(config, info, force_other=bool(args.force_other), action=f"snapshot {command}")
    before_snapshots = client.snapshot_list(name)
    if command == "create":
        snap_name = args.snap_name or f"snap-{datetime.now().strftime('%Y%m%d-%H%M%S')}"
        if not re.match(r"^[A-Za-z0-9._-]{1,64}$", snap_name):
            raise UsageError("snapshot name must match [A-Za-z0-9._-]{1,64}")
        existing = {str(item.get("name")) for item in before_snapshots}
        base = snap_name
        suffix = 2
        while snap_name in existing:
            snap_name = f"{base}-{suffix}"
            suffix += 1
        result = client.snapshot_create(name, snap_name, str(args.desc or ""))
        after_snapshots = client.snapshot_list(name)
        data = {
            "action": "snapshot create",
            "vm": {"name": name, "title": info.get("title")},
            "snapshot": result or {"name": snap_name},
            "before": before_snapshots,
            "after": after_snapshots,
        }
        out.emit(data)
        if not out.json_mode:
            print(f"created snapshot {snap_name} on {info.get('title') or name}", file=sys.stdout)
        return EXIT_OK
    if command in ("revert", "delete"):
        snapshot = _snapshot_by_name(client, name, args.snap)
        if not args.yes:
            raise UsageError("snapshot operations are irreversible; pass --yes")
        out.log(
            f"{command} snapshot {snapshot.get('name')} on {info.get('title') or name} "
            f"(created {format_epoch(snapshot.get('createdTime'))})"
        )
        if command == "revert":
            client.snapshot_revert(name, str(snapshot["name"]))
        else:
            client.snapshot_delete(name, str(snapshot["name"]))
        after_snapshots = client.snapshot_list(name)
        data = {
            "action": f"snapshot {command}",
            "vm": {"name": name, "title": info.get("title")},
            "snapshot": snapshot,
            "before": before_snapshots,
            "after": after_snapshots,
        }
        out.emit(data)
        if not out.json_mode:
            print(f"{command}ed snapshot {snapshot.get('name')}", file=sys.stdout)
        return EXIT_OK
    raise UsageError(f"unknown snapshot command {command}")


def cmd_modify(args: argparse.Namespace) -> int:
    out = Output(json_mode=json_mode(args), table_mode=bool(_arg(args, "table", False)))
    client, config = _client_and_config(args)
    _vm, info = _resolve(client, args.name)
    _require_owned(config, info, force_other=bool(args.force_other), action="modify")
    changes: dict[str, Any] = {
        "cpu": args.cpu,
        "mem": args.mem,
        "desc": args.desc,
        "autostart": args.autostart,
        "clipboard": args.clipboard,
        "title": args.title,
        "bootType": args.boot,
    }
    if args.title is not None:
        changes["title"] = str(args.title).strip()
    if args.file:
        changes["file"] = _load_json_file(args.file)
    if all(value is None for key, value in changes.items() if key != "file") and not args.file:
        raise UsageError("nothing to modify; pass --cpu/--mem/--desc/--title/--autostart/--clipboard/--file")
    payload = build_modify_payload(info, changes)
    if payload.get("title") and payload.get("title") != info.get("title"):
        if not client.available_title(str(payload["title"]), name=str(info["name"])):
            raise BusinessError(f"VM title is already in use: {payload['title']}")
    before = info
    client.modify(payload)
    after = client.info(str(info["name"]))
    out.emit({"action": "modify", "target": {"name": info.get("name"), "title": info.get("title")}, "before": before, "after": after})
    if not out.json_mode:
        print(f"modified {info.get('title') or info.get('name')}", file=sys.stdout)
    return EXIT_OK


def cmd_create(args: argparse.Namespace) -> int:
    out = Output(json_mode=json_mode(args), table_mode=bool(_arg(args, "table", False)))
    client, config = _client_and_config(args)
    if args.no_network and args.network:
        raise UsageError("use either --network or --no-network, not both")
    if args.disk_size and not args.image and not args.file:
        raise UsageError("--disk-size is only valid together with --image")
    hardware = client.hardware()
    support = client.hostdev_support()
    volumes = client.volumes()
    ovs_inited = client.ovs_inited()
    ovs = client.ovs_list()
    spec: dict[str, Any] = {}
    if args.file:
        raw = _load_json_file(args.file)
        if not isinstance(raw, dict):
            raise UsageError("create --file must contain a JSON object")
        spec.update(raw)
    if args.name:
        spec["title"] = args.name
    if args.os_type:
        spec["osType"] = args.os_type
    if args.os_version:
        spec["osVersion"] = args.os_version
    if args.cpu is not None:
        spec["cpuCoreCnt"] = args.cpu
    if args.mem:
        spec["memCapacityInBytes"] = parse_size(args.mem, field="memory")
    if args.bus:
        spec["busType"] = args.bus
    if args.hypervisor:
        spec["hypervisor"] = args.hypervisor
    if args.chipset:
        spec["chipset"] = args.chipset
    if args.firmware:
        spec["firmware"] = args.firmware
    if args.video:
        spec["videoType"] = args.video
    if args.boot:
        spec["bootType"] = args.boot
    if args.desc is not None:
        spec["desc"] = args.desc
    if args.autostart is not None:
        spec["autostart"] = args.autostart
    if args.clipboard is not None:
        spec["clipboard"] = args.clipboard
    if args.iso:
        spec["iso"] = args.iso
    if args.driver_iso:
        spec["driverIso"] = args.driver_iso
    if args.seed:
        spec["seed"] = args.seed
    if args.image:
        spec["image"] = args.image
    if args.disk_size:
        spec["imageSize"] = args.disk_size
    if args.disk:
        spec["disks"] = list(args.disk)
    if args.no_network:
        spec["networks"] = []
    elif args.network:
        spec["networks"] = list(args.network)

    payload = build_create_payload(
        spec,
        hardware=hardware,
        volumes=volumes,
        ovs_list=ovs,
        support=support,
    )
    title = str(payload.get("title"))
    if not client.available_title(title):
        raise BusinessError(f"VM title is already in use: {title}")
    if payload.get("disks"):
        # Re-fetch full info for the memory accounting; list is usually enough but
        # info is authoritative for memCapacityInBytes.
        vms = client.list_vms()
        full_vms = list(_fetch_infos(client, vms).values()) or vms
    else:
        vms = client.list_vms()
        full_vms = list(_fetch_infos(client, vms).values()) or vms
    facts = preflight_create(
        payload,
        hardware=hardware,
        volumes=volumes,
        ovs_inited=ovs_inited,
        ovs_list=ovs,
        support=support,
        vms=full_vms,
    )
    if args.dry_run:
        data = {"dryRun": True, "payload": payload, "preflight": facts, "availableTitle": True}
        out.emit(data)
        if not out.json_mode:
            print(json.dumps(payload, ensure_ascii=False, indent=2), file=sys.stdout)
        return EXIT_OK
    out.log(f"creating {title} ...")
    created = client.create(payload)
    name = str(created.get("name") or "")
    if not name:
        raise BusinessError("create returned no VM name")
    try:
        info = client.info(name)
    except FnosVmError:
        info = created
    config.record_vm(info, source="create")
    save_config(config)
    out.emit({"action": "create", "before": None, "after": info, "payload": payload})
    if not out.json_mode:
        print(f"created {info.get('title') or title} ({name})", file=sys.stdout)
        _render_info(out, info, owned=True, vnc_url=_vnc_url(client, info))
    return EXIT_OK


# ---------------------------------------------------------------------------
# Image commands
# ---------------------------------------------------------------------------
def _seed_material(args: argparse.Namespace) -> tuple[str, str, str | None]:
    if args.password and (":" in args.password or "\n" in args.password or "\r" in args.password):
        raise UsageError("password must not contain ':' or newlines")
    keys = _read_keys(args.ssh_key_file) + [str(key).strip() for key in (args.ssh_key or [])]
    user_data = cloud_init_user_data(
        user=args.user,
        password=args.password,
        ssh_keys=keys,
        hostname=args.hostname,
    )
    meta = cloud_init_meta_data(hostname=args.hostname, instance_id=args.meta)
    network = None
    if args.network_config:
        try:
            network = Path(args.network_config).read_text(encoding="utf-8")
        except Exception as exc:
            raise UsageError(f"cannot read network config {args.network_config}: {exc}") from exc
    elif args.static_ip:
        if not args.mac:
            raise UsageError("--static-ip requires --mac")
        network = network_config_static(
            mac=args.mac,
            address=args.static_ip,
            prefix=int(args.prefix),
            gateway=args.gateway,
            dns=args.dns or [],
        )
    elif args.mac:
        network = network_config_dhcp(mac=args.mac)
    return user_data, meta, network


def cmd_image(args: argparse.Namespace) -> int:
    out = Output(json_mode=json_mode(args), table_mode=bool(_arg(args, "table", False)))
    command = args.image_command
    if command == "list":
        data = [{"alias": alias, "url": url} for alias, url in sorted(IMAGE_ALIASES.items())]
        out.emit(data)
        if not out.json_mode:
            out.table(["ALIAS", "URL"], [[item["alias"], item["url"]] for item in data])
        return EXIT_OK
    if command == "download":
        if args.list:
            data = [{"alias": alias, "url": url} for alias, url in sorted(IMAGE_ALIASES.items())]
            out.emit(data)
            if not out.json_mode:
                out.table(["ALIAS", "URL"], [[item["alias"], item["url"]] for item in data])
            return EXIT_OK
        if not args.dest:
            raise UsageError("image download requires --dest PATH")
        if not args.source:
            raise UsageError("image download requires an image alias or URL")
        path = download(
            resolve_image_url(args.source),
            args.dest,
            sha256=args.sha256,
            force=args.force,
            timeout=float(_arg(args, "timeout", 300.0)),
            log=out.log if not out.json_mode else None,
        )
        digest = sha256_file(path)
        data = {"path": str(path), "sizeBytes": path.stat().st_size, "sha256": digest}
        out.emit(data)
        if not out.json_mode:
            out.kv([("Path", path), ("Size", format_size(data["sizeBytes"])), ("SHA-256", digest)])
        return EXIT_OK
    if command == "resize":
        size = parse_size(args.size, field="size")
        if args.output and str(args.output) != str(args.input):
            import shutil

            shutil.copy2(args.input, args.output)
            target = args.output
        else:
            target = args.input
        old, new = patch_qcow2_size(target, size)
        data = {"path": target, "oldVirtualSizeBytes": old, "virtualSizeBytes": new}
        out.emit(data)
        if not out.json_mode:
            print(f"{target}: {format_size(old)} -> {format_size(new)}", file=sys.stdout)
        return EXIT_OK
    if command == "seed":
        user_data, meta, network = _seed_material(args)
        path = build_seed(args.output, user_data=user_data, meta_data=meta, network_config=network)
        data = {
            "path": str(path),
            "sizeBytes": path.stat().st_size,
            "files": ["user-data", "meta-data"] + (["network-config"] if network else []),
        }
        out.emit(data)
        if not out.json_mode:
            out.kv([("Seed ISO", path), ("Size", format_size(data["sizeBytes"])), ("Files", ", ".join(data["files"]))])
        return EXIT_OK
    if command == "autoinstall":
        if not args.password_hash and not args.password:
            raise UsageError("autoinstall requires --password or --password-hash")
        password_hash = args.password_hash or sha512_crypt(args.password)
        keys = _read_keys(args.ssh_key_file) + [str(key).strip() for key in (args.ssh_key or [])]
        network = None
        if args.network_config:
            try:
                network = Path(args.network_config).read_text(encoding="utf-8-sig")
            except Exception as exc:
                raise UsageError(f"cannot read network config {args.network_config}: {exc}") from exc
        elif args.static_ip:
            if not args.mac:
                raise UsageError("--static-ip requires --mac")
            network = network_config_static(
                mac=args.mac,
                address=args.static_ip,
                prefix=int(args.prefix),
                gateway=args.gateway,
                dns=args.dns or [],
            )
        elif args.mac:
            network = network_config_dhcp(mac=args.mac)
        user_data = autoinstall_user_data(
            user=args.user,
            password_hash=password_hash,
            ssh_keys=keys,
            hostname=args.hostname,
            network_config=network,
            storage_layout=args.storage_layout,
            packages=args.package or [],
            late_commands=args.late_command or [],
            timezone=args.timezone,
            shutdown=args.shutdown,
            allow_pw=bool(args.allow_pw),
        )
        meta = cloud_init_meta_data(hostname=args.hostname, instance_id=f"autoinstall-{args.hostname}")
        path = build_autoinstall_seed(args.output, user_data=user_data, meta_data=meta)
        data = {
            "path": str(path),
            "sizeBytes": path.stat().st_size,
            "files": ["user-data", "meta-data"],
            "user": args.user,
            "hostname": args.hostname,
            "storageLayout": args.storage_layout,
            "shutdown": args.shutdown,
            "sshKeys": len(keys),
            "passwordConfigured": True,
        }
        out.emit(data)
        if not out.json_mode:
            out.kv(
                [
                    ("Autoinstall seed", path),
                    ("Size", format_size(data["sizeBytes"])),
                    ("User", args.user),
                    ("Hostname", args.hostname),
                    ("SSH keys", len(keys)),
                    ("Shutdown", args.shutdown),
                ]
            )
        return EXIT_OK
    if command == "push":
        push_with_fnos_cli(
            args.file,
            args.remote_dir,
            profile=args.profile,
            fnos_cli=args.fnos_cli,
            log=out.log,
        )
        data = {
            "uploaded": args.file,
            "remoteDir": args.remote_dir,
            "remotePath": args.remote_dir.rstrip("/") + "/" + Path(args.file).name,
        }
        out.emit(data)
        if not out.json_mode:
            print(f"uploaded {args.file} to {args.remote_dir}", file=sys.stdout)
        return EXIT_OK
    if command == "prepare":
        size = parse_size(args.disk_size, field="disk size")
        prepared = prepare_image(
            source=args.source,
            dest=args.dest,
            disk_size=size,
            force=args.force,
            log=out.log if not out.json_mode else None,
        )
        seed_path = args.seed_output or str(Path(args.dest).with_suffix(".seed.iso"))
        seed_args = argparse.Namespace(
            user=args.user,
            password=args.password,
            ssh_key_file=args.ssh_key_file,
            ssh_key=args.ssh_key,
            hostname=args.hostname,
            meta=None,
            static_ip=None,
            prefix=24,
            gateway=None,
            dns=None,
            mac=None,
            network_config=None,
        )
        user_data, meta, network = _seed_material(seed_args)
        build_seed(seed_path, user_data=user_data, meta_data=meta, network_config=network)
        image_name = Path(args.dest).name
        seed_name = Path(seed_path).name
        result = {
            **prepared,
            "seedPath": str(seed_path),
            "seedSizeBytes": Path(seed_path).stat().st_size,
            "remoteImagePath": None,
            "remoteSeedPath": None,
        }
        if args.remote_dir:
            push_with_fnos_cli(args.dest, args.remote_dir, profile=args.profile, fnos_cli=args.fnos_cli, log=out.log)
            push_with_fnos_cli(seed_path, args.remote_dir, profile=args.profile, fnos_cli=args.fnos_cli, log=out.log)
            result["remoteImagePath"] = args.remote_dir.rstrip("/") + "/" + image_name
            result["remoteSeedPath"] = args.remote_dir.rstrip("/") + "/" + seed_name
        out.emit(result)
        if not out.json_mode:
            out.kv(
                [
                    ("Image", prepared["path"]),
                    ("Virtual size", format_size(prepared["virtualSizeBytes"])),
                    ("Seed ISO", seed_path),
                    ("Remote image", result["remoteImagePath"] or "-"),
                    ("Remote seed", result["remoteSeedPath"] or "-"),
                ]
            )
            if result["remoteImagePath"]:
                print("", file=sys.stdout)
                out.log("create command:")
                out.log(
                    f"fnos-vm create --name <title> --image {result['remoteImagePath']} "
                    f"--disk-size {format_size(prepared['virtualSizeBytes'])} "
                    f"--seed {result['remoteSeedPath']}"
                )
        return EXIT_OK
    raise UsageError(f"unknown image command {command}")


# ---------------------------------------------------------------------------
# Dispatch
# ---------------------------------------------------------------------------
def json_mode(args: argparse.Namespace) -> bool:
    if getattr(args, "table", False):
        return False
    explicit = getattr(args, "json", None)
    if explicit is not None:
        return bool(explicit)
    return default_json_mode()


def dispatch(args: argparse.Namespace) -> int:
    command = args.command
    if command == "login":
        return cmd_login(args)
    if command == "logout":
        return cmd_logout(args)
    if command == "host":
        return cmd_host(args)
    if command == "list":
        return cmd_list(args)
    if command == "info":
        return cmd_info(args)
    if command == "create":
        return cmd_create(args)
    if command == "wait":
        return cmd_wait(args)
    if command == "vnc":
        return cmd_vnc(args)
    if command == "delete":
        return cmd_delete(args)
    if command == "adopt":
        return cmd_adopt(args)
    if command == "modify":
        return cmd_modify(args)
    if command == "snapshot":
        return cmd_snapshot(args)
    if command == "image":
        return cmd_image(args)
    if command in {"start", "stop", "reboot", "pause", "resume"}:
        return _lifecycle(args, command)
    raise UsageError(f"unknown command {command}")


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    _set_config_env(args)
    if getattr(args, "json", False) and getattr(args, "table", False):
        print("error: --json and --table are mutually exclusive", file=sys.stderr)
        return 2
    out = Output(json_mode=json_mode(args), table_mode=bool(_arg(args, "table", False)))
    try:
        return dispatch(args)
    except FnosVmError as exc:
        out.error(exc.as_dict())
        return exc.exit_code
    except KeyboardInterrupt:
        out.error({"ok": False, "error": {"kind": "interrupted", "msg": "interrupted"}})
        return 130
    except Exception as exc:  # pragma: no cover - defensive
        if os.environ.get("FNOS_VM_DEBUG") or _arg(args, "debug", False):
            traceback.print_exc(file=sys.stderr)
        out.error(
            {
                "ok": False,
                "error": {
                    "kind": "internal",
                    "msg": f"{type(exc).__name__}: {exc}",
                },
            }
        )
        return 1
