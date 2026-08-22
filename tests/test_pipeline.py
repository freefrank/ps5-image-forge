"""Pipeline, settings, history and library tests."""

from __future__ import annotations

import json
import random
from pathlib import Path

import pytest

from exfat_forge import backport, core, library, pipeline, ufs
from exfat_forge.settings import History, HistoryEntry, Settings

_HAS_UFS = ufs.tool_available() and ufs.dotnet_status().available


def _patchable_eboot(path: Path, *, band: int = 10) -> Path:
    """Write a minimal ELF whose SCE process-param carries an SDK band."""
    import struct

    data = bytearray(0x180)
    data[:4] = backport.ELF_MAGIC
    data[4:7] = b"\x02\x01\x01"
    data[7] = 9
    struct.pack_into("<HHI", data, 0x10, 0xFE10, 0x3E, 1)
    struct.pack_into("<Q", data, 0x20, 0x40)
    struct.pack_into("<H", data, 0x34, 0x40)
    struct.pack_into("<H", data, 0x36, 0x38)
    struct.pack_into("<H", data, 0x38, 2)
    struct.pack_into("<II6Q", data, 0x40,
                     1, 4, 0x100, 0, 0, 0x30, 0x30, 0x10)
    struct.pack_into("<I", data, 0x78, backport.PT_SCE_PROCPARAM)
    struct.pack_into("<Q", data, 0x80, 0x100)
    struct.pack_into("<Q", data, 0x98, 0x30)
    struct.pack_into("<I", data, 0x100, 0x30)
    struct.pack_into("<I", data, 0x108, backport.SCE_PROCESS_PARAM_MAGIC)
    ps5, ps4 = backport.SDK_VERSION_PAIRS[band]
    struct.pack_into("<I", data, 0x110, ps4)
    struct.pack_into("<I", data, 0x114, ps5)
    path.write_bytes(data)
    return path


@pytest.fixture()
def dump(tmp_path: Path) -> Path:
    rng = random.Random(4242)
    src = tmp_path / "PPSA55555-app0"
    (src / "sce_sys").mkdir(parents=True)
    (src / "sce_sys" / "param.json").write_text(json.dumps({
        "titleId": "PPSA55555", "contentVersion": "02.000.000",
        "localizedParameters": {"defaultLanguage": "en-US",
                                "en-US": {"titleName": "Pipeline Test"}},
    }), encoding="utf-8")
    (src / "eboot.bin").write_bytes(rng.randbytes(200_000))
    (src / "data").mkdir()
    for i in range(5):
        (src / "data" / f"f{i}.dat").write_bytes(rng.randbytes(80_000))
    return src


