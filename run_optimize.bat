@echo off
rem Full optimization pipeline - asks "Apply? [y/N]" before changing anything
cd /d "%~dp0"
python main.py optimize
pause
