#!/usr/bin/env python3
"""Intelligent OS Resource Optimization System.

Pipeline:  real OS data -> analysis -> bottleneck detection -> optimization
           algorithm -> safe optimization -> before/after measurement ->
           measured performance improvement.

All input comes from the live operating system through psutil (Win32 /
POSIX APIs underneath).  Nothing is simulated.

Usage:
  python main.py dashboard [--interval S] [--sort cpu|mem] [--once]
  python main.py gui      [--interval S]        real-time graphical window
  python main.py processes [--sort cpu|mem|threads] [--top N]
  python main.py analyze
  python main.py optimize [--yes] [--deep] [--include NAME] [--target PID ...]
                          [--skip-benchmark] [--dry-run]
  python main.py history [--limit N]
  python main.py restore [--last]
"""

import argparse
import sys
import time

try:
    sys.stdout.reconfigure(errors="replace")
except Exception:
    pass

from optsys import config as cfg
from optsys import history, measurement, optimizer, ui
from optsys.analyzer import (detect_bottlenecks, explain_score,
                             rank_processes, select_candidates)
from optsys.collector import (Collector, current_username, static_info)
from optsys.measurement import (calibrate, evaluate, take_measurement,
                                verdict)


# ---------------------------------------------------------------------------
# shared helpers
# ---------------------------------------------------------------------------
def _print_static(static):
    up = time.time() - static.boot_ts
    rows = [
        ["Host", f"{static.hostname}"],
        ["OS", f"{static.system} {static.release} (build {static.os_version})"],
        ["CPU", f"{static.cores_logical} logical / "
                f"{static.cores_physical or '?'} physical cores"
                + (f", {static.freq_mhz:.0f} MHz" if static.freq_mhz else "")],
        ["RAM", f"{static.ram_total_mb:,.0f} MiB total, "
                f"swap {static.swap_total_mb:,.0f} MiB"],
        ["Uptime", ui.fmt_age(up)],
        ["Python", static.python_version],
    ]
    print(ui.table(["Field", "Value"], rows))


def _process_rows(ranked, limit):
    rows = []
    for idx, r in enumerate(ranked[:limit], 1):
        i = r.info
        rows.append([
            idx, i.pid, i.name[:28], i.username or "?", i.status[:8],
            i.priority_name or "?",
            f"{i.cpu_total:.1f}", f"{i.cpu_one_core:.0f}",
            f"{i.rss_mb:,.0f}", i.threads, ui.fmt_age(i.age_s),
            f"{r.score:.1f}",
        ])
    return rows


PROC_HEADERS = ["#", "PID", "Name", "User", "Status", "Priority",
                "CPU%tot", "CPU%c1", "RSS MiB", "Thr", "Age", "Score"]


def _collect_ranked(collector, gap_s=1.2):
    """Prime counters, wait for a measurable window, sweep."""
    collector.prime()
    time.sleep(gap_s)
    infos, sample = collector.collect()
    return rank_processes(infos), sample


