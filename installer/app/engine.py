"""Install / uninstall mechanics for the PS5 Image Forge setup app.

No admin rights anywhere: the app lands in %LOCALAPPDATA%\\Programs and the
Add/Remove Programs entry goes to HKCU, so setup never triggers a UAC prompt.

The payload (the app exe, LICENSE, a VERSION stamp) is embedded in this
program's own bundle by installer/build_setup.ps1 and read back out of
``payload/``.

Everything here is callable headless — the GUI in ``main.py`` only supplies the
``log`` / ``progress`` callbacks and the answers to the two questions the user
gets asked (desktop shortcut? app still running?).
"""

from __future__ import annotations

import ctypes
import os
import shutil
import subprocess
import sys
import time
import winreg
from pathlib import Path
from typing import Callable, Iterable

PRODUCT = "PS5 Image Forge"
APP_EXE = "PS5-Image-Forge.exe"
UNINST_EXE = "uninstall.exe"
PUBLISHER = "freefrank"
WEB_SITE = "https://github.com/freefrank/ps5-image-forge"

REG_UNINSTALL = rf"Software\Microsoft\Windows\CurrentVersion\Uninstall\{PRODUCT}"
REG_APP = rf"Software\{PRODUCT}"

# Files staged into payload/ that describe the payload rather than belong to
# the installed program.
_META_FILES = {"VERSION"}

# CreateProcess flag: this app is GUI-subsystem, so any child console process
# would otherwise pop a black window over the installer for a fraction of a
# second.
_NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0x08000000)

Logger = Callable[[str, str], None]
Progress = Callable[[int, str], None]


class AppRunningError(RuntimeError):
    """Raised instead of killing the app; the GUI turns this into a retry."""


def _noop_log(message: str, kind: str = "") -> None:
    pass


def _noop_progress(pct: int, phase: str) -> None:
    pass


# --------------------------------------------------------------------- paths

def payload_dir() -> Path:
    """Where the embedded copy of the app lives inside this bundle."""
    if getattr(sys, "frozen", False):
        return Path(sys._MEIPASS) / "payload"  # type: ignore[attr-defined]
    # Running from source: fall back to the repo's build staging dir, then dist.
    here = Path(__file__).resolve().parents[2]
    staged = here / "build" / "setup-payload"
    return staged if staged.is_dir() else here / "dist"


def payload_files() -> list[Path]:
    src = payload_dir()
    if not src.is_dir():
        return []
    return sorted(p for p in src.iterdir()
                  if p.is_file() and p.name not in _META_FILES)


def version() -> str:
    stamp = payload_dir() / "VERSION"
    try:
        return stamp.read_text(encoding="utf-8").strip() or "0.0.0"
    except OSError:
        return "0.0.0"


def install_dir() -> Path:
    base = os.environ.get("LOCALAPPDATA") or str(Path.home() / "AppData" / "Local")
    return Path(base) / "Programs" / PRODUCT


def _shell_folder(csidl: int) -> Path:
    """CSIDL over %USERPROFILE% guesswork: Desktop and Start Menu are routinely
    redirected (OneDrive, roaming profiles) and a hardcoded path lands in a
    folder nobody looks at."""
    buf = ctypes.create_unicode_buffer(260)
    ctypes.windll.shell32.SHGetFolderPathW(None, csidl, None, 0, buf)
    return Path(buf.value)


def start_menu_dir() -> Path:
    return _shell_folder(0x0002) / PRODUCT          # CSIDL_PROGRAMS


def desktop_link() -> Path:
    return _shell_folder(0x0010) / f"{PRODUCT}.lnk"  # CSIDL_DESKTOPDIRECTORY


def installed_size() -> int:
    dest = registered_dir() or install_dir()
    if not dest.is_dir():
        return 0
    return sum(p.stat().st_size for p in dest.rglob("*") if p.is_file())


def registered_dir() -> Path | None:
    """The location a previous install recorded, if any."""
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, REG_APP) as key:
            return Path(str(winreg.QueryValueEx(key, "InstallDir")[0]))
    except OSError:
        return None


def installed_version() -> str | None:
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, REG_APP) as key:
            return str(winreg.QueryValueEx(key, "Version")[0])
    except OSError:
        return None


def existing_install() -> Path | None:
    """Where a previous copy lives, registry entry or not.

    The registry alone is not enough: an install whose keys were lost still has
    its files sitting there, and overwriting them silently while the UI claims
    this is a fresh install is how you end up with two half-versions.
    """
    for candidate in (registered_dir(), install_dir()):
        if candidate and (candidate / APP_EXE).is_file():
            return candidate
    return None


# ------------------------------------------------------------ running checks

def running_processes() -> list[str]:
    """Image names of live PS5-Image-Forge.exe processes.

    tasklist rather than psutil: the setup app must stay dependency-free so its
    bundle is not another 10 MB of the user's download.
    """
    try:
        out = subprocess.run(
            ["tasklist", "/FI", f"IMAGENAME eq {APP_EXE}", "/NH", "/FO", "CSV"],
            capture_output=True, text=True, timeout=20, creationflags=_NO_WINDOW,
        ).stdout
    except (OSError, subprocess.SubprocessError):
        return []
    # No match prints "INFO: No tasks are running ..." — which does not contain
    # the image name, so a plain substring test is enough.
    return [line for line in out.splitlines() if APP_EXE.lower() in line.lower()]


