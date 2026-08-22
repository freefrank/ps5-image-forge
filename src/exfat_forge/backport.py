"""Safe PS5 ELF SDK inspection and downgrade support.

The file-layout constants are public SCE ELF format facts documented by the
PS5 homebrew SDK and idlesauce's SDK downgrade research.  This implementation
is deliberately small and independent: it patches only unsigned ELF files,
validates every offset before writing, and replaces files atomically.
"""

from __future__ import annotations

import os
import io
import shutil
import struct
import tempfile
import re
import zipfile
from contextlib import redirect_stdout
from dataclasses import asdict, dataclass, replace
from pathlib import Path

from ._vendor import make_fself


class BackportError(RuntimeError):
    pass


ELF_MAGIC = b"\x7fELF"
SIGNED_MAGICS = {b"\x4f\x15\x3d\x1d", b"\x54\x14\xf5\xee"}
EXECUTABLE_SUFFIXES = {".bin", ".elf", ".self", ".prx", ".sprx"}
BACKUP_NAME_RE = re.compile(
    r"^(?P<target>.+)\.bak(?:\.(?P<sequence>\d+))?(?P<archive>\.zip)?$",
                            re.IGNORECASE)
PT_SCE_PROCPARAM = 0x61000001
PT_SCE_MODULE_PARAM = 0x61000002
SCE_PROCESS_PARAM_MAGIC = 0x4942524F
SCE_MODULE_PARAM_MAGIC = 0x3C13F4BF
SELF_COMMON_SIZE = struct.calcsize("<4s4B")
SELF_EXT_SIZE = struct.calcsize("<I2HQ2H4x")
SELF_ENTRY_SIZE = struct.calcsize("<4Q")
PT_SCE_VERSION = 0x6FFFFF01
LIBC_PATCH_PATTERN = b"4h6F1LLbTiw#A#B"
LIBC_PATCH_REPLACEMENT = b"IWIBBdTHit4#A#B"

# Target SDK band -> (PS5 SDK value, paired PS4 SDK value).
SDK_VERSION_PAIRS: dict[int, tuple[int, int]] = {
    1: (0x01000050, 0x07590001),
    2: (0x02000009, 0x08050001),
    3: (0x03000027, 0x08540001),
    4: (0x04000031, 0x09040001),
    5: (0x05000033, 0x09590001),
    6: (0x06000038, 0x10090001),
    7: (0x07000038, 0x10590001),
    8: (0x08000041, 0x11090001),
    9: (0x09000040, 0x11590001),
    10: (0x10000040, 0x12090001),
}


def recommended_target(firmware: str | None) -> int | None:
    """Map a user-selected PS5 firmware to the nearest supported SDK band."""
    match = re.match(r"^\s*(\d+)", firmware or "")
    if not match:
        return None
    major = int(match.group(1))
    if major < min(SDK_VERSION_PAIRS):
        return None
    return max(min(major, max(SDK_VERSION_PAIRS)), min(SDK_VERSION_PAIRS))


@dataclass(frozen=True)
class ElfSdkInfo:
    path: str
    status: str                 # patchable | signed | no-param | invalid
    ps5_sdk: int | None = None
    ps4_sdk: int | None = None
    sdk_band: int | None = None
    detail: str = ""
    ps5_offset: int | None = None
    ps4_offset: int | None = None
    backup_path: str = ""

    def public_dict(self) -> dict:
        result = asdict(self)
        result.pop("ps5_offset")
        result.pop("ps4_offset")
        if self.ps5_sdk is not None:
            result["ps5_sdk_hex"] = f"0x{self.ps5_sdk:08X}"
        if self.ps4_sdk is not None:
            result["ps4_sdk_hex"] = f"0x{self.ps4_sdk:08X}"
        return result


def _read_exact(fh, offset: int, size: int, file_size: int) -> bytes:
    if offset < 0 or size < 0 or offset + size > file_size:
        raise BackportError("ELF offset is outside the file")
    fh.seek(offset)
    value = fh.read(size)
    if len(value) != size:
        raise BackportError("truncated ELF structure")
    return value


