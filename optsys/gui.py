"""Real-time Tkinter dashboard - pure visualization layer.

Every number displayed comes from the same OS-collecting engine the CLI uses:
Collector -> analyzer -> optimizer -> measurement. The GUI adds no data
source of its own; it renders live samples and drives safe optimization runs
(manual or auto) through the existing pipeline, then shows the measured
before/after result.
"""

import collections
import os
import queue
import threading
import time
import traceback

import tkinter as tk
from tkinter import ttk

from . import config as cfg
from . import history as hist_mod
from .analyzer import detect_bottlenecks, rank_processes, select_candidates
from .collector import Collector, current_username, static_info
from .measurement import calibrate, evaluate, take_measurement, verdict
from .optimizer import run_actions

C_BG = "#101418"
C_PANEL = "#171c22"
C_FG = "#e8eaed"
C_DIM = "#9aa0a6"
C_GREEN = "#34a853"
C_ORANGE = "#f29900"
C_RED = "#ea4335"
C_BLUE = "#4cc9f0"
C_PURPLE = "#b388ff"


class LineGraph(tk.Canvas):
    """Minimal real-time line graph fed exclusively by measured values."""

    def __init__(self, master, title, series, colors, ymax=100.0, **kw):
        super().__init__(master, bg=C_PANEL, highlightthickness=1,
                         highlightbackground="#2a2f36", **kw)
        self.title = title
        self.series = series          # list of deques
        self.colors = colors
        self.names = [s for s in title]
        self.ymax = ymax
        self.bind("<Configure>", lambda e: self.redraw())

    def redraw(self):
        self.delete("all")
        w, h = self.winfo_width(), self.winfo_height()
        if w < 60 or h < 60:
            return
        pad_l, pad_r, pad_t, pad_b = 34, 12, 20, 16
        pw, ph = w - pad_l - pad_r, h - pad_t - pad_b

        # grid + y labels
        for v in range(0, 101, 25):
            y = pad_t + ph * (1 - v / 100.0)
            self.create_line(pad_l, y, w - pad_r, y, fill="#242a31")
            self.create_text(pad_l - 4, y, text=str(v), anchor="e",
                             fill=C_DIM, font=("Segoe UI", 7))
        self.create_text(w // 2, h - 5, text="time  ->", fill=C_DIM,
                         font=("Segoe UI", 7))
        self.create_text(6, 6, text=self.title[0], anchor="nw",
                         fill=C_FG, font=("Segoe UI", 9, "bold"))

        n = cfg.GUI_GRAPH_POINTS
        for dq, color in zip(self.series, self.colors):
            if not dq:
                continue
            denom = max(len(dq) - 1, 1)
            pts = []
            for i, val in enumerate(dq):
                frac = i / denom if len(dq) < n else i / (n - 1)
                x = pad_l + pw * frac
                y = pad_t + ph * (1 - max(0.0, min(self.ymax, val)) / self.ymax)
                pts += [x, y]
            if len(pts) >= 4:
                self.create_line(*pts, fill=color, width=2)
        # current values legend
        x = w - pad_r - 8
        for dq, name, color in zip(self.series, self.title[1], self.colors):
            if dq:
                txt = f"{name}: {dq[-1]:.1f}%"
            else:
                txt = f"{name}: --"
            self.create_text(x, pad_t - 8, text=txt, anchor="e",
                             fill=color, font=("Segoe UI", 8, "bold"))
            x -= 110


