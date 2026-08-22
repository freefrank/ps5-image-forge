"""Pipeline, settings, history and library tests."""

from __future__ import annotations

import json
import random
from pathlib import Path

import pytest

from exfat_forge import core, library, pipeline, ufs
from exfat_forge.settings import History, HistoryEntry, Settings

_HAS_UFS = ufs.tool_available() and ufs.dotnet_status().available


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
