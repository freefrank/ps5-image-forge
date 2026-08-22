"""End-to-end tests: build → verify → extract → byte-compare.

The whole pipeline runs against a small synthetic dump tree, entirely in a
temp directory — no admin rights, no mounting, a few seconds per run.
"""

from __future__ import annotations

import json
import os
import random
from pathlib import Path

import pytest

from exfat_forge import core


@pytest.fixture()
def dump(tmp_path: Path) -> Path:
    """A miniature PS5-dump-shaped tree with deterministic random content."""
    rng = random.Random(1234)
    src = tmp_path / "PPSA99999-app0"
    (src / "sce_sys").mkdir(parents=True)
    (src / "sce_sys" / "param.json").write_text(json.dumps({
        "titleId": "PPSA99999",
        "contentVersion": "01.000.000",
        "localizedParameters": {
            "defaultLanguage": "en-US",
            "en-US": {"titleName": "Test Game"},
        },
    }), encoding="utf-8")
    (src / "eboot.bin").write_bytes(rng.randbytes(300_000))
    deep = src / "assets" / "textures" / "level01"
    deep.mkdir(parents=True)
    for i in range(30):
        (deep / f"tex_{i:03d}.dat").write_bytes(rng.randbytes(rng.randrange(0, 200_000)))
    (src / "assets" / "empty.bin").write_bytes(b"")
    (src / "assets" / "big.bin").write_bytes(rng.randbytes(3_000_000))
    (src / "名字 with spaces éà.txt").write_bytes("中文内容".encode("utf-8"))
    return src


def test_scan_reads_param_json(dump: Path) -> None:
    info = core.scan_source(dump)
    assert info.title_id == "PPSA99999"
    assert info.title == "Test Game"
    assert info.file_count == 35
    assert info.total_bytes > 3_000_000


def test_build_verify_roundtrip(dump: Path, tmp_path: Path) -> None:
    out_dir = tmp_path / "out"
    out_dir.mkdir()
    image = core.build_exfat(dump, out_dir)
    assert image.name == "PPSA99999.exfat"
    assert not list(out_dir.glob("*.part")), "partial file must not survive"

    # exFAT signature at offset 3 — same check the old tool's verifier used
    with image.open("rb") as fh:
        assert fh.read(11)[3:] == b"EXFAT   "

    files, total = core.verify_image(image, dump)
    assert files == 35
    assert total == core.scan_source(dump).total_bytes


def test_verify_catches_corruption(dump: Path, tmp_path: Path) -> None:
    image = core.build_exfat(dump, tmp_path / "img")
    # Corrupt file DATA (not cluster padding): find big.bin's unique first
    # bytes inside the image and flip them there.
    needle = (dump / "assets" / "big.bin").read_bytes()[:32]
    blob = image.read_bytes()
    offset = blob.index(needle)
    with image.open("r+b") as fh:
        fh.seek(offset)
        fh.write(bytes(b ^ 0xFF for b in needle))
    with pytest.raises(core.VerifyError):
        core.verify_image(image, dump)


def test_extract_roundtrip(dump: Path, tmp_path: Path) -> None:
    image = core.build_exfat(dump, tmp_path / "img")
    dest = tmp_path / "unpacked"
    count = core.extract_image(image, dest)
    assert count == 35
    for root, _dirs, files in os.walk(dump):
        for name in files:
            src_file = Path(root) / name
            rel = src_file.relative_to(dump)
            assert (dest / rel).read_bytes() == src_file.read_bytes(), rel


def test_cancel_stops_build(dump: Path, tmp_path: Path) -> None:
    token = core.CancelToken()
    token.cancel()
    with pytest.raises(core.BuildCancelled):
        core.build_exfat(dump, tmp_path / "img", cancel=token)
    assert not list((tmp_path / "img").glob("*")) if (tmp_path / "img").exists() else True


def test_output_never_clobbered_on_failure(dump: Path, tmp_path: Path) -> None:
    """A failed rebuild must leave an existing good image untouched."""
    out = tmp_path / "img"
    image = core.build_exfat(dump, out)
    good_bytes = image.stat().st_size
    token = core.CancelToken()
    token.cancel()
    with pytest.raises(core.BuildCancelled):
        core.build_exfat(dump, out, cancel=token)
    assert image.stat().st_size == good_bytes


def test_pack_pfs_compressed(dump: Path, tmp_path: Path) -> None:
    """Compressed ffpfsc must be valid per mkpfs and smaller than plain."""
    import subprocess
    import sys

    # Highly compressible payload so the ratio check is meaningful.
    (dump / "assets" / "zeros.bin").write_bytes(b"\0" * 4_000_000)
    image = core.build_exfat(dump, tmp_path / "img")
    plain = core.pack_pfs(image, tmp_path / "plain.ffpfsc", compress=False)
    events: list[core.ProgressEvent] = []
    packed = core.pack_pfs(image, tmp_path / "packed.ffpfsc",
                           compress=True, compression_level=6,
                           progress=events.append)
    assert packed.stat().st_size < plain.stat().st_size
    assert any(event.detail.startswith("$ ") and "mkpfs" in event.detail
               for event in events)
    compression = [event for event in events if event.phase == "compress"]
    assert compression and compression[-1].done == compression[-1].total == 100
    check = subprocess.run(
        [sys.executable, "-m", "mkpfs", "verify", str(packed)],
        capture_output=True, text=True)
    assert check.returncode == 0, check.stdout + check.stderr
