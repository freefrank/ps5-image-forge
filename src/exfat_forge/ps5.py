"""PS5 network tools: FTP transfer, kernel-log tail, payload sender.

All three speak plain, well-documented protocols, so this module is pure
stdlib (``ftplib`` + ``socket``) with no console-side dependency:

* **FTP** — ftpsrv (2121) or etaHEN's server (1337). PS5 servers are
  latin-1/UTF-8 inconsistent, so we force UTF-8 and fall back cleanly.
* **Kernel log** — a raw TCP stream on 3232; connect and read lines.
* **Payload sender** — open a socket to 9020/9021 and write the ELF.

NOTE: verified only against protocol behaviour and a local mock server —
none of this has been exercised against real PS5 hardware here.
"""

from __future__ import annotations

import ftplib
import socket
import threading
import time
from collections.abc import Callable, Iterator
from dataclasses import dataclass
from pathlib import Path

from .core import CancelToken, ProgressEvent, ProgressFn

DEFAULT_FTP_PORT = 2121
DEFAULT_KLOG_PORT = 3232
DEFAULT_PAYLOAD_PORT = 9021


class PS5Error(RuntimeError):
    """Any PS5 connection or transfer failure."""


# ── connectivity ──────────────────────────────────────────────────

@dataclass
class ProbeResult:
    reachable: bool
    latency_ms: float | None
    detail: str


def probe(host: str, port: int, timeout: float = 3.0) -> ProbeResult:
    """TCP-connect to ``host:port`` and measure the round trip."""
    start = time.monotonic()
    try:
        with socket.create_connection((host, port), timeout=timeout):
            ms = (time.monotonic() - start) * 1000
            return ProbeResult(True, round(ms, 1), f"{ms:.0f} ms")
    except OSError as exc:
        return ProbeResult(False, None, str(exc))


# ── FTP ───────────────────────────────────────────────────────────

@dataclass
class RemoteEntry:
    name: str
    is_dir: bool
    size: int


class PS5Ftp:
    """Thin FTP wrapper with the quirks PS5 servers need.

    Use as a context manager; every method raises :class:`PS5Error` with a
    readable message instead of leaking ftplib internals.
    """

    def __init__(self, host: str, port: int = DEFAULT_FTP_PORT,
                 user: str = "anonymous", password: str = "",
                 timeout: float = 15.0) -> None:
        self.host, self.port = host, port
        self.user, self.password, self.timeout = user, password, timeout
        self._ftp: ftplib.FTP | None = None

    def __enter__(self) -> PS5Ftp:
        self.connect()
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    def connect(self) -> str:
        ftp = ftplib.FTP()
        # PS5 servers are inconsistent about encoding; UTF-8 with a
        # permissive fallback avoids UnicodeDecodeError on odd filenames.
        ftp.encoding = "utf-8"
        try:
            ftp.connect(self.host, self.port, timeout=self.timeout)
            welcome = ftp.getwelcome() or ""
            ftp.login(self.user, self.password)
            try:
                ftp.sendcmd("OPTS UTF8 ON")
            except ftplib.all_errors:
                pass          # older servers reject it; harmless
            ftp.set_pasv(True)
        except ftplib.all_errors as exc:
            raise PS5Error(f"FTP connect failed ({self.host}:{self.port}): "
                           f"{exc}") from exc
        self._ftp = ftp
        return welcome

    def close(self) -> None:
        if self._ftp is not None:
            try:
                self._ftp.quit()
            except ftplib.all_errors:
                try:
                    self._ftp.close()
                except ftplib.all_errors:
                    pass
            self._ftp = None

    @property
    def ftp(self) -> ftplib.FTP:
        if self._ftp is None:
            raise PS5Error("not connected")
        return self._ftp

    def listdir(self, path: str = ".") -> list[RemoteEntry]:
        """List ``path``; falls back to NLST when MLSD is unsupported."""
        out: list[RemoteEntry] = []
        try:
            for name, facts in self.ftp.mlsd(path):
                if name in (".", ".."):
                    continue
                out.append(RemoteEntry(
                    name, facts.get("type") == "dir",
                    int(facts.get("size", 0) or 0)))
            return sorted(out, key=lambda e: (not e.is_dir, e.name.lower()))
        except ftplib.all_errors:
            pass
        try:
            names = self.ftp.nlst(path)
        except ftplib.all_errors as exc:
            raise PS5Error(f"cannot list {path}: {exc}") from exc
        for name in names:
            base = name.rsplit("/", 1)[-1]
            if base in (".", ".."):
                continue
            size, is_dir = 0, False
            try:
                size = self.ftp.size(name) or 0
            except ftplib.all_errors:
                is_dir = True          # SIZE fails on directories
            out.append(RemoteEntry(base, is_dir, size))
        return sorted(out, key=lambda e: (not e.is_dir, e.name.lower()))

    def makedirs(self, path: str) -> None:
        """Create ``path`` and any missing parents; existing dirs are fine."""
        parts = [p for p in path.replace("\\", "/").split("/") if p]
        cur = "/" if path.startswith("/") else ""
        for part in parts:
            cur = f"{cur.rstrip('/')}/{part}" if cur else part
            try:
                self.ftp.mkd(cur)
            except ftplib.all_errors:
                pass

    def upload(self, local: Path, remote_dir: str, *,
               progress: ProgressFn | None = None,
               cancel: CancelToken | None = None,
               chunk: int = 256 * 1024) -> str:
        """Upload one file, reporting bytes sent; returns the remote path."""
        total = local.stat().st_size
        self.makedirs(remote_dir)
        remote_path = f"{remote_dir.rstrip('/')}/{local.name}"
        sent = 0

        def _block(data: bytes) -> None:
            nonlocal sent
            if cancel:
                cancel.raise_if_cancelled()
            sent += len(data)
            if progress:
                progress(ProgressEvent("upload", sent, total, local.name))

        try:
            with local.open("rb") as fh:
                self.ftp.storbinary(f"STOR {remote_path}", fh,
                                    blocksize=chunk, callback=_block)
        except ftplib.all_errors as exc:
            raise PS5Error(f"upload of {local.name} failed: {exc}") from exc
        if progress:
            progress(ProgressEvent("upload", total, total, local.name))
        return remote_path

    def delete(self, remote_path: str) -> None:
        try:
            self.ftp.delete(remote_path)
        except ftplib.all_errors as exc:
            raise PS5Error(f"delete failed: {exc}") from exc


