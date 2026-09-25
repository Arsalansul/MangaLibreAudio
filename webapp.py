#!/usr/bin/env python3
"""Local browser UI for editing and building AudioManga projects."""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import threading
import webbrowser
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import unquote, urlparse

import audiomanga


ROOT = Path(__file__).resolve().parent
WEB_ROOT = ROOT / "web"
DEFAULT_CHAPTER = Path(r"E:\MangaTranslateProjects\Bleach\out\chapter")
VOICES = ["aidar", "baya", "kseniya", "xenia"]
RATES = ["x-slow", "slow", "medium", "fast", "x-fast"]
PITCHES = ["x-low", "low", "medium", "high", "x-high"]


class AppState:
    def __init__(self, chapter: Path | None = None) -> None:
        self.lock = threading.RLock()
        self.chapter = chapter
        self.project: dict | None = None
        self.building = False
        self.build_log: list[str] = []
        self.build_ok: bool | None = None
        if chapter and chapter.is_dir():
            self.open_chapter(chapter)

    def open_chapter(self, chapter: Path) -> dict:
        chapter = chapter.expanduser().resolve()
        if not chapter.is_dir():
            raise audiomanga.BuildError(f"Папка не найдена: {chapter}")
        project, _ = audiomanga.load_or_create_project(chapter, refresh=False)
        with self.lock:
            self.chapter = chapter
            self.project = project
        return project

    def save(self, project: dict) -> None:
        with self.lock:
            if self.chapter is None:
                raise audiomanga.BuildError("Сначала выберите папку главы")
            if not isinstance(project.get("pages"), list):
                raise audiomanga.BuildError("Некорректный сценарий: отсутствует pages")
            project["chapter"] = str(self.chapter)
            audiomanga.write_json(self.chapter / audiomanga.PROJECT_FILE, project)
            self.project = project

    def start_build(self) -> None:
        with self.lock:
            if self.building:
                raise audiomanga.BuildError("Сборка уже выполняется")
            if self.chapter is None or self.project is None:
                raise audiomanga.BuildError("Сначала выберите папку главы")
            self.save(self.project)
            self.building = True
            self.build_ok = None
            self.build_log = ["Запуск сборки..."]
            chapter = self.chapter
        threading.Thread(target=self._build_worker, args=(chapter,), daemon=True).start()

    def _build_worker(self, chapter: Path) -> None:
        environment = os.environ.copy()
        environment["PYTHONUTF8"] = "1"
        command = [sys.executable, str(ROOT / "audiomanga.py"), "build", str(chapter)]
        try:
            process = subprocess.Popen(
                command,
                cwd=str(ROOT),
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                encoding="utf-8",
                errors="replace",
                env=environment,
            )
            assert process.stdout is not None
            for line in process.stdout:
                with self.lock:
                    self.build_log.append(line.rstrip())
            return_code = process.wait()
            with self.lock:
                self.build_ok = return_code == 0
                self.build_log.append(
                    "Сборка завершена." if self.build_ok else f"Ошибка сборки, код {return_code}."
                )
        except Exception as exc:
            with self.lock:
                self.build_ok = False
                self.build_log.append(f"Ошибка запуска: {exc}")
        finally:
            with self.lock:
                self.building = False

    def payload(self) -> dict:
        with self.lock:
            return {
                "chapter": str(self.chapter) if self.chapter else "",
                "project": self.project,
                "voices": VOICES,
                "rates": RATES,
                "pitches": PITCHES,
                "building": self.building,
                "build_ok": self.build_ok,
                "build_log": self.build_log,
            }


