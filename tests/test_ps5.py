"""PS5 client tests against local mock servers.

No PlayStation is involved: an in-process pyftpdlib-free FTP stand-in is
too fragile, so FTP is covered by protocol-level unit tests plus a real
loopback server where the stdlib allows it. The kernel-log tail and the
payload sender talk plain TCP, so those are exercised end to end against
sockets on 127.0.0.1 — which is exactly the wire behaviour a console sees.
"""

from __future__ import annotations

import socket
import threading
from pathlib import Path

import pytest

from ps5_image_forge import ps5


def _serve_once(handler, host: str = "127.0.0.1") -> tuple[str, int, threading.Thread]:
    """Bind a throwaway TCP server, run ``handler(conn)`` for one client."""
    srv = socket.socket()
    srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    srv.bind((host, 0))
    srv.listen(1)
    port = srv.getsockname()[1]

    def run() -> None:
        try:
            conn, _addr = srv.accept()
            with conn:
                handler(conn)
        except OSError:
            pass
        finally:
            srv.close()

    thread = threading.Thread(target=run, daemon=True)
    thread.start()
    return host, port, thread


# ── probe ─────────────────────────────────────────────────────────

def test_probe_reports_reachable() -> None:
    host, port, _t = _serve_once(lambda conn: conn.recv(1))
    result = ps5.probe(host, port, timeout=2)
    assert result.reachable and result.latency_ms is not None


def test_probe_reports_unreachable() -> None:
    # port 1 on loopback: nothing listens there
    result = ps5.probe("127.0.0.1", 1, timeout=1)
    assert not result.reachable and result.detail


# ── kernel log ────────────────────────────────────────────────────

def test_kernel_log_streams_lines() -> None:
    def handler(conn: socket.socket) -> None:
        conn.sendall(b"[kernel] boot ok\n[kernel] etaHEN ready\n")
        conn.sendall(b"partial line without newline")

    host, port, _t = _serve_once(handler)
    log = ps5.KernelLog(host, port, timeout=3)
    lines = list(log.stream())
    assert lines[:2] == ["[kernel] boot ok", "[kernel] etaHEN ready"]
    assert lines[-1] == "partial line without newline"


def test_kernel_log_connect_failure_is_reported() -> None:
    log = ps5.KernelLog("127.0.0.1", 1, timeout=1)
    with pytest.raises(ps5.PS5Error, match="kernel log connect failed"):
        list(log.stream())


def test_kernel_log_stop_ends_stream() -> None:
    ready = threading.Event()

    def handler(conn: socket.socket) -> None:
        conn.sendall(b"line one\n")
        ready.wait(5)               # hold the connection open

    host, port, _t = _serve_once(handler)
    log = ps5.KernelLog(host, port, timeout=3)
    seen = []
    for line in log.stream():
        seen.append(line)
        log.stop()                  # stop after the first line
    ready.set()
    assert seen == ["line one"]


# ── payload sender ────────────────────────────────────────────────

def test_send_payload_transfers_bytes(tmp_path: Path) -> None:
    blob = b"\x7fELF" + bytes(range(256)) * 40
    payload = tmp_path / "goldhen.elf"
    payload.write_bytes(blob)

    received = bytearray()
    done = threading.Event()

    def handler(conn: socket.socket) -> None:
        while len(received) < len(blob):
            chunk = conn.recv(4096)
            if not chunk:
                break
            received.extend(chunk)
        done.set()

    host, port, _t = _serve_once(handler)
    events = []
    sent = ps5.send_payload(host, payload, port, progress=events.append)
    done.wait(5)

    assert sent == len(blob)
    assert bytes(received) == blob
    assert events and events[-1].done == events[-1].total


def test_send_payload_rejects_non_elf(tmp_path: Path) -> None:
    bad = tmp_path / "notelf.bin"
    bad.write_bytes(b"MZ this is a windows exe")
    with pytest.raises(ps5.PS5Error, match="not an ELF"):
        ps5.send_payload("127.0.0.1", bad, 9021)


