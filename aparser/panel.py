"""Панель задач: живое состояние долгих запусков любых скиллов в браузере.

    python panel.py                  # http://127.0.0.1:8770/
    python panel.py --port 8771

Команды скилла пишут JSON-файлы запусков в data\\_runs (формат — README, раздел «Панель задач»).
Панель файлы только читает: ничего не удаляет и не меняет. Слушает только 127.0.0.1 —
из интернета её не видно. Результаты открываются только из папки data скилла.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from datetime import datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, quote, urlsplit

import requests

for _s in (sys.stdout, sys.stderr):
    try:
        _s.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, ValueError):
        pass

SKILL = Path(__file__).resolve().parent
RUNS = Path(os.getenv("TASK_PANEL_RUNS") or SKILL / "data" / "_runs")
METHODS = Path(os.getenv("TASK_PANEL_METHODS") or RUNS.parent / "methods")   # реестр методов (python run.py check) — кнопки наверху
DATA_ROOT = (SKILL / "data").resolve()
APARSER_ENV = Path(os.getenv("APARSER_CONFIG") or SKILL / ".env")
STALE_SEC = 30          # «пульс» не обновлялся дольше — процесс, скорее всего, остановлен
SHOW = 40               # сколько последних запусков показывать


def _parse(ts):
    try:
        return datetime.fromisoformat(ts)
    except (TypeError, ValueError):
        return None


def load_runs() -> list[dict]:
    out = []
    for f in sorted(RUNS.glob("*.json"), key=lambda p: p.stat().st_mtime, reverse=True)[:SHOW]:
        try:
            r = json.loads(f.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        now = datetime.now()
        started, beat = _parse(r.get("started")), _parse(r.get("heartbeat") or r.get("updated"))
        end = _parse(r.get("finished")) or now
        r["elapsed"] = int((end - started).total_seconds()) if started else None
        if r.get("status") == "running" and beat and (now - beat).total_seconds() > STALE_SEC:
            r["status"] = "stale"
        stages = r.get("stages") or []
        total = sum(s.get("total") or 0 for s in stages)
        done = sum(min(s.get("done") or 0, s.get("total") or 0) for s in stages)
        good = sum(s.get("ok") or 0 for s in stages)
        r["total"], r["done"], r["good"] = total, done, good
        r["pct"] = round(100 * done / total) if total else (100 if r.get("status") == "done" else 0)
        r["eta"] = int(r["elapsed"] / done * (total - done)) if (r.get("status") == "running" and done and total > done and r["elapsed"]) else None
        r["outputs"] = [{"path": p, "name": Path(p).name, "href": "/open?path=" + quote(p)} for p in r.get("outputs") or []]
        out.append(r)
    order = {"running": 0, "stale": 1}
    out.sort(key=lambda r: (order.get(r.get("status"), 2), r.get("started") or ""), reverse=False)
    running = [r for r in out if r.get("status") in ("running", "stale")]
    rest = sorted([r for r in out if r.get("status") not in ("running", "stale")], key=lambda r: r.get("started") or "", reverse=True)
    return running + rest


def load_methods() -> list[dict]:
    """Реестры методов (кнопки наверху): у каждого метода статус последней проверки."""
    out = []
    for f in sorted(METHODS.glob("*.json")):
        try:
            out.append(json.loads(f.read_text(encoding="utf-8")))
        except (OSError, ValueError):
            continue
    return out


_ap_cache = {"t": 0, "data": None}


def aparser_state() -> dict | None:
    """Состояние A-Parser, если он настроен на этой машине (кэш 10 с, баланс капчи — раз в минуту)."""
    if not APARSER_ENV.exists():
        return None
    if time.time() - _ap_cache["t"] < 10 and _ap_cache["data"]:
        return _ap_cache["data"]
    env = {}
    for line in APARSER_ENV.read_text(encoding="utf-8-sig").splitlines():
        if "=" in line and not line.lstrip().startswith("#"):
            k, v = line.split("=", 1)
            env[k.strip()] = v.strip()
    url, pwd = env.get("APARSER_API_URL") or "http://127.0.0.1:9091/API", env.get("APARSER_API_PASSWORD", "")

    def call(action, data=None):
        body = {"password": pwd, "action": action, **({"data": data} if data is not None else {})}
        return requests.post(url, json=body, timeout=8).json().get("data")

    st = {"alive": False}
    try:
        info = call("info") or {}
        st.update(alive=True, version=info.get("version"), threads=info.get("activeThreads"),
                  queue=info.get("tasksInQueue"), working=info.get("workingTasks"))
        st["proxies"] = len(call("getProxies") or {})
    except (requests.RequestException, ValueError):
        pass
    prev = _ap_cache["data"] or {}
    if time.time() - prev.get("bal_t", 0) > 60 and env.get("CAPMONSTER_KEY"):
        try:
            st["captcha"] = requests.post("https://api.capmonster.cloud/getBalance", json={"clientKey": env["CAPMONSTER_KEY"]},
                                          timeout=8).json().get("balance")
        except (requests.RequestException, ValueError):
            st["captcha"] = None
        st["bal_t"] = time.time()
    else:
        st["captcha"], st["bal_t"] = prev.get("captcha"), prev.get("bal_t", 0)
    _ap_cache.update(t=time.time(), data=st)
    return st


class Server(ThreadingHTTPServer):
    # на Windows SO_REUSEADDR даёт сесть на уже занятый порт — запрещаем
    allow_reuse_address = False


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *args):
        pass

    def _send(self, code, body: bytes, ctype: str, extra: dict | None = None):
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Cache-Control", "no-store")
        for k, v in (extra or {}).items():
            self.send_header(k, v)
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        u = urlsplit(self.path)
        if u.path == "/":
            return self._send(200, (SKILL / "panel.html").read_bytes(), "text/html; charset=utf-8")
        if u.path == "/api/state":
            body = json.dumps({"now": datetime.now().isoformat(timespec="seconds"), "runs_dir": str(RUNS),
                               "runs": load_runs(), "methods": load_methods(), "aparser": aparser_state()}, ensure_ascii=False, default=str)
            return self._send(200, body.encode("utf-8"), "application/json; charset=utf-8")
        if u.path == "/open":
            p = Path(parse_qs(u.query).get("path", [""])[0])
            try:
                rp = p.resolve()
                rp.relative_to(DATA_ROOT)
            except (ValueError, OSError):
                return self._send(403, "Можно открывать только файлы из папки data скилла".encode("utf-8"), "text/plain; charset=utf-8")
            if not rp.is_file():
                return self._send(404, "Файла нет".encode("utf-8"), "text/plain; charset=utf-8")
            ctype = {".xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet", ".json": "application/json",
                     ".png": "image/png", ".csv": "text/csv"}.get(rp.suffix.lower(), "application/octet-stream")
            disp = "inline" if rp.suffix.lower() in (".png", ".json") else "attachment"
            return self._send(200, rp.read_bytes(), ctype, {"Content-Disposition": f"{disp}; filename*=UTF-8''{quote(rp.name)}"})
        return self._send(404, b"not found", "text/plain")


def main() -> int:
    a = argparse.ArgumentParser(description="Панель задач")
    a.add_argument("--port", type=int, default=8770)
    x = a.parse_args()
    RUNS.mkdir(parents=True, exist_ok=True)
    try:
        srv = Server(("127.0.0.1", x.port), Handler)
    except OSError:
        print(f"Порт {x.port} занят — запустите с другим: python panel.py --port {x.port + 1}", flush=True)
        return 1
    print(f"Панель задач: http://127.0.0.1:{x.port}/  (запуски — {RUNS})", flush=True)
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        pass
    return 0


if __name__ == "__main__":
    sys.exit(main())
