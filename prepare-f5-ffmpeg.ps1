$ErrorActionPreference = "Stop"

function Find-SharedFfmpeg {
    $packages = Join-Path $env:LOCALAPPDATA "Microsoft\WinGet\Packages"
    if (-not (Test-Path -LiteralPath $packages)) {
        return $null
    }
    $root = Get-ChildItem -LiteralPath $packages -Directory |
        Where-Object { $_.Name -like "Gyan.FFmpeg.Shared_*" } |
        Select-Object -First 1
    if (-not $root) {
        return $null
    }
    return Get-ChildItem -LiteralPath $root.FullName -Recurse -File -Filter ffmpeg.exe |
        Select-Object -First 1
}

$ffmpeg = Find-SharedFfmpeg
if (-not $ffmpeg) {
    Write-Host "[AudioManga] Installing FFmpeg Shared through Winget..."
    & winget install --id Gyan.FFmpeg.Shared -e --silent `
        --accept-package-agreements --accept-source-agreements
    if ($LASTEXITCODE -ne 0) {
        throw "Winget could not install Gyan.FFmpeg.Shared (code $LASTEXITCODE)"
    }
    $ffmpeg = Find-SharedFfmpeg
}
if (-not $ffmpeg) {
    throw "Gyan.FFmpeg.Shared was installed, but ffmpeg.exe was not found"
}

$torchCodec = Join-Path $PSScriptRoot ".f5-venv\Lib\site-packages\torchcodec"
if (Test-Path -LiteralPath $torchCodec) {
    Get-ChildItem -LiteralPath $ffmpeg.Directory.FullName -File -Filter *.dll |
        Copy-Item -Destination $torchCodec -Force
    Write-Host "[AudioManga] FFmpeg DLLs copied for TorchCodec."
}
Write-Host "[AudioManga] FFmpeg: $($ffmpeg.FullName)"
