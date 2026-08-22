"""Payload catalogue: metadata we ship, binaries we do not.

``payload_catalog.json`` carries names, versions, firmware ranges and links
for the payloads the scene actually uses. It carries no executable content.
When the user asks for one, :func:`download` fetches it **from the project's
own release asset** — the same URL the project publishes — into the folder
the user picked, and the existing :mod:`payloads` reader then describes it
like any other local file.

Two reasons it works this way rather than embedding the binaries:

* an unverifiable third-party executable inside our exe is a supply-chain
  problem for everyone who installs it, and
* payloads are released far more often than this tool is, so a bundled copy
  is stale almost immediately.

Three catalogue entries publish through a release *tag* or a CI run rather
than a direct asset. Those carry ``page_url`` instead of ``binary_url``; the
UI sends the user to the page rather than offering a download that cannot
work.
"""

from __future__ import annotations

import json
import sys
import threading
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import asdict, dataclass
from pathlib import Path

_UA = "exfat-forge"
_TIMEOUT = 30


class CatalogError(RuntimeError):
    """Catalogue could not be read, or a download failed."""


@dataclass
class CatalogEntry:
    id: str
    title: str
    file: str
    author: str
    version: str
    description: str
    project_url: str
    binary_url: str | None
    page_url: str | None
    firmwares: list[str] | None
    port: int | None


def _data_file() -> Path:
    """Locate the JSON next to this module, frozen or not."""
    base = Path(getattr(sys, "_MEIPASS", Path(__file__).parent))
    here = base / "exfat_forge" / "payload_catalog.json"
    return here if here.is_file() else Path(__file__).with_name("payload_catalog.json")


def load() -> list[CatalogEntry]:
    try:
        doc = json.loads(_data_file().read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise CatalogError(f"catalogue unreadable: {exc}") from exc
    return [CatalogEntry(**e) for e in doc.get("entries", [])]


def metadata_source() -> str:
    try:
        return json.loads(_data_file().read_text(encoding="utf-8")).get(
            "metadata_source", "")
    except (OSError, ValueError):
        return ""


def as_dicts() -> list[dict]:
    return [asdict(e) for e in load()]


def find(entry_id: str) -> CatalogEntry:
    for e in load():
        if e.id == entry_id:
            return e
    raise CatalogError(f"unknown catalogue entry: {entry_id}")


def matches_firmware(entry: CatalogEntry, firmware: str) -> bool:
    """The catalogue stores firmware *prefixes*; empty means "all"."""
    if not entry.firmwares:
        return True
    return any(firmware.startswith(p) for p in entry.firmwares)


def _check_url(url: str) -> None:
    """Refuse anything a download has no business fetching.

    https only, so an edited catalogue cannot quietly downgrade to plain
    http or point the fetch at ``file://``. Loopback is the one exception —
    it cannot be intercepted, and it is what the tests serve from.
    """
    parts = urllib.parse.urlsplit(url)
    if parts.scheme == "https":
        return
    if parts.scheme == "http" and parts.hostname in ("127.0.0.1", "::1",
                                                     "localhost"):
        return
    raise CatalogError(f"refusing non-https source: {url}")


def download(entry: CatalogEntry, dest_dir: Path, *,
             progress=None,
             cancel: threading.Event | None = None,
             overwrite: bool = False) -> Path:
    """Fetch ``entry`` into ``dest_dir`` and return the written path.

    Writes ``<name>.part`` and renames on success, so an interrupted or
    cancelled download never leaves a half file that looks like a payload —
    the same rule the image builder follows.
    """
    if not entry.binary_url:
        raise CatalogError(f"{entry.title} has no direct download link")
    _check_url(entry.binary_url)

    dest_dir = Path(dest_dir)
    dest_dir.mkdir(parents=True, exist_ok=True)
    target = dest_dir / entry.file
    if target.exists() and not overwrite:
        raise CatalogError(f"{target.name} already exists")

    partial = target.with_name(target.name + ".part")
    req = urllib.request.Request(entry.binary_url, headers={"User-Agent": _UA})
    try:
        with urllib.request.urlopen(req, timeout=_TIMEOUT) as resp:
            total = int(resp.headers.get("Content-Length") or 0)
            done = 0
            with partial.open("wb") as fh:
                while True:
                    if cancel and cancel.is_set():
                        raise CatalogError("cancelled")
                    chunk = resp.read(65536)
                    if not chunk:
                        break
                    fh.write(chunk)
                    done += len(chunk)
                    if progress:
                        progress(done, total)
    except (urllib.error.URLError, OSError, CatalogError) as exc:
        partial.unlink(missing_ok=True)
        if isinstance(exc, CatalogError):
            raise
        raise CatalogError(f"download failed: {exc}") from exc

    if not done:
        partial.unlink(missing_ok=True)
        raise CatalogError("download was empty")
    partial.replace(target)
    return target
