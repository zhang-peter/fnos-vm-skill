"""Cloud image download, qcow2 resizing and cloud-init seed helpers."""

from __future__ import annotations

import hashlib
import os
import posixpath
import re
import shutil
import subprocess
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any, Iterable

from .errors import BusinessError, UsageError
from .iso9660 import build_iso
from .payload import format_size, patch_qcow2_size, read_qcow2_size, validate_remote_path


IMAGE_ALIASES = {
    "ubuntu-24.04": "https://cloud-images.ubuntu.com/noble/current/noble-server-cloudimg-amd64.img",
    "ubuntu-24.04-minimal": "https://cloud-images.ubuntu.com/minimal/releases/noble/release/ubuntu-24.04-minimal-cloudimg-amd64.img",
    "ubuntu-26.04": "https://cloud-images.ubuntu.com/resolute/current/resolute-server-cloudimg-amd64.img",
    "ubuntu-22.04": "https://cloud-images.ubuntu.com/jammy/current/jammy-server-cloudimg-amd64.img",
    "debian-12": "https://cloud.debian.org/images/cloud/bookworm/latest/debian-12-genericcloud-amd64.qcow2",
    "debian-13": "https://cloud.debian.org/images/cloud/trixie/latest/debian-13-genericcloud-amd64.qcow2",
    "alpine-3.22": "https://dl-cdn.alpinelinux.org/alpine/v3.22/releases/cloud/nocloud_alpine-3.22.1-x86_64-bios-cloudinit-r0.qcow2",
}


def resolve_image_url(name_or_url: str) -> str:
    text = str(name_or_url or "").strip()
    if not text:
        raise UsageError("image name or URL is required")
    if text in IMAGE_ALIASES:
        return IMAGE_ALIASES[text]
    if text.startswith(("http://", "https://")):
        return text
    aliases = ", ".join(sorted(IMAGE_ALIASES))
    raise UsageError(f"unknown image {text!r}; use a URL or one of: {aliases}")


def sha256_file(path: str | os.PathLike[str]) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def download(
    url: str,
    dest: str | os.PathLike[str],
    *,
    sha256: str | None = None,
    force: bool = False,
    timeout: float = 300.0,
    log=None,
) -> Path:
    target = Path(dest)
    if target.exists() and not force:
        if sha256 and sha256_file(target).lower() != sha256.lower():
            raise BusinessError(f"{target} exists but its SHA-256 does not match")
        if log:
            log(f"reusing existing image {target} ({format_size(target.stat().st_size)})")
        return target
    target.parent.mkdir(parents=True, exist_ok=True)
    partial = target.with_name(target.name + ".part")
    request = urllib.request.Request(url, headers={"User-Agent": "fnos-vm/0.1"})
    if log:
        log(f"downloading {url}")
    try:
        response = urllib.request.urlopen(request, timeout=timeout)
    except urllib.error.HTTPError as exc:
        raise BusinessError(f"download failed: HTTP {exc.code} for {url}") from exc
    except Exception as exc:
        raise BusinessError(f"download failed for {url}: {exc}") from exc
    try:
        with response:
            total = int(response.headers.get("Content-Length") or 0)
            digest = hashlib.sha256()
            written = 0
            last_report = 0.0
            with open(partial, "wb") as handle:
                while True:
                    chunk = response.read(1024 * 256)
                    if not chunk:
                        break
                    handle.write(chunk)
                    digest.update(chunk)
                    written += len(chunk)
                    now = time.monotonic()
                    if log and (now - last_report > 2.0 or written == total):
                        if total:
                            pct = written * 100.0 / total
                            log(
                                f"\r  {format_size(written)} / {format_size(total)} "
                                f"({pct:5.1f}%)"
                            )
                        else:
                            log(f"\r  {format_size(written)}")
                        last_report = now
            if log:
                log("")
            actual = digest.hexdigest()
            if sha256 and actual.lower() != sha256.lower():
                partial.unlink(missing_ok=True)
                raise BusinessError(
                    f"SHA-256 mismatch for {url}: expected {sha256}, got {actual}"
                )
        os.replace(partial, target)
    except Exception:
        try:
            partial.unlink(missing_ok=True)
        except Exception:
            pass
        raise
    return target


