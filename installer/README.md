# Installer

Windows installer for PS5 Image Forge, built with [NSIS](https://nsis.sourceforge.io)
(Modern UI 2) and styled to match the app's cyberpunk UI.

## Files

| File | Purpose |
|---|---|
| `installer.nsi` | The NSIS script. Compiles the single-file exe into a setup with Start Menu / desktop shortcuts, Add/Remove Programs entry, and an uninstaller. |
| `make_assets.py` | Generates the branding art (`assets/welcome.bmp`, `header.bmp`, `installer.ico`, `uninstall.ico`) in the app's palette. Needs Pillow. |
| `build_installer.ps1` | One-shot build: reads the version from `pyproject.toml`, builds the exe if missing, regenerates assets, finds `makensis`, and compiles. |

`assets/` is generated and git-ignored; `build_installer.ps1` recreates it.

## Build

From the repo root:

```powershell
powershell -ExecutionPolicy Bypass -File installer\build_installer.ps1
```

Pass `-Build` to force a fresh PyInstaller build of the exe first. The finished
setup is written to `dist\PS5-Image-Forge-Setup-<version>.exe`.

### Requirements

- **NSIS 3.x** on `PATH`, or installed at `%ProgramFiles(x86)%\NSIS`.
  Install with `winget install NSIS.NSIS`.
- **Pillow** for the branding art: `python -m pip install pillow`
  (the fonts are read from `C:\Windows\Fonts`).
- The app exe at `dist\PS5-Image-Forge.exe` (built by PyInstaller; the script
  builds it automatically if absent).

### Manual compile

If `dist\PS5-Image-Forge.exe` and `assets\` already exist:

```powershell
makensis /DVERSION=0.7.4 installer\installer.nsi
```

Overridable `/D` defines: `VERSION`, `SRC_EXE`, `OUTFILE`, `APP_EXE`.

## Styling

Colors, wordmark (`PS5 IMAGE ▮ FORGE`) and texture come straight from
`src/ps5_image_forge/webui/app.css`: deep-navy ground `#070b14`, neon-cyan
`#00e5ff`, magenta accent `#ff2d95`, faint grid and CRT scanlines. The
Welcome/Finish pages and the install log render fully dark with neon text;
interior pages carry a dark neon header band.