def test_send_payload_connection_failure(tmp_path: Path) -> None:
    payload = tmp_path / "p.elf"
    payload.write_bytes(b"\x7fELF" + b"\0" * 100)
    with pytest.raises(ps5.PS5Error, match="payload send failed"):
        ps5.send_payload("127.0.0.1", payload, 1, timeout=1)


# ── ftp ───────────────────────────────────────────────────────────

def test_ftp_connect_failure_is_wrapped() -> None:
    client = ps5.PS5Ftp("127.0.0.1", 1, timeout=1)
    with pytest.raises(ps5.PS5Error, match="FTP connect failed"):
        client.connect()


def test_ftp_methods_require_connection() -> None:
    client = ps5.PS5Ftp("127.0.0.1", 2121)
    with pytest.raises(ps5.PS5Error, match="not connected"):
        client.listdir("/")


def test_ftp_list_changes_directory_before_argumentless_mlsd() -> None:
    """ftpsrv ignores MLSD's path argument, so navigation must use CWD."""
    class FakeFtp:
        def __init__(self) -> None:
            self.current = "/"
            self.calls: list[tuple] = []

        def cwd(self, path: str) -> None:
            self.calls.append(("cwd", path))
            self.current = path

        def pwd(self) -> str:
            self.calls.append(("pwd",))
            return self.current

        def mlsd(self, *args):
            self.calls.append(("mlsd", args))
            assert not args
            return iter([
                (f"{self.current}/games", {"type": "dir"}),
                (f"{self.current}/readme.txt", {"type": "file", "size": "12"}),
            ])

    fake = FakeFtp()
    client = ps5.PS5Ftp("127.0.0.1", 2121)
    client._ftp = fake  # type: ignore[assignment]

    entries = client.listdir("/mnt")

    assert fake.calls[:3] == [("cwd", "/mnt"), ("pwd",), ("mlsd", ())]
    assert [(e.name, e.is_dir, e.size) for e in entries] == [
        ("games", True, 0), ("readme.txt", False, 12)]


def test_ftp_rename_issues_rnfr_rnto() -> None:
    class FakeFtp:
        def __init__(self) -> None:
            self.cmds: list[str] = []

        def sendcmd(self, cmd: str) -> str:
            self.cmds.append(cmd)
            return "350 ready" if cmd.startswith("RNFR") else "250 renamed"

    fake = FakeFtp()
    client = ps5.PS5Ftp("127.0.0.1", 2121)
    client._ftp = fake  # type: ignore[assignment]
    client.rename("/games/old.bin", "/games/new.bin")
    assert fake.cmds == ["RNFR /games/old.bin", "RNTO /games/new.bin"]


def test_ftp_delete_accepts_226_reply() -> None:
    """PS5 ftpsrv answers DELE with 226, which must count as success."""
    class FakeFtp:
        def __init__(self) -> None:
            self.cmds: list[str] = []

        def sendcmd(self, cmd: str) -> str:
            self.cmds.append(cmd)
            return "226 File deleted"

    fake = FakeFtp()
    client = ps5.PS5Ftp("127.0.0.1", 2121)
    client._ftp = fake  # type: ignore[assignment]
    client.delete("/data/game.bin")            # must not raise
    assert fake.cmds == ["DELE /data/game.bin"]


class _TreeFtp:
    """A tiny in-memory FTP that models a directory tree for remove/download."""

    def __init__(self, tree: dict) -> None:
        self.tree = tree           # dir -> list[(name, is_dir, bytes|None)]
        self.current = "/"
        self.deleted: list[str] = []
        self.rmdirs: list[str] = []

    def cwd(self, path: str) -> None:
        self.current = path

    def pwd(self) -> str:
        return self.current

    def mlsd(self, *args):
        for name, is_dir, _ in self.tree.get(self.current, []):
            yield (name, {"type": "dir" if is_dir else "file", "size": "0"})

    def sendcmd(self, cmd: str) -> str:
        verb, path = cmd.split(" ", 1)
        if verb == "DELE":
            self.deleted.append(path)
        elif verb == "RMD":
            self.rmdirs.append(path)
        return "226 done"

    def size(self, path: str):
        for members in self.tree.values():
            for name, is_dir, data in members:
                if not is_dir and path.endswith("/" + name):
                    return len(data or b"")
        return 0

    def retrbinary(self, cmd, callback, blocksize=8192):
        path = cmd.split(" ", 1)[1]
        for members in self.tree.values():
            for name, is_dir, data in members:
                if not is_dir and path.endswith("/" + name):
                    callback(data or b"")
                    return
        raise KeyError(path)


