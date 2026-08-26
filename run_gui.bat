@echo off
rem Intelligent OS Resource Optimization System - GUI launcher
cd /d "%~dp0"
start "" powershell -NoProfile -Command "Set-Location 'C:\Users\DLG\os-resource-optimizer'; python main.py gui 2>&1 | Tee-Object -FilePath gui_console.log"
exit
