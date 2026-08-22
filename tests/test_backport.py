"""PS5 SDK downgrade inspection and safe patching."""

from __future__ import annotations

import struct
import zipfile
from pathlib import Path

import pytest

from exfat_forge import backport


@pytest.mark.parametrize(("firmware", "target"), [
    ("5.50", 5), ("9.60", 9), ("12.00", 10), (" 4.xx", 4), ("", None),
])
def test_recommended_target_follows_selected_firmware(
        firmware: str, target: int | None) -> None:
    assert backport.recommended_target(firmware) == target


def _elf(path: Path, *, band: int = 10,
         kind: int = backport.PT_SCE_PROCPARAM) -> Path:
    data = bytearray(0x180)
    data[:4] = backport.ELF_MAGIC
    data[4:7] = b"\x02\x01\x01"               # ELF64, little-endian
    data[7] = 9                                 # FreeBSD ABI
    struct.pack_into("<HHI", data, 0x10, 0xFE10, 0x3E, 1)
    struct.pack_into("<Q", data, 0x20, 0x40)       # e_phoff
    struct.pack_into("<H", data, 0x34, 0x40)       # e_ehsize
    struct.pack_into("<H", data, 0x36, 0x38)       # e_phentsize
    struct.pack_into("<H", data, 0x38, 2)          # e_phnum
    struct.pack_into("<II6Q", data, 0x40,
                     1, 4, 0x100, 0, 0, 0x30, 0x30, 0x10)  # PT_LOAD
    struct.pack_into("<I", data, 0x78, kind)
    struct.pack_into("<Q", data, 0x80, 0x100)      # p_offset
    struct.pack_into("<Q", data, 0x98, 0x30)       # p_filesz
    struct.pack_into("<I", data, 0x100, 0x30)
    magic = (backport.SCE_PROCESS_PARAM_MAGIC if kind == backport.PT_SCE_PROCPARAM
             else backport.SCE_MODULE_PARAM_MAGIC)
    struct.pack_into("<I", data, 0x108, magic)
    ps5, ps4 = backport.SDK_VERSION_PAIRS[band]
    struct.pack_into("<I", data, 0x110, ps4)
    struct.pack_into("<I", data, 0x114, ps5)
    path.write_bytes(data)
    return path


def test_inspect_and_patch_creates_verified_backup(tmp_path: Path) -> None:
    path = _elf(tmp_path / "eboot.bin", band=10)
    original = path.read_bytes()
    info = backport.inspect_file(path)
    assert info.status == "patchable" and info.sdk_band == 10

    updated = backport.patch_file(path, 5)
    assert updated.sdk_band == 5
    backup = path.with_name("eboot.bin.bak.zip")
    assert updated.backup_path == str(backup)
    with zipfile.ZipFile(backup) as bundle:
        assert bundle.read("eboot.bin") == original
    assert not list(tmp_path.glob("*.tmp"))


def test_patch_preserves_old_backup_and_saves_current_original(tmp_path: Path) -> None:
    path = _elf(tmp_path / "eboot.bin", band=10)
    first_original = path.read_bytes()
    backport.patch_file(path, 7)
    current_original = path.read_bytes()

    updated = backport.patch_file(path, 5)

    first_backup = path.with_name("eboot.bin.bak.zip")
    current_backup = path.with_name("eboot.bin.bak.1.zip")
    with zipfile.ZipFile(first_backup) as bundle:
        assert bundle.read("eboot.bin") == first_original
    with zipfile.ZipFile(current_backup) as bundle:
        assert bundle.read("eboot.bin") == current_original
    assert updated.backup_path == str(current_backup)


def test_restore_folder_restores_original_and_preserves_patched_file(
        tmp_path: Path) -> None:
    path = _elf(tmp_path / "eboot.bin", band=10)
    original = path.read_bytes()
    backport.patch_file(path, 5)
    patched = path.read_bytes()

    inventory = backport.backup_inventory(tmp_path)
    assert len(inventory) == 1
    assert inventory[0]["backup_path"] == str(tmp_path / "eboot.bin.bak.zip")
    assert inventory[0]["status"] == "archive" and inventory[0]["restorable"]

    result = backport.restore_folder(tmp_path)

    assert result["restored"] == 1 and result["failed"] == 0
    assert path.read_bytes() == original
    preserved = tmp_path / "eboot.bin.bak.1.zip"
    with zipfile.ZipFile(preserved) as bundle:
        assert bundle.read("eboot.bin") == patched
    assert result["items"][0]["preserved_path"].endswith("eboot.bin.bak.1.zip")


def test_restore_rejects_corrupt_backup_without_touching_target(
        tmp_path: Path) -> None:
    path = _elf(tmp_path / "eboot.bin", band=10)
    backport.patch_file(path, 5)
    patched = path.read_bytes()
    (tmp_path / "eboot.bin.bak.zip").write_bytes(b"corrupt")

    result = backport.restore_folder(tmp_path)

    assert result["restored"] == 0 and result["failed"] == 1
    assert path.read_bytes() == patched
    assert not (tmp_path / "eboot.bin.bak.1.zip").exists()


def test_restore_can_recreate_a_missing_target(tmp_path: Path) -> None:
    path = _elf(tmp_path / "module.prx", band=9)
    original = path.read_bytes()
    backup = tmp_path / "module.prx.bak"
    path.replace(backup)

    result = backport.restore_folder(tmp_path)

    assert result["restored"] == 1 and path.read_bytes() == original
    assert result["items"][0]["preserved_path"] == ""