class GuiApp:
    def __init__(self, root: tk.Tk, interval: float):
        self.root = root
        self.interval = max(0.5, float(interval))
        self.q: queue.Queue = queue.Queue()
        self.stop_evt = threading.Event()
        self.collect_pause = threading.Event()   # paused during optimization
        self.opt_busy = False
        self.last_auto_finish = 0.0
        self.bench_iters = None
        self.user = current_username()
        self.static = static_info()

        self.cpu = collections.deque(maxlen=cfg.GUI_GRAPH_POINTS)
        self.mem_used = collections.deque(maxlen=cfg.GUI_GRAPH_POINTS)
        self.mem_free = collections.deque(maxlen=cfg.GUI_GRAPH_POINTS)
        self.sort_mode = tk.StringVar(value="cpu")
        auto_default = cfg.AUTO_OPTIMIZE_DEFAULT or \
            os.environ.get("OSOPT_GUI_AUTO") == "1"
        self.auto_var = tk.BooleanVar(value=auto_default)
        self.err_shown = False

        self._build()
        threading.Thread(target=self._collector_worker, daemon=True).start()
        self.root.protocol("WM_DELETE_WINDOW", self._on_close)
        self.root.after(int(self.interval * 1000), self._tick)

        if os.environ.get("OSOPT_GUI_TEST_OPT"):
            self.root.after(2500, self._start_optimisation, "manual")
        autotest = os.environ.get("OSOPT_GUI_AUTOTEST")
        if autotest:
            self.root.after(int(float(autotest) * 1000), self._autotest_close)

    # ------------------------------------------------------------------ UI
    def _build(self):
        self.root.title(f"Intelligent OS Resource Optimizer - {self.static.hostname}")
        self.root.configure(bg=C_BG)
        self.root.geometry("1180x720")
        self.root.minsize(980, 600)

        outer = ttk.Frame(self.root, padding=6)
        outer.pack(fill="both", expand=True)

        top = ttk.Frame(outer)
        top.pack(fill="x")
        top.columnconfigure(0, weight=1)
        top.columnconfigure(1, weight=1)
        self.g_cpu = LineGraph(top, ("CPU UTILIZATION (%)", ["CPU"]),
                               [self.cpu], [C_BLUE], height=180)
        self.g_cpu.grid(row=0, column=0, sticky="nsew", padx=(0, 3))
        self.g_mem = LineGraph(top, ("MEMORY USED / AVAILABLE (%)",
                                     ["Used", "Free"]),
                               [self.mem_used, self.mem_free],
                               [C_PURPLE, C_GREEN], height=180)
        self.g_mem.grid(row=0, column=1, sticky="nsew", padx=(3, 0))

        mid = ttk.Frame(outer)
        mid.pack(fill="both", expand=True, pady=(6, 0))
        mid.columnconfigure(0, weight=3)
        mid.columnconfigure(1, weight=2)
        mid.rowconfigure(0, weight=1)

        left = ttk.Frame(mid)
        left.grid(row=0, column=0, sticky="nsew", padx=(0, 6))
        bar = ttk.Frame(left)
        bar.pack(fill="x")
        ttk.Label(bar, text="PROCESS TABLE (live)", foreground=C_FG,
                  background=C_BG, font=("Segoe UI", 10, "bold")).pack(side="left")
        ttk.Label(bar, text="   sort:", foreground=C_DIM,
                  background=C_BG).pack(side="left")
        for val, lbl in (("cpu", "Top CPU"), ("mem", "Top RAM")):
            tk.Radiobutton(bar, text=lbl, variable=self.sort_mode, value=val,
                           command=lambda: None, bg=C_BG, fg=C_FG,
                           activebackground=C_BG, activeforeground=C_FG,
                           selectcolor=C_PANEL).pack(side="left")

        cols = ("pid", "name", "cpu", "mem", "prio", "user")
        heads = ("PID", "Process Name", "CPU %", "Memory %", "Priority", "Owner")
        widths = (70, 210, 80, 90, 120, 90)
        self.tree = ttk.Treeview(left, columns=cols, show="headings", height=14)
        for c, hd, wd in zip(cols, heads, widths):
            self.tree.heading(c, text=hd)
            self.tree.column(c, width=wd, anchor="w")
        vsb = ttk.Scrollbar(left, orient="vertical", command=self.tree.yview)
        self.tree.configure(yscrollcommand=vsb.set)
        self.tree.pack(side="left", fill="both", expand=True)
        vsb.pack(side="right", fill="y")
        self.tree.tag_configure("hot", foreground=C_RED)
        self.tree.tag_configure("warm", foreground=C_ORANGE)

        right = ttk.Frame(mid)
        right.grid(row=0, column=1, sticky="nsew")

        ctl = tk.Frame(right, bg=C_PANEL, bd=1, relief="solid", pady=6, padx=8)
        ctl.pack(fill="x")
        self.lbl_cpu_status = tk.Label(ctl, text="CPU Status: ...", bg=C_PANEL,
                                       fg=C_DIM, font=("Segoe UI", 10, "bold"),
                                       anchor="w")
        self.lbl_cpu_status.pack(fill="x")
        self.lbl_mem_status = tk.Label(ctl, text="Memory Status: ...", bg=C_PANEL,
                                       fg=C_DIM, font=("Segoe UI", 10, "bold"),
                                       anchor="w")
        self.lbl_mem_status.pack(fill="x")
        self.auto_chk = tk.Checkbutton(
            ctl, text="Auto Optimization ON/OFF "
                      "(safe demotion only, never kills)", variable=self.auto_var,
            bg=C_PANEL, fg=C_FG, activebackground=C_PANEL, activeforeground=C_FG,
            selectcolor=C_BG)
        self.auto_chk.pack(anchor="w")
        row = tk.Frame(ctl, bg=C_PANEL)
        row.pack(fill="x", pady=(4, 0))
        self.btn_opt = tk.Button(row, text="Optimize Now", command=self._manual,
                                 bg="#233", fg=C_FG, activebackground="#345",
                                 activeforeground=C_FG, relief="groove")
        self.btn_opt.pack(side="left")
        self.lbl_state = tk.Label(row, text="engine idle", bg=C_PANEL, fg=C_DIM)
        self.lbl_state.pack(side="left", padx=8)

        lf_live = tk.LabelFrame(right, text=" Live analysis ",
                                bg=C_PANEL, fg=C_FG)
        lf_live.pack(fill="both", expand=True, pady=(6, 3))
        self.txt_live = tk.Text(lf_live, height=10, bg=C_PANEL, fg=C_FG,
                                relief="flat", wrap="word",
                                font=("Consolas", 9), state="disabled")
        self.txt_live.pack(fill="both", expand=True, padx=4, pady=2)

        lf_res = tk.LabelFrame(right, text=" Last optimization result ",
                               bg=C_PANEL, fg=C_FG)
        lf_res.pack(fill="both", expand=True, pady=(3, 0))
        self.txt_result = tk.Text(lf_res, height=11, bg=C_PANEL, fg=C_GREEN,
                                  relief="flat", wrap="word",
                                  font=("Consolas", 9), state="disabled")
        self.txt_result.pack(fill="both", expand=True, padx=4, pady=2)
        self._set_text(self.txt_result,
                       "No optimization run yet this session.\n"
                       "Toggle Auto Optimization or press 'Optimize Now'.")

        foot = tk.Label(outer, text="", bg=C_BG, fg=C_DIM, anchor="w")
        foot.pack(fill="x", pady=(4, 0))
        self.lbl_foot = foot

    @staticmethod
    def _set_text(widget, content, color=None):
        widget.configure(state="normal")
        widget.delete("1.0", "end")
        widget.insert("1.0", content)
        if color:
            widget.configure(fg=color)
        widget.configure(state="disabled")

    # ------------------------------------------------------------ workers
    def _collector_worker(self):
        try:
            col = Collector()
            col.prime()
            while not self.stop_evt.is_set():
                if self.collect_pause.is_set():
                    self.stop_evt.wait(0.25)
                    continue
                infos, sample = col.collect()
                ranked = rank_processes(infos)
                self.q.put(("tick", ranked, sample))
                self.stop_evt.wait(self.interval)
        except Exception:
            self.q.put(("error", traceback.format_exc()))

    def _optimise_worker(self, mode, allowed_pids):
        try:
            # Freeze live collection while measuring: this process's own
            # sampler would otherwise compete with the benchmark and distort
            # the before/after comparison (measured as a real regression in
            # early testing).
            self.collect_pause.set()
            self.q.put(("opt_status", "calibrating benchmark..."))
            if self.bench_iters is None:
                self.bench_iters = calibrate()
            self.q.put(("opt_status", "measuring BEFORE state (~6s)..."))
            before = take_measurement(self.bench_iters)

            self.q.put(("opt_status", "collecting processes..."))
            col = Collector()
            col.prime()
            self.stop_evt.wait(1.2)
            infos, sample = col.collect()
            ranked = rank_processes(infos)
            bottlenecks = detect_bottlenecks(sample)
            candidates, skipped = select_candidates(
                ranked, self.user, allowed_pids=allowed_pids)

            bdesc = "; ".join(b.describe() for b in bottlenecks) or \
                f"CPU {sample.cpu_pct:.1f}% / RAM {sample.mem_pct:.1f}%"

            if not candidates:
                self.q.put(("opt_result", {
                    "mode": mode, "ok": False, "applied": 0,
                    "verdict": "NO-OP: no eligible optimization target found.",
                    "detail": "All consumers are protected/system-owned/already "
                              "low-priority/below CPU threshold.",
                    "bottleneck": bdesc,
                    "before": before.line(), "after": None,
                }))
                return

            pids = ", ".join(str(c.info.pid) for c in candidates)
            plan = (f"Demote {len(candidates)} user process(es): "
                    f"'{candidates[0].current_priority}' -> "
                    f"'{candidates[0].target_priority}' (PIDs {pids})")
            self.q.put(("opt_status",
                        f"applying {len(candidates)} demotion(s)..."))

            results = run_actions(candidates)
            applied = [r for r in results if r.status == "applied"]
            actions_rec = [{
                "pid": r.candidate.info.pid, "name": r.candidate.info.name,
                "from_priority": r.candidate.current_priority,
                "to_priority": r.candidate.target_priority,
                "status": r.status, "detail": r.detail,
            } for r in results]

            after = None
            ev = v = None
            if applied:
                self.q.put(("opt_status", f"settling {cfg.SETTLE_S:g}s, then "
                                          "measuring AFTER (~6s)..."))
                self.stop_evt.wait(cfg.SETTLE_S)
                after = take_measurement(self.bench_iters)
                ev = evaluate(before, after)
                v = verdict(ev, len(applied))

            rec = {
                "mode": f"auto-gui" if mode == "auto" else "manual-gui",
                "scope": "GUI auto" if mode == "auto" else "GUI manual",
                "deep": False,
                "bottlenecks": [vars(b) for b in bottlenecks],
                "before": vars(before),
                "after": vars(after) if after else None,
                "applied": len(applied),
                "actions": actions_rec,
                "evaluation": ({
                    "cpu_delta_pp": round(ev.cpu_delta_pp, 2),
                    "bench_gain_pct": (round(ev.bench_gain_pct, 2)
                                       if ev.bench_gain_pct is not None else None),
                    "contention_before": before.contention,
                    "contention_after": after.contention,
                } if ev else {}),
                "verdict": v or "FAILED: every change was refused by the OS.",
            }
            hist_mod.record(rec)

            self.q.put(("opt_result", {
                "mode": mode, "ok": bool(applied),
                "plan": plan, "bottleneck": bdesc,
                "before": before.line(),
                "after": after.line() if after else None,
                "eval_lines": ev.summary_lines() if ev else None,
                "verdict": rec["verdict"],
                "applied": len(applied), "failed": len(results) - len(applied),
            }))
        except Exception:
            self.q.put(("error", traceback.format_exc()))
        finally:
            self.collect_pause.clear()
            self.q.put(("opt_done", None))

    # ---------------------------------------------------------- UI events
    def _manual(self):
        self._start_optimisation("manual")

    def _start_optimisation(self, mode):
        if self.opt_busy:
            return
        self.opt_busy = True
        self.btn_opt.configure(state="disabled")
        self.lbl_state.configure(text=f"{mode} optimization running...",
                                 fg=C_ORANGE)
        threading.Thread(target=self._optimise_worker, daemon=True,
                         args=(mode, None)).start()

    def _tick(self):
        try:
            while True:
                try:
                    msg = self.q.get_nowait()
                except queue.Empty:
                    break
                kind = msg[0]
                if kind == "tick":
                    self._on_sample(msg[1], msg[2])
                elif kind == "opt_status":
                    self.lbl_state.configure(text=msg[1], fg=C_BLUE)
                elif kind == "opt_result":
                    self._render_result(msg[1])
                elif kind == "opt_done":
                    self.opt_busy = False
                    self.btn_opt.configure(state="normal")
                    self.lbl_state.configure(text="engine idle", fg=C_DIM)
                elif kind == "error":
                    if not self.err_shown:
                        self.err_shown = True
                        self._set_text(self.txt_live, "collector error:\n" + msg[1],
                                       C_RED)
        finally:
            if not self.stop_evt.is_set():
                self.root.after(int(self.interval * 1000), self._tick)

    def _on_sample(self, ranked, sample):
        self.cpu.append(sample.cpu_pct)
        self.mem_used.append(sample.mem_pct)
        self.mem_free.append(max(0.0, 100.0 - sample.mem_pct))
        self.g_cpu.redraw()
        self.g_mem.redraw()

        cpu_hi = sample.cpu_pct >= cfg.CPU_ELEVATED_PCT
        mem_hi = sample.mem_pct >= cfg.MEM_ELEVATED_PCT
        self.lbl_cpu_status.configure(
            text=f"CPU Status: {'HIGH' if cpu_hi else 'NORMAL'} "
                 f"({sample.cpu_pct:.1f}%)",
            fg=(C_ORANGE if cpu_hi else C_GREEN))
        used_gb = sample.mem_used_mb / 1024.0
        total_gb = sample.mem_total_mb / 1024.0
        self.lbl_mem_status.configure(
            text=f"Memory Status: {'HIGH' if mem_hi else 'NORMAL'} "
                 f"({sample.mem_pct:.1f}% | {used_gb:.1f}/{total_gb:.1f} GB)",
            fg=(C_ORANGE if mem_hi else C_GREEN))

        view = list(ranked)
        if self.sort_mode.get() == "mem":
            view.sort(key=lambda r: r.info.rss_mb, reverse=True)
        self.tree.delete(*self.tree.get_children())
        for r in view[:cfg.GUI_TABLE_ROWS]:
            i = r.info
            tag = ("hot" if i.cpu_total >= 50 else
                   "warm" if i.cpu_total >= 15 else "")
            self.tree.insert("", "end", tags=(tag,) if tag else (), values=(
                i.pid, i.name[:40], f"{i.cpu_total:.1f}", f"{i.mem_pct:.2f}",
                i.priority_name or "?", i.username or "?"))

        bottlenecks = detect_bottlenecks(sample)
        lines = ["Detected Bottleneck:"]
        if bottlenecks:
            top = ranked[0].info if ranked else None
            pinned = [f"C{k}" for k, v in enumerate(sample.per_core)
                      if v >= cfg.CPU_CRITICAL_PCT]
            if top is not None:
                lines.append(f"  {top.name} (PID {top.pid}) - "
                             f"{top.cpu_total:.1f}% of total CPU, "
                             f"'{top.priority_name}' priority")
            if pinned:
                lines.append(f"  pinned cores: {', '.join(pinned[:16])}")
            if any(b.kind == "memory" for b in bottlenecks):
                lines.append("  note: RAM pressure cannot be relieved safely "
                             "(no process killing is ever performed)")
        else:
            lines.append(f"  none (CPU < {cfg.CPU_ELEVATED_PCT:g}%, "
                         f"RAM < {cfg.MEM_ELEVATED_PCT:g}%)")

        lines.append("")
        lines.append("Optimization:")
        if not self.opt_busy:
            candidates, _ = select_candidates(ranked, self.user)
            if candidates:
                tgt = candidates[0].target_priority
                cur = candidates[0].current_priority
                pids = ", ".join(str(c.info.pid) for c in candidates)
                lines.append(f"  ready: demote {len(candidates)} eligible user "
                             f"process(es) '{cur}' -> '{tgt}'")
                lines.append(f"  PIDs: {pids}")
            else:
                lines.append("  no eligible target (system healthy or all "
                             "consumers protected/low-priority)")

        self.lbl_foot.configure(text=(
            f"owner gate '{self.user}' | {self.static.cores_logical} logical "
            f"cores | refresh {self.interval:g}s | samples {len(self.cpu)} | "
            f"graph window ~{len(self.cpu) * self.interval / 60:.0f} min | "
            f"auto-opt {'ARMED' if self.auto_var.get() else 'OFF'} "
            f"(cooldown {cfg.AUTO_COOLDOWN_S:g}s)"))

        self._set_text(self.txt_live, "\n".join(lines))

        if (self.auto_var.get() and not self.opt_busy
                and any(b.kind == "cpu" and b.level == "critical"
                        for b in bottlenecks)
                and time.monotonic() - self.last_auto_finish >= cfg.AUTO_COOLDOWN_S):
            self._start_optimisation("auto")

    def _render_result(self, res):
        self.last_auto_finish = time.monotonic()
        sep = "-" * 46
        out = [f"Mode      : {res['mode']}",
               f"Bottleneck: {res['bottleneck']}"]
        if res["ok"]:
            out += [f"Optimization: {res['plan']}",
                    "",
                    "Before:",
                    f"  {res['before']}"]
            if res["after"]:
                out += ["After:",
                        f"  {res['after']}"]
            if res.get("eval_lines"):
                out += ["", res["eval_lines"]]
            out += ["", f"Verdict : {res['verdict']}"]
            color = C_GREEN if str(res["verdict"]).startswith("SUCCESS") else (
                C_ORANGE if str(res["verdict"]).startswith("MARGINAL") else C_RED)
        else:
            out += [res.get("detail", ""), "", f"Verdict : {res['verdict']}"]
            color = C_DIM
        self._set_text(self.txt_result, "\n".join(out), color)

    # ------------------------------------------------------------- close
    def _autotest_close(self):
        print(f"GUI SMOKE OK ticks={len(self.cpu)} busy={self.opt_busy}",
              flush=True)
        self._on_close()

    def _on_close(self):
        self.stop_evt.set()
        self.root.destroy()


