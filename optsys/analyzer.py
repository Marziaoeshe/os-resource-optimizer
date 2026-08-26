"""Analysis layer: bottleneck detection, process ranking, candidate selection.

This is the "brain" between raw OS data and the optimization actions.
Everything it outputs carries a human-readable reason so every decision can
be explained after the fact (explainable decisions requirement).
"""

import time
from dataclasses import dataclass
from typing import List, Optional, Set, Tuple

import psutil

from . import config as cfg
from .collector import ProcessInfo, SystemSample


def _self_tree_pids() -> Set[int]:
    """PIDs of this tool and all of its ancestors - never touched."""
    pids = set()
    try:
        cur = psutil.Process()
        while cur is not None:
            pids.add(cur.pid)
            cur = cur.parent()
    except Exception:
        pass
    return pids


@dataclass
class Bottleneck:
    kind: str            # 'cpu' | 'memory'
    level: str           # 'critical' | 'elevated'
    value: float         # observed percent
    threshold: float     # threshold that was crossed

    def describe(self) -> str:
        unit = {"cpu": "total CPU", "memory": "physical RAM"}[self.kind]
        return (f"{self.kind.upper()} {self.level}: {self.value:.1f}% of "
                f"{unit} (threshold {self.threshold:.0f}%)")


@dataclass
class Ranked:
    info: ProcessInfo
    score: float
    cpu_pts: float
    mem_pts: float
    thread_pts: float


@dataclass
class Candidate:
    info: ProcessInfo
    current_priority: str
    target_priority: str
    deep: bool
    score: float
    reason: str


@dataclass
class Skipped:
    info: ProcessInfo
    why: str


# ---------------------------------------------------------------------------
# Bottleneck detection
# ---------------------------------------------------------------------------
def detect_bottlenecks(sample: SystemSample) -> List[Bottleneck]:
    out: List[Bottleneck] = []
    if sample.cpu_pct >= cfg.CPU_CRITICAL_PCT:
        out.append(Bottleneck("cpu", "critical", sample.cpu_pct, cfg.CPU_CRITICAL_PCT))
    elif sample.cpu_pct >= cfg.CPU_ELEVATED_PCT:
        out.append(Bottleneck("cpu", "elevated", sample.cpu_pct, cfg.CPU_ELEVATED_PCT))
    if sample.mem_pct >= cfg.MEM_CRITICAL_PCT:
        out.append(Bottleneck("memory", "critical", sample.mem_pct, cfg.MEM_CRITICAL_PCT))
    elif sample.mem_pct >= cfg.MEM_ELEVATED_PCT:
        out.append(Bottleneck("memory", "elevated", sample.mem_pct, cfg.MEM_ELEVATED_PCT))
    return out


# ---------------------------------------------------------------------------
# Ranking
# ---------------------------------------------------------------------------
def rank_processes(infos: List[ProcessInfo]) -> List[Ranked]:
    """Weighted resource-pressure score; higher == bigger consumer.

    PID 0 ('System Idle Process') is excluded up front: on Windows its CPU
    time is the *idle* share, i.e. free capacity - not real consumption.
    """
    ranked = []
    for i in infos:
        if i.pid in cfg.PROTECTED_PIDS or i.pid == 0:
            continue
        cpu_pts = max(0.0, min(i.cpu_total, 100.0))          # % of capacity
        mem_pts = max(0.0, min(i.mem_pct, 100.0))            # % of RAM
        thr_pts = min(i.threads / cfg.THREAD_REF_COUNT, 1.0) * 100.0
        score = (cfg.W_CPU * cpu_pts + cfg.W_MEM * mem_pts +
                 cfg.W_THREADS * thr_pts)
        ranked.append(Ranked(i, score, cpu_pts, mem_pts, thr_pts))
    ranked.sort(key=lambda r: r.score, reverse=True)
    return ranked


