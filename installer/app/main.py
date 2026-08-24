"""PS5 Image Forge setup — entry point.

Runs in three shapes:

  (no flags)   branded pywebview GUI, install or uninstall depending on where
               the outer exe was started from
  --auto       unattended, no window, exit code says what happened (CI)
  --uninstall  force uninstall mode

``--self=<path>`` is prepended by setup.nsi and is the *outer* exe — the one
the user double-clicked. The GUI process itself lives in NSIS's $PLUGINSDIR and
is thrown away on exit, so it can never be the thing we copy in as
uninstall.exe; the path has to be handed over.

Exit codes: 0 success, 1 failure, 2 the app is still running (unattended only,
since the GUI asks the user to close it instead), 3 cancelled.
"""

from __future__ import annotations

import os
import sys
import tempfile
import threading
from pathlib import Path

import engine

WINDOW_W = 640
WINDOW_MIN_H = 240


def _parse(argv: list[str]) -> dict:
    opts = {"self": None, "auto": False, "uninstall": False}
    for arg in argv:
        if arg.startswith("--self="):
            opts["self"] = Path(arg[7:].strip('"'))
        elif arg in ("--auto", "/S", "/s"):
            opts["auto"] = True
        elif arg in ("--uninstall", "/uninstall"):
            opts["uninstall"] = True
    return opts


def _is_uninstall(opts: dict) -> bool:
    """Add/Remove Programs invokes uninstall.exe with no arguments at all, so
    mode has to be inferred from the image we were launched from."""
    if opts["uninstall"]:
        return True
    outer = opts["self"]
    if not outer:
        return False
    return (outer.name.lower() == engine.UNINST_EXE
            or engine._inside(outer, engine.install_dir()))


# --------------------------------------------------------------- unattended

def _run_auto(opts: dict, removing: bool) -> int:
    # This binary is GUI-subsystem (it has to be, or every run would flash a
    # console), so stdout goes nowhere when it is launched from a script. The
    # log file is the only way a failing unattended run can be diagnosed.
    trace = Path(tempfile.gettempdir()) / "ps5if-setup.log"

    def log(message: str, kind: str = "") -> None:
        line = f"[{kind or 'info'}] {message}"
        print(line, flush=True)
        with trace.open("a", encoding="utf-8") as fh:
            fh.write(line + "\n")

    trace.write_text(f"--- {'uninstall' if removing else 'install'} "
                     f"{engine.version()} self={opts['self']}\n", encoding="utf-8")
    try:
        if removing:
            engine.uninstall(self_image=opts["self"], log=log)
        else:
            engine.install(desktop=False, self_image=opts["self"], log=log)
    except engine.AppRunningError:
        log(f"{engine.PRODUCT} is running — close it and retry", "error")
        return 2
    except Exception as exc:                       # noqa: BLE001 — report, don't trace
        log(str(exc), "error")
        return 1
    return 0


# ---------------------------------------------------------------------- GUI