def _log_crash(text: str):
    """Persist tracebacks - pythonw has no stderr to show them."""
    try:
        log = os.path.join(os.path.dirname(os.path.dirname(
            os.path.abspath(__file__))), "gui_error.log")
        with open(log, "a", encoding="utf-8") as f:
            f.write(f"[{time.strftime('%Y-%m-%d %H:%M:%S')}]\n{text}\n\n")
    except Exception:
        pass


def run_gui(interval: float = cfg.GUI_REFRESH_S) -> int:
    try:
        root = tk.Tk()
    except Exception as exc:
        print(f"Cannot open display/GUI: {exc}", file=os.sys.stderr)
        return 4

    def _cb_exc(etyp, eval_, etb):
        _log_crash("".join(traceback.format_exception(etyp, eval_, etb)))

    root.report_callback_exception = _cb_exc
    _log_crash(f"session starting pid={os.getpid()} interval={interval}")
    root.withdraw()
    try:
        app = GuiApp(root, interval)
        root.deiconify()
        root.mainloop()
        _log_crash("mainloop returned cleanly")
        return 0
    except Exception:
        tb = traceback.format_exc()
        traceback.print_exc()
        _log_crash(tb)
        try:
            from tkinter import messagebox
            messagebox.showerror("Optimizer GUI error", tb)
        except Exception:
            pass
        return 4
