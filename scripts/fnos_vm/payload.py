"""Payload construction and validation for the fnOS VM API.

The field names here were verified against trim.vm 0.9.3 on 2026-11-08/09.
"""

from __future__ import annotations

import copy
import os
import posixpath
import re
import secrets
import struct
from typing import Any, Iterable, Mapping

from .errors import PreflightError, UsageError


SIZE_RE = re.compile(r"^\s*(\d+(?:\.\d+)?)\s*([KMGTPE]?)(i?B?)\s*$", re.IGNORECASE)
MAC_RE = re.compile(r"^[0-9a-fA-F]{2}(?::[0-9a-fA-F]{2}){5}$")
REMOTE_PATH_RE = re.compile(r"^/vol(\d+)/")
TITLE_MAX = 64
BUS_TYPES = {"virtio", "scsi", "sata", "ide"}
NIC_MODELS = {"virtio", "e1000", "e1000e", "rtl8139"}
CHIPSETS = {"q35", "i440fx"}
FIRMWARES = {"efi", "bios"}
VIDEO_TYPES = {"vga", "virtio", "vmvga", "qxl"}
OS_TYPES = {"linux", "windows", "others"}
BOOT_TYPES = {"default", "cdrom"}


def parse_size(value: Any, *, field: str = "size", allow_zero: bool = False) -> int:
    if isinstance(value, int):
        number = value
    elif isinstance(value, float):
        number = int(value)
    else:
        text = str(value or "").strip()
        if not text:
            raise UsageError(f"{field} is empty")
        match = SIZE_RE.match(text)
        if not match:
            raise UsageError(
                f"invalid {field} {value!r}; use a size such as 20G, 4096M or 1GiB"
            )
        amount = float(match.group(1))
        unit = match.group(2).upper()
        if unit:
            power = {"K": 1, "M": 2, "G": 3, "T": 4, "P": 5, "E": 6}[unit]
            number = int(round(amount * (1024**power)))
        else:
            number = int(round(amount))
    if number < 0 or (number == 0 and not allow_zero):
        raise UsageError(f"{field} must be greater than zero")
    return number


def format_size(value: Any) -> str:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return "-"
    units = ["B", "KiB", "MiB", "GiB", "TiB"]
    index = 0
    while number >= 1024 and index + 1 < len(units):
        number /= 1024.0
        index += 1
    return f"{int(number)} {units[index]}" if index == 0 else f"{number:.1f} {units[index]}"


def memory_fields(total_bytes: int) -> tuple[int, str, int]:
    """Return (memCapacity, memCapacityUnit, memCapacityInBytes)."""
    if total_bytes <= 0:
        raise UsageError("memory must be greater than zero")
    if total_bytes % (1024**3) == 0:
        return total_bytes // (1024**3), "GB", total_bytes
    if total_bytes % (1024**2) == 0:
        return total_bytes // (1024**2), "MB", total_bytes
    raise UsageError("memory must be a whole number of MiB (for example 4G or 4096M)")


def parse_bool(value: Any, *, field: str = "value") -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, int):
        return bool(value)
    text = str(value or "").strip().lower()
    if text in {"1", "true", "yes", "on", "y"}:
        return True
    if text in {"0", "false", "no", "off", "n"}:
        return False
    raise UsageError(f"{field} must be true or false, got {value!r}")


def validate_title(value: Any) -> str:
    title = str(value or "").strip()
    if not title:
        raise UsageError("VM name/title is required")
    if len(title) > TITLE_MAX:
        raise UsageError(f"VM name/title must be at most {TITLE_MAX} characters")
    if any(ord(char) < 32 or ord(char) == 127 for char in title):
        raise UsageError("VM name/title must not contain control characters")
    if title in {".", ".."}:
        raise UsageError("invalid VM name/title")
    return title