def _write_locked(path: Path) -> bool:
    """True if the file cannot be opened for writing.

    A running exe is mapped by the loader without FILE_SHARE_WRITE, so this
    catches an instance started from the install dir even in the (unlikely)
    case tasklist is unavailable or the image was renamed.
    """
    try:
        fd = os.open(path, os.O_RDWR | os.O_BINARY)
    except FileNotFoundError:
        return False
    except OSError:
        return True
    os.close(fd)
    return False


def app_is_running(target: Path | None = None) -> bool:
    """Whether PS5 Image Forge is running and would block install/uninstall.

    Deliberately does NOT kill anything. The previous NSIS installer ran
    `taskkill /F` here, which threw away whatever the user had in flight —
    a long image build, an open FTP transfer — with no warning at all.
    """
    if running_processes():
        return True
    exe = (target or install_dir()) / APP_EXE
    return _write_locked(exe)


# ---------------------------------------------------------------- shortcuts

def _make_shortcuts(specs: Iterable[tuple[Path, Path, str]]) -> None:
    """Create .lnk files via WScript.Shell.

    A .lnk is an IShellLink COM object; there is no stdlib way to write one and
    pulling in pywin32 just for this would double the bundle. One PowerShell
    invocation covers all of them, so the ~1 s of shell startup is paid once.
    """
    specs = list(specs)
    if not specs:
        return
    lines = ["$w = New-Object -ComObject WScript.Shell"]
    for lnk, target, desc in specs:
        lnk.parent.mkdir(parents=True, exist_ok=True)
        lines += [
            f"$s = $w.CreateShortcut('{lnk}')",
            f"$s.TargetPath = '{target}'",
            f"$s.WorkingDirectory = '{target.parent}'",
            f"$s.IconLocation = '{target},0'",
            f"$s.Description = '{desc}'",
            "$s.Save()",
        ]
    subprocess.run(
        ["powershell", "-NoProfile", "-NonInteractive", "-Command", "; ".join(lines)],
        capture_output=True, text=True, timeout=120, creationflags=_NO_WINDOW,
        check=True,
    )


# ------------------------------------------------------------------- install

def install(*, desktop: bool = False, self_image: Path | None = None,
            dest: Path | None = None,
            log: Logger = _noop_log, progress: Progress = _noop_progress) -> Path:
    """Copy the payload into place, register it, and return the install dir.

    ``self_image`` is the outer setup exe (handed over as --self= by setup.nsi).
    It is copied in as uninstall.exe so the uninstaller re-enters this very same
    wrapper — one binary, one code path, nothing to keep in sync.

    ``dest`` overrides the default per-user location when the user picks one.
    """
    dest = Path(dest) if dest else install_dir()
    files = payload_files()
    if not files:
        raise RuntimeError(f"installer payload is empty ({payload_dir()})")

    if app_is_running(dest):
        raise AppRunningError(PRODUCT)

    progress(5, "PREPARE")
    log(f"Target: {dest}", "")
    dest.mkdir(parents=True, exist_ok=True)

    # Plain overwrite, no wipe: an upgrade must not touch anything the user put
    # in the folder, and the app's own state lives under %APPDATA% anyway.
    span = 70
    for i, src in enumerate(files):
        out = dest / src.name
        log(f"Writing {src.name} ({src.stat().st_size / 1048576:.1f} MB)", "")
        shutil.copy2(src, out)
        progress(5 + int(span * (i + 1) / len(files)), "COPY")

    if self_image and self_image.is_file():
        log(f"Writing {UNINST_EXE}", "")
        shutil.copy2(self_image, dest / UNINST_EXE)
    else:
        log("No outer setup image (--self) — uninstall.exe not written", "warn")

    progress(82, "SHORTCUTS")
    app = dest / APP_EXE
    links = [(start_menu_dir() / f"{PRODUCT}.lnk", app, f"{PRODUCT} — PS5 dump image toolkit")]
    if (dest / UNINST_EXE).is_file():
        links.append((start_menu_dir() / f"Uninstall {PRODUCT}.lnk",
                      dest / UNINST_EXE, f"Remove {PRODUCT}"))
    if desktop:
        links.append((desktop_link(), app, PRODUCT))
    _make_shortcuts(links)
    log(f"Start Menu: {start_menu_dir()}", "")
    if desktop:
        log("Desktop shortcut created", "")

    progress(92, "REGISTER")
    _write_registry(dest)
    log("Registered in Add/Remove Programs (HKCU)", "ok")

    progress(100, "DONE")
    return dest


