"""Payload catalogue: the shipped metadata, and the download rules.

Downloads are exercised against a loopback HTTP server, never the real
internet — what matters here is that a cancelled or failed fetch leaves no
file behind that the payload reader would later present as a payload.
"""

from __future__ import annotations

import http.server
import json
import threading
from pathlib import Path

import pytest

from ps5_image_forge import catalog


def _serve(body: bytes, *, chunked_delay: threading.Event | None = None):
    """Serve ``body`` at /payload.elf on a throwaway loopback port."""

    class Handler(http.server.BaseHTTPRequestHandler):
        def do_GET(self) -> None:              # noqa: N802 (stdlib naming)
            self.send_response(200)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            for i in range(0, len(body), 4096):
                if chunked_delay is not None:
                    chunked_delay.wait(2)
                self.wfile.write(body[i:i + 4096])

        def log_message(self, *_a) -> None:
            pass

    srv = http.server.HTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return f"http://127.0.0.1:{srv.server_port}/payload.elf", srv


def _entry(url: str, name: str = "test.elf") -> catalog.CatalogEntry:
    return catalog.CatalogEntry(
        id="test", title="Test", file=name, author="nobody", version="1.0",
        description="", project_url="", binary_url=url, page_url=None,
        firmwares=None, port=9021)


# ── the shipped catalogue ─────────────────────────────────────────

def test_catalogue_loads_and_is_complete() -> None:
    entries = catalog.load()
    assert len(entries) >= 19
    ids = [e.id for e in entries]
    assert len(set(ids)) == len(ids), "duplicate catalogue id"
    for e in entries:
        assert e.title and e.file and e.version and e.project_url
        assert e.binary_url or e.page_url, f"{e.id} has nowhere to go"


def test_every_download_link_is_https_and_upstream() -> None:
    """No download may point at the metadata source or anywhere unencrypted."""
    for e in catalog.load():
        if not e.binary_url:
            continue
        assert e.binary_url.startswith("https://"), e.id
        assert "45.56.67.85" not in e.binary_url, e.id
        assert "/releases/download/" in e.binary_url, e.id


def test_catalogue_json_contains_metadata_not_embedded_blobs() -> None:
    doc = json.loads(
        Path(catalog._data_file()).read_text(encoding="utf-8"))
    blob = json.dumps(doc)
    assert "base64" not in blob.lower()
    assert doc["metadata_source"]


def test_bundled_payload_manifest_and_hashes_are_valid() -> None:
    assert catalog.validate_bundled() == 18
    bundled = [e for e in catalog.as_dicts() if e["bundled"]]
    assert len(bundled) == 18


def test_find_and_unknown_id() -> None:
    assert catalog.find("ftpsrv-ps5").title == "ftpsrv"
    with pytest.raises(catalog.CatalogError):
        catalog.find("does-not-exist")


def test_firmware_prefixes_are_matched() -> None:
    bye = catalog.find("byepervisor")
    assert catalog.matches_firmware(bye, "2.50")
    assert not catalog.matches_firmware(bye, "5.02")
    # no firmware list means "all"
    assert catalog.matches_firmware(_entry("https://x/y"), "9.99")

    remote_play = catalog.find("rp-get-pin")
    assert catalog.matches_firmware(remote_play, "5.50")
    assert catalog.matches_firmware(remote_play, "6.50")
    assert catalog.matches_firmware(remote_play, "7.61")
    assert not catalog.matches_firmware(remote_play, "8.00")


def test_firmware_ranges_are_matched() -> None:
    full = catalog.find("kstuff")
    lite = catalog.find("kstuff-lite")
    assert catalog.matches_firmware(full, "3.00")
    assert catalog.matches_firmware(full, "10.01")
    assert not catalog.matches_firmware(full, "10.20")
    assert catalog.matches_firmware(lite, "12.70")
    assert not catalog.matches_firmware(lite, "12.71")
    assert not catalog.matches_firmware(lite, "not-a-version")
    pork = catalog.find("backpork")
    assert catalog.matches_firmware(pork, "12.00")
    assert not catalog.matches_firmware(pork, "12.02")


def test_install_bundled_verifies_and_is_idempotent(tmp_path: Path) -> None:
    entry = catalog.find("ftpsrv-ps5")
    first = catalog.install_bundled(entry, tmp_path)
    assert first.is_file() and first.read_bytes().startswith(b"\x7fELF")
    assert catalog.install_bundled(entry, tmp_path) == first
    first.write_bytes(b"not the bundled payload")
    with pytest.raises(catalog.CatalogError, match="different content"):
        catalog.install_bundled(entry, tmp_path)
    restored = catalog.install_bundled(entry, tmp_path, overwrite=True)
    assert restored.read_bytes().startswith(b"\x7fELF")
    assert not list(tmp_path.glob("*.part"))


# ── downloading ───────────────────────────────────────────────────

def test_download_writes_the_file_and_reports_progress(tmp_path: Path) -> None:
    payload = b"\x7fELF" + bytes(20_000)
    url, srv = _serve(payload)
    seen: list[tuple[int, int]] = []
    try:
        out = catalog.download(_entry(url), tmp_path,
                               progress=lambda d, t: seen.append((d, t)))
    finally:
        srv.shutdown()
    assert out.read_bytes() == payload
    assert seen and seen[-1][0] == len(payload) == seen[-1][1]
    assert not list(tmp_path.glob("*.part"))


def test_existing_file_is_not_clobbered(tmp_path: Path) -> None:
    (tmp_path / "test.elf").write_bytes(b"mine")
    url, srv = _serve(b"\x7fELF" + bytes(100))
    try:
        with pytest.raises(catalog.CatalogError, match="already exists"):
            catalog.download(_entry(url), tmp_path)
        assert (tmp_path / "test.elf").read_bytes() == b"mine"
        catalog.download(_entry(url), tmp_path, overwrite=True)
        assert (tmp_path / "test.elf").read_bytes() != b"mine"
    finally:
        srv.shutdown()


def test_cancel_leaves_nothing_behind(tmp_path: Path) -> None:
    gate = threading.Event()
    url, srv = _serve(bytes(200_000), chunked_delay=gate)
    cancel = threading.Event()
    cancel.set()
    gate.set()
    try:
        with pytest.raises(catalog.CatalogError, match="cancelled"):
            catalog.download(_entry(url), tmp_path, cancel=cancel)
    finally:
        srv.shutdown()
    assert list(tmp_path.iterdir()) == []


def test_failed_download_leaves_nothing_behind(tmp_path: Path) -> None:
    # nothing listens on port 1
    with pytest.raises(catalog.CatalogError):
        catalog.download(_entry("https://127.0.0.1:1/x.elf"), tmp_path)
    assert list(tmp_path.iterdir()) == []


def test_non_https_source_is_refused(tmp_path: Path) -> None:
    with pytest.raises(catalog.CatalogError, match="non-https"):
        catalog.download(_entry("http://example.invalid/x.elf"), tmp_path)
    with pytest.raises(catalog.CatalogError, match="non-https"):
        catalog.download(_entry("file:///C:/Windows/notepad.exe"), tmp_path)


def test_page_only_entry_cannot_be_downloaded(tmp_path: Path) -> None:
    e = _entry("https://x/y")
    e.binary_url = None
    with pytest.raises(catalog.CatalogError, match="no direct download"):
        catalog.download(e, tmp_path)