def validate_remote_path(value: Any, *, field: str = "path", extensions: Iterable[str] = ()) -> str:
    path = str(value or "").strip()
    if not path:
        raise UsageError(f"{field} is empty")
    if "\x00" in path or "\n" in path or "\r" in path:
        raise UsageError(f"{field} contains control characters")
    if "\\" in path:
        raise UsageError(f"{field} must use POSIX separators: {path!r}")
    if not path.startswith("/"):
        raise UsageError(f"{field} must be an absolute NAS path such as /vol1/1000/iso/x.iso")
    if not REMOTE_PATH_RE.match(path):
        raise UsageError(f"{field} must start with /vol<n>/: {path!r}")
    parts = path.split("/")
    if any(part in (".", "..") for part in parts):
        raise UsageError(f"{field} must not contain '.' or '..': {path!r}")
    if posixpath.normpath(path) != path.rstrip("/") and not (path.endswith("/") and posixpath.normpath(path) == path[:-1]):
        raise UsageError(f"{field} is not normalized: {path!r}")
    if extensions:
        lowered = path.lower()
        if not any(lowered.endswith(ext.lower()) for ext in extensions):
            raise UsageError(f"{field} must end with one of {', '.join(extensions)}: {path!r}")
    return path


def random_mac() -> str:
    # Locally administered, unicast, stable-looking OUI used by the fnOS UI.
    octets = [0x4E, 0xCD, 0xFF, secrets.randbelow(256), secrets.randbelow(256), secrets.randbelow(256)]
    return ":".join(f"{octet:02x}" for octet in octets)


def volume_maps(volumes: Mapping[str, Any]) -> tuple[dict[int, Mapping[str, Any]], dict[str, int]]:
    by_index: dict[int, Mapping[str, Any]] = {}
    aliases: dict[str, int] = {}
    for volume in volumes.get("vols") or []:
        if not isinstance(volume, dict):
            continue
        try:
            index = int(volume.get("index"))
        except (TypeError, ValueError):
            continue
        by_index[index] = volume
        aliases[str(index)] = index
        aliases[f"vol{index}"] = index
        aliases[f"/vol{index}"] = index
        if volume.get("sysname"):
            aliases[str(volume["sysname"])] = index
    return by_index, aliases


def resolve_pool(value: Any, aliases: Mapping[str, int]) -> int:
    if value is None or value == "":
        if not aliases:
            raise UsageError("no storage volumes are available")
        return sorted(set(aliases.values()))[0]
    text = str(value).strip()
    if text in aliases:
        return aliases[text]
    if text.lstrip("/").startswith("vol") and text.lstrip("/")[3:] in aliases:
        return aliases[text.lstrip("/")[3:]]
    raise UsageError(f"unknown storage volume {value!r}; choose one of {sorted(set(aliases.values()))}")


def parse_disk_spec(spec: str, aliases: Mapping[str, int]) -> dict[str, Any]:
    text = str(spec or "").strip()
    if not text:
        raise UsageError("disk specification is empty")
    pool = None
    if "@" in text:
        size_text, pool = text.split("@", 1)
    else:
        size_text = text
    size = parse_size(size_text, field="disk size")
    return {"capacityInBytes": size, "pool": resolve_pool(pool, aliases)}


def parse_network_spec(spec: str, ovs_names: Iterable[str]) -> dict[str, Any]:
    names = list(ovs_names)
    text = str(spec or "").strip()
    if not text:
        raise UsageError("network specification is empty")
    mac = None
    if "@" in text:
        text, mac = text.split("@", 1)
        mac = mac.strip()
    parts = text.split(":")
    if parts and parts[0] == "bridge":
        parts = parts[1:]
    if not parts or not parts[0]:
        raise UsageError("network source is required")
    source = parts[0]
    model = parts[1] if len(parts) > 1 and parts[1] else "e1000"
    if len(parts) > 2:
        raise UsageError("network syntax is SOURCE[:MODEL][@MAC]")
    if names and source not in names:
        raise UsageError(f"unknown virtual network {source!r}; available: {', '.join(names)}")
    if not names:
        raise UsageError("the host has no initialized OVS virtual network")
    if model not in NIC_MODELS:
        raise UsageError(f"unknown NIC model {model!r}; use one of {', '.join(sorted(NIC_MODELS))}")
    if mac is not None:
        mac = mac.strip()
        if not MAC_RE.match(mac):
            raise UsageError(f"invalid MAC address {mac!r}")
    return {
        "type": "bridge",
        "source": source,
        "nicModel": model,
        "macAddr": mac or random_mac(),
    }


