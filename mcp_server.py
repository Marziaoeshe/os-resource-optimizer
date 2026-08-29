#!/usr/bin/env python3
"""MCP Server for OS Resource Optimizer.

Wraps the CLI commands as MCP tools for OpenCode integration.
"""

import sys
import os
import json
from pathlib import Path

# Add the project root to path so we can import optsys
sys.path.insert(0, str(Path(__file__).parent))

from mcp.server.fastmcp import FastMCP

from optsys import config as cfg, history, measurement
from optsys.analyzer import detect_bottlenecks, rank_processes, select_candidates
from optsys.collector import Collector, current_username, static_info
from optsys.measurement import calibrate, take_measurement, evaluate, verdict
from optsys import optimizer

mcp = FastMCP("os-resource-optimizer", dependencies=["psutil>=5.9"])


@mcp.tool()
def get_system_info() -> str:
    """Get static system information (hostname, OS, CPU, RAM, uptime)."""
    s = static_info()
    import time
    up = time.time() - s.boot_ts
    lines = [
        f"Host: {s.hostname}",
        f"OS: {s.system} {s.release} (build {s.os_version})",
        f"CPU: {s.cores_logical} logical / {s.cores_physical or '?'} physical cores"
        + (f", {s.freq_mhz:.0f} MHz" if s.freq_mhz else ""),
        f"RAM: {s.ram_total_mb:,.0f} MiB total, swap {s.swap_total_mb:,.0f} MiB",
        f"Python: {s.python_version}",
    ]
    return "\n".join(lines)


@mcp.tool()
def list_processes(sort_by: str = "cpu", top: int = 15) -> str:
    """List top processes by CPU or memory usage.

    Args:
        sort_by: Sort by 'cpu', 'mem', or 'threads'
        top: Number of processes to show (default 15)
    """
    collector = Collector()
    collector.prime()
    import time
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
            "rank": idx,
            "pid": i.pid,
            "name": i.name[:28],
            "user": i.username or "?",
            "status": i.status[:8],
            "priority": i.priority_name or "?",
            "cpu_percent_total": round(i.cpu_total, 1),
            "cpu_percent_one_core": round(i.cpu_one_core, 0),
            "ram_mib": round(i.rss_mb, 0),
            "threads": i.threads,
            "score": round(r.score, 1),
        })
    return json.dumps(rows, indent=2)


@mcp.tool()
def analyze_system(deep: bool = False) -> str:
    """Analyze system for bottlenecks and show optimization preview.

    Args:
        deep: If true, consider idle-priority demotion targets
    """
    s = static_info()
    import time
    up = time.time() - s.boot_ts
    lines = [
        "=== SYSTEM INFO ===",
        f"Host: {s.hostname}",
        f"OS: {s.system} {s.release}",
        f"CPU: {s.cores_logical} logical cores",
        f"RAM: {s.ram_total_mb:,.0f} MiB",
        "",
        "=== SAMPLING (3s) ===",
    ]

    sysm = take_measurement(bench_iters=None)
    lines.append(sysm.line())

    lines.append("")
    lines.append("=== BOTTLENECK DETECTION ===")
    collector = Collector()
    collector.prime()
    time.sleep(1.2)
    infos, sample = collector.collect()
    ranked = rank_processes(infos)
    bottlenecks = detect_bottlenecks(sample)

    if bottlenecks:
        for b in bottlenecks:
            lines.append(f"!! {b.describe()}")
    else:
        lines.append("No bottleneck: CPU/RAM below thresholds.")

    lines.append("")
    lines.append("=== TOP CONSUMERS ===")
    for idx, r in enumerate(ranked[:10], 1):
        i = r.info
        lines.append(
            f"{idx}. PID {i.pid} {i.name[:24]} | "
            f"CPU {i.cpu_total:.1f}% | RAM {i.rss_mb:,.0f} MiB | "
            f"Score {r.score:.1f}"
        )

    lines.append("")
    lines.append("=== OPTIMIZATION PREVIEW ===")
    user = current_username()
    candidates, skipped = select_candidates(ranked, user, deep=deep)
    if candidates:
        for n, c in enumerate(candidates, 1):
            lines.append(
                f"[{n}] PID {c.info.pid} ({c.info.name}): "
                f"{c.current_priority} -> {c.target_priority} | {c.reason}"
            )
    else:
        lines.append("No eligible optimization targets.")

    return "\n".join(lines)


