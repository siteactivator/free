"""Состояние запуска для панели задач (panel.py в папке скилла).

Каждый запуск пишет JSON в data\\_runs\\<id>.json в папке скилла (или TASK_PANEL_RUNS):
команда, этапы с прогрессом, удачные/неудачные, файлы результатов, хвост лога, «пульс» раз в 5 с.
Формат описан в README, раздел «Панель задач».
Панель не нужна для работы скилла: если папку не создать, запуск просто идёт без неё.
"""

from __future__ import annotations

import atexit
import json
import os
import sys
import threading
import time
from datetime import datetime
from pathlib import Path

RUNS = Path(os.getenv("TASK_PANEL_RUNS") or Path(__file__).resolve().parent / "data" / "_runs")


def _now() -> str:
    return datetime.now().isoformat(timespec="seconds")


class Run:
    def __init__(self, skill: str, command: str, argv: list[str]):
        self.lock = threading.Lock()
        self.id = f"{datetime.now():%Y%m%d-%H%M%S}-{os.getpid()}"
        self.path = RUNS / f"{self.id}.json"
        self.data = {"id": self.id, "skill": skill, "command": command, "args": " ".join(argv), "pid": os.getpid(),
                     "status": "running", "started": _now(), "updated": _now(), "heartbeat": _now(), "finished": None,
                     "stages": [], "outputs": [], "log": [], "error": None}
        self.ok = True
        try:
            RUNS.mkdir(parents=True, exist_ok=True)
            self._write()
        except OSError:
            self.ok = False
        self._stop = threading.Event()
        threading.Thread(target=self._beat, daemon=True).start()

    def _write(self):
        if not self.ok:
            return
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(json.dumps(self.data, ensure_ascii=False, indent=1), encoding="utf-8")
        os.replace(tmp, self.path)

    def _beat(self):
        while not self._stop.wait(5):
            with self.lock:
                self.data["heartbeat"] = _now()
                self._write()

    def stage(self, name: str, total: int) -> int:
        with self.lock:
            self.data["stages"].append({"name": name, "total": total, "done": 0, "ok": 0, "started": _now(), "finished": None})
            self.data["updated"] = _now()
            self._write()
            return len(self.data["stages"]) - 1

    def advance(self, idx: int, done: int, ok: int):
        with self.lock:
            st = self.data["stages"][idx]
            st["done"], st["ok"] = done, ok
            if done >= st["total"]:
                st["finished"] = _now()
            self.data["updated"] = _now()
            self._write()

    def title(self, text: str):
        """Заголовок карточки в панели вместо «скилл · команда» — например, что именно проверяли."""
        with self.lock:
            self.data["title"] = text
            self._write()

    def output(self, path: str):
        with self.lock:
            self.data["outputs"].append(str(path))
            self.data["updated"] = _now()
            self._write()

    def log(self, line: str):
        line = line.rstrip()
        if not line:
            return
        with self.lock:
            self.data["log"] = (self.data["log"] + [line])[-40:]
            self.data["updated"] = _now()
            self._write()

    def finish(self, status: str = "done", error: str | None = None):
        self._stop.set()
        with self.lock:
            if self.data["status"] != "running":
                return
            self.data.update(status=status, finished=_now(), updated=_now(), error=error)
            self._write()


class _Tee:
    """Дублирует вывод в лог запуска."""

    def __init__(self, stream, run: Run):
        self.stream, self.run, self.buf = stream, run, ""

    def write(self, s):
        self.stream.write(s)
        self.buf += s
        while "\n" in self.buf:
            line, self.buf = self.buf.split("\n", 1)
            self.run.log(line)
        return len(s)

    def flush(self):
        self.stream.flush()

    def __getattr__(self, name):
        return getattr(self.stream, name)


CURRENT: Run | None = None


def start(skill: str, command: str, argv: list[str]) -> Run:
    global CURRENT
    CURRENT = Run(skill, command, argv)
    sys.stdout = _Tee(sys.stdout, CURRENT)
    atexit.register(lambda: CURRENT and CURRENT.finish("done"))
    return CURRENT
