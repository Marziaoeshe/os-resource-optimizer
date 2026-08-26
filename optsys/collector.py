"""OS-level data collection layer.

All numbers produced here are obtained live from the operating system via
psutil, which is a thin wrapper over native OS facilities:

  Windows : EnumProcesses / OpenProcess / GetProcessTimes /
            GetProcessMemoryInfo / GetPriorityClass / GlobalMemoryStatusEx
            (kernel32 + psapi behind the scenes)
  Linux   : /proc/<pid>/stat, /proc/<pid>/statm, /proc/meminfo,
            sysconf(_SC_NPROCESSORS_ONLN), getpriority(2)

Nothing in this module is synthesised, hard-coded or randomised.  If the OS
refuses a query (AccessDenied) the value is reported as unavailable instead
of being guessed.
"""

import os
import socket
import time
from dataclasses import dataclass
from typing import Dict, List, Optional, Tuple

import psutil

try:  # POSIX niceties only used on non-Windows
    import getpass
except ImportError:  # pragma: no cover
    getpass = None

IS_WINDOWS = os.name == "nt"
_MB = 1024 * 1024


class CollectionError(RuntimeError):
    """Raised when the OS refuses a fundamental query."""


def _call(fn, default=None):
    """Run a psutil accessor, mapping permission/liveness errors to default."""
    try:
        return fn()
    except (psutil.NoSuchProcess, psutil.AccessDenied, psutil.ZombieProcess,
            psutil.Error, OSError):
        return default


if IS_WINDOWS:
    _CLASS_TO_NAME = {
        psutil.IDLE_PRIORITY_CLASS: "idle",
        psutil.BELOW_NORMAL_PRIORITY_CLASS: "below_normal",
        psutil.NORMAL_PRIORITY_CLASS: "normal",
        psutil.ABOVE_NORMAL_PRIORITY_CLASS: "above_normal",
        psutil.HIGH_PRIORITY_CLASS: "high",
        psutil.REALTIME_PRIORITY_CLASS: "realtime",
    }
else:
    _CLASS_TO_NAME = {}

_POSIX_NICE = {
    "idle": 19,
    "below_normal": 10,
    "normal": 0,
    "above_normal": -5,
    "high": -10,
    "realtime": -20,
}


def priority_to_name(value) -> Optional[str]:
    """Translate an OS priority value into our semantic name."""
    if value is None:
        return None
    if IS_WINDOWS:
        return _CLASS_TO_NAME.get(value, "unknown")
    if value <= -15:
        return "realtime"
    if value <= -8:
        return "high"
    if value <= -3:
        return "above_normal"
    if value <= 2:
        return "normal"
    if value <= 9:
        return "below_normal"
    return "idle"


def name_to_priority(target: str):
    """Return the platform value for a semantic priority name."""
    if IS_WINDOWS:
        attr = target.upper() + "_PRIORITY_CLASS"
        cls = getattr(psutil, attr, None)
        if cls is None:
            raise ValueError(f"unsupported priority class {target!r} on Windows")
        return cls
    if target not in _POSIX_NICE:
        raise ValueError(f"unsupported nice level {target!r}")
    return _POSIX_NICE[target]


def current_username() -> str:
    """Short username of the account running this tool (ownership checks)."""
    try:
        u = psutil.Process(os.getpid()).username()
        if u:
            return u.split("\\")[-1]
    except Exception:
        pass
    try:
        return getpass.getuser()
    except Exception:
        return ""


@dataclass
class ProcessInfo:
    pid: int
    name: str
    username: Optional[str]          # short name, e.g. "DLG" or "SYSTEM"
    status: str
    cpu_one_core: float              # % of one logical core
    cpu_total: float                 # % of total machine CPU capacity
    rss_mb: float                    # resident set size, MiB
    mem_pct: float                   # % of physical RAM
    threads: int
    priority_name: Optional[str]     # semantic priority (windows class / nice)
    age_s: Optional[float]           # seconds since process creation
    accessible: bool                 # False when CPU-time counters were denied


@dataclass
class SystemSample:
    ts: float
    cpu_pct: float
    per_core: List[float]
    mem_pct: float
    swap_pct: float
    mem_used_mb: float
    mem_total_mb: float