@mcp.tool()
def optimize_system(
    yes: bool = True,
    deep: bool = False,
    target_pids: list[int] | None = None,
    name_filter: str | None = None,
    dry_run: bool = False,
) -> str:
    """Run the full optimization pipeline (safe priority demotion).

    Args:
        yes: Skip confirmation prompt (default True for MCP)
        deep: Demote straight to lowest class instead of one level
        target_pids: Restrict to specific PIDs (optional)
        name_filter: Only consider processes whose name contains this string
        dry_run: Show plan without applying changes
    """
    lines = []
    user = current_username()

    lines.append("=== CALIBRATING BENCHMARK ===")
    bench_iters = calibrate()
    lines.append(f"Calibrated: {bench_iters:,} iterations")

    lines.append("")
    lines.append("=== STEP 1: BEFORE MEASUREMENT ===")
    before = take_measurement(bench_iters)
    lines.append(before.line())

    lines.append("")
    lines.append("=== STEP 2: BOTTLENECK DETECTION ===")
    collector = Collector()
    collector.prime()
    import time
    time.sleep(1.2)
    infos, sample = collector.collect()
    ranked = rank_processes(infos)
    bottlenecks = detect_bottlenecks(sample)

    if bottlenecks:
        for b in bottlenecks:
            lines.append(f"!! {b.describe()}")
    else:
        lines.append(f"CPU {sample.cpu_pct:.1f}% / RAM {sample.mem_pct:.1f}% - normal.")

    candidates, skipped = select_candidates(
        ranked, user,
        allowed_pids=set(target_pids) if target_pids else None,
        name_filter=name_filter,
        deep=deep,
    )

    if not candidates:
        lines.append("")
        lines.append("No eligible optimization targets found. Nothing changed.")
        return "\n".join(lines)

    lines.append("")
    lines.append(f"=== STEP 3: OPTIMIZATION PLAN ({len(candidates)} targets) ===")
    for n, c in enumerate(candidates, 1):
        lines.append(
            f"[{n}] PID {c.info.pid} ({c.info.name}): "
            f"{c.current_priority} -> {c.target_priority}"
        )
        lines.append(f"    {c.reason}")

    if dry_run:
        lines.append("")
        lines.append("DRY RUN: plan generated, nothing was applied.")
        return "\n".join(lines)

    lines.append("")
    lines.append("=== STEP 4: APPLYING PRIORITY CHANGES ===")
    results = optimizer.run_actions(candidates)
    applied = []
    for r in results:
        lines.append(
            f"PID {r.candidate.info.pid} {r.candidate.info.name}: "
            f"{r.status} ({r.detail[:60]})"
        )
        if r.status == "applied":
            applied.append(r)

    if not applied:
        lines.append("FAILED: every change was refused by the OS.")
        return "\n".join(lines)

    lines.append("")
    lines.append(f"=== STEP 5: SETTLING ({cfg.SETTLE_S}s) ===")
    time.sleep(cfg.SETTLE_S)
    after = take_measurement(bench_iters)

    lines.append(f"BEFORE: {before.line()}")
    lines.append(f"AFTER:  {after.line()}")

    ev = evaluate(before, after)
    v = verdict(ev, len(applied))

    lines.append("")
    lines.append("=== STEP 6: RESULT ===")
    lines.append(ev.summary_lines())
    lines.append(f"VERDICT: {v}")

    # Save to history
    rec = {
        "mode": "mcp",
        "scope": f"target PIDs {target_pids}" if target_pids else "auto",
        "deep": deep,
        "bottlenecks": [vars(b) for b in bottlenecks],
        "before": vars(before),
        "after": vars(after),
        "applied": len(applied),
        "verdict": v,
        "actions": [{
            "pid": r.candidate.info.pid,
            "name": r.candidate.info.name,
            "from_priority": r.candidate.current_priority,
            "to_priority": r.candidate.target_priority,
            "status": r.status,
        } for r in results],
    }
    history.record(rec)
    lines.append(f"Record saved to {history.HISTORY_FILE}")

    return "\n".join(lines)


@mcp.tool()
def get_history(limit: int = 10) -> str:
    """Show past optimization runs and their results.

    Args:
        limit: Number of history records to show (default 10)
    """
    recs = history.recent(limit)
    if not recs:
        return "No optimization history yet - run optimize_system first."

    rows = []
    for r in recs:
        ev = r.get("evaluation", {}) or {}
        gain = ev.get("bench_gain_pct")
        rows.append({
            "timestamp": r.get("timestamp", "?"),
            "mode": r.get("mode", "?"),
            "applied": r.get("applied", 0),
            "cpu_delta_pp": ev.get("cpu_delta_pp"),
            "bench_gain_pct": gain,
            "verdict": r.get("verdict", "?"),
        })
    return json.dumps(rows, indent=2)


@mcp.tool()
def restore_last() -> str:
    """Undo the last optimization run's priority changes."""
    rec = history.last_record()
    if not rec:
        return "Nothing to restore."

    targets = history.restore_targets(rec)
    if not targets:
        return "Last record has no revertible applied changes."

    lines = [f"Restoring {len(targets)} process(es) to original priority..."]
    ok = 0
    for a in targets:
        status, detail, verified = optimizer.apply_priority(
            a["pid"], a["from_priority"])
        lines.append(
            f"PID {a['pid']} {a['name'][:24]} -> "
            f"'{a['from_priority']}': {status} ({detail})"
        )
        if status == "applied":
            ok += 1

    lines.append(f"Restored {ok}/{len(targets)}.")
    return "\n".join(lines)


if __name__ == "__main__":
    mcp.run(transport="stdio")
