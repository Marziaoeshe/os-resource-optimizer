#!/usr/bin/env python3
"""CLI wrapper for OS Resource Optimizer - for OpenCode integration."""

import sys
import os
import json
import time

# Add project root to path
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from optsys import config as cfg, history, measurement
from optsys.analyzer import detect_bottlenecks, rank_processes, select_candidates
from optsys.collector import Collector, current_username, static_info
from optsys.measurement import calibrate, take_measurement, evaluate, verdict
from optsys import optimizer


def cmd_info():
    s = static_info()
    up = time.time() - s.boot_ts
    print(json.dumps({
        "hostname": s.hostname,
        "os": f"{s.system} {s.release} (build {s.os_version})",
        "cpu_logical": s.cores_logical,
        "cpu_physical": s.cores_physical,
        "cpu_mhz": s.freq_mhz,
        "ram_total_mib": s.ram_total_mb,
        "swap_total_mib": s.swap_total_mb,
        "uptime_seconds": round(up),
        "python": s.python_version,
    }, indent=2))


def cmd_processes(sort_by="cpu", top=15):
    collector = Collector()
    collector.prime()
    time.sleep(1.2)
    infos, sample = collector.collect()
    ranked = rank_processes(infos)

    keyfn = {
        "cpu": lambda r: r.info.cpu_total,
        "mem": lambda r: r.info.rss_mb,
        "threads": lambda r: r.info.threads,
    }.get(sort_by, lambda r: r.info.cpu_total)
    ranked.sort(key=keyfn, reverse=True)

    rows = []
    for idx, r in enumerate(ranked[:top], 1):
        i = r.info
        rows.append({
            "rank": idx, "pid": i.pid, "name": i.name[:28],
            "user": i.username or "?", "priority": i.priority_name or "?",
            "cpu_pct": round(i.cpu_total, 1), "ram_mib": round(i.rss_mb, 0),
            "threads": i.threads, "score": round(r.score, 1),
        })
    print(json.dumps(rows, indent=2))


def cmd_analyze(deep=False):
    s = static_info()
    result = {"system": {"host": s.hostname, "os": f"{s.system} {s.release}",
                         "cpu": s.cores_logical, "ram_mib": s.ram_total_mb}}

    sysm = take_measurement(bench_iters=None)
    result["before_measurement"] = {"cpu": sysm.cpu_pct, "mem": sysm.mem_pct,
                                    "swap": sysm.swap_pct}

    collector = Collector()
    collector.prime()
    time.sleep(1.2)
    infos, sample = collector.collect()
    ranked = rank_processes(infos)
    bottlenecks = detect_bottlenecks(sample)

    result["bottlenecks"] = [{"level": b.level, "type": b.btype,
                              "value": b.value, "threshold": b.threshold,
                              "description": b.describe()} for b in bottlenecks]

    result["top_processes"] = [{"pid": r.info.pid, "name": r.info.name[:24],
                                "cpu": round(r.info.cpu_total, 1),
                                "ram_mib": round(r.info.rss_mb, 0),
                                "score": round(r.score, 1)}
                               for r in ranked[:10]]

    user = current_username()
    candidates, skipped = select_candidates(ranked, user, deep=deep)
    result["optimization_candidates"] = [
        {"pid": c.info.pid, "name": c.info.name,
         "from": c.current_priority, "to": c.target_priority,
         "reason": c.reason} for c in candidates]
    result["skipped"] = [{"pid": s.info.pid, "name": s.info.name,
                          "reason": s.why} for s in skipped[:5]]

    print(json.dumps(result, indent=2))