def _yaml_quote(value: str) -> str:
    text = str(value)
    if "\n" in text or "\r" in text:
        raise UsageError("YAML scalar must not contain newlines")
    return '"' + text.replace("\\", "\\\\").replace('"', '\\"') + '"'


def sha512_crypt(password: str, *, salt: str | None = None) -> str:
    """Generate a SHA-512 crypt hash for autoinstall identity.password."""
    if not password:
        raise UsageError("password is empty")
    if salt is None:
        alphabet = "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789./"
        salt = "".join(alphabet[b % len(alphabet)] for b in os.urandom(16))
    openssl = shutil.which("openssl")
    if not openssl and os.name == "nt":
        for candidate in (
            r"C:\Program Files\Git\usr\bin\openssl.exe",
            r"C:\Program Files\Git\mingw64\bin\openssl.exe",
        ):
            if Path(candidate).exists():
                openssl = candidate
                break
    if openssl:
        result = subprocess.run(
            [openssl, "passwd", "-6", "-salt", salt, password],
            capture_output=True,
            text=True,
            timeout=15,
        )
        if result.returncode == 0 and result.stdout.strip().startswith("$6$"):
            return result.stdout.strip()
    try:
        import crypt  # type: ignore

        return crypt.crypt(password, crypt.mksalt(crypt.METHOD_SHA512))
    except Exception as exc:
        raise UsageError(
            "cannot generate a SHA-512 crypt password hash; install OpenSSL or use "
            "--password-hash with a hash generated by `openssl passwd -6`"
        ) from exc


def _indent(text: str, spaces: int) -> str:
    prefix = " " * spaces
    return "\n".join(prefix + line if line else line for line in text.splitlines())


def autoinstall_user_data(
    *,
    user: str = "ubuntu",
    password_hash: str | None = None,
    ssh_keys: Iterable[str] = (),
    hostname: str = "ubuntu",
    network_config: str | None = None,
    storage_layout: str = "direct",
    packages: Iterable[str] = (),
    late_commands: Iterable[str] = (),
    timezone: str = "Etc/UTC",
    shutdown: str = "poweroff",
    allow_pw: bool = False,
) -> str:
    user = str(user or "").strip()
    if not re.match(r"^[a-z_][a-z0-9_-]{0,31}$", user):
        raise UsageError("autoinstall user name must match [a-z_][a-z0-9_-]{0,31}")
    hostname = str(hostname or "").strip()
    if not re.match(r"^[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?$", hostname):
        raise UsageError("hostname must be a valid DNS label")
    if not password_hash:
        raise UsageError("autoinstall requires a password hash")
    if not password_hash.startswith(("$6$", "$5$", "$y$", "$2")):
        raise UsageError("password hash must be a crypt hash (for example $6$...)")
    if shutdown not in ("poweroff", "reboot"):
        raise UsageError("shutdown must be poweroff or reboot")
    if storage_layout not in ("direct", "lvm", "zfs"):
        raise UsageError("storage layout must be direct, lvm or zfs")

    keys = [str(key).strip() for key in ssh_keys if str(key).strip()]
    lines = [
        "#cloud-config",
        "autoinstall:",
        "  version: 1",
        "  identity:",
        f"    hostname: {hostname}",
        f"    username: {user}",
        f"    password: {_yaml_quote(password_hash)}",
    ]
    if keys:
        lines.extend(
            [
                "  ssh:",
                "    install-server: true",
                f"    allow-pw: {'true' if allow_pw else 'false'}",
                "    authorized-keys:",
            ]
        )
        for key in keys:
            lines.append(f"      - {_yaml_quote(key)}")
    else:
        lines.extend(
            [
                "  ssh:",
                "    install-server: true",
                "    allow-pw: true",
                "    authorized-keys: []",
            ]
        )
    lines.extend(
        [
            "  storage:",
            "    layout:",
            f"      name: {storage_layout}",
            "  keyboard:",
            "    layout: us",
            f"  timezone: {_yaml_quote(timezone)}",
            "  updates: security",
        ]
    )
    if network_config:
        lines.append("  network:")
        lines.append(_indent(network_config.rstrip("\n"), 4))
    pkgs = [str(pkg) for pkg in packages if str(pkg).strip()]
    if pkgs:
        lines.append("  packages:")
        for pkg in pkgs:
            lines.append(f"    - {_yaml_quote(pkg)}")
    commands = [str(cmd) for cmd in late_commands if str(cmd).strip()]
    if commands:
        lines.append("  late-commands:")
        for cmd in commands:
            lines.append(f"    - {_yaml_quote(cmd)}")
    lines.append(f"  shutdown: {shutdown}")
    return "\n".join(lines) + "\n"


