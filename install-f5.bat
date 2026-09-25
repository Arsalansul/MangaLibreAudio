@echo off
setlocal
cd /d "%~dp0"

echo [AudioManga] Installing F5-TTS in an isolated environment...
py -3.11 -c "import sys; print(sys.version)" >nul 2>&1
if errorlevel 1 goto :python_error

if exist ".f5-venv\Scripts\python.exe" (
  ".f5-venv\Scripts\python.exe" -c "import sys; raise SystemExit(0 if sys.version_info[:2] == (3, 11) else 1)"
  if errorlevel 1 (
    echo Recreating .f5-venv with Python 3.11...
    rmdir /s /q ".f5-venv"
  )
)
if not exist ".f5-venv\Scripts\python.exe" py -3.11 -m venv .f5-venv
if errorlevel 1 goto :error

".f5-venv\Scripts\python.exe" -m pip install --upgrade pip
if errorlevel 1 goto :error
".f5-venv\Scripts\python.exe" -m pip install --force-reinstall --index-url https://download.pytorch.org/whl/cpu torch==2.6.0 torchaudio==2.6.0
if errorlevel 1 goto :error
".f5-venv\Scripts\python.exe" -m pip install -r requirements-f5.txt
if errorlevel 1 goto :error
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0prepare-f5-ffmpeg.ps1"
if errorlevel 1 goto :error

echo.
echo F5-TTS installed successfully.
echo Model checkpoints will be downloaded on the first F5 build.
pause
exit /b 0

:python_error
echo.
echo Python 3.11 was not found.
echo Install it with: winget install --id Python.Python.3.11 -e
echo Then run this file again.
pause
exit /b 1

:error
echo.
echo F5-TTS installation failed.
pause
exit /b 1
