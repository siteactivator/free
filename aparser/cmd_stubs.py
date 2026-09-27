"""Заготовки для парсеров, которые не вошли в основные команды: не заработали с первой попытки,
работают частично или требуют своего аккаунта.

    python run.py stub                       # список заготовок и что про каждую известно
    python run.py stub <имя> -q "…"          # запуск: все поля результата — в xlsx, как у raw
    python run.py stub <имя> -q "…" --debug  # плюс лог A-Parser по первому запросу
    python run.py stub <имя> -q "…" --set опция=значение

Метод добавлен, но в работе не проверен: запрос и настройки — стартовая точка, а не рецепт.
Куки и токены берутся из .env (переменные APARSER_…), чтобы не светить их в командной строке.
Поля с персональными данными посторонних людей (авторы, подписчики, email, телефоны) вырезаются.
"""

from __future__ import annotations

import json
import sys

import aparser as ap
from common import add_io, read_items, report_failed, run, save

# имя → парсер, что подавать на вход, настройки по умолчанию, что нужно (опция, переменная .env,
# что это, обязательно ли), что было у нас 27.09.2026, с чего начать, что вырезать (массивы, поля)
STUBS = {
    # --- выдача и позиции ---
    "bing": {"parser": "SE::Bing", "input": "фраза", "opts": {"pagecount": 1},
             "status": "❌ пусто за 7 мин", "hint": "выдача Bing уже есть в serp --engine bing (через SE::Bing::Position); попробуйте --set engine=browser"},
    "brave": {"parser": "SE::Brave", "input": "фраза", "opts": {"pagecount": 1},
              "status": "❌ пусто", "hint": "по форуму A-Parser — слайдер-капча (429); помогает оператор в запросе (inurl:, site:), иначе — перебор прокси"},
    "startpage": {"parser": "SE::Startpage", "input": "фраза", "opts": {"pagecount": 1},
                  "status": "❌ пусто за 85 с", "hint": "по форуму — работает на прокси Unlimited, на Premium неверно собирал анкоры"},
    "baidu": {"parser": "SE::Baidu", "input": "фраза", "opts": {"pagecount": 1}, "status": "❌ отказ за 5 с",
              "hint": "вероятно, нужны прокси из Азии; смотрите лог (--debug)"},
    "dogpile": {"parser": "SE::Dogpile", "input": "фраза", "opts": {"pagecount": 1}, "status": "❌ пусто",
                "hint": "у парсера есть обход Cloudflare через Chrome (bypassCloudFlare) — проверьте, что он включён"},
    "yandex-position": {"parser": "SE::Yandex::Position", "input": "«домен фраза», например «site.ru пластиковые окна»",
                        "opts": {"lr": 213}, "status": "❌ без браузера — пусто за 3 мин, с engine=browser — завис на 38 мин",
                        "hint": "позиции в Яндексе уже считает positions --engine yandex (по выдаче); по форуму нужен Maximum number of pages for smartcaptcha = 0 и 300+ повторов"},
    "trustcheck": {"parser": "SE::Google::TrustCheck", "input": "домен", "opts": {"pagecount": 1},
                   "status": "⚠️ отвечает, но траст 0 даже у Википедии", "hint": "проверьте, что парсер вообще видит sitelinks в выдаче Google (лист serp)"},
    # --- подсказки ---
    "aol-suggest": {"parser": "SE::AOL::Suggest", "input": "фраза", "opts": {}, "status": "❌ пусто за 7 мин", "hint": "смотрите лог (--debug)"},
    # --- картинки и видео ---
    "google-images": {"parser": "SE::Google::Images", "input": "фраза", "opts": {}, "status": "❌ пусто за 4 мин",
                      "hint": "нужна капча ReCaptcha2 (подставляется сама); по форуму работу с капчей чинили в 1.2.3599"},
    "google-byimage": {"parser": "SE::Google::ByImage", "input": "прямой адрес картинки", "opts": {}, "status": "❌ пусто (JPG и PNG)",
                       "hint": "по документации — ссылка на картинку «в Google»; поиск по картинке в Яндексе уже есть — byimage"},
    "dogpile-images": {"parser": "SE::Dogpile::Images", "input": "фраза", "opts": {}, "status": "❌ пусто", "hint": "нужна капча ReCaptcha2"},
    "startpage-images": {"parser": "SE::Startpage::Images", "input": "фраза", "opts": {}, "status": "❌ пусто", "hint": "как startpage"},
    "startpage-videos": {"parser": "SE::Startpage::Videos", "input": "фраза", "opts": {}, "status": "❌ пусто", "hint": "как startpage"},
    "bing-images": {"parser": "SE::Bing::Images", "input": "фраза", "opts": {}, "status": "⚠️ 1 результат вместо сотен", "hint": "попробуйте --set pagecount=5"},
    "bing-video": {"parser": "SE::Bing::Video", "input": "фраза", "opts": {}, "status": "⚠️ успех, но 0 результатов", "hint": "смотрите лог (--debug)"},
    # --- тексты ---
    "yandex-translate": {"parser": "SE::Yandex::Translate", "input": "текст", "opts": {}, "status": "❌ 13 мин без ответа",
                         "hint": "перевод уже есть — translate --engine google/bing/deepl"},
    # --- нейросети ---
    "chatgpt-free": {"parser": "FreeAI::ChatGPT", "input": "вопрос", "opts": {}, "status": "❌ отказ за 25 с",
                     "hint": "по форуму парсер часто ломается и чинится; ответы OpenAI по ключу — ai --engine openai"},
    "copilot": {"parser": "FreeAI::Copilot", "input": "вопрос", "opts": {}, "status": "❌ 6 мин без ответа", "hint": "смотрите лог (--debug)"},
    "perplexity": {"parser": "FreeAI::Perplexity", "input": "вопрос", "opts": {},
                   "needs": [("cookies", "APARSER_PERPLEXITY_COOKIES", "куки авторизованного аккаунта perplexity.ai", False)],
                   "status": "❌ без куки пусто", "hint": "по форуму с 1.2.3526 нужна cookie-авторизация: войдите в perplexity.ai и скопируйте куки"},
    "kimi": {"parser": "FreeAI::Kimi", "input": "вопрос", "opts": {},
             "needs": [("auth_token", "APARSER_KIMI_TOKEN", "токен kimi.com: DevTools → Network → заголовок Authorization без «Bearer»", True)],
             "status": "🔑 не проверяли — нужен токен", "hint": ""},
    # --- магазины ---
    "ebay": {"parser": "Shop::eBay", "input": "фраза", "opts": {}, "status": "❌ пусто: 403 Forbidden на каждый прокси",
             "hint": "eBay блокирует резидентные прокси Premium Mix — попробуйте другие прокси или --set engine=browser, если есть"},
    "wb-search": {"parser": "Shop::Wildberries::ProductsList", "input": "фраза", "opts": {}, "status": "❌ пусто за 105 с",
                  "hint": "возможно, Wildberries отдаёт выдачу только российским IP — попробуйте российские прокси"},
    "wb-suggest": {"parser": "Shop::Wildberries::Suggest", "input": "фраза", "opts": {}, "status": "❌ пусто", "hint": "как wb-search"},
    "wb-product": {"parser": "Shop::Wildberries::ProductInfo", "input": "адрес карточки wildberries.ru/catalog/<артикул>/detail.aspx", "opts": {},
                   "status": "🔍 не проверяли (ссылку брали из wb-search)", "hint": "дайте адрес реальной карточки"},
    # --- карты и соцсети ---
    "google-reviews": {"parser": "Maps::Google::Reviews", "input": "адрес места: https://maps.google.com/?cid=<номер> (есть в maps --engine google, колонка «карточка»)",
                       "opts": {}, "status": "⚠️ рейтинг и число отзывов есть, самих отзывов — первые 5, потом отказ",
                       "hint": "смотрите лог на пагинации (--debug)", "drop_fields": ["name", "url"]},
    "reddit-comments": {"parser": "Reddit::Comments", "input": "фраза", "opts": {}, "status": "❌ пусто",
                        "hint": "посты и пост с комментариями уже есть — reddit", "drop_fields": ["author", "authorFlair", "postAuthor", "postAuthorFlair"]},
    "instagram-profile": {"parser": "Social::Instagram::Profile", "input": "профиль (ник или адрес)", "opts": {},
                          "needs": [("cookie", "APARSER_INSTAGRAM_COOKIE", "куки авторизованного аккаунта Instagram", True)],
                          "status": "🔑 не проверяли — нужна кука", "hint": "берите отдельный аккаунт: Instagram блокирует за парсинг",
                          "drop_arrays": ["followers", "followings", "numbers"], "drop_fields": ["email"]},
    "instagram-post": {"parser": "Social::Instagram::Post", "input": "адрес поста", "opts": {},
                       "needs": [("cookie", "APARSER_INSTAGRAM_COOKIE", "куки авторизованного аккаунта Instagram", True)],
                       "status": "🔑 не проверяли — нужна кука", "hint": "", "drop_fields": ["author"]},
    "instagram-tag": {"parser": "Social::Instagram::Tag", "input": "хэштег без #", "opts": {},
                      "needs": [("cookie", "APARSER_INSTAGRAM_COOKIE", "куки авторизованного аккаунта Instagram", True)],
                      "status": "🔑 не проверяли — нужна кука", "hint": ""},
    "instagram-search": {"parser": "Social::Instagram::Search", "input": "фраза", "opts": {},
                         "needs": [("cookie", "APARSER_INSTAGRAM_COOKIE", "куки авторизованного аккаунта Instagram", True)],
                         "status": "🔑 не проверяли — нужна кука", "hint": "", "drop_arrays": ["users"]},
    "instagram-geo": {"parser": "Social::Instagram::Geo", "input": "адрес или id места", "opts": {},
                      "needs": [("cookie", "APARSER_INSTAGRAM_COOKIE", "куки авторизованного аккаунта Instagram", True)],
                      "status": "🔑 не проверяли — нужна кука", "hint": "", "drop_fields": ["full_name"]},
    "quora": {"parser": "SE::Quora", "input": "фраза", "opts": {},
              "needs": [("cookie", "APARSER_QUORA_COOKIE", "куки авторизованного аккаунта Quora", True)],
              "status": "🔑 не проверяли — нужна кука", "hint": "",
              "drop_arrays": ["profiles"], "drop_fields": ["authorProfile", "authorName", "authorAvatar", "author"]},
    # --- ключевые слова и домены с аккаунтами ---
    "keysso": {"parser": "Rank::KeysSo", "input": "домен", "opts": {"se_db": "msk"},
               "needs": [("userlogin", "APARSER_KEYSSO_USERLOGIN", "значение куки userlogin из браузера после входа в keys.so (токен API не подходит)", True)],
               "status": "🔑 не проверяли — нужна кука", "hint": "база --set se_db=msk|spb|…; у Keys.so есть и официальный API по токену"},
    "kp-ideas": {"parser": "SE::Google::KeywordPlanner::Ideas", "input": "фраза", "opts": {},
                 "needs": [("email", "APARSER_GOOGLE_ADS_EMAIL", "логин Google Ads", False), ("password", "APARSER_GOOGLE_ADS_PASSWORD", "пароль Google Ads", False),
                           ("allCookies", "APARSER_GOOGLE_ADS_COOKIES", "или куки кабинета Google Ads целиком", False)],
                 "status": "🔑 не проверяли — нужен аккаунт Google Ads с созданной кампанией",
                 "hint": "по документации — логин и пароль или куки + заголовки кабинета; смотрите --set location=… и lang=…"},
    "kp-volume": {"parser": "SE::Google::KeywordPlanner::SearchVolume", "input": "фраза", "opts": {},
                  "needs": [("email", "APARSER_GOOGLE_ADS_EMAIL", "логин Google Ads", False), ("password", "APARSER_GOOGLE_ADS_PASSWORD", "пароль Google Ads", False),
                            ("allCookies", "APARSER_GOOGLE_ADS_COOKIES", "или куки кабинета Google Ads целиком", False)],
                  "status": "🔑 не проверяли — нужен аккаунт Google Ads", "hint": "как kp-ideas"},
    "wordcraft": {"parser": "SE::Yandex::WordCraft", "input": "фраза", "opts": {},
                  "status": "🔑 не проверяли — нужны аккаунты Яндекса",
                  "hint": "логин:пароль по строке в <папка A-Parser>\\files\\SE-Yandex\\accounts.txt; основной аккаунт не давайте — "
                          "A-Parser входит с разных IP прокси, Яндекс может заблокировать вход"},
    "trails-ip": {"parser": "SecurityTrails::IP", "input": "IP", "opts": {},
                  "needs": [("login", "APARSER_SECURITYTRAILS_LOGIN", "логин securitytrails.com", False),
                            ("password", "APARSER_SECURITYTRAILS_PASSWORD", "пароль securitytrails.com", False)],
                  "status": "❌ 10 мин без ответа (без логина)", "hint": "по документации для поиска доменов на IP нужен вход в SecurityTrails"},
}