# ---------------------------------------------------------------------------
# commands
# ---------------------------------------------------------------------------
def cmd_dashboard(args):
    collector = Collector()
    static = static_info()
    collector.prime()
    frames = 0
    try:
        while True:
            infos, sample = collector.collect()
            ranked = rank_processes(infos)
            key = args.sort
            if key == "mem":
                ranked.sort(key=lambda r: r.info.rss_mb, reverse=True)
            rows = []
            for idx, r in enumerate(ranked[:args.lines], 1):
                i = r.info
                rows.append([idx, i.pid, i.name[:30], i.priority_name or "?",
                             f"{i.cpu_total:.1f}", f"{i.cpu_one_core:.0f}",
                             f"{i.rss_mb:,.0f}", i.threads])
            ui.clear()
            print(ui.paint("INTELLIGENT OS RESOURCE OPTIMIZER - LIVE DASHBOARD",
                           ui.C.BOLD))
            print(f"{static.hostname} | {static.system} {static.release} | "
                  f"{time.strftime('%H:%M:%S')}   (Ctrl+C to quit)")
            print(ui.hr())
            print(f"CPU   {ui.bar(sample.cpu_pct)}")
            print(f"RAM   {ui.bar(sample.mem_pct)}   "
                  f"({sample.mem_used_mb:,.0f}/{sample.mem_total_mb:,.0f} MiB)")
            print(f"SWAP  {ui.bar(sample.swap_pct)}")
            per_core = sample.per_core[:32]
            width = 14
            for start in range(0, len(per_core), 4):
                cells = []
                for ci, val in enumerate(per_core[start:start + 4], start):
                    filled = int(round(val / 100 * width))
                    cells.append(f"C{ci:<2}" + "[" + "#" * filled +
                                 "." * (width - filled) + f"]{val:4.0f}")
                print(" ".join(cells))
            print(ui.hr())
            hdr = ["#", "PID", "Name", "Prio", "CPU%t", "CPU%c",
                   "RSS MiB", "Thr"]
            print(ui.table(hdr, rows))
            frames += 1
            if args.once:
                break
            time.sleep(args.interval)
    except KeyboardInterrupt:
        print(f"\nDashboard stopped after {frames} frame(s).")


def cmd_processes(args):
    collector = Collector()
    ranked, _ = _collect_ranked(collector)
    keyfn = {
        "cpu": lambda r: r.info.cpu_total,
        "mem": lambda r: r.info.rss_mb,
        "threads": lambda r: r.info.threads,
    }[args.sort]
    ranked.sort(key=keyfn, reverse=True)
    print(ui.title(f"TOP {args.top} PROCESSES BY {args.sort.upper()} (live)"))
    print(ui.table(PROC_HEADERS, _process_rows(ranked, args.top)))


def cmd_analyze(args):
    static = static_info()
    print(ui.title("SYSTEM ANALYSIS (live OS data)"))
    _print_static(static)
    print()

    print(ui.title("PHASE 1: SYSTEM SAMPLING"))
    sysm = take_measurement(bench_iters=None)
    print(f"  averaged over {cfg.SAMPLE_WINDOW_S:g}s: {sysm.line()}")

    print()
    print(ui.title("PHASE 2: PROCESS SWEEP & BOTTLENECK DETECTION"))
    collector = Collector()
    ranked, sample = _collect_ranked(collector)
    bottlenecks = detect_bottlenecks(sample)
    if bottlenecks:
        for b in bottlenecks:
            color = ui.RED if b.level == "critical" else ui.YELLOW
            print("  " + ui.paint("!! " + b.describe(), color))
    else:
        print("  No bottleneck: CPU/RAM below thresholds "
              f"({cfg.CPU_ELEVATED_PCT:g}% / {cfg.MEM_ELEVATED_PCT:g}%).")

    hot = [c for c in sample.per_core if c >= cfg.CPU_CRITICAL_PCT]
    if hot:
        print(f"  {len(hot)} core(s) pinned >= {cfg.CPU_CRITICAL_PCT:g}%: "
              + ", ".join(f"C{i}:{v:.0f}%" for i, v in enumerate(sample.per_core)
                          if v >= cfg.CPU_CRITICAL_PCT))

    print()
    print(ui.title(f"PHASE 3: TOP CONSUMERS (weighted score: "
                   f"cpu x{cfg.W_CPU:g} + mem x{cfg.W_MEM:g} + threads x{cfg.W_THREADS:g})"))
    print(ui.table(PROC_HEADERS, _process_rows(ranked, 15)))
    if ranked:
        print("\n  example score breakdown (rank 1): "
              + explain_score(ranked[0]))

    print()
    print(ui.title("PHASE 4: OPTIMIZATION PREVIEW (what 'optimize' would do)"))
    candidates, skipped = select_candidates(
        ranked, current_username(),
        allowed_pids=set(args.target) if args.target else None,
        name_filter=args.include, deep=args.deep)
    if candidates:
        rows = [[n, c.info.pid, c.info.name[:24], c.current_priority,
                 "->", c.target_priority, f"{c.score:.1f}"]
                for n, c in enumerate(candidates, 1)]
        print(ui.table(["#", "PID", "Name", "Current", "", "Target", "Score"],
                       rows))
        for n, c in enumerate(candidates, 1):
            print(f"  [{n}] PID {c.info.pid} ({c.info.name}): {c.reason}")
    else:
        print("  No eligible optimization target right now "
              "(system healthy or all consumers protected/low-priority).")
    inscope = skipped
    if args.include or args.target:
        print("  In-scope but skipped:")
        for s in inscope[:15]:
            print(f"    PID {s.info.pid} {s.info.name}: {s.why}")
    return 0