class Handler(BaseHTTPRequestHandler):
    server_version = "AudioManga/1.0"

    @property
    def state(self) -> AppState:
        return self.server.state  # type: ignore[attr-defined]

    def log_message(self, format: str, *args: object) -> None:
        return

    def send_json(self, value: object, status: int = 200) -> None:
        body = json.dumps(value, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def read_json(self) -> dict:
        length = int(self.headers.get("Content-Length", "0"))
        if length > 10_000_000:
            raise audiomanga.BuildError("Слишком большой запрос")
        try:
            value = json.loads(self.rfile.read(length).decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise audiomanga.BuildError("Некорректный JSON") from exc
        if not isinstance(value, dict):
            raise audiomanga.BuildError("Ожидался JSON-объект")
        return value

    def do_GET(self) -> None:
        path = urlparse(self.path).path
        if path == "/api/state":
            self.send_json(self.state.payload())
            return
        if path.startswith("/api/page/"):
            self.serve_page_image(unquote(path.removeprefix("/api/page/")))
            return
        static = {"/": "index.html", "/app.js": "app.js", "/styles.css": "styles.css"}
        if path in static:
            self.serve_static(static[path])
            return
        self.send_error(HTTPStatus.NOT_FOUND)

    def do_POST(self) -> None:
        try:
            if self.path == "/api/open":
                data = self.read_json()
                self.state.open_chapter(Path(str(data.get("chapter", ""))))
                self.send_json(self.state.payload())
            elif self.path == "/api/browse":
                selected = choose_directory(self.state.chapter)
                if selected:
                    self.state.open_chapter(selected)
                self.send_json(self.state.payload())
            elif self.path == "/api/save":
                data = self.read_json()
                self.state.save(data.get("project") or {})
                self.send_json({"ok": True})
            elif self.path == "/api/build":
                data = self.read_json()
                self.state.save(data.get("project") or {})
                self.state.start_build()
                self.send_json({"ok": True}, HTTPStatus.ACCEPTED)
            else:
                self.send_error(HTTPStatus.NOT_FOUND)
        except (audiomanga.BuildError, OSError, ValueError) as exc:
            self.send_json({"error": str(exc)}, HTTPStatus.BAD_REQUEST)

    def serve_static(self, name: str) -> None:
        content_types = {
            ".html": "text/html; charset=utf-8",
            ".js": "text/javascript; charset=utf-8",
            ".css": "text/css; charset=utf-8",
        }
        path = WEB_ROOT / name
        body = path.read_bytes()
        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", content_types[path.suffix])
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def serve_page_image(self, name: str) -> None:
        with self.state.lock:
            chapter = self.state.chapter
            project = self.state.project
        allowed = {page.get("image") for page in (project or {}).get("pages", [])}
        if chapter is None or name not in allowed or Path(name).name != name:
            self.send_error(HTTPStatus.NOT_FOUND)
            return
        path = chapter / name
        if not path.is_file():
            self.send_error(HTTPStatus.NOT_FOUND)
            return
        types = {".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".webp": "image/webp"}
        body = path.read_bytes()
        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", types.get(path.suffix.lower(), "application/octet-stream"))
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


def choose_directory(initial: Path | None) -> Path | None:
    try:
        import tkinter as tk
        from tkinter import filedialog

        root = tk.Tk()
        root.withdraw()
        root.attributes("-topmost", True)
        value = filedialog.askdirectory(
            title="Выберите папку главы манги",
            initialdir=str(initial or DEFAULT_CHAPTER.parent),
        )
        root.destroy()
        return Path(value) if value else None
    except Exception as exc:
        raise audiomanga.BuildError(f"Не удалось открыть выбор папки: {exc}") from exc


def main() -> int:
    parser = argparse.ArgumentParser(description="Веб-интерфейс AudioManga")
    parser.add_argument("chapter", nargs="?", help="папка главы, открываемая при запуске")
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--no-browser", action="store_true")
    args = parser.parse_args()
    chapter = Path(args.chapter).resolve() if args.chapter else (DEFAULT_CHAPTER if DEFAULT_CHAPTER.is_dir() else None)
    state = AppState(chapter)
    server = ThreadingHTTPServer(("127.0.0.1", args.port), Handler)
    server.state = state  # type: ignore[attr-defined]
    url = f"http://127.0.0.1:{args.port}"
    print(f"AudioManga: {url}")
    print("Для остановки нажмите Ctrl+C.")
    if not args.no_browser:
        threading.Timer(0.6, lambda: webbrowser.open(url)).start()
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
    return 0


if __name__ == "__main__":
    audiomanga.use_local_runtime()
    raise SystemExit(main())
