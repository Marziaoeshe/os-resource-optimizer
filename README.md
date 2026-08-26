# Intelligent OS Resource Optimization System

A system-level tool that watches **real** operating-system data, detects CPU/RAM
bottlenecks, ranks the processes causing them, performs a **safe, reversible,
priority-based optimization**, and then **measures** whether performance actually
improved. Nothing is simulated: every number in this document was produced by
the OS itself during live runs on Windows 11.

```
Real OS data ──► Process analysis ──► Bottleneck detection ──► Optimization algorithm
      ▲                                                        │
Measured improvement ◄── Before/after measurement ◄── Safe optimization (user-confirmed)
```

* Language: **Python 3.8+** (chosen over C++ for safe, rapid access to OS APIs;
  all heavy lifting is done by `psutil`, which wraps native Win32/POSIX calls)
* GUI: none — CLI dashboard, as requested ("system-level approach")

---

## 1. Quick start

```powershell
pip install -r requirements.txt        # psutil only

python main.py dashboard               # live read-only dashboard
python main.py processes --sort cpu    # top consumers table
python main.py analyze                 # bottleneck detection + ranking + preview
python main.py optimize                # full pipeline (asks for confirmation)
python main.py history                 # past runs + measured improvements
python main.py restore --last          # undo the last run's priority changes
```

### Reproducing the demo (real workload, real measurement)

```powershell
# terminal 1 – create a genuine CPU saturation with 8 real spinning processes:
python tools/loadgen.py --cpus 8 --duration 300 --out pids.json

# terminal 2 – point the optimizer at those exact PIDs (or omit --target for auto mode):
python main.py optimize --target 62648 --target 28240 ... --yes
```

`loadgen.py` is only a *test harness that creates real pressure*; the optimizer
never talks to it and never trusts it — it observes the workers through the
OS exactly like any other process.

---

## 1b. Real-time GUI dashboard

```powershell
python main.py gui            # tkinter window, live every 2 s
```

* **CPU / Memory graphs** – line graphs drawn only from samples measured while
  the program runs (`GetSystemTimes` deltas); ~5-minute sliding window.
* **Process table** – PID | Name | CPU % | Memory % | Priority | Owner,
  refreshed each tick, sortable Top-CPU / Top-RAM; hot rows highlighted.
* **Optimization panel** – CPU/Memory status (HIGH/NORMAL), the actual
  detected bottleneck process, the planned safe action, and the measured
  Before / After lines with the improvement percentage of the last run.
* **Auto Optimization ON/OFF** – when ON, a CPU-critical bottleneck triggers
  the same pipeline as the CLI (select candidates -> demote one level ->
  verify -> settle -> re-measure -> history) without a confirmation prompt;
  OFF requires pressing *Optimize Now*. Collection is paused during runs so
  the dashboard's own sampler cannot distort the measurement. Cooldown:
  `AUTO_COOLDOWN_S` (120 s). Never kills processes; system/other-user
  processes remain protected by the same gates as everywhere else.

---

## 2. Verified end-to-end run (actual output, actual numbers)

Machine: `DESKTOP-LLC7PEI`, Windows 11 (build 26100), 8 logical / 4 physical
cores @ 2803 MHz, 16 GB RAM, Python 3.13.7, psutil 7.2.2.
Load: 8 real `python.exe` spinner processes (`loadgen.py --cpus 8`).

```text
STEP 1/6  BEFORE MEASUREMENT
  CPU 100.0% | RAM 65.1% | swap 8.4% | benchmark    398.7 ms | contention 1.50x

STEP 2/6  LIVE DATA COLLECTION & BOTTLENECK DETECTION
  !! CPU critical: 100.0% of total CPU (threshold 85%)

STEP 3/6  OPTIMIZATION PLAN   (8 candidates, e.g.)
  Decision [1] PID 70112 python.exe:
      consumes 38.8% of total CPU (100% of one core), RSS 16 MiB (0.1% of RAM),
      4 threads, running at 'normal' priority for 0.2 min; demoting one level
      to 'below_normal' ...

STEP 4/6  APPLYING SAFE PRIORITY CHANGES
70112 | python.exe | normal -> below_normal | applied | OS confirms priority is now 'below_normal'
(... all 8 rows: applied, verified by read-back from the OS)

STEP 5/6  SETTLING (2s) THEN AFTER MEASUREMENT
  BEFORE: CPU 100.0% | RAM 65.1% | benchmark    398.7 ms | contention 1.50x
  AFTER : CPU 100.0% | RAM 65.4% | benchmark    274.4 ms | contention 1.03x

STEP 6/6  MEASURED RESULT
Benchmark latency   : improved 31.17%
Contention factor   : +0.47x (lower is better; 1.00x = never preempted)
Benchmark throughput: x1.453
VERDICT: SUCCESS: foreground benchmark ran 31.17% faster after optimization
(measured, not estimated).
```