def _u16(fh, offset: int, file_size: int) -> int:
    return struct.unpack("<H", _read_exact(fh, offset, 2, file_size))[0]


def _u32(fh, offset: int, file_size: int) -> int:
    return struct.unpack("<I", _read_exact(fh, offset, 4, file_size))[0]


def _u64(fh, offset: int, file_size: int) -> int:
    return struct.unpack("<Q", _read_exact(fh, offset, 8, file_size))[0]


def _sdk_band(raw: int) -> int | None:
    for band, (ps5, _ps4) in SDK_VERSION_PAIRS.items():
        if raw == ps5:
            return band
    major = (raw >> 24) & 0xFF
    return major if 1 <= major <= 99 else None


def _align_up(value: int, alignment: int) -> int:
    return (value + alignment - 1) & ~(alignment - 1)


def _extract_fself(source: Path, output: Path) -> None:
    """Extract a plaintext, uncompressed SELF/FSELF container to ELF.

    Dumped files can retain encrypted/compressed flag bits even when their
    mounted contents are already plaintext.  We therefore reconstruct first
    and accept the result only when its ELF and SDK structures verify.
    """
    source = Path(source)
    file_size = source.stat().st_size
    with source.open("rb") as fh:
        magic = _read_exact(fh, 0, 4, file_size)
        if magic not in SIGNED_MAGICS:
            raise BackportError("not a SELF/FSELF container")
        ext = _read_exact(fh, SELF_COMMON_SIZE, SELF_EXT_SIZE, file_size)
        _key_type, _header_size, _meta_size, declared_size, num_entries, _flags = \
            struct.unpack("<I2HQ2H4x", ext)
        if num_entries > 4096:
            raise BackportError("invalid SELF entry count")
        entries_end = SELF_COMMON_SIZE + SELF_EXT_SIZE + num_entries * SELF_ENTRY_SIZE
        if entries_end > file_size:
            raise BackportError("SELF entry table is outside the file")
        entries = []
        for index in range(num_entries):
            raw = _read_exact(fh, SELF_COMMON_SIZE + SELF_EXT_SIZE +
                              index * SELF_ENTRY_SIZE, SELF_ENTRY_SIZE, file_size)
            props, offset, encoded_size, decoded_size = struct.unpack("<4Q", raw)
            if offset + encoded_size > file_size:
                raise BackportError("SELF segment is outside the file")
            if props & (1 << 11):
                entries.append((props >> 20 & 0xFFFF, offset,
                                encoded_size, decoded_size))

        elf_base = _align_up(entries_end, 16)
        ident = _read_exact(fh, elf_base, 0x40, file_size)
        if ident[:4] != ELF_MAGIC or ident[4:6] != b"\x02\x01":
            raise BackportError("SELF does not contain a supported ELF64 header")
        phoff = struct.unpack_from("<Q", ident, 0x20)[0]
        phentsize = struct.unpack_from("<H", ident, 0x36)[0]
        phnum = struct.unpack_from("<H", ident, 0x38)[0]
        if phentsize < 0x38 or phnum > 4096:
            raise BackportError("invalid embedded ELF program-header table")
        headers_size = max(struct.unpack_from("<H", ident, 0x34)[0],
                           phoff + phentsize * phnum)
        headers = _read_exact(fh, elf_base, headers_size, file_size)
        program_headers = []
        output_size = headers_size
        for index in range(phnum):
            pos = phoff + index * phentsize
            p_type = struct.unpack_from("<I", headers, pos)[0]
            p_offset = struct.unpack_from("<Q", headers, pos + 0x08)[0]
            p_filesz = struct.unpack_from("<Q", headers, pos + 0x20)[0]
            if p_offset + p_filesz > (1 << 36):
                raise BackportError("unreasonable embedded ELF segment range")
            output_size = max(output_size, p_offset + p_filesz)
            program_headers.append((p_type, p_offset, p_filesz))

        entry_map = {index: (offset, size) for index, offset, size, _ in entries}
        output.parent.mkdir(parents=True, exist_ok=True)
        with output.open("w+b") as out:
            out.truncate(output_size)
            out.seek(0)
            out.write(headers)
            for index, (p_type, p_offset, p_filesz) in enumerate(program_headers):
                if not p_filesz:
                    continue
                if index in entry_map:
                    seg_offset, seg_size = entry_map[index]
                    if seg_size < p_filesz:
                        raise BackportError("SELF segment is shorter than its ELF segment")
                    data = _read_exact(fh, seg_offset, p_filesz, file_size)
                elif p_type == PT_SCE_VERSION and p_filesz <= file_size:
                    # make_fself appends this segment after its declared SELF size.
                    version_end = min(file_size, declared_size + p_filesz)
                    data = _read_exact(fh, version_end - p_filesz,
                                       p_filesz, file_size)
                else:
                    continue
                out.seek(p_offset)
                out.write(data)
            out.flush()
            os.fsync(out.fileno())