def _normalize_network(entry: Any, ovs_names: Iterable[str]) -> dict[str, Any]:
    if isinstance(entry, str):
        return parse_network_spec(entry, ovs_names)
    if not isinstance(entry, dict):
        raise UsageError("network entries must be objects or strings")
    source = entry.get("source") or entry.get("name") or ""
    model = entry.get("nicModel") or entry.get("model") or "e1000"
    if source and not entry.get("type"):
        entry = dict(entry)
        entry["type"] = "bridge"
    source = entry.get("source") or source
    if not source:
        raise UsageError("network entry requires a source")
    names = list(ovs_names)
    if names and source not in names:
        raise UsageError(f"unknown virtual network {source!r}; available: {', '.join(names)}")
    if model not in NIC_MODELS:
        raise UsageError(f"unknown NIC model {model!r}")
    mac = entry.get("macAddr") or random_mac()
    if not MAC_RE.match(str(mac)):
        raise UsageError(f"invalid MAC address {mac!r}")
    return {"type": entry.get("type") or "bridge", "source": source, "nicModel": model, "macAddr": mac}


def _normalize_disk(entry: Any, aliases: Mapping[str, int], default_bus: str) -> dict[str, Any]:
    if isinstance(entry, str):
        disk = parse_disk_spec(entry, aliases)
        disk.update({"busType": default_bus, "actionType": "create"})
        return disk
    if not isinstance(entry, dict):
        raise UsageError("disk entries must be objects or strings")
    disk = dict(entry)
    if "capacityInBytes" not in disk:
        size = disk.pop("size", None)
        if size is None:
            raise UsageError("disk entry requires capacityInBytes or size")
        disk["capacityInBytes"] = parse_size(size, field="disk size")
    if "pool" not in disk:
        disk["pool"] = resolve_pool(None, aliases)
    else:
        disk["pool"] = resolve_pool(disk["pool"], aliases)
    if "bus" in disk and "busType" not in disk:
        disk["busType"] = disk.pop("bus")
    disk.setdefault("busType", default_bus)
    if disk["busType"] not in BUS_TYPES:
        raise UsageError(f"unknown disk bus type {disk['busType']!r}")
    action = disk.get("actionType")
    if action not in (None, "create", "import"):
        raise UsageError(f"unsupported disk actionType {action!r}")
    if action == "import" or disk.get("sourcePath"):
        disk.setdefault("actionType", "import")
        if not disk.get("sourcePath"):
            raise UsageError("import disk requires sourcePath")
        disk["sourcePath"] = validate_remote_path(
            disk["sourcePath"], field="disk sourcePath", extensions=(".qcow2", ".img", ".raw", ".vmdk", ".vhd", ".vhdx")
        )
    else:
        disk.setdefault("actionType", "create")
    return disk


def _normalize_file(raw: Mapping[str, Any], aliases: Mapping[str, int], ovs_names: Iterable[str]) -> dict[str, Any]:
    data = copy.deepcopy(dict(raw))
    if not data.get("title") and data.get("name"):
        data["title"] = data["name"]
    if data.get("name") and data.get("title"):
        data.pop("name", None)
    if "cpu" in data and "cpuCoreCnt" not in data:
        data["cpuCoreCnt"] = data.pop("cpu")
    if "memory" in data or "mem" in data:
        size = data.pop("memory", data.pop("mem", None))
        data["memCapacityInBytes"] = parse_size(size, field="memory")
    elif "memCapacityInBytes" not in data and "memCapacity" in data:
        unit = str(data.pop("memCapacityUnit", "GB")).upper()
        amount = float(data.pop("memCapacity"))
        multiplier = 1024**3 if unit.startswith("G") else 1024**2
        data["memCapacityInBytes"] = int(amount * multiplier)
    data.pop("memory", None)
    data.pop("mem", None)
    data.pop("memCapacityUnit", None)
    data.pop("memCapacity", None)

    cdroms = data.get("cdroms")
    if not isinstance(cdroms, dict):
        cdroms = {}
    if data.get("iso"):
        cdroms["osFilePath"] = data.pop("iso")
    if data.get("driverIso") or data.get("driver_iso"):
        cdroms["driverFilePath"] = data.pop("driverIso", data.pop("driver_iso", ""))
    if data.get("seed"):
        cdroms["driverFilePath"] = data.pop("seed")
    data["cdroms"] = cdroms

    image = data.pop("image", None)
    image_size = data.pop("imageSize", None) or data.pop("diskSize", None)
    default_bus = str(data.get("busType") or ("virtio" if data.get("osType") == "linux" else "sata"))
    disks: list[dict[str, Any]] = []
    raw_disks = data.get("disks") or []
    if isinstance(raw_disks, (str, dict)):
        raw_disks = [raw_disks]
    if not isinstance(raw_disks, list):
        raise UsageError("disks must be a list")
    for entry in raw_disks:
        disks.append(_normalize_disk(entry, aliases, default_bus))
    if image:
        source = validate_remote_path(image, field="image path", extensions=(".qcow2", ".img", ".raw"))
        capacity = parse_size(image_size, field="image disk size") if image_size else None
        if capacity is None:
            raise UsageError("image import requires image disk size (--disk-size)")
        disks.append(
            {
                "busType": default_bus,
                "pool": resolve_pool(data.get("pool"), aliases),
                "capacityInBytes": capacity,
                "actionType": "import",
                "sourcePath": source,
            }
        )
    data["disks"] = disks

    networks = data.get("networks")
    if networks is not None:
        if isinstance(networks, (str, dict)):
            networks = [networks]
        if not isinstance(networks, list):
            raise UsageError("networks must be a list")
        data["networks"] = [_normalize_network(entry, ovs_names) for entry in networks]
    elif data.get("network"):
        raw_network = data.pop("network")
        if isinstance(raw_network, (str, dict)):
            raw_network = [raw_network]
        if not isinstance(raw_network, list):
            raise UsageError("network must be a list")
        data["networks"] = [_normalize_network(entry, ovs_names) for entry in raw_network]
    else:
        data.pop("networks", None)
    return data


