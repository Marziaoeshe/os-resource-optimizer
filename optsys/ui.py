"""Minimal ANSI CLI rendering helpers (tables, bars, colors, prompts)."""

import os
import sys

if os.name == "nt":
    os.system("")          # enables ANSI escape processing on conhost/PS 5.1

_USE_COLOR = sys.stdout.isatty() and os.environ.get("NO_COLOR") is None


class C:
    RESET = "\033[0m" if _USE_COLOR else ""
    BOLD = "\033[1m" if _USE_COLOR else ""
    DIM = "\033[2m" if _USE_COLOR else ""
    GREEN = "\033[32m" if _USE_COLOR else ""
    YELLOW = "\033[33m" if _USE_COLOR else ""
    RED = "\033[31m" if _USE_COLOR else ""
    CYAN = "\033[36m" if _USE_COLOR else ""


# convenience aliases
RESET, BOLD, DIM = C.RESET, C.BOLD, C.DIM
GREEN, YELLOW, RED, CYAN = C.GREEN, C.YELLOW, C.RED, C.CYAN


def paint(text: str, color: str) -> str:
    return f"{color}{text}{C.RESET}" if color and _USE_COLOR else text


def clear() -> None:
    os.system("cls" if os.name == "nt" else "clear")


def hr(ch: str = "-", width: int = 100) -> str:
    return ch * width


def title(text: str) -> str:
    return f"=== {text} " + "=" * max(4, 100 - len(text) - 4)


def bar(pct: float, width: int = 24) -> str:
    pct = max(0.0, min(100.0, pct))
    filled = int(round(pct / 100.0 * width))
    return "[" + "#" * filled + "." * (width - filled) + f"] {pct:5.1f}%"


def table(headers, rows, aligns=None) -> str:
    """Plain-ASCII fixed-width table."""
    if aligns is None:
        aligns = ["<"] * len(headers)
    rows = [[("" if v is None else str(v)) for v in r] for r in rows]
    widths = [len(str(h)) for h in headers]
    for r in rows:
        for j, v in enumerate(r):
            widths[j] = min(max(widths[j], len(v)), 60)
    out = []
    hdr = " | ".join(f"{str(h):{aligns[j]}{widths[j]}}"
                     for j, h in enumerate(headers))
    out.append(hdr)
    out.append("-+-".join("-" * w for w in widths))
    for r in rows:
        out.append(" | ".join(f"{v:{aligns[j]}{widths[j]}}"
                              for j, v in enumerate(r)))
    return "\n".join(out)


def confirm(prompt: str) -> bool:
    while True:
        try:
            ans = input(f"{prompt} [y/N]: ").strip().lower()
        except (EOFError, KeyboardInterrupt):
            return False
        if ans in ("y", "yes"):
            return True
        if ans in ("n", "no", ""):
            return False
        print("Please answer y or n.")


def fmt_age(seconds):
    if seconds is None:
        return "?"
    m, s = divmod(int(seconds), 60)
    h, m = divmod(m, 60)
    if h >= 24:
        d, h = divmod(h, 24)
        return f"{d}d{h}h"
    if h:
        return f"{h}h{m:02d}m"
    if m:
        return f"{m}m{s:02d}s"
    return f"{s}s"