@pytest.fixture(autouse=True)
def isolated_config(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Keep tests away from the real %APPDATA% store."""
    monkeypatch.setenv("APPDATA", str(tmp_path / "appdata"))


# ── settings / history ────────────────────────────────────────────

def test_settings_roundtrip_and_unknown_keys(tmp_path: Path) -> None:
    s = Settings()
    s.output_dir = r"D:\PS5"
    s.pfs_level = 4
    s.save()
    # a stale key from an older version must not break loading
    raw = json.loads(Settings.path().read_text(encoding="utf-8"))
    raw["removed_option_from_v1"] = True
    Settings.path().write_text(json.dumps(raw), encoding="utf-8")

    loaded = Settings.load()
    assert loaded.output_dir == r"D:\PS5"
    assert loaded.pfs_level == 4


def test_settings_load_survives_corrupt_file() -> None:
    Settings.path().write_text("{ this is not json", encoding="utf-8")
    assert Settings.load().pfs_level == 9      # falls back to defaults


def test_history_newest_first_and_capped() -> None:
    h = History(limit=3)
    for i in range(5):
        h.add(HistoryEntry(timestamp=f"t{i}", source="s", output="o",
                           fmt="exfat", size_bytes=i, duration_s=1.0,
                           file_count=1, verified=True, status="ok"))
    entries = h.load()
    assert len(entries) == 3
    assert [e.timestamp for e in entries] == ["t4", "t3", "t2"]


# ── library ───────────────────────────────────────────────────────

def test_library_finds_dumps_and_images(dump: Path, tmp_path: Path) -> None:
    (tmp_path / "images").mkdir()
    (tmp_path / "images" / "GAME1.exfat").write_bytes(b"x" * 100)
    (tmp_path / "images" / "GAME2.ffpfsc").write_bytes(b"y" * 200)
    (tmp_path / "images" / "notes.txt").write_text("ignore me")

    dumps, images = library.scan_folders([str(tmp_path)])
    assert [d.title_id for d in dumps] == ["PPSA55555"]
    assert dumps[0].title == "Pipeline Test"
    assert dumps[0].file_count == 7
    assert {i.fmt for i in images} == {"exfat", "pfs"}
    assert not any(i.name == "notes.txt" for i in images)


def test_library_does_not_descend_into_dumps(dump: Path, tmp_path: Path) -> None:
    """A dump's internals must not be scanned as if they held more dumps."""
    (dump / "data" / "nested.exfat").write_bytes(b"z" * 10)
    _dumps, images = library.scan_folders([str(tmp_path)])
    assert not any("nested" in i.name for i in images)


# ── pipeline ──────────────────────────────────────────────────────

def test_default_names_include_title_id_title_and_version(dump: Path) -> None:
    stem = "PPSA55555_Pipeline_Test_02.000.000"
    assert pipeline.default_name(dump, "exfat") == stem + ".exfat"
    assert pipeline.default_name(dump, "ffpkg") == stem + ".ffpkg"
    assert pipeline.default_name(dump, "pfs") == stem + ".ffpfsc"

    image = core.build_exfat(dump, dump.parent / "PPSA55555.exfat")
    assert pipeline.default_name(image, "pfs") == stem + ".ffpfsc"


def test_pipeline_exfat_records_history(dump: Path, tmp_path: Path) -> None:
    spec = pipeline.JobSpec(source=dump, output_dir=tmp_path / "out",
                            fmt="exfat")
    result = pipeline.run_job(spec)
    assert result.output.name == \
        "PPSA55555_Pipeline_Test_02.000.000.exfat"
    assert result.verified and result.file_count == 7

    entry = History().load()[0]
    assert entry.status == "ok" and entry.title_id == "PPSA55555"
    assert entry.fmt == "exfat" and entry.size_bytes == result.size_bytes


def test_pipeline_pfs_via_exfat_removes_intermediate(dump: Path,
                                                     tmp_path: Path) -> None:
    out = tmp_path / "out"
    spec = pipeline.JobSpec(source=dump, output_dir=out, fmt="pfs",
                            intermediate="exfat", level=3)
    result = pipeline.run_job(spec)
    assert result.output.suffix == ".ffpfsc"
    assert not list(out.glob("*.exfat")), "intermediate should be cleaned up"


def test_pipeline_pfs_keeps_intermediate_when_asked(dump: Path,
                                                    tmp_path: Path) -> None:
    out = tmp_path / "out"
    pipeline.run_job(pipeline.JobSpec(
        source=dump, output_dir=out, fmt="pfs", level=1,
        keep_intermediate=True))
    assert list(out.glob("*.exfat")), "intermediate should be kept"


def test_pipeline_failure_is_recorded(tmp_path: Path) -> None:
    spec = pipeline.JobSpec(source=tmp_path / "nope", output_dir=tmp_path,
                            fmt="exfat")
    with pytest.raises(Exception):
        pipeline.run_job(spec)
    entry = History().load()[0]
    assert entry.status == "failed" and entry.message


def test_extract_any_roundtrip_exfat(dump: Path, tmp_path: Path) -> None:
    res = pipeline.run_job(pipeline.JobSpec(
        source=dump, output_dir=tmp_path / "out", fmt="exfat"))
    dest = tmp_path / "back"
    assert pipeline.extract_any(res.output, dest) == 7
    assert (dest / "eboot.bin").read_bytes() == (dump / "eboot.bin").read_bytes()


def test_extract_any_roundtrip_pfs(dump: Path, tmp_path: Path) -> None:
    res = pipeline.run_job(pipeline.JobSpec(
        source=dump, output_dir=tmp_path / "out", fmt="pfs", level=1))
    dest = tmp_path / "back"
    count = pipeline.extract_any(res.output, dest, overwrite=True)
    assert count >= 1


@pytest.mark.skipif(not _HAS_UFS, reason="needs UFS2Tool + .NET")
def test_pipeline_ffpkg_and_pfs_via_ffpkg(dump: Path, tmp_path: Path) -> None:
    out = tmp_path / "out"
    res = pipeline.run_job(pipeline.JobSpec(
        source=dump, output_dir=out, fmt="ffpkg"))
    assert res.output.suffix == ".ffpkg" and res.verified

    res2 = pipeline.run_job(pipeline.JobSpec(
        source=dump, output_dir=tmp_path / "out2", fmt="pfs",
        intermediate="ffpkg", level=1))
    assert res2.output.suffix == ".ffpfsc"


def test_bad_format_rejected(dump: Path, tmp_path: Path) -> None:
    with pytest.raises(ValueError):
        pipeline.JobSpec(source=dump, output_dir=tmp_path, fmt="iso")


# ── image-internal editing: backport + patch overwrite ────────────

def test_rebuild_image_roundtrip_exfat(dump: Path, tmp_path: Path) -> None:
    image = core.build_exfat(dump, tmp_path / "a.exfat")
    work = tmp_path / "work"
    pipeline.extract_any(image, work)
    (work / "eboot.bin").write_bytes(b"changed after extract")

    pipeline.rebuild_image(work, image)
    back = tmp_path / "back"
    pipeline.extract_any(image, back)
    assert (back / "eboot.bin").read_bytes() == b"changed after extract"


def test_backport_image_patches_eboot_in_place_exfat(
        dump: Path, tmp_path: Path) -> None:
    _patchable_eboot(dump / "eboot.bin", band=10)
    image = core.build_exfat(dump, tmp_path / "game.exfat")
    original_eboot = (dump / "eboot.bin").read_bytes()

    result = pipeline.backport_image(image, 5)
    assert result["patched"] == 1 and result["failed"] == 0
    # Backup is a small sidecar zip of only the changed files' originals,
    # never a copy of the whole (potentially 100 GB+) image.
    backup = Path(result["backup_path"])
    assert backup.name == "game.bak.zip"
    import zipfile
    with zipfile.ZipFile(backup) as bundle:
        assert bundle.namelist() == ["eboot.bin"]
        assert bundle.read("eboot.bin") == original_eboot

    check = tmp_path / "check"
    pipeline.extract_any(image, check)
    assert backport.inspect_file(check / "eboot.bin").sdk_band == 5

    # Overlaying the backup restores the original executable inside the image.
    pipeline.overwrite_image(image, backup, backup=False)
    restored = tmp_path / "restored"
    pipeline.extract_any(image, restored, overwrite=True)
    assert (restored / "eboot.bin").read_bytes() == original_eboot


def test_backport_image_can_skip_backup(dump: Path, tmp_path: Path) -> None:
    _patchable_eboot(dump / "eboot.bin", band=10)
    image = core.build_exfat(dump, tmp_path / "game.exfat")
    result = pipeline.backport_image(image, 7, backup=False)
    assert result["patched"] == 1
    assert "backup_path" not in result
    assert not (tmp_path / "game.bak.zip").exists()


def test_overwrite_image_replaces_and_adds_files_exfat(
        dump: Path, tmp_path: Path) -> None:
    image = core.build_exfat(dump, tmp_path / "game.exfat")

    patch = tmp_path / "patch"
    patch.mkdir()
    (patch / "eboot.bin").write_bytes(b"overwritten eboot payload")
    (patch / "mods").mkdir()
    (patch / "mods" / "extra.prx").write_bytes(b"a whole new module")

    result = pipeline.overwrite_image(image, patch)
    assert result["replaced"] == 1 and result["added"] == 1

    check = tmp_path / "check"
    pipeline.extract_any(image, check)
    assert (check / "eboot.bin").read_bytes() == b"overwritten eboot payload"
    assert (check / "mods" / "extra.prx").read_bytes() == b"a whole new module"
    assert (check / "data" / "f0.dat").read_bytes() == \
        (dump / "data" / "f0.dat").read_bytes()


def test_backport_image_in_place_pfs(dump: Path, tmp_path: Path) -> None:
    _patchable_eboot(dump / "eboot.bin", band=10)
    res = pipeline.run_job(pipeline.JobSpec(
        source=dump, output_dir=tmp_path / "out", fmt="pfs", level=1))
    image = res.output

    result = pipeline.backport_image(image, 5, backup=False, level=1)
    assert result["patched"] == 1 and result["failed"] == 0
    assert image.suffix == ".ffpfsc"

    # A PFS unwraps to its inner exfat, which then holds the game tree.
    outer = tmp_path / "outer"
    pipeline.extract_any(image, outer, overwrite=True)
    inner = next(p for p in outer.iterdir() if p.suffix.lower() == ".exfat")
    check = tmp_path / "check"
    pipeline.extract_any(inner, check, overwrite=True)
    assert backport.inspect_file(check / "eboot.bin").sdk_band == 5


def test_edit_image_rejects_unsupported_source(tmp_path: Path) -> None:
    plain = tmp_path / "notes.txt"
    plain.write_text("x", encoding="utf-8")
    with pytest.raises(backport.BackportError):
        pipeline.backport_image(plain, 5)


def test_backport_image_stages_scratch_in_work_dir(
        dump: Path, tmp_path: Path) -> None:
    _patchable_eboot(dump / "eboot.bin", band=10)
    image = core.build_exfat(dump, tmp_path / "lib" / "game.exfat")
    ssd = tmp_path / "ssd"

    # The pipeline's own scratch dirs carry these prefixes; backport's internal
    # fself temp (system temp) is unrelated and must not be asserted on.
    pipeline_prefixes = ("exfat_forge_edit_", "exfat_forge_tree_",
                         "exfat_forge_pfs_", "exfat_forge_bak_")
    staged: list[str] = []
    real_mkdtemp = pipeline.tempfile.mkdtemp

    def spy(*args, **kwargs):
        prefix = kwargs.get("prefix", "")
        if prefix.startswith(pipeline_prefixes):
            staged.append(kwargs.get("dir", ""))
        return real_mkdtemp(*args, **kwargs)

    import unittest.mock as mock
    with mock.patch.object(pipeline.tempfile, "mkdtemp", spy):
        result = pipeline.backport_image(image, 5, work_dir=ssd)

    assert result["patched"] == 1
    # Every large scratch dir was created under the SSD work dir, not the
    # image's (HDD) folder.
    assert staged and all(str(ssd) in d for d in staged), staged
    # Nothing is left behind on either volume.
    assert not any(p.name.startswith("exfat_forge_") for p in ssd.iterdir())
    assert not any(p.name.startswith("exfat_forge_")
                   for p in (tmp_path / "lib").iterdir())


def test_compress_passes_temp_dir_to_mkpfs(dump: Path, tmp_path: Path) -> None:
    image = core.build_exfat(dump, tmp_path / "game.exfat")
    ssd = tmp_path / "ssd"
    captured: dict[str, object] = {}
    real_pack = core.pack_pfs

    def spy(*args, **kwargs):
        captured["temp_dir"] = kwargs.get("temp_dir")
        return real_pack(*args, **kwargs)

    import unittest.mock as mock
    with mock.patch.object(core, "pack_pfs", spy):
        pipeline.run_job(pipeline.JobSpec(
            source=image, output_dir=tmp_path / "out", fmt="pfs", level=1,
            work_dir=str(ssd)))
    assert captured["temp_dir"] == ssd
