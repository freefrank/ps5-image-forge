"""Unified build pipeline — the modern replacement for the old Unibuild tab.

One entry point, :func:`run_job`, covers every path the old tool spread over
Build / Convert / Extract / PFS / ffpkg tabs:

    dump directory ──┬─> .exfat
                     ├─> .ffpkg
                     └─> .ffpfsc   (via an .exfat or .ffpkg intermediate)

    existing image ──┬─> .ffpfsc   (pack straight through, no extraction)
                     └─> directory (extract)

Every job reports through the same :class:`~.core.ProgressEvent` stream, is
cancellable at chunk granularity, writes via ``.part`` + atomic rename, and
records itself in the build history.
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from pathlib import Path

from . import core, ufs
from .core import CancelToken, ProgressEvent, ProgressFn
from .settings import History, HistoryEntry

FORMATS = ("exfat", "ffpkg", "pfs")
EXT_BY_FORMAT = {"exfat": ".exfat", "ffpkg": ".ffpkg", "pfs": ".ffpfsc"}


@dataclass
class JobSpec:
    """A single build/convert request."""

    source: Path                       # dump directory, or an existing image
    output_dir: Path
    fmt: str = "exfat"                 # exfat | ffpkg | pfs
    intermediate: str = "exfat"        # for fmt="pfs": exfat | ffpkg
    verify: bool = True
    # exfat
    cluster_size: int | None = None
    # pfs
    compress: bool = True
    level: int = 9
    threads: int | None = None
    keep_intermediate: bool = False
    # ffpkg
    ffpkg_block: int = 65536
    ffpkg_frag: int = 65536
    ffpkg_minfree: int = 0

    def __post_init__(self) -> None:
        if self.fmt not in FORMATS:
            raise ValueError(f"unknown output format: {self.fmt}")
        if self.intermediate not in ("exfat", "ffpkg"):
            raise ValueError(f"bad intermediate: {self.intermediate}")


@dataclass
class JobResult:
    output: Path
    fmt: str
    size_bytes: int
    duration_s: float
    file_count: int
    verified: bool


def source_kind(path: Path) -> str:
    """``"dump"`` for a directory, ``"image"`` for a known image file."""
    if path.is_dir():
        return "dump"
    if path.suffix.lower() in (".exfat", ".ffpkg", ".ffpfsc", ".ffpfs"):
        return "image"
    raise ValueError(f"unsupported source: {path}")


def default_name(source: Path, fmt: str) -> str:
    """Use ``TITLE_ID_TITLE_VERSION`` metadata when it is available."""
    stem = core.default_output_stem(source)
    return stem + EXT_BY_FORMAT[fmt]


def run_job(spec: JobSpec, *,
            progress: ProgressFn | None = None,
            cancel: CancelToken | None = None,
            record: bool = True) -> JobResult:
    """Execute one job end to end and return where the output landed."""
    started = time.monotonic()
    final = spec.output_dir / default_name(spec.source, spec.fmt)

    title_id = title = ""
    file_count = 0
    verified = False
    status = "ok"
    message = ""

    try:
        # Inside the try so an unusable source is still recorded as a
        # failed job rather than vanishing without a history entry.
        kind = source_kind(spec.source)
        spec.output_dir.mkdir(parents=True, exist_ok=True)
        if kind == "image":
            final = _from_image(spec, final, progress=progress)
        else:
            info = core.scan_source(spec.source, progress, cancel)
            title_id, title = info.title_id or "", info.title or ""
            file_count = info.file_count
            final, verified = _from_dump(spec, final, progress=progress,
                                         cancel=cancel)
    except core.BuildCancelled:
        status = "cancelled"
        raise
    except Exception as exc:
        status, message = "failed", str(exc)
        raise
    finally:
        if record:
            elapsed = time.monotonic() - started
            History().add(HistoryEntry(
                timestamp=History.now(),
                source=str(spec.source), output=str(final),
                fmt=spec.fmt,
                size_bytes=final.stat().st_size if final.exists() else 0,
                duration_s=round(elapsed, 1), file_count=file_count,
                verified=verified, status=status,
                title_id=title_id, title=title, message=message[:300]))

    return JobResult(final, spec.fmt,
                     final.stat().st_size if final.exists() else 0,
                     time.monotonic() - started, file_count, verified)


def _from_dump(spec: JobSpec, final: Path, *,
               progress: ProgressFn | None,
               cancel: CancelToken | None) -> tuple[Path, bool]:
    """Build from a dump directory into the requested format."""
    verified = False

    if spec.fmt == "exfat":
        image = core.build_exfat(spec.source, final,
                                 cluster_size=spec.cluster_size,
                                 progress=progress, cancel=cancel)
        if spec.verify:
            core.verify_image(image, spec.source, progress=progress,
                              cancel=cancel)
            verified = True
        return image, verified

    if spec.fmt == "ffpkg":
        image = ufs.build_ffpkg(spec.source, final,
                                block_size=spec.ffpkg_block,
                                fragment_size=spec.ffpkg_frag,
                                min_free=spec.ffpkg_minfree,
                                progress=progress)
        if spec.verify:
            ufs.info_ffpkg(image)      # parses the superblock; raises if bad
            verified = True
        return image, verified

    # fmt == "pfs": build the chosen intermediate, then pack it
    inter_ext = EXT_BY_FORMAT[spec.intermediate]
    inter_path = final.with_suffix(inter_ext)
    if spec.intermediate == "exfat":
        inter = core.build_exfat(spec.source, inter_path,
                                 cluster_size=spec.cluster_size,
                                 progress=progress, cancel=cancel)
        if spec.verify:
            core.verify_image(inter, spec.source, progress=progress,
                              cancel=cancel)
            verified = True
    else:
        inter = ufs.build_ffpkg(spec.source, inter_path,
                                block_size=spec.ffpkg_block,
                                fragment_size=spec.ffpkg_frag,
                                min_free=spec.ffpkg_minfree,
                                progress=progress)
        if spec.verify:
            ufs.info_ffpkg(inter)
            verified = True

    if cancel:
        cancel.raise_if_cancelled()
    out = core.pack_pfs(inter, final, compress=spec.compress,
                        compression_level=spec.level, threads=spec.threads,
                        progress=progress)
    if not spec.keep_intermediate:
        inter.unlink(missing_ok=True)
    return out, verified


def _from_image(spec: JobSpec, final: Path, *,
                progress: ProgressFn | None) -> Path:
    """Convert an existing image (only PFS packing makes sense here)."""
    if spec.fmt != "pfs":
        raise ValueError(
            "converting an existing image is only supported for PFS output; "
            "extract it to a directory first for other formats")
    return core.pack_pfs(spec.source, final, compress=spec.compress,
                         compression_level=spec.level, threads=spec.threads,
                         progress=progress)


def extract_any(image: Path, dest: Path, *,
                progress: ProgressFn | None = None,
                cancel: CancelToken | None = None,
                overwrite: bool = False) -> int:
    """Extract any supported image to a directory; returns the file count."""
    ext = image.suffix.lower()
    if ext == ".exfat":
        return core.extract_image(image, dest, progress=progress,
                                  cancel=cancel, overwrite=overwrite)
    if ext == ".ffpkg":
        ufs.extract_ffpkg(image, dest, progress=progress)
        return sum(1 for p in dest.rglob("*") if p.is_file())
    if ext in (".ffpfsc", ".ffpfs"):
        return core.extract_pfs(image, dest, progress=progress,
                                overwrite=overwrite)
    raise ValueError(f"cannot extract {image.suffix} images")
