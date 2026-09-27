"""Клиент HTTP API A-Parser (Enterprise): вызовы, проверка опций, разбор результатов.

Настройки — файл .env в папке скилла (образец — .env.example) или переменная APARSER_CONFIG
с путём к другому .env. Результаты — папка data\\ в папке скилла или APARSER_DATA.

Особенности API, которые тут учтены (проверено 26–27.09.2026 на 1.2.3390 и 1.2.3656):
- с rawResults=1 массивы результата приходят «сплющенными»: serp у SE::Yandex —
  плоский список по N значений на позицию; порядок полей — из getParserInfo;
- в результатах Net::HTTP и др. есть поле proxy с логином/паролем прокси — вырезаем;
- опечатка в id опции override может уронить A-Parser — сверяем с getParserPreset
  до отправки (для связанных пресетов допустим id вида «Util_AntiGate_preset.key»).
"""

from __future__ import annotations

import os
import re
import sys
import time
from functools import lru_cache
from pathlib import Path

import requests

for _s in (sys.stdout, sys.stderr):
    try:
        _s.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, ValueError):
        pass

SKILL_DIR = Path(__file__).resolve().parent
CONFIG_ENV = Path(os.getenv("APARSER_CONFIG") or SKILL_DIR / ".env")

# Поля, которые не отдаём наружу: сырые страницы и доступы прокси
DROP_FIELDS = {"proxy", "pages", "data", "headers"}
PROXY_AUTH = re.compile(r"(\w+://)[^\s:@/]+:[^\s@/]+@")


def _env() -> dict:
    vals = {}
    if CONFIG_ENV.exists():
        for line in CONFIG_ENV.read_text(encoding="utf-8-sig").splitlines():
            if "=" in line and not line.lstrip().startswith("#"):
                k, v = line.split("=", 1)
                vals[k.strip()] = v.strip()
    vals.update({k: v for k, v in os.environ.items() if k.startswith(("APARSER_", "CAPMONSTER_", "OPENAI_"))})
    return vals


ENV = _env()
API_URL = ENV.get("APARSER_API_URL") or "http://127.0.0.1:9091/API"
PASSWORD = ENV.get("APARSER_API_PASSWORD", "")
DATA = Path(ENV.get("APARSER_DATA") or SKILL_DIR / "data")
# Имя пресетов капчи у Util::AntiGate, Util::ReCaptcha2 и Util::Turnstile в A-Parser
CAPTCHA = ENV.get("APARSER_CAPTCHA_PRESET") or "capmonster"
# Проксичекер, по которому status показывает живые прокси
PROXY_CHECKER = ENV.get("APARSER_PROXY_CHECKER", "")


class APError(RuntimeError):
    pass


def call(action: str, data: dict | None = None, timeout: int = 600) -> object:
    body = {"password": PASSWORD, "action": action}
    if data is not None:
        body["data"] = data
    try:
        r = requests.post(API_URL, json=body, timeout=timeout)
    except requests.RequestException as e:
        raise APError(f"A-Parser не отвечает на {API_URL} — запущен ли он? (python server.py start)") from e
    try:
        j = r.json()
    except ValueError as e:
        raise APError(f"{API_URL} ответил не как API A-Parser (код {r.status_code}) — "
                      f"проверьте APARSER_API_URL, адрес должен кончаться на /API") from e
    if not j.get("success"):
        if j.get("msg") == "Auth failed":
            raise APError("A-Parser не принял пароль — проверьте APARSER_API_PASSWORD "
                          "(это пароль интерфейса A-Parser)")
        raise APError(f"{action}: {j}")
    return j.get("data")


def ping() -> bool:
    try:
        return call("ping", timeout=10) == "pong"
    except APError:
        return False


@lru_cache(maxsize=None)
def available_parsers() -> tuple[str, ...]:
    return tuple(call("info").get("availableParsers", []))


@lru_cache(maxsize=None)
def options(parser: str, preset: str = "default") -> dict:
    return call("getParserPreset", {"parser": parser, "preset": preset})


@lru_cache(maxsize=None)
def schema(parser: str) -> dict:
    """{'arrays': {имя: [поля]}, 'flat': [поля]} из getParserInfo."""
    res = call("getParserInfo", {"parser": parser})["results"]
    return {"arrays": {k: [f[0] for f in v[1]] for k, v in res.get("arrays", {}).items()},
            "flat": [f[0] for f in res.get("flat", [])]}


def validate(parser: str, overrides: dict | None, preset: str = "default") -> None:
    if parser not in available_parsers():
        raise APError(f"Нет парсера {parser}. Есть: {', '.join(available_parsers())}")
    valid = options(parser, preset)
    bad = [k for k in (overrides or {}) if k.split(".")[0] not in valid]
    if bad:
        raise APError(f"{parser}: неизвестные опции {bad}. Допустимые: {sorted(valid)}")