class Api:
    """js_api for webui/app.js. The worker runs off the UI thread and the page
    polls ``poll()``; pushing with evaluate_js from a thread is not safe on the
    WebView2 backend."""

    def __init__(self, opts: dict, removing: bool) -> None:
        self._opts = opts
        self._removing = removing
        self._lines: list[dict] = []
        self._pct = 0
        self._phase = ""
        self._done: bool | None = None
        self._error = ""
        self._lock = threading.Lock()
        # Underscore on purpose. pywebview walks the js_api object to build the
        # JS-side function table, and it recurses: a public ``self.window``
        # drags the entire WinForms control tree into it, which both slows every
        # call to a crawl and shadows the real methods. The main app hit this
        # exact bug (see bridge.py).
        self._window = None
        self._target: Path | None = None

    # -- state ------------------------------------------------------------
    def state(self) -> dict:
        return {
            "mode": "uninstall" if self._removing else "install",
            "product": engine.PRODUCT,
            "version": engine.version(),
            "installed": engine.installed_version(),
            "dir": str(self._dest()),
            "size": sum(p.stat().st_size for p in engine.payload_files()),
            "on_disk": engine.installed_size(),
            "running": engine.app_is_running(),
        }

    def _dest(self) -> Path:
        return self._target or engine.install_dir()

    def browse(self) -> str:
        """Folder picker for the install location; returns the chosen path."""
        import webview
        if self._window is None:
            return str(self._dest())
        picked = self._window.create_file_dialog(
            webview.FOLDER_DIALOG, directory=str(self._dest().parent))
        if picked:
            # The dialog yields the parent the user browsed to; keep the
            # product folder so we never scatter files into e.g. Documents.
            chosen = Path(picked[0] if isinstance(picked, (list, tuple)) else picked)
            self._target = (chosen if chosen.name == engine.PRODUCT
                            else chosen / engine.PRODUCT)
        return str(self._dest())

    def recheck(self) -> bool:
        """'Check again' after the user closes the app."""
        return engine.app_is_running()

    def fit(self, height: float) -> None:
        """Shrink-wrap the window around the current stage.

        The stages differ by more than 250 px — a window tall enough for the
        'app is running' warning is two-thirds empty on the confirmation step.
        The page measures itself and calls this after each change.
        """
        if self._window is None:
            return
        try:
            self._window.resize(WINDOW_W, max(WINDOW_MIN_H, int(height)))
        except Exception:                              # noqa: BLE001
            pass                                       # backend without resize

    def poll(self) -> dict:
        with self._lock:
            lines, self._lines = self._lines, []
            return {"lines": lines, "pct": self._pct, "phase": self._phase,
                    "done": self._done, "error": self._error}

    # -- actions ----------------------------------------------------------
    def start(self, desktop: bool = False) -> bool:
        threading.Thread(target=self._work, args=(bool(desktop),),
                         daemon=True).start()
        return True

    def _log(self, message: str, kind: str = "") -> None:
        with self._lock:
            self._lines.append({"text": message, "kind": kind})

    def _progress(self, pct: int, phase: str) -> None:
        with self._lock:
            self._pct, self._phase = pct, phase

    def _work(self, desktop: bool) -> None:
        try:
            if self._removing:
                engine.uninstall(self_image=self._opts["self"],
                                 log=self._log, progress=self._progress)
            else:
                engine.install(desktop=desktop, self_image=self._opts["self"],
                               dest=self._target,
                               log=self._log, progress=self._progress)
            with self._lock:
                self._done = True
        except engine.AppRunningError:
            self._log(f"{engine.PRODUCT} is running — close it and retry", "err")
            with self._lock:
                self._done, self._error = False, "running"
        except Exception as exc:                   # noqa: BLE001
            self._log(str(exc), "err")
            with self._lock:
                self._done, self._error = False, str(exc)

    def launch(self) -> bool:
        exe = self._dest() / engine.APP_EXE
        if not exe.is_file():
            return False
        os.startfile(exe)  # noqa: S606 — our own freshly written exe
        return True

    # -- frameless window chrome -----------------------------------------
    def minimize(self) -> None:
        if self._window:
            self._window.minimize()

    def close(self) -> None:
        if self._window:
            self._window.destroy()


def _run_gui(opts: dict, removing: bool) -> int:
    import webview

    api = Api(opts, removing)
    here = Path(getattr(sys, "_MEIPASS", Path(__file__).parent))
    api._window = webview.create_window(
        f"{engine.PRODUCT} Setup",
        (here / "webui" / "index.html").as_uri(),
        js_api=api,
        width=WINDOW_W, height=470, resizable=False,
        background_color="#070b14",
        frameless=True, easy_drag=False,
    )
    # Same reason as the main app: letting pywebview auto-pick can land on the
    # ancient MSHTML backend, where none of this CSS renders.
    try:
        webview.start(gui="edgechromium")
    except Exception:                              # noqa: BLE001
        webview.start()
    # webview.start() returns when the window closes; _done is None if the user
    # closed it without ever pressing the button.
    return {True: 0, False: 1, None: 3}[api._done]


def main(argv: list[str] | None = None) -> int:
    opts = _parse(list(argv if argv is not None else sys.argv[1:]))
    removing = _is_uninstall(opts)
    if opts["auto"]:
        return _run_auto(opts, removing)
    return _run_gui(opts, removing)


if __name__ == "__main__":
    raise SystemExit(main())
