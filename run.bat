@echo off
cd /d "%~dp0"
if exist ".venv\Scripts\python.exe" (
  ".venv\Scripts\python.exe" main.py
) else (
  python main.py
)
if errorlevel 1 (
  echo.
  echo If the program did not start, run setup.bat once.
  pause
)