def _fake_sign(source: Path, output: Path) -> None:
    """Create a fake-signed SELF using the GPL PS5 payload SDK helper."""
    try:
        with Path(source).open("rb") as fh:
            elf = make_fself.ElfFile(ignore_shdrs=True)
            elf.load(fh)
        with Path(output).open("wb") as fh, redirect_stdout(io.StringIO()):
            signed = make_fself.SignedElfFile(
                elf, paid=0x3100000000000002, ptype=1,
                app_version=0, fw_version=0, auth_info=None)
            signed.save(fh)
            fh.flush()
            os.fsync(fh.fileno())
    except Exception as exc:
        Path(output).unlink(missing_ok=True)
        raise BackportError(f"fake signing failed: {exc}") from exc


def _inspect_signed(path: Path) -> ElfSdkInfo:
    with tempfile.TemporaryDirectory(prefix="exfat_forge_fself_") as tmp:
        elf_path = Path(tmp) / "unsigned.elf"
        try:
            _extract_fself(path, elf_path)
        except (OSError, struct.error, BackportError) as exc:
            return ElfSdkInfo(str(path), "invalid",
                              detail=f"SELF/FSELF cannot be reconstructed: {exc}")
        embedded = inspect_file(elf_path)
        detail = "SELF/FSELF · automatic extract, patch and fake-sign available"
        if embedded.status != "patchable":
            return ElfSdkInfo(str(path), embedded.status,
                              detail=embedded.detail or
                              "SELF/FSELF has no SDK parameter")
        return ElfSdkInfo(str(path), "signed", embedded.ps5_sdk,
                          embedded.ps4_sdk, embedded.sdk_band, detail)


def inspect_file(path: Path) -> ElfSdkInfo:
    """Locate the SCE parameter SDK fields without modifying ``path``."""
    path = Path(path)
    try:
        file_size = path.stat().st_size
        with path.open("rb") as fh:
            magic = _read_exact(fh, 0, 4, file_size)
            if magic in SIGNED_MAGICS:
                return _inspect_signed(path)
            if magic != ELF_MAGIC:
                return ElfSdkInfo(str(path), "invalid", detail="not an ELF/SELF file")
            if file_size < 0x40:
                raise BackportError("truncated ELF header")

            phoff = _u64(fh, 0x20, file_size)
            phentsize = _u16(fh, 0x36, file_size)
            phnum = _u16(fh, 0x38, file_size)
            if phentsize < 0x28 or phnum > 4096:
                raise BackportError("invalid ELF program-header table")
            if phoff + phentsize * phnum > file_size:
                raise BackportError("program-header table is outside the file")

            for index in range(phnum):
                header = phoff + index * phentsize
                kind = _u32(fh, header, file_size)
                if kind not in (PT_SCE_PROCPARAM, PT_SCE_MODULE_PARAM):
                    continue
                param = _u64(fh, header + 0x08, file_size)
                param_size = _u32(fh, param, file_size)
                if not param_size and kind == PT_SCE_MODULE_PARAM:
                    return ElfSdkInfo(str(path), "no-param",
                                      detail="module has no parameter block")
                if param_size < 0x18:
                    raise BackportError("SCE parameter block is too small")
                expected = (SCE_PROCESS_PARAM_MAGIC if kind == PT_SCE_PROCPARAM
                            else SCE_MODULE_PARAM_MAGIC)
                if _u32(fh, param + 0x08, file_size) != expected:
                    raise BackportError("invalid SCE parameter magic")
                ps4 = _u32(fh, param + 0x10, file_size)
                ps5 = _u32(fh, param + 0x14, file_size)
                return ElfSdkInfo(
                    str(path), "patchable", ps5, ps4, _sdk_band(ps5),
                    ps5_offset=param + 0x14, ps4_offset=param + 0x10)
    except (OSError, struct.error, BackportError) as exc:
        return ElfSdkInfo(str(path), "invalid", detail=str(exc))
    return ElfSdkInfo(str(path), "no-param", detail="no SCE parameter segment")


