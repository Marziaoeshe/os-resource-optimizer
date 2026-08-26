"""Optimization history persistence (JSON-lines).

Each run appends one record.  Original priorities are stored so any change
can be reverted with `main.py restore --last`.
"""

import json
import time
from pathlib import Path
from typing import List

PROJECT_ROOT = Path(__file__).resolve().parents[1]
HISTORY_FILE = PROJECT_ROOT / "history.jsonl"


def record(rec: dict) -> None:
    rec.setdefault("timestamp", time.strftime("%Y-%m-%d %H:%M:%S"))
    with open(HISTORY_FILE, "a", encoding="utf-8") as f:
        f.write(json.dumps(rec, ensure_ascii=True, default=str) + "\n")


def recent(limit: int = 20) -> List[dict]:
    if not HISTORY_FILE.exists():
        return []
    out = []
    with open(HISTORY_FILE, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                out.append(json.loads(line))
            except json.JSONDecodeError:
                continue
    return list(reversed(out[-limit:]))


def last_record() -> dict:
    recs = recent(1)
    return recs[0] if recs else None


def restore_targets(rec: dict) -> List[dict]:
    """Extract revertible actions from a record."""
    out = []
    for a in rec.get("actions", []):
        if a.get("status") == "applied" and a.get("from_priority"):
            out.append(a)
    return out
