@echo off
rem Past optimization runs with measured improvements
cd /d "%~dp0"
python main.py history --limit 25
pause