def build_create_payload(
    spec: Mapping[str, Any],
    *,
    hardware: Mapping[str, Any],
    volumes: Mapping[str, Any],
    ovs_list: list[dict[str, Any]],
    support: Mapping[str, Any],
    default_cpu: int = 1,
    default_mem: int = 1024**3,
) -> dict[str, Any]:
    by_index, aliases = volume_maps(volumes)
    if not by_index:
        raise UsageError("no storage volumes are available on the host")
    ovs_names = [str(item.get("name")) for item in ovs_list if item.get("name")]
    data = _normalize_file(spec, aliases, ovs_names)

    title = validate_title(data.get("title"))
    os_type = str(data.get("osType") or "linux")
    if os_type not in OS_TYPES:
        raise UsageError(f"osType must be one of {', '.join(sorted(OS_TYPES))}")
    os_version = str(data.get("osVersion") or ("other" if os_type != "linux" else "other"))
    hypervisor = str(data.get("hypervisor") or ("kvm" if support.get("kvm") else "qemu"))
    if hypervisor not in {"kvm", "qemu"}:
        raise UsageError("hypervisor must be kvm or qemu")
    chipset = str(data.get("chipset") or "q35")
    if chipset not in CHIPSETS:
        raise UsageError(f"chipset must be one of {', '.join(sorted(CHIPSETS))}")
    firmware = str(data.get("firmware") or "efi")
    if firmware not in FIRMWARES:
        raise UsageError(f"firmware must be one of {', '.join(sorted(FIRMWARES))}")
    video = str(data.get("videoType") or "vga")
    if video not in VIDEO_TYPES:
        raise UsageError(f"videoType must be one of {', '.join(sorted(VIDEO_TYPES))}")

    try:
        cpu = int(data.get("cpuCoreCnt") or default_cpu)
    except (TypeError, ValueError) as exc:
        raise UsageError("CPU count must be an integer") from exc
    if cpu < 1:
        raise UsageError("CPU count must be at least 1")

    mem_bytes = int(data.get("memCapacityInBytes") or default_mem)
    mem_capacity, mem_unit, mem_bytes = memory_fields(mem_bytes)

    default_bus = str(data.get("busType") or ("virtio" if os_type == "linux" else "sata"))
    disks: list[dict[str, Any]] = []
    for entry in data.get("disks") or []:
        disk = _normalize_disk(entry, aliases, default_bus)
        disks.append(disk)

    networks = data.get("networks")
    if isinstance(networks, list):
        networks = [_normalize_network(entry, ovs_names) for entry in networks]
    elif networks is None:
        if ovs_names:
            networks = [parse_network_spec(ovs_names[0], ovs_names)]
        else:
            networks = []

    cdroms = data.get("cdroms") if isinstance(data.get("cdroms"), dict) else {}
    os_iso = cdroms.get("osFilePath") or ""
    driver_iso = cdroms.get("driverFilePath") or ""
    if os_iso:
        os_iso = validate_remote_path(os_iso, field="OS ISO path", extensions=(".iso",))
    if driver_iso:
        driver_iso = validate_remote_path(driver_iso, field="driver/seed ISO path", extensions=(".iso",))
    if os_iso and driver_iso and os_iso == driver_iso:
        raise UsageError("OS ISO and driver/seed ISO must be different files")

    boot_type = str(data.get("bootType") or ("cdrom" if os_iso else "default"))
    if boot_type not in BOOT_TYPES and not re.match(r"^disk-\d+$", boot_type):
        raise UsageError("bootType must be default, cdrom or disk-<n>")
    boot_index = int(data.get("bootIndex") or 0)

    payload: dict[str, Any] = {
        "title": title,
        "osType": os_type,
        "osVersion": os_version,
        "hypervisor": hypervisor,
        "chipset": chipset,
        "firmware": firmware,
        "videoType": video,
        "cpuCoreCnt": cpu,
        "memCapacity": mem_capacity,
        "memCapacityUnit": mem_unit,
        "memCapacityInBytes": mem_bytes,
        "autostart": parse_bool(data.get("autostart", False), field="autostart"),
        "clipboard": parse_bool(data.get("clipboard", True), field="clipboard"),
        "desc": str(data.get("desc") or ""),
        "bootType": boot_type,
        "bootIndex": boot_index,
        "cdroms": {"osFilePath": os_iso, "driverFilePath": driver_iso},
        "disks": disks,
        "networks": networks,
        "hostpcis": [],
        "hostusbs": [],
    }
    for key in ("hostpcis", "hostusbs"):
        if data.get(key):
            raise UsageError(f"{key} pass-through is not supported by this skill")
    return payload


