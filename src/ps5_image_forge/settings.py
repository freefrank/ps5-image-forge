"""Persisted settings and build history.

Both live under ``%APPDATA%/ps5-image-forge`` (never next to the exe, which may
sit on read-only media). Writes go through a temp file + atomic replace so a
crash mid-save cannot corrupt the store — the old tool wrote settings in
place and a killed process could leave truncated JSON.
"""

from __future__ import annotations

import json
import os
import tempfile
from dataclasses import asdict, dataclass, field, fields
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


def config_dir() -> Path:
    base = os.environ.get("APPDATA") or os.path.expanduser("~")
    path = Path(base) / "ps5-image-forge"
    path.mkdir(parents=True, exist_ok=True)
    # One-time carry-over from the pre-rename folder so an existing user keeps
    # their settings, history and payload notes after the exFAT Forge → PS5
    # Image Forge rename. Only fills gaps; never overwrites newer data.
    legacy = Path(base) / "exfat-forge"
    if legacy.is_dir() and legacy != path:
        for name in ("settings.json", "history.json", "payload_notes.json"):
            src, dst = legacy / name, path / name
            if src.is_file() and not dst.exists():
                try:
                    dst.write_bytes(src.read_bytes())
                except OSError:
                    pass
    return path


def _atomic_write(path: Path, text: str) -> None:
    fd, tmp = tempfile.mkstemp(dir=str(path.parent), suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write(text)
        os.replace(tmp, path)
    except BaseException:
        Path(tmp).unlink(missing_ok=True)
        raise


@dataclass
class Settings:
    """User preferences. Unknown keys in the file are ignored on load."""

    # paths
    source_dir: str = ""
    output_dir: str = ""
    library_dirs: list[str] = field(default_factory=list)
    # scratch space for IO-heavy work (extract/repack/compress). Empty = beside
    # the target. Point it at an SSD when the library lives on a slow HDD.
    work_dir: str = ""

    # ui
    lang: str = ""            # "" = follow system
    theme_accent: str = "cyan"
    ui_scale: float = 1.0
    reduce_effects: bool = False   # "smooth mode": drop animations/glows

    # exfat
    cluster_size: int = 65536  # 0 = mkpfs auto; default 64 KiB
    verify_after_build: bool = True

    # pfs
    pfs_compress: bool = True
    pfs_level: int = 9
    pfs_threads: int = 0      # 0 = all cores
    keep_intermediate: bool = False

    # ffpkg (UFS)
    ffpkg_block: int = 65536
    ffpkg_frag: int = 65536
    ffpkg_minfree: int = 0

    # ps5 connection
    ps5_host: str = ""
    ps5_ftp_port: int = 2121
    ps5_ftp_path: str = "/data/etaHEN/games/"
    ps5_klog_port: int = 3232
    ps5_payload_port: int = 9021
    ps5_firmware: str = ""       # user-selected; never probed through 9021
    payload_dir: str = ""

    # backport
    backport_dir: str = ""
    backport_target: int = 5

    @classmethod
    def path(cls) -> Path:
        return config_dir() / "settings.json"

    @classmethod
    def load(cls) -> Settings:
        try:
            data = json.loads(cls.path().read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return cls()
        known = {f.name for f in fields(cls)}
        return cls(**{k: v for k, v in data.items() if k in known})

    def save(self) -> None:
        _atomic_write(self.path(),
                      json.dumps(asdict(self), indent=2, ensure_ascii=False))

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)

    def update(self, values: dict[str, Any]) -> None:
        known = {f.name for f in fields(self)}
        for key, value in values.items():
            if key in known:
                setattr(self, key, value)


@dataclass
class HistoryEntry:
    """One completed (or failed) build."""

    timestamp: str
    source: str
    output: str
    fmt: str                  # exfat | ffpkg | pfs
    size_bytes: int
    duration_s: float
    file_count: int
    verified: bool
    status: str               # ok | failed | cancelled
    title_id: str = ""
    title: str = ""
    message: str = ""


class History:
    """Append-only build log, newest first, capped at ``limit`` entries."""

    def __init__(self, limit: int = 200) -> None:
        self.limit = limit
        self.path = config_dir() / "history.json"

    def load(self) -> list[HistoryEntry]:
        try:
            raw = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return []
        known = {f.name for f in fields(HistoryEntry)}
        out = []
        for item in raw:
            if isinstance(item, dict):
                out.append(HistoryEntry(
                    **{k: v for k, v in item.items() if k in known}))
        return out

    def add(self, entry: HistoryEntry) -> None:
        entries = [entry] + self.load()
        _atomic_write(self.path, json.dumps(
            [asdict(e) for e in entries[:self.limit]],
            indent=2, ensure_ascii=False))

    def clear(self) -> None:
        _atomic_write(self.path, "[]")

    @staticmethod
    def now() -> str:
        return datetime.now(timezone.utc).astimezone().strftime(
            "%Y-%m-%d %H:%M:%S")
