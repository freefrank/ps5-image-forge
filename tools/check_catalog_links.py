"""Check that every link in the shipped payload catalogue still resolves.

Not part of the test suite: it needs the network, and a project deleting a
release should not turn into a red build on someone else's machine. Run it
by hand before a release, and fix or drop whatever it reports.

    python tools/check_catalog_links.py

Exits non-zero if anything is unreachable.
"""

from __future__ import annotations

import sys
import urllib.error
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from ps5_image_forge import catalog          # noqa: E402


def check(url: str) -> tuple[bool, str]:
    """Ask for the first kilobyte — enough to prove the asset is served."""
    req = urllib.request.Request(url, headers={
        "User-Agent": "ps5-image-forge-linkcheck", "Range": "bytes=0-1023"})
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            return True, f"{resp.status} {resp.headers.get('Content-Type', '')}"
    except urllib.error.HTTPError as exc:
        return False, f"HTTP {exc.code}"
    except (urllib.error.URLError, OSError) as exc:
        return False, str(exc)


def main() -> int:
    entries = catalog.load()
    print(f"{len(entries)} entries · metadata from {catalog.metadata_source()}\n")
    bad = []
    for e in entries:
        url = e.binary_url or e.page_url or ""
        kind = "direct" if e.binary_url else "page  "
        ok, detail = check(url)
        print(f"{'OK ' if ok else 'DEAD'}  {e.id:30} {kind}  {detail}")
        if not ok:
            bad.append((e.id, url, detail))

    if not bad:
        print("\nall links resolve")
        return 0
    print(f"\n{len(bad)} dead link(s):")
    for entry_id, url, detail in bad:
        print(f"  {entry_id}: {detail}\n    {url}")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
