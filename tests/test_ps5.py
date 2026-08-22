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