def explain_score(r: Ranked) -> str:
    return (f"cpu={r.cpu_pts:.1f}% x{cfg.W_CPU:g} + mem={r.mem_pts:.1f}% "
            f"x{cfg.W_MEM:g} + threads={r.thread_pts:.0f}/100 x{cfg.W_THREADS:g}"
            f" => {r.score:.2f}")


# ---------------------------------------------------------------------------
# Candidate selection for safe priority demotion
# ---------------------------------------------------------------------------
def select_candidates(
    ranked: List[Ranked],
    current_user: str,
    *,
    allowed_pids: Optional[Set[int]] = None,
    name_filter: Optional[str] = None,
    deep: bool = False,
    min_cpu_total: float = cfg.MIN_CANDIDATE_CPU_TOTAL,
    max_actions: int = cfg.MAX_ACTIONS_PER_RUN,
) -> Tuple[List[Candidate], List[Skipped]]:
    """Pick processes eligible for a safe priority demotion.

    Hard safety gates, evaluated in order (first failure wins and is
    reported as a skip-reason):

      0. target/name filters (only restrict, never widen)
      1. PID 0/4 and PROTECTED_NAMES are untouchable
      2. must be owned by the account running this tool
         => SYSTEM / LOCAL SERVICE / other users are structurally excluded
      3. kernel counters must be readable (we do not act blind)
      4. alive and not stopped/zombie
      5. older than MIN_PROCESS_AGE_S (avoid transient installers/updaters)
      6. priority already low  -> nothing to demote
      7. unknown priority      -> refuse to guess
      8. must actually burn CPU (>= min_cpu_total % of total capacity)
    """
    ladder = cfg.DEEP_TARGET if deep else cfg.DEMOTE_ONE_LEVEL
    candidates: List[Candidate] = []
    skipped: List[Skipped] = []
    now = time.time()
    own_tree = _self_tree_pids()

    for r in ranked:
        i = r.info
        if allowed_pids is not None and i.pid not in allowed_pids:
            continue                                   # scope filter only
        if name_filter and name_filter.lower() not in i.name.lower():
            continue                                   # scope filter only

        def skip(why: str) -> None:
            skipped.append(Skipped(i, why))

        key = i.name.lower()
        if i.pid in own_tree:
            skip("belongs to this tool's own process tree")
            continue
        if i.pid in cfg.PROTECTED_PIDS or key in cfg.PROTECTED_NAMES:
            skip("protected system process (block list)")
            continue
        if not current_user or (i.username and i.username != current_user):
            skip(f"owned by '{i.username or '?'}' - refusing cross-account change")
            continue
        if not i.accessible:
            skip("insufficient permission to read CPU-time counters")
            continue
        if i.status in ("stopped", "zombie", "dead"):
            skip(f"status is '{i.status}'")
            continue
        if i.age_s is not None and i.age_s < cfg.MIN_PROCESS_AGE_S:
            skip(f"too young ({i.age_s:.1f}s < {cfg.MIN_PROCESS_AGE_S:.0f}s)")
            continue
        prio = i.priority_name
        if prio in ("idle", "below_normal"):
            skip(f"already low priority ('{prio}')")
            continue
        if prio not in ladder:
            skip(f"unrecognised priority '{prio}'")
            continue
        if i.cpu_total < min_cpu_total:
            skip(f"CPU {i.cpu_total:.1f}% of total < {min_cpu_total:g}% threshold")
            continue

        target = ladder[prio]
        reason = (
            f"consumes {i.cpu_total:.1f}% of total CPU ({i.cpu_one_core:.0f}% "
            f"of one core), RSS {i.rss_mb:.0f} MiB ({i.mem_pct:.1f}% of RAM), "
            f"{i.threads} threads, running at '{prio}' priority for "
            f"{i.age_s / 60.0:.1f} min; demoting one level to '{target}' "
            f"frees scheduler slices for foreground work without stopping it"
        )
        candidates.append(Candidate(i, prio, target, deep, r.score, reason))
        if len(candidates) >= max_actions:
            break

    return candidates, skipped
