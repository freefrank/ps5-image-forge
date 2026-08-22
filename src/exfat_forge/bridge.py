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
import traceback
from dataclasses import asdict
from pathlib import Path

from . import core, i18n, library, pipeline, ps5, ufs
from .settings import History, Settings


class Bridge:
    """Backend surface exposed to the page."""

    def __init__(self, window=None) -> None:
        self.window = window
        self.settings = Settings.load()
        self._worker: threading.Thread | None = None
        self._cancel: core.CancelToken | None = None
        self._klog: ps5.KernelLog | None = None

    # ── plumbing ──────────────────────────────────────────────────

    def _js(self, fn: str, *args) -> None:
        if self.window is None:
            return
        payload = ", ".join(json.dumps(a, ensure_ascii=False, default=str)
                            for a in args)
        try:
            self.window.evaluate_js(f"window.forge.{fn}({payload})")
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
        if self.window:
            self.window.minimize()

    def toggle_maximize(self) -> None:
        if self.window:
            self.window.toggle_fullscreen()

    def close(self) -> None:
        if self._klog:
            self._klog.stop()
        if self.window:
            self.window.destroy()

    # ── settings ──────────────────────────────────────────────────

    def get_settings(self) -> dict:
        data = self.settings.as_dict()
        data["lang"] = i18n.get_locale()
        return data

    def save_settings(self, values: dict) -> dict:
        self.settings.update(values)
        self.settings.save()
        if values.get("lang"):
            i18n.set_locale(values["lang"])
        return self.get_settings()

    def get_lang(self) -> str:
        return i18n.get_locale()

    def set_lang(self, lang: str) -> None:
        i18n.set_locale(lang)
        self.settings.lang = lang
        self.settings.save()

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
        if not self.window:
            return None
        res = self.window.create_file_dialog(webview.FOLDER_DIALOG)
        return (res[0] if isinstance(res, (list, tuple)) else res) if res else None

    def pick_file(self, patterns: list[str] | None = None) -> str | None:
        import webview
        if not self.window:
            return None
        types = tuple(patterns) if patterns else (
            "Game images (*.exfat;*.ffpkg;*.ffpfsc)", "All files (*.*)")
        res = self.window.create_file_dialog(webview.OPEN_DIALOG,
                                             file_types=types)
        return (res[0] if isinstance(res, (list, tuple)) else res) if res else None

    # ── library ───────────────────────────────────────────────────

    def scan_library(self, folders: list[str] | None = None) -> dict:
        dirs = folders if folders is not None else self.settings.library_dirs
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
            try:
                for line in self._klog.stream():
                    self._js("onKlog", line)
            except ps5.PS5Error as exc:
                self._js("onKlogError", str(exc))

        threading.Thread(target=pump, daemon=True).start()

    def klog_stop(self) -> None:
        if self._klog:
            self._klog.stop()
            self._klog = None
