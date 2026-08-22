"""Tiny i18n layer shared by the CLI and the GUI bridge.

Locale resolution order: explicit ``set_locale`` call (GUI language toggle,
``--lang`` flag) > ``EXFAT_FORGE_LANG`` env var > Windows UI language.
Only the string tables below ship; unknown locales fall back to English.
"""

from __future__ import annotations

import ctypes
import locale
import os

_STRINGS: dict[str, dict[str, str]] = {
    "en": {
        "scan.done": "[scan] {files:,} files, {dirs:,} dirs, {size}",
        "write.image": "[write] image: {path}",
        "verify.ok": "[verify] OK — {files:,} files, {size}",
        "verify.structure_only": "  (structure only; pass --source to compare)",
        "pfs.result": "[pfs] {path}  ({size}, {ratio:.0f}% of exFAT)",
        "pfs.removed": "[pfs] removed intermediate {name}",
        "extract.done": "[extract] {count:,} files -> {dest}",
        "done": "[done] {mins}m {secs:02d}s",
        "cancelled": "[cancelled]",
        "error": "[error] {msg}",
        "log.no_eboot": "warning: no eboot.bin in source — not a game dump?",
    },
    "zh": {
        "scan.done": "[扫描] {files:,} 个文件, {dirs:,} 个目录, {size}",
        "write.image": "[写入] 镜像: {path}",
        "verify.ok": "[校验] 通过 — {files:,} 个文件, {size}",
        "verify.structure_only": "  (仅结构校验; 加 --source 可与源目录比对)",
        "pfs.result": "[pfs] {path}  ({size}, 为 exFAT 的 {ratio:.0f}%)",
        "pfs.removed": "[pfs] 已删除中间镜像 {name}",
        "extract.done": "[解包] {count:,} 个文件 -> {dest}",
        "done": "[完成] {mins}m {secs:02d}s",
        "cancelled": "[已取消]",
        "error": "[错误] {msg}",
        "log.no_eboot": "警告: 源目录中没有 eboot.bin — 可能不是游戏 dump?",
    },
}

_current: str | None = None


def detect_locale() -> str:
    """Best-effort system language: 'zh' or 'en'."""
    env = os.environ.get("EXFAT_FORGE_LANG", "")
    if env[:2].lower() in _STRINGS:
        return env[:2].lower()
    try:
        lang_id = ctypes.windll.kernel32.GetUserDefaultUILanguage()
        if lang_id & 0xFF == 0x04:  # LANG_CHINESE
            return "zh"
    except (AttributeError, OSError):
        loc = locale.getdefaultlocale()[0] or ""
        if loc.lower().startswith("zh"):
            return "zh"
    return "en"


def get_locale() -> str:
    global _current
    if _current is None:
        _current = detect_locale()
    return _current


def set_locale(lang: str) -> None:
    global _current
    _current = lang if lang in _STRINGS else "en"


def t(key: str, **kwargs) -> str:
    """Translate ``key`` for the active locale, formatting with kwargs."""
    table = _STRINGS.get(get_locale(), _STRINGS["en"])
    template = table.get(key) or _STRINGS["en"].get(key) or key
    return template.format(**kwargs) if kwargs else template
