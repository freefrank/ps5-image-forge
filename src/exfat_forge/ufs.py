"""`.ffpkg` (UFS1/UFS2) support via the bundled UFS2Tool (.NET 8).

UFS2Tool ships as three .NET assemblies. Its ``UFS2Tool.exe`` launcher
carries a ``requireAdministrator`` manifest — but that is only needed for
the Dokan *mount* feature, which we never use. Invoking the assembly as
``dotnet UFS2Tool.dll <cmd>`` bypasses the launcher manifest entirely, so
image building/extraction runs unelevated, matching the rest of this tool.

The only external requirement is the .NET 8 runtime; :func:`dotnet_status`
reports whether it is present so the UI can explain what is missing rather
than failing mid-build.
"""

from __future__ import annotations

import os
import re
import shutil
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

from .core import ProgressEvent, ProgressFn


class UfsError(RuntimeError):
    """UFS2Tool failed, or its runtime is unavailable."""


def _tool_dir() -> Path:
    if getattr(sys, "frozen", False):
        return Path(sys._MEIPASS) / "ufs2tool"  # type: ignore[attr-defined]
    return Path(__file__).resolve().parent.parent.parent / "vendor" / "ufs2tool"


def tool_available() -> bool:
    return (_tool_dir() / "UFS2Tool.dll").is_file()


@dataclass
class DotnetStatus:
    """Result of probing the host for a usable .NET runtime."""

    available: bool
    version: str | None
    detail: str


def dotnet_status() -> DotnetStatus:
    """Check for a .NET 8+ runtime (Microsoft.NETCore.App)."""
    exe = shutil.which("dotnet")
    if not exe:
        return DotnetStatus(False, None, "dotnet not found on PATH")
    try:
        proc = subprocess.run([exe, "--list-runtimes"], capture_output=True,
                              text=True, encoding="utf-8", errors="replace",
                              timeout=20,
                              creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    except (OSError, subprocess.SubprocessError) as exc:
        return DotnetStatus(False, None, f"dotnet probe failed: {exc}")
    versions = [m.group(1) for m in
                re.finditer(r"Microsoft\.NETCore\.App (\d+\.\d+\.\d+)", proc.stdout)]
    usable = [v for v in versions if int(v.split(".")[0]) >= 8]
    if not usable:
        return DotnetStatus(
            False, None,
            "no Microsoft.NETCore.App 8+ runtime "
            f"(found: {', '.join(versions) or 'none'})")
    # Prefer the runtime the assembly targets (8.x); roll forward only if
    # no 8.x is installed.
    def _key(v: str) -> list[int]:
        return [int(p) for p in v.split(".")]
    eight = [v for v in usable if v.startswith("8.")]
    best = sorted(eight or usable, key=_key)[-1]
    return DotnetStatus(True, best, f".NET runtime {best}")


def _run(args: list[str], progress: ProgressFn | None = None,
         phase: str = "ffpkg") -> str:
    """Run a UFS2Tool command, streaming its output into ``progress``."""
    if not tool_available():
        raise UfsError("UFS2Tool assemblies are missing from this build")
    status = dotnet_status()
    if not status.available:
        raise UfsError(f"ffpkg support needs the .NET 8 runtime — {status.detail}")

    tool = _tool_dir()
    cmd = ["dotnet", str(tool / "UFS2Tool.dll"), *args]
    lines: list[str] = []
    proc = subprocess.Popen(
        cmd, cwd=str(tool), stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
        text=True, encoding="utf-8", errors="replace", bufsize=1,
        env=dict(os.environ, DOTNET_CLI_TELEMETRY_OPTOUT="1",
                 DOTNET_NOLOGO="1"),
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    assert proc.stdout is not None
    for line in proc.stdout:
        line = line.rstrip("\n")
        lines.append(line)
        if progress and line.strip():
            pct = re.search(r"(\d{1,3})\s*%", line)
            done = int(pct.group(1)) if pct else 0
            progress(ProgressEvent(phase, done, 100 if pct else 0,
                                   line.strip()[:120]))
    proc.wait()
    out = "\n".join(lines)
    if proc.returncode != 0:
        raise UfsError(f"UFS2Tool {args[0]} failed (exit {proc.returncode}):\n"
                       f"{out[-1500:]}")
    return out


def build_ffpkg(source: Path, output: Path, *,
                block_size: int = 65536,
                fragment_size: int = 65536,
                min_free: int = 0,
                density: int | None = None,
                label: str | None = None,
                progress: ProgressFn | None = None) -> Path:
    """Build a `.ffpkg` (UFS) image from a source directory.

    Uses ``makefs``, which sizes the image from the tree automatically.
    Written to ``<output>.part`` and renamed on success, same as the exFAT
    path, so a failure never leaves a half-image under the real name.
    """
    if output.is_dir():
        from .core import read_param_json
        title_id, _, _ = read_param_json(source)
        output = output / f"{title_id or source.name}.ffpkg"
    output.parent.mkdir(parents=True, exist_ok=True)
    partial = output.with_name(output.name + ".part")
    partial.unlink(missing_ok=True)

    fs_opts = [f"bsize={block_size}", f"fsize={fragment_size}",
               f"minfree={min_free}", "softupdates=0"]
    if density:
        fs_opts.append(f"density={density}")
    if label:
        fs_opts.append(f"label={label}")

    # makefs' auto-sizing leaves no room for UFS metadata overhead at large
    # block sizes and dies with "no more cylinder groups available" partway
    # through. Size the image ourselves from the payload with headroom, and
    # retry with more if the tool still says it is short.
    payload = sum(p.stat().st_size for p in source.rglob("*") if p.is_file())
    base = max(payload, 1 << 20)
    # Headroom is proportional (metadata scales with the tree) plus a small
    # floor for the superblock/cylinder-group structures on tiny images.
    slack = max(8 * 2**20, int(base * 0.02))
    try:
        last: Exception | None = None
        for factor in (1.15, 1.45, 2.00):
            size = int(base * factor) + slack
            size += (-size) % block_size
            partial.unlink(missing_ok=True)
            try:
                _run(["makefs", "-s", str(size), "-o", ",".join(fs_opts),
                      str(partial), str(source)], progress=progress)
                break
            except UfsError as exc:
                if "too small" not in str(exc):
                    raise
                last = exc
        else:
            raise UfsError(f"could not size the UFS image for {source}: {last}")
        partial.replace(output)
    except BaseException:
        partial.unlink(missing_ok=True)
        raise
    return output


def extract_ffpkg(image: Path, dest: Path, *,
                  progress: ProgressFn | None = None) -> Path:
    """Extract an entire `.ffpkg` image into ``dest``."""
    dest.mkdir(parents=True, exist_ok=True)
    _run(["extract", str(image), str(dest)], progress=progress,
         phase="extract")
    return dest


def info_ffpkg(image: Path) -> dict[str, str]:
    """Return UFS2Tool's ``info`` output as a key/value mapping."""
    out = _run(["info", str(image)])
    data: dict[str, str] = {}
    for line in out.splitlines():
        if ":" in line:
            key, _, value = line.partition(":")
            key, value = key.strip(), value.strip()
            if key and value:
                data[key] = value
    return data


def list_ffpkg(image: Path, path: str = "/") -> list[str]:
    """List a directory inside a `.ffpkg` image."""
    return [ln for ln in _run(["ls", str(image), path]).splitlines() if ln.strip()]


def fsck_ffpkg(image: Path) -> str:
    """Run a non-interactive consistency check over the image."""
    return _run(["fsck_ufs", "-n", str(image)])