def candidates(folder: Path) -> list[Path]:
    folder = Path(folder)
    if not folder.is_dir():
        raise BackportError("source folder does not exist")
    return sorted(
        (p for p in folder.rglob("*")
         if p.is_file() and p.suffix.lower() in EXECUTABLE_SUFFIXES
         and not p.name.lower().endswith(".bak")),
        key=lambda p: str(p).lower(),
    )


def scan(folder: Path) -> list[ElfSdkInfo]:
    return [inspect_file(path) for path in candidates(folder)]


def _same_bytes(left: Path, right: Path) -> bool:
    if left.stat().st_size != right.stat().st_size:
        return False
    with left.open("rb") as left_fh, right.open("rb") as right_fh:
        while True:
            left_chunk = left_fh.read(1024 * 1024)
            right_chunk = right_fh.read(1024 * 1024)
            if left_chunk != right_chunk:
                return False
            if not left_chunk:
                return True


def _zip_member(archive: Path, target_name: str) -> zipfile.ZipInfo:
    """Return the sole safe member expected in a per-file backup archive."""
    try:
        with zipfile.ZipFile(archive, "r") as bundle:
            members = [item for item in bundle.infolist() if not item.is_dir()]
            if len(members) != 1 or members[0].filename != target_name:
                raise BackportError("backup ZIP must contain only the target file")
            return members[0]
    except (OSError, zipfile.BadZipFile) as exc:
        raise BackportError(f"invalid backup ZIP: {exc}") from exc


def _backup_matches(source: Path, backup_path: Path, *,
                    target_name: str | None = None) -> bool:
    """Compare a file with a legacy plain backup or a ZIP backup member."""
    if backup_path.suffix.lower() != ".zip" and not zipfile.is_zipfile(backup_path):
        return _same_bytes(source, backup_path)
    expected = target_name or source.name
    info = _zip_member(backup_path, expected)
    if source.stat().st_size != info.file_size:
        return False
    try:
        with source.open("rb") as left, zipfile.ZipFile(backup_path, "r") as bundle, \
                bundle.open(info, "r") as right:
            while True:
                left_chunk = left.read(1024 * 1024)
                right_chunk = right.read(1024 * 1024)
                if left_chunk != right_chunk:
                    return False
                if not left_chunk:
                    return True
    except (OSError, zipfile.BadZipFile, RuntimeError) as exc:
        raise BackportError(f"cannot verify backup ZIP: {exc}") from exc


