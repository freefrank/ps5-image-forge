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

import os
import shutil
import tempfile
import time
import zipfile
from dataclasses import dataclass
from pathlib import Path

from . import backport, core, ufs
from .core import CancelToken, ProgressEvent, ProgressFn
from .settings import History, HistoryEntry

IMAGE_SUFFIXES = (".exfat", ".ffpkg", ".ffpfsc", ".ffpfs")

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


def rebuild_image(source_dir: Path, target_image: Path, *,
                  progress: ProgressFn | None = None,
                  cancel: CancelToken | None = None,
                  cluster_size: int | None = None,
                  compress: bool = True, level: int = 9,
                  threads: int | None = None,
                  ffpkg_block: int = 65536, ffpkg_frag: int = 65536,
                  ffpkg_minfree: int = 0) -> Path:
    """Rebuild ``target_image`` from ``source_dir`` in the image's own format.

    Builds beside the target (``*.rebuild.<ext>``) and atomically replaces it
    only on success, so a failed rebuild leaves the original image untouched.
    """
    target_image = Path(target_image)
    ext = target_image.suffix.lower()
    partial = target_image.with_name(
        target_image.stem + ".rebuild" + target_image.suffix)
    partial.unlink(missing_ok=True)
    try:
        if ext == ".exfat":
            core.build_exfat(source_dir, partial, cluster_size=cluster_size,
                             progress=progress, cancel=cancel)
        elif ext == ".ffpkg":
            ufs.build_ffpkg(source_dir, partial, block_size=ffpkg_block,
                            fragment_size=ffpkg_frag, min_free=ffpkg_minfree,
                            progress=progress)
        elif ext in (".ffpfsc", ".ffpfs"):
            inter = target_image.with_name(target_image.stem + ".rebuild.exfat")
            inter.unlink(missing_ok=True)
            try:
                core.build_exfat(source_dir, inter, cluster_size=cluster_size,
                                 progress=progress, cancel=cancel)
                if cancel:
                    cancel.raise_if_cancelled()
                core.pack_pfs(inter, partial, compress=compress,
                              compression_level=level, threads=threads,
                              progress=progress)
            finally:
                inter.unlink(missing_ok=True)
        else:
            raise ValueError(f"cannot rebuild {ext} images")
        os.replace(partial, target_image)
    except BaseException:
        partial.unlink(missing_ok=True)
        raise
    return target_image


def _same_bytes(left: Path, right: Path) -> bool:
    if left.stat().st_size != right.stat().st_size:
        return False
    with left.open("rb") as lf, right.open("rb") as rf:
        while True:
            a, b = lf.read(1024 * 1024), rf.read(1024 * 1024)
            if a != b:
                return False
            if not a:
                return True


def _snapshot_originals(tree: Path, rels: list[str], staging: Path) -> None:
    """Copy the current bytes of ``rels`` (those that exist) into ``staging``."""
    for rel in rels:
        src = tree / rel
        if src.is_file():
            dest = staging / rel
            dest.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(src, dest)


def _write_change_backup(image: Path, staging: Path, tree: Path,
                         snapped: list[str]) -> str:
    """Zip only the originals that ``mutate`` actually changed, beside ``image``.

    A game ROM can be 100 GB+, so the backup never copies the whole image —
    just the pre-change bytes of the touched files, enough to overlay back as a
    patch and undo the edit. Returns the backup path, or ``""`` if nothing
    changed.
    """
    changed = [rel for rel in snapped
               if (staging / rel).is_file() and (tree / rel).is_file()
               and not _same_bytes(staging / rel, tree / rel)]
    if not changed:
        return ""
    sequence = 0
    while True:
        tag = ".bak.zip" if sequence == 0 else f".bak.{sequence}.zip"
        backup_path = image.with_name(image.stem + tag)
        if not backup_path.exists():
            break
        sequence += 1
    fd, temp_name = tempfile.mkstemp(prefix=backup_path.name + ".",
                                     suffix=".tmp", dir=str(image.parent))
    os.close(fd)
    temp_path = Path(temp_name)
    try:
        with zipfile.ZipFile(temp_path, "w", compression=zipfile.ZIP_DEFLATED,
                             compresslevel=6, allowZip64=True) as bundle:
            for rel in changed:
                bundle.write(staging / rel, arcname=rel)
        os.replace(temp_path, backup_path)
    finally:
        temp_path.unlink(missing_ok=True)
    return str(backup_path)


def _edit_tree(image: Path, tree: Path, mutate, affected_rels, *,
               backup: bool) -> dict:
    """Snapshot affected originals, run ``mutate``, and back up what changed."""
    staging = Path(tempfile.mkdtemp(prefix="exfat_forge_bak_",
                                    dir=str(image.parent)))
    try:
        snapped = list(affected_rels(tree)) if backup else []
        _snapshot_originals(tree, snapped, staging)
        result = mutate(tree)
        if backup:
            backup_path = _write_change_backup(image, staging, tree, snapped)
            if backup_path:
                result = {**result, "backup_path": backup_path}
    finally:
        shutil.rmtree(staging, ignore_errors=True)
    return result