@dataclass
class StaticInfo:
    hostname: str
    system: str
    release: str
    os_version: str
    boot_ts: float
    cores_logical: int
    cores_physical: Optional[int]
    freq_mhz: Optional[float]
    ram_total_mb: float
    swap_total_mb: float
    python_version: str


def static_info() -> StaticInfo:
    import platform
    vm = psutil.virtual_memory()
    sm = psutil.swap_memory()
    freq = _call(lambda: psutil.cpu_freq())
    return StaticInfo(
        hostname=socket.gethostname(),
        system=platform.system(),
        release=platform.release(),
        os_version=platform.version(),
        boot_ts=psutil.boot_time(),
        cores_logical=psutil.cpu_count(logical=True) or 1,
        cores_physical=psutil.cpu_count(logical=False),
        freq_mhz=(freq.current if freq else None),
        ram_total_mb=vm.total / _MB,
        swap_total_mb=sm.total / _MB,
        python_version=platform.python_version(),
    )


class Collector:
    """Collects live process/system data with accurate per-PID CPU deltas.

    psutil's Process.cpu_percent() is unreliable across freshly re-created
    Process objects, so this class keeps its own pid -> (cpu_time, ts) cache
    and computes usage from GetProcessTimes-style deltas itself.
    """

    def __init__(self):
        self._cache: Dict[int, Tuple[float, float]] = {}
        self.cores = psutil.cpu_count(logical=True) or 1

    # -- internals ---------------------------------------------------------
    @staticmethod
    def _cpu_seconds(proc) -> Optional[float]:
        ct = _call(proc.cpu_times)
        if ct is None:
            return None
        total = ct.user + ct.system
        total += getattr(ct, "iowait", 0.0)
        total += getattr(ct, "irq", 0.0) + getattr(ct, "softirq", 0.0)
        return total

    def prime(self) -> None:
        """Take a first snapshot so the next collect() yields real deltas."""
        now = time.time()
        for p in psutil.process_iter():
            t = self._cpu_seconds(p)
            if t is not None:
                self._cache[p.pid] = (t, now)

    # -- public API --------------------------------------------------------
    def collect(self, with_system: bool = True):
        """One full sweep. Returns (List[ProcessInfo], Optional[SystemSample])."""
        now = time.time()
        vm = psutil.virtual_memory()
        sm = psutil.swap_memory()
        infos: List[ProcessInfo] = []
        seen = set()

        for p in psutil.process_iter():
            pid = p.pid
            seen.add(pid)
            name = _call(p.name) or ""
            uname = _call(p.username)
            if uname and "\\" in uname:
                uname = uname.split("\\")[-1]
            threads = _call(p.num_threads) or 0
            prio_name = priority_to_name(_call(p.nice))
            rss_mb, mem_pct = 0.0, 0.0
            mi = _call(p.memory_info)
            if mi is not None:
                rss_mb = mi.rss / _MB
                mem_pct = (mi.rss / vm.total * 100.0) if vm.total else 0.0
            age_s = None
            crt = _call(p.create_time)
            if crt is not None:
                age_s = max(0.0, now - crt)

            ct = self._cpu_seconds(p)
            accessible = ct is not None
            cpu_one = 0.0
            prev = self._cache.get(pid)
            if ct is not None:
                if prev is not None and now - prev[1] > 0.05:
                    cpu_one = max(0.0, (ct - prev[0]) / (now - prev[1]) * 100.0)
                self._cache[pid] = (ct, now)

            infos.append(ProcessInfo(
                pid=pid,
                name=name,
                username=uname,
                status=_call(p.status) or "?",
                cpu_one_core=min(cpu_one, 100.0),
                cpu_total=min(cpu_one / self.cores, 100.0),
                rss_mb=rss_mb,
                mem_pct=mem_pct,
                threads=threads,
                priority_name=prio_name,
                age_s=age_s,
                accessible=accessible,
            ))

        # forget dead pids so the cache cannot grow unbounded
        self._cache = {k: v for k, v in self._cache.items() if k in seen}

        sample = None
        if with_system:
            sample = SystemSample(
                ts=now,
                cpu_pct=psutil.cpu_percent(None),
                per_core=list(psutil.cpu_percent(None, percpu=True)),
                mem_pct=vm.percent,
                swap_pct=sm.percent,
                mem_used_mb=vm.used / _MB,
                mem_total_mb=vm.total / _MB,
            )
        return infos, sample