def cmd_optimize(deep=False, target_pids=None, name_filter=None, dry_run=False):
    result = {"steps": []}
    user = current_username()

    bench_iters = calibrate()
    result["calibration"] = bench_iters

    before = take_measurement(bench_iters)
    result["before"] = {"cpu": before.cpu_pct, "mem": before.mem_pct,
                        "benchmark_ms": before.bench_ms}

    collector = Collector()
    collector.prime()
    time.sleep(1.2)
    infos, sample = collector.collect()
    ranked = rank_processes(infos)
    bottlenecks = detect_bottlenecks(sample)

    candidates, skipped = select_candidates(
        ranked, user,
        allowed_pids=set(target_pids) if target_pids else None,
        name_filter=name_filter, deep=deep)

    if not candidates:
        result["status"] = "no_targets"
        result["message"] = "No eligible optimization targets found."
        print(json.dumps(result, indent=2))
        return

    result["plan"] = [{"pid": c.info.pid, "name": c.info.name,
                       "from": c.current_priority, "to": c.target_priority}
                      for c in candidates]

    if dry_run:
        result["status"] = "dry_run"
        print(json.dumps(result, indent=2))
        return

    results = optimizer.run_actions(candidates)
    applied = [r for r in results if r.status == "applied"]

    result["applied"] = [{"pid": r.candidate.info.pid,
                          "name": r.candidate.info.name,
                          "from": r.candidate.current_priority,
                          "to": r.candidate.target_priority,
                          "status": r.status} for r in results]

    if not applied:
        result["status"] = "failed"
        print(json.dumps(result, indent=2))
        return

    time.sleep(cfg.SETTLE_S)
    after = take_measurement(bench_iters)
    ev = evaluate(before, after)
    v = verdict(ev, len(applied))

    result["after"] = {"cpu": after.cpu_pct, "mem": after.mem_pct,
                       "benchmark_ms": after.bench_ms}
    result["improvement"] = {
        "bench_gain_pct": round(ev.bench_gain_pct, 2) if ev.bench_gain_pct else None,
        "contention_before": before.contention,
        "contention_after": after.contention,
    }
    result["status"] = v

    rec = {"mode": "mcp", "deep": deep,
           "before": vars(before), "after": vars(after),
           "applied": len(applied), "verdict": v,
           "actions": [{"pid": r.candidate.info.pid,
                        "name": r.candidate.info.name,
                        "from": r.candidate.current_priority,
                        "to": r.candidate.target_priority,
                        "status": r.status} for r in results]}
    history.record(rec)

    print(json.dumps(result, indent=2))


def cmd_history(limit=10):
    recs = history.recent(limit)
    rows = []
    for r in recs:
        ev = r.get("evaluation", {}) or {}
        rows.append({
            "timestamp": r.get("timestamp"),
            "mode": r.get("mode"),
            "applied": r.get("applied"),
            "verdict": r.get("verdict"),
            "bench_gain_pct": ev.get("bench_gain_pct"),
        })
    print(json.dumps(rows, indent=2))


def cmd_restore():
    rec = history.last_record()
    if not rec:
        print(json.dumps({"status": "nothing_to_restore"}))
        return

    targets = history.restore_targets(rec)
    if not targets:
        print(json.dumps({"status": "no_revertible_changes"}))
        return

    results = []
    ok = 0
    for a in targets:
        status, detail, verified = optimizer.apply_priority(
            a["pid"], a["from_priority"])
        results.append({"pid": a["pid"], "name": a["name"],
                        "priority": a["from_priority"], "status": status})
        if status == "applied":
            ok += 1

    print(json.dumps({"status": "restored", "ok": ok,
                       "total": len(targets), "details": results}, indent=2))


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Usage: opencode_tool.py <command> [args]")
        print("Commands: info, processes, analyze, optimize, history, restore")
        sys.exit(1)

    cmd = sys.argv[1]
    args = sys.argv[2:]

    if cmd == "info":
        cmd_info()
    elif cmd == "processes":
        sort_by = args[0] if args else "cpu"
        top = int(args[1]) if len(args) > 1 else 15
        cmd_processes(sort_by, top)
    elif cmd == "analyze":
        deep = "--deep" in args
        cmd_analyze(deep)
    elif cmd == "optimize":
        deep = "--deep" in args
        dry_run = "--dry-run" in args
        targets = [int(a) for a in args if a.isdigit()]
        name_filter = None
        if "--name" in args:
            idx = args.index("--name")
            if idx + 1 < len(args):
                name_filter = args[idx + 1]
        cmd_optimize(deep, targets or None, name_filter, dry_run)
    elif cmd == "history":
        limit = int(args[0]) if args else 10
        cmd_history(limit)
    elif cmd == "restore":
        cmd_restore()
    else:
        print(f"Unknown command: {cmd}")
        sys.exit(1)
