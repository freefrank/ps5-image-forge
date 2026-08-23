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
from types import SimpleNamespace
from pathlib import Path

import pytest

from ps5_image_forge import i18n
from ps5_image_forge.bridge import Bridge


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
        "titleId": "PPSA77777", "contentVersion": "03.000.000",
        "localizedParameters": {"defaultLanguage": "en-US",
                                "en-US": {"titleName": "Bridge Test"}},
    }), encoding="utf-8")
    (src / "eboot.bin").write_bytes(rng.randbytes(150_000))
    return src


def _patchable_eboot(path: Path, *, band: int = 10) -> Path:
    """A minimal ELF whose SCE process-param carries an SDK band."""
    import struct

    from ps5_image_forge import backport

    data = bytearray(0x180)
    data[:4] = backport.ELF_MAGIC
    data[4:7] = b"\x02\x01\x01"
    data[7] = 9
    struct.pack_into("<HHI", data, 0x10, 0xFE10, 0x3E, 1)
    struct.pack_into("<Q", data, 0x20, 0x40)
    struct.pack_into("<H", data, 0x34, 0x40)
    struct.pack_into("<H", data, 0x36, 0x38)
    struct.pack_into("<H", data, 0x38, 2)
    struct.pack_into("<II6Q", data, 0x40, 1, 4, 0x100, 0, 0, 0x30, 0x30, 0x10)
    struct.pack_into("<I", data, 0x78, backport.PT_SCE_PROCPARAM)
    struct.pack_into("<Q", data, 0x80, 0x100)
    struct.pack_into("<Q", data, 0x98, 0x30)
    struct.pack_into("<I", data, 0x100, 0x30)
    struct.pack_into("<I", data, 0x108, backport.SCE_PROCESS_PARAM_MAGIC)
    ps5, ps4 = backport.SDK_VERSION_PAIRS[band]
    struct.pack_into("<I", data, 0x110, ps4)
    struct.pack_into("<I", data, 0x114, ps5)
    path.write_bytes(data)
    return path


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
                             "ui_scale": 2.5,
                             "bogus_key": 1})
    assert saved["output_dir"] == r"D:\PS5" and saved["pfs_level"] == 5
    assert saved["ui_scale"] == 2.5
    assert "bogus_key" not in saved
    assert Bridge(window=None).get_settings()["output_dir"] == r"D:\PS5"


def test_window_maximize_is_not_fullscreen() -> None:
    class FakeWindow:
        def __init__(self) -> None:
            self.calls: list[str] = []

        def maximize(self) -> None:
            self.calls.append("maximize")

        def restore(self) -> None:
            self.calls.append("restore")

    window = FakeWindow()
    b = Bridge(window=window)
    assert b.toggle_maximize() is True
    assert b.toggle_maximize() is False
    assert window.calls == ["maximize", "restore"]
    assert b.begin_resize("invalid-edge") is False


def test_frameless_resize_posts_native_hit_test(monkeypatch: pytest.MonkeyPatch) -> None:
    import ctypes

    class NativeFunction:
        def __init__(self) -> None:
            self.calls: list[tuple] = []
            self.argtypes = None
            self.restype = None

        def __call__(self, *args):
            self.calls.append(args)
            return 1

    release = NativeFunction()
    post = NativeFunction()
    monkeypatch.setattr(ctypes, "windll", SimpleNamespace(user32=SimpleNamespace(
        ReleaseCapture=release, PostMessageW=post)))

    handle_value = 0x1234_5678_ABCD
    dispatched: list[object] = []

    def begin_invoke(action) -> None:
        dispatched.append(action)
        action()

    native = SimpleNamespace(
        InvokeRequired=True,
        BeginInvoke=begin_invoke,
        Handle=SimpleNamespace(ToInt64=lambda: handle_value),
    )
    b = Bridge(window=SimpleNamespace(native=native))
    assert b.begin_resize("bottom-right") is True
    assert len(dispatched) == 1
    assert len(release.calls) == 1
    assert post.calls[0][0].value == handle_value
    assert post.calls[0][1:] == (0x00A1, 17, 0)


def test_firmware_updates_recommended_backport_target() -> None:
    b = RecordingBridge()
    saved = b.save_settings({"ps5_firmware": "9.60"})
    assert saved["backport_target"] == 9
    assert saved["backport_target_recommended"] == 9

    saved = b.save_settings({"ps5_firmware": "12.00"})
    assert saved["backport_target"] == 10
    assert saved["backport_target_recommended"] == 10


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