### Why CPU% stayed at 100 % while the run still counts as SUCCESS (honesty note)

The spinners keep spinning after demotion, so total utilization stays ~100 %
and the tool reports `CPU change +0.0 pp` instead of pretending otherwise.
Priority optimization does not create free CPU; it changes **who gets it**.
That effect is measured directly: a fixed deterministic benchmark ran inside
the tool before and after, and its wall-time dropped 398.7 → 274.4 ms because
the scheduler stopped splitting its slices with normal-priority hogs
(contention factor 1.50x → 1.03x). When a system has idle capacity, or when
demoted processes yield, the CPU%-delta drops too and is reported alongside.

### Safety gates verified live

| Target attempted            | Gate that stopped it                        | Result |
|-----------------------------|---------------------------------------------|--------|
| `explorer.exe`              | this tool's own ancestor tree (+ block list)| skipped |
| `audiodg.exe` (SYSTEM)      | name block list / cross-account ownership   | skipped |
| full run revert             | `restore --last`                            | 8/8 restored, OS-verified |

History record written to `history.jsonl` (timestamp, scope, bottlenecks,
before/after metrics, per-action status, verdict) — see `python main.py history`.

---

## 3. OS APIs used (no simulation anywhere)

All collection goes through **psutil ≥ 5.9**, which is a thin wrapper over the
platform's native facilities:

| Data                          | Windows API behind psutil                     | Linux equivalent                  |
|-------------------------------|-----------------------------------------------|-----------------------------------|
| process list / PIDs           | `EnumProcesses`, `OpenProcess`                | `/proc` enumeration, `readdir`    |
| per-process CPU time          | `GetProcessTimes`                             | `/proc/<pid>/stat`                |
| per-process memory (RSS)      | `GetProcessMemoryInfo`                        | `/proc/<pid>/statm`, `/status`    |
| thread count                  | `GetProcessInformation` / TOOLHELP snapshot   | `/proc/<pid>/status` (Threads)    |
| creation time / age           | `GetProcessTimes`                             | `/proc/<pid>/stat` starttime      |
| owner / username              | `OpenProcessToken`, `GetTokenInformation`     | `/proc/<pid>/status` (Uid)        |
| current priority              | `GetPriorityClass` / `GetThreadPriority`      | `getpriority(2)`                  |
| **change priority (optimize)**| **`SetPriorityClass`** (`BELOW_NORMAL_PRIORITY_CLASS`) | **`setpriority(2)`**     |
| total/system CPU %            | `GetSystemTimes`                              | `/proc/stat`                      |
| per-core CPU %                | `GetSystemTimes` per logical processor        | `/proc/stat` per cpuN             |
| RAM / swap                    | `GlobalMemoryStatusEx`                        | `/proc/meminfo`, `sysconf`        |
| CPU frequency / topology      | `CallNtPowerInformation`, CPU sets             | `cpufreq`, `/proc/cpuinfo`        |
| boot time / uptime            | `GetSystemTimeAsFileTime` deltas              | `/proc/stat` btime                |

Permission errors (`AccessDenied`) are surfaced as data-quality flags
(`accessible=False`, priority shown as `?`) — never replaced with guesses.

---

## 4. Architecture

