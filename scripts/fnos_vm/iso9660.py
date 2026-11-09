"""A tiny ISO 9660 + Joliet writer for cloud-init NoCloud seed images.

Cloud-init's NoCloud datasource looks for a filesystem labelled ``CIDATA``
containing ``user-data`` and ``meta-data``.  A seed ISO is the most portable
way to deliver those files to a VM, and building one here avoids a dependency
on genisoimage/mkisofs on Windows.
"""

from __future__ import annotations

import datetime as _dt
import os
import struct
from pathlib import Path
from typing import Mapping


SECTOR = 2048
VOLUME_ID = "CIDATA"


def _both16(value: int) -> bytes:
    return struct.pack("<H", value) + struct.pack(">H", value)


def _both32(value: int) -> bytes:
    return struct.pack("<I", value) + struct.pack(">I", value)


def _date7(when: _dt.datetime) -> bytes:
    return bytes(
        [
            max(0, min(255, when.year - 1900)),
            when.month,
            when.day,
            when.hour,
            when.minute,
            when.second,
            0,
        ]
    )


def _pvd_date(when: _dt.datetime) -> bytes:
    text = when.strftime("%Y%m%d%H%M%S") + "00"
    return text.encode("ascii") + b"\x00"


def _pad(text: str, length: int) -> bytes:
    raw = text.encode("ascii", errors="replace")[:length]
    return raw + b" " * (length - len(raw))


def _dir_record(
    extent: int,
    size: int,
    *,
    is_dir: bool,
    name: bytes,
    when: _dt.datetime,
) -> bytes:
    length = 33 + len(name)
    if len(name) % 2 == 0:
        length += 1
    record = bytearray(length)
    record[0] = length
    record[1] = 0
    record[2:10] = _both32(extent)
    record[10:18] = _both32(size)
    record[18:25] = _date7(when)
    record[25] = 0x02 if is_dir else 0x00
    record[26] = 0
    record[27] = 0
    record[28:32] = _both16(1)
    record[32] = len(name)
    record[33 : 33 + len(name)] = name
    return bytes(record)


def _primary_name(name: str, *, is_dir: bool) -> bytes:
    # Level 2 identifiers allow up to 31 characters; upper-case is what Linux
    # isofs maps back to lower-case.  Files require a ;1 version suffix.
    text = name.upper()
    if not is_dir:
        text += ";1"
    return text.encode("ascii")


def _joliet_name(name: str) -> bytes:
    return name.encode("utf-16-be")


def _directory_data(
    entries: list[tuple[str, int, int, bool]],
    *,
    self_extent: int,
    parent_extent: int,
    joliet: bool,
    when: _dt.datetime,
) -> bytes:
    records: list[bytes] = []
    dot_name = b"\x00"
    dotdot_name = b"\x01"
    records.append(
        _dir_record(self_extent, SECTOR, is_dir=True, name=dot_name, when=when)
    )
    records.append(
        _dir_record(parent_extent, SECTOR, is_dir=True, name=dotdot_name, when=when)
    )
    for name, extent, size, is_dir in sorted(entries, key=lambda item: item[0]):
        encoded = _joliet_name(name) if joliet else _primary_name(name, is_dir=is_dir)
        records.append(
            _dir_record(extent, size, is_dir=is_dir, name=encoded, when=when)
        )
    data = b"".join(records)
    if len(data) > SECTOR:
        raise ValueError("ISO root directory does not fit in one sector")
    return data + b"\x00" * (SECTOR - len(data))


def _path_table(root_extent: int, *, big_endian: bool) -> bytes:
    if big_endian:
        record = b"\x01\x00" + struct.pack(">I", root_extent) + struct.pack(">H", 1) + b"\x00\x00"
    else:
        record = b"\x01\x00" + struct.pack("<I", root_extent) + struct.pack("<H", 1) + b"\x00\x00"
    return record + b"\x00" * (SECTOR - len(record))


