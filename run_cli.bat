@echo off
rem Intelligent OS Resource Optimization System - CLI launcher
cd /d "%~dp0"
echo Commands: dashboard | gui | analyze | optimize | processes | history | restore
echo Example:  python main.py optimize --target 1234
python main.py dashboard
pause
