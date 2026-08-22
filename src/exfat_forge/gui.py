"""Minimal tkinter front-end over the core pipeline.

One window: pick source and output, choose exFAT or exFAT+PFS, watch a
progress bar and log. The build runs in a worker thread; the UI thread only
ever touches tkinter (events cross over via a queue), and cancellation is
cooperative through :class:`core.CancelToken`.
"""

from __future__ import annotations

import queue
import threading
import time
import tkinter as tk
from pathlib import Path
from tkinter import filedialog, messagebox, ttk

from . import core


class App:
    def __init__(self, root: tk.Tk) -> None:
        self.root = root
        root.title("exFAT Forge")
        root.minsize(560, 420)

        self.source_var = tk.StringVar()
        self.output_var = tk.StringVar()
        self.mode_var = tk.StringVar(value="exfat")
        self.verify_var = tk.BooleanVar(value=True)

        self._queue: queue.Queue = queue.Queue()
        self._worker: threading.Thread | None = None
        self._cancel: core.CancelToken | None = None
        self._t0 = 0.0

        pad = {"padx": 8, "pady": 4}
        frm = ttk.Frame(root)
        frm.pack(fill="x", **pad)

        ttk.Label(frm, text="源目录").grid(row=0, column=0, sticky="w")
        ttk.Entry(frm, textvariable=self.source_var).grid(
            row=0, column=1, sticky="ew", padx=6)
        ttk.Button(frm, text="浏览…", command=self._pick_source).grid(
            row=0, column=2)

        ttk.Label(frm, text="输出目录").grid(row=1, column=0, sticky="w")
        ttk.Entry(frm, textvariable=self.output_var).grid(
            row=1, column=1, sticky="ew", padx=6)
        ttk.Button(frm, text="浏览…", command=self._pick_output).grid(
            row=1, column=2)
        frm.columnconfigure(1, weight=1)

        opts = ttk.Frame(root)
        opts.pack(fill="x", **pad)
        ttk.Radiobutton(opts, text="exFAT", value="exfat",
                        variable=self.mode_var).pack(side="left")
        ttk.Radiobutton(opts, text="exFAT + PFS (.ffpfsc)", value="pfs",
                        variable=self.mode_var).pack(side="left", padx=12)
        ttk.Checkbutton(opts, text="构建后校验",
                        variable=self.verify_var).pack(side="left", padx=12)

        bar = ttk.Frame(root)
        bar.pack(fill="x", **pad)
        self.progress = ttk.Progressbar(bar, maximum=1000)
        self.progress.pack(fill="x", side="left", expand=True)
        self.pct_label = ttk.Label(bar, text="  0.0%", width=8)
        self.pct_label.pack(side="left")

        self.status = ttk.Label(root, text="就绪", anchor="w")
        self.status.pack(fill="x", padx=8)

        self.log = tk.Text(root, height=12, state="disabled", wrap="none")
        self.log.pack(fill="both", expand=True, **pad)

        btns = ttk.Frame(root)
        btns.pack(fill="x", **pad)
        self.start_btn = ttk.Button(btns, text="开始构建", command=self._start)
        self.start_btn.pack(side="right")
        self.cancel_btn = ttk.Button(btns, text="取消", command=self._do_cancel,
                                     state="disabled")
        self.cancel_btn.pack(side="right", padx=8)

        root.after(100, self._drain_queue)

    # ── UI helpers ────────────────────────────────────────────────

    def _pick_source(self) -> None:
        path = filedialog.askdirectory(title="选择游戏 dump 目录")
        if path:
            self.source_var.set(path)
            if not self.output_var.get():
                self.output_var.set(str(Path(path).parent))

    def _pick_output(self) -> None:
        path = filedialog.askdirectory(title="选择输出目录")
        if path:
            self.output_var.set(path)

    def _append_log(self, text: str) -> None:
        self.log.configure(state="normal")
        self.log.insert("end", text + "\n")
        self.log.see("end")
        self.log.configure(state="disabled")

    # ── worker plumbing ───────────────────────────────────────────

    def _start(self) -> None:
        source = Path(self.source_var.get().strip('" '))
        output = Path(self.output_var.get().strip('" ') or source.parent)
        if not source.is_dir():
            messagebox.showerror("exFAT Forge", f"源目录不存在:\n{source}")
            return
        if not (source / "eboot.bin").is_file():
            if not messagebox.askyesno(
                    "exFAT Forge",
                    "源目录里没有 eboot.bin，看起来不是游戏 dump。\n仍然继续？"):
                return
        self.start_btn.configure(state="disabled")
        self.cancel_btn.configure(state="normal")
        self._cancel = core.CancelToken()
        self._t0 = time.monotonic()
        self._worker = threading.Thread(
            target=self._run, args=(source, output, self.mode_var.get(),
                                    self.verify_var.get(), self._cancel),
            daemon=True)
        self._worker.start()

    def _do_cancel(self) -> None:
        if self._cancel:
            self._cancel.cancel()
            self._post("log", "正在取消…")

    def _post(self, kind: str, payload: object) -> None:
        self._queue.put((kind, payload))

    def _run(self, source: Path, output: Path, mode: str,
             verify: bool, cancel: core.CancelToken) -> None:
        progress: core.ProgressFn = lambda ev: self._post("progress", ev)
        try:
            info = core.scan_source(source, progress, cancel)
            self._post("log",
                       f"扫描完成: {info.file_count:,} 文件, "
                       f"{info.total_bytes / 2**30:.2f} GB"
                       + (f"  [{info.title_id} {info.title or ''}]"
                          if info.title_id else ""))
            image = core.build_exfat(source, output,
                                     progress=progress, cancel=cancel)
            self._post("log", f"镜像已写出: {image}")
            if verify:
                files, total = core.verify_image(image, source,
                                                 progress=progress,
                                                 cancel=cancel)
                self._post("log", f"校验通过: {files:,} 文件, "
                                  f"{total / 2**30:.2f} GB")
            if mode == "pfs":
                pfs = core.pack_pfs(image, progress=progress)
                self._post("log", f"PFS 已生成: {pfs}")
            mins, secs = divmod(int(time.monotonic() - self._t0), 60)
            self._post("done", f"完成 ({mins}m {secs:02d}s)")
        except core.BuildCancelled:
            self._post("done", "已取消")
        except Exception as exc:  # noqa: BLE001 — surfaced to the user
            self._post("error", str(exc))

    def _drain_queue(self) -> None:
        try:
            while True:
                kind, payload = self._queue.get_nowait()
                if kind == "progress":
                    self._on_progress(payload)
                elif kind == "log":
                    self._append_log(str(payload))
                elif kind == "done":
                    self._append_log(str(payload))
                    self.status.configure(text=str(payload))
                    self._reset_buttons()
                elif kind == "error":
                    self._append_log(f"错误: {payload}")
                    self.status.configure(text="失败")
                    messagebox.showerror("exFAT Forge", str(payload))
                    self._reset_buttons()
        except queue.Empty:
            pass
        self.root.after(100, self._drain_queue)

    def _on_progress(self, ev: core.ProgressEvent) -> None:
        phase_names = {"scan": "扫描", "write": "写入镜像",
                       "verify": "校验", "pfs": "PFS 打包", "extract": "解包"}
        name = phase_names.get(ev.phase, ev.phase)
        if ev.total:
            frac = ev.done / ev.total
            self.progress.configure(value=int(frac * 1000))
            self.pct_label.configure(text=f"{frac * 100:5.1f}%")
            elapsed = time.monotonic() - self._t0
            speed = ev.done / elapsed if elapsed > 0 else 0
            if ev.phase in ("write", "verify", "extract") and speed > 0:
                eta = (ev.total - ev.done) / speed
                self.status.configure(
                    text=f"{name}: {ev.done / 2**30:.2f} / "
                         f"{ev.total / 2**30:.2f} GB   "
                         f"{speed / 2**20:.0f} MB/s   剩余 {eta:.0f}s")
            else:
                self.status.configure(text=f"{name}: {ev.detail}")
        else:
            self.status.configure(text=f"{name}: {ev.done:,} {ev.detail}")

    def _reset_buttons(self) -> None:
        self.start_btn.configure(state="normal")
        self.cancel_btn.configure(state="disabled")


def main() -> int:
    root = tk.Tk()
    try:
        ttk.Style().theme_use("vista")
    except tk.TclError:
        pass
    App(root)
    root.mainloop()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
