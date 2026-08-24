"""Stamp one version string into every place that hardcodes it.

The release tag is the source of truth, but nothing used to carry it into the
source: CI took the version from the tag for the *filenames* only, so v0.7.5
shipped an app that reported 0.7.4 in its own footer. The release workflow now
runs this before every build.

    python tools/set_version.py 0.7.6      # rewrite all sites
    python tools/set_version.py --check    # all sites agree with pyproject?
    python tools/set_version.py --check 0.7.6

Exit code 1 means a site disagreed (in --check) or could not be rewritten.
"""
from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

# (path, regex with a single group around the version, human name)
SITES = [
    (ROOT / "pyproject.toml",
     re.compile(r'(?m)^(version\s*=\s*")([^"]+)(")'),
     "pyproject.toml [project].version"),
    (ROOT / "src/ps5_image_forge/__init__.py",
     re.compile(r'(?m)^(__version__\s*=\s*")([^"]+)(")'),
     "__init__.py __version__  (shown in the app via bridge.py)"),
    (ROOT / "src/ps5_image_forge/webui/app.js",
     re.compile(r'(?m)^(const APP_VERSION\s*=\s*")([^"]+)(")'),
     "webui/app.js APP_VERSION  (demo mode only)"),
]

VERSION_RE = re.compile(r"^\d+\.\d+\.\d+([-.+][0-9A-Za-z.-]+)?$")


def read(site) -> tuple[str, str]:
    path, pattern, name = site
    text = path.read_text(encoding="utf-8")
    found = pattern.search(text)
    if not found:
        sys.exit(f"error: no version found in {path.relative_to(ROOT)} ({name})")
    return found.group(2), text


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("version", nargs="?", help="x.y.z; defaults to pyproject's")
    ap.add_argument("--check", action="store_true",
                    help="report drift instead of rewriting")
    args = ap.parse_args()

    want = args.version
    if want:
        want = want.lstrip("v")
        if not VERSION_RE.match(want):
            sys.exit(f"error: {want!r} is not a valid version")
    else:
        want = read(SITES[0])[0]

    drift = []
    for site in SITES:
        path, pattern, name = site
        current, text = read(site)
        if current == want:
            continue
        if args.check:
            drift.append(f"  {current:<12} {path.relative_to(ROOT)}  — {name}")
            continue
        path.write_text(pattern.sub(rf"\g<1>{want}\g<3>", text, count=1),
                        encoding="utf-8")
        print(f"  {current} -> {want}  {path.relative_to(ROOT)}")

    if args.check:
        if drift:
            print(f"version drift (expected {want}):")
            print("\n".join(drift))
            return 1
        print(f"all version sites agree: {want}")
        return 0

    print(f"stamped {want}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
