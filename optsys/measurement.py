"""Before/after measurement layer.

Two independent, honest metrics are collected for every phase (before and
after optimization):

  1. System-wide CPU / RAM percentages sampled over a wall-clock window
     straight from the OS counters.

  2. A deterministic in-process benchmark: a fixed amount of pure-Python
     integer arithmetic.  Its WALL time relative to its own PROCESSOR time
     gives a "contention factor":

         contention = wall_ms / cpu_ms      (>1 means we were preempted)

     When a background hog is demoted, the OS scheduler hands our benchmark
     full slices instead of sharing them, so wall-time drops even though the
     hog keeps spinning at total-CPU level.  This measures *responsiveness*,
     which is exactly what priority optimization is supposed to improve -
     without faking any number.
"""

import statistics
import time
from dataclasses import dataclass
from typing import Optional

import psutil

from . import config as cfg


def _crunch(n: int) -> int:
    """Deterministic compute body (~no allocation, no I/O, no randomness)."""
    acc = 1
    for i in range(n):
        acc = (acc + i * 2654435761) % 2147483647
    return acc


def calibrate(target_ms: float = cfg.BENCH_TARGET_MS,
              hard_cap: int = 200_000_000) -> int:
    """Choose an iteration count so one benchmark pass ~ target_ms on THIS
    machine right now (measured, never assumed)."""
    n = 250_000
    while n < hard_cap:
        t0 = time.perf_counter()
        _crunch(n)
        ms = (time.perf_counter() - t0) * 1000.0
        if ms >= target_ms:
            return n
        grow = max(2.0, target_ms / max(ms, 0.5))
        n = min(int(n * grow) + 1, hard_cap)
    return n


@dataclass
class Measurement:
    cpu_pct: float
    mem_pct: float
    swap_pct: float
    bench_ms: Optional[float]
    bench_iters: Optional[int]
    contention: Optional[float]      # wall/cpu; None when benchmark skipped

    def line(self) -> str:
        base = (f"CPU {self.cpu_pct:5.1f}% | RAM {self.mem_pct:5.1f}% | "
                f"swap {self.swap_pct:4.1f}%")
        if self.bench_ms is not None:
            base += (f" | benchmark {self.bench_ms:8.1f} ms | "
                     f"contention {self.contention:.2f}x")
        else:
            base += " | benchmark skipped"
        return base


def take_measurement(bench_iters: Optional[int],
                     window_s: float = cfg.SAMPLE_WINDOW_S,
                     step_s: float = cfg.SAMPLE_STEP_S,
                     trials: int = cfg.BENCH_TRIALS) -> Measurement:
    """Sample system counters for window_s seconds, then run the benchmark."""
    samples = []
    deadline = time.time() + window_s
    while True:
        samples.append(psutil.cpu_percent(step_s))
        if time.time() >= deadline:
            break
    cpu = sum(samples) / len(samples)

    vm = psutil.virtual_memory()
    sm = psutil.swap_memory()

    bench_ms = contention = None
    if bench_iters:
        walls, cputimes = [], []
        for _ in range(max(1, trials)):
            t0 = time.perf_counter()
            c0 = time.process_time()
            _crunch(bench_iters)
            c1 = time.process_time()
            t1 = time.perf_counter()
            walls.append((t1 - t0) * 1000.0)
            cputimes.append((c1 - c0) * 1000.0)
        # Best-of-N (minimum) wall time: the least-interfered pass is the
        # most reproducible estimate of achievable latency; medians are
        # easily skewed by unrelated background spikes.
        bench_ms = min(walls)
        cpu_ms = max(min(cputimes), 1e-9)
        contention = bench_ms / cpu_ms

    return Measurement(cpu_pct=cpu, mem_pct=vm.percent, swap_pct=sm.percent,
                       bench_ms=bench_ms, bench_iters=bench_iters,
                       contention=contention)


@dataclass
class Evaluation:
    cpu_delta_pp: float              # before - after, positive == less load
    mem_delta_pp: float
    bench_gain_pct: Optional[float]  # positive == faster after optimization
    contention_gain: Optional[float]
    throughput_before: Optional[float]  # iters/ms equivalent (relative units/s)
    throughput_after: Optional[float]

    def summary_lines(self) -> str:
        out = []
        out.append(f"CPU change          : {self.cpu_delta_pp:+.1f} pp "
                   f"(positive = system CPU decreased)")
        out.append(f"RAM change          : {self.mem_delta_pp:+.1f} pp")
        if self.bench_gain_pct is not None:
            out.append(f"Benchmark latency   : improved {self.bench_gain_pct:.2f}%"
                       if self.bench_gain_pct >= 0 else
                       f"Benchmark latency   : regressed {-self.bench_gain_pct:.2f}%")
            out.append(f"Contention factor   : {self.contention_gain:+.2f}x "
                       f"(lower is better; 1.00x = never preempted)")
            out.append(f"Benchmark throughput: x{self.throughput_after / self.throughput_before:.3f}"
                       if self.throughput_before else "")
        return "\n".join(l for l in out if l)


def evaluate(before: Measurement, after: Measurement) -> Evaluation:
    gain = None
    cont_gain = None
    tp_b = tp_a = None
    if before.bench_ms and after.bench_ms and before.bench_iters:
        gain = (before.bench_ms - after.bench_ms) / before.bench_ms * 100.0
        cont_gain = before.contention - after.contention
        # iterations per second is identical work; compare effective rate
        tp_b = before.bench_iters / before.bench_ms
        tp_a = after.bench_iters / after.bench_ms
    return Evaluation(
        cpu_delta_pp=before.cpu_pct - after.cpu_pct,
        mem_delta_pp=before.mem_pct - after.mem_pct,
        bench_gain_pct=gain,
        contention_gain=cont_gain,
        throughput_before=tp_b,
        throughput_after=tp_a,
    )


def verdict(ev: Evaluation, actions_applied: int) -> str:
    if actions_applied == 0:
        return "NO-OP: no eligible process was changed, nothing to measure."
    if ev.bench_gain_pct is None:
        return ("INCONCLUSIVE: benchmark disabled; CPU delta alone cannot "
                "prove a scheduling improvement.")
    if ev.bench_gain_pct >= cfg.IMPROVE_SUCCESS_PCT:
        return (f"SUCCESS: foreground benchmark ran {ev.bench_gain_pct:.2f}% "
                f"faster after optimization (measured, not estimated).")
    if ev.bench_gain_pct > 0:
        return ("MARGINAL: small measured improvement within noise band "
                f"(<{cfg.IMPROVE_SUCCESS_PCT:g}%).")
    return ("REGRESSION: after-measurement was slower; system variance or "
            "competing load. No success is claimed.")


__all__ = ["Measurement", "Evaluation", "calibrate", "take_measurement",
           "evaluate", "verdict"]