def _write_registry(dest: Path) -> None:
    size_kb = sum(p.stat().st_size for p in dest.rglob("*") if p.is_file()) // 1024
    uninst = f'"{dest / UNINST_EXE}"'
    with winreg.CreateKey(winreg.HKEY_CURRENT_USER, REG_APP) as key:
        winreg.SetValueEx(key, "InstallDir", 0, winreg.REG_SZ, str(dest))
        winreg.SetValueEx(key, "Version", 0, winreg.REG_SZ, version())
    with winreg.CreateKey(winreg.HKEY_CURRENT_USER, REG_UNINSTALL) as key:
        for name, value in (
            ("DisplayName", PRODUCT),
            ("DisplayVersion", version()),
            ("Publisher", PUBLISHER),
            ("DisplayIcon", str(dest / APP_EXE)),
            ("InstallLocation", str(dest)),
            ("URLInfoAbout", WEB_SITE),
            ("UninstallString", uninst),
            ("QuietUninstallString", f"{uninst} --auto"),
        ):
            winreg.SetValueEx(key, name, 0, winreg.REG_SZ, value)
        for name, value in (("NoModify", 1), ("NoRepair", 1),
                            ("EstimatedSize", size_kb)):
            winreg.SetValueEx(key, name, 0, winreg.REG_DWORD, value)


# ----------------------------------------------------------------- uninstall

def uninstall(*, self_image: Path | None = None, log: Logger = _noop_log,
              progress: Progress = _noop_progress) -> None:
    # Where install() actually put things, which is not the default location
    # when the user picked their own. Fall back to the outer image's own folder
    # (uninstall.exe lives in the install dir) before guessing the default.
    dest = registered_dir()
    if dest is None and self_image and self_image.name.lower() == UNINST_EXE:
        dest = self_image.parent
    if dest is None:
        dest = install_dir()
    if app_is_running(dest):
        raise AppRunningError(PRODUCT)

    progress(10, "SHORTCUTS")
    for lnk in (start_menu_dir() / f"{PRODUCT}.lnk",
                start_menu_dir() / f"Uninstall {PRODUCT}.lnk",
                desktop_link()):
        _unlink(lnk)
    _rmdir(start_menu_dir())
    log("Shortcuts removed", "")

    progress(35, "REGISTRY")
    for root in (REG_UNINSTALL, REG_APP):
        try:
            winreg.DeleteKey(winreg.HKEY_CURRENT_USER, root)
        except OSError:
            pass
    log("Registry entries removed", "")

    progress(55, "FILES")
    # uninstall.exe is the image of the NSIS wrapper running us right now.
    # Everything else goes here and now, with real error reporting; that one
    # file and the emptied folder are handed to _tombstone().
    live = self_image if (self_image and self_image.is_file()
                          and _inside(self_image, dest)) else None
    if dest.is_dir():
        for item in dest.iterdir():
            if live and item.name.lower() == live.name.lower():
                continue
            if item.is_dir():
                _rmtree(item, log)
            else:
                _unlink(item)
    log("Program files removed", "")

    if live:
        _tombstone(dest, live)
        log("uninstall.exe removes itself moments after setup exits", "")
    else:
        _rmtree(dest, log)
        # Loud, not silent: an uninstall that leaves the folder behind is a bug
        # report waiting to happen, and CI has no other way to notice.
        if dest.exists():
            raise RuntimeError(f"{dest} could not be fully removed")
        log(f"Removed {dest}", "ok")
    progress(100, "DONE")


def _inside(path: Path, folder: Path) -> bool:
    try:
        path.resolve().relative_to(folder.resolve())
        return True
    except (OSError, ValueError):
        return False


def _tombstone(dest: Path, image: Path) -> None:
    """Leave a detached command behind to delete the uninstaller and its folder.

    Neither renaming nor deleting works from inside: NSIS keeps its own exe open
    without FILE_SHARE_DELETE, so the usual "a running exe can still be renamed"
    trick fails with a sharing violation (WinError 32). MoveFileEx's
    delay-until-reboot fallback writes to HKLM and needs admin, which a per-user
    install does not have. That leaves the standard Windows self-delete idiom: a
    process that outlives both of ours.

    `rd` without /s only removes an empty directory, so this can never take
    anything with it that the loop above did not already account for. ping is
    the delay because timeout.exe refuses to run without a console.
    """
    DETACHED_PROCESS = 0x00000008   # mutually exclusive with CREATE_NO_WINDOW
    # One raw command line, not an argv list: list2cmdline escapes the inner
    # quotes as \" and cmd.exe does not understand that escape, so the paths
    # arrive split on their spaces and nothing gets deleted.
    subprocess.Popen(
        f'cmd /c ping -n 4 127.0.0.1 >nul & del /f /q "{image}" & rd /q "{dest}"',
        creationflags=DETACHED_PROCESS, close_fds=True,
    )


def _unlink(path: Path) -> None:
    try:
        path.unlink()
    except OSError:
        pass


def _rmdir(path: Path) -> None:
    try:
        path.rmdir()
    except OSError:
        pass


def _rmtree(path: Path, log: Logger) -> None:
    """Retry briefly: an antivirus scan or Explorer preview can hold a handle
    for a second or two right after the process that used it exits."""
    for attempt in range(6):
        if not path.exists():
            return
        try:
            shutil.rmtree(path)
            return
        except OSError as exc:
            if attempt == 5:
                log(f"Could not fully remove {path}: {exc}", "warn")
                return
            time.sleep(0.5)
