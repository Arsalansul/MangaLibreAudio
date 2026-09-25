from pathlib import Path
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

from worker.app import app, build_command, select_device


def test_auto_selects_cpu_without_cuda(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("F5_DEVICE", "auto")
    with patch("worker.app.torch.cuda.is_available", return_value=False):
        assert select_device() == "cpu"


def test_cuda_request_fails_when_unavailable() -> None:
    with patch("worker.app.torch.cuda.is_available", return_value=False):
        with pytest.raises(ValueError, match="CUDA"):
            select_device("cuda")


def test_command_uses_russian_model() -> None:
    with patch("worker.app._cli", return_value="f5-cli"):
        command = build_command(Path("ref.wav"), Path("out"), "Привет", "Текст", 1.0, 16, "cpu")
    assert "hotstone228/F5-TTS-Russian/model_last.safetensors" in " ".join(command)
    assert command[command.index("--device") + 1] == "cpu"


def test_command_accepts_resolved_local_model_files() -> None:
    with patch("worker.app._cli", return_value="f5-cli"):
        command = build_command(
            Path("ref.wav"), Path("out"), "Привет", "Текст", 1.0, 16, "cuda",
            Path("/models/model.safetensors"), Path("/models/vocab.txt"),
        )
    assert command[command.index("--ckpt_file") + 1] == "/models/model.safetensors"
    assert command[command.index("--vocab_file") + 1] == "/models/vocab.txt"


def test_empty_text_is_rejected() -> None:
    client = TestClient(app)
    response = client.post(
        "/synthesize",
        data={"text": " ", "reference_text": "", "speed": "1", "nfe_step": "16"},
        files={"reference_audio": ("ref.wav", b"audio", "audio/wav")},
    )
    assert response.status_code == 422