def scrub(rows: list[dict], spec: dict) -> None:
    """Вырезает массивы и поля с персональными данными посторонних людей — и из xlsx, и из json."""
    arrays, fields = set(spec.get("drop_arrays", [])), set(spec.get("drop_fields", []))
    for r in rows:
        for k in list(r):
            if k in arrays or k in fields:
                r.pop(k, None)
            elif isinstance(r[k], list):
                for x in r[k]:
                    if isinstance(x, dict):
                        for f in fields:
                            x.pop(f, None)


def list_stubs() -> None:
    print("Заготовки: метод добавлен, но в работе не проверен — разберитесь сами (README, раздел про заготовки).\n")
    for name, s in STUBS.items():
        need = ", ".join(env for _, env, _, _ in s.get("needs", []))
        print(f"  {name:<18} {s['parser']:<40} {s['status']}" + (f"  [.env: {need}]" if need else ""))
    print("\nЗапуск: python run.py stub <имя> -q \"…\" [--debug] [--set опция=значение]")


def cmd_stub(a):
    if not a.name:
        list_stubs()
        return
    if a.name not in STUBS:
        raise SystemExit(f"Нет заготовки {a.name}. Список: python run.py stub")
    s = STUBS[a.name]
    print(f"⚠️ Заготовка «{a.name}» → {s['parser']}. Метод добавлен, но в работе не проверен.")
    print(f"   Было у нас 27.09.2026: {s['status']}")
    if s.get("hint"):
        print(f"   С чего начать: {s['hint']}")
    print(f"   На вход: {s['input']}")
    ov = dict(s["opts"])
    for opt, env, what, required in s.get("needs", []):
        v = ap.ENV.get(env)
        if v:
            ov[opt] = v
        elif required:
            raise SystemExit(f"Нужна переменная {env} в .env: {what}")
    for kv in a.set or []:
        k, v = kv.split("=", 1)
        ov[k] = int(v) if v.lstrip("-").isdigit() else v
    q = read_items(a)
    if a.debug:
        r = ap.one(s["parser"], q[0], {**ap.captcha(s["parser"]), **ov}, log=True)
        print(f"\n--- лог A-Parser по запросу «{q[0]}» (success={r.get('success')}) ---")
        print("\n".join(r.get("_log", [])[-60:]))
        print("--- конец лога ---\n")
    rows = run(s["parser"], q, ov, threads=a.threads)
    scrub(rows, s)
    arrays = set(ap.schema(s["parser"])["arrays"])
    flat, sheets = [], {}
    for r in rows:
        f = {}
        for k, v in r.items():
            if k in arrays and isinstance(v, list) and v and isinstance(v[0], dict):
                sheets.setdefault(k, []).extend({"query": r["query"], **x} for x in v)
                f[k] = len(v)
            else:
                f[k] = json.dumps(v, ensure_ascii=False) if isinstance(v, (list, dict)) else v
        flat.append(f)
    report_failed(rows)
    got = sum(1 for r in rows if r.get("success") == 1 and (any(isinstance(v, list) and v for v in r.values())
                                                            or any(v not in (None, "", "none", 0, "0") for k, v in r.items()
                                                                   if k not in ("query", "success", "retries", "resultsCount"))))
    print(f"  с данными: {got} из {len(rows)} — если 0, смотрите --debug и README")
    save(f"stub-{a.name}", a.label or a.name, {"результаты": flat, **sheets}, rows)


def register(sub):
    p = sub.add_parser("stub", help="заготовки для непроверенных парсеров (37): метод есть, но в работе не проверен — разбирайтесь сами")
    p.add_argument("name", nargs="?", help="имя заготовки; без имени — список")
    add_io(p)
    p.add_argument("--set", action="append", help="опция=значение (можно несколько)")
    p.add_argument("--debug", action="store_true", help="напечатать лог A-Parser по первому запросу")
    p.set_defaults(func=cmd_stub)
