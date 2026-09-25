# AudioManga F5 Worker

Автономный HTTP-сервис синтеза русской речи. Используется F5-TTS `1.1.7`,
`F5TTS_Base` и модель `hotstone228/F5-TTS-Russian`.

## Запуск в Docker

CPU (работает везде, но медленно):

```bash
docker compose -f compose.f5-worker.yml --profile cpu up --build
```

NVIDIA CUDA (требуются драйвер NVIDIA, Docker и NVIDIA Container Toolkit):

```bash
docker compose -f compose.f5-worker.yml --profile nvidia up --build
```

Первый синтез скачивает модель в volume `f5-models`. Сервис доступен по адресу
`http://localhost:8770`. Для другого компьютера замените `localhost` его IP и
разрешите TCP-порт 8770 в брандмауэре.

## API

`GET /health` возвращает состояние, настроенное и фактически выбранное устройство,
доступность CUDA и имя GPU.

`POST /synthesize` принимает `multipart/form-data`:

- `text` — уже нормализованный текст реплики;
- `reference_audio` — WAV/MP3 референс;
- `reference_text` — точная расшифровка (можно оставить пустой, но тогда загрузится ASR);
- `speed` — 0.3–2.0, по умолчанию 1.0;
- `nfe_step` — 4–64, по умолчанию 16.

Успешный ответ — mono PCM16 WAV 48 kHz; заголовок `X-F5-Device` содержит `cpu`
или `cuda`.
Пример:

```bash
curl -o speech.wav http://localhost:8770/synthesize \
  -F "text=Я чувствую, что скоро произойдёт нечто важное." \
  -F "reference_text=Иногда тишина говорит намного больше любых слов." \
  -F "speed=1.0" -F "nfe_step=16" \
  -F "reference_audio=@reference.wav"
```

Переменная `F5_DEVICE` принимает `auto`, `cpu` или `cuda`. `auto` выбирает CUDA,
если её видит PyTorch, иначе CPU. `F5_SYNTHESIS_TIMEOUT` задаёт таймаут в секундах,
`F5_MAX_REFERENCE_MB` — максимальный размер референса.

## Локальные тесты

```bash
python -m pip install -r worker/requirements.txt -r worker/requirements-test.txt
python -m pytest worker/tests
```

Тесты не загружают модель и не запускают синтез.
