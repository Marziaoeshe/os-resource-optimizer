"""Optimization executor: safe, verified priority demotion.

The ONLY action this module can ever perform is lowering the scheduling
priority of an eligible process (SetPriorityClass on Windows /
setpriority(2) on POSIX).  It never terminates, suspends or signals any
process.  Every change is read back from the OS to verify it took effect.
"""

import os
from dataclasses import dataclass
from typing import List

import psutil

from .analyzer import Candidate
from .collector import IS_WINDOWS, _call, name_to_priority, priority_to_name


@dataclass
class ActionResult:
    candidate: Candidate
    status: str          # applied | access_denied | gone | error | aborted
    detail: str
    verified: bool


def apply_priority(pid: int, target: str) -> (str, str, bool):
    """Lowest-level mutation. Returns (status, detail, verified)."""
    try:
        proc = psutil.Process(pid)
        value = name_to_priority(target)
        if IS_WINDOWS:
            proc.nice(value)                    # SetPriorityClass
        else:
            os.setpriority(os.PRIO_PROCESS, pid, value)
        # ---- verification: ask the OS what it actually is now ----
        actual = priority_to_name(_call(proc.nice))
        if actual == target:
            return "applied", f"OS confirms priority is now '{actual}'", True
        return ("error",
                f"set attempted but OS reports '{actual}' (expected '{target}')",
                False)
    except psutil.AccessDenied:
        return ("access_denied",
                "Access denied by the OS - run elevated to manage this process",
                False)
    except psutil.NoSuchProcess:
        return "gone", "process exited before the change", False
    except psutil.ZombieProcess:
        return "gone", "process became a zombie", False
    except PermissionError as exc:
        return "access_denied", f"PermissionError: {exc}", False
    except Exception as exc:                     # pragma: no cover - defensive
        return "error", f"{type(exc).__name__}: {exc}", False


def run_actions(candidates: List[Candidate]) -> List[ActionResult]:
    results: List[ActionResult] = []
    for c in candidates:
        status, detail, verified = apply_priority(c.info.pid, c.target_priority)
        results.append(ActionResult(c, status, detail, verified))
    return results