def _backup_original(path: Path) -> Path:
    """Create and verify a side-by-side ZIP without replacing an older one."""
    sequence = 0
    while True:
        suffix = ".bak" if sequence == 0 else f".bak.{sequence}"
        legacy_path = path.with_name(path.name + suffix)
        archive_path = path.with_name(path.name + suffix + ".zip")
        occupied = [candidate for candidate in (legacy_path, archive_path)
                    if candidate.exists()]
        # Old uncompressed backups participate in the same generation chain.
        # Reuse a byte-identical generation, otherwise allocate the next one.
        for candidate in occupied:
            if _backup_matches(path, candidate):
                return candidate
        if not occupied:
            backup_path = archive_path
            break
        sequence += 1

    fd, temp_name = tempfile.mkstemp(
        prefix=backup_path.name + ".", suffix=".tmp", dir=str(path.parent))
    os.close(fd)
    temp_path = Path(temp_name)
    try:
        with zipfile.ZipFile(temp_path, "w", compression=zipfile.ZIP_DEFLATED,
                             compresslevel=6, allowZip64=True) as bundle:
            bundle.write(path, arcname=path.name)
        with temp_path.open("rb+") as fh:
            fh.flush()
            os.fsync(fh.fileno())
        with zipfile.ZipFile(temp_path, "r") as bundle:
            if bundle.testzip() is not None:
                raise BackportError("backup ZIP CRC verification failed")
        if not _backup_matches(path, temp_path):
            raise BackportError("verification of original-file backup failed")
        os.replace(temp_path, backup_path)
        if not _backup_matches(path, backup_path):
            raise BackportError("verification of installed backup failed")
    finally:
        temp_path.unlink(missing_ok=True)
    return backup_path


def backup_inventory(folder: Path) -> list[dict]:
    """Return the earliest original backup for every Backport target.

    ``file.bak`` is the pre-first-patch original. Numbered generations are
    intentionally ignored while that file exists; they hold later states and
    are useful for undoing a restore, but are not the game's original file.
    """
    folder = Path(folder)
    if not folder.is_dir():
        raise BackportError("source folder does not exist")
    grouped: dict[Path, list[tuple[int, Path]]] = {}
    for backup_path in folder.rglob("*"):
        if not backup_path.is_file():
            continue
        match = BACKUP_NAME_RE.match(backup_path.name)
        if not match:
            continue
        target = backup_path.with_name(match.group("target"))
        if target.suffix.lower() not in EXECUTABLE_SUFFIXES:
            continue
        sequence = int(match.group("sequence") or 0)
        grouped.setdefault(target, []).append((sequence, backup_path))

    inventory: list[dict] = []
    for target, choices in sorted(grouped.items(), key=lambda pair: str(pair[0]).lower()):
        sequence, backup_path = min(
            choices, key=lambda choice: (choice[0],
                                         choice[1].suffix.lower() == ".zip"))
        if backup_path.suffix.lower() == ".zip":
            try:
                _zip_member(backup_path, target.name)
            except BackportError as exc:
                status, sdk_band, ps5_sdk_hex = "invalid", None, ""
                detail, restorable = str(exc), False
            else:
                status, sdk_band, ps5_sdk_hex = "archive", None, ""
                detail, restorable = "verified single-file ZIP", True
        else:
            info = inspect_file(backup_path)
            status, sdk_band = info.status, info.sdk_band
            ps5_sdk_hex = (f"0x{info.ps5_sdk:08X}"
                           if info.ps5_sdk is not None else "")
            detail = info.detail
            restorable = (info.status in {"patchable", "signed"} and
                          info.ps5_sdk is not None)
        inventory.append({
            "target_path": str(target), "backup_path": str(backup_path),
            "sequence": sequence, "target_exists": target.is_file(),
            "status": status, "sdk_band": sdk_band,
            "ps5_sdk_hex": ps5_sdk_hex,
            "detail": detail, "restorable": restorable,
        })
    return inventory


def _validated_rel(raw: str) -> str:
    """Normalize a patch member path and reject anything that escapes the root.

    ZIP entries and folder walks are untrusted layout: an absolute path, a
    ``..`` component, or a drive letter must never write outside the target.
    """
    rel = raw.replace("\\", "/")
    parts: list[str] = []
    for part in rel.split("/"):
        if part in ("", "."):
            continue
        if part == ".." or (len(part) >= 2 and part[1] == ":"):
            raise BackportError(f"unsafe path in patch source: {raw!r}")
        parts.append(part)
    if not parts:
        raise BackportError(f"patch source has an empty member path: {raw!r}")
    return "/".join(parts)