def cmd_optimize(args):
    user = current_username()
    static = static_info()
    bench_iters = None
    if not args.skip_benchmark:
        print("Calibrating benchmark against this machine...", flush=True)
        bench_iters = calibrate()
        print(f"  calibrated: {bench_iters:,} iterations "
              f"(~{cfg.BENCH_TARGET_MS:g} ms target)")

    print()
    print(ui.title("STEP 1/6  BEFORE MEASUREMENT"))
    before = take_measurement(bench_iters)
    print("  " + before.line())

    print()
    print(ui.title("STEP 2/6  LIVE DATA COLLECTION & BOTTLENECK DETECTION"))
    collector = Collector()
    ranked, sample = _collect_ranked(collector)
    bottlenecks = detect_bottlenecks(sample)
    if bottlenecks:
        for b in bottlenecks:
            color = ui.RED if b.level == "critical" else ui.YELLOW
            print("  " + ui.paint("!! " + b.describe(), color))
    else:
        print(f"  CPU {sample.cpu_pct:.1f}% / RAM {sample.mem_pct:.1f}% - "
              "below configured bottleneck thresholds.")

    scope = ("explicit PIDs " + ",".join(map(str, args.target))
             if args.target else
             (f"name filter '{args.include}'" if args.include else
              "all current-user processes"))
    print(f"  Scope: {scope} | owner gate: '{user}' | deep={args.deep}")

    candidates, skipped = select_candidates(
        ranked, user,
        allowed_pids=set(args.target) if args.target else None,
        name_filter=args.include, deep=args.deep)

    def history_record(extra):
        rec = {
            "mode": "target" if args.target else (
                "filter" if args.include else "auto"),
            "scope": scope,
            "deep": args.deep,
            "bottlenecks": [vars(b) for b in bottlenecks],
            "before": vars(before),
            "applied": extra.get("applied", 0),
            "actions": extra.get("actions", []),
        }
        if args.skip_benchmark:
            rec["note"] = "benchmark disabled"
        rec.update({k: v for k, v in extra.items() if k not in rec})
        history.record(rec)

    if not candidates:
        print()
        print(ui.paint("RESULT: no eligible optimization target found - "
                       "nothing was changed.", ui.YELLOW))
        if skipped:
            print("  Nearest rejects:")
            for s in skipped[:10]:
                print(f"    PID {s.info.pid} {s.info.name}: {s.why}")
        after = take_measurement(bench_iters)
        ev = evaluate(before, after)
        v = verdict(ev, 0)
        history_record({"after": vars(after),
                        "evaluation": {"cpu_delta_pp": ev.cpu_delta_pp},
                        "verdict": v})
        print(f"  History saved to {history.HISTORY_FILE}")
        return 2

    print()
    print(ui.title("STEP 3/6  OPTIMIZATION PLAN (explainable decisions)"))
    rows = [[n, c.info.pid, c.info.name[:26], c.current_priority,
             c.target_priority, f"{c.score:.1f}", f"{c.info.username}"]
            for n, c in enumerate(candidates, 1)]
    print(ui.table(["#", "PID", "Name", "Current prio", "Target prio",
                    "Score", "Owner"], rows))
    print()
    for n, c in enumerate(candidates, 1):
        print(f"  Decision [{n}] PID {c.info.pid} {c.info.name}:")
        print(f"      {c.reason}")

    if skipped:
        shown = skipped[:8]
        print("\n  Not eligible (examples):")
        for s in shown:
            print(f"    PID {s.info.pid} {s.info.name}: {s.why}")

    if args.dry_run:
        print()
        print(ui.paint("DRY RUN: plan generated, nothing was applied.",
                       ui.CYAN))
        history_record({"verdict": "DRY-RUN (plan only)",
                        "actions": [], "applied": 0})
        return 0

    print()
    if not args.yes:
        if not ui.confirm(ui.paint(
                f"Apply {len(candidates)} SAFE priority demotion(s) now?",
                ui.BOLD)):
            print("Aborted by user - no changes made.")
            history_record({"verdict": "ABORTED by user", "actions": [],
                            "applied": 0})
            return 1

    print()
    print(ui.title("STEP 4/6  APPLYING SAFE PRIORITY CHANGES"))
    results = optimizer.run_actions(candidates)
    status_rows = []
    for r in results:
        color = {"applied": ui.GREEN, "access_denied": ui.YELLOW}.get(
            r.status, ui.RED)
        status_rows.append([
            r.candidate.info.pid, r.candidate.info.name[:26],
            f"{r.candidate.current_priority} -> {r.candidate.target_priority}",
            ui.paint(r.status, color), r.detail[:60],
        ])
    print(ui.table(["PID", "Name", "Change", "Status", "Detail"], status_rows))
    applied = [r for r in results if r.status == "applied"]

    if not applied:
        msg = ("FAILED: every change was refused by the OS "
               "(permissions?). Nothing optimized.")
        print(ui.paint(msg, ui.RED))
        history_record({"verdict": msg, "applied": 0,
                        "actions": [vars(r) for r in results]})
        return 3

    print()
    print(ui.title(f"STEP 5/6  SETTLING ({cfg.SETTLE_S:g}s) THEN AFTER MEASUREMENT"))
    time.sleep(cfg.SETTLE_S)
    after = take_measurement(bench_iters)
    print("  BEFORE: " + before.line())
    print("  AFTER : " + after.line())

    ev = evaluate(before, after)
    v = verdict(ev, len(applied))
    print()
    print(ui.title("STEP 6/6  MEASURED RESULT"))
    print(ev.summary_lines())
    color = ui.GREEN if v.startswith("SUCCESS") else (
        ui.YELLOW if v.startswith(("MARGINAL", "INCONCLUSIVE")) else ui.RED)
    print()
    print(ui.paint("VERDICT: " + v, color))

    history_record({
        "after": vars(after),
        "evaluation": {
            "cpu_delta_pp": round(ev.cpu_delta_pp, 2),
            "mem_delta_pp": round(ev.mem_delta_pp, 2),
            "bench_gain_pct": (round(ev.bench_gain_pct, 2)
                               if ev.bench_gain_pct is not None else None),
            "contention_before": before.contention,
            "contention_after": after.contention,
        },
        "verdict": v,
        "applied": len(applied),
        "actions": [{
            "pid": r.candidate.info.pid,
            "name": r.candidate.info.name,
            "from_priority": r.candidate.current_priority,
            "to_priority": r.candidate.target_priority,
            "status": r.status,
            "detail": r.detail,
        } for r in results],
    })
    print(f"\n  Record appended to {history.HISTORY_FILE}")
    return 0


