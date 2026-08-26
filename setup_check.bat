@echo off
rem Installs/repairs dependencies (internet required once)
cd /d "%~dp0"
echo Checking Python...
python --version || (echo Python 3.8+ is required. & pause & exit /b 1)
echo Installing requirements (psutil)...
python -m pip install -r requirements.txt
echo.
echo Done. You can now use any launcher.
pause
