# Intelligent OS Resource Optimization System

[![Python Version](https://img.shields.io/badge/Python-3.8%2B-brightgreen.svg?style=flat-square&logo=python)](https://python.org)
[![Platform](https://img.shields.io/badge/Platform-Windows%20%7C%20Linux-lightgrey.svg?style=flat-square)]()
[![psutil](https://img.shields.io/badge/psutil-5.9%2B-blue.svg?style=flat-square)](https://github.com/giampaolo/psutil)
[![License](https://img.shields.io/badge/License-Academic%20Use-orange.svg?style=flat-square)]()

A system-level tool that watches **real** operating-system data, detects CPU/RAM bottlenecks, ranks the processes causing them, performs a **safe, reversible, priority-based optimization**, and then **measures** whether performance actually improved. Nothing is simulated: every number was produced by the OS itself during live runs on Windows 11.

---

## Key Highlights

- **Real OS Telemetry**: Reads live process data via `psutil` (wrapping Win32/POSIX APIs) — no synthetic or mocked data.
- **Bottleneck Detection**: CPU critical ≥ 85%, Memory critical ≥ 88% thresholds with per-core pinned-core detection.
- **Weighted Process Ranking**: Multi-factor scoring (CPU 60% + Memory 25% + Threads 15%) to identify top pressure sources.
- **Safe Priority Demotion**: Only action is `SetPriorityClass` one level down — never kills, suspends, or signals processes.
- **Measured Improvement**: Calibrated benchmark runs before/after with contention factor analysis — not estimated, measured.
- **Full Reversibility**: Every change is recorded and can be undone with `restore --last`.
- **Interactive GUI Dashboard**: Real-time CPU/Memory graphs, process table, and auto-optimization mode (Tkinter).
- **OpenCode Integration**: CLI wrapper for AI-assisted system analysis.

---

## Project Video Presentation & Live Demonstration

> **Faculty & Reviewer Quick Link:** Click the link below to watch the complete video presentation and live system demonstration.

### Direct Video Link

👉 **[Click Here to Watch the Full Video](assets/demo.mp4)** or [Open on Google Drive](https://drive.google.com/file/d/1nddnfIjiUGCLbPFMxf4oZDGUnkt-RIyO/view?usp=drive_link)

---

## System Architecture

```
┌─────────────────────────────────────────────────────────────────────┐
│                         Operating System Kernel                     │
│          Windows (Win32 API)            │    Linux (/proc, syscalls)│
└─────────────────────────────────────────┬───────────────────────────┘
                                          │ Raw OS Telemetry (psutil)
                                          ▼
                          ┌───────────────────────────────┐
                          │       Collector Module         │
                          │  • Per-PID CPU-time Deltas    │
                          │  • RSS Memory Sampling        │
                          │  • Thread Count & Priority    │
                          └───────────────┬───────────────┘
                                          │ ProcessInfo Records
                                          ▼
                          ┌───────────────────────────────┐
                          │       Analyzer Module          │
                          │  • Bottleneck Detection       │
                          │  • Weighted Ranking Score     │
                          │  • Candidate Selection        │
                          │  • Safety Gate Filtering      │
                          └───────────────┬───────────────┘
                                          │ Ranked Candidates
                                          ▼
                          ┌───────────────────────────────┐
                          │      Optimizer Module          │
                          │  • SetPriorityClass (Win32)   │
                          │  • setpriority(2) (Linux)     │
                          │  • OS Verification Read-back  │
                          └───────────────┬───────────────┘
                                          │
                  ┌───────────────────────┴───────────────────────┐
                  ▼                                               ▼
     ┌─────────────────────────┐                 ┌─────────────────────────┐
     │     CLI Dashboard       │                 │      GUI Dashboard      │
     │  • ASCII Tables & Bars  │                 │  • Live CPU/RAM Graphs  │
     │  • ANSI Color Output    │                 │  • Process Table        │
     │  • Batch Script Support │                 │  • Auto-Optimization    │
     └─────────────────────────┘                 └─────────────────────────┘
```

---

## Project Structure

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
├── tools/
│   └── loadgen.py          real multi-process CPU load generator (test only)
├── opencode_tool.py        OpenCode CLI wrapper (JSON output)
├── requirements.txt        psutil only
└── README.md
```

---

## Quickstart

### 1. Installation

```powershell
pip install -r requirements.txt        # psutil only
```

### 2. Launching the CLI

```powershell
python main.py dashboard               # live read-only dashboard
python main.py processes --sort cpu    # top consumers table
python main.py analyze                 # bottleneck detection + ranking + preview
python main.py optimize                # full pipeline (asks for confirmation)
python main.py history                 # past runs + measured improvements
python main.py restore --last          # undo the last run's priority changes
```

### 3. Launching the GUI

```powershell
python main.py gui            # tkinter window, live every 2 s
```

### 4. Reproducing the Demo (real workload, real measurement)

```powershell
# terminal 1 – create a genuine CPU saturation with 8 real spinning processes:
python tools/loadgen.py --cpus 8 --duration 300 --out pids.json

# terminal 2 – point the optimizer at those exact PIDs (or omit --target for auto mode):
python main.py optimize --target 62648 --target 28240 ... --yes
```

---

## Verified End-to-End Run (Actual Output)

Machine: `DESKTOP-LLC7PEI`, Windows 11 (build 26100), 8 logical / 4 physical cores @ 2803 MHz, 16 GB RAM, Python 3.13.7, psutil 7.2.2. Load: 8 real `python.exe` spinner processes.

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

---

## Safety Gates Verified

| Target Attempted       | Gate That Stopped It                        | Result              |
|------------------------|---------------------------------------------|---------------------|
| `explorer.exe`         | this tool's own ancestor tree (+ block list)| skipped             |
| `audiodg.exe` (SYSTEM) | name block list / cross-account ownership   | skipped             |
| full run revert        | `restore --last`                            | 8/8 restored, verified |

---

## OS APIs Used (No Simulation)

All collection goes through **psutil ≥ 5.9**, a thin wrapper over platform-native facilities:

| Data                          | Windows API behind psutil                     | Linux equivalent                  |
|-------------------------------|-----------------------------------------------|-----------------------------------|
| process list / PIDs           | `EnumProcesses`, `OpenProcess`                | `/proc` enumeration               |
| per-process CPU time          | `GetProcessTimes`                             | `/proc/<pid>/stat`                |
| per-process memory (RSS)      | `GetProcessMemoryInfo`                        | `/proc/<pid>/statm`               |
| thread count                  | `GetProcessInformation` / TOOLHELP snapshot   | `/proc/<pid>/status`              |
| current priority              | `GetPriorityClass` / `GetThreadPriority`      | `getpriority(2)`                  |
| **change priority (optimize)**| **`SetPriorityClass`**                        | **`setpriority(2)`**              |
| total/system CPU %            | `GetSystemTimes`                              | `/proc/stat`                      |
| RAM / swap                    | `GlobalMemoryStatusEx`                        | `/proc/meminfo`                   |

---

## Optimization Scoring Weights

The Analyzer ranks processes using a weighted multi-criteria scoring model:

```
Score = 0.60 × min(cpu_%_of_total_capacity, 100)
      + 0.25 × min(ram_%_used, 100)
      + 0.15 × min(threads / 128, 1) × 100
```

| Metric           | Direction      | Default Weight | Optimization Rationale                          |
|------------------|----------------|----------------|--------------------------------------------------|
| **CPU Usage**    | Lower is better| **60%**        | Primary bottleneck indicator                     |
| **Memory Usage** | Lower is better| **25%**        | Secondary pressure source                        |
| **Thread Count** | Lower is better| **15%**        | Kernel scheduling overhead proxy                 |

---

## Limitations

- Without admin rights only same-user processes are manageable (by design); elevated runs still respect the block list.
- Priority demotion cannot reduce total utilization of a purely CPU-bound hog — the measured benefit appears in foreground latency/contention.
- Memory-pressure bottlenecks are detected but deliberately not "optimized" by killing anything; they are reported with top RAM contributors instead.

---

## License & Course Information

Developed as an academic Operating Systems course project. Designed for educational, research, and benchmarking use.
