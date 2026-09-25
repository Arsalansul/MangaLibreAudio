@echo off
setlocal
cd /d "%~dp0"

echo [AudioManga] Installing F5-TTS with NVIDIA CUDA support...
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
".f5-venv\Scripts\python.exe" -m pip install --force-reinstall --index-url https://download.pytorch.org/whl/cu126 torch==2.6.0 torchaudio==2.6.0
if errorlevel 1 goto :error
".f5-venv\Scripts\python.exe" -m pip install -r requirements-f5.txt
if errorlevel 1 goto :error
powershell -NoProfile -ExecutionPolicy Bypass -Command "$root=Get-ChildItem -LiteralPath ($env:LOCALAPPDATA+'\Microsoft\WinGet\Packages') -Directory ^| Where-Object Name -like 'Gyan.FFmpeg.Shared_*' ^| Select-Object -First 1; if(-not $root){throw 'Install Gyan.FFmpeg.Shared first'}; $bin=Get-ChildItem -LiteralPath $root.FullName -Recurse -File -Filter ffmpeg.exe ^| Select-Object -First 1; Copy-Item -Path ($bin.Directory.FullName+'\*.dll') -Destination '.f5-venv\Lib\site-packages\torchcodec' -Force"
if errorlevel 1 goto :error
".f5-venv\Scripts\python.exe" -c "import torch; ok=torch.cuda.is_available(); print('PyTorch:', torch.__version__); print('GPU:', torch.cuda.get_device_name(0) if ok else 'not available'); raise SystemExit(0 if ok else 1)"
if errorlevel 1 goto :cuda_error

echo.
echo F5-TTS with NVIDIA CUDA installed successfully.
echo Select "Local NVIDIA CUDA" in AudioManga settings.
pause
exit /b 0

:python_error
echo.
echo Python 3.11 was not found.
echo Install it with: winget install --id Python.Python.3.11 -e
echo Then run this file again.
pause
exit /b 1

:cuda_error
echo.
echo PyTorch was installed, but CUDA is not available.
echo Update the NVIDIA driver and check the GPU with nvidia-smi.
pause
exit /b 1

:error
echo.
echo F5-TTS NVIDIA installation failed.
pause
exit /b 1
