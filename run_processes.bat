@echo off
rem Top processes by live CPU / memory / threads
cd /d "%~dp0"
python main.py processes --top 25
pause