def test_inspect_any_reads_folder_and_exfat_image(dump: Path, tmp_path: Path) -> None:
    from ps5_image_forge import core

    b = RecordingBridge()
    # dump folder
    folder = b.inspect_any(str(dump))
    assert folder["ok"] and folder["kind"] == "dump"
    assert folder["title_id"] == "PPSA77777" and folder["title"] == "Bridge Test"

    # exFAT image built from the same dump — metadata read in place
    image = core.build_exfat(dump, tmp_path / "g.exfat")
    img = b.inspect_any(str(image))
    assert img["ok"] and img["kind"] == "image"
    assert img["title_id"] == "PPSA77777" and img["version"] == "03.000.000"

    # a non-game path returns not-ok (no info line shown)
    assert b.inspect_any(str(tmp_path / "nope.txt"))["ok"] is False


def test_build_pushes_progress_and_done(dump: Path, tmp_path: Path) -> None:
    b = RecordingBridge()
    b.start_build({"source": str(dump), "output": str(tmp_path / "out"),
                   "mode": "exfat", "verify": True})
    b.wait()
    assert b.last("onDone") is not None
    assert b.last("onError") is None
    phases = {a[0]["phase"] for n, a in b.calls if n == "onProgress"}
    assert {"scan", "write", "verify"} <= phases
    assert (tmp_path / "out" /
            "PPSA77777_Bridge_Test_03.000.000.exfat").is_file()


def test_build_can_skip_optional_verification(dump: Path, tmp_path: Path) -> None:
    b = RecordingBridge()
    b.start_build({"source": str(dump), "output": str(tmp_path / "out"),
                   "mode": "exfat", "verify": False})
    b.wait()
    phases = {a[0]["phase"] for n, a in b.calls if n == "onProgress"}
    assert "write" in phases and "verify" not in phases
    assert b.last("onDone") is not None


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
    image = (tmp_path / "out" /
             "PPSA77777_Bridge_Test_03.000.000.exfat")

    info = b.inspect_image(str(image))
    assert info["ok"] and info["fmt"] == "exfat" and info["file_count"] == 2

    b.start_extract(str(image), str(tmp_path / "back"))
    b.wait()
    assert b.last("onError") is None
    assert (tmp_path / "back" / "eboot.bin").read_bytes() == \
           (dump / "eboot.bin").read_bytes()


def test_compress_exfat_to_pfs_via_bridge(dump: Path, tmp_path: Path) -> None:
    from ps5_image_forge import core

    image = core.build_exfat(dump, tmp_path / "game.exfat")
    b = RecordingBridge()
    b.start_compress({"image": str(image), "output": str(tmp_path / "out.ffpfsc"),
                      "level": 1})
    b.wait()
    assert b.last("onError") is None
    assert b.last("onDone") is not None
    assert (tmp_path / "out.ffpfsc").is_file()


def test_backport_image_via_bridge(dump: Path, tmp_path: Path) -> None:
    from ps5_image_forge import backport, core

    _patchable_eboot(dump / "eboot.bin", band=10)
    image = core.build_exfat(dump, tmp_path / "game.exfat")

    b = RecordingBridge()
    b.start_backport_image(str(image), 5)
    b.wait()
    assert b.last("onError") is None
    result = b.last("onBackportImage")
    assert result is not None and result[0]["patched"] == 1

    check = tmp_path / "check"
    from ps5_image_forge import pipeline
    pipeline.extract_any(image, check)
    assert backport.inspect_file(check / "eboot.bin").sdk_band == 5


def test_overwrite_folder_and_image_via_bridge(dump: Path, tmp_path: Path) -> None:
    from ps5_image_forge import core, pipeline

    patch = tmp_path / "patch"
    patch.mkdir()
    (patch / "eboot.bin").write_bytes(b"patched by user")

    # folder target
    folder = tmp_path / "game_folder"
    (folder / "sce_sys").mkdir(parents=True)
    (folder / "eboot.bin").write_bytes(b"orig")
    b = RecordingBridge()
    b.start_overwrite(str(folder), str(patch))
    b.wait()
    assert b.last("onError") is None
    assert (folder / "eboot.bin").read_bytes() == b"patched by user"

    # image target
    image = core.build_exfat(dump, tmp_path / "game.exfat")
    b2 = RecordingBridge()
    b2.start_overwrite(str(image), str(patch))
    b2.wait()
    assert b2.last("onError") is None
    res = b2.last("onOverwrite")
    assert res is not None and res[0]["replaced"] == 1
    check = tmp_path / "check"
    pipeline.extract_any(image, check)
    assert (check / "eboot.bin").read_bytes() == b"patched by user"


def test_scan_library(dump: Path, tmp_path: Path) -> None:
    result = RecordingBridge().scan_library([str(tmp_path)])
    assert [d["title_id"] for d in result["dumps"]] == ["PPSA77777"]