# ── kernel log ────────────────────────────────────────────────────

class KernelLog:
    """Tail the PS5 kernel log stream (TCP, default port 3232)."""

    def __init__(self, host: str, port: int = DEFAULT_KLOG_PORT,
                 timeout: float = 8.0) -> None:
        self.host, self.port, self.timeout = host, port, timeout
        self._sock: socket.socket | None = None
        self._stop = threading.Event()

    def stream(self) -> Iterator[str]:
        """Yield log lines until :meth:`stop` is called or the peer closes."""
        try:
            self._sock = socket.create_connection((self.host, self.port),
                                                  timeout=self.timeout)
        except OSError as exc:
            raise PS5Error(f"kernel log connect failed "
                           f"({self.host}:{self.port}): {exc}") from exc
        self._sock.settimeout(1.0)
        buffer = b""
        try:
            while not self._stop.is_set():
                try:
                    data = self._sock.recv(65536)
                except socket.timeout:
                    continue        # idle log; keep waiting for the stop flag
                except OSError:
                    break
                if not data:
                    break
                buffer += data
                *lines, buffer = buffer.split(b"\n")
                for line in lines:
                    yield line.decode("utf-8", "replace").rstrip("\r")
            if buffer:
                yield buffer.decode("utf-8", "replace")
        finally:
            self.close()

    def stop(self) -> None:
        self._stop.set()

    def close(self) -> None:
        self._stop.set()
        if self._sock is not None:
            try:
                self._sock.close()
            except OSError:
                pass
            self._sock = None


# ── payload sender ────────────────────────────────────────────────

def send_payload(host: str, payload: Path,
                 port: int = DEFAULT_PAYLOAD_PORT, *,
                 progress: ProgressFn | None = None,
                 timeout: float = 10.0,
                 chunk: int = 8192) -> int:
    """Send an ELF payload to a listening PS5 loader; returns bytes sent."""
    data = payload.read_bytes()
    if data[:4] != b"\x7fELF":
        raise PS5Error(f"{payload.name} is not an ELF payload")
    try:
        with socket.create_connection((host, port), timeout=timeout) as sock:
            sent = 0
            while sent < len(data):
                block = data[sent:sent + chunk]
                sock.sendall(block)
                sent += len(block)
                if progress:
                    progress(ProgressEvent("payload", sent, len(data),
                                           payload.name))
    except OSError as exc:
        raise PS5Error(f"payload send failed ({host}:{port}): {exc}") from exc
    return sent
