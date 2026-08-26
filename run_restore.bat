@echo off
rem Revert the priority changes made by the last optimization run
cd /d "%~dp0"
python main.py restore --last
pause
