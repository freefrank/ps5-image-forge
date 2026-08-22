"""Payload library: discover ELF payloads and read what they declare.

The tool this replaces kept a hand-maintained list — you typed the name and
notes yourself. Here a folder is scanned and each payload is described from
its own bytes: ELF header validity, build id, toolchain, and the readable
strings it carries, from which a name, version and capability guesses are
derived. Anything the file cannot tell us can still be supplied by a sidecar
(``<name>.json`` / ``.txt`` / ``.md``) or by the user's own notes, which are
stored separately and never overwrite what was parsed.
"""

from __future__ import annotations

import json
import re
import struct
from dataclasses import asdict, dataclass, field
from datetime import datetime
from pathlib import Path

from .settings import _atomic_write, config_dir

PAYLOAD_EXTS = {".elf", ".bin"}

# ELF constants we care about
_ET = {1: "REL", 2: "EXEC", 3: "DYN", 4: "CORE"}
_MACHINE = {0x3E: "x86-64", 0xB7: "aarch64"}
_OSABI = {0: "SysV", 9: "FreeBSD", 0x51: "OpenBSD"}

# Capability hints: (label, patterns). Matched against the payload's strings.
_CAPABILITY_HINTS: list[tuple[str, tuple[str, ...]]] = [
    ("ftp", ("ftpsrv", "ftp server", "FTP server", "STOR ", "RETR ")),
    ("klog", ("klog", "kernel log", "/dev/klog")),
    ("mount", ("nmount", "unmount", "mount_", "/mnt/sandbox")),
    ("backport", ("fakelib", "backport", "sce_module", "sprx")),
    ("jailbreak", ("jailbreak", "escape", "sandbox escape", "setuid")),
    ("debug", ("ptrace", "ktrace", "gdb", "debugger")),
    ("elfldr", ("elfldr", "elf loader", "payload loader")),
    ("http", ("libSceHttp", "http://", "https://")),
    ("net", ("libSceNet", "socket", "AF_INET")),
]

# Known payload families — first match wins for the display name.
_KNOWN_NAMES: list[tuple[str, str]] = [
    (r"\bgoldhen\b", "GoldHEN"),
    (r"\betahen\b", "etaHEN"),
    (r"\bbackpork\b", "backpork"),
    (r"\bftpsrv\b", "ftpsrv"),
    (r"\belfldr\b", "elfldr"),
    (r"\bkstuff\b", "kstuff"),
    (r"\bitemzflow\b", "itemzflow"),
    (r"\bshadow ?mount\b", "ShadowMount"),
    (r"\bmicro ?mount\b", "MicroMount"),
]

_VERSION_RE = re.compile(
    r"\bv?(\d+\.\d+(?:\.\d+)?(?:[-_]?(?:a|b|rc|beta|alpha)\d*)?)\b", re.I)


class PayloadError(ValueError):
    """The file is not a payload we can describe."""


@dataclass
class PayloadInfo:
    """Everything known about one payload file."""

    path: str
    name: str
    filename: str
    size_bytes: int
    modified: str

    # ELF facts (empty when the file is not a valid ELF)
    is_elf: bool = False
    elf_class: str = ""          # ELF32 / ELF64
    elf_type: str = ""           # EXEC / DYN ...
    machine: str = ""
    osabi: str = ""
    entry: str = ""
    build_id: str = ""
    toolchain: str = ""
    section_count: int = 0

    version: str = ""
    capabilities: list[str] = field(default_factory=list)
    description: str = ""
    source: str = ""             # where description came from: elf|sidecar|notes
    strings_sample: list[str] = field(default_factory=list)
    warning: str = ""


# ── ELF parsing ───────────────────────────────────────────────────

def _sections(data: bytes) -> dict[str, tuple[int, int]]:
    """Return {section_name: (offset, size)}; empty when unreadable."""
    try:
        e_shoff, = struct.unpack_from("<Q", data, 0x28)
        e_shentsize, e_shnum, e_shstrndx = struct.unpack_from("<HHH", data, 0x3A)
        if not e_shoff or not e_shnum or e_shstrndx >= e_shnum:
            return {}

        def hdr(i: int) -> tuple:
            return struct.unpack_from("<IIQQQQIIQQ", data,
                                      e_shoff + i * e_shentsize)

        _, _, _, _, str_off, str_size, *_ = hdr(e_shstrndx)
        strtab = data[str_off:str_off + str_size]
        out: dict[str, tuple[int, int]] = {}
        for i in range(e_shnum):
            nm, _typ, _fl, _addr, off, size, *_ = hdr(i)
            end = strtab.find(b"\0", nm)
            name = strtab[nm:end if end >= 0 else None].decode("utf-8", "replace")
            if name:
                out[name] = (off, size)
        return out
    except (struct.error, IndexError, ValueError):
        return {}