def test_ps5_probe_unreachable_is_reported() -> None:
    res = RecordingBridge().ps5_probe("127.0.0.1", 1)
    assert res["reachable"] is False and res["detail"]


def test_ps5_list_failure_returns_error() -> None:
    res = RecordingBridge().ps5_list("127.0.0.1", 1, "/")
    assert res["ok"] is False and "FTP connect failed" in res["error"]


def test_ftp_mkdir_and_rename_reject_bad_names_before_connecting() -> None:
    b = RecordingBridge()
    # invalid names are refused up front, so no FTP connection is attempted
    for bad in ("", "  ", "a/b", "..", "/"):
        assert b.ftp_mkdir("127.0.0.1", 1, "/data", bad)["ok"] is False
        assert b.ftp_rename("127.0.0.1", 1, "/data/x.bin", bad)["ok"] is False


def test_ftp_child_join() -> None:
    b = RecordingBridge()
    assert b._ftp_child("/data/etaHEN/games", "PPSA01.bin") == \
        "/data/etaHEN/games/PPSA01.bin"
    assert b._ftp_child("/", "app0") == "/app0"


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


def test_payload_catalog_exposes_bundled_and_firmware_compatibility() -> None:
    r = RecordingBridge().payload_catalog("12.70")
    assert r["ok"] and len(r["entries"]) >= 19
    assert r["source"] and r["firmware"] == "12.70"
    assert sum(1 for e in r["entries"] if e["bundled"]) == 18
    assert next(e for e in r["entries"] if e["id"] == "kstuff")["compatible"] is False
    assert next(e for e in r["entries"] if e["id"] == "kstuff-lite")["compatible"] is True
    for e in r["entries"]:
        assert e["binary_url"] or e["page_url"]


def test_install_bundled_payload_uses_managed_folder(tmp_path: Path) -> None:
    b = RecordingBridge()
    r = b.install_bundled_payload("ftpsrv-ps5")
    assert r["ok"] and Path(r["path"]).read_bytes().startswith(b"\x7fELF")
    assert b.get_settings()["payload_dir"] == r["folder"]


def test_download_needs_a_folder() -> None:
    assert RecordingBridge().download_catalog_payload("ftpsrv-ps5")["ok"] is False


def test_download_rejects_unknown_entry(tmp_path: Path) -> None:
    b = RecordingBridge()
    r = b.download_catalog_payload("nope", str(tmp_path))
    assert r["ok"] is False and "unknown" in r["error"]


def test_open_url_refuses_non_https() -> None:
    assert RecordingBridge().open_url("file:///C:/Windows")["ok"] is False


def test_klog_lines_arrive_batched() -> None:
    """Every _js() is an IPC call, so the pump must not make one per line."""
    import socket

    srv = socket.socket()
    srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    srv.bind(("127.0.0.1", 0))
    srv.listen(1)
    port = srv.getsockname()[1]
    lines = [f"kernel: line {i}" for i in range(500)]

    def serve() -> None:
        conn, _ = srv.accept()
        with conn:
            conn.sendall(("\n".join(lines) + "\n").encode())
        srv.close()

    threading.Thread(target=serve, daemon=True).start()

    b = RecordingBridge()
    b.klog_start("127.0.0.1", port)
    deadline = time.monotonic() + 15
    got: list[str] = []
    while len(got) < len(lines) and time.monotonic() < deadline:
        got = [ln for n, a in b.calls if n == "onKlog" for ln in a[0]]
        time.sleep(0.05)
    b.klog_stop()

    pushes = [a for n, a in b.calls if n == "onKlog"]
    assert got[:len(lines)] == lines
    assert all(isinstance(a[0], list) for a in pushes)
    assert len(pushes) < len(lines) / 10, \
        f"{len(pushes)} pushes for {len(lines)} lines — not batching"


def test_scan_reports_a_verdict_and_remembers_the_host() -> None:
    b = RecordingBridge()
    b.scan_ps5_ports("127.0.0.1", [1, 2])
    deadline = time.monotonic() + 30
    while b.last("onScanDone") is None and time.monotonic() < deadline:
        time.sleep(0.05)
    summary = b.last("onScanDone")[0]
    assert summary["verdict"]["confidence"] == "none"
    # the address typed in the Manager becomes the one every page uses
    assert Bridge(window=None).get_settings()["ps5_host"] == "127.0.0.1"


def test_set_ps5_host_persists_and_trims() -> None:
    b = RecordingBridge()
    assert b.set_ps5_host("  192.168.1.42 ")["host"] == "192.168.1.42"
    assert Bridge(window=None).get_settings()["ps5_host"] == "192.168.1.42"
    # an empty value must not wipe a good address
    assert b.set_ps5_host("")["host"] == "192.168.1.42"
