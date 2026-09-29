"""Панель задач: живое состояние долгих запусков любых скиллов в браузере.

    python panel.py                  # http://127.0.0.1:8770/
    python panel.py --port 8771

Команды скилла пишут JSON-файлы запусков в data\\_runs (формат — README, раздел «Панель задач»).
Панель файлы читает и ничего не удаляет. Единственное действие — по кнопке запускает проверку
методов скилла командой из его реестра (поле check): одна проверка за раз на скилл, остальное — в
очереди; «остановить» гасит проверку и помечает её запуск остановленным. Слушает только 127.0.0.1 —
из интернета её не видно; запуск принимается только со страницы самой панели. Результаты
открываются только из папки data скилла.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import signal
import subprocess
import sys
import threading
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


def alive_runs() -> dict[str, dict]:
    """Запуски, которые идут прямо сейчас (статус running и свежий пульс), по id."""
    out, now = {}, time.time()
    for f in RUNS.glob("*.json"):
        try:
            if now - f.stat().st_mtime > 2 * STALE_SEC:
                continue
            r = json.loads(f.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        beat = _parse(r.get("heartbeat") or r.get("updated"))
        if r.get("status") == "running" and beat and (datetime.now() - beat).total_seconds() <= STALE_SEC:
            out[r.get("id") or f.stem] = r
    return out


def registries() -> dict[str, dict]:
    """Реестры методов скиллов по имени скилла."""
    out = {}
    for f in sorted(METHODS.glob("*.json")):
        try:
            r = json.loads(f.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        out[r.get("skill") or f.stem] = r
    return out


def check_spec(reg: dict):
    """Команда проверки из реестра: (python, папка, аргументы) — или None, если её нет или она подозрительная."""
    c = reg.get("check") or {}
    cwd, args = Path(c.get("cwd") or "."), c.get("args") or []
    py = Path(c.get("python") or sys.executable)
    if not (c.get("cwd") and cwd.is_dir() and args and all(isinstance(a, str) for a in args)
            and (cwd / args[0]).is_file() and "{ids}" in args and py.is_file() and py.name.lower().startswith("python")):
        return None
    return py, cwd, args


def load_methods(alive: dict[str, dict]) -> list[dict]:
    """Реестры методов (кнопки наверху): у каждого метода статус последней проверки."""
    out = []
    for r in registries().values():
        for m in r.get("methods", []):
            # «идёт», а проверки уже нет (закрыли, упала) — показать прежний итог
            if m.get("status") == "running" and m.get("run") not in alive:
                m["status"] = m.get("prev_status") or "unknown"
                m["note"] = "проверку прервали — показан прежний итог" + (f": {m['prev_note']}" if m.get("prev_note") else "")
        r["checkable"] = check_spec(r) is not None
        r.pop("check", None)
        out.append(r)
    return out


# --- проверка методов по кнопке: очередь, одна проверка за раз на скилл ---

CHECK_LOGS = RUNS.parent / "checks"
ID_RE = re.compile(r"^[\w.:-]{1,80}$")
_checks: dict[str, dict] = {}     # скилл → {"proc", "current", "queue", "started", "error"}
_ck_lock = threading.Lock()


def _ck(skill: str) -> dict:
    return _checks.setdefault(skill, {"proc": None, "current": [], "queue": [], "started": None, "error": None})


def pump():
    """Запустить очередь, если скилл свободен: ни своей проверки, ни проверки из терминала."""
    alive = alive_runs()
    busy_ext = {r.get("skill") for r in alive.values() if r.get("command") == "check"}
    regs = registries()
    with _ck_lock:
        for skill, st in _checks.items():
            p = st["proc"]
            if p and p.poll() is not None:
                st["proc"], st["current"] = None, []
            if not st["queue"] or st["proc"] or skill in busy_ext:
                continue
            spec = check_spec(regs.get(skill, {}))
            if not spec:
                st["queue"], st["error"] = [], "в реестре нет команды проверки"
                continue
            py, cwd, args = spec
            ids, st["queue"] = st["queue"], []
            argv = [str(py)] + [a.replace("{ids}", ",".join(ids)) for a in args]
            CHECK_LOGS.mkdir(parents=True, exist_ok=True)
            log_path = CHECK_LOGS / f"{skill}_{datetime.now():%Y%m%d-%H%M%S}.log"
            with open(log_path, "w", encoding="utf-8") as log:
                kw = ({"creationflags": subprocess.CREATE_NEW_PROCESS_GROUP | subprocess.CREATE_NO_WINDOW} if os.name == "nt"
                      else {"start_new_session": True})
                st["proc"] = subprocess.Popen(argv, cwd=str(cwd), stdout=log, stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL,
                                              env={**os.environ, "PYTHONIOENCODING": "utf-8"}, **kw)
            st["current"], st["started"], st["error"] = ids, datetime.now().isoformat(timespec="seconds"), None
            print(f"проверка {skill}: {len(ids)} методов, лог — {log_path}", flush=True)


def _pump_loop():
    while True:
        try:
            pump()
        except Exception as e:                      # очередь не должна ронять панель
            print("очередь проверок:", e, flush=True)
        time.sleep(2)


def enqueue(skill: str, ids: list) -> tuple[int, dict]:
    reg = registries().get(skill or "")
    if not reg:
        return 404, {"error": f"нет реестра методов «{skill}»"}
    if not check_spec(reg):
        return 400, {"error": "в реестре нет команды проверки — один раз запустите проверку скилла из терминала новой версией"}
    known = {m.get("id") for m in reg.get("methods", []) if m.get("id") and m.get("example")}
    ids = [i for i in dict.fromkeys(ids if isinstance(ids, list) else []) if isinstance(i, str) and ID_RE.match(i) and i in known]
    if not ids:
        return 400, {"error": "таких методов в реестре нет"}
    with _ck_lock:
        st = _ck(skill)
        add = [i for i in ids if i not in st["queue"] and i not in st["current"]]
        st["queue"] += add
    pump()
    return 200, {"queued": len(add)}


def _mark_stopped(pid: int):
    """Файл запуска остановленной проверки: «ошибка — остановлено из панели», иначе висел бы «не отвечает»."""
    for rid, r in alive_runs().items():
        if r.get("pid") != pid:
            continue
        r.update(status="error", error="остановлено кнопкой в панели", finished=datetime.now().isoformat(timespec="seconds"))
        path = RUNS / f"{rid}.json"
        tmp = path.with_suffix(".tmp")
        tmp.write_text(json.dumps(r, ensure_ascii=False, indent=1), encoding="utf-8")
        os.replace(tmp, path)


def stop(skill: str) -> tuple[int, dict]:
    with _ck_lock:
        st = _ck(skill or "")
        dropped, st["queue"] = len(st["queue"]), []
        p = st["proc"]
    killed = False
    if p and p.poll() is None:
        if os.name == "nt":           # вместе с дочерними run.py
            subprocess.run(["taskkill", "/T", "/F", "/PID", str(p.pid)], capture_output=True, creationflags=subprocess.CREATE_NO_WINDOW)
        else:
            os.killpg(p.pid, signal.SIGKILL)
        killed = True
        try:
            p.wait(timeout=10)
        except subprocess.TimeoutExpired:
            pass
        _mark_stopped(p.pid)
    with _ck_lock:
        st["proc"], st["current"] = None, []
    return 200, {"stopped": killed, "dropped": dropped}


def checks_state(alive: dict[str, dict]) -> dict:
    ext = {r.get("skill") for r in alive.values() if r.get("command") == "check"}
    with _ck_lock:
        out = {s: {"current": st["current"], "queue": st["queue"], "started": st["started"], "error": st["error"],
                   "running": bool(st["proc"] and st["proc"].poll() is None)} for s, st in _checks.items()}
    for s in ext:       # проверка, запущенная не из панели (из терминала или прошлой панелью)
        out.setdefault(s, {"current": [], "queue": [], "started": None, "error": None, "running": False})
        out[s]["external"] = not out[s]["running"]
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

    def _json(self, code: int, obj):
        return self._send(code, json.dumps(obj, ensure_ascii=False, default=str).encode("utf-8"), "application/json; charset=utf-8")

    def _own_page(self) -> bool:
        """Запрос со страницы самой панели. Чужой сайт, открытый в том же браузере, не должен
        запускать проверки: у него другой Origin, а заголовок X-Panel без разрешения CORS он не пошлёт."""
        port = self.server.server_address[1]
        hosts = {f"127.0.0.1:{port}", f"localhost:{port}"}
        origin = self.headers.get("Origin")
        return (self.headers.get("Host") in hosts and self.headers.get("X-Panel") == "1"
                and (not origin or origin in {f"http://{h}" for h in hosts}))

    def do_POST(self):
        u = urlsplit(self.path)
        if not self._own_page():
            return self._json(403, {"error": "только со страницы панели"})
        n = int(self.headers.get("Content-Length") or 0)
        if n > 100_000:
            return self._json(413, {"error": "слишком большой запрос"})
        try:
            body = json.loads(self.rfile.read(n) or b"{}")
        except ValueError:
            return self._json(400, {"error": "не JSON"})
        if u.path == "/api/check":
            return self._json(*enqueue(body.get("skill"), body.get("ids")))
        if u.path == "/api/check/stop":
            return self._json(*stop(body.get("skill")))
        return self._json(404, {"error": "not found"})

    def do_GET(self):
        u = urlsplit(self.path)
        if u.path == "/":
            return self._send(200, (SKILL / "panel.html").read_bytes(), "text/html; charset=utf-8")
        if u.path == "/api/state":
            alive = alive_runs()
            return self._json(200, {"now": datetime.now().isoformat(timespec="seconds"), "runs_dir": str(RUNS),
                                    "runs": load_runs(), "methods": load_methods(alive), "checks": checks_state(alive),
                                    "aparser": aparser_state()})
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
    threading.Thread(target=_pump_loop, daemon=True).start()
    print(f"Панель задач: http://127.0.0.1:{x.port}/  (запуски — {RUNS})", flush=True)
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        pass
    return 0


if __name__ == "__main__":
    sys.exit(main())
