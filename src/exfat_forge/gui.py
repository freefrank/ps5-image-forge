"""Web GUI shell: a frameless pywebview (WebView2) window over the bridge.

The interface itself lives in ``webui/`` (HTML + CSS + JS, with a demo mode
so it can be previewed in a browser). This module only creates the window
and wires it to :class:`~.bridge.Bridge`.
"""

from __future__ import annotations

import sys
import ctypes
from ctypes import wintypes
from pathlib import Path

import webview

from .bridge import Bridge


# Keep Win32 callback delegates alive for the lifetime of their windows.
_NATIVE_RESIZE_HOOKS: dict[int, tuple[object, int]] = {}


def _install_native_resize(window) -> None:
    """Give a frameless WinForms window standard Windows resize hit tests."""
    if sys.platform != "win32" or window.native is None:
        return

    def install() -> None:
        native = window.native
        hwnd = int(native.Handle.ToInt64())
        if hwnd in _NATIVE_RESIZE_HOOKS:
            return

        user32 = ctypes.windll.user32
        lresult = ctypes.c_ssize_t
        wndproc_type = ctypes.WINFUNCTYPE(
            lresult, wintypes.HWND, wintypes.UINT,
            wintypes.WPARAM, wintypes.LPARAM)
        set_wndproc = user32.SetWindowLongPtrW
        set_wndproc.argtypes = (wintypes.HWND, ctypes.c_int, ctypes.c_void_p)
        set_wndproc.restype = ctypes.c_void_p
        call_wndproc = user32.CallWindowProcW
        call_wndproc.argtypes = (
            ctypes.c_void_p, wintypes.HWND, wintypes.UINT,
            wintypes.WPARAM, wintypes.LPARAM)
        call_wndproc.restype = lresult
        user32.GetWindowRect.argtypes = (wintypes.HWND,
                                         ctypes.POINTER(wintypes.RECT))
        user32.GetWindowRect.restype = wintypes.BOOL
        user32.GetCursorPos.argtypes = (ctypes.POINTER(wintypes.POINT),)
        user32.GetCursorPos.restype = wintypes.BOOL
        user32.IsZoomed.argtypes = (wintypes.HWND,)
        user32.IsZoomed.restype = wintypes.BOOL

        old_proc = 0

        @wndproc_type
        def wndproc(handle, message, wparam, lparam):
            if message == 0x0084 and not user32.IsZoomed(handle):  # WM_NCHITTEST
                rect = wintypes.RECT()
                point = wintypes.POINT()
                if (user32.GetWindowRect(handle, ctypes.byref(rect)) and
                        user32.GetCursorPos(ctypes.byref(point))):
                    border = 8
                    left = point.x < rect.left + border
                    right = point.x >= rect.right - border
                    top = point.y < rect.top + border
                    bottom = point.y >= rect.bottom - border
                    if top and left:
                        return 13       # HTTOPLEFT
                    if top and right:
                        return 14       # HTTOPRIGHT
                    if bottom and left:
                        return 16       # HTBOTTOMLEFT
                    if bottom and right:
                        return 17       # HTBOTTOMRIGHT
                    if left:
                        return 10       # HTLEFT
                    if right:
                        return 11       # HTRIGHT
                    if top:
                        return 12       # HTTOP
                    if bottom:
                        return 15       # HTBOTTOM
            return call_wndproc(old_proc, handle, message, wparam, lparam)

        old_proc = int(set_wndproc(hwnd, -4, wndproc) or 0)  # GWLP_WNDPROC
        if old_proc:
            _NATIVE_RESIZE_HOOKS[hwnd] = (wndproc, old_proc)

    try:
        native = window.native
        if native.InvokeRequired:
            import clr  # noqa: F401
            from System import Action
            native.BeginInvoke(Action(install))
        else:
            install()
    except (AttributeError, OSError, TypeError, ValueError):
        pass


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
        width=1120, height=780, min_size=(720, 520), resizable=True,
        background_color="#070b14",
        frameless=True, easy_drag=False,   # the header carries the drag region
    )
    api._window = window
    window.events.shown += lambda: _install_native_resize(window)
    webview.start()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