def cmd_history(args):
    recs = history.recent(args.limit)
    if not recs:
        print("No optimization history yet - run 'optimize' first.")
        return 0
    rows = []
    for r in recs:
        ev = r.get("evaluation", {}) or {}
        gain = ev.get("bench_gain_pct")
        rows.append([
            r.get("timestamp", "?"),
            r.get("mode", "?"),
            f"{r.get('applied', 0)}",
            (f"{ev.get('cpu_delta_pp', 0):+.1f}"
             if isinstance(ev.get("cpu_delta_pp"), (int, float)) else "-"),
            (f"{gain:+.2f}%" if isinstance(gain, (int, float)) else "-"),
            str(r.get("verdict", "?"))[:52],
        ])
    print(ui.title(f"OPTIMIZATION HISTORY (last {len(recs)}, newest first)"))
    print(ui.table(["Timestamp", "Mode", "Applied", "CPU dpp", "Bench gain",
                    "Verdict"], rows))
    return 0


def cmd_restore(args):
    rec = history.last_record()
    if not rec:
        print("Nothing to restore.")
        return 1
    targets = history.restore_targets(rec)
    if not targets:
        print("Last record has no revertible applied changes.")
        return 1
    print(f"Restoring {len(targets)} process(es) to their original priority "
          f"(record {rec.get('timestamp')})...")
    ok = 0
    import psutil
    for a in targets:
        status, detail, verified = optimizer.apply_priority(
            a["pid"], a["from_priority"])
        color = ui.GREEN if status == "applied" else ui.YELLOW
        print(f"  PID {a['pid']} {a['name'][:24]:24} -> "
              f"'{a['from_priority']}': "
              + ui.paint(status, color) + f" ({detail})")
        ok += status == "applied"
    print(f"Restored {ok}/{len(targets)}.")
    return 0 if ok else 3


