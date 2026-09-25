@echo off
setlocal
cd /d "%~dp0"

echo [AudioManga] Installing F5-TTS with NVIDIA CUDA support...
if not exist ".f5-venv\Scripts\python.exe" (
  python -m venv .f5-venv || goto :error
)
".f5-venv\Scripts\python.exe" -m pip install --upgrade pip || goto :error
".f5-venv\Scripts\python.exe" -m pip install --force-reinstall --index-url https://download.pytorch.org/whl/cu126 torch==2.6.0 torchaudio==2.6.0 || goto :error
".f5-venv\Scripts\python.exe" -m pip install -r requirements-f5.txt || goto :error
powershell -NoProfile -ExecutionPolicy Bypass -Command "$root=Get-ChildItem -LiteralPath ($env:LOCALAPPDATA+'\Microsoft\WinGet\Packages') -Directory ^| Where-Object Name -like 'Gyan.FFmpeg.Shared_*' ^| Select-Object -First 1; if(-not $root){throw 'Install Gyan.FFmpeg.Shared first'}; $bin=Get-ChildItem -LiteralPath $root.FullName -Recurse -File -Filter ffmpeg.exe ^| Select-Object -First 1; Copy-Item -Path ($bin.Directory.FullName+'\*.dll') -Destination '.f5-venv\Lib\site-packages\torchcodec' -Force" || goto :error

echo.
echo F5-TTS with NVIDIA CUDA installed successfully.
echo Select "Local NVIDIA CUDA" in AudioManga settings.
pause
exit /b 0

:error
echo.
echo F5-TTS NVIDIA installation failed.
pause
exit /b 1
