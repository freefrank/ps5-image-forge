<div align="center">

# PS5 Image Forge

**A toolkit for building, converting, inspecting and managing PS5 game-dump images.**

[![release](https://github.com/freefrank/ps5-image-forge/actions/workflows/release.yml/badge.svg)](https://github.com/freefrank/ps5-image-forge/actions/workflows/release.yml)
[![License: GPL-3.0](https://img.shields.io/badge/License-GPLv3-blue.svg)](LICENSE)
![Platform](https://img.shields.io/badge/platform-Windows%20%7C%20Linux%20%7C%20macOS-8A2BE2)
![Python](https://img.shields.io/badge/python-3.11%2B-3776AB)

English · [简体中文](README.zh-CN.md)

<img src="docs/screenshots/home.png" alt="PS5 Image Forge — Home" width="900">

</div>

---

## Download

Prebuilt binaries are attached to each [release](https://github.com/freefrank/ps5-image-forge/releases) (built automatically when a `v*` tag is pushed):

| Asset | Platform |
|---|---|
| `PS5-Image-Forge-<version>-win64-portable.exe` | **Windows** — portable single file, no install |
| `PS5-Image-Forge-Setup-<version>.exe` | **Windows** — installer |
| `PS5-Image-Forge-<version>-x86_64.AppImage` | **Linux** — AppImage (needs the host's `libwebkit2gtk-4.1`) |
| `PS5-Image-Forge-<version>-macos-arm64.dmg` | **macOS** — DMG, Apple Silicon (unsigned; see `HOW-TO-OPEN-macOS.txt` in the release) |

## Screenshots

<table>
  <tr>
    <td width="50%"><img src="docs/screenshots/build.png" alt="Build"><br><sub><b>Build</b> — dump → exFAT / ffpkg / PFS</sub></td>
    <td width="50%"><img src="docs/screenshots/payload.png" alt="Payloads"><br><sub><b>Payloads</b> — bundled catalog, SHA-256 verified</sub></td>
  </tr>
  <tr>
    <td colspan="2"><img src="docs/screenshots/backport.png" alt="Backport"><br><sub><b>Backport</b> — SELF/FSELF restore, SDK downgrade, fake-sign, verify</sub></td>
  </tr>
</table>

## Features

| Page | Description |
|---|---|
| **Home** | Environment status, shortcuts, recent builds |
| **Build** | dump → exFAT / ffpkg / PFS, with an optional intermediate format and live telemetry |
| **Extract** | Any image format → a directory |
| **Inspect** | Read image structure and file tree, read-only |
| **Library** | Scan and browse source dumps and built images |
| **History** | Build records, including failure reasons |
| **Connection Check** | Scan the common jailbreak service ports on one console and decide whether it is a jailbroken PS5; the IP is entered once and the FTP / Kernel Log / Payload pages stay in sync |
| **FTP** | Connect to the PS5, browse remote directories, upload images |
| **Kernel Log** | Receive the PS5 kernel log in real time |
| **Payloads** | Payload library: scan a folder, auto-read ELF info and capabilities, add notes, send to the PS5; the bundled catalog can be downloaded from the project release on demand |
| **Backport** | Restore SELF/FSELF, lower the SDK, fake-sign, verify, and write back automatically; also handles bare ELFs, backing up before changes by default |
| **Settings** | Paths, cluster size, compression, ffpkg parameters, PS5 connection info |

The interface is cyberpunk-styled (neon panels, scanlines, flowing progress bars) with live zh/en switching.

## Format support

| Format | Backend | Dependency |
|---|---|---|
| `.exfat` | MkPFS pure-Python serializer (default cluster 64 KiB) | none |
| `.ffpfsc` (PFS) | MkPFS, PFSC block compression (deflate 1–9) | none |
| `.ffpkg` (UFS) | bundled UFS2Tool | .NET 8 runtime |

## Install

```bash
pip install -e .
```

### Single-file exe

```bash
pip install pyinstaller pillow
python installer/make_assets.py --version 0.7.5
python -m PyInstaller --noconfirm PS5-Image-Forge.spec
```

Produces a single-file `PS5-Image-Forge.exe`: double-click for the GUI; pass arguments for the CLI; `--selftest` self-checks. The asset step first generates the app icon the spec embeds; skip it and you get an icon-less exe.

### Windows installer

```powershell
powershell -ExecutionPolicy Bypass -File installer\build_setup.ps1
```

A single `PS5-Image-Forge-Setup-<version>.exe` — a silent NSIS self-extractor wrapped around a branded setup app in the same style as the main window. It installs per-user (no UAC), lets you pick the folder, detects an existing copy and switches to update mode, and asks you to close the app rather than killing it. See [installer/](installer/README.md).

## CLI

```bash
ps5-image-forge env
ps5-image-forge build E:\PPSA21564-app0 -o D:\PS5
ps5-image-forge build E:\dump -o D:\PS5 -f pfs --level 6
ps5-image-forge build E:\dump -o D:\PS5 -f ffpkg
ps5-image-forge build E:\dump -o D:\PS5 -f pfs --via ffpkg --keep-intermediate
ps5-image-forge compress D:\PS5\PPSA21564.exfat -o D:\PS5\PPSA21564.ffpfsc --level 9
ps5-image-forge backport D:\PS5\PPSA21564.exfat --target 5
ps5-image-forge backport E:\PPSA21564-app0 --overwrite-from D:\patch.zip
ps5-image-forge verify D:\PS5\PPSA21564.exfat --source E:\PPSA21564-app0
ps5-image-forge extract D:\PS5\PPSA21564.ffpfsc D:\unpacked
ps5-image-forge list D:\PS5\PPSA21564.exfat
ps5-image-forge history
ps5-image-forge catalog
```

`compress` compresses an existing exFAT image into PFS (`.ffpfsc`). `backport` works on both an unpacked game folder and an `.exfat`/`.ffpfsc`/`.ffpkg` image directly: the image is unpacked → modified → repacked (`--target` does the SDK downgrade; `--overwrite-from` overlays files from a zip/folder by relative path). Because a ROM can be hundreds of GB, the backup keeps only the **original version of the changed files** in a `NAME.bak.zip` next to the image; use it as a one-shot overlay to restore.

IO-heavy temporary/intermediate data (compression, image unpack/repack) lands next to the output by default; the library usually lives on an HDD, so the **work directory** in Settings (or CLI `--work-dir`) can point it at an SSD, with only the final image written back to the HDD.

The default output filename comes from the game metadata, formatted as `PPSA_TITLE_VERSION.ext`, e.g. `PPSA21564_ASTRO_BOT_01.000.000.ffpfsc`. Windows-illegal characters in the title are cleaned automatically; converting an existing exFAT image to PFS also reads the image's `sce_sys/param.json` without unpacking first.

CLI messages follow the system language; `PS5_IMAGE_FORGE_LANG=en|zh` overrides it.

## Development

```bash
pytest tests/
```

The test suite covers image build/verify/corruption-detection/byte-exact unpack round-trip, the three-format pipeline, exFAT→PFS compression, in-image Backport and patch overlay, settings and history persistence, library scanning, payload ELF parsing, the PS5 protocol and port scan (a local socket simulates real wire behavior), the payload catalog and download, Backport scan/downgrade/backup and signed-file protection, and the GUI's full backend interface (driven without a window).

The UI can be developed straight in a browser:

```bash
python -m http.server 8899 -d src/ps5_image_forge/webui
```

Without a backend the pages enter demo mode and render every screen with synthetic data.

## Payload library

Drop `.elf` / `.bin` files into a folder and select it on the Payload page to scan. Each payload's info is **read directly from the file itself**: the ELF header (arch / OSABI / entry), GNU build-id, compiler, and a name, version and capability tags (ftp / mount / backport / jailbreak, etc.) inferred from strings.

The description is taken by priority: **your note** > a same-name `.txt` / `.md` / `.json` > auto-generated from the ELF. Notes live in `%APPDATA%/ps5-image-forge/payload_notes.json` and never touch the payload file itself. Non-ELF files, or ones with an unusual architecture, are flagged to avoid sending a file that is bound to fail.

The Payload page also ships a set of common payload binaries with full catalog metadata. After the user selects the PS5 firmware, only compatible entries are shown by default; clicking "Use" verifies SHA-256 against the shipped manifest, then atomically extracts to the payload folder. Each binary is pinned to the upstream release URL noted in the catalog; the source manifest is at `vendor/payloads/manifest.json`.

## Backport downgrade

The Backport page can scan a game folder for `.bin` / `.elf` / `.self` / `.prx` / `.sprx` and read the PS5 SDK requirement. A bare ELF is modified directly; a SELF/FSELF is restored to ELF, has its SDK lowered to the user-chosen version (1.00–10.00), and is re-emitted as a fake-signed SELF. By default a `.bak.zip` is generated and verified first, and only after "re-restore and check SDK" passes is the original atomically replaced. A container that cannot be reliably restored is reported as a failure with the original left untouched.

The target SDK is auto-suggested from the PS5 firmware in Settings. The Payload page also bundles BestPig's official BackPork payload for the background unionfs overlay flow: Auto Backport prepares the fake-signed downgraded files, and the BackPork payload provides the overlay on the console side.

## Development docs

Current progress, settled requirements and design decisions, and the todo list: [docs/DEVELOPMENT.md](docs/DEVELOPMENT.md).

## License

GPL-3.0 (following [MkPFS](https://github.com/PSBrew/MkPFS) upstream). The UFS2Tool assemblies under `vendor/ufs2tool/` come from exFAT Image Builder v4.0.2; see `PROVENANCE.md` in that directory. Auto Backport uses the unmodified `make_fself.py` from [ps5-payload-dev/sdk](https://github.com/ps5-payload-dev/sdk); provenance, hashes, and the GPL-3.0-or-later text are under `src/ps5_image_forge/_vendor/`.