def captcha(parser: str) -> dict:
    """Пресеты капчи для тех связанных Util-парсеров, которые есть у парсера."""
    return {k: CAPTCHA for k in options(parser)
            if k in ("Util_AntiGate_preset", "Util_ReCaptcha2_preset", "Util_Turnstile_preset")}


def _overrides(overrides: dict | None) -> list[dict]:
    return [{"type": "override", "id": k, "value": v} for k, v in (overrides or {}).items()]


def _reshape(parser: str, res: dict, keep: tuple = ()) -> dict:
    """Результат rawResults → плоские поля + массивы списков словарей."""
    sch = schema(parser)
    q = res.get("query") or {}
    info = res.get("info") or {}
    out = {"query": q.get("orig") or q.get("first") or q.get("query"),
           "success": info.get("success"), "retries": info.get("retries")}
    for k, v in res.items():
        if k in ("query", "info", "success") or (k in DROP_FIELDS and k not in keep):
            continue
        if k in sch["arrays"] and isinstance(v, list):
            names = sch["arrays"][k]
            if v and not isinstance(v[0], dict):
                n = len(names)
                v = [dict(zip(names, v[i:i + n])) for i in range(0, len(v), n)]
            out[k] = v
        else:
            out[k] = v
    return out


def one(parser: str, query: str, overrides: dict | None = None, preset: str = "default",
        log: bool = False) -> dict:
    validate(parser, overrides, preset)
    d = call("oneRequest", {"parser": parser, "preset": preset, "query": query, "rawResults": 1,
                            "doLog": 1 if log else 0, "options": _overrides(overrides)})
    res = (d.get("results") or [{}])[0]
    out = _reshape(parser, res)
    if log:
        out["_log"] = [mask(str(x[2])) for x in d.get("logs", []) if len(x) > 2]
    return out


def mask(text: str) -> str:
    """Прячет доступы прокси в логах A-Parser. Форматы бывают разные:
    «socks5://логин:пароль@хост:порт» и «хост:порт:socks5:логин:пароль» (сессии) —
    поэтому, кроме шаблона, вырезаются сами значения логина и пароля из .env."""
    text = PROXY_AUTH.sub(r"\1***:***@", text)
    for k in ("APARSER_MIX_PASSWORD", "APARSER_MIX_LOGIN", "APARSER_PROXY_PASSWORD", "APARSER_PROXY_LOGIN",
              "CAPMONSTER_KEY", "APARSER_API_PASSWORD", "OPENAI_API_KEY", "GOOGLE_SAFEBROWSING_KEY"):
        v = ENV.get(k)
        if v and len(v) >= 4:
            text = text.replace(v, "***")
    return text


def bulk(parser: str, queries: list[str], overrides: dict | None = None, threads: int = 5,
         preset: str = "default", batch: int | None = None, progress=print, keep: tuple = (),
         on_batch=None) -> list[dict]:
    """Пачка запросов через bulkRequest (ответ сразу). Списки режем на порции: по умолчанию так, чтобы
    порций было не меньше пяти (прогресс для панели задач), но не больше 50 запросов в порции.
    on_batch(сделано, успешно) — после каждой порции."""
    validate(parser, overrides, preset)
    if batch is None:
        batch = min(50, max(threads, -(-len(queries) // 5)))
    out = []
    for i in range(0, len(queries), batch):
        part = queries[i:i + batch]
        t = time.time()
        d = call("bulkRequest", {"parser": parser, "preset": preset, "configPreset": "default",
                                 "threads": threads, "rawResults": 1, "doLog": 0,
                                 "queries": part, "options": _overrides(overrides)}, timeout=3600)
        rows = [_reshape(parser, r, keep) for r in d.get("results", [])]
        out += rows
        ok = sum(1 for r in rows if r.get("success") == 1)
        if progress:
            progress(f"  {parser}: {min(i + batch, len(queries))}/{len(queries)} "
                     f"(успешно {ok}/{len(part)}, {time.time() - t:.0f} с)")
        if on_batch:
            on_batch(len(out), sum(1 for r in out if r.get("success") == 1))
    return out


def live_proxies(checker: str | None = None) -> int:
    d = call("getProxies", {"checkers": [checker]} if checker else None) or {}
    return len(d)


def capmonster_balance() -> float | None:
    key = ENV.get("CAPMONSTER_KEY")
    if not key:
        return None
    try:
        return requests.post("https://api.capmonster.cloud/getBalance", json={"clientKey": key},
                             timeout=20).json().get("balance")
    except (requests.RequestException, ValueError):
        return None
