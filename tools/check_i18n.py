"""Verify the UI translation tables against the keys the UI actually uses.

Three ways this drifts, all of which reach the user as a raw key on screen or
as a string stuck in the wrong language:

  * a key used by index.html / app.js that no table defines
  * a key defined in one language but not the other
  * a key nothing uses any more

DEVELOPMENT.md described this as a manual check; it is a CI step now.

    python tools/check_i18n.py
    python tools/check_i18n.py --quiet   # only complain

Unused keys are reported but do not fail the run — pruning them is a judgement
call, and a key can be reached from code this script cannot see.
"""
from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
WEBUI = ROOT / "src/ps5_image_forge/webui"

# Keys referenced from markup and from code.
HTML_KEY = re.compile(r'data-i18n(?:-ph)?="([^"]+)"')
# A literal t("key"). The lookahead skips t("phase." + kind) style
# concatenation, where the string is a prefix and not a key in its own right.
JS_KEY = re.compile(r'\bt\(\s*"([^"]+)"(?!\s*\+)')
# A table entry's key: `"some.key":` at the start of a line or after a comma.
# Only the key is captured; values may contain anything, including quotes.
TABLE_KEY = re.compile(r'(?:^|,)\s*"([\w.\-]+)"\s*:', re.M)
# Language table headers: two-space-indented `en: {` inside window.I18N.
LANG_HEAD = re.compile(r'(?m)^\s{2}(\w+)\s*:\s*\{')


def tables(js: str) -> dict[str, set[str]]:
    """Split i18n.js into per-language key sets.

    Slicing on the language headers rather than parsing JS: the file is a plain
    `window.I18N = { en: {...}, zh: {...} }` and a real parser would be a
    dependency for no gain.
    """
    heads = [(m.group(1), m.start()) for m in LANG_HEAD.finditer(js)]
    if not heads:
        sys.exit("error: no language tables found in i18n.js")
    out: dict[str, set[str]] = {}
    for i, (lang, start) in enumerate(heads):
        end = heads[i + 1][1] if i + 1 < len(heads) else len(js)
        out[lang] = {m.group(1) for m in TABLE_KEY.finditer(js[start:end])}
    return out


def check(webui: Path, quiet: bool) -> int:
    i18n = webui / "i18n.js"
    if not i18n.is_file():
        return 0
    langs = tables(i18n.read_text(encoding="utf-8"))

    used: set[str] = set()
    for name, pattern in (("index.html", HTML_KEY), ("app.js", JS_KEY)):
        f = webui / name
        if f.is_file():
            used |= set(pattern.findall(f.read_text(encoding="utf-8")))

    defined_anywhere: set[str] = set().union(*langs.values())
    in_every_table: set[str] = set.intersection(*langs.values())

    problems = 0
    rel = webui.relative_to(ROOT)

    missing = sorted(used - defined_anywhere)
    if missing:
        problems += len(missing)
        print(f"{rel}: {len(missing)} key(s) used but never defined")
        for key in missing:
            print(f"    {key}")

    partial = sorted(defined_anywhere - in_every_table)
    if partial:
        problems += len(partial)
        print(f"{rel}: {len(partial)} key(s) missing from some language")
        for key in partial:
            have = sorted(lang for lang, keys in langs.items() if key in keys)
            print(f"    {key}  (only in: {', '.join(have)})")

    unused = sorted(defined_anywhere - used)
    if unused and not quiet:
        print(f"{rel}: {len(unused)} defined but unused (informational)")

    if not problems and not quiet:
        sizes = ", ".join(f"{lang}={len(keys)}" for lang, keys in sorted(langs.items()))
        print(f"{rel}: OK - {len(used)} keys used; {sizes}")
    return problems


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--quiet", action="store_true")
    args = ap.parse_args()
    if check(WEBUI, args.quiet):
        print("\ni18n check failed")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