def build_autoinstall_seed(
    output: str | os.PathLike[str],
    *,
    user_data: str,
    meta_data: str,
) -> Path:
    return build_seed(output, user_data=user_data, meta_data=meta_data)



def cloud_init_user_data(
    *,
    user: str = "ubuntu",
    password: str | None = None,
    ssh_keys: Iterable[str] = (),
    hostname: str = "fnos-vm",
    packages: Iterable[str] = (),
) -> str:
    user = str(user or "").strip()
    if not user:
        raise UsageError("cloud-init user name is required")
    if not re.match(r"^[a-z_][a-z0-9_-]{0,31}$", user):
        raise UsageError("cloud-init user name must match [a-z_][a-z0-9_-]{0,31}")
    hostname = str(hostname or "").strip()
    if not re.match(r"^[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?$", hostname):
        raise UsageError("hostname must be a valid DNS label")
    keys = [str(key).strip() for key in ssh_keys if str(key).strip()]
    lines = [
        "#cloud-config",
        f"hostname: {hostname}",
        "manage_etc_hosts: true",
        "preserve_hostname: false",
        "disable_root: true",
        "users:",
        f"  - name: {user}",
        f"    gecos: {user}",
        "    groups: [adm, sudo]",
        "    sudo: ALL=(ALL) NOPASSWD:ALL",
        "    shell: /bin/bash",
        "    lock_passwd: false",
    ]
    if keys:
        lines.append("    ssh_authorized_keys:")
        for key in keys:
            lines.append(f"      - {_yaml_quote(key)}")
    else:
        lines.append("    ssh_authorized_keys: []")
    if password:
        lines.extend(
            [
                "ssh_pwauth: true",
                "chpasswd:",
                "  expire: false",
                "  list: |",
                f"    {user}:{password}",
            ]
        )
    else:
        lines.append("ssh_pwauth: false")
    packages = [str(pkg) for pkg in packages if str(pkg).strip()]
    if packages:
        lines.append("packages:")
        for pkg in packages:
            lines.append(f"  - {pkg}")
    lines.extend(
        [
            "package_update: false",
            "final_message: \"fnos-vm cloud-init finished after $UPTIME seconds\"",
        ]
    )
    return "\n".join(lines) + "\n"


def cloud_init_meta_data(*, hostname: str = "fnos-vm", instance_id: str | None = None) -> str:
    instance = instance_id or f"fnos-vm-{int(time.time())}"
    return (
        "instance-id: " + str(instance) + "\n"
        "local-hostname: " + str(hostname) + "\n"
    )


def network_config_static(
    *,
    mac: str,
    address: str,
    prefix: int = 24,
    gateway: str | None = None,
    dns: Iterable[str] = (),
) -> str:
    lines = [
        "version: 2",
        "ethernets:",
        "  primary:",
        f"    match:",
        f"      macaddress: {_yaml_quote(mac)}",
        "    set-name: eth0",
        "    dhcp4: false",
        f"    addresses: [{address}/{int(prefix)}]",
    ]
    if gateway:
        lines.extend(
            [
                "    routes:",
                "      - to: default",
                f"        via: {gateway}",
            ]
        )
    dns_list = [str(item) for item in dns if str(item).strip()]
    if dns_list:
        lines.append("    nameservers:")
        lines.append("      addresses: [" + ", ".join(dns_list) + "]")
    return "\n".join(lines) + "\n"


def network_config_dhcp(*, mac: str) -> str:
    return (
        "version: 2\n"
        "ethernets:\n"
        "  primary:\n"
        "    match:\n"
        f"      macaddress: {_yaml_quote(mac)}\n"
        "    set-name: eth0\n"
        "    dhcp4: true\n"
    )


