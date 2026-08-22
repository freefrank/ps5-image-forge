"""ffpkg (UFS) backend tests — skipped when the .NET runtime is absent."""

from __future__ import annotations

import json
import random
from pathlib import Path

import pytest

from exfat_forge import ufs

pytestmark = pytest.mark.skipif(
    not (ufs.tool_available() and ufs.dotnet_status().available),
    reason="UFS2Tool or the .NET 8 runtime is unavailable")


@pytest.fixture()
def dump(tmp_path: Path) -> Path:
    rng = random.Random(99)
    src = tmp_path / "PPSA12345-app0"
    (src / "sce_sys").mkdir(parents=True)
    (src / "sce_sys" / "param.json").write_text(
        json.dumps({"titleId": "PPSA12345"}), encoding="utf-8")
    (src / "eboot.bin").write_bytes(rng.randbytes(250_000))
    (src / "assets").mkdir()
    for i in range(6):
        (src / "assets" / f"a{i}.dat").write_bytes(rng.randbytes(120_000))
    return src


def test_dotnet_prefers_net8() -> None:
    st = ufs.dotnet_status()
    assert st.available and st.version
    installed8 = "8." in "".join(st.detail)
    if installed8:
        assert st.version.startswith("8."), "should not roll forward past 8.x"


def test_build_info_list_extract_roundtrip(dump: Path, tmp_path: Path) -> None:
    out = tmp_path / "out"
    out.mkdir()
    image = ufs.build_ffpkg(dump, out)
    assert image.name == "PPSA12345.ffpkg"
    assert not list(out.glob("*.part")), "partial must not survive"

    info = ufs.info_ffpkg(image)
    assert "Valid UFS" in info.get("Magic", "")

    entries = ufs.list_ffpkg(image)
    assert any("eboot.bin" in e for e in entries)

    dest = tmp_path / "unpacked"
    ufs.extract_ffpkg(image, dest)
    for src_file in dump.rglob("*"):
        if src_file.is_file():
            rel = src_file.relative_to(dump)
            assert (dest / rel).read_bytes() == src_file.read_bytes(), rel


def test_build_failure_leaves_no_partial(tmp_path: Path) -> None:
    missing = tmp_path / "does-not-exist"
    out = tmp_path / "out"
    out.mkdir()
    with pytest.raises(Exception):
        ufs.build_ffpkg(missing, out / "x.ffpkg")
    assert not list(out.glob("*")), "no leftovers on failure"
