"""Проверка всех методов скилла на маленьких запросах → реестр методов для панели задач.

    python run.py check                    # все методы и заготовки, по 4 одновременно
    python run.py check --only serp,ahrefs # только методы, чьё имя начинается так
    python run.py check --no-stubs --workers 3 --timeout 20

Каждый метод запускается отдельной командой скилла (python run.py …) на одном-двух запросах.
Итог — data\\methods\\aparser.json в папке скилла: у каждого метода статус
(работает / частично / не работает / таймаут / нужен доступ), время, заметка и пример команды.
Панель задач показывает его полосой кнопок наверху. Результаты проверок — в data\\_check\\.
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
from pathlib import Path

import aparser as ap
import progress as pg
from cmd_stubs import STUBS

SKILL_NAME = "aparser"
METHODS_DIR = Path(os.getenv("TASK_PANEL_METHODS") or pg.RUNS.parent / "methods")
JPG = "https://upload.wikimedia.org/wikipedia/commons/3/3f/JPEG_example_flower.jpg"
AIQ = "Что такое SEO? Ответь одним предложением."
RU, EN = "пластиковые окна", "plastic windows"


def _methods() -> list[dict]:
    """(группа, имя кнопки, аргументы run.py, что делает)."""
    m = []
    add = lambda group, name, argv, what: m.append({"group": group, "name": name, "argv": argv, "what": what})
    for e in ("yandex", "google", "bing", "duckduckgo", "yahoo", "aol", "rambler", "seznam", "you"):
        add("Выдача", e, ["serp", "--engine", e, "-q", RU if e not in ("yahoo", "aol", "seznam", "you") else EN], f"выдача {e}")
    add("Выдача", "positions", ["positions", "--engine", "duckduckgo", "--site", "okna.ru,veka.ru", "-q", RU], "позиции сайтов (по выдаче)")
    add("Выдача", "index", ["index", "--site", "kremlin.ru"], "страниц в индексе Яндекса (site:)")
    for e in ("yandex", "google", "bing", "yahoo", "youtube", "pinterest", "trends"):
        add("Подсказки", e, ["suggest", "--engine", e, "-q", RU], f"подсказки {e}")
    for e in ("bukvarix", "ahrefs"):
        add("Ключевые слова", e, ["keywords", "--source", e, "-q", RU, "--limit", "50"], f"расширение фраз: {e}")
    add("Ключевые слова", "domain-keywords", ["domain-keywords", "-q", "lenta.ru", "--limit", "50"], "фразы домена (Букварикс)")
    add("Ключевые слова", "kd", ["kd", "-q", RU], "сложность фразы (Ahrefs)")
    add("Ключевые слова", "trends", ["trends", "-q", RU], "Google Trends")
    add("Ключевые слова", "ads", ["ads", "-q", RU], "объявления Яндекс Директа")
    sample = {"whois": "example.com", "archive": "example.com", "dns": "example.com", "rkn": "example.com", "cms": "wordpress.org",
              "rkn_ap": "rutracker.org", "sqi": "kremlin.ru", "hosting": "example.com"}
    for c in ("whois", "archive", "rkn", "rkn_ap", "sqi", "dns", "hosting", "cms", "ahrefs", "traffic", "moz", "majestic", "mustat",
              "safe_google", "safe_yandex", "hacked", "curlie", "social", "radar", "trails"):
        add("Проверка доменов", c, ["domains", "--checks", c, "-q", sample.get(c, "wikipedia.org")], f"domains --checks {c}")
    add("Ссылки и домены", "ahrefs", ["ahrefs", "-q", "wikipedia.org"], "DR, ASpamRank, 20 доноров")
    add("Ссылки и домены", "traffic", ["traffic", "-q", "wikipedia.org"], "трафик по Ahrefs")
    add("Ссылки и домены", "broken", ["broken", "-q", "a-parser.com"], "битые ссылки по Ahrefs")
    add("Ссылки и домены", "moz", ["moz", "-q", "wikipedia.org"], "MOZ подробно")
    add("Ссылки и домены", "backlink", ["backlink", "-q", "https://example.com/ https://iana.org/domains/example"], "ссылка на доноре")
    add("Ссылки и домены", "ip", ["ip", "-q", "8.8.8.8"], "IP и хостинг")
    add("Ссылки и домены", "trails", ["trails", "-q", "example.com"], "SecurityTrails без логина")
    add("Страницы", "http", ["http", "-q", "https://example.com/"], "код ответа, title")
    add("Страницы", "http-browser", ["http", "--browser", "-q", "https://example.com/"], "через Chrome")
    for w in ("article", "text", "links", "lang"):
        add("Страницы", w, ["extract", "--what", w, "-q", "https://en.wikipedia.org/wiki/Search_engine_optimization" if w == "article" else "https://example.com/"], f"extract {w}")
    add("Страницы", "screenshot", ["screenshot", "-q", "https://example.com/"], "скриншот")
    for e in ("google", "bing", "deepl"):
        add("Тексты", e, ["translate", "--engine", e, "-q", "Hello, world"], f"перевод {e}")
    add("Тексты", "speller", ["speller", "-q", "превет как дила"], "орфография")
    add("Тексты", "proofread", ["proofread", "-q", "This are a test sentence."], "DeepL Write")
    for e in ("googleai", "openai", "duckai", "deepai"):
        add("Нейросети", e, ["ai", "--engine", e, "-q", AIQ], f"ответ {e}")
    for e in ("yandex", "duckduckgo", "pinterest"):
        add("Картинки и видео", f"images-{e}", ["images", "--engine", e, "-q", RU if e != "pinterest" else "window design"], f"картинки {e}")
    for e in ("yandex", "youtube"):
        add("Картинки и видео", f"video-{e}", ["video", "--engine", e, "-q", RU], f"видео {e}")
    add("Картинки и видео", "byimage", ["byimage", "-q", JPG], "поиск по картинке (Яндекс)")
    add("Картинки и видео", "youtube", ["youtube", "-q", "https://www.youtube.com/watch?v=dQw4w9WgXcQ", "--subs", "en"], "видео YouTube с расшифровкой")
    add("Прочее", "wiki", ["wiki", "-q", "поисковая оптимизация"], "поиск по Википедии")
    for s in ("appstore", "googleplay"):
        add("Прочее", s, ["apps", "--store", s, "-q", "telegram"], f"приложения {s}")
    for s, q in (("market", "чехол для телефона"), ("amazon", "phone case"), ("aliexpress", "phone case")):
        add("Прочее", s, ["shop", "--store", s, "-q", q], f"товары {s}")
    for e in ("yandex", "google"):
        add("Прочее", f"maps-{e}", ["maps", "--engine", e, "-q", "кафе", "--pages", "1"], f"карты {e}")
    add("Прочее", "reddit", ["reddit", "-q", "seo"], "посты Reddit")
    add("Прочее", "tiktok", ["tiktok", "-q", "@tiktok"], "профиль TikTok")
    add("Прочее", "telegram", ["telegram", "-q", "@durov", "--last", "3"], "посты Telegram")
    add("Прочее", "crypto", ["crypto", "-q", "bitcoin"], "курс криптовалюты")
    stub_q = {"yandex-position": "ru.wikipedia.org википедия", "trustcheck": "wikipedia.org", "google-byimage": JPG,
              "yandex-translate": "Hello, world", "chatgpt-free": AIQ, "copilot": AIQ, "perplexity": AIQ, "kimi": AIQ,
              "ebay": "phone case", "wb-search": "чехол для телефона", "wb-suggest": "чехол", "brave": "inurl:blog seo",
              "google-reviews": "https://maps.google.com/?cid=1343986984805261189", "reddit-comments": "seo",
              "instagram-profile": "instagram", "instagram-post": "https://www.instagram.com/p/C0000000000/", "instagram-tag": "seo",
              "instagram-search": "seo", "instagram-geo": "213385402", "quora": "seo", "keysso": "wikipedia.org",
              "kp-ideas": RU, "kp-volume": RU, "wordcraft": RU, "trails-ip": "8.8.8.8", "google-images": RU}
    for name in STUBS:
        if name == "wb-product":
            m.append({"group": "Заготовки", "name": name, "argv": None, "what": "карточка Wildberries",
                      "skip": "не проверяется автоматически: нужен адрес реальной карточки"})
            continue
        add("Заготовки", name, ["stub", name, "-q", stub_q.get(name, EN)], STUBS[name]["parser"])
    return m


class Registry:
    def __init__(self, methods: list[dict]):
        self.lock = threading.Lock()
        self.path = METHODS_DIR / f"{SKILL_NAME}.json"
        old = {}
        if self.path.exists():
            try:
                old = {(x["group"], x["name"]): x for x in json.loads(self.path.read_text(encoding="utf-8")).get("methods", [])}
            except (OSError, ValueError, KeyError):
                old = {}
        self.data = {"skill": SKILL_NAME, "title": "aparser — методы A-Parser", "updated": None, "methods": []}
        for x in methods:
            prev = old.get((x["group"], x["name"]), {})
            self.data["methods"].append({"group": x["group"], "name": x["name"], "what": x["what"],
                                         "example": "python run.py " + " ".join(f'"{a}"' if " " in a else a for a in x["argv"]) if x["argv"] else "",
                                         "status": prev.get("status", "unknown"), "checked": prev.get("checked"),
                                         "seconds": prev.get("seconds"), "note": prev.get("note", "")})
        METHODS_DIR.mkdir(parents=True, exist_ok=True)
        self.write()

    def set(self, group, name, **kw):
        with self.lock:
            for x in self.data["methods"]:
                if x["group"] == group and x["name"] == name:
                    x.update(kw)
            self.write()

    def write(self):
        self.data["updated"] = datetime.now().isoformat(timespec="seconds")
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(json.dumps(self.data, ensure_ascii=False, indent=1), encoding="utf-8")
        os.replace(tmp, self.path)


def judge(code: int | None, out: str, stub: bool) -> tuple[str, str]:
    """Статус по коду выхода и выводу команды."""
    if code is None:
        return "timeout", "не уложился в предел времени"
    need = re.search(r"Нужна переменная (\S+)", out)
    if need:
        return "need", f"нужен доступ: {need.group(1)} в .env"
    if code != 0:
        last = [l for l in out.strip().splitlines() if l.strip()][-1:] or ["ошибка"]
        return "fail", last[0][:200]
    if stub:
        m = re.search(r"с данными: (\d+) из (\d+)", out)
        if m:
            got, total = int(m.group(1)), int(m.group(2))
            return ("ok" if got == total else "part" if got else "fail"), f"с данными {got} из {total}"
    bad = re.search(r"НЕ получено \((\d+)\)|: не получено (\d+)", out)
    if "→ " not in out:
        return "fail", "нет файла результата"
    if bad:
        return "fail", "не получено — " + re.sub(r"\s+", " ", bad.group(0))
    return "ok", ""


def cmd_check(a):
    methods = _methods()
    if a.only:
        pref = [x.strip() for x in a.only.split(",") if x.strip()]
        def hit(x, p):
            if ":" in p:                      # «команда:метод» — точно, например domains:ahrefs
                cmd, nm = p.split(":", 1)
                return bool(x["argv"]) and x["argv"][0] == cmd and x["name"] == nm
            return x["name"].startswith(p) or (bool(x["argv"]) and x["argv"][0].startswith(p))
        methods = [x for x in methods if any(hit(x, p) for p in pref)]
    if a.no_stubs:
        methods = [x for x in methods if x["group"] != "Заготовки"]
    reg = Registry(_methods())
    run_ = pg.CURRENT
    stamp = f"{datetime.now():%Y-%m-%d_%H%M}"
    child_env = {**os.environ, "PYTHONIOENCODING": "utf-8",
                 "APARSER_TOOLS_DATA": str(ap.DATA / "_check" / stamp),
                 "TASK_PANEL_RUNS": str(ap.DATA / "_check" / stamp / "_runs")}
    groups = {}
    for x in methods:
        groups.setdefault(x["group"], []).append(x)
    stage_of = {g: (run_.stage(g, len(xs)) if run_ else None) for g, xs in groups.items()}
    done = {g: [0, 0] for g in groups}
    print(f"Проверка {len(methods)} методов, по {a.workers} одновременно, предел {a.timeout} мин на метод")

    def one(x):
        if not x["argv"]:
            reg.set(x["group"], x["name"], status="skip", note=x.get("skip", ""), checked=datetime.now().isoformat(timespec="seconds"))
            return x, "skip", x.get("skip", ""), 0
        reg.set(x["group"], x["name"], status="running", note="идёт проверка…")
        t = time.time()
        try:
            p = subprocess.run([sys.executable, "run.py", *x["argv"]], cwd=str(Path(__file__).parent), env=child_env,
                               capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=a.timeout * 60)
            code, out = p.returncode, p.stdout + p.stderr
        except subprocess.TimeoutExpired as e:
            code, out = None, (e.stdout or "") if isinstance(e.stdout, str) else ""
        st, note = judge(code, out, x["group"] == "Заготовки")
        sec = round(time.time() - t)
        reg.set(x["group"], x["name"], status=st, note=note, seconds=sec, checked=datetime.now().isoformat(timespec="seconds"))
        return x, st, note, sec

    icons = {"ok": "✅", "part": "⚠️", "fail": "❌", "timeout": "⏱", "need": "🔑", "skip": "·"}
    with ThreadPoolExecutor(a.workers) as ex:
        for f in as_completed([ex.submit(one, x) for x in methods]):
            x, st, note, sec = f.result()
            g = x["group"]
            done[g][0] += 1
            done[g][1] += st == "ok"
            if run_:
                run_.advance(stage_of[g], done[g][0], done[g][1])
            print(f"{icons.get(st, st)} {g} · {x['name']}: {sec} с {note}")
    counts = {}
    for x in reg.data["methods"]:
        counts[x["status"]] = counts.get(x["status"], 0) + 1
    print("Итог:", ", ".join(f"{icons.get(k, k)} {v}" for k, v in sorted(counts.items())))
    print(f"→ {reg.path}")
    if run_:
        run_.output(reg.path)


def register(sub):
    p = sub.add_parser("check", help="проверить все методы на маленьких запросах → кнопки методов в панели задач")
    p.add_argument("--only", help="только методы, чьё имя или команда начинается так (через запятую); точно — команда:метод, например domains:ahrefs")
    p.add_argument("--no-stubs", action="store_true", help="без заготовок")
    p.add_argument("--workers", type=int, default=4, help="сколько методов проверять одновременно (4)")
    p.add_argument("--timeout", type=int, default=30, help="предел на метод, минут (30)")
    p.set_defaults(func=cmd_check)
