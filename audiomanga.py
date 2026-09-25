#!/usr/bin/env python3
"""Build a narrated manga chapter from translated page analysis files."""

from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import os
import re
import shutil
import subprocess
import sys
import urllib.error
import urllib.request
import uuid
import warnings
import wave
from array import array
from html import escape as xml_escape
from pathlib import Path
from typing import Any


PROJECT_FILE = "audiomanga.project.json"
BUILD_DIR = ".audiomanga"
DEFAULT_SILERO_MODEL = Path(__file__).resolve().parent / ".runtime" / "models" / "v5_4_ru.pt"
LOCAL_PYTHON = Path(__file__).resolve().parent / ".venv" / "Scripts" / "python.exe"
DEFAULT_F5_CLI = Path(__file__).resolve().parent / ".f5-venv" / "Scripts" / "f5-tts_infer-cli.exe"
DEFAULT_FFMPEG_CANDIDATES = (
    Path(r"E:\ffmpeg\bin\ffmpeg.exe"),
    Path(r"E:\ffmpeg\ffmpeg.exe"),
)
IMAGE_EXTENSIONS = (".png", ".jpg", ".jpeg", ".webp")


class BuildError(RuntimeError):
    pass


def use_local_runtime() -> None:
    """Re-exec through AudioManga's venv when the launcher Python lacks Torch."""
    if importlib.util.find_spec("torch") is not None:
        return
    if not LOCAL_PYTHON.is_file():
        return
    current = Path(sys.executable).resolve()
    if current == LOCAL_PYTHON.resolve():
        return
    completed = subprocess.run([str(LOCAL_PYTHON), str(Path(__file__).resolve()), *sys.argv[1:]])
    raise SystemExit(completed.returncode)


def log(message: str) -> None:
    print(message, flush=True)


def read_json(path: Path) -> dict[str, Any]:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise BuildError(f"Не удалось прочитать JSON {path}: {exc}") from exc


def write_json(path: Path, value: Any) -> None:
    path.write_text(
        json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )


def find_page_image(chapter: Path, stem: str) -> Path | None:
    for extension in IMAGE_EXTENSIONS:
        candidate = chapter / f"{stem}{extension}"
        if candidate.is_file():
            return candidate
    return None


