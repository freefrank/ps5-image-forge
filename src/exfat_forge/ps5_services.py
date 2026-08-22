"""Known PS5 (jailbreak / homebrew) network services, and a host scanner.

The port map is public homebrew-scene knowledge — the same values the tool
this replaces used (2121/9021/3232/9090) plus the other loaders people run.
It is reference data only; nothing here reaches out on its own.

:func:`scan_host` probes ONE host — the user's own console on their LAN —
for these ports concurrently. It is deliberately not a subnet/range scanner:
there is no discovery of other machines, only a check of the address you
type. A TCP connect is all it takes to tell whether a loader is listening.
"""

from __future__ import annotations

import socket
import threading
from concurrent.futures import ThreadPoolExecutor
from dataclasses import asdict, dataclass


@dataclass(frozen=True)
class Service:
    """One well-known homebrew service."""

    port: int
    key: str
    name: str
    kind: str            # ftp | payload | log | http | rpc | other
    note: str


# Ordered by how commonly they matter when checking a jailbroken console.
KNOWN_SERVICES: tuple[Service, ...] = (
    Service(2121, "ftp", "FTP (ftpsrv)", "ftp",
            "GoldHEN / ftpsrv file server — browse and push games"),
    Service(1337, "ftp_etahen", "FTP (etaHEN)", "ftp",
            "etaHEN's built-in FTP server"),
    Service(9021, "elfldr", "ELF loader", "payload",
            "etaHEN elfldr — send .elf payloads here"),
    Service(9020, "payload", "Payload port", "payload",
            "Classic payload/ELF loader port"),
    Service(9090, "payload_alt", "Payload (alt)", "payload",
            "Alternate loader port used by some payloads"),
    Service(3232, "klog", "Kernel log", "log",
            "Kernel log stream — live console output"),
    Service(3233, "klog_alt", "Kernel log (alt)", "log",
            "Alternate kernel-log port"),
    Service(9022, "shadowmount", "ShadowMount+", "rpc",
            "ShadowMount+ remote mount service"),
    Service(9023, "micromount", "MicroMount", "rpc",
            "MicroMount remote mount service"),
    Service(8080, "webman", "Web UI", "http",
            "Some payloads expose an HTTP control panel here"),
    Service(3000, "itemzflow", "itemzflow", "http",
            "itemzflow game manager web UI"),
)

SERVICE_BY_PORT = {s.port: s for s in KNOWN_SERVICES}


@dataclass
class PortResult:
    port: int
    key: str
    name: str
    kind: str
    note: str
    open: bool
    latency_ms: float | None


def _probe_port(host: str, port: int, timeout: float) -> tuple[bool, float | None]:
    import time
    start = time.monotonic()
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True, round((time.monotonic() - start) * 1000, 1)
    except OSError:
        return False, None


def scan_host(host: str, *,
              ports: list[int] | None = None,
              timeout: float = 1.2,
              on_result=None,
              cancel: threading.Event | None = None) -> list[PortResult]:
    """Probe ``host`` for the known service ports (or a custom list).

    Runs the probes concurrently; ``on_result(PortResult)`` is called as each
    finishes so a UI can fill the table live. Returns the full list in the
    canonical service order.
    """
    targets = ports or [s.port for s in KNOWN_SERVICES]
    results: dict[int, PortResult] = {}

    def work(port: int) -> None:
        if cancel and cancel.is_set():
            return
        svc = SERVICE_BY_PORT.get(port)
        is_open, latency = _probe_port(host, port, timeout)
        res = PortResult(
            port=port, key=svc.key if svc else f"port_{port}",
            name=svc.name if svc else f"port {port}",
            kind=svc.kind if svc else "other",
            note=svc.note if svc else "",
            open=is_open, latency_ms=latency)
        results[port] = res
        if on_result:
            on_result(res)

    with ThreadPoolExecutor(max_workers=min(16, len(targets))) as pool:
        list(pool.map(work, targets))

    order = {p: i for i, p in enumerate(targets)}
    return sorted(results.values(), key=lambda r: (not r.open, order.get(r.port, 999)))


#: Ports that only a jailbroken console has any reason to be running. A
#: bare TCP connect cannot prove what is on the other end, so the verdict is
#: stated as confidence, never as fact — see :func:`identify`.
LOADER_PORTS = (9021, 9020, 9090)


@dataclass
class Verdict:
    """What the open ports suggest about the host that answered."""

    is_ps5: bool
    confidence: str          # high | likely | unlikely | none
    reason: str              # i18n key the page renders
    loader_port: int | None  # where payloads should go, when we found one
    open_ports: list[int]


def identify(results: list[PortResult]) -> Verdict:
    """Read a scan as "is this a jailbroken PS5?".

    9021 is the strong signal: it is etaHEN's elfldr, and nothing else puts
    a listener there. The other loader ports and the FTP ports are weaker —
    plenty of machines run an FTP server on 2121 — so they downgrade the
    verdict rather than confirm it.
    """
    open_ports = [r.port for r in results if r.open]
    if not open_ports:
        return Verdict(False, "none", "ps5.verdict.none", None, [])

    if 9021 in open_ports:
        return Verdict(True, "high", "ps5.verdict.high", 9021, open_ports)

    loader = next((p for p in LOADER_PORTS if p in open_ports), None)
    if loader:
        return Verdict(True, "likely", "ps5.verdict.likely", loader, open_ports)

    if any(p in open_ports for p in (2121, 1337, 3232, 3233)):
        return Verdict(False, "unlikely", "ps5.verdict.unlikely", None,
                       open_ports)

    return Verdict(False, "unlikely", "ps5.verdict.other", None, open_ports)


def services_as_dicts() -> list[dict]:
    return [asdict(s) for s in KNOWN_SERVICES]