# ---------------------------------------------------------------------------
def cmd_gui(args):
    try:
        from optsys.gui import run_gui
    except ImportError as exc:
        print(f"tkinter is not available: {exc}", file=sys.stderr)
        return 4
    return run_gui(interval=args.interval)


def build_parser():
    p = argparse.ArgumentParser(
        prog="main.py",
        description="Intelligent OS Resource Optimization System "
                    "(real data, safe priority-based optimization)")
    sub = p.add_subparsers(dest="cmd")

    d = sub.add_parser("dashboard", help="live read-only dashboard")
    d.add_argument("--interval", type=float, default=2.0)
    d.add_argument("--sort", choices=["cpu", "mem"], default="cpu")
    d.add_argument("--lines", type=int, default=12)
    d.add_argument("--once", action="store_true")
    d.set_defaults(fn=cmd_dashboard)

    pr = sub.add_parser("processes", help="one-shot process table")
    pr.add_argument("--sort", choices=["cpu", "mem", "threads"],
                    default="cpu")
    pr.add_argument("--top", type=int, default=25)
    pr.set_defaults(fn=cmd_processes)

    an = sub.add_parser("analyze", help="analysis + ranking + preview")
    an.add_argument("--include", default=None)
    an.add_argument("--target", type=int, action="append", default=[])
    an.add_argument("--deep", action="store_true")
    an.set_defaults(fn=cmd_analyze)

    op = sub.add_parser("optimize", help="full optimize pipeline")
    op.add_argument("--yes", "-y", action="store_true",
                    help="skip confirmation prompt")
    op.add_argument("--deep", action="store_true",
                    help="demote straight to lowest class instead of one level")
    op.add_argument("--include", default=None,
                    help="only consider processes whose name contains NAME")
    op.add_argument("--target", type=int, action="append", default=[],
                    help="restrict to explicit PID(s); repeatable")
    op.add_argument("--skip-benchmark", action="store_true",
                    help="measure system counters only")
    op.add_argument("--dry-run", action="store_true",
                    help="show the plan without applying anything")
    op.set_defaults(fn=cmd_optimize)

    g = sub.add_parser("gui", help="real-time graphical dashboard (tkinter)")
    g.add_argument("--interval", type=float, default=cfg.GUI_REFRESH_S)
    g.set_defaults(fn=cmd_gui)

    h = sub.add_parser("history", help="show past optimization runs")
    h.add_argument("--limit", type=int, default=20)
    h.set_defaults(fn=cmd_history)

    rs = sub.add_parser("restore", help="undo last applied priority changes")
    rs.add_argument("--last", action="store_true")
    rs.set_defaults(fn=cmd_restore)
    return p


def main(argv=None):
    parser = build_parser()
    args = parser.parse_args(argv)
    if not getattr(args, "fn", None):
        parser.print_help()
        return 0
    try:
        return args.fn(args)
    except KeyboardInterrupt:
        print("\nInterrupted.")
        return 130
    except Exception as exc:
        print(f"FATAL: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 4


if __name__ == "__main__":
    sys.exit(main())