def preflight_create(
    payload: Mapping[str, Any],
    *,
    hardware: Mapping[str, Any],
    volumes: Mapping[str, Any],
    ovs_inited: bool,
    ovs_list: list[dict[str, Any]],
    support: Mapping[str, Any],
    vms: list[dict[str, Any]],
) -> dict[str, Any]:
    problems: list[str] = []
    facts: dict[str, Any] = {}
    title = str(payload.get("title") or "")
    if not title:
        problems.append("title is required")

    cpu_limit = int(((hardware.get("cpu") or {}).get("thread") or 0))
    cpu = int(payload.get("cpuCoreCnt") or 0)
    facts["cpuLimit"] = cpu_limit
    if cpu_limit and cpu > cpu_limit:
        problems.append(f"CPU count {cpu} exceeds host threads {cpu_limit}")

    mem_total = int(((hardware.get("mem") or {}).get("total") or 0))
    mem_bytes = int(payload.get("memCapacityInBytes") or 0)
    allocated = 0
    for vm in vms:
        try:
            allocated += int(vm.get("memCapacityInBytes") or 0)
        except (TypeError, ValueError):
            pass
    facts.update({"memTotalBytes": mem_total, "memAllocatedBytes": allocated, "memFreeBytes": max(0, mem_total - allocated)})
    if mem_total and mem_bytes > mem_total:
        problems.append(f"memory {format_size(mem_bytes)} exceeds host total {format_size(mem_total)}")
    elif mem_total and mem_bytes > mem_total - allocated:
        problems.append(
            f"memory {format_size(mem_bytes)} exceeds free memory "
            f"{format_size(max(0, mem_total - allocated))} (host total minus running/defined VMs)"
        )

    if payload.get("hypervisor") == "kvm" and not support.get("kvm"):
        problems.append("KVM is not available on this host (use --hypervisor qemu)")

    _, aliases = volume_maps(volumes)
    by_index, _ = volume_maps(volumes)
    requested: dict[int, int] = {}
    for disk in payload.get("disks") or []:
        try:
            pool = int(disk.get("pool"))
            capacity = int(disk.get("capacityInBytes"))
        except (TypeError, ValueError):
            problems.append("disk entry is missing a valid pool or capacity")
            continue
        requested[pool] = requested.get(pool, 0) + capacity
    for pool, wanted in requested.items():
        volume = by_index.get(pool)
        if not volume:
            problems.append(f"storage volume {pool} does not exist")
            continue
        free = int(volume.get("size") or 0) - int(volume.get("used") or 0)
        facts[f"vol{pool}FreeBytes"] = free
        if wanted > free:
            problems.append(
                f"disks on vol{pool} need {format_size(wanted)}, only {format_size(free)} free"
            )

    networks = payload.get("networks") or []
    if networks and not ovs_inited:
        problems.append("OVS is not initialized on the host")
    if networks and not ovs_list:
        problems.append("no OVS virtual networks are available")
    ovs_names = {str(item.get("name")) for item in ovs_list}
    for network in networks:
        if network.get("source") not in ovs_names:
            problems.append(f"virtual network {network.get('source')!r} does not exist")

    cdroms = payload.get("cdroms") or {}
    if payload.get("bootType") == "cdrom" and not cdroms.get("osFilePath"):
        problems.append("bootType cdrom requires an OS ISO")
    if cdroms.get("osFilePath"):
        validate_remote_path(cdroms["osFilePath"], field="OS ISO path", extensions=(".iso",))
    if cdroms.get("driverFilePath"):
        validate_remote_path(cdroms["driverFilePath"], field="driver/seed ISO path", extensions=(".iso",))
    if not payload.get("disks") and not cdroms.get("osFilePath"):
        # Empty shells are legal and useful for lifecycle tests; do not reject.
        facts["emptyShell"] = True
    if problems:
        raise PreflightError(problems)
    return facts


