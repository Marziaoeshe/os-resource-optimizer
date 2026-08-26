@echo off
title Intelligent OS Resource Optimization System
cd /d "%~dp0"
:menu
cls
echo ======================================================
echo    INTELLIGENT OS RESOURCE OPTIMIZATION SYSTEM
echo ======================================================
echo    1. GUI dashboard          (graphs + auto-optimize)
echo    2. Live CLI dashboard     (Ctrl+C to stop)
echo    3. Analyze system         (bottlenecks + ranking)
echo    4. Optimize now           (asks Y/N confirmation)
echo    5. Process table          (top CPU / RAM consumers)
echo    6. Optimization history   (measured improvements)
echo    7. Undo last optimization (restore priorities)
echo    8. DEMO: real CPU load -^> optimize -^> measure
echo    9. Setup / repair         (install psutil)
echo    0. Exit
echo ======================================================
set "choice="
set /p choice="Select [0-9]: "
if "%choice%"=="1" start "" powershell -NoProfile -Command "Set-Location '%~dp0'; python main.py gui 2>&1 | Tee-Object -FilePath '%~dp0gui_console.log'" & goto menu
if "%choice%"=="2" call run_dashboard.bat
if "%choice%"=="3" call run_analyze.bat
if "%choice%"=="4" call run_optimize.bat
if "%choice%"=="5" call run_processes.bat
if "%choice%"=="6" call run_history.bat
if "%choice%"=="7" call run_restore.bat
if "%choice%"=="8" call run_demo.bat
if "%choice%"=="9" call setup_check.bat
if "%choice%"=="0" exit /b 0
goto menu
