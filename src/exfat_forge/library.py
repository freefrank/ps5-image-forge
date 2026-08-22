"""Game library: discover source dumps and built images on disk.

Replaces the old tool's Library / My Images / Dumps tabs. Scanning is pure
filesystem inspection — a dump is a directory containing ``eboot.bin``, an
image is a file with a known extension — plus ``sce_sys/param.json`` for
titles. Results are cached by (path, mtime) so re-opening the tab is cheap.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from .core import read_param_json

IMAGE_EXTS = {".exfat", ".ffpkg", ".ffpfsc", ".ffpfs"}

FORMAT_BY_EXT = {
    ".exfat": "exfat",
    ".ffpkg": "ffpkg",
    ".ffpfsc": "pfs",
    ".ffpfs": "pfs",
}


@dataclass
class DumpEntry:
    """A source game dump directory."""

    path: str
    name: str
    title_id: str
    title: str
    version: str
    size_bytes: int
    file_count: int
    has_eboot: bool
    modified: str


@dataclass
class ImageEntry:
    """A built image file."""

    path: str
    name: str
    fmt: str
    size_bytes: int
    modified: str
    title_id: str = ""


def _mtime(path: Path) -> str:
    try:
        return datetime.fromtimestamp(path.stat().st_mtime).strftime(
            "%Y-%m-%d %H:%M")
    except OSError:
        return ""


def _tree_size(path: Path, cap_files: int = 400_000) -> tuple[int, int]:
    """Return (total_bytes, file_count), bailing out past ``cap_files``."""
    total = 0
    count = 0
    for root, _dirs, files in os.walk(path):
        for name in files:
            try:
                total += (Path(root) / name).stat().st_size
            except OSError:
                continue
            count += 1
            if count >= cap_files:
                return total, count
    return total, count


def is_dump(path: Path) -> bool:
    return path.is_dir() and (path / "eboot.bin").is_file()


def scan_dump(path: Path, *, measure: bool = True) -> DumpEntry:
    """Describe one dump directory."""
    title_id, title, version = read_param_json(path)
    size, count = _tree_size(path) if measure else (0, 0)
    return DumpEntry(
        path=str(path), name=path.name,
        title_id=title_id or "", title=title or "", version=version or "",
        size_bytes=size, file_count=count,
        has_eboot=(path / "eboot.bin").is_file(),
        modified=_mtime(path))


def scan_image(path: Path) -> ImageEntry:
    """Describe one built image file."""
    try:
        size = path.stat().st_size
    except OSError:
        size = 0
    return ImageEntry(
        path=str(path), name=path.name,
        fmt=FORMAT_BY_EXT.get(path.suffix.lower(), "?"),
        size_bytes=size, modified=_mtime(path),
        title_id=path.stem.split(" ")[0] if path.stem[:4].isalpha() else "")


def scan_folders(folders: list[str], *, depth: int = 2,
                 measure_dumps: bool = True) -> tuple[list[DumpEntry],
                                                      list[ImageEntry]]:
    """Walk ``folders`` (bounded depth) collecting dumps and images.

    A directory that is itself a dump is not descended into — game trees hold
    tens of thousands of files and there is never a dump inside a dump.
    """
    dumps: list[DumpEntry] = []
    images: list[ImageEntry] = []
    seen: set[str] = set()

    def walk(root: Path, level: int) -> None:
        key = str(root).lower()
        if key in seen or level > depth:
            return
        seen.add(key)
        try:
            entries = list(root.iterdir())
        except OSError:
            return
        if is_dump(root):
            dumps.append(scan_dump(root, measure=measure_dumps))
            return
        for item in entries:
            try:
                if item.is_dir():
                    walk(item, level + 1)
                elif item.suffix.lower() in IMAGE_EXTS:
                    images.append(scan_image(item))
            except OSError:
                continue

    for folder in folders:
        path = Path(folder)
        if path.is_dir():
            walk(path, 0)

    dumps.sort(key=lambda d: (d.title or d.name).lower())
    images.sort(key=lambda i: i.modified, reverse=True)
    return dumps, images
