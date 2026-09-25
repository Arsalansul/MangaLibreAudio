# AudioManga

Локальная сборка озвученной главы манги из готовых страниц и файлов
`*.analysis.json`, создаваемых переводчиком манги.

## Веб-интерфейс

Запустите двойным щелчком `start.bat` или из терминала:

```powershell
.\start.bat
```

Можно сразу передать папку главы:

```powershell
.\start.bat "E:\MangaTranslateProjects\Bleach\out\chapter"
```

Интерфейс открывается по адресу `http://127.0.0.1:8765`. В нём можно выбрать
папку, настроить текст, голос, персонажа, темп, высоту и паузы каждой реплики,
сохранить сценарий и запустить сборку видео.

## Формат входной папки

Для каждой страницы должны находиться два файла с одинаковым именем:

```text
0006.png
0006.analysis.json
```

Текст берётся из `regions[].translation`. Страницы без готового изображения
пропускаются с предупреждением.

## Запуск

```powershell
python -m venv .venv
.\.venv\Scripts\python -m pip install -r requirements.txt
python audiomanga.py build "E:\MangaTranslateProjects\Bleach\out\chapter"
```

Если `.venv` уже создано, обычный системный Python автоматически перезапустит
команду через окружение AudioManga. Активировать окружение перед каждым запуском
необязательно.

Первый запуск создаёт рядом со страницами файл `audiomanga.project.json`.
Перед повторным запуском в нём можно менять порядок реплик, текст, голос и флаг
`speak`. Аудио кешируется в `.audiomanga/audio`, результат сохраняется как
`chapter.mp4`.

По умолчанию используется собственная модель AudioManga
`.runtime/models/v5_4_ru.pt` и голос `aidar`. Проект не обращается к файлам
других приложений. Альтернативы:

```powershell
python audiomanga.py build "ПАПКА" --voice xenia
python audiomanga.py build "ПАПКА" --model "D:\Models\v5_4_ru.pt"
python audiomanga.py build "ПАПКА" --ffmpeg "D:\Tools\ffmpeg\bin\ffmpeg.exe"
```

## Интонация и паузы

У каждой реплики в `audiomanga.project.json` есть отдельный текст для
произношения и настройки SSML:

```json
{
  "text": "Я тебя вижу...",
  "tts_text": "Я тебя в+ижу...",
  "voice": "aidar",
  "prosody": {
    "rate": "slow",
    "pitch": "low",
    "pause_before_ms": 150,
    "pause_after_ms": 400
  }
}
```

Допустимый темп: `x-slow`, `slow`, `medium`, `fast`, `x-fast`; высота:
`x-low`, `low`, `medium`, `high`, `x-high`. Знак `+` перед ударной гласной
задаёт ручное ударение. Поле `text` можно оставить без изменений — озвучивается
`tts_text`.

`--refresh` заново формирует сценарий из файлов анализа и удаляет из него
ручные изменения.

## FFmpeg

Нужна готовая Windows-сборка с файлом `ffmpeg.exe`. Исходный репозиторий FFmpeg
сам по себе не содержит исполняемого файла. По умолчанию проверяются `PATH`,
переменная `AUDIOMANGA_FFMPEG` и путь `E:\ffmpeg\bin\ffmpeg.exe`.
