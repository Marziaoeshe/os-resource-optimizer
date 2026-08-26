#!/usr/bin/env python3
"""Real CPU load generator (test harness - NOT part of the optimizer data).

This tool creates GENUINE CPU pressure by running real spinning processes.
The optimizer then observes them through the OS exactly like any other
workload: every number the optimizer reports about these workers comes from
the operating system itself.  No fake PIDs, no random values.

Usage:
  python tools/loadgen.py --cpus 7 --duration 300 [--out pids.json]
"""

import argparse
import json
import multiprocessing as mp
import os
import sys
import time


def _spin(deadline: float) -> None:
    """Burn CPU with cheap integer math until 'deadline' (wall clock)."""
    x = 1
    i = 2
    while time.time() < deadline:
        x = (x * i + i) % 99991
        i = (i + 1) % 1000003


def main() -> int:
    ap = argparse.ArgumentParser(description="real CPU load generator")
    ap.add_argument("--cpus", type=int,
                    default=max(1, (os.cpu_count() or 2) - 1))
    ap.add_argument("--duration", type=float, default=180.0)
    ap.add_argument("--out", default="loadgen.pids.json")
    args = ap.parse_args()

    deadline = time.time() + args.duration
    workers = []
    for _ in range(max(1, args.cpus)):
        p = mp.Process(target=_spin, args=(deadline,), daemon=True)
        p.start()
        workers.append(p)

    payload = {
        "started": time.strftime("%Y-%m-%d %H:%M:%S"),
        "duration_s": args.duration,
        "cpus": args.cpus,
        "launcher_pid": os.getpid(),
        "workers": [p.pid for p in workers],
    }
    try:
        with open(args.out, "w") as f:
            json.dump(payload, f, indent=2)
    except OSError as exc:
        print(f"warn: could not write {args.out}: {exc}", file=sys.stderr)

    print(json.dumps(payload), flush=True)
    try:
        for p in workers:
            p.join()
    except KeyboardInterrupt:
        pass
    finally:
        for p in workers:
            if p.is_alive():
                p.terminate()
        for p in workers:
            p.join(timeout=2)
        try:
            os.remove(args.out)
        except OSError:
            pass
    return 0


if __name__ == "__main__":
    sys.exit(main())
