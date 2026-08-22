"""Known-service table and the single-host port scanner.

The scanner is checked against real loopback sockets: a listening socket
stands in for a payload loader, and a port nothing binds stands in for a
closed one. That is the same signal a console gives, so the test covers
the actual decision the UI shows rather than a mocked-out version of it.
"""

from __future__ import annotations

import socket
import threading

from exfat_forge import ps5_services as svc


def _listener() -> tuple[int, socket.socket]:
    """Bind a loopback port and keep it listening for the test's duration."""
    srv = socket.socket()
    srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    srv.bind(("127.0.0.1", 0))
    srv.listen(4)
    return srv.getsockname()[1], srv


# ── the table ─────────────────────────────────────────────────────

def test_known_services_are_well_formed() -> None:
    assert svc.KNOWN_SERVICES
    ports = [s.port for s in svc.KNOWN_SERVICES]
    keys = [s.key for s in svc.KNOWN_SERVICES]
    assert len(set(ports)) == len(ports), "duplicate port in the table"
    assert len(set(keys)) == len(keys), "duplicate key in the table"
    for s in svc.KNOWN_SERVICES:
        assert 1 <= s.port <= 65535
        assert s.name and s.note
        assert s.kind in {"ftp", "payload", "log", "http", "rpc", "other"}


def test_table_covers_the_ports_the_rest_of_the_app_uses() -> None:
    # these are the defaults the FTP / klog / payload pages hand out, and
    # the ones the tool this replaces shipped with — the Manager page is
    # useless if it cannot tell you about them.
    for port in (2121, 9021, 3232, 9090):
        assert port in svc.SERVICE_BY_PORT


def test_services_as_dicts_is_json_shaped() -> None:
    rows = svc.services_as_dicts()
    assert len(rows) == len(svc.KNOWN_SERVICES)
    assert set(rows[0]) == {"port", "key", "name", "kind", "note"}


# ── scanning ──────────────────────────────────────────────────────

def test_scan_finds_an_open_port_and_misses_a_closed_one() -> None:
    port, srv = _listener()
    try:
        results = svc.scan_host("127.0.0.1", ports=[port, 1], timeout=1.0)
    finally:
        srv.close()

    by_port = {r.port: r for r in results}
    assert by_port[port].open and by_port[port].latency_ms is not None
    assert not by_port[1].open and by_port[1].latency_ms is None


def test_open_ports_sort_first() -> None:
    port, srv = _listener()
    try:
        # closed port listed first; the open one must still lead the result
        results = svc.scan_host("127.0.0.1", ports=[1, port], timeout=1.0)
    finally:
        srv.close()
    assert results[0].port == port


def test_unknown_port_gets_a_placeholder_label() -> None:
    (res,) = svc.scan_host("127.0.0.1", ports=[1], timeout=1.0)
    assert res.key == "port_1" and res.kind == "other" and res.name


def test_known_port_carries_its_description() -> None:
    (res,) = svc.scan_host("127.0.0.1", ports=[2121], timeout=1.0)
    assert res.key == "ftp" and res.note


def test_on_result_fires_once_per_port() -> None:
    seen: list[svc.PortResult] = []
    lock = threading.Lock()

    def record(r: svc.PortResult) -> None:
        with lock:
            seen.append(r)

    ports = [1, 2, 3, 4]
    results = svc.scan_host("127.0.0.1", ports=ports, timeout=0.5,
                            on_result=record)
    assert len(results) == len(ports)
    assert sorted(r.port for r in seen) == ports


def test_cancel_skips_the_probes() -> None:
    cancel = threading.Event()
    cancel.set()
    results = svc.scan_host("127.0.0.1", ports=[1, 2, 3], timeout=5.0,
                            cancel=cancel)
    assert results == []


def test_default_scan_covers_every_known_service() -> None:
    results = svc.scan_host("127.0.0.1", timeout=0.3)
    assert {r.port for r in results} == {s.port for s in svc.KNOWN_SERVICES}


# ── verdict ───────────────────────────────────────────────────────

def _open(*ports: int) -> list[svc.PortResult]:
    return [svc.PortResult(p, "k", "n", "payload", "", True, 1.0)
            for p in ports]


def test_9021_confirms_a_jailbroken_console() -> None:
    v = svc.identify(_open(9021, 2121))
    assert v.is_ps5 and v.confidence == "high" and v.loader_port == 9021


def test_another_loader_port_is_only_likely() -> None:
    v = svc.identify(_open(9090))
    assert v.is_ps5 and v.confidence == "likely" and v.loader_port == 9090


def test_ftp_alone_does_not_make_it_a_ps5() -> None:
    # plenty of machines run an FTP server on 2121
    v = svc.identify(_open(2121, 3232))
    assert not v.is_ps5 and v.confidence == "unlikely" and v.loader_port is None


def test_unrelated_ports_are_not_a_ps5() -> None:
    assert svc.identify(_open(8080)).reason == "ps5.verdict.other"


def test_silence_is_reported_as_silence() -> None:
    v = svc.identify([svc.PortResult(9021, "k", "n", "payload", "", False, None)])
    assert not v.is_ps5 and v.confidence == "none" and v.open_ports == []


def test_every_verdict_key_is_translated() -> None:
    """The verdict text is picked at runtime, so the key check must be too."""
    import re
    from pathlib import Path

    src = Path(svc.__file__).read_text(encoding="utf-8")
    keys = set(re.findall(r'"(ps5\.verdict\.[a-z]+)"', src))
    assert keys, "no verdict keys found — did they get renamed?"

    i18n_js = (Path(svc.__file__).parent / "webui" / "i18n.js").read_text(
        encoding="utf-8")
    for table in ("en", "zh"):
        section = i18n_js.split(f"  {table}: {{", 1)[1]
        for key in keys:
            assert f'"{key}"' in section, f"{key} missing from the {table} table"