def _emit_file(dest: Path, produce) -> bool:
    """Atomically write ``dest`` via a temp sibling; return whether it existed."""
    existed = dest.is_file()
    dest.parent.mkdir(parents=True, exist_ok=True)
    fd, name = tempfile.mkstemp(prefix=dest.name + ".", suffix=".tmp",
                               dir=str(dest.parent))
    temp_path = Path(name)
    try:
        with os.fdopen(fd, "wb") as out:
            produce(out)
            out.flush()
            os.fsync(out.fileno())
        os.replace(temp_path, dest)
    finally:
        temp_path.unlink(missing_ok=True)
    return existed


def patch_member_paths(patch: Path) -> list[str]:
    """List the validated relative paths a folder/ZIP patch would write."""
    patch = Path(patch)
    if patch.is_dir():
        return [_validated_rel(str(p.relative_to(patch)))
                for p in sorted((q for q in patch.rglob("*") if q.is_file()),
                                key=lambda q: str(q).lower())]
    if zipfile.is_zipfile(patch):
        try:
            with zipfile.ZipFile(patch) as bundle:
                return [_validated_rel(info.filename)
                        for info in bundle.infolist() if not info.is_dir()]
        except zipfile.BadZipFile as exc:
            raise BackportError(f"invalid patch ZIP: {exc}") from exc
    raise BackportError("patch source must be a folder or a .zip file")


def apply_overwrite(target_dir: Path, patch: Path, *,
                    progress=None) -> dict:
    """Overlay a user-supplied ZIP or folder onto ``target_dir``.

    Every file in ``patch`` is written to the same relative path inside
    ``target_dir`` — replacing an existing file or adding a new one — exactly
    like dropping a patch/overwrite over an extracted game.  Directory layout
    is preserved and each file lands atomically.  Nothing in ``target_dir`` is
    deleted; unmatched originals are left untouched.
    """
    target_dir = Path(target_dir)
    if not target_dir.is_dir():
        raise BackportError("overwrite target folder does not exist")
    patch = Path(patch)
    items: list[dict] = []
    added = replaced = 0

    def record(rel: str, existed: bool) -> None:
        nonlocal added, replaced
        if existed:
            replaced += 1
        else:
            added += 1
        items.append({"path": rel, "result": "replaced" if existed else "added"})
        if progress is not None:
            from .core import ProgressEvent
            progress(ProgressEvent("overwrite", added + replaced, 0, rel))

    if patch.is_dir():
        members = sorted((p for p in patch.rglob("*") if p.is_file()),
                         key=lambda p: str(p).lower())
        if not members:
            raise BackportError("patch folder contains no files")
        for member in members:
            rel = _validated_rel(str(member.relative_to(patch)))

            def produce(out, src=member) -> None:
                with src.open("rb") as handle:
                    shutil.copyfileobj(handle, out, 1024 * 1024)
            existed = _emit_file(target_dir / rel, produce)
            record(rel, existed)
    elif zipfile.is_zipfile(patch):
        try:
            with zipfile.ZipFile(patch) as bundle:
                files = [i for i in bundle.infolist() if not i.is_dir()]
                if not files:
                    raise BackportError("patch ZIP contains no files")
                for info in files:
                    rel = _validated_rel(info.filename)

                    def produce(out, info=info, bundle=bundle) -> None:
                        with bundle.open(info) as src:
                            shutil.copyfileobj(src, out, 1024 * 1024)
                    existed = _emit_file(target_dir / rel, produce)
                    record(rel, existed)
        except zipfile.BadZipFile as exc:
            raise BackportError(f"invalid patch ZIP: {exc}") from exc
    else:
        raise BackportError("patch source must be a folder or a .zip file")

    return {"target": str(target_dir), "source": str(patch),
            "written": added + replaced, "added": added, "replaced": replaced,
            "items": items}


