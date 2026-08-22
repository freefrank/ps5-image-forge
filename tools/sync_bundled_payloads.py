"""Fetch the pinned payload catalogue assets and write an audit manifest.

This is a maintainer tool, not an application update mechanism.  It downloads
only direct HTTPS assets already pinned in ``payload_catalog.json``.  The
resulting files and SHA-256 hashes are committed and bundled by PyInstaller.
"""

from __future__ import annotations

import hashlib
import json
import urllib.request
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
CATALOG = ROOT / "src" / "exfat_forge" / "payload_catalog.json"
DEST = ROOT / "vendor" / "payloads"
MANIFEST = DEST / "manifest.json"
USER_AGENT = "exfat-forge-payload-bundler"


def main() -> int:
    document = json.loads(CATALOG.read_text(encoding="utf-8"))
    DEST.mkdir(parents=True, exist_ok=True)
    bundled: list[dict[str, object]] = []

    for entry in document.get("entries", []):
        url = entry.get("binary_url")
        if not url:
            continue
        if not str(url).startswith("https://"):
            raise RuntimeError(f"refusing non-HTTPS asset for {entry['id']}: {url}")

        target = DEST / entry["file"]
        partial = target.with_name(target.name + ".part")
        print(f"fetch {entry['id']}: {url}")
        request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
        try:
            with urllib.request.urlopen(request, timeout=90) as response, partial.open("wb") as out:
                while chunk := response.read(1024 * 1024):
                    out.write(chunk)
            partial.replace(target)
        except BaseException:
            partial.unlink(missing_ok=True)
            raise

        data = target.read_bytes()
        if len(data) < 64:
            raise RuntimeError(f"asset is unexpectedly small: {target}")
        bundled.append({
            "id": entry["id"],
            "file": entry["file"],
            "size": len(data),
            "sha256": hashlib.sha256(data).hexdigest(),
            "source_url": url,
            "version": entry["version"],
        })

    manifest = {
        "schema": 1,
        "catalog_source": document.get("metadata_source", ""),
        "entries": bundled,
    }
    MANIFEST.write_text(json.dumps(manifest, indent=2, ensure_ascii=False) + "\n",
                        encoding="utf-8")
    print(f"bundled {len(bundled)} payloads in {DEST}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
