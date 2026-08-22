"""JS-facing API for the web GUI.

Every method here is callable from the page as ``window.pywebview.api.*``.
Long-running work is dispatched to a worker thread and reports back by
pushing into JS; short queries return their value directly.

Keeping this separate from :mod:`gui` means the whole surface the UI can
reach is one readable file, and it can be driven from tests without a
window.
"""

from __future__ import annotations

import json
import threading
import time
import traceback
from dataclasses import asdict
from pathlib import Path

from . import (catalog, core, i18n, library, payloads, pipeline, ps5,
               ps5_services, ufs)
from .settings import History, Settings


class Bridge:
    """Backend surface exposed to the page."""

    def __init__(self, window=None) -> None:
        # Everything the page must not see stays underscored: pywebview 6.x
        # builds its JS API by walking every public attribute of this object
        # recursively, so a plain ``self.window`` sends it down the whole
        # WinForms control tree — a huge malformed function table that makes
        # each call slow and shadows real methods.
        self._window = window
        self._settings = Settings.load()
        self._worker: threading.Thread | None = None
        self._cancel: core.CancelToken | None = None
        self._klog: ps5.KernelLog | None = None
        self._scanning: threading.Thread | None = None
        self._scan_cancel: threading.Event | None = None
        self._dl_cancel: threading.Event | None = None
        self._downloading: threading.Thread | None = None

    # ── plumbing ──────────────────────────────────────────────────

    def _js(self, fn: str, *args) -> None:
        if self._window is None:
            return
        payload = ", ".join(json.dumps(a, ensure_ascii=False, default=str)
                            for a in args)
        try:
            self._window.evaluate_js(f"window.forge.{fn}({payload})")
        except Exception:
            pass          # window closing

    def _log(self, msg: str, cls: str = "") -> None:
        self._js("onLog", msg, cls)

    def _progress(self, ev: core.ProgressEvent) -> None:
        self._js("onProgress", {"phase": ev.phase, "done": ev.done,
                                "total": ev.total, "detail": ev.detail})

    def _busy(self) -> bool:
        return self._worker is not None and self._worker.is_alive()

    def _spawn(self, fn, *args) -> None:
        if self._busy():
            self._log(i18n.t("busy"), "err")
            return
        self._cancel = core.CancelToken()
        self._worker = threading.Thread(target=self._guard, args=(fn, *args),
                                        daemon=True)
        self._worker.start()

    def _guard(self, fn, *args) -> None:
        try:
            fn(*args)
        except core.BuildCancelled:
            self._js("onCancelled")
        except Exception as exc:
            traceback.print_exc()
            self._js("onError", str(exc))

    # ── window ────────────────────────────────────────────────────

    def minimize(self) -> None:
        if self._window:
            self._window.minimize()

    def toggle_maximize(self) -> None:
        if self._window:
            self._window.toggle_fullscreen()

    def close(self) -> None:
        if self._klog:
            self._klog.stop()
        if self._window:
            self._window.destroy()

    # ── settings ──────────────────────────────────────────────────

    def get_settings(self) -> dict:
        data = self._settings.as_dict()
        data["lang"] = i18n.get_locale()
        return data

    def save_settings(self, values: dict) -> dict:
        self._settings.update(values)
        self._settings.save()
        if values.get("lang"):
            i18n.set_locale(values["lang"])
        return self.get_settings()

    def get_lang(self) -> str:
        return i18n.get_locale()

    def set_lang(self, lang: str) -> None:
        i18n.set_locale(lang)
        self._settings.lang = lang
        self._settings.save()

    def environment(self) -> dict:
        """Capability report shown on the Home dashboard."""
        dn = ufs.dotnet_status()
        return {
            "ffpkg": {"available": ufs.tool_available() and dn.available,
                      "detail": dn.detail,
                      "tool": ufs.tool_available()},
            "version": __import__("exfat_forge").__version__,
        }

    # ── dialogs ───────────────────────────────────────────────────

    def pick_folder(self) -> str | None:
        import webview
        if not self._window:
            return None
        res = self._window.create_file_dialog(webview.FOLDER_DIALOG)
        return (res[0] if isinstance(res, (list, tuple)) else res) if res else None

    def pick_file(self, patterns: list[str] | None = None) -> str | None:
        import webview
        if not self._window:
            return None
        types = tuple(patterns) if patterns else (
            "Game images (*.exfat;*.ffpkg;*.ffpfsc)", "All files (*.*)")
        res = self._window.create_file_dialog(webview.OPEN_DIALOG,
                                             file_types=types)
        return (res[0] if isinstance(res, (list, tuple)) else res) if res else None

    # ── library ───────────────────────────────────────────────────

    def scan_library(self, folders: list[str] | None = None) -> dict:
        dirs = folders if folders is not None else self._settings.library_dirs
        dumps, images = library.scan_folders(dirs)
        return {"dumps": [asdict(d) for d in dumps],
                "images": [asdict(i) for i in images]}

    def inspect_source(self, path: str) -> dict:
        """Quick metadata read for the build form (no full size walk)."""
        p = Path(path)
        if not p.is_dir():
            return {"ok": False, "error": "not a directory"}
        title_id, title, version = core.read_param_json(p)
        return {"ok": True, "title_id": title_id or "", "title": title or "",
                "version": version or "",
                "has_eboot": (p / "eboot.bin").is_file()}

    # ── history ───────────────────────────────────────────────────

    def get_history(self) -> list[dict]:
        return [asdict(e) for e in History().load()]

    def clear_history(self) -> None:
        History().clear()

    # ── build ─────────────────────────────────────────────────────

    def start_build(self, opts: dict) -> None:
        self._spawn(self._run_build, opts)

    def cancel(self) -> None:
        if self._cancel:
            self._cancel.cancel()

    def _run_build(self, opts: dict) -> None:
        source = Path(str(opts.get("source", "")).strip('" '))
        out_dir = Path(str(opts.get("output", "")).strip('" ')
                       or (source.parent if source.exists() else "."))
        spec = pipeline.JobSpec(
            source=source, output_dir=out_dir,
            fmt=opts.get("mode", "exfat"),
            intermediate=opts.get("intermediate", "exfat"),
            verify=bool(opts.get("verify", True)),
            cluster_size=(int(opts["cluster"]) or None) if opts.get("cluster") else None,
            compress=bool(opts.get("compress", True)),
            level=max(1, min(9, int(opts.get("level", 9)))),
            threads=int(opts["threads"]) or None if opts.get("threads") else None,
            keep_intermediate=bool(opts.get("keep_intermediate", False)),
            ffpkg_block=int(opts.get("ffpkg_block", 65536)),
            ffpkg_frag=int(opts.get("ffpkg_frag", 65536)),
            ffpkg_minfree=int(opts.get("ffpkg_minfree", 0)),
        )
        if source.is_dir() and not (source / "eboot.bin").is_file():
            self._log(i18n.t("log.no_eboot"), "warn")

        result = pipeline.run_job(spec, progress=self._progress,
                                  cancel=self._cancel)
        gb = result.size_bytes / 2**30
        mins, secs = divmod(int(result.duration_s), 60)
        self._log(i18n.t("build.ok", path=result.output.name,
                         size=f"{gb:.2f} GB"), "ok")
        self._js("onDone", f"{result.output.name} · {gb:.2f} GB · "
                           f"{mins}m {secs:02d}s")

    # ── extract ───────────────────────────────────────────────────

    def start_extract(self, image: str, dest: str, overwrite: bool = False) -> None:
        self._spawn(self._run_extract, image, dest, overwrite)

    def _run_extract(self, image: str, dest: str, overwrite: bool) -> None:
        count = pipeline.extract_any(Path(image), Path(dest),
                                     progress=self._progress,
                                     cancel=self._cancel, overwrite=overwrite)
        self._log(i18n.t("extract.done", count=count, dest=dest), "ok")
        self._js("onDone", i18n.t("extract.done", count=count, dest=dest))

    # ── inspect an image ──────────────────────────────────────────

    def inspect_image(self, path: str) -> dict:
        p = Path(path)
        ext = p.suffix.lower()
        try:
            if ext == ".exfat":
                from mkpfs.exfat import open_exfat, render_exfat_tree
                reader = open_exfat(str(p))
                files = [e for e in reader.iter_files() if not e.is_dir]
                return {"ok": True, "fmt": "exfat",
                        "file_count": len(files),
                        "total": sum(e.length for e in files),
                        "tree": render_exfat_tree(reader.root_entries())[:400]}
            if ext == ".ffpkg":
                return {"ok": True, "fmt": "ffpkg",
                        "info": ufs.info_ffpkg(p),
                        "tree": ufs.list_ffpkg(p)[:400]}
            return {"ok": False, "error": f"cannot inspect {ext} yet"}
        except Exception as exc:
            return {"ok": False, "error": str(exc)}

    # ── PS5 ───────────────────────────────────────────────────────

    def ps5_probe(self, host: str, port: int) -> dict:
        res = ps5.probe(host, int(port))
        return {"reachable": res.reachable, "latency_ms": res.latency_ms,
                "detail": res.detail}

    def list_known_services(self) -> list[dict]:
        return ps5_services.services_as_dicts()

    def scan_ps5_ports(self, host: str, ports: list[int] | None = None) -> dict:
        """Probe one console for the known service ports.

        Runs on its own thread rather than through :meth:`_spawn`: a scan is
        short and read-only, so it must not be refused because a build is
        running, nor share the build's cancel token.
        """
        host = (host or "").strip()
        if not host:
            return {"ok": False, "error": "no host"}
        if self._scanning and self._scanning.is_alive():
            return {"ok": False, "error": "scan already running"}

        wanted = [int(p) for p in ports] if ports else None
        self._scan_cancel = threading.Event()
        cancel = self._scan_cancel

        def run() -> None:
            try:
                results = ps5_services.scan_host(
                    host, ports=wanted, cancel=cancel,
                    on_result=lambda r: self._js("onPortResult", asdict(r)))
            except Exception as exc:                    # DNS, bad host, ...
                self._js("onScanError", str(exc))
                return
            self._js("onScanDone", {
                "host": host,
                "open": sum(1 for r in results if r.open),
                "total": len(results),
                "cancelled": cancel.is_set(),
            })

        self._scanning = threading.Thread(target=run, daemon=True)
        self._scanning.start()
        return {"ok": True, "host": host}

    def cancel_scan(self) -> None:
        if self._scan_cancel:
            self._scan_cancel.set()

    def ps5_list(self, host: str, port: int, path: str) -> dict:
        try:
            with ps5.PS5Ftp(host, int(port)) as ftp:
                return {"ok": True,
                        "entries": [asdict(e) for e in ftp.listdir(path)]}
        except ps5.PS5Error as exc:
            return {"ok": False, "error": str(exc)}

    def ps5_upload(self, host: str, port: int, files: list[str],
                   remote_dir: str) -> None:
        self._spawn(self._run_upload, host, int(port), files, remote_dir)

    def _run_upload(self, host: str, port: int, files: list[str],
                    remote_dir: str) -> None:
        with ps5.PS5Ftp(host, port) as ftp:
            for name in files:
                path = Path(name)
                remote = ftp.upload(path, remote_dir, progress=self._progress,
                                    cancel=self._cancel)
                self._log(i18n.t("upload.ok", path=remote), "ok")
        self._js("onDone", i18n.t("upload.all", count=len(files)))

    # ── payload library ───────────────────────────────────────────

    def scan_payloads(self, folder: str = "") -> dict:
        """Describe every payload in ``folder`` (defaults to the saved one)."""
        target = folder.strip('" ') or self._settings.payload_dir
        if not target:
            return {"ok": False, "error": "no folder selected", "items": []}
        try:
            items = payloads.scan(Path(target))
        except payloads.PayloadError as exc:
            return {"ok": False, "error": str(exc), "items": []}
        if target != self._settings.payload_dir:
            self._settings.payload_dir = target
            self._settings.save()
        return {"ok": True, "folder": target, "items": payloads.as_dicts(items)}

    def describe_payload(self, path: str) -> dict:
        try:
            return {"ok": True, **asdict(payloads.describe(Path(path)))}
        except OSError as exc:
            return {"ok": False, "error": str(exc)}

    def save_payload_note(self, path: str, note: str) -> dict:
        payloads.save_note(path, note)
        return {"ok": True}

    # ── payload catalogue ─────────────────────────────────────────

    def payload_catalog(self) -> dict:
        """Metadata for the known payloads. Ships no binaries — see catalog.py."""
        try:
            return {"ok": True, "source": catalog.metadata_source(),
                    "entries": catalog.as_dicts()}
        except catalog.CatalogError as exc:
            return {"ok": False, "error": str(exc), "entries": []}

    def download_catalog_payload(self, entry_id: str, folder: str = "",
                                 overwrite: bool = False) -> dict:
        """Fetch one catalogue entry into the user's payload folder."""
        target = (folder or "").strip('" ') or self._settings.payload_dir
        if not target:
            return {"ok": False, "error": "no payload folder selected"}
        try:
            entry = catalog.find(entry_id)
        except catalog.CatalogError as exc:
            return {"ok": False, "error": str(exc)}
        if self._dl_cancel and not self._dl_cancel.is_set() and                 self._downloading and self._downloading.is_alive():
            return {"ok": False, "error": "a download is already running"}

        self._dl_cancel = threading.Event()
        cancel = self._dl_cancel

        def run() -> None:
            try:
                path = catalog.download(
                    entry, Path(target), cancel=cancel, overwrite=bool(overwrite),
                    progress=lambda d, n: self._js("onDownload", {
                        "id": entry.id, "done": d, "total": n}))
            except catalog.CatalogError as exc:
                self._js("onDownloadError", {"id": entry.id, "error": str(exc)})
                return
            self._js("onDownloadDone", {"id": entry.id, "path": str(path),
                                        "name": path.name})

        self._downloading = threading.Thread(target=run, daemon=True)
        self._downloading.start()
        return {"ok": True, "folder": target, "file": entry.file}

    def cancel_download(self) -> None:
        if self._dl_cancel:
            self._dl_cancel.set()

    def open_url(self, url: str) -> dict:
        """Open a project page in the system browser, at the user's click."""
        if not url.startswith("https://"):
            return {"ok": False, "error": "refusing non-https url"}
        import webbrowser
        webbrowser.open(url)
        return {"ok": True}

    def ps5_send_payload(self, host: str, port: int, payload: str) -> dict:
        try:
            sent = ps5.send_payload(host, Path(payload), int(port),
                                    progress=self._progress)
            self._log(i18n.t("payload.ok", name=Path(payload).name,
                             bytes=sent), "ok")
            return {"ok": True, "sent": sent}
        except ps5.PS5Error as exc:
            self._log(str(exc), "err")
            return {"ok": False, "error": str(exc)}

    def klog_start(self, host: str, port: int) -> None:
        if self._klog:
            self._klog.stop()
        self._klog = ps5.KernelLog(host, int(port))

        def pump() -> None:
            # Batch the lines. Every _js() is a synchronous IPC call, and a
            # console under load emits far more lines per second than the UI
            # thread can absorb one at a time -- the same cost that made
            # window dragging stall.
            batch: list[str] = []
            last = time.monotonic()
            try:
                for line in self._klog.stream():
                    batch.append(line)
                    now = time.monotonic()
                    if len(batch) >= 200 or now - last >= 0.1:
                        self._js("onKlog", batch)
                        batch, last = [], now
                if batch:
                    self._js("onKlog", batch)
            except ps5.PS5Error as exc:
                if batch:
                    self._js("onKlog", batch)
                self._js("onKlogError", str(exc))

        threading.Thread(target=pump, daemon=True).start()

    def klog_stop(self) -> None:
        if self._klog:
            self._klog.stop()
            self._klog = None
