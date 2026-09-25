from __future__ import annotations

import os
import shutil
import subprocess
import tempfile
from pathlib import Path
from typing import Literal

import torch
from fastapi import FastAPI, File, Form, HTTPException, UploadFile
from fastapi.responses import FileResponse
from starlette.background import BackgroundTask


MODEL = "F5TTS_Base"
CHECKPOINT = "hf://hotstone228/F5-TTS-Russian/model_last.safetensors"
VOCAB = "hf://hotstone228/F5-TTS-Russian/vocab.txt"
MAX_REFERENCE_BYTES = int(os.getenv("F5_MAX_REFERENCE_MB", "50")) * 1024 * 1024
ALLOWED_DEVICES = {"auto", "cpu", "cuda"}


def select_device(requested: str | None = None) -> Literal["cpu", "cuda"]:
    value = (requested or os.getenv("F5_DEVICE", "auto")).strip().lower()
    if value not in ALLOWED_DEVICES:
        raise ValueError("F5_DEVICE должен быть auto, cpu или cuda")
    if value == "auto":
        return "cuda" if torch.cuda.is_available() else "cpu"
    if value == "cuda" and not torch.cuda.is_available():
        raise ValueError("Запрошен CUDA, но PyTorch не видит совместимую NVIDIA GPU")
    return value  # type: ignore[return-value]


def _cli() -> str:
    executable = os.getenv("F5_CLI", "f5-tts_infer-cli")
    resolved = shutil.which(executable)
    if not resolved:
        raise RuntimeError(f"Не найден F5-TTS CLI: {executable}")
    return resolved


def build_command(
    reference: Path,
    output_dir: Path,
    text: str,
    reference_text: str,
    speed: float,
    nfe_step: int,
    device: str,
) -> list[str]:
    return [
        _cli(),
        "--model", MODEL,
        "--ckpt_file", CHECKPOINT,
        "--vocab_file", VOCAB,
        "--ref_audio", str(reference),
        "--ref_text", reference_text,
        "--gen_text", text,
        "--output_dir", str(output_dir),
        "--output_file", "raw.wav",
        "--speed", str(speed),
        "--nfe_step", str(nfe_step),
        "--device", device,
        "--remove_silence",
    ]


app = FastAPI(title="AudioManga F5 Worker", version="1.0.0")


@app.get("/health")
def health() -> dict[str, object]:
    configured = os.getenv("F5_DEVICE", "auto").strip().lower()
    try:
        selected = select_device(configured)
        error = None
    except ValueError as exc:
        selected = None
        error = str(exc)
    return {
        "status": "ok" if error is None else "error",
        "configured_device": configured,
        "device": selected,
        "cuda_available": torch.cuda.is_available(),
        "gpu": torch.cuda.get_device_name(0) if torch.cuda.is_available() else None,
        "model": "hotstone228/F5-TTS-Russian",
        "error": error,
    }


@app.post("/synthesize", response_class=FileResponse)
async def synthesize(
    text: str = Form(...),
    reference_audio: UploadFile = File(...),
    reference_text: str = Form(""),
    speed: float = Form(1.0),
    nfe_step: int = Form(16),
) -> FileResponse:
    text = text.strip()
    if not text:
        raise HTTPException(422, "Поле text не может быть пустым")
    if not 0.3 <= speed <= 2.0:
        raise HTTPException(422, "speed должен быть от 0.3 до 2.0")
    if not 4 <= nfe_step <= 64:
        raise HTTPException(422, "nfe_step должен быть от 4 до 64")
    try:
        device = select_device()
    except ValueError as exc:
        raise HTTPException(503, str(exc)) from exc

    temp_dir = Path(tempfile.mkdtemp(prefix="audiomanga-f5-"))
    suffix = Path(reference_audio.filename or "reference.wav").suffix[:10] or ".wav"
    reference = temp_dir / f"reference{suffix}"
    raw_output = temp_dir / "raw.wav"
    output = temp_dir / "result.wav"
    try:
        size = 0
        with reference.open("wb") as stream:
            while chunk := await reference_audio.read(1024 * 1024):
                size += len(chunk)
                if size > MAX_REFERENCE_BYTES:
                    raise HTTPException(413, "Референсное аудио слишком большое")
                stream.write(chunk)
        if size == 0:
            raise HTTPException(422, "Референсное аудио пустое")

        command = build_command(
            reference, temp_dir, text, reference_text.strip(), speed, nfe_step, device
        )
        environment = os.environ.copy()
        environment.setdefault("PYTHONUTF8", "1")
        timeout = float(os.getenv("F5_SYNTHESIS_TIMEOUT", "900"))
        try:
            result = subprocess.run(
                command,
                env=environment,
                capture_output=True,
                text=True,
                timeout=timeout,
                encoding="utf-8",
                errors="replace",
            )
        except subprocess.TimeoutExpired as exc:
            raise HTTPException(504, "F5-TTS превысил время ожидания") from exc
        if result.returncode or not raw_output.is_file():
            details = (result.stderr or result.stdout or "неизвестная ошибка").strip()[-2000:]
            raise HTTPException(500, f"F5-TTS завершился с ошибкой: {details}")
        conversion = subprocess.run(
            [
                "ffmpeg", "-y", "-hide_banner", "-loglevel", "error",
                "-i", str(raw_output), "-ac", "1", "-ar", "48000",
                "-c:a", "pcm_s16le", str(output),
            ],
            capture_output=True,
            text=True,
            timeout=120,
            encoding="utf-8",
            errors="replace",
        )
        if conversion.returncode or not output.is_file():
            details = (conversion.stderr or "неизвестная ошибка").strip()[-2000:]
            raise HTTPException(500, f"FFmpeg не смог подготовить WAV: {details}")
        return FileResponse(
            output,
            media_type="audio/wav",
            filename="speech.wav",
            background=BackgroundTask(shutil.rmtree, temp_dir, ignore_errors=True),
            headers={"X-F5-Device": device},
        )
    except HTTPException:
        shutil.rmtree(temp_dir, ignore_errors=True)
        raise
    except Exception as exc:
        shutil.rmtree(temp_dir, ignore_errors=True)
        raise HTTPException(500, f"Ошибка синтеза: {exc}") from exc
