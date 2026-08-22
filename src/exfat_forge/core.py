"""Core build / verify / extract pipeline.

Design notes — every choice here removes a failure mode observed in the
tool this replaces (exFAT Image Builder v4.0.2):

* The image is written directly with :mod:`mkpfs.exfat_writer` — no OSFMount,
  no drive letter (the old tool grabbed whatever letter was next, colliding
  with WSL's Z:), no administrator elevation (whose UAC relaunch silently
  dropped the caller's PATH), and no robocopy (whose localized summary the
  old parser could not read, making it delete finished images on any
  non-English Windows).
* Verification reads the image bytes back with :class:`mkpfs.exfat.ExfatReader`
  and compares the tree against the source directory — no text parsing, no
  locale dependence, no mounting.
* The image is written to ``<name>.exfat.part`` and atomically renamed on
  success, so a crashed or concurrent build can never leave a half-written
  file under the final name — and we never delete anything we did not create
  in this run (the old tool's post-build check deleted a *good* image that a
  second instance was still writing).
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import threading
from collections.abc import Callable, Iterator
from dataclasses import dataclass, field
from pathlib import Path

from mkpfs.exfat import ExfatEntry, ExfatReader, open_exfat
from mkpfs.exfat_writer import iter_exfat_image
from mkpfs.utils import is_ignored_name


class BuildCancelled(Exception):
    """Raised inside the pipeline when the caller requested cancellation."""


class VerifyError(Exception):
    """Raised when the written image does not match the source tree."""


@dataclass
class ScanResult:
    """Summary of a source directory scan."""

    file_count: int
    dir_count: int
    total_bytes: int
    title_id: str | None
    title: str | None
    version: str | None


@dataclass
class ProgressEvent:
    """A single progress update handed to the caller's callback."""

    phase: str            # "scan" | "write" | "verify" | "pfs" | "extract"
    done: int             # bytes (write/verify/extract) or files (scan)
    total: int            # same unit as ``done``; 0 while still unknown
    detail: str = ""      # current file or free-form status text


ProgressFn = Callable[[ProgressEvent], None]


@dataclass
class CancelToken:
    """Cooperative cancellation flag shared between UI and worker threads."""

    _event: threading.Event = field(default_factory=threading.Event)

    def cancel(self) -> None:
        self._event.set()

    @property
    def cancelled(self) -> bool:
        return self._event.is_set()

    def raise_if_cancelled(self) -> None:
        if self._event.is_set():
            raise BuildCancelled()