def region_sort_key(region: dict[str, Any]) -> tuple[int, int, int]:
    """Approximate Japanese manga order: rows top-down, items right-to-left."""
    bbox = region.get("bbox") or [0, 0, 0, 0]
    x, y, _width, _height = (list(bbox) + [0, 0, 0, 0])[:4]
    # A fixed band keeps nearby bubbles on the same reading row. Using each
    # bubble's own height here would make large bubbles jump ahead of small ones.
    return (int(y) // 120, -int(x), int(y))


def clean_translation(value: Any) -> str:
    text = str(value or "").strip()
    text = re.sub(r"\s*\|\s*", " ", text)
    text = re.sub(r"\s+", " ", text)
    return text


def default_prosody(text: str) -> dict[str, Any]:
    """Create a conservative first-pass delivery hint from punctuation."""
    if "..." in text or "…" in text:
        return {
            "rate": "slow",
            "pitch": "low",
            "pause_before_ms": 0,
            "pause_after_ms": 350,
        }
    if "!" in text:
        return {
            "rate": "fast",
            "pitch": "high",
            "pause_before_ms": 0,
            "pause_after_ms": 180,
        }
    return {
        "rate": "medium",
        "pitch": "medium",
        "pause_before_ms": 0,
        "pause_after_ms": 0,
    }


def discover_pages(chapter: Path) -> tuple[list[dict[str, Any]], list[str]]:
    pages: list[dict[str, Any]] = []
    warnings: list[str] = []
    for analysis_path in sorted(chapter.glob("*.analysis.json")):
        stem = analysis_path.name.removesuffix(".analysis.json")
        image = find_page_image(chapter, stem)
        if image is None:
            warnings.append(f"{stem}: пропущена — рядом нет готового изображения")
            continue
        analysis = read_json(analysis_path)
        regions = []
        raw_regions = sorted(analysis.get("regions") or [], key=region_sort_key)
        for region in raw_regions:
            text = clean_translation(region.get("translation"))
            if not text:
                continue
            regions.append(
                {
                    "id": str(region.get("id") or f"r{len(regions) + 1:03d}"),
                    "text": text,
                    "tts_text": text,
                    "speak": True,
                    "engine": "silero",
                    "speaker": "narrator",
                    "voice": "aidar",
                    "prosody": default_prosody(text),
                    "f5": {
                        "reference_audio": "",
                        "reference_text": "",
                        "speed": 1.0,
                        "nfe_step": 32,
                    },
                    "kind": str(region.get("kind") or "text"),
                    "bbox": region.get("bbox") or [],
                }
            )
        pages.append(
            {
                "id": stem,
                "image": image.name,
                "analysis": analysis_path.name,
                "regions": regions,
            }
        )
    return pages, warnings


def new_project(chapter: Path) -> dict[str, Any]:
    pages, warnings = discover_pages(chapter)
    if not pages:
        raise BuildError(
            "Не найдено ни одной пары <страница> + <страница>.analysis.json"
        )
    return {
        "version": 1,
        "chapter": str(chapter.resolve()),
        "settings": {
            "default_voice": "aidar",
            "sample_rate": 48000,
            "pause_between_replicas_ms": 450,
            "page_lead_ms": 500,
            "page_tail_ms": 800,
            "resolution": "1920x1080",
            "fps": 30,
            "background": "black",
            "f5_execution": "cpu",
            "f5_worker_url": "http://127.0.0.1:8770",
        },
        "pages": pages,
        "warnings": warnings,
    }


def load_or_create_project(chapter: Path, refresh: bool) -> tuple[dict[str, Any], Path]:
    project_path = chapter / PROJECT_FILE
    if project_path.exists() and not refresh:
        project = read_json(project_path)
        log(f"Сценарий: {project_path} (существующий)")
        return project, project_path
    project = new_project(chapter)
    write_json(project_path, project)
    log(f"Сценарий создан: {project_path}")
    return project, project_path


def find_ffmpeg(explicit: str | None) -> Path:
    candidates: list[Path] = []
    if explicit:
        candidates.append(Path(explicit))
    env_path = os.environ.get("AUDIOMANGA_FFMPEG")
    if env_path:
        candidates.append(Path(env_path))
    on_path = shutil.which("ffmpeg")
    if on_path:
        candidates.append(Path(on_path))
    candidates.extend(DEFAULT_FFMPEG_CANDIDATES)
    local_app_data = os.environ.get("LOCALAPPDATA")
    if local_app_data:
        winget_packages = Path(local_app_data) / "Microsoft" / "WinGet" / "Packages"
        if winget_packages.is_dir():
            candidates.extend(
                sorted(
                    winget_packages.glob("Gyan.FFmpeg.Shared_*/*/bin/ffmpeg.exe"),
                    reverse=True,
                )
            )
            candidates.extend(
                sorted(
                    winget_packages.glob("Gyan.FFmpeg_*/*/bin/ffmpeg.exe"),
                    reverse=True,
                )
            )
    for candidate in candidates:
        if candidate.is_file():
            return candidate.resolve()
    raise BuildError(
        "ffmpeg.exe не найден. В E:\\ffmpeg находится исходный код, а не Windows-"
        "сборка. Положите готовый файл в E:\\ffmpeg\\bin\\ffmpeg.exe либо передайте "
        "--ffmpeg <путь>."
    )


def write_pcm16_wav(path: Path, samples: Any, sample_rate: int) -> None:
    values = samples.tolist() if hasattr(samples, "tolist") else list(samples)
    pcm = array(
        "h",
        (
            max(-32768, min(32767, int(float(value) * 32767.0)))
            for value in values
        ),
    )
    with wave.open(str(path), "wb") as output:
        output.setnchannels(1)
        output.setsampwidth(2)
        output.setframerate(sample_rate)
        output.writeframes(pcm.tobytes())


_silero_model: Any = None
VALID_RATES = {"x-slow", "slow", "medium", "fast", "x-fast"}
VALID_PITCHES = {"x-low", "low", "medium", "high", "x-high"}


def make_ssml(text: str, prosody: dict[str, Any] | None = None) -> str:
    prosody = prosody or {}
    rate = str(prosody.get("rate", "medium"))
    pitch = str(prosody.get("pitch", "medium"))
    if rate not in VALID_RATES:
        raise BuildError(f"Некорректный темп Silero: {rate}")
    if pitch not in VALID_PITCHES:
        raise BuildError(f"Некорректная высота голоса Silero: {pitch}")
    before = max(0, int(prosody.get("pause_before_ms", 0)))
    after = max(0, int(prosody.get("pause_after_ms", 0)))
    parts = ["<speak>"]
    if before:
        parts.append(f'<break time="{before}ms"/>')
    parts.append(
        f'<prosody rate="{rate}" pitch="{pitch}">{xml_escape(text)}</prosody>'
    )
    if after:
        parts.append(f'<break time="{after}ms"/>')
    parts.append("</speak>")
    return "".join(parts)


def synthesize_silero(
    text: str,
    voice: str,
    output: Path,
    model_path: Path,
    prosody: dict[str, Any] | None = None,
) -> None:
    global _silero_model
    if not model_path.is_file():
        raise BuildError(f"Не найдена модель Silero: {model_path}")
    try:
        import torch
    except ImportError as exc:
        raise BuildError(
            "Для Silero не найден пакет torch. Запустите AudioManga в Python-"
            "окружении с PyTorch или установите зависимости из requirements.txt."
        ) from exc
    if _silero_model is None:
        torch.set_num_threads(4)
        with warnings.catch_warnings():
            # The packaged upstream model contains an old regex escape which is
            # harmless on current Python but otherwise prints on every process.
            warnings.simplefilter("ignore", SyntaxWarning)
            _silero_model = torch.package.PackageImporter(str(model_path)).load_pickle(
                "tts_models", "model"
            )
        _silero_model.to(torch.device("cpu"))
    audio = _silero_model.apply_tts(
        ssml_text=make_ssml(text, prosody),
        speaker=voice,
        sample_rate=48000,
        put_accent=True,
        put_yo=True,
    )
    # Tensor.tolist() works without NumPy and keeps the minimal runtime robust.
    write_pcm16_wav(output, audio.detach().cpu(), 48000)


def synthesize(
    text: str,
    voice: str,
    output: Path,
    model_path: Path,
    prosody: dict[str, Any] | None = None,
) -> None:
    output.parent.mkdir(parents=True, exist_ok=True)
    temporary = output.with_suffix(".tmp.wav")
    try:
        synthesize_silero(text, voice, temporary, model_path, prosody)
        temporary.replace(output)
    finally:
        temporary.unlink(missing_ok=True)


def resolve_reference_audio(chapter: Path, value: str) -> Path:
    path = Path(value).expanduser()
    if not path.is_absolute():
        path = chapter / path
    return path.resolve()


def clean_f5_text(text: str) -> str:
    """Remove Silero markers and avoid spelling all-caps text character by character."""
    cleaned = text.replace("+", "")
    letters = "".join(character for character in cleaned if character.isalpha())
    if letters and letters.isupper():
        cleaned = cleaned.lower()
        for index, character in enumerate(cleaned):
            if character.isalpha():
                cleaned = cleaned[:index] + character.upper() + cleaned[index + 1 :]
                break
    return cleaned


def wav_has_speech(path: Path, minimum_peak: int = 100) -> bool:
    """Return false for a valid WAV that contains only digital silence."""
    try:
        with wave.open(str(path), "rb") as audio:
            if audio.getsampwidth() != 2:
                return True
            frames = audio.readframes(audio.getnframes())
    except (OSError, EOFError, wave.Error):
        return False
    if not frames:
        return False
    samples = memoryview(frames).cast("h")
    return any(abs(sample) >= minimum_peak for sample in samples)


def synthesize_f5(
    text: str,
    output: Path,
    reference_audio: Path,
    reference_text: str,
    speed: float,
    nfe_step: int,
    f5_cli: Path,
    ffmpeg: Path,
    device: str = "cpu",
    prosody: dict[str, Any] | None = None,
) -> None:
    if not f5_cli.is_file():
        raise BuildError(f"F5-TTS не установлен: {f5_cli}. Запустите install-f5.bat.")
    if not reference_audio.is_file():
        raise BuildError(f"Не найден референс голоса F5-TTS: {reference_audio}")
    if not 0.3 <= speed <= 2.0:
        raise BuildError(f"Скорость F5-TTS должна быть от 0.3 до 2.0, получено: {speed}")
    if not 4 <= nfe_step <= 64:
        raise BuildError(f"NFE steps F5-TTS должны быть от 4 до 64, получено: {nfe_step}")
    if device not in {"cpu", "cuda"}:
        raise BuildError(f"Неизвестное устройство F5-TTS: {device}")

    output.parent.mkdir(parents=True, exist_ok=True)
    raw_output = output.with_suffix(".f5.wav")
    converted = output.with_suffix(".tmp.wav")
    raw_output.unlink(missing_ok=True)
    converted.unlink(missing_ok=True)
    command = [
        str(f5_cli),
        "--model", "F5TTS_Base",
        "--ckpt_file", "hf://hotstone228/F5-TTS-Russian/model_last.safetensors",
        "--vocab_file", "hf://hotstone228/F5-TTS-Russian/vocab.txt",
        "--ref_audio", str(reference_audio),
        "--ref_text", reference_text,
        "--gen_text", text,
        "--output_dir", str(output.parent),
        "--output_file", raw_output.name,
        "--speed", str(speed),
        "--nfe_step", str(nfe_step),
        "--device", device,
    ]
    if sum(character.isalpha() for character in text) >= 12:
        command.append("--remove_silence")
    environment = os.environ.copy()
    environment["PYTHONUTF8"] = "1"
    environment["PATH"] = str(ffmpeg.parent) + os.pathsep + environment.get("PATH", "")
    environment.setdefault(
        "HF_HOME", str(Path(__file__).resolve().parent / ".runtime" / "huggingface")
    )
    if not reference_text.strip():
        log(
            "F5-TTS: текст референса пуст — запускаю автоматическую расшифровку. "
            "При первом запуске будет загружена модель Whisper."
        )
    try:
        result = subprocess.run(command, env=environment)
        if result.returncode or not raw_output.is_file():
            raise BuildError(f"F5-TTS завершился с кодом {result.returncode}")
        conversion = subprocess.run(
            [
                str(ffmpeg), "-y", "-hide_banner", "-loglevel", "error",
                "-i", str(raw_output), "-ac", "1", "-ar", "48000",
                "-c:a", "pcm_s16le", str(converted),
            ]
        )
        if conversion.returncode or not converted.is_file():
            raise BuildError("FFmpeg не смог привести звук F5-TTS к PCM16 48 kHz")
        converted.replace(output)
        if not wav_has_speech(output):
            raise BuildError(
                "F5-TTS создал пустую озвучку. Попробуйте увеличить текст реплики "
                "или число NFE steps."
            )
        add_wav_padding(
            output,
            max(0, int((prosody or {}).get("pause_before_ms", 0))),
            max(0, int((prosody or {}).get("pause_after_ms", 0))),
        )
    finally:
        raw_output.unlink(missing_ok=True)
        converted.unlink(missing_ok=True)


def encode_multipart(fields: dict[str, str], file_field: str, file_path: Path) -> tuple[bytes, str]:
    boundary = f"----AudioManga{uuid.uuid4().hex}"
    chunks: list[bytes] = []
    for name, value in fields.items():
        chunks.extend(
            [
                f"--{boundary}\r\n".encode(),
                f'Content-Disposition: form-data; name="{name}"\r\n\r\n'.encode(),
                value.encode("utf-8"),
                b"\r\n",
            ]
        )
    safe_name = file_path.name.replace('"', "")
    chunks.extend(
        [
            f"--{boundary}\r\n".encode(),
            (
                f'Content-Disposition: form-data; name="{file_field}"; '
                f'filename="{safe_name}"\r\n'
            ).encode("utf-8"),
            b"Content-Type: application/octet-stream\r\n\r\n",
            file_path.read_bytes(),
            b"\r\n",
            f"--{boundary}--\r\n".encode(),
        ]
    )
    return b"".join(chunks), f"multipart/form-data; boundary={boundary}"


def synthesize_f5_remote(
    text: str,
    output: Path,
    reference_audio: Path,
    reference_text: str,
    speed: float,
    nfe_step: int,
    worker_url: str,
    timeout: float = 600.0,
    prosody: dict[str, Any] | None = None,
) -> None:
    if not reference_audio.is_file():
        raise BuildError(f"Не найден референс голоса F5-TTS: {reference_audio}")
    if not worker_url.strip():
        raise BuildError("Не указан адрес удалённого F5 Worker")
    body, content_type = encode_multipart(
        {
            "text": text,
            "reference_text": reference_text,
            "speed": str(speed),
            "nfe_step": str(nfe_step),
        },
        "reference_audio",
        reference_audio,
    )
    endpoint = worker_url.rstrip("/") + "/synthesize"
    request = urllib.request.Request(
        endpoint,
        data=body,
        headers={"Content-Type": content_type, "Accept": "audio/wav"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            audio = response.read()
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")[:1000]
        raise BuildError(f"F5 Worker вернул HTTP {exc.code}: {detail}") from exc
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        raise BuildError(f"F5 Worker недоступен по адресу {endpoint}: {exc}") from exc
    if not audio.startswith(b"RIFF") or b"WAVE" not in audio[:16]:
        raise BuildError("F5 Worker вернул данные, которые не похожи на WAV")
    output.parent.mkdir(parents=True, exist_ok=True)
    temporary = output.with_suffix(".remote.wav")
    try:
        temporary.write_bytes(audio)
        temporary.replace(output)
        channels, width, rate, _frames = wav_info(output)
        if (channels, width, rate) != (1, 2, 48000):
            raise BuildError(
                "F5 Worker вернул WAV в неподдерживаемом формате: "
                f"channels={channels}, sample_width={width}, sample_rate={rate}"
            )
        if not wav_has_speech(output):
            raise BuildError("F5 Worker вернул пустую озвучку")
        add_wav_padding(
            output,
            max(0, int((prosody or {}).get("pause_before_ms", 0))),
            max(0, int((prosody or {}).get("pause_after_ms", 0))),
        )
    except Exception:
        output.unlink(missing_ok=True)
        raise
    finally:
        temporary.unlink(missing_ok=True)


def add_wav_padding(path: Path, before_ms: int, after_ms: int) -> None:
    if before_ms <= 0 and after_ms <= 0:
        return
    channels, width, rate, _frames = wav_info(path)
    if (channels, width) != (1, 2):
        raise BuildError(f"Для добавления пауз ожидается mono PCM16 WAV: {path}")
    with wave.open(str(path), "rb") as source:
        frames = source.readframes(source.getnframes())
    temporary = path.with_suffix(".padding.wav")
    try:
        with wave.open(str(temporary), "wb") as output:
            output.setnchannels(1)
            output.setsampwidth(2)
            output.setframerate(rate)
            append_silence(output, before_ms, rate)
            output.writeframes(frames)
            append_silence(output, after_ms, rate)
        temporary.replace(path)
    finally:
        temporary.unlink(missing_ok=True)


def wav_info(path: Path) -> tuple[int, int, int, int]:
    with wave.open(str(path), "rb") as audio:
        return (
            audio.getnchannels(),
            audio.getsampwidth(),
            audio.getframerate(),
            audio.getnframes(),
        )


def append_silence(output: wave.Wave_write, milliseconds: int, sample_rate: int) -> None:
    frames = max(0, round(sample_rate * milliseconds / 1000))
    output.writeframes(b"\x00\x00" * frames)


def combine_audio(
    page_clips: list[list[Path]], output_path: Path, settings: dict[str, Any]
) -> list[float]:
    sample_rate = int(settings.get("sample_rate", 48000))
    replica_pause = int(settings.get("pause_between_replicas_ms", 450))
    page_lead = int(settings.get("page_lead_ms", 500))
    page_tail = int(settings.get("page_tail_ms", 800))
    page_durations: list[float] = []
    with wave.open(str(output_path), "wb") as combined:
        combined.setnchannels(1)
        combined.setsampwidth(2)
        combined.setframerate(sample_rate)
        for clips in page_clips:
            start_frames = combined.getnframes()
            append_silence(combined, page_lead, sample_rate)
            for index, clip in enumerate(clips):
                channels, width, rate, _frames = wav_info(clip)
                if (channels, width, rate) != (1, 2, sample_rate):
                    raise BuildError(
                        f"Несовместимый WAV {clip}: ожидается mono PCM16 {sample_rate} Hz"
                    )
                with wave.open(str(clip), "rb") as source:
                    combined.writeframes(source.readframes(source.getnframes()))
                if index + 1 < len(clips):
                    append_silence(combined, replica_pause, sample_rate)
            append_silence(combined, page_tail, sample_rate)
            frames = combined.getnframes() - start_frames
            page_durations.append(max(1.0, frames / sample_rate))
    return page_durations


def ffconcat_quote(path: Path) -> str:
    return str(path.resolve()).replace("\\", "/").replace("'", "'\\''")


def render_video(
    ffmpeg: Path,
    chapter: Path,
    pages: list[dict[str, Any]],
    durations: list[float],
    audio: Path,
    output: Path,
    settings: dict[str, Any],
    build_dir: Path,
) -> None:
    concat_path = build_dir / "pages.ffconcat"
    lines = ["ffconcat version 1.0"]
    for page, duration in zip(pages, durations):
        image = chapter / page["image"]
        lines.extend((f"file '{ffconcat_quote(image)}'", f"duration {duration:.6f}"))
    lines.append(f"file '{ffconcat_quote(chapter / pages[-1]['image'])}'")
    concat_path.write_text("\n".join(lines) + "\n", encoding="utf-8")

    resolution = str(settings.get("resolution", "1920x1080"))
    try:
        width, height = (int(value) for value in resolution.lower().split("x", 1))
    except (ValueError, TypeError) as exc:
        raise BuildError(f"Некорректное разрешение: {resolution}") from exc
    fps = int(settings.get("fps", 30))
    background = str(settings.get("background", "black"))
    video_filter = (
        f"scale={width}:{height}:force_original_aspect_ratio=decrease,"
        f"pad={width}:{height}:(ow-iw)/2:(oh-ih)/2:color={background},"
        "setsar=1,format=yuv420p"
    )
    command = [
        str(ffmpeg), "-y", "-hide_banner", "-loglevel", "warning",
        "-f", "concat", "-safe", "0", "-i", str(concat_path),
        "-i", str(audio), "-vf", video_filter, "-r", str(fps),
        "-c:v", "libx264", "-preset", "medium", "-crf", "20",
        "-c:a", "aac", "-b:a", "192k", "-shortest", "-movflags", "+faststart",
        str(output),
    ]
    log("Сборка видео через FFmpeg...")
    result = subprocess.run(command, text=True)
    if result.returncode:
        raise BuildError(f"FFmpeg завершился с кодом {result.returncode}")


def build(args: argparse.Namespace) -> Path:
    chapter = Path(args.chapter).expanduser().resolve()
    if not chapter.is_dir():
        raise BuildError(f"Папка главы не найдена: {chapter}")
    project, _project_path = load_or_create_project(chapter, args.refresh)
    settings = project.setdefault("settings", {})
    if args.voice:
        settings["default_voice"] = args.voice
    default_voice = str(settings.get("default_voice", "aidar"))
    f5_execution = str(settings.get("f5_execution", "cpu"))
    if f5_execution not in {"cpu", "cuda", "remote"}:
        raise BuildError(f"Некорректный режим F5-TTS: {f5_execution}")
    f5_worker_url = str(settings.get("f5_worker_url", "http://127.0.0.1:8770"))
    f5_worker_timeout = float(settings.get("f5_worker_timeout_seconds", 600))
    model_path = Path(args.model).expanduser().resolve()
    f5_cli = Path(args.f5_cli).expanduser().resolve()
    ffmpeg = find_ffmpeg(args.ffmpeg)

    warnings = project.get("warnings") or []
    for warning in warnings:
        log(f"Внимание: {warning}")
    pages = project.get("pages") or []
    if not pages:
        raise BuildError("В сценарии нет страниц")

    build_dir = chapter / BUILD_DIR
    audio_dir = build_dir / "audio"
    build_dir.mkdir(exist_ok=True)
    audio_dir.mkdir(exist_ok=True)
    page_clips: list[list[Path]] = []
    total_regions = 0
    for page in pages:
        clips: list[Path] = []
        for region in page.get("regions") or []:
            if not region.get("speak", True):
                continue
            text = clean_translation(region.get("tts_text") or region.get("text"))
            if not text:
                continue
            voice = str(region.get("voice") or default_voice)
            engine = str(region.get("engine") or "silero")
            prosody = region.get("prosody") or {}
            synthesis_text = text
            if engine == "f5":
                f5 = region.get("f5") or {}
                synthesis_text = clean_f5_text(text)
                reference_audio = resolve_reference_audio(
                    chapter, str(f5.get("reference_audio") or "")
                )
                reference_text = clean_translation(f5.get("reference_text"))
                if reference_text:
                    reference_text = clean_f5_text(reference_text)
                speed = float(f5.get("speed", 1.0))
                nfe_step = int(f5.get("nfe_step", 32))
                reference_signature = "missing"
                if reference_audio.is_file():
                    stat = reference_audio.stat()
                    reference_signature = (
                        f"{reference_audio}:{stat.st_size}:{stat.st_mtime_ns}"
                    )
                cache_value = (
                    f"f5-russian-hotstone228-v2\0{f5_execution}\0{f5_worker_url}\0"
                    f"{synthesis_text}\0{reference_signature}\0{reference_text}\0"
                    f"{speed}\0{nfe_step}\0"
                    f"{int(prosody.get('pause_before_ms', 0))}\0"
                    f"{int(prosody.get('pause_after_ms', 0))}"
                )
            elif engine == "silero":
                cache_value = f"silero-v5.4\0{voice}\0{make_ssml(text, prosody)}"
            else:
                raise BuildError(f"Неизвестный движок TTS: {engine}")
            digest = hashlib.sha1(cache_value.encode("utf-8")).hexdigest()[:12]
            clip = audio_dir / f"{page['id']}_{region['id']}_{digest}.wav"
            if engine == "f5" and clip.exists() and not wav_has_speech(clip):
                log(f"Внимание: {page['id']}/{region['id']}: удаляю пустой кэш F5-TTS")
                clip.unlink()
            if not clip.exists():
                log(
                    f"Озвучка {page['id']}/{region['id']} "
                    f"({engine}/{voice}): {synthesis_text}"
                )
                if engine == "f5":
                    if f5_execution == "remote":
                        synthesize_f5_remote(
                            synthesis_text, clip, reference_audio, reference_text,
                            speed, nfe_step, f5_worker_url, f5_worker_timeout, prosody,
                        )
                    else:
                        synthesize_f5(
                            synthesis_text, clip, reference_audio, reference_text, speed,
                            nfe_step, f5_cli, ffmpeg, f5_execution, prosody,
                        )
                else:
                    synthesize(synthesis_text, voice, clip, model_path, prosody)
            clips.append(clip)
            total_regions += 1
        page_clips.append(clips)
    if not total_regions:
        raise BuildError("В сценарии нет включённых реплик для озвучки")

    combined_audio = build_dir / "chapter.wav"
    durations = combine_audio(page_clips, combined_audio, settings)
    output = Path(args.output).expanduser().resolve() if args.output else chapter / "chapter.mp4"
    render_video(ffmpeg, chapter, pages, durations, combined_audio, output, settings, build_dir)
    log(f"Готово: {output}")
    return output


def make_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="audiomanga.py",
        description="Локальная озвучка переведённой главы манги",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)
    command = subparsers.add_parser("build", help="озвучить и собрать главу в MP4")
    command.add_argument("chapter", help="папка с PNG и *.analysis.json")
    command.add_argument("--voice", help="голос Silero, например aidar или xenia")
    command.add_argument("--ffmpeg", help="путь к ffmpeg.exe")
    command.add_argument(
        "--model",
        default=str(DEFAULT_SILERO_MODEL),
        help="путь к собственной модели Silero v5.4",
    )
    command.add_argument(
        "--f5-cli",
        default=str(DEFAULT_F5_CLI),
        help="путь к f5-tts_infer-cli.exe",
    )
    command.add_argument("--output", help="выходной MP4; по умолчанию chapter.mp4")
    command.add_argument(
        "--refresh",
        action="store_true",
        help="пересоздать сценарий из analysis.json (ручные правки будут потеряны)",
    )
    command.set_defaults(handler=build)
    return parser


def main() -> int:
    parser = make_parser()
    args = parser.parse_args()
    try:
        args.handler(args)
        return 0
    except BuildError as exc:
        print(f"Ошибка: {exc}", file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        print("Прервано пользователем", file=sys.stderr)
        return 130


if __name__ == "__main__":
    use_local_runtime()
    raise SystemExit(main())