def test_new_zip_generation_preserves_legacy_original_backup(
        tmp_path: Path) -> None:
    path = _elf(tmp_path / "eboot.bin", band=10)
    legacy = tmp_path / "eboot.bin.bak"
    legacy.write_bytes(path.read_bytes())
    _elf(path, band=9)

    created = backport._backup_original(path)
    inventory = backport.backup_inventory(tmp_path)

    assert created.name == "eboot.bin.bak.1.zip"
    assert inventory[0]["backup_path"] == str(legacy)
    assert inventory[0]["sdk_band"] == 10


def test_patch_folder_preflights_and_reports_skips(tmp_path: Path) -> None:
    _elf(tmp_path / "eboot.bin", band=9)
    _elf(tmp_path / "module.prx", band=9,
         kind=backport.PT_SCE_MODULE_PARAM)
    (tmp_path / "signed.sprx").write_bytes(b"\x54\x14\xf5\xee" + bytes(64))
    (tmp_path / "readme.txt").write_text("leave me alone", encoding="utf-8")

    result = backport.patch_folder(tmp_path, 7)
    assert result["patched"] == 2 and result["skipped"] == 1
    assert result["failed"] == 0
    assert backport.inspect_file(tmp_path / "eboot.bin").sdk_band == 7
    assert (tmp_path / "readme.txt").read_text(encoding="utf-8") == "leave me alone"


def test_signed_and_malformed_files_are_never_modified(tmp_path: Path) -> None:
    signed = tmp_path / "eboot.bin"
    signed.write_bytes(b"\x4f\x15\x3d\x1d" + bytes(64))
    before = signed.read_bytes()
    assert backport.inspect_file(signed).status == "invalid"
    with pytest.raises(backport.BackportError):
        backport.patch_file(signed, 5)
    assert signed.read_bytes() == before
    assert not signed.with_name("eboot.bin.bak.zip").exists()


def test_auto_backport_extracts_and_resigns_plaintext_fself(tmp_path: Path) -> None:
    unsigned = _elf(tmp_path / "unsigned.elf", band=10)
    signed = tmp_path / "eboot.bin"
    backport._fake_sign(unsigned, signed)
    unsigned.unlink()
    original = signed.read_bytes()

    before = backport.inspect_file(signed)
    assert before.status == "signed" and before.sdk_band == 10

    result = backport.patch_folder(tmp_path, 5)
    assert result["patched"] == 1 and result["skipped"] == 0
    assert result["failed"] == 0 and len(result["items"]) == 1
    assert signed.read_bytes()[:4] in backport.SIGNED_MAGICS
    with zipfile.ZipFile(signed.with_name("eboot.bin.bak.zip")) as bundle:
        assert bundle.read("eboot.bin") == original
    after = backport.inspect_file(signed)
    assert after.status == "signed" and after.sdk_band == 5


def test_apply_overwrite_from_folder_replaces_and_adds(tmp_path: Path) -> None:
    target = tmp_path / "game"
    (target / "sce_sys").mkdir(parents=True)
    (target / "eboot.bin").write_bytes(b"original eboot")
    (target / "keep.txt").write_text("untouched", encoding="utf-8")

    patch = tmp_path / "patch"
    (patch / "sce_sys").mkdir(parents=True)
    (patch / "eboot.bin").write_bytes(b"patched eboot")
    (patch / "sce_sys" / "new.prx").write_bytes(b"brand new")

    result = backport.apply_overwrite(target, patch)

    assert result["replaced"] == 1 and result["added"] == 1
    assert (target / "eboot.bin").read_bytes() == b"patched eboot"
    assert (target / "sce_sys" / "new.prx").read_bytes() == b"brand new"
    assert (target / "keep.txt").read_text(encoding="utf-8") == "untouched"


def test_apply_overwrite_from_zip(tmp_path: Path) -> None:
    target = tmp_path / "game"
    target.mkdir()
    (target / "eboot.bin").write_bytes(b"old")

    patch = tmp_path / "patch.zip"
    with zipfile.ZipFile(patch, "w") as bundle:
        bundle.writestr("eboot.bin", b"new")
        bundle.writestr("assets/level.dat", b"level")

    result = backport.apply_overwrite(target, patch)

    assert result["replaced"] == 1 and result["added"] == 1
    assert (target / "eboot.bin").read_bytes() == b"new"
    assert (target / "assets" / "level.dat").read_bytes() == b"level"


def test_apply_overwrite_rejects_path_traversal(tmp_path: Path) -> None:
    target = tmp_path / "game"
    target.mkdir()
    patch = tmp_path / "evil.zip"
    with zipfile.ZipFile(patch, "w") as bundle:
        bundle.writestr("../escape.bin", b"nope")

    with pytest.raises(backport.BackportError, match="unsafe path"):
        backport.apply_overwrite(target, patch)
    assert not (tmp_path / "escape.bin").exists()


def test_apply_overwrite_rejects_non_patch_source(tmp_path: Path) -> None:
    target = tmp_path / "game"
    target.mkdir()
    plain = tmp_path / "notes.txt"
    plain.write_text("not a patch", encoding="utf-8")
    with pytest.raises(backport.BackportError, match="folder or a .zip"):
        backport.apply_overwrite(target, plain)


def test_invalid_target_and_missing_folder(tmp_path: Path) -> None:
    path = _elf(tmp_path / "x.elf")
    with pytest.raises(backport.BackportError, match="unsupported"):
        backport.patch_file(path, 11)
    with pytest.raises(backport.BackportError, match="does not exist"):
        backport.scan(tmp_path / "missing")
