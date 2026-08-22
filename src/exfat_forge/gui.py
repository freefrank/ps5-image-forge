"""Web GUI shell: a frameless pywebview (WebView2) window over the bridge.

The interface itself lives in ``webui/`` (HTML + CSS + JS, with a demo mode
so it can be previewed in a browser). This module only creates the window
and wires it to :class:`~.bridge.Bridge`.
"""

from __future__ import annotations

import sys
from pathlib import Path

import webview

from .bridge import Bridge


def webui_dir() -> Path:
    if getattr(sys, "frozen", False):
        return Path(sys._MEIPASS) / "exfat_forge" / "webui"  # type: ignore[attr-defined]
    return Path(__file__).parent / "webui"


def main() -> int:
    api = Bridge()
    window = webview.create_window(
        "exFAT Forge",
        (webui_dir() / "index.html").as_uri(),
        js_api=api,
        width=1000, height=720, min_size=(880, 620),
        background_color="#070b14",
        frameless=True, easy_drag=False,   # the header carries the drag region
    )
    api._window = window
    webview.start()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
