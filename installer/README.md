# Installer

The Windows setup is a **silent NSIS self-extractor wrapped around a branded
GUI installer written in Python + pywebview** — the same stack, and the same
cyberpunk shell, as the app itself. NSIS never draws a window; the only UI the
user sees is ours.

```
PS5-Image-Forge-Setup-<version>.exe      NSIS, SilentInstall silent
  └─ $PLUGINSDIR\setup\ps5if-setup.exe   the GUI installer (PyInstaller onedir)
       └─ _internal\payload\             PS5-Image-Forge.exe, LICENSE, VERSION
```

The wrapper extracts, then `ExecWait`s the installer app with
`--self=$EXEPATH` plus whatever the user passed, and propagates the exit code
with `SetErrorLevel`.

## Why `--self`

The installer reproduces itself: the outer exe is copied into the install
folder as `uninstall.exe`, so running the uninstaller re-enters the very same
wrapper. Under NSIS that outer image is no longer the running process — the
running process lives in `$PLUGINSDIR` and is deleted on exit — so the path has
to be handed over explicitly. `$EXEPATH` is also how the app tells install from
uninstall when Add/Remove Programs invokes `uninstall.exe` with no arguments at
all.

## Install target — no admin

| | |
|---|---|
| Files | `%LOCALAPPDATA%\Programs\PS5 Image Forge` |
| Uninstall entry | `HKCU\Software\Microsoft\Windows\CurrentVersion\Uninstall\PS5 Image Forge` |
| Shortcuts | per-user Start Menu, plus an optional desktop shortcut |

`RequestExecutionLevel user`: nothing is written outside the user's profile, so
setup never raises a UAC prompt.

The folder is shown on the ready page and can be changed. An update targets
wherever the previous copy actually lives rather than the default, or the old
one would be orphaned in place.

## Update detection

An existing copy switches setup into update mode: the tag, title and button all
read UPDATE, and the installed version is shown next to the one about to
replace it.

Files decide, not the registry. An install whose keys were lost still has its
files sitting there, and overwriting them while the UI claims a fresh install is
how you end up with two half-versions. The registry only supplies the version
string — when it is missing the page shows `unknown`.

## Running-app handling

If `PS5-Image-Forge.exe` is running, setup **stops and asks the user to close
it**, with a "check again" button. It does not kill anything. The old MUI2
installer ran `taskkill /IM ... /F`, which threw away whatever was in flight —
a long image build, an open FTP transfer — without a word.

Detection is dependency-free: a `tasklist` image-name query, plus an
open-for-write probe on the installed exe (a running image is mapped without
`FILE_SHARE_WRITE`).

## Command line

| Flag | Effect |
|---|---|
| `--self=<path>` | outer exe path; prepended by `setup.nsi`, not for humans |
| `--auto` | unattended, no window — used by CI and by `QuietUninstallString` |
| `--uninstall` | force uninstall mode |

Exit codes: `0` ok, `1` failed, `2` the app is still running, `3` cancelled.
The binary is GUI-subsystem, so unattended runs also write
`%TEMP%\ps5if-setup.log` — that is the only trace a failing CI run leaves.

## Gotchas

Two of these cost a full debug cycle each.

**The window reference on `js_api` must be private.** pywebview walks the
`js_api` object to build the JS-side function table and recurses into it, so a
public `self.window` sends the whole WinForms control tree down the wire: every
call crawls and the real methods are shadowed. It presents as the page loading
but the version never appearing and every button doing nothing. `bridge.py` in
the main app documents the same trap.

**Dragging has to be throttled.** pywebview turns each `mousemove` into a
synchronous IPC call, and a mouse reports 125-1000 times a second; untouched,
the drag saturates the UI thread. `app.js` gates moves to one per 8 ms and
pauses animation while `html.dragging` is set.

**`build_setup.ps1` must keep its BOM.** Windows PowerShell 5.1 reads a BOM-less
file as the host's ANSI codepage. On a CP1252 runner an em dash decodes to a
curly quote, which PowerShell honours as a string delimiter — a string closes
early and the parse dies far away with "the string is missing the terminator".
A dev box on a CJK codepage decodes the same bytes harmlessly, so this only
ever fails in CI.

## Files

| File | Purpose |
|---|---|
| `setup.nsi` | The silent wrapper. UTF-8 **with BOM** — makensis rejects bare UTF-8 with non-ASCII in it. |
| `app/engine.py` | Install / uninstall / running-app detection. Callable headless. |
| `app/main.py` | Entry point: argument parsing, unattended path, pywebview window, js_api. |
| `app/webui/` | The UI — palette, wordmark, grid and scanlines lifted from `src/ps5_image_forge/webui/app.css`. |
| `build_setup.ps1` | One-shot build (below). UTF-8 **with BOM**, and no non-ASCII inside quoted strings — see the note in its header. |
| `make_assets.py` | Generates the branding art in `assets/`. Needs Pillow. |
| `installer.nsi`, `build_installer.ps1` | The previous MUI2 wizard. Kept for reference; nothing builds them. |

`assets/`, `build/` and `dist/` are git-ignored.

## Build

From the repo root:

```powershell
powershell -ExecutionPolicy Bypass -File installer\build_setup.ps1
```

Pass `-Build` to force a fresh PyInstaller build of the app exe first. Steps:
version from `pyproject.toml` → branding art → app exe → payload staging
(`build\setup-payload`, with a size floor so a truncated exe cannot ship) →
installer app (`build\setup-app`) → `makensis`. Output lands in
`dist\PS5-Image-Forge-Setup-<version>.exe` (~43 MB).

### Requirements

- **NSIS 3.x** on `PATH` or at `%ProgramFiles(x86)%\NSIS`
  (`winget install NSIS.NSIS`).
- **Pillow** for the branding art; **PyInstaller** and **pywebview** for the
  builds. The script prefers `.venv\Scripts\python.exe` and falls back to the
  system interpreter for the assets step.

### Manual compile

```powershell
makensis /DSRCDIR=build\setup-app\ps5if-setup `
         /DOUTFILE=dist\PS5-Image-Forge-Setup-0.7.5.exe `
         /DAPPVER=0.7.5 /DICONFILE=installer\assets\installer.ico `
         /DINNER_EXE=ps5if-setup.exe installer\setup.nsi
```

## Uninstalling

`uninstall.exe --auto` removes the shortcuts, the registry entries and the
program files synchronously, then hands the last two operations — deleting the
still-running `uninstall.exe` and its now-empty folder — to a detached command.
NSIS keeps its own image open without `FILE_SHARE_DELETE`, so neither deleting
nor renaming it from inside is possible. Tests must therefore poll for the
folder to disappear rather than trust the exit code alone.
