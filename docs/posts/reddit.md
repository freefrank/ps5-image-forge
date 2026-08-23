# I wrote a PS5 game image tool

**PS5 Image Forge** — a toolkit for building, converting, inspecting and
managing PS5 game-dump images. GUI + CLI, cross-platform, free and open
source (GPL-3.0).

**TL;DR:** turn a game dump into an exFAT / PFS / UFS image, backport the
SDK with one click, manage payloads, and talk to your console — all from one
app. Prebuilt for Windows, Linux and macOS.

[Home](https://raw.githubusercontent.com/freefrank/ps5-image-forge/master/docs/screenshots/home.png) · [Build](https://raw.githubusercontent.com/freefrank/ps5-image-forge/master/docs/screenshots/build.png) · [Payloads](https://raw.githubusercontent.com/freefrank/ps5-image-forge/master/docs/screenshots/payload.png) · [Backport](https://raw.githubusercontent.com/freefrank/ps5-image-forge/master/docs/screenshots/backport.png)

## What it does
- **Build** a dump → raw exFAT, PFS (`.ffpfsc`, compressed) or UFS (`.ffpkg`)
- **Extract** any of those back to a folder; **Inspect** an image read-only
- **Auto Backport** — restore SELF/FSELF, lower the SDK (1.00–10.00),
  fake-sign, verify, write back; always makes a verified `.bak.zip` first
- **Payload library** — reads info from the ELF itself, plus a bundled,
  SHA-256-verified catalog
- **Console tools** — connection/port scan, FTP browser, live kernel-log
  tail, payload sender

Building is mount-free (no OSFMount, no drive letters, no admin), and the
whole thing is covered by a large automated test suite.

## Downloads (v0.7.4)
- Windows — portable exe or installer
- Linux — AppImage (needs `libwebkit2gtk-4.1`)
- macOS — DMG (Apple Silicon, unsigned; open instructions included)

➡️ https://github.com/freefrank/ps5-image-forge/releases/latest
Source: https://github.com/freefrank/ps5-image-forge

## Caveats
- The on-console network features (FTP / kernel log / payload sending) are
  tested against a mock server but **not yet on a real PS5** — testers
  welcome, please report back if you try them on hardware.
- The macOS build is unsigned, so Gatekeeper will block the first launch
  (a HOW-TO-OPEN txt ships with the DMG).

Happy to answer questions, and feedback / bug reports / PRs are very welcome.

---

<sub>Note: Reddit self-posts don't embed external image URLs — the screenshot
links above are clickable, or upload the PNGs from `docs/screenshots/` via
Reddit's image/gallery uploader so they render inline.</sub>
