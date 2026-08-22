"""Command-line interface: build / verify / extract / list."""

from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

from mkpfs.exfat import open_exfat, render_exfat_tree

from . import core
from .i18n import t as _


def _fmt_gb(n: int) -> str:
    return f"{n / 2**30:.2f} GB"


def _console_progress() -> core.ProgressFn:
    state = {"last_len": 0, "t0": time.monotonic()}

    def cb(ev: core.ProgressEvent) -> None:
        if ev.phase == "scan":
            line = f"[scan] {ev.done:,} files  {ev.detail}"
        elif ev.total:
            pct = 100 * ev.done / ev.total
            elapsed = time.monotonic() - state["t0"]
            speed = ev.done / elapsed if elapsed > 0 else 0
            eta = (ev.total - ev.done) / speed if speed > 0 else 0
            line = (f"[{ev.phase}] {pct:5.1f}%  "
                    f"{_fmt_gb(ev.done)} / {_fmt_gb(ev.total)}  "
                    f"{speed / 2**20:.0f} MB/s  ETA {eta:4.0f}s  {ev.detail}")
        else:
            line = f"[{ev.phase}] {ev.detail}"
        pad = " " * max(0, state["last_len"] - len(line))
        state["last_len"] = len(line)
        print("\r" + line + pad, end="", flush=True)
        if ev.total and ev.done >= ev.total:
            print()
            state["t0"] = time.monotonic()

    return cb


def cmd_build(args: argparse.Namespace) -> int:
    source = Path(args.source)
    output = Path(args.output) if args.output else source.parent
    progress = _console_progress()
    t0 = time.monotonic()

    info = core.scan_source(source, progress)
    print(_("scan.done", files=info.file_count, dirs=info.dir_count,
            size=_fmt_gb(info.total_bytes))
          + (f"  [{info.title_id} {info.title or ''} {info.version or ''}]"
             if info.title_id else ""))

    image = core.build_exfat(source, output,
                             cluster_size=args.cluster, progress=progress)
    print(_("write.image", path=image))

    if not args.no_verify:
        files, total = core.verify_image(image, source, progress=progress)
        print(_("verify.ok", files=files, size=_fmt_gb(total)))

    if args.pfs:
        pfs = core.pack_pfs(image, compress=args.compress,
                            compression_level=args.level,
                            threads=args.threads, progress=progress)
        ratio = 100 * pfs.stat().st_size / max(1, image.stat().st_size)
        print(_("pfs.result", path=pfs, size=_fmt_gb(pfs.stat().st_size),
                ratio=ratio))
        if not args.keep_exfat:
            image.unlink()
            print(_("pfs.removed", name=image.name))

    mins, secs = divmod(int(time.monotonic() - t0), 60)
    print(_("done", mins=mins, secs=secs))
    return 0


def cmd_verify(args: argparse.Namespace) -> int:
    image = Path(args.image)
    source = Path(args.source) if args.source else None
    files, total = core.verify_image(image, source,
                                     progress=_console_progress())
    print(_("verify.ok", files=files, size=_fmt_gb(total))
          + ("" if source else _("verify.structure_only")))
    return 0


def cmd_extract(args: argparse.Namespace) -> int:
    count = core.extract_image(Path(args.image), Path(args.dest),
                               progress=_console_progress(),
                               overwrite=args.overwrite)
    print(_("extract.done", count=count, dest=args.dest))
    return 0


def cmd_list(args: argparse.Namespace) -> int:
    reader = open_exfat(args.image)
    for line in render_exfat_tree(reader.root_entries()):
        print(line)
    return 0


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(
        prog="exfat-forge",
        description="Mount-free exFAT / PFS image builder for PS5 game dumps. "
                    "No admin rights, no drive letters, no locale bugs.")
    sub = p.add_subparsers(dest="cmd", required=True)

    b = sub.add_parser("build", help="build an image from a dump directory")
    b.add_argument("source", help="game dump directory (contains eboot.bin)")
    b.add_argument("-o", "--output",
                   help="output file or directory (default: next to source)")
    b.add_argument("--cluster", type=int,
                   help="cluster size in bytes (default: auto)")
    b.add_argument("--pfs", action="store_true",
                   help="also convert the image to .ffpfsc")
    b.add_argument("--no-compress", dest="compress", action="store_false",
                   help="with --pfs, write uncompressed blocks "
                        "(compression is on by default)")
    b.add_argument("--level", type=int, default=9, choices=range(1, 10),
                   metavar="1-9", help="compression level (default 9)")
    b.add_argument("--threads", type=int,
                   help="compression worker processes (default: all cores)")
    b.add_argument("--keep-exfat", action="store_true",
                   help="with --pfs, keep the intermediate .exfat")
    b.add_argument("--no-verify", action="store_true",
                   help="skip the read-back verification pass")
    b.set_defaults(fn=cmd_build)

    v = sub.add_parser("verify", help="verify an existing image")
    v.add_argument("image")
    v.add_argument("--source", help="compare against this source directory")
    v.set_defaults(fn=cmd_verify)

    e = sub.add_parser("extract", help="unpack an image to a directory")
    e.add_argument("image")
    e.add_argument("dest")
    e.add_argument("--overwrite", action="store_true")
    e.set_defaults(fn=cmd_extract)

    ls = sub.add_parser("list", help="print the image's file tree")
    ls.add_argument("image")
    ls.set_defaults(fn=cmd_list)

    args = p.parse_args(argv)
    try:
        return args.fn(args)
    except core.BuildCancelled:
        print("\n" + _("cancelled"), file=sys.stderr)
        return 130
    except (core.VerifyError, FileNotFoundError, FileExistsError,
            OSError, RuntimeError) as exc:
        print("\n" + _("error", msg=exc), file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