def _edit_image_in_place(image: Path, mutate, affected_rels, *,
                         backup: bool,
                         progress: ProgressFn | None,
                         cancel: CancelToken | None,
                         compress: bool = True, level: int = 9,
                         threads: int | None = None) -> tuple[dict, Path]:
    """Extract ``image``, run ``mutate`` on the game tree, and rebuild in place.

    ``mutate(tree_dir)`` edits the game files and returns an auditable result
    dict; ``affected_rels(tree_dir)`` names the relative paths the edit may
    touch, so only their pre-change bytes are backed up (never the whole
    image). exfat/pfs/ffpkg are compressed or read-only containers, so the
    only safe way to change files inside them is unpack → edit → repack.

    A ``.ffpfsc``/``.ffpfs`` wraps a single exfat image rather than a file
    tree, so it is unwrapped one extra layer: the inner exfat is edited, then
    re-packed into PFS.
    """
    image = Path(image)
    if not image.is_file():
        raise backport.BackportError(f"image does not exist: {image}")
    ext = image.suffix.lower()
    if ext not in IMAGE_SUFFIXES:
        raise backport.BackportError(f"unsupported image: {image.suffix}")

    if ext in (".ffpfsc", ".ffpfs"):
        work = Path(tempfile.mkdtemp(prefix="exfat_forge_pfs_",
                                     dir=str(image.parent)))
        try:
            extract_any(image, work, progress=progress, cancel=cancel,
                        overwrite=True)
            inners = [p for p in work.iterdir()
                      if p.is_file() and p.suffix.lower() == ".exfat"]
            if len(inners) != 1:
                raise backport.BackportError(
                    "unexpected PFS payload (not a single exfat image)")
            tree = Path(tempfile.mkdtemp(prefix="exfat_forge_tree_",
                                         dir=str(image.parent)))
            try:
                extract_any(inners[0], tree, progress=progress, cancel=cancel,
                            overwrite=True)
                # Snapshot/backup keys off the outer PFS image's name.
                result = _edit_tree(image, tree, mutate, affected_rels,
                                    backup=backup)
                if cancel:
                    cancel.raise_if_cancelled()
                rebuild_image(tree, inners[0], progress=progress, cancel=cancel)
            finally:
                shutil.rmtree(tree, ignore_errors=True)
            partial = image.with_name(image.stem + ".rebuild" + image.suffix)
            partial.unlink(missing_ok=True)
            try:
                core.pack_pfs(inners[0], partial, compress=compress,
                              compression_level=level, threads=threads,
                              progress=progress)
                os.replace(partial, image)
            except BaseException:
                partial.unlink(missing_ok=True)
                raise
        finally:
            shutil.rmtree(work, ignore_errors=True)
        return result, image

    tree = Path(tempfile.mkdtemp(prefix="exfat_forge_edit_",
                                 dir=str(image.parent)))
    try:
        extract_any(image, tree, progress=progress, cancel=cancel,
                    overwrite=True)
        result = _edit_tree(image, tree, mutate, affected_rels, backup=backup)
        if cancel:
            cancel.raise_if_cancelled()
        rebuild_image(tree, image, progress=progress, cancel=cancel,
                      compress=compress, level=level, threads=threads)
    finally:
        shutil.rmtree(tree, ignore_errors=True)
    return result, image


def _candidate_rels(tree: Path) -> list[str]:
    return [c.relative_to(tree).as_posix() for c in backport.candidates(tree)]


def backport_image(image: Path, target: int, *,
                   backup: bool = True,
                   progress: ProgressFn | None = None,
                   cancel: CancelToken | None = None,
                   compress: bool = True, level: int = 9,
                   threads: int | None = None) -> dict:
    """SDK-downgrade every eligible executable inside an image, in place."""
    def mutate(tree: Path) -> dict:
        return backport.patch_folder(tree, int(target), backup=False)

    result, rebuilt = _edit_image_in_place(
        image, mutate, _candidate_rels, backup=backup, progress=progress,
        cancel=cancel, compress=compress, level=level, threads=threads)
    return {**result, "image": str(rebuilt)}


def overwrite_image(image: Path, patch: Path, *,
                    backup: bool = True,
                    progress: ProgressFn | None = None,
                    cancel: CancelToken | None = None,
                    compress: bool = True, level: int = 9,
                    threads: int | None = None) -> dict:
    """Overlay a user ZIP/folder onto the files inside an image, in place."""
    patch = Path(patch)
    members = backport.patch_member_paths(patch)   # validates up front

    def mutate(tree: Path) -> dict:
        return backport.apply_overwrite(tree, patch, progress=progress)

    result, rebuilt = _edit_image_in_place(
        image, mutate, lambda _tree: members, backup=backup, progress=progress,
        cancel=cancel, compress=compress, level=level, threads=threads)
    return {**result, "image": str(rebuilt)}


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
