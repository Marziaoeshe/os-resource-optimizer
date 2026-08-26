@echo off
rem DEMO: creates REAL CPU pressure, then optimizes it and shows
rem the measured before/after result. Nothing is simulated - the load
rem generator spawns real spinning processes that the OS reports on.
cd /d "%~dp0"
mkdir "%TEMP%\opencode" 2>nul

echo [1/3] Starting real CPU load: 8 worker processes for 90 seconds...
start "" /min python tools\loadgen.py --cpus 8 --duration 90 --out "%TEMP%\opencode\lg.pids.json"
timeout /t 5 /nobreak >nul

echo [2/3] Running optimization pipeline (before -^> optimize -^> after)...
python main.py optimize --yes

echo [3/3] Optimization history:
python main.py history --limit 3

powershell -NoProfile -Command "$j = Get-Content '$env:TEMP\opencode\lg.pids.json' | ConvertFrom-Json; foreach ($w in @($j.workers) + @($j.launcher_pid)) { try { Stop-Process -Id $w -Force } catch {} }; Remove-Item '$env:TEMP\opencode\lg.pids.json' -ErrorAction SilentlyContinue"
echo Load removed. DEMO finished.
pause
