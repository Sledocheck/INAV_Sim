@echo off
cd /d "%~dp0"
echo === INAV Sim: установка зависимостей ===
where python >nul 2>&1
if errorlevel 1 (
  echo Python 3.10+ не найден в PATH.
  echo Установите Python с python.org и запустите setup.bat снова.
  pause
  exit /b 1
)
if not exist ".venv\Scripts\python.exe" (
  python -m venv .venv
  if errorlevel 1 goto fail
)
call ".venv\Scripts\activate.bat"
python -m pip install --upgrade pip
pip install -r requirements.txt
if errorlevel 1 goto fail
echo.
echo Готово. Запускайте run.bat.
pause
exit /b 0

:fail
echo.
echo Не удалось установить зависимости. Проверьте интернет и Python.
pause
exit /b 1