def _readable_strings(data: bytes, min_len: int = 6,
                      limit: int = 4000) -> list[str]:
    """ASCII runs from the file, de-duplicated, in first-seen order."""
    seen: dict[str, None] = {}
    for match in re.finditer(rb"[\x20-\x7e]{%d,}" % min_len, data):
        s = match.group().decode("ascii", "replace").strip()
        if s and s not in seen:
            seen[s] = None
            if len(seen) >= limit:
                break
    return list(seen)


#: A run of printable bytes inside .text is usually x86 opcodes that happen
#: to be ASCII ("AWAVAUATSPH"). Real text has lowercase words with vowels,
#: or is a path/format string.
_WORDY_RE = re.compile(r"[a-z]{2}[a-z]*[aeiou][a-z]*")


def _is_meaningful(s: str) -> bool:
    """Filter machine-code noise out of the strings shown to the user."""
    if "/" in s or "%" in s or "." in s:
        return bool(re.search(r"[A-Za-z]{3,}", s))
    return bool(_WORDY_RE.search(s))


def _guess_name(filename: str, strings: list[str]) -> str:
    """Prefer a known family name, else a cleaned-up filename."""
    haystack = filename.lower() + "\n" + "\n".join(strings[:600]).lower()
    for pattern, label in _KNOWN_NAMES:
        if re.search(pattern, haystack):
            return label
    stem = Path(filename).stem
    return re.sub(r"[_\-.]+", " ", stem).strip().title() or stem


#: Strings from the build toolchain carry their own version numbers
#: ("Ubuntu clang version 18.1.3") which must never be mistaken for the
#: payload's version.
_TOOLCHAIN_RE = re.compile(
    r"clang|gcc|g\+\+|\bGNU\b|\bLLD\b|Ubuntu|Debian|MSVC|rustc|"
    r"GLIBC|libc\+\+|binutils", re.I)


def _guess_version(filename: str, strings: list[str]) -> str:
    m = _VERSION_RE.search(Path(filename).stem)
    if m:
        return m.group(1)
    # Look only at strings that mention a version-ish word, so random
    # numbers inside unrelated text do not win — and skip anything that
    # smells of the compiler rather than the payload.
    for s in strings[:800]:
        if _TOOLCHAIN_RE.search(s):
            continue
        if re.search(r"\b(version|ver|build|rel)\b", s, re.I):
            m = _VERSION_RE.search(s)
            if m:
                return m.group(1)
    return ""


def _guess_capabilities(strings: list[str]) -> list[str]:
    blob = "\n".join(strings)
    found = []
    for label, needles in _CAPABILITY_HINTS:
        if any(n in blob for n in needles):
            found.append(label)
    return found


def _sidecar(path: Path) -> tuple[str, str]:
    """Read a description from <name>.json/.txt/.md next to the payload."""
    for suffix in (".json", ".txt", ".md"):
        side = path.with_suffix(suffix)
        if not side.is_file():
            continue
        try:
            text = side.read_text(encoding="utf-8", errors="replace").strip()
        except OSError:
            continue
        if suffix == ".json":
            try:
                data = json.loads(text)
                desc = (data.get("description") or data.get("notes")
                        or data.get("about") or "")
                return str(desc), side.name
            except ValueError:
                continue
        if text:
            return text[:2000], side.name
    return "", ""