def restore_file(target: Path, backup_path: Path) -> dict:
    """Atomically restore one target while preserving its current state."""
    target, backup_path = Path(target), Path(backup_path)
    match = BACKUP_NAME_RE.match(backup_path.name)
    if not match or backup_path.with_name(match.group("target")) != target:
        raise BackportError("backup does not belong to the target file")
    if not backup_path.is_file():
        raise BackportError("backup file does not exist")
    if target.is_file() and _backup_matches(target, backup_path,
                                             target_name=target.name):
        return {"target_path": str(target), "backup_path": str(backup_path),
                "preserved_path": "", "result": "already"}

    fd, temp_name = tempfile.mkstemp(prefix=target.name + ".restore.",
                                     suffix=".tmp", dir=str(target.parent))
    os.close(fd)
    temp_path = Path(temp_name)
    preserved: Path | None = None
    installed = False
    try:
        if backup_path.suffix.lower() == ".zip":
            info = _zip_member(backup_path, target.name)
            try:
                with zipfile.ZipFile(backup_path, "r") as bundle, \
                        bundle.open(info, "r") as source, temp_path.open("wb") as dest:
                    shutil.copyfileobj(source, dest, length=1024 * 1024)
                    dest.flush()
                    os.fsync(dest.fileno())
            except (OSError, zipfile.BadZipFile, RuntimeError) as exc:
                raise BackportError(f"cannot extract backup ZIP: {exc}") from exc
        else:
            shutil.copy2(backup_path, temp_path)
        with temp_path.open("rb+") as fh:
            fh.flush()
            os.fsync(fh.fileno())
        if not _backup_matches(temp_path, backup_path, target_name=target.name):
            raise BackportError("verification of restore copy failed")
        restored_info = inspect_file(temp_path)
        if (restored_info.status not in {"patchable", "signed"} or
                restored_info.ps5_sdk is None):
            raise BackportError(restored_info.detail or
                                "backup does not contain a valid executable")
        if target.is_file():
            preserved = _backup_original(target)
        os.replace(temp_path, target)
        installed = True
        if not _backup_matches(target, backup_path, target_name=target.name):
            raise BackportError("verification of restored file failed")
    except Exception:
        if installed:
            if preserved and preserved.is_file():
                shutil.copy2(preserved, temp_path)
                os.replace(temp_path, target)
            else:
                target.unlink(missing_ok=True)
        raise
    finally:
        temp_path.unlink(missing_ok=True)
    return {"target_path": str(target), "backup_path": str(backup_path),
            "preserved_path": str(preserved) if preserved else "",
            "result": "restored"}


def restore_folder(folder: Path) -> dict:
    """Restore every original backup in ``folder`` with per-file reporting."""
    inventory = backup_inventory(folder)
    if not inventory:
        raise BackportError("no Backport backups were found")
    results: list[dict] = []
    restored = already = failed = 0
    for item in inventory:
        if not item["restorable"]:
            failed += 1
            results.append({**item, "result": "failed"})
            continue
        try:
            result = restore_file(Path(item["target_path"]),
                                  Path(item["backup_path"]))
        except (OSError, BackportError) as exc:
            failed += 1
            results.append({**item, "result": "failed", "detail": str(exc)})
        else:
            if result["result"] == "already":
                already += 1
            else:
                restored += 1
            results.append({**item, **result})
    return {"restored": restored, "already": already, "failed": failed,
            "items": results}