def _volume_descriptor(
    *,
    descriptor_type: int,
    volume_id: str,
    total_sectors: int,
    root_extent: int,
    root_size: int,
    path_table_size: int,
    l_path_lba: int,
    m_path_lba: int,
    joliet: bool,
    when: _dt.datetime,
) -> bytes:
    block = bytearray(SECTOR)
    block[0] = descriptor_type
    block[1:6] = b"CD001"
    block[6] = 1
    block[8:40] = _pad("", 32)
    if joliet:
        # The Joliet SVD volume identifier is UCS-2BE; writing ASCII here makes
        # blkid/udev report a garbled label and breaks NoCloud label discovery.
        encoded_id = volume_id.upper().encode("utf-16-be")[:32]
        block[40:72] = encoded_id + b"\x00" * (32 - len(encoded_id))
    else:
        block[40:72] = _pad(volume_id.upper(), 32)
    block[80:88] = _both32(total_sectors)
    block[120:124] = _both16(1)
    block[124:128] = _both16(1)
    block[128:132] = _both16(SECTOR)
    block[132:140] = _both32(path_table_size)
    block[140:144] = struct.pack("<I", l_path_lba)
    block[144:148] = struct.pack("<I", 0)
    block[148:152] = struct.pack(">I", m_path_lba)
    block[152:156] = struct.pack(">I", 0)
    block[156:190] = _dir_record(
        root_extent, root_size, is_dir=True, name=b"\x00", when=when
    )
    block[190:318] = _pad("", 128)
    block[318:446] = _pad("FNOS-VM", 128)
    block[446:574] = _pad("FNOS-VM", 128)
    block[574:702] = _pad("FNOS-VM CLOUD-INIT", 128)
    block[702:739] = _pad("", 37)
    block[739:776] = _pad("", 37)
    block[776:813] = _pad("", 37)
    stamp = _pvd_date(when)
    block[813:830] = stamp
    block[830:847] = stamp
    block[847:864] = _pad("", 17)
    block[864:881] = _pad("", 17)
    block[881] = 1
    if joliet:
        block[88:120] = _pad("%/E", 32)
    return bytes(block)


def _terminator() -> bytes:
    block = bytearray(SECTOR)
    block[0] = 255
    block[1:6] = b"CD001"
    block[6] = 1
    return bytes(block)


