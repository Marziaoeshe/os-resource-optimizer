"""Central configuration: thresholds, weights, safety lists.

Every tunable used by the analysis / optimization pipeline lives here so
behaviour is auditable and easy to justify in a report.
"""

# ---------------------------------------------------------------------------
# Bottleneck detection thresholds (percent of capacity)
# ---------------------------------------------------------------------------
CPU_CRITICAL_PCT = 85.0     # sustained total-CPU% at or above this => critical
CPU_ELEVATED_PCT = 65.0     # elevated but not critical
MEM_CRITICAL_PCT = 88.0     # physical RAM pressure
MEM_ELEVATED_PCT = 72.0

# ---------------------------------------------------------------------------
# Optimization candidate eligibility
# ---------------------------------------------------------------------------
MIN_CANDIDATE_CPU_TOTAL = 5.0    # process must use >=5% of TOTAL cpu capacity
MIN_PROCESS_AGE_S = 5.0          # ignore processes younger than this
MAX_ACTIONS_PER_RUN = 8          # never change more than N processes per run

# ---------------------------------------------------------------------------
# Before/after measurement
# ---------------------------------------------------------------------------
SAMPLE_WINDOW_S = 3.0            # wall-clock window for system CPU sampling
SAMPLE_STEP_S = 0.4              # granularity of cpu_percent() calls
BENCH_TARGET_MS = 450.0          # calibration target for the benchmark body
BENCH_TRIALS = 5                 # trials per phase; BEST (min) wall kept
SETTLE_S = 2.0                   # grace period between apply and re-measure
IMPROVE_SUCCESS_PCT = 5.0        # benchmark gain >= this counts as success

# ---------------------------------------------------------------------------
# GUI dashboard + auto-optimization
# ---------------------------------------------------------------------------
GUI_REFRESH_S = 2.0              # data refresh tick for the graphical view
GUI_TABLE_ROWS = 20              # rows shown in the live process table
GUI_GRAPH_POINTS = 150           # points kept per graph (~5 min at 2s ticks)
AUTO_OPTIMIZE_DEFAULT = False    # initial state of the Auto Optimization switch
AUTO_COOLDOWN_S = 120.0          # min seconds between automatic optimize runs

# ---------------------------------------------------------------------------
# Process ranking score weights (must sum to 1.0)
#   score = W_CPU*cpu%_of_total + W_MEM*ram% + W_THREADS*thread-pressure%
# ---------------------------------------------------------------------------
W_CPU = 0.60
W_MEM = 0.25
W_THREADS = 0.15
THREAD_REF_COUNT = 128           # thread count normalised against this

# ---------------------------------------------------------------------------
# Safety model
# ---------------------------------------------------------------------------
# Name-based block list (case-insensitive). These are never touched even when
# running elevated. The ownership rule (see analyzer) blocks everything owned
# by other accounts (SYSTEM/LOCAL SERVICE/...) as a second layer.
PROTECTED_NAMES = frozenset({
    "system", "system idle process", "registry", "memcompression",
    "smss.exe", "csrss.exe", "wininit.exe", "winlogon.exe",
    "services.exe", "lsass.exe", "svchost.exe", "dwm.exe",
    "fontdrvhost.exe", "explorer.exe", "sihost.exe", "ctfmon.exe",
    "taskhostw.exe", "audiodg.exe", "conhost.exe", "runtimebroker.exe",
    "msmpeng.exe", "nissrv.exe", "securityhealthservice.exe",
    "securityhealthsystray.exe", "wudfhost.exe", "spoolsv.exe",
    "cmd.exe", "powershell.exe", "pwsh.exe", "windowsterminal.exe",
    "openconsole.exe", "code.exe", "devenv.exe",
})

PROTECTED_PIDS = frozenset({0, 4})

# Semantic priority ranks (higher number == more CPU-favoured).
PRIORITY_RANK = {
    "idle": 0,
    "below_normal": 1,
    "normal": 2,
    "above_normal": 3,
    "high": 4,
    "realtime": 5,
}

# Conservative demotion ladder: exactly one level down per run.
DEMOTE_ONE_LEVEL = {
    "realtime": "high",
    "high": "above_normal",
    "above_normal": "normal",
    "normal": "below_normal",
}

# --deep mode: jump straight to the bottom (realtime still capped for safety).
DEEP_TARGET = {
    "realtime": "below_normal",
    "high": "idle",
    "above_normal": "idle",
    "normal": "idle",
}