def patch_file(path: Path, target: int, *, backup: bool = True) -> ElfSdkInfo:
    """Patch one ELF or plaintext SELF and atomically install fake-signed output."""
    if target not in SDK_VERSION_PAIRS:
        raise BackportError(f"unsupported target SDK: {target}")
    path = Path(path)
    info = inspect_file(path)
    if info.status not in {"patchable", "signed"} or info.ps5_sdk is None:
        raise BackportError(info.detail or f"file is not patchable: {info.status}")
    ps5, ps4 = SDK_VERSION_PAIRS[target]
    if info.ps5_sdk == ps5 and info.ps4_sdk == ps4:
        return info

    work_dir = Path(tempfile.mkdtemp(prefix="exfat_forge_backport_"))
    unsigned = work_dir / "unsigned.elf"
    patched = work_dir / "patched.elf"
    fd, result_name = tempfile.mkstemp(prefix=path.name + ".", suffix=".tmp",
                                       dir=str(path.parent))
    os.close(fd)
    result_file = Path(result_name)
    backup_path: Path | None = None
    try:
        with path.open("rb") as fh:
            source_magic = fh.read(4)
        if source_magic in SIGNED_MAGICS:
            _extract_fself(path, unsigned)
        else:
            shutil.copy2(path, unsigned)
        shutil.copy2(unsigned, patched)
        patch_info = inspect_file(patched)
        if (patch_info.status != "patchable" or patch_info.ps5_offset is None or
                patch_info.ps4_offset is None):
            raise BackportError(patch_info.detail or "extracted ELF is not patchable")
        with patched.open("r+b") as fh:
            fh.seek(patch_info.ps4_offset)
            fh.write(struct.pack("<I", ps4))
            fh.seek(patch_info.ps5_offset)
            fh.write(struct.pack("<I", ps5))
            fh.flush()
            os.fsync(fh.fileno())
        check = inspect_file(patched)
        if check.status != "patchable" or check.ps5_sdk != ps5 or check.ps4_sdk != ps4:
            raise BackportError("verification of patched ELF failed")

        _fake_sign(patched, result_file)
        if target <= 6:
            content = result_file.read_bytes()
            if LIBC_PATCH_PATTERN in content:
                result_file.write_bytes(
                    content.replace(LIBC_PATCH_PATTERN, LIBC_PATCH_REPLACEMENT))

        # Verify that the installed container can be extracted and still has
        # exactly the requested SDK values before touching the original.
        verified = work_dir / "verified.elf"
        _extract_fself(result_file, verified)
        final_check = inspect_file(verified)
        if (final_check.status != "patchable" or final_check.ps5_sdk != ps5 or
                final_check.ps4_sdk != ps4):
            raise BackportError("verification of fake-signed output failed")

        if backup:
            # This is the last operation before replacement. The source file
            # is still untouched, and the verified backup must be durable
            # before the patched container can take its place.
            backup_path = _backup_original(path)
        os.replace(result_file, path)
    finally:
        result_file.unlink(missing_ok=True)
        shutil.rmtree(work_dir, ignore_errors=True)
    updated = inspect_file(path)
    return replace(updated, backup_path=str(backup_path) if backup_path else "")


def patch_folder(folder: Path, target: int, *, backup: bool = True) -> dict:
    """Patch all eligible files and return an auditable per-file result."""
    before = scan(folder)
    results: list[dict] = []
    patched = already = skipped = failed = 0
    target_ps5, target_ps4 = SDK_VERSION_PAIRS.get(target, (None, None))
    if target_ps5 is None:
        raise BackportError(f"unsupported target SDK: {target}")

    # Complete preflight before the first write.
    def can_patch(item: ElfSdkInfo) -> bool:
        return (item.status == "patchable" or
                (item.status == "signed" and item.ps5_sdk is not None))

    eligible = [item for item in before if can_patch(item)]
    for item in before:
        if not can_patch(item):
            skipped += 1
            results.append({**item.public_dict(), "result": "skipped"})
    for item in eligible:
        if item.ps5_sdk == target_ps5 and item.ps4_sdk == target_ps4:
            already += 1
            results.append({**item.public_dict(), "result": "already"})
            continue
        try:
            updated = patch_file(Path(item.path), target, backup=backup)
        except (OSError, BackportError) as exc:
            failed += 1
            results.append({**item.public_dict(), "result": "failed",
                            "detail": str(exc)})
        else:
            patched += 1
            results.append({**updated.public_dict(), "result": "patched"})
    backups = sum(bool(item.get("backup_path")) for item in results)
    return {"target": target, "ps5_sdk": f"0x{target_ps5:08X}",
            "ps4_sdk": f"0x{target_ps4:08X}", "patched": patched,
            "already": already, "skipped": skipped, "failed": failed,
            "backups": backups,
            "items": results}
