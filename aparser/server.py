"""Управление A-Parser на этом компьютере: status / start / stop / log.

    python server.py status
    python server.py start [--stoptasks]
    python server.py stop
    python server.py log [-n 40]

Папка A-Parser — APARSER_HOME в .env (по умолчанию C:\\A-parser). start и stop
работают на Windows; status — везде, в том числе с удалённым A-Parser по API.
С 1.2.3390 в дистрибутиве есть aparser-core.exe — его и запускаем: aparser.exe
на Windows Server 2019 молча выходит через ~5 с.
"""

from __future__ import annotations

import argparse
import os
import subprocess
import sys
import time
from pathlib import Path

import aparser as ap

HOME = Path(ap.ENV.get("APARSER_HOME") or "C:/A-parser")
WINDOWS = os.name == "nt"


def exe() -> Path:
    core = HOME / "aparser-core.exe"
    return core if core.exists() else HOME / "aparser.exe"


def processes() -> list[str]:
    if not WINDOWS:
        return []
    out = subprocess.run(["tasklist", "/FI", "IMAGENAME eq aparser*", "/FO", "CSV", "/NH"],
                         capture_output=True, text=True, encoding="cp866", errors="replace").stdout
    return [line.split(",")[0].strip('"') + "#" + line.split(",")[1].strip('"')
            for line in out.splitlines() if line.startswith('"aparser')]


def status() -> int:
    try:
        ap.call("ping", timeout=10)
        alive, why = True, ""
    except ap.APError as e:
        alive, why = False, str(e)
    if WINDOWS:
        print(f"процессы: {', '.join(processes()) or 'нет'}")
    print(f"API {ap.API_URL}: отвечает" if alive else why)
    if alive:
        info = ap.call("info")
        print(f"версия {info.get('version')} · заданий в очереди {info.get('tasksInQueue')} · "
              f"работает {info.get('workingTasks')} · потоков {info.get('activeThreads')} · "
              f"парсеров {len(info.get('availableParsers', []))}")
        line = f"живых прокси: всего {ap.live_proxies()}"
        if ap.PROXY_CHECKER:
            line += f", в проксичекере {ap.PROXY_CHECKER} {ap.live_proxies(ap.PROXY_CHECKER)}"
        print(line)
    bal = ap.capmonster_balance()
    print(f"CapMonster: баланс {'$' + str(bal) if bal is not None else 'не проверен'}")
    return 0 if alive else 1


def start(stoptasks: bool = False) -> int:
    try:
        ap.call("ping", timeout=10)
        print("A-Parser уже работает")
        return 0
    except ap.APError as e:
        why = str(e)
    if WINDOWS and processes():
        # процесс есть, API не отвечает: ещё стартует, неверный пароль или адрес в .env
        print(f"A-Parser уже запущен, но API не отвечает: {why}\n"
              f"Если он только что стартовал — подождите минуту и проверьте python server.py status; "
              f"если завис — python server.py stop, затем start")
        return 1
    if not WINDOWS:
        print("start работает только на Windows. Запустите A-Parser по инструкции "
              "для вашей системы и проверьте: python server.py status")
        return 1
    if not exe().exists():
        print(f"Нет {exe()} — проверьте APARSER_HOME в .env")
        return 1
    args = [str(exe())] + (["-stoptasks"] if stoptasks else [])
    subprocess.Popen(args, cwd=str(HOME), creationflags=subprocess.CREATE_NEW_CONSOLE,
                     stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    for i in range(60):
        time.sleep(3)
        if ap.ping():
            print(f"A-Parser запущен за {3 * (i + 1)} с")
            return 0
    print("Не дождался API за 3 минуты — смотрите python server.py log")
    return 1


def stop() -> int:
    if not WINDOWS:
        print("stop работает только на Windows")
        return 1
    if not processes():
        print("A-Parser не запущен")
        return 0
    subprocess.run(["taskkill", "/F", "/IM", "aparser-core.exe"], capture_output=True)
    subprocess.run(["taskkill", "/F", "/IM", "aparser.exe"], capture_output=True)
    subprocess.run(["taskkill", "/F", "/IM", "aparser-node.exe"], capture_output=True)
    time.sleep(2)
    left = processes()
    print("остановлен" if not left else f"остались процессы: {left}")
    return 0 if not left else 1


def log(n: int = 40) -> int:
    f = HOME / "aparser.log"
    if not f.exists():
        print(f"Нет {f} — проверьте APARSER_HOME в .env")
        return 1
    lines = f.read_text(encoding="utf-8", errors="replace").splitlines()
    print("\n".join(ap.mask(x) for x in lines[-n:]))
    return 0


def main() -> int:
    a = argparse.ArgumentParser(description="Управление A-Parser")
    a.add_argument("cmd", choices=["status", "start", "stop", "log"])
    a.add_argument("--stoptasks", action="store_true", help="start: запустить с остановленными заданиями")
    a.add_argument("-n", type=int, default=40, help="log: сколько последних строк")
    x = a.parse_args()
    if x.cmd == "start":
        return start(x.stoptasks)
    if x.cmd == "log":
        return log(x.n)
    return status() if x.cmd == "status" else stop()


if __name__ == "__main__":
    sys.exit(main())