def read_param_json(source: Path) -> tuple[str | None, str | None, str | None]:
    """Return (title_id, title, version) from sce_sys/param.json, or Nones."""
    param = source / "sce_sys" / "param.json"
    if not param.is_file():
        return None, None, None
    try:
        data = json.loads(param.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None, None, None
    loc = data.get("localizedParameters") or {}
    default_lang = loc.get("defaultLanguage")
    title = None
    if isinstance(default_lang, str):
        title = (loc.get(default_lang) or {}).get("titleName")
    return data.get("titleId"), title, data.get("contentVersion")


def scan_source(source: Path, progress: ProgressFn | None = None,
                cancel: CancelToken | None = None) -> ScanResult:
    """Walk the source tree and return counts, sizes, and title metadata."""
    if not source.is_dir():
        raise FileNotFoundError(f"source directory not found: {source}")
    files = 0
    dirs = 0
    total = 0
    for root, dirnames, filenames in os.walk(source):
        if cancel:
            cancel.raise_if_cancelled()
        dirnames[:] = [d for d in dirnames if not is_ignored_name(d)]
        dirs += len(dirnames)
        for name in filenames:
            if is_ignored_name(name):
                continue
            files += 1
            try:
                total += (Path(root) / name).stat().st_size
            except OSError:
                pass
        if progress and files % 5000 < len(filenames):
            progress(ProgressEvent("scan", files, 0, f"{total / 2**30:.2f} GB"))
    title_id, title, version = read_param_json(source)
    result = ScanResult(files, dirs, total, title_id, title, version)
    if progress:
        progress(ProgressEvent("scan", files, files,
                               f"{total / 2**30:.2f} GB, {dirs} dirs"))
    return result


def default_output_name(source: Path) -> str:
    """``<titleId>.exfat`` when param.json names one, else the folder name."""
    title_id, _, _ = read_param_json(source)
    return f"{title_id or source.name}.exfat"


#: exFAT allocation unit used when the caller does not pick one. mkpfs would
#: otherwise choose per-tree (32K, or 64K for large-average-file sets); PS5
#: game dumps are overwhelmingly large-file, and a fixed 64K keeps cluster
#: overhead low and image sizes reproducible across builds.
DEFAULT_CLUSTER_SIZE = 65536


def build_exfat(source: Path, output: Path, *,
                cluster_size: int | None = None,
                progress: ProgressFn | None = None,
                cancel: CancelToken | None = None) -> Path:
    """Write an exFAT image of ``source`` to ``output`` (file or directory).

    ``cluster_size`` defaults to :data:`DEFAULT_CLUSTER_SIZE` (64 KiB); pass
    an explicit size to override it.

    Writes to ``<output>.part`` first and renames into place only on success,
    so no failure mode can leave a truncated image under the final name.
    """
    if cluster_size is None:
        cluster_size = DEFAULT_CLUSTER_SIZE
    if output.is_dir():
        output = output / default_output_name(source)
    output.parent.mkdir(parents=True, exist_ok=True)
    partial = output.with_name(output.name + ".part")

    total = 0

    def on_layout(size: int) -> None:
        nonlocal total
        total = size
        free = _free_bytes(output.parent)
        if free is not None and free < size:
            raise OSError(
                f"not enough free space on {output.parent}: "
                f"image needs {size / 2**30:.2f} GB, {free / 2**30:.2f} GB free")
        if progress:
            progress(ProgressEvent("write", 0, size))

    written = 0
    last_report = 0
    try:
        with partial.open("wb") as out:
            for chunk in iter_exfat_image(source, cluster_size=cluster_size,
                                          on_layout=on_layout):
                if cancel:
                    cancel.raise_if_cancelled()
                out.write(chunk)
                written += len(chunk)
                if progress and written - last_report >= 32 * 2**20:
                    last_report = written
                    progress(ProgressEvent("write", written, total))
        partial.replace(output)
    except BaseException:
        partial.unlink(missing_ok=True)
        raise
    if progress:
        progress(ProgressEvent("write", total, total))
    return output


def verify_image(image: Path, source: Path | None = None, *,
                 progress: ProgressFn | None = None,
                 cancel: CancelToken | None = None,
                 sample_bytes: int = 64 * 2**10) -> tuple[int, int]:
    """Read the image back and check it; returns (file_count, total_bytes).

    Structural checks (boot signature, FAT chains, directory tree) always run.
    With ``source`` given, the image tree is compared against the source tree:
    same relative paths, same sizes, and the first/last ``sample_bytes`` of
    every file must match the source bytes.  Pure reads — nothing is mounted.
    """
    with image.open("rb") as fh:
        reader = ExfatReader(fh)
        entries = [e for e in reader.iter_files() if not e.is_dir]
        img_files = {e.rel_path.replace("\\", "/"): e for e in entries}
        total = sum(e.length for e in entries)

        if source is not None:
            src_files: dict[str, Path] = {}
            for root, dirnames, filenames in os.walk(source):
                dirnames[:] = [d for d in dirnames if not is_ignored_name(d)]
                for name in filenames:
                    if is_ignored_name(name):
                        continue
                    p = Path(root) / name
                    src_files[p.relative_to(source).as_posix()] = p

            missing = sorted(set(src_files) - set(img_files))
            extra = sorted(set(img_files) - set(src_files))
            if missing or extra:
                raise VerifyError(
                    f"tree mismatch: {len(missing)} missing "
                    f"(first: {missing[:3]}), {len(extra)} unexpected "
                    f"(first: {extra[:3]})")

            checked = 0
            for rel, entry in img_files.items():
                if cancel:
                    cancel.raise_if_cancelled()
                src_path = src_files[rel]
                src_size = src_path.stat().st_size
                if entry.length != src_size:
                    raise VerifyError(
                        f"size mismatch for {rel}: image {entry.length}, "
                        f"source {src_size}")
                if sample_bytes and src_size:
                    head = min(sample_bytes, src_size)
                    img_head = _read_entry_range(reader, entry, 0, head)
                    with src_path.open("rb") as sf:
                        if img_head != sf.read(head):
                            raise VerifyError(f"content mismatch (head) in {rel}")
                        if src_size > head:
                            tail = min(sample_bytes, src_size - head)
                            sf.seek(src_size - tail)
                            src_tail = sf.read(tail)
                            img_tail = _read_entry_range(
                                reader, entry, src_size - tail, tail)
                            if img_tail != src_tail:
                                raise VerifyError(
                                    f"content mismatch (tail) in {rel}")
                checked += entry.length
                if progress and checked % (256 * 2**20) < entry.length:
                    progress(ProgressEvent("verify", checked, total, rel))

    if progress:
        progress(ProgressEvent("verify", total, total,
                               f"{len(img_files)} files OK"))
    return len(img_files), total


def _read_entry_range(reader: ExfatReader, entry: ExfatEntry,
                      offset: int, length: int) -> bytes:
    """Read ``length`` bytes at ``offset`` within a file entry."""
    buf = bytearray()
    pos = 0
    for chunk in reader.read_file(entry):
        end = pos + len(chunk)
        if end > offset:
            start = max(0, offset - pos)
            buf += chunk[start:start + (offset + length - pos - start)]
            if len(buf) >= length:
                break
        pos = end
    return bytes(buf[:length])


def extract_image(image: Path, dest: Path, *,
                  progress: ProgressFn | None = None,
                  cancel: CancelToken | None = None,
                  overwrite: bool = False) -> int:
    """Unpack every file in the image into ``dest``; returns the file count."""
    dest.mkdir(parents=True, exist_ok=True)
    count = 0
    with image.open("rb") as fh:
        reader = ExfatReader(fh)
        entries = [e for e in reader.iter_files() if not e.is_dir]
        total = sum(e.length for e in entries)
        done = 0
        for entry in entries:
            if cancel:
                cancel.raise_if_cancelled()
            rel = entry.rel_path.replace("\\", "/").lstrip("/")
            target = dest / rel
            if target.exists() and not overwrite:
                raise FileExistsError(
                    f"{target} exists (pass overwrite=True to replace)")
            target.parent.mkdir(parents=True, exist_ok=True)
            with target.open("wb") as out:
                for chunk in reader.read_file(entry):
                    out.write(chunk)
                    done += len(chunk)
            count += 1
            if progress:
                progress(ProgressEvent("extract", done, total, rel))
    return count


def pack_pfs(image: Path, output: Path | None = None, *,
             compress: bool = True,
             compression_level: int = 9,
             threads: int | None = None,
             progress: ProgressFn | None = None) -> Path:
    """Convert an .exfat image to .ffpfsc via mkpfs's own CLI.

    Runs ``python -m mkpfs pack file`` as a subprocess so the (large, GPL)
    PFS packer stays a black box behind its supported interface.  PFSC
    block compression (deflate, level 1-9) is on by default — that is what
    the "c" in .ffpfsc stands for; ``compress=False`` writes uncompressed
    blocks instead.  ``threads`` caps the compressor's worker processes
    (default: all cores).
    """
    if output is None:
        output = image.with_suffix(".ffpfsc")
    # In a PyInstaller onefile build, sys.executable is our own exe and
    # ``-m mkpfs`` does not exist; the app entrypoint recognizes a
    # ``--mkpfs`` first argument and forwards to mkpfs's CLI in-process.
    if getattr(sys, "frozen", False):
        cmd = [sys.executable, "--mkpfs"]
    else:
        cmd = [sys.executable, "-m", "mkpfs"]
    cmd += ["pack", "file", str(image), str(output)]
    if compress:
        cmd += ["--compress", "--compression-level", str(compression_level)]
    else:
        cmd += ["--no-compress"]
    if threads:
        cmd += ["--cpu-count", str(threads)]
    if progress:
        progress(ProgressEvent(
            "pfs", 0, 0,
            f"packing {image.name}"
            + (f" (compress L{compression_level})" if compress else "")))
    creationflags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    env = dict(os.environ, PYTHONIOENCODING="utf-8:replace")
    proc = subprocess.run(
        cmd, capture_output=True, text=True,
        encoding="utf-8", errors="replace", creationflags=creationflags,
        env=env)
    if proc.returncode != 0:
        raise RuntimeError(
            f"mkpfs pack failed (exit {proc.returncode}):\n"
            f"{proc.stdout[-2000:]}\n{proc.stderr[-2000:]}")
    if progress:
        progress(ProgressEvent("pfs", 1, 1, output.name))
    return output


def extract_pfs(image: Path, dest: Path, *,
                progress: ProgressFn | None = None,
                overwrite: bool = False) -> int:
    """Unpack a .ffpfsc / .ffpfs image via mkpfs; returns the file count."""
    dest.mkdir(parents=True, exist_ok=True)
    if getattr(sys, "frozen", False):
        cmd = [sys.executable, "--mkpfs"]
    else:
        cmd = [sys.executable, "-m", "mkpfs"]
    cmd += ["unpack", str(image), str(dest), "--no-progress"]
    if overwrite:
        cmd.append("--overwrite")
    if progress:
        progress(ProgressEvent("extract", 0, 0, f"unpacking {image.name}"))
    proc = subprocess.run(
        cmd, capture_output=True, text=True, encoding="utf-8",
        errors="replace",
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        env=dict(os.environ, PYTHONIOENCODING="utf-8:replace"))
    if proc.returncode != 0:
        raise RuntimeError(
            f"mkpfs unpack failed (exit {proc.returncode}):\n"
            f"{proc.stdout[-1500:]}\n{proc.stderr[-1500:]}")
    count = sum(1 for p in dest.rglob("*") if p.is_file())
    if progress:
        progress(ProgressEvent("extract", 1, 1, f"{count:,} files"))
    return count


def _free_bytes(path: Path) -> int | None:
    try:
        import shutil
        return shutil.disk_usage(path).free
    except OSError:
        return None
