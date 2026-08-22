"""Single-exe entrypoint (PyInstaller onefile).

Dispatch:
* no arguments        -> GUI
* ``--mkpfs ...``     -> forward to mkpfs's own CLI (used by core.pack_pfs,
                         which re-invokes this exe as its subprocess)
* ``--selftest``      -> build+verify+pack a tiny synthetic dump in a temp
                         dir; exit 0 on success (used to validate the exe)
* anything else       -> the exfat-forge CLI

``multiprocessing.freeze_support()`` must run first: mkpfs's compressor
spawns worker processes, and under PyInstaller those re-enter this exe.
"""

from __future__ import annotations

import multiprocessing
import os
import sys


def _selftest() -> int:
    import json
    import random
    import tempfile
    from pathlib import Path

    from . import catalog, core

    with tempfile.TemporaryDirectory(prefix="forge_selftest_") as td:
        tmp = Path(td)
        src = tmp / "PPSA00000-app0"
        (src / "sce_sys").mkdir(parents=True)
        (src / "sce_sys" / "param.json").write_text(
            json.dumps({"titleId": "PPSA00000"}), encoding="utf-8")
        rng = random.Random(7)
        (src / "eboot.bin").write_bytes(rng.randbytes(200_000))
        (src / "data.bin").write_bytes(b"\0" * 1_000_000)  # compressible
        image = core.build_exfat(src, tmp)
        core.verify_image(image, src)
        pfs = core.pack_pfs(image, compress=True, compression_level=6)
        assert pfs.stat().st_size > 0
    # data files must survive freezing, not just import
    assert catalog.load(), "payload catalog missing from the bundle"
    return 0


def main() -> int:
    multiprocessing.freeze_support()
    # In a windowed (no-console) build stdout/stderr are None and any
    # print() would raise; give the CLI paths a sink to write into.
    if sys.stdout is None:
        sys.stdout = open(os.devnull, "w", encoding="utf-8")
    if sys.stderr is None:
        sys.stderr = open(os.devnull, "w", encoding="utf-8")
    # On zh-CN Windows the streams default to the GBK code page and
    # mkpfs's celebratory emoji (🎉) kills the subprocess with a
    # UnicodeEncodeError; force UTF-8 with replacement instead.
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, OSError):
            pass
    argv = sys.argv[1:]
    if argv and argv[0] == "--mkpfs":
        from mkpfs.cli import cli_mkpfs_main
        return cli_mkpfs_main(argv[1:])
    if argv and argv[0] == "--selftest":
        return _selftest()
    if argv:
        from .cli import main as cli_main
        return cli_main(argv)
    from .gui import main as gui_main
    return gui_main()


if __name__ == "__main__":
    raise SystemExit(main())