def build_seed(
    output: str | os.PathLike[str],
    *,
    user_data: str,
    meta_data: str,
    network_config: str | None = None,
    volume_id: str = "CIDATA",
) -> Path:
    files: dict[str, str] = {"user-data": user_data, "meta-data": meta_data}
    if network_config:
        files["network-config"] = network_config
    return build_iso(output, files, volume_id=volume_id)


def _find_fnos_cli(explicit: str | None = None) -> str | None:
    candidates: list[str] = []
    if explicit:
        candidates.append(explicit)
    env = os.environ.get("FNOS_CLI")
    if env:
        candidates.append(env)
    if os.name == "nt":
        candidates.append(str(Path.home() / ".dsh" / "skills" / "fnos-cli" / "scripts" / "fnos-cli.cmd"))
        candidates.append(str(Path.home() / ".dsh" / "skills" / "fnos-cli" / "scripts" / "fnos-cli.exe"))
    else:
        candidates.append(str(Path.home() / ".dsh" / "skills" / "fnos-cli" / "scripts" / "fnos-cli"))
    found = shutil.which("fnos-cli")
    if found:
        candidates.append(found)
    for candidate in candidates:
        if candidate and Path(candidate).exists():
            return candidate
    return None


def push_with_fnos_cli(
    local_file: str | os.PathLike[str],
    remote_dir: str,
    *,
    profile: str = "fnos",
    fnos_cli: str | None = None,
    allow_insecure_http: bool = True,
    overwrite: bool = True,
    log=None,
) -> None:
    remote_dir = validate_remote_path(remote_dir, field="remote directory")
    if not remote_dir.endswith("/"):
        # validate_remote_path returns the path unchanged; ensure it is a dir-like path.
        pass
    local = Path(local_file)
    if not local.exists():
        raise UsageError(f"local file does not exist: {local}")
    executable = _find_fnos_cli(fnos_cli)
    if not executable:
        raise BusinessError(
            "fnos-cli was not found; install the fnos-cli skill, or upload the files manually "
            "and pass the resulting /vol... paths to `fnos-vm create`"
        )
    args: list[str] = [executable, "--profile", profile]
    if allow_insecure_http:
        args.append("--allow-insecure-http")
    args.extend(["file", "upload"])
    args.append("--yes")
    if overwrite:
        args.extend(["--overwrite", "replace"])
    args.extend([remote_dir, str(local)])
    if executable.lower().endswith((".cmd", ".bat")) and os.name == "nt":
        args = ["cmd", "/c"] + args
    if log:
        log(f"uploading {local.name} to {remote_dir}")
    result = subprocess.run(args, capture_output=True, text=True, timeout=1800)
    if result.returncode != 0:
        detail = (result.stderr or result.stdout or "").strip()
        raise BusinessError(f"fnos-cli upload failed: {detail[:800]}")
    if log and (result.stdout or "").strip():
        log(result.stdout.strip())


def prepare_image(
    *,
    source: str,
    dest: str | os.PathLike[str],
    disk_size: int | None = None,
    force: bool = False,
    log=None,
) -> dict[str, Any]:
    url = resolve_image_url(source)
    path = download(url, dest, force=force, log=log)
    old_size = read_qcow2_size(str(path))
    new_size = old_size
    if disk_size and disk_size < old_size:
        raise UsageError(
            f"requested disk size {format_size(disk_size)} is smaller than the image "
            f"virtual size {format_size(old_size)}"
        )
    if disk_size and disk_size > old_size:
        old_size, new_size = patch_qcow2_size(str(path), disk_size)
        if log:
            log(f"resized qcow2 virtual size {format_size(old_size)} -> {format_size(new_size)}")
    return {
        "path": str(path),
        "url": url,
        "virtualSizeBytes": new_size,
        "originalVirtualSizeBytes": old_size,
        "sizeBytes": path.stat().st_size,
    }


def remote_file(remote_dir: str, local_name: str) -> str:
    remote_dir = str(remote_dir).rstrip("/")
    return posixpath.join(remote_dir, local_name)