def describe(path: Path) -> PayloadInfo:
    """Describe one payload file from its own bytes (plus any sidecar)."""
    stat = path.stat()
    info = PayloadInfo(
        path=str(path), name=path.stem, filename=path.name,
        size_bytes=stat.st_size,
        modified=datetime.fromtimestamp(stat.st_mtime).strftime("%Y-%m-%d %H:%M"))

    # Payloads are small; reading the whole file keeps string scanning simple.
    try:
        data = path.read_bytes()
    except OSError as exc:
        info.warning = str(exc)
        return info

    if data[:4] != b"\x7fELF":
        info.warning = "not an ELF file — the PS5 loader will reject it"
        info.name = _guess_name(path.name, [])
        return info

    info.is_elf = True
    info.elf_class = "ELF64" if data[4] == 2 else "ELF32"
    info.osabi = _OSABI.get(data[7], f"0x{data[7]:02x}")
    try:
        e_type, e_machine = struct.unpack_from("<HH", data, 16)
        e_entry, = struct.unpack_from("<Q", data, 24)
        info.elf_type = _ET.get(e_type, f"0x{e_type:x}")
        info.machine = _MACHINE.get(e_machine, f"0x{e_machine:x}")
        info.entry = f"0x{e_entry:x}"
    except struct.error:
        info.warning = "truncated ELF header"
        return info

    sections = _sections(data)
    info.section_count = len(sections)

    if ".note.gnu.build-id" in sections:
        off, size = sections[".note.gnu.build-id"]
        note = data[off:off + size]
        if len(note) > 16:                      # 12-byte note header + "GNU\0"
            info.build_id = note[16:].hex()

    if ".comment" in sections:
        off, size = sections[".comment"]
        parts = [p.decode("utf-8", "replace")
                 for p in data[off:off + size].split(b"\0") if p]
        info.toolchain = "; ".join(parts)[:160]

    # Capability/name detection wants everything (imports live in .dynstr),
    # but the sample shown to the user comes from .rodata where the real
    # human-readable text lives.
    strings = _readable_strings(data)
    info.name = _guess_name(path.name, strings)
    info.version = _guess_version(path.name, strings)
    info.capabilities = _guess_capabilities(strings)

    if ".rodata" in sections:
        off, size = sections[".rodata"]
        sample_pool = _readable_strings(data[off:off + size])
    else:
        sample_pool = strings
    info.strings_sample = [s for s in sample_pool
                           if 8 <= len(s) <= 90 and _is_meaningful(s)][:60]

    desc, origin = _sidecar(path)
    if desc:
        info.description, info.source = desc, origin
    else:
        info.description, info.source = _synthesize(info), "elf"

    if info.machine not in ("x86-64", ""):
        info.warning = f"unexpected architecture {info.machine} for a PS5 payload"
    elif info.osabi not in ("FreeBSD", "SysV"):
        info.warning = f"unusual OS ABI {info.osabi}"
    return info


def _synthesize(info: PayloadInfo) -> str:
    """A one-line summary when nothing else describes the payload."""
    bits = [f"{info.elf_class} {info.elf_type} · {info.machine}"]
    if info.osabi:
        bits.append(info.osabi)
    if info.capabilities:
        bits.append("capabilities: " + ", ".join(info.capabilities))
    return " · ".join(bits)


# ── library ───────────────────────────────────────────────────────

def scan(folder: Path, *, recursive: bool = True) -> list[PayloadInfo]:
    """Describe every payload-looking file under ``folder``."""
    if not folder.is_dir():
        raise PayloadError(f"not a folder: {folder}")
    it = folder.rglob("*") if recursive else folder.glob("*")
    out: list[PayloadInfo] = []
    for path in it:
        try:
            if path.is_file() and path.suffix.lower() in PAYLOAD_EXTS:
                out.append(describe(path))
        except OSError:
            continue
    notes = load_notes()
    for info in out:
        note = notes.get(info.path)
        if note:
            info.description = note
            info.source = "notes"
    out.sort(key=lambda p: p.name.lower())
    return out


def _notes_path() -> Path:
    return config_dir() / "payload_notes.json"


def load_notes() -> dict[str, str]:
    """User-written notes, keyed by absolute payload path."""
    try:
        data = json.loads(_notes_path().read_text(encoding="utf-8"))
        return {str(k): str(v) for k, v in data.items()} if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def save_note(path: str, note: str) -> None:
    """Attach (or clear, with an empty note) a user description."""
    notes = load_notes()
    if note.strip():
        notes[path] = note.strip()
    else:
        notes.pop(path, None)
    _atomic_write(_notes_path(), json.dumps(notes, indent=2, ensure_ascii=False))


def as_dicts(items: list[PayloadInfo]) -> list[dict]:
    return [asdict(i) for i in items]