def build_modify_payload(info: Mapping[str, Any], changes: Mapping[str, Any]) -> dict[str, Any]:
    payload = copy.deepcopy(dict(info))
    payload.pop("memCapacityUnit", None)
    payload.pop("memCapacity", None)
    if "cpu" in changes and changes["cpu"] is not None:
        try:
            payload["cpuCoreCnt"] = int(changes["cpu"])
        except (TypeError, ValueError) as exc:
            raise UsageError("CPU count must be an integer") from exc
    if "mem" in changes and changes["mem"] is not None:
        payload["memCapacityInBytes"] = parse_size(changes["mem"], field="memory")
    if "desc" in changes and changes["desc"] is not None:
        payload["desc"] = str(changes["desc"])
    if "autostart" in changes and changes["autostart"] is not None:
        payload["autostart"] = parse_bool(changes["autostart"], field="autostart")
    if "clipboard" in changes and changes["clipboard"] is not None:
        payload["clipboard"] = parse_bool(changes["clipboard"], field="clipboard")
    if "title" in changes and changes["title"] is not None:
        payload["title"] = validate_title(changes["title"])
    if "bootType" in changes and changes["bootType"] is not None:
        payload["bootType"] = str(changes["bootType"])
    if "bootIndex" in changes and changes["bootIndex"] is not None:
        payload["bootIndex"] = int(changes["bootIndex"])
    if "file" in changes and changes["file"]:
        file_data = changes["file"]
        if isinstance(file_data, Mapping):
            payload.update(copy.deepcopy(dict(file_data)))
    if not payload.get("name") or not payload.get("uuid"):
        raise UsageError("modify requires the current VM name and uuid")
    return payload


# --------------------------------------------------------------------- images
def read_qcow2_size(path: str) -> int:
    try:
        with open(path, "rb") as handle:
            header = handle.read(32)
    except OSError as exc:
        raise UsageError(f"cannot read image {path}: {exc}") from exc
    if len(header) < 32 or header[:4] != b"QFI\xfb":
        raise UsageError(f"{path} is not a qcow2 image")
    version, backing_offset = struct.unpack_from(">IQ", header, 4)
    if version not in (2, 3):
        raise UsageError(f"{path}: unsupported qcow2 version {version}")
    if backing_offset:
        raise UsageError(f"{path}: qcow2 images with a backing file are not supported")
    size = struct.unpack_from(">Q", header, 24)[0]
    if size <= 0:
        raise UsageError(f"{path}: invalid qcow2 virtual size")
    return size


def patch_qcow2_size(path: str, new_size: int) -> tuple[int, int]:
    old_size = read_qcow2_size(path)
    if new_size < old_size:
        raise UsageError(
            f"cannot shrink {path} from {format_size(old_size)} to {format_size(new_size)}"
        )
    if new_size == old_size:
        return old_size, old_size
    with open(path, "r+b") as handle:
        handle.seek(24)
        handle.write(struct.pack(">Q", new_size))
        handle.flush()
        os.fsync(handle.fileno())
    return old_size, new_size
