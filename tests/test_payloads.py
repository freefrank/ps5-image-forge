"""Payload library tests.

Synthetic ELFs are assembled byte by byte so every parsed field has a known
answer. The real payload on this machine, when present, is used as a
smoke test that the parser survives a genuine PS5 binary.
"""

from __future__ import annotations

import json
import struct
from pathlib import Path

import pytest

from exfat_forge import payloads


@pytest.fixture(autouse=True)
def isolated_config(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("APPDATA", str(tmp_path / "appdata"))


def _make_elf(*, sections: dict[str, bytes] | None = None,
              e_type: int = 3, machine: int = 0x3E, osabi: int = 9,
              entry: int = 0x40) -> bytes:
    """Build a minimal but structurally valid ELF64 with real sections."""
    sections = dict(sections or {})
    names = ["", *sections.keys(), ".shstrtab"]

    shstrtab = bytearray()
    offsets: dict[str, int] = {}
    for n in names:
        offsets[n] = len(shstrtab)
        shstrtab += n.encode() + b"\0"
    sections[".shstrtab"] = bytes(shstrtab)

    ehsize, shentsize = 64, 64
    body = bytearray()
    placed: dict[str, tuple[int, int]] = {}
    for name, blob in sections.items():
        placed[name] = (ehsize + len(body), len(blob))
        body += blob

    shoff = ehsize + len(body)
    ordered = ["", *[n for n in sections if n != ".shstrtab"], ".shstrtab"]

    hdr = bytearray(64)
    hdr[0:4] = b"\x7fELF"
    hdr[4], hdr[5], hdr[6], hdr[7] = 2, 1, 1, osabi
    struct.pack_into("<HHI", hdr, 16, e_type, machine, 1)
    struct.pack_into("<QQQ", hdr, 24, entry, 0, shoff)
    struct.pack_into("<IHHHHHH", hdr, 48, 0, ehsize, 56, 0,
                     shentsize, len(ordered), len(ordered) - 1)

    shdrs = bytearray()
    for name in ordered:
        off, size = placed.get(name, (0, 0))
        sh = bytearray(64)
        struct.pack_into("<IIQQQQIIQQ", sh, 0,
                         offsets.get(name, 0), 1 if name else 0, 0, 0,
                         off, size, 0, 0, 1, 0)
        shdrs += sh
    return bytes(hdr) + bytes(body) + bytes(shdrs)


def _build_id_note(hexid: str) -> bytes:
    raw = bytes.fromhex(hexid)
    return struct.pack("<III", 4, len(raw), 3) + b"GNU\0" + raw


# ── ELF parsing ───────────────────────────────────────────────────

def test_describe_reads_elf_header(tmp_path: Path) -> None:
    p = tmp_path / "test.elf"
    p.write_bytes(_make_elf())
    info = payloads.describe(p)
    assert info.is_elf
    assert info.elf_class == "ELF64" and info.elf_type == "DYN"
    assert info.machine == "x86-64" and info.osabi == "FreeBSD"
    assert info.entry == "0x40" and not info.warning


def test_describe_reads_build_id_and_toolchain(tmp_path: Path) -> None:
    p = tmp_path / "x.elf"
    p.write_bytes(_make_elf(sections={
        ".note.gnu.build-id": _build_id_note("aabbccdd" * 4),
        ".comment": b"\0Ubuntu clang version 18.1.3\0Linker: LLD 22\0",
    }))
    info = payloads.describe(p)
    assert info.build_id == "aabbccdd" * 4
    assert "clang version 18.1.3" in info.toolchain


def test_toolchain_version_is_not_mistaken_for_payload_version(
        tmp_path: Path) -> None:
    """Regression: clang's version leaked out of .comment as the payload's."""
    p = tmp_path / "nover.elf"
    p.write_bytes(_make_elf(sections={
        ".comment": b"\0Ubuntu clang version 18.1.3 (1ubuntu1)\0",
        ".rodata": b"some helpful message here\0",
    }))
    assert payloads.describe(p).version == ""


def test_version_from_filename_and_strings(tmp_path: Path) -> None:
    a = tmp_path / "goldhen-2.4.2.elf"
    a.write_bytes(_make_elf())
    assert payloads.describe(a).version == "2.4.2"

    b = tmp_path / "plain.elf"
    b.write_bytes(_make_elf(sections={".rodata": b"ftpsrv version 1.7b ready\0"}))
    assert payloads.describe(b).version == "1.7b"


def test_known_names_and_capabilities(tmp_path: Path) -> None:
    p = tmp_path / "hen.elf"
    p.write_bytes(_make_elf(sections={
        ".rodata": b"GoldHEN loaded\0ftpsrv listening\0/mnt/sandbox/app0\0",
        ".dynstr": b"\0nmount\0socket\0ptrace\0",
    }))
    info = payloads.describe(p)
    assert info.name == "GoldHEN"
    assert {"ftp", "mount", "debug"} <= set(info.capabilities)


def test_machine_code_noise_filtered_from_sample(tmp_path: Path) -> None:
    """Printable x86 opcode runs must not appear as 'readable strings'."""
    p = tmp_path / "noisy.elf"
    p.write_bytes(_make_elf(sections={
        ".rodata": b"AWAVAUATSPH\0[A\\A]A^A_]\0Directory removed successfully\0",
    }))
    sample = payloads.describe(p).strings_sample
    assert "Directory removed successfully" in sample
    assert not any("AWAVAUATSPH" in s for s in sample)


def test_non_elf_is_flagged_not_crashed(tmp_path: Path) -> None:
    p = tmp_path / "readme.bin"
    p.write_bytes(b"MZ this is a windows binary" * 20)
    info = payloads.describe(p)
    assert not info.is_elf and "not an ELF" in info.warning


def test_wrong_architecture_warns(tmp_path: Path) -> None:
    p = tmp_path / "arm.elf"
    p.write_bytes(_make_elf(machine=0xB7))
    info = payloads.describe(p)
    assert info.machine == "aarch64" and "unexpected architecture" in info.warning


def test_truncated_elf_does_not_crash(tmp_path: Path) -> None:
    p = tmp_path / "cut.elf"
    p.write_bytes(b"\x7fELF\x02\x01\x01\x09" + b"\0" * 6)
    info = payloads.describe(p)
    assert info.is_elf and info.warning


# ── sidecars and notes ────────────────────────────────────────────

def test_sidecar_txt_and_json_描述(tmp_path: Path) -> None:
    a = tmp_path / "a.elf"
    a.write_bytes(_make_elf())
    (tmp_path / "a.txt").write_text("Enables FTP on port 2121", encoding="utf-8")
    info = payloads.describe(a)
    assert info.description == "Enables FTP on port 2121"
    assert info.source == "a.txt"

    b = tmp_path / "b.elf"
    b.write_bytes(_make_elf())
    (tmp_path / "b.json").write_text(
        json.dumps({"description": "Kernel logger"}), encoding="utf-8")
    assert payloads.describe(b).description == "Kernel logger"


def test_synthesized_description_when_nothing_else(tmp_path: Path) -> None:
    p = tmp_path / "bare.elf"
    p.write_bytes(_make_elf())
    info = payloads.describe(p)
    assert info.source == "elf" and "ELF64" in info.description


# ── scanning ──────────────────────────────────────────────────────

def test_scan_folder_finds_payloads_and_applies_notes(tmp_path: Path) -> None:
    (tmp_path / "sub").mkdir()
    (tmp_path / "one.elf").write_bytes(_make_elf())
    (tmp_path / "sub" / "two.bin").write_bytes(_make_elf())
    (tmp_path / "notes.txt").write_text("ignore me", encoding="utf-8")

    found = payloads.scan(tmp_path)
    assert len(found) == 2
    assert not any(f.filename == "notes.txt" for f in found)

    target = found[0].path
    payloads.save_note(target, "my own note")
    again = {f.path: f for f in payloads.scan(tmp_path)}
    assert again[target].description == "my own note"
    assert again[target].source == "notes"

    payloads.save_note(target, "")           # clearing restores the parsed one
    assert payloads.scan(tmp_path)[0].source != "notes"


def test_scan_rejects_missing_folder(tmp_path: Path) -> None:
    with pytest.raises(payloads.PayloadError):
        payloads.scan(tmp_path / "nope")


@pytest.mark.skipif(
    not Path(r"C:\Users\freefrank\Downloads\ps5-backpork.elf").is_file(),
    reason="no real payload available on this machine")
def test_real_ps5_payload() -> None:
    info = payloads.describe(
        Path(r"C:\Users\freefrank\Downloads\ps5-backpork.elf"))
    assert info.is_elf and info.machine == "x86-64" and info.osabi == "FreeBSD"
    assert info.name == "backpork"
    assert info.build_id and "clang" in info.toolchain
    assert "backport" in info.capabilities
    assert info.version == "", "clang's version must not leak through"
    assert any("sprx" in s for s in info.strings_sample)