def test_ftp_remove_recurses_and_deletes_dir_last() -> None:
    tree = {
        "/games": [("app0", True, None), ("eboot.bin", False, b"x")],
        "/games/app0": [("data.bin", False, b"y")],
    }
    fake = _TreeFtp(tree)
    client = ps5.PS5Ftp("127.0.0.1", 2121)
    client._ftp = fake  # type: ignore[assignment]

    client.remove("/games", is_dir=True)

    assert set(fake.deleted) == {"/games/app0/data.bin", "/games/eboot.bin"}
    # inner directory removed before its parent
    assert fake.rmdirs == ["/games/app0", "/games"]


def test_ftp_upload_resumes_from_remote_size(tmp_path: Path) -> None:
    class FakeFtp:
        def __init__(self, existing: int) -> None:
            self.existing = existing
            self.rest = None
            self.stored = b""

        def size(self, path: str) -> int:
            return self.existing

        def mkd(self, path: str) -> None:
            pass

        def storbinary(self, cmd, fp, blocksize=8192, callback=None, rest=None):
            self.rest = rest
            self.stored = fp.read()      # reads from fp's current position

    data = b"A" * 1000
    local = tmp_path / "rom.bin"
    local.write_bytes(data)
    fake = FakeFtp(existing=400)
    client = ps5.PS5Ftp("127.0.0.1", 2121)
    client._ftp = fake  # type: ignore[assignment]

    client.upload(local, "/data")
    assert fake.rest == 400
    assert fake.stored == data[400:]     # only the missing tail is sent


def test_ftp_upload_skips_when_already_complete(tmp_path: Path) -> None:
    class FakeFtp:
        def __init__(self) -> None:
            self.stored = False

        def size(self, path: str) -> int:
            return 1000

        def mkd(self, path: str) -> None:
            pass

        def storbinary(self, *a, **k) -> None:
            self.stored = True

    local = tmp_path / "rom.bin"
    local.write_bytes(b"A" * 1000)
    fake = FakeFtp()
    client = ps5.PS5Ftp("127.0.0.1", 2121)
    client._ftp = fake  # type: ignore[assignment]
    client.upload(local, "/data")
    assert fake.stored is False          # nothing re-sent


def test_ftp_download_resumes_from_partial_local(tmp_path: Path) -> None:
    remaining = b"B" * 600

    class FakeFtp:
        def __init__(self) -> None:
            self.rest = None

        def size(self, path: str) -> int:
            return 1000

        def retrbinary(self, cmd, callback, blocksize=8192, rest=None):
            self.rest = rest
            callback(remaining)

    local = tmp_path / "rom.bin"
    local.write_bytes(b"A" * 400)        # a partial download
    fake = FakeFtp()
    client = ps5.PS5Ftp("127.0.0.1", 2121)
    client._ftp = fake  # type: ignore[assignment]

    client.download("/games/rom.bin", local)
    assert fake.rest == 400
    assert local.read_bytes() == b"A" * 400 + remaining   # appended, not truncated


def test_ftp_download_writes_bytes_and_reports_progress(tmp_path: Path) -> None:
    payload = b"PS5 game bytes" * 100
    fake = _TreeFtp({"/games": [("rom.bin", False, payload)]})
    client = ps5.PS5Ftp("127.0.0.1", 2121)
    client._ftp = fake  # type: ignore[assignment]

    events: list = []
    local = client.download("/games/rom.bin", tmp_path / "rom.bin",
                            progress=events.append)
    assert local.read_bytes() == payload
    assert events and events[-1].done == len(payload)