def build_iso(
    output: str | os.PathLike[str],
    files: Mapping[str, bytes | str],
    *,
    volume_id: str = VOLUME_ID,
    when: _dt.datetime | None = None,
) -> Path:
    when = when or _dt.datetime.now(_dt.timezone.utc).replace(tzinfo=None)
    normalized: dict[str, bytes] = {}
    for name, value in files.items():
        clean = str(name)
        if not clean or "/" in clean or "\\" in clean or clean in {".", ".."}:
            raise ValueError(f"invalid ISO file name: {name!r}")
        if any(ord(char) < 32 for char in clean):
            raise ValueError(f"invalid ISO file name: {name!r}")
        normalized[clean] = value.encode("utf-8") if isinstance(value, str) else bytes(value)
    if not normalized:
        raise ValueError("ISO must contain at least one file")

    # Fixed layout: descriptors 16-18, path tables 19-22, root dirs 23-24.
    primary_root_lba = 23
    joliet_root_lba = 24
    first_data_lba = 25
    extents: dict[str, tuple[int, int]] = {}
    cursor = first_data_lba
    for name in sorted(normalized):
        data = normalized[name]
        extents[name] = (cursor, len(data))
        cursor += max(1, (len(data) + SECTOR - 1) // SECTOR)
    total_sectors = cursor

    entries = [
        (name, extents[name][0], extents[name][1], False) for name in sorted(normalized)
    ]
    primary_dir = _directory_data(
        entries,
        self_extent=primary_root_lba,
        parent_extent=primary_root_lba,
        joliet=False,
        when=when,
    )
    joliet_dir = _directory_data(
        entries,
        self_extent=joliet_root_lba,
        parent_extent=joliet_root_lba,
        joliet=True,
        when=when,
    )

    primary_path = _path_table(primary_root_lba, big_endian=False)
    primary_path_m = _path_table(primary_root_lba, big_endian=True)
    joliet_path = _path_table(joliet_root_lba, big_endian=False)
    joliet_path_m = _path_table(joliet_root_lba, big_endian=True)

    pvd = _volume_descriptor(
        descriptor_type=1,
        volume_id=volume_id,
        total_sectors=total_sectors,
        root_extent=primary_root_lba,
        root_size=SECTOR,
        path_table_size=len(primary_path),
        l_path_lba=19,
        m_path_lba=20,
        joliet=False,
        when=when,
    )
    svd = _volume_descriptor(
        descriptor_type=2,
        volume_id=volume_id,
        total_sectors=total_sectors,
        root_extent=joliet_root_lba,
        root_size=SECTOR,
        path_table_size=len(joliet_path),
        l_path_lba=21,
        m_path_lba=22,
        joliet=True,
        when=when,
    )

    image = bytearray(total_sectors * SECTOR)
    image[16 * SECTOR : 17 * SECTOR] = pvd
    image[17 * SECTOR : 18 * SECTOR] = svd
    image[18 * SECTOR : 19 * SECTOR] = _terminator()
    image[19 * SECTOR : 20 * SECTOR] = primary_path
    image[20 * SECTOR : 21 * SECTOR] = primary_path_m
    image[21 * SECTOR : 22 * SECTOR] = joliet_path
    image[22 * SECTOR : 23 * SECTOR] = joliet_path_m
    image[23 * SECTOR : 24 * SECTOR] = primary_dir
    image[24 * SECTOR : 25 * SECTOR] = joliet_dir
    for name, data in normalized.items():
        lba, _length = extents[name]
        image[lba * SECTOR : lba * SECTOR + len(data)] = data

    output_path = Path(output)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    temp = output_path.with_name(output_path.name + ".part")
    temp.write_bytes(bytes(image))
    os.replace(temp, output_path)
    return output_path


def read_iso_files(path: str | os.PathLike[str]) -> dict[str, bytes]:
    """Read files back from a generated ISO (used by tests and diagnostics)."""
    raw = Path(path).read_bytes()
    if len(raw) < 18 * SECTOR:
        raise ValueError("not an ISO image")
    svd = raw[17 * SECTOR : 18 * SECTOR]
    pvd = raw[16 * SECTOR : 17 * SECTOR]
    if pvd[1:6] != b"CD001":
        raise ValueError("missing ISO 9660 primary volume descriptor")

    def read_dir(descriptor: bytes, *, joliet: bool) -> dict[str, bytes]:
        record = descriptor[156:190]
        extent = struct.unpack_from("<I", record, 2)[0]
        size = struct.unpack_from("<I", record, 10)[0]
        data = raw[extent * SECTOR : extent * SECTOR + size]
        result: dict[str, bytes] = {}
        offset = 0
        while offset < len(data):
            length = data[offset]
            if length == 0:
                offset = ((offset // SECTOR) + 1) * SECTOR
                continue
            name_len = data[offset + 32]
            name_raw = data[offset + 33 : offset + 33 + name_len]
            file_extent = struct.unpack_from("<I", data, offset + 2)[0]
            file_size = struct.unpack_from("<I", data, offset + 10)[0]
            flags = data[offset + 25]
            offset += length
            if name_len == 1 and name_raw in (b"\x00", b"\x01"):
                continue
            if flags & 0x02:
                continue
            if joliet:
                name = name_raw.decode("utf-16-be", errors="replace")
            else:
                if name_raw.endswith(b";1"):
                    name_raw = name_raw[:-2]
                name = name_raw.decode("ascii", errors="replace").lower()
            result[name] = raw[file_extent * SECTOR : file_extent * SECTOR + file_size]
        return result

    # Prefer Joliet names when present.
    if svd[0] == 2 and svd[1:6] == b"CD001":
        return read_dir(svd, joliet=True)
    return read_dir(pvd, joliet=False)
