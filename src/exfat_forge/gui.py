"""Cyberpunk web GUI: a pywebview (WebView2) shell over the core pipeline.

The whole interface lives in ``webui/index.html`` (self-contained HTML/CSS/JS
with its own i18n table and a bridge-less demo mode for browser preview).
This module is only plumbing: an ``Api`` object exposed to JS, and progress
pushed back with ``evaluate_js`` from the worker thread.
"""

from __future__ import annotations

import json
import sys
import threading
from pathlib import Path

import webview

from . import core, i18n


def _webui_index() -> Path:
    if getattr(sys, "frozen", False):
        base = Path(sys._MEIPASS)  # type: ignore[attr-defined]
        return base / "exfat_forge" / "webui" / "index.html"
    return Path(__file__).parent / "webui" / "index.html"


class Api:
    """Methods callable from JS as ``window.pywebview.api.*``."""

    def __init__(self) -> None:
        self.window: webview.Window | None = None
        self._worker: threading.Thread | None = None
        self._cancel: core.CancelToken | None = None

    # ── small sync calls ──────────────────────────────────────────

    def get_lang(self) -> str:
        return i18n.get_locale()

    def set_lang(self, lang: str) -> None:
        i18n.set_locale(lang)

    def pick_folder(self) -> str | None:
        assert self.window is not None
        result = self.window.create_file_dialog(webview.FOLDER_DIALOG)
        if result:
            return result[0] if isinstance(result, (list, tuple)) else result
        return None

    def suggest_output(self, source: str) -> str:
        return str(Path(source).parent)

    def cancel(self) -> None:
        if self._cancel:
            self._cancel.cancel()

    # ── build ─────────────────────────────────────────────────────

    def start_build(self, opts: dict) -> None:
        if self._worker and self._worker.is_alive():
            return
        self._cancel = core.CancelToken()
        self._worker = threading.Thread(
            target=self._run, args=(opts, self._cancel), daemon=True)
        self._worker.start()

    # ── worker side: push into JS ─────────────────────────────────

    def _js(self, fn: str, *args) -> None:
        if self.window is None:
            return
        payload = ", ".join(json.dumps(a, ensure_ascii=False) for a in args)
        try:
            self.window.evaluate_js(f"window.forge.{fn}({payload})")
        except Exception:
            pass  # window is closing; nothing useful to do

    def _run(self, opts: dict, cancel: core.CancelToken) -> None:
        log = lambda msg, *cls: self._js("onLog", msg, *cls)
        progress: core.ProgressFn = lambda ev: self._js(
            "onProgress", {"phase": ev.phase, "done": ev.done,
                           "total": ev.total, "detail": ev.detail})
        try:
            source = Path(opts["source"].strip('" '))
            output = Path(opts.get("output", "").strip('" ') or source.parent)
            if not source.is_dir():
                raise FileNotFoundError(str(source))
            if not (source / "eboot.bin").is_file():
                log(i18n.t("log.no_eboot"))

            info = core.scan_source(source, progress, cancel)
            size = f"{info.total_bytes / 2**30:.2f} GB"
            log(i18n.t("scan.done", files=info.file_count,
                       dirs=info.dir_count, size=size)
                + (f"  [{info.title_id} {info.title or ''}]"
                   if info.title_id else ""))

            image = core.build_exfat(source, output,
                                     progress=progress, cancel=cancel)
            log(i18n.t("write.image", path=str(image)))

            if opts.get("verify", True):
                files, total = core.verify_image(
                    image, source, progress=progress, cancel=cancel)
                log(i18n.t("verify.ok", files=files,
                           size=f"{total / 2**30:.2f} GB"))

            final = image
            if opts.get("mode") == "pfs":
                pfs = core.pack_pfs(
                    image,
                    compress=opts.get("compress", True),
                    compression_level=max(1, min(9, int(opts.get("level", 9)))),
                    progress=progress)
                ratio = 100 * pfs.stat().st_size / max(1, image.stat().st_size)
                log(i18n.t("pfs.result", path=str(pfs),
                           size=f"{pfs.stat().st_size / 2**30:.2f} GB",
                           ratio=ratio))
                if not opts.get("keep_exfat", False):
                    image.unlink()
                    log(i18n.t("pfs.removed", name=image.name))
                final = pfs
            self._js("onDone", f"{final.name} ✓")
        except core.BuildCancelled:
            self._js("onCancelled")
        except Exception as exc:  # noqa: BLE001 — surfaced in the UI
            self._js("onError", str(exc))


def main() -> int:
    api = Api()
    window = webview.create_window(
        "exFAT Forge",
        _webui_index().as_uri(),
        js_api=api,
        width=860, height=640, min_size=(720, 560),
        background_color="#070b14",
    )
    api.window = window
    webview.start()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