```
os-resource-optimizer/
├── main.py                 CLI: dashboard / processes / analyze / optimize /
│                           history / restore (argparse subcommands)
├── optsys/
│   ├── config.py           thresholds, weights, demotion ladder, block lists
│   ├── collector.py        OS-level data collection (per-PID CPU-time deltas)
│   ├── analyzer.py         bottleneck detection, weighted ranking, candidate
│   │                       selection with hard safety gates + skip reasons
│   ├── optimizer.py        applies SetPriorityClass/setpriority + verifies
│   ├── measurement.py      calibrated deterministic benchmark, contention
│   │                       factor, before/after evaluation, honest verdicts
│   ├── history.py          JSONL persistence + revertible-action extraction
│   └── ui.py               ASCII tables, bars, ANSI colors, confirm prompt
└── tools/loadgen.py        real multi-process CPU load generator (test only)
```

Collector detail: `psutil.Process.cpu_percent()` loses state between freshly
created objects, so `collector.Collector` keeps its own `pid -> (cpu_seconds,
timestamp)` cache and computes usage as `(Δcpu_time / Δwall) × 100` per core,
then divides by logical-core count for `% of total capacity`. First sweep is a
priming pass; every later sweep yields true interval deltas.

---

## 5. Analysis methodology

* **Bottleneck detection** (configurable in `config.py`)
  * CPU: `critical ≥ 85 %` of total capacity, `elevated ≥ 65 %` (averaged over
    a 3 s window); pinned cores listed individually.
  * Memory: `critical ≥ 88 %`, `elevated ≥ 72 %` physical RAM.
* **Ranking score** (higher = bigger pressure source):

  ```
  score = 0.60 · min(cpu_%_of_total_capacity, 100)
        + 0.25 · min(ram_%_used, 100)
        + 0.15 · min(threads / 128, 1) · 100
  ```

  Example breakdown printed by `analyze`:
  `cpu=38.8% x0.6 + mem=0.1% x0.25 + threads=3/100 x0.15 => 23.8`

---

## 6. Optimization algorithm (safe priority demotion)

Only one action exists in the entire system: **lowering an eligible process's
scheduling priority by one level** (`SetPriorityClass` on Windows,
`setpriority(2)` elsewhere). Processes are never killed, suspended, or
signalled. Demotion ladder: `realtime→high→above_normal→normal→below_normal`;
`--deep` jumps to `idle` (realtime capped at `below_normal`).

Eligibility gates (all must pass; first failure is reported verbatim):

1. not PID 0/4, name not in `PROTECTED_NAMES` (smss/csrss/lsass/services/
   svchost/dwm/explorer/Defender/shells/IDEs…)
2. owned by the account running the tool → SYSTEM / other users structurally
   unreachable without elevation
3. kernel counters readable (we refuse to act blind)
4. alive, not stopped/zombie
5. age > 5 s (skips transient installers/updaters)
6. current priority not already low
7. actually consuming ≥ 5 % of total CPU capacity
8. not part of this tool's own process tree
9. at most `MAX_ACTIONS_PER_RUN = 8` changes per run
10. explicit user confirmation (`--yes` overrides the prompt only)

Every applied change is **read back from the OS** and recorded with
`from_priority` so `restore --last` can revert it.

---

## 7. Measurement methodology

1. A tiny integer-arithmetic loop is calibrated against the machine (~350 ms
   per pass) — no fixed magic numbers.
2. Before phase: sample OS CPU/RAM counters for 3 s, then run the benchmark
   3× (median kept), computing `contention = wall_ms / cpu_ms`.
3. Apply confirmed changes; wait 2 s settle.
4. After phase: identical procedure.
5. Improvement:
   * benchmark gain % = (before − after) / before × 100
   * throughput ratio, contention delta, CPU/RAM percentage-point deltas
6. Verdict rules (never fabricates success):
   * no actions → `NO-OP`; actions refused → `FAILED`
   * gain ≥ 5 % → `SUCCESS`; 0–5 % → `MARGINAL`; < 0 → `REGRESSION`

## 8. Limitations

* Without admin rights only same-user processes are manageable (by design);
  elevated runs still respect the block list.
* Priority demotion cannot reduce total utilization of a purely CPU-bound
  hog — the measured benefit appears in foreground latency/contention (§2).
* Memory-pressure bottlenecks are detected but deliberately not "optimized"
  by killing anything; they are reported with top RAM contributors instead.

## 9. Requirements

* Python 3.8+, `psutil>=5.9` (`pip install -r requirements.txt`)
* Windows 10/11 (primary, tested on Windows 11 build 26100) or Linux
