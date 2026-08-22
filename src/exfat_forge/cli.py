"""Command-line interface: build / verify / extract / list."""

from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

from mkpfs.exfat import open_exfat, render_exfat_tree

from . import core, pipeline, ufs
from .i18n import t as _
from .settings import History


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
    spec = pipeline.JobSpec(
        source=source,
        output_dir=Path(args.output) if args.output else source.parent,
        fmt=args.format, intermediate=args.via,
        verify=not args.no_verify, cluster_size=args.cluster,
        compress=args.compress, level=args.level, threads=args.threads,
        keep_intermediate=args.keep_intermediate,
        ffpkg_block=args.ffpkg_block, ffpkg_frag=args.ffpkg_frag)
    result = pipeline.run_job(spec, progress=_console_progress())
    print(_("write.image", path=result.output))
    mins, secs = divmod(int(result.duration_s), 60)
    print(f"  {_fmt_gb(result.size_bytes)}"
          + (f" · {result.file_count:,} files" if result.file_count else ""))
    print(_("done", mins=mins, secs=secs))
    return 0


def cmd_history(args: argparse.Namespace) -> int:
    rows = History().load()
    if args.clear:
        History().clear()
        print("history cleared")
        return 0
    if not rows:
        print("no builds recorded")
        return 0
    for h in rows[:args.limit]:
        mins, secs = divmod(int(h.duration_s), 60)
        print(f"{h.timestamp}  {h.status:<9} {h.fmt:<6} "
              f"{h.size_bytes / 2**30:8.2f} GB  {mins:>3}m{secs:02d}s  "
              f"{h.title or h.title_id or Path(h.source).name}")
    return 0


def cmd_catalog(args: argparse.Namespace) -> int:
    """List the payload catalogue. Metadata only — nothing is downloaded."""
    from . import catalog
    entries = catalog.load()
    print(f"{len(entries)} entries · metadata from {catalog.metadata_source()}")
    for e in entries:
        fw = " ".join(e.firmwares) if e.firmwares else "all"
        how = e.binary_url or f"(page) {e.page_url}"
        print(f"  {e.id:30} v{e.version:12} fw={fw:22} {how}")
    return 0


def cmd_env(args: argparse.Namespace) -> int:
    dn = ufs.dotnet_status()
    print(f"exfat/pfs : available (built in)")
    print(f"ffpkg     : "
          + ("available" if (ufs.tool_available() and dn.available)
             else "unavailable")
          + f"  ({dn.detail}"
          + ("" if ufs.tool_available() else ", UFS2Tool missing") + ")")
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
    count = pipeline.extract_any(Path(args.image), Path(args.dest),
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
                   help="cluster size in bytes (default: 65536; 0 = auto)")
    b.add_argument("-f", "--format", default="exfat",
                   choices=["exfat", "ffpkg", "pfs"],
                   help="output format (default: exfat)")
    b.add_argument("--via", default="exfat", choices=["exfat", "ffpkg"],
                   help="intermediate format when building PFS")
    b.add_argument("--no-compress", dest="compress", action="store_false",
                   help="for PFS output, write uncompressed blocks "
                        "(compression is on by default)")
    b.add_argument("--level", type=int, default=9, choices=range(1, 10),
                   metavar="1-9", help="compression level (default 9)")
    b.add_argument("--threads", type=int,
                   help="compression worker processes (default: all cores)")
    b.add_argument("--keep-intermediate", action="store_true",
                   help="for PFS output, keep the intermediate image")
    b.add_argument("--ffpkg-block", type=int, default=65536,
                   help="ffpkg block size (default 65536)")
    b.add_argument("--ffpkg-frag", type=int, default=65536,
                   help="ffpkg fragment size (default 65536)")
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

    h = sub.add_parser("history", help="show recent builds")
    h.add_argument("--limit", type=int, default=20)
    h.add_argument("--clear", action="store_true")
    h.set_defaults(fn=cmd_history)

    cat = sub.add_parser("catalog", help="list the payload catalogue")
    cat.set_defaults(fn=cmd_catalog)

    en = sub.add_parser("env", help="report available image formats")
    en.set_defaults(fn=cmd_env)

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
