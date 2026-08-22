"""Bridge tests — the GUI's whole backend surface, driven without a window.

``Bridge(window=None)`` makes every JS push a no-op, so the same code the
page calls can be exercised headlessly. This is what keeps the UI honest:
if a button's backing call breaks, these fail rather than the bug reaching
a user mid-build.
"""

from __future__ import annotations

import json
import random
import threading
import time
from pathlib import Path

import pytest

from exfat_forge import i18n
from exfat_forge.bridge import Bridge


@pytest.fixture(autouse=True)
def isolated_config(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("APPDATA", str(tmp_path / "appdata"))
    i18n.set_locale("en")


@pytest.fixture()
def dump(tmp_path: Path) -> Path:
    rng = random.Random(2024)
    src = tmp_path / "PPSA77777-app0"
    (src / "sce_sys").mkdir(parents=True)
    (src / "sce_sys" / "param.json").write_text(json.dumps({
        "titleId": "PPSA77777",
        "localizedParameters": {"defaultLanguage": "en-US",
                                "en-US": {"titleName": "Bridge Test"}},
    }), encoding="utf-8")
    (src / "eboot.bin").write_bytes(rng.randbytes(150_000))
    return src


class RecordingBridge(Bridge):
    """Captures what would have been pushed into the page."""

    def __init__(self) -> None:
        super().__init__(window=None)
        self.calls: list[tuple] = []

    def _js(self, fn: str, *args) -> None:
        self.calls.append((fn, args))

    def wait(self, timeout: float = 60.0) -> None:
        deadline = time.monotonic() + timeout
        while self._busy() and time.monotonic() < deadline:
            time.sleep(0.05)
        assert not self._busy(), "worker did not finish in time"

    def last(self, fn: str):
        for name, args in reversed(self.calls):
            if name == fn:
                return args
        return None


def test_environment_and_settings_roundtrip() -> None:
    b = RecordingBridge()
    env = b.environment()
    assert "ffpkg" in env and "version" in env

    saved = b.save_settings({"output_dir": r"D:\PS5", "pfs_level": 5,
                             "bogus_key": 1})
    assert saved["output_dir"] == r"D:\PS5" and saved["pfs_level"] == 5
    assert "bogus_key" not in saved
    assert Bridge(window=None).get_settings()["output_dir"] == r"D:\PS5"


def test_lang_switch_persists() -> None:
    b = RecordingBridge()
    b.set_lang("zh")
    assert b.get_lang() == "zh"
    assert Bridge(window=None).get_settings()["lang"] == "zh"


def test_inspect_source_reads_param_json(dump: Path) -> None:
    info = RecordingBridge().inspect_source(str(dump))
    assert info["ok"] and info["title_id"] == "PPSA77777"
    assert info["title"] == "Bridge Test" and info["has_eboot"]


def test_inspect_source_rejects_non_directory(tmp_path: Path) -> None:
    assert not RecordingBridge().inspect_source(str(tmp_path / "nope"))["ok"]


def test_build_pushes_progress_and_done(dump: Path, tmp_path: Path) -> None:
    b = RecordingBridge()
    b.start_build({"source": str(dump), "output": str(tmp_path / "out"),
                   "mode": "exfat", "verify": True})
    b.wait()
    assert b.last("onDone") is not None
    assert b.last("onError") is None
    phases = {a[0]["phase"] for n, a in b.calls if n == "onProgress"}
    assert {"scan", "write", "verify"} <= phases
    assert (tmp_path / "out" / "PPSA77777.exfat").is_file()


def test_build_reports_error_not_crash(tmp_path: Path) -> None:
    b = RecordingBridge()
    b.start_build({"source": str(tmp_path / "missing"),
                   "output": str(tmp_path / "out"), "mode": "exfat"})
    b.wait()
    assert b.last("onError") is not None
    assert b.last("onDone") is None


def test_second_build_is_rejected_while_busy(dump: Path, tmp_path: Path) -> None:
    b = RecordingBridge()
    started = threading.Event()

    def slow(*_a):
        started.set()
        time.sleep(0.6)

    b._spawn(slow)
    started.wait(5)
    b.start_build({"source": str(dump), "output": str(tmp_path)})
    assert any(n == "onLog" and "already running" in str(a[0])
               for n, a in b.calls), "busy guard should log a refusal"
    b.wait()


def test_cancel_stops_build(dump: Path, tmp_path: Path) -> None:
    b = RecordingBridge()
    b.start_build({"source": str(dump), "output": str(tmp_path / "out"),
                   "mode": "exfat"})
    b.cancel()
    b.wait()
    # either it finished before the flag landed, or it reported cancellation
    assert b.last("onCancelled") is not None or b.last("onDone") is not None


def test_history_records_and_clears(dump: Path, tmp_path: Path) -> None:
    b = RecordingBridge()
    b.start_build({"source": str(dump), "output": str(tmp_path / "out"),
                   "mode": "exfat"})
    b.wait()
    hist = b.get_history()
    assert hist and hist[0]["title_id"] == "PPSA77777"
    b.clear_history()
    assert b.get_history() == []


def test_extract_and_inspect_image(dump: Path, tmp_path: Path) -> None:
    b = RecordingBridge()
    b.start_build({"source": str(dump), "output": str(tmp_path / "out"),
                   "mode": "exfat"})
    b.wait()
    image = tmp_path / "out" / "PPSA77777.exfat"

    info = b.inspect_image(str(image))
    assert info["ok"] and info["fmt"] == "exfat" and info["file_count"] == 2

    b.start_extract(str(image), str(tmp_path / "back"))
    b.wait()
    assert b.last("onError") is None
    assert (tmp_path / "back" / "eboot.bin").read_bytes() == \
           (dump / "eboot.bin").read_bytes()


def test_scan_library(dump: Path, tmp_path: Path) -> None:
    result = RecordingBridge().scan_library([str(tmp_path)])
    assert [d["title_id"] for d in result["dumps"]] == ["PPSA77777"]


def test_ps5_probe_unreachable_is_reported() -> None:
    res = RecordingBridge().ps5_probe("127.0.0.1", 1)
    assert res["reachable"] is False and res["detail"]


def test_ps5_list_failure_returns_error() -> None:
    res = RecordingBridge().ps5_list("127.0.0.1", 1, "/")
    assert res["ok"] is False and "FTP connect failed" in res["error"]


def test_ps5_send_payload_failure_returns_error(tmp_path: Path) -> None:
    bad = tmp_path / "x.elf"
    bad.write_bytes(b"not an elf at all")
    res = RecordingBridge().ps5_send_payload("127.0.0.1", 1, str(bad))
    assert res["ok"] is False


def test_payload_library_scan_and_notes(tmp_path: Path) -> None:
    lib = tmp_path / "payloads"
    lib.mkdir()
    # minimal valid ELF64 header is enough for the bridge round trip
    (lib / "a.elf").write_bytes(b"\x7fELF\x02\x01\x01\x09" + b"\0" * 120)
    (lib / "a.txt").write_text("does a thing", encoding="utf-8")

    b = RecordingBridge()
    r = b.scan_payloads(str(lib))
    assert r["ok"] and len(r["items"]) == 1
    item = r["items"][0]
    assert item["description"] == "does a thing"
    assert b.get_settings()["payload_dir"] == str(lib)

    b.save_payload_note(item["path"], "mine")
    assert b.scan_payloads(str(lib))["items"][0]["source"] == "notes"


def test_payload_scan_reports_bad_folder(tmp_path: Path) -> None:
    r = RecordingBridge().scan_payloads(str(tmp_path / "missing"))
    assert r["ok"] is False and r["items"] == []


def test_known_services_exposed_to_the_page() -> None:
    rows = RecordingBridge().list_known_services()
    assert rows and {r["port"] for r in rows} >= {2121, 9021, 3232, 9090}


def test_port_scan_pushes_results_and_a_summary() -> None:
    b = RecordingBridge()
    assert b.scan_ps5_ports("127.0.0.1", [1, 2])["ok"]
    deadline = time.monotonic() + 30
    while b.last("onScanDone") is None and time.monotonic() < deadline:
        time.sleep(0.05)
    summary = b.last("onScanDone")
    assert summary is not None and summary[0]["total"] == 2
    assert len([1 for n, _a in b.calls if n == "onPortResult"]) == 2


def test_port_scan_rejects_empty_host() -> None:
    assert RecordingBridge().scan_ps5_ports("  ")["ok"] is False
