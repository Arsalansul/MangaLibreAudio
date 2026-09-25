@echo off
setlocal
cd /d "%~dp0"

if not exist ".venv\Scripts\python.exe" (
  echo [AudioManga] Creating Python environment...
  python -m venv .venv || goto :error
  ".venv\Scripts\python.exe" -m pip install -r requirements.txt || goto :error
)

echo [AudioManga] Starting local web interface...
if "%~1"=="" (
  ".venv\Scripts\python.exe" webapp.py
) else (
  ".venv\Scripts\python.exe" webapp.py "%~1"
)
exit /b %errorlevel%

:error
echo.
echo AudioManga failed to start.
pause
exit /b 1
