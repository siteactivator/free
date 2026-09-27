"""Поиск: выдача, позиции, подсказки, индексация, картинки, видео, поиск по картинке, Википедия."""

from __future__ import annotations

import re

from common import add_io, clean, date_of, domain_of, duration, joined, label_of, ok, read_items, report_failed, run, save, val

# ---------- поисковики ----------

# parser — парсер A-Parser; opts(a) — настройки; query — как превратить фразу в запрос парсера.
# Bing: обычный SE::Bing на 27.09.2026 не работает, а SE::Bing::Position отдаёт ту же выдачу —
# ему нужен домен в начале запроса, подставляем служебный example.com и смотрим только serp.
SERP = {
    "yandex": {"parser": "SE::Yandex", "opts": lambda a: {"engine": "browser", "proxyretries": 20, "lr": a.lr, "pagecount": a.pages}},
    "google": {"parser": "SE::Google", "opts": lambda a: {"pagecount": a.pages, "gl": a.gl, "hl": a.hl, "lr": f"lang_{a.hl}", "domain": a.google_domain}},
    "bing": {"parser": "SE::Bing::Position", "opts": lambda a: {"pagecount": a.pages}, "query": lambda q: f"example.com {q}"},
    "duckduckgo": {"parser": "SE::DuckDuckGo", "opts": lambda a: {"pagecount": a.pages, "location": "ru-ru", "language": "ru_RU"}},
    "yahoo": {"parser": "SE::Yahoo", "opts": lambda a: {"pagecount": a.pages}},
    "aol": {"parser": "SE::AOL", "opts": lambda a: {"page_count": a.pages}},
    "rambler": {"parser": "SE::Rambler", "opts": lambda a: {"pagecount": a.pages}},
    "seznam": {"parser": "SE::Seznam", "opts": lambda a: {"pagecount": a.pages}},
    "you": {"parser": "SE::You", "opts": lambda a: {}},
}


def is_internal(url: str) -> bool:
    """Ссылки на сам поисковик (колдунщики Яндекса, «ещё по запросу», блоки Google) — не органика.
    Относительная ссылка /search?… приходит как http:///search?… — хоста нет."""
    d = domain_of(url)
    return d == "" or (d in ("ya.ru", "yandex.ru") and "/search" in url) or \
        (re.match(r"(.+\.)?google\.[a-z.]+$", d) is not None and ("/search" in url or "/maps" in url))


def engines(a, table: dict) -> list[str]:
    names = [x.strip().lower() for x in a.engine.split(",") if x.strip()]
    if names == ["all"]:
        return list(table)
    bad = [x for x in names if x not in table]
    if bad:
        raise SystemExit(f"Неизвестный поисковик {bad}. Есть: {', '.join(table)}")
    return names


def serp_rows(engine: str, queries: list[str], a) -> tuple[list, dict]:
    """Выдача одного поисковика → (сырые строки, листы: выдача/сводка/реклама/похожие/вопросы/ИИ-источники)."""
    spec = SERP[engine]
    qmap = spec.get("query", lambda q: q)
    organic_of = lambda r: [s for s in (r.get("serp") or []) if s.get("link") and not is_internal(s.get("link", ""))]
    rows = run(spec["parser"], [qmap(q) for q in queries], spec["opts"](a), threads=a.threads)
    # Пустая органика при «успехе» — не ответ, а сбой: 27.09.2026 Яндекс отдал страницу с 2 объявлениями
    # и без выдачи, A-Parser засчитал её успешной без повторов. Такие запросы переспрашиваем один раз.
    empty = [i for i, r in enumerate(rows) if not organic_of(r)]
    if empty:
        print(f"  {engine}: пустая выдача у {len(empty)} запросов — повтор")
        again = run(spec["parser"], [qmap(queries[i]) for i in empty], spec["opts"](a), threads=a.threads, progress=None)
        for i, r in zip(empty, again):
            if organic_of(r):
                rows[i] = r
    sheets = {"выдача": [], "сводка": [], "реклама": [], "похожие запросы": [], "вопросы": [], "ИИ-источники": []}
    for q, r in zip(queries, rows):
        r["query"] = q
        organic = organic_of(r)
        r["organic"] = organic
        if not organic:
            r["success"] = 0          # пустая выдача дважды — считаем «не получено», запрос перезапустить
        answer = val(r.get("ai_answer")) or val(r.get("answer"))
        sheets["сводка"].append({"поисковик": engine, "запрос": q, "получено": ok(r), "всего результатов": val(r.get("totalcount")),
                                 "позиций": len(organic), "исправленный запрос": val(r.get("corrected")) or val(r.get("misspell")),
                                 "ИИ-ответ": clean(answer) if answer else None, "определённый регион": val(r.get("detected_geo"))})
        for i, s in enumerate(organic, 1):
            title = clean(s.get("anchor") or s.get("title"))
            # Yahoo кладёт в заголовок «хлебные крошки» адреса: «https://site.com › pl › windows Заголовок»
            title = re.sub(r"^https?://\S+(?:\s*›\s*\S+)*\s+", "", title) if engine == "yahoo" else title
            sheets["выдача"].append({"поисковик": engine, "запрос": q, "позиция": i, "url": s.get("link"), "домен": domain_of(s.get("link", "")),
                                     "заголовок": title, "сниппет": clean(s.get("snippet") or s.get("desc")),
                                     "дата": date_of(s.get("date"))})
        for i, s in enumerate(r.get("ads") or [], 1):
            sheets["реклама"].append({"поисковик": engine, "запрос": q, "№": i, "домен": s.get("domain") or domain_of(s.get("link", "")),
                                      "url": s.get("link"), "заголовок": clean(s.get("anchor") or s.get("title")),
                                      "текст": clean(s.get("snippet") or s.get("text")), "видимая ссылка": clean(s.get("visiblelink"))})
        for s in (r.get("related") or []) + (r.get("hints") or []):
            k = s.get("key") or s.get("related") or s.get("hint")
            if k:
                sheets["похожие запросы"].append({"поисковик": engine, "запрос": q, "похожий запрос": clean(k)})
        for s in r.get("paa") or []:
            sheets["вопросы"].append({"поисковик": engine, "запрос": q, "вопрос": clean(s.get("question")),
                                      "ответ": clean(s.get("answer")), "источник": s.get("link"), "ИИ": s.get("isAI")})
        for s in r.get("ai") or []:
            sheets["ИИ-источники"].append({"поисковик": engine, "запрос": q, "url": s.get("link"),
                                           "заголовок": clean(s.get("anchor")), "фрагмент": clean(s.get("snippet"))})
    return rows, sheets


def merge(dst: dict, src: dict) -> dict:
    for k, v in src.items():
        dst.setdefault(k, []).extend(v)
    return dst


def serp_args(p, default_engine: str = "yandex") -> None:
    p.add_argument("--engine", default=default_engine, help=f"поисковик или несколько через запятую: {', '.join(SERP)}; all — все")
    p.add_argument("--pages", type=int, default=1, help="страниц выдачи (~10 результатов на странице)")
    p.add_argument("--lr", type=int, default=213, help="Яндекс: регион (213 Москва, 2 СПб …)")
    p.add_argument("--gl", default="ru", help="Google: страна выдачи (ru)")
    p.add_argument("--hl", default="ru", help="Google: язык интерфейса и результатов (ru)")
    p.add_argument("--google-domain", default="www.google.ru", help="Google: домен поисковика (www.google.ru)")


def cmd_serp(a):
    q = read_items(a)
    raw, sheets = {}, {}
    for e in engines(a, SERP):
        rows, sh = serp_rows(e, q, a)
        raw[e] = rows
        merge(sheets, sh)
        report_failed(rows)
    for rows in raw.values():
        for r in rows:
            r.pop("organic", None)
    save("serp", a.label or a.engine.replace(",", "-"), sheets, raw)


def cmd_positions(a):
    q = read_items(a)
    sites = [domain_of(s) for s in a.site.split(",") if s.strip()]
    raw, sheets, pos = {}, {}, []
    for e in engines(a, SERP):
        rows, sh = serp_rows(e, q, a)
        raw[e] = rows
        merge(sheets, sh)
        for r in rows:
            serp = r.get("organic") or []
            for site in sites:
                hit = next(((i, s.get("link")) for i, s in enumerate(serp, 1)
                            if domain_of(s.get("link", "")) == site or domain_of(s.get("link", "")).endswith("." + site)), None)
                pos.append({"поисковик": e, "запрос": r["query"], "сайт": site,
                            "позиция": hit[0] if hit else (None if not ok(r) else f">{len(serp)}"),
                            "url": hit[1] if hit else "", "получено": ok(r)})
        report_failed(rows)
    for rows in raw.values():
        for r in rows:
            r.pop("organic", None)
    save("positions", a.label or "_".join(sites)[:30], {"позиции": pos, **sheets}, raw)


# ---------- подсказки ----------

SUGGEST = {
    "yandex": {"parser": "SE::Yandex::Suggest", "opts": lambda a: {"lr": str(a.lr)}},
    "google": {"parser": "SE::Google::Suggest", "opts": lambda a: {"hl": "ru", "gl": "ru", "domain": "www.google.ru"}},
    "bing": {"parser": "SE::Bing::Suggest", "opts": lambda a: {"region": "ru-RU"}},
    "yahoo": {"parser": "SE::Yahoo::Suggest", "opts": lambda a: {}},
    "youtube": {"parser": "SE::YouTube::Suggest", "opts": lambda a: {"gl": "RU", "hl": "ru"}},
    "pinterest": {"parser": "SE::Pinterest::Suggest", "opts": lambda a: {}},
    "trends": {"parser": "SE::Google::Trends::Suggest", "opts": lambda a: {"hl": "ru"}},
}


def cmd_suggest(a):
    q = read_items(a)
    raw, flat = {}, []
    for e in engines(a, SUGGEST):
        rows = run(SUGGEST[e]["parser"], q, SUGGEST[e]["opts"](a), threads=a.threads)
        raw[e] = rows
        for r in rows:
            for s in r.get("results") or []:
                if s.get("suggest"):
                    flat.append({"источник": e, "запрос": r["query"], "подсказка": clean(s.get("suggest")),
                                 "тип": val(s.get("type")) or val(s.get("sgtype")) or val(s.get("desc"))})
        report_failed(rows)
    uniq = sorted({x["подсказка"] for x in flat})
    save("suggest", a.label or a.engine.replace(",", "-"),
         {"подсказки": flat, "уникальные": [{"подсказка": x} for x in uniq]}, raw)


# ---------- индексация (Яндекс) ----------

YANDEX = {"engine": "browser", "proxyretries": 20}


def cmd_index(a):
    if a.site:
        sites = [domain_of(s) for s in a.site.split(",")]
        rows = run("SE::Yandex", [f"site:{s}" for s in sites], {**YANDEX, "lr": a.lr, "pagecount": 1}, threads=a.threads)
        report_failed(rows)
        save("index", a.label or "site", {"сайты": [{"сайт": s, "страниц в индексе Яндекса": val(r.get("totalcount")), "получено": ok(r)}
                                                   for s, r in zip(sites, rows)]}, rows)
        return
    urls = read_items(a)
    norm = lambda u: re.sub(r"^https?://", "", u).rstrip("/").lower()
    rows = run("SE::Yandex", [f"url:{norm(u)}" for u in urls], {**YANDEX, "lr": a.lr, "pagecount": 1}, threads=a.threads)
    out = []
    for u, r in zip(urls, rows):
        links = [norm(s.get("link", "")).removeprefix("www.") for s in (r.get("serp") or [])]
        out.append({"url": u, "в индексе Яндекса": (norm(u).removeprefix("www.") in links) if ok(r) else None, "получено": ok(r)})
    report_failed(rows)
    save("index", a.label or "urls", {"индексация": out}, rows)


# ---------- картинки и видео ----------

IMAGES = {
    "yandex": ("SE::Yandex::Images", lambda a: {"pagecount": a.pages}),
    "duckduckgo": ("SE::DuckDuckGo::Images", lambda a: {"pagecount": a.pages, "location": "ru-ru", "language": "ru_RU"}),
    "pinterest": ("SE::Pinterest", lambda a: {"pagecount": a.pages}),
}


def cmd_images(a):
    q = read_items(a)
    raw, flat = {}, []
    for e in engines(a, IMAGES):
        parser, opts = IMAGES[e]
        rows = run(parser, q, opts(a), threads=a.threads)
        raw[e] = rows
        for r in rows:
            for i, s in enumerate(r.get("serp") or [], 1):
                image = s.get("image") if e == "pinterest" else s.get("link")
                page = s.get("link") if e == "pinterest" else (s.get("pagelink") or s.get("page"))
                flat.append({"источник": e, "запрос": r["query"], "№": i, "картинка": image, "страница": page,
                             "домен": s.get("domain") or domain_of(page or ""), "заголовок": clean(s.get("anchor") or s.get("title")),
                             "описание": clean(s.get("snippet") or s.get("desc")), "ширина": val(s.get("width")), "высота": val(s.get("height"))})
        report_failed(rows)
    save("images", a.label or a.engine.replace(",", "-"), {"картинки": flat}, raw)


VIDEO = {
    "yandex": ("SE::Yandex::Video", lambda a: {"pagecount": a.pages}),
    "youtube": ("SE::YouTube", lambda a: {"pagecount": a.pages, "gl": "RU", "hl": "ru"}),
}


def cmd_video(a):
    q = read_items(a)
    raw, flat = {}, []
    for e in engines(a, VIDEO):
        parser, opts = VIDEO[e]
        rows = run(parser, q, opts(a), threads=a.threads)
        raw[e] = rows
        for r in rows:
            for i, s in enumerate(r.get("serp") or [], 1):
                flat.append({"источник": e, "запрос": r["query"], "№": i, "url": s.get("link"),
                             "название": clean(s.get("anchor") or s.get("title")), "описание": clean(s.get("snippet") or s.get("desc")),
                             "площадка / канал": clean(s.get("service") or s.get("channel") or s.get("user")),
                             "длительность": duration(s.get("duration") or s.get("time")), "просмотров": val(s.get("views")),
                             "дата": date_of(s.get("date")), "подписчиков канала": val(s.get("subs"))})
        report_failed(rows)
    save("video", a.label or a.engine.replace(",", "-"), {"видео": flat}, raw)


def cmd_byimage(a):
    urls = read_items(a)
    rows = run("SE::Yandex::ByImage", urls, {}, threads=a.threads)
    keys, found, summary = [], [], []
    for r in rows:
        summary.append({"картинка": r["query"], "получено": ok(r), "что на картинке": joined(r.get("keywords"), "key"),
                        "страниц с картинкой": len(r.get("serp") or [])})
        for s in r.get("keywords") or []:
            keys.append({"картинка": r["query"], "фраза": clean(s.get("key"))})
        for i, s in enumerate(r.get("serp") or [], 1):
            found.append({"картинка": r["query"], "№": i, "страница": s.get("link"), "домен": s.get("domain") or domain_of(s.get("link", "")),
                          "заголовок": clean(s.get("anchor")), "фрагмент": clean(s.get("snippet")), "копия картинки": s.get("image"),
                          "ширина": val(s.get("width")), "высота": val(s.get("height"))})
    report_failed(rows)
    save("byimage", label_of(a, urls, "images"), {"сводка": summary, "где встречается": found, "что на картинке": keys}, rows)


# ---------- Википедия ----------

def cmd_wiki(a):
    items = read_items(a)
    articles = [x for x in items if x.startswith("http") or re.match(r"^[a-z-]{2,12}:", x)]
    phrases = [x for x in items if x not in articles]
    sheets, raw = {"статьи": [], "поиск": [], "категории": []}, {}
    if phrases:
        rows = run("SE::Wikipedia", phrases, {"lang": a.lang, "pagecount": a.pages}, threads=a.threads)
        raw["search"] = rows
        for r in rows:
            for i, s in enumerate(r.get("serp") or [], 1):
                sheets["поиск"].append({"запрос": r["query"], "№": i, "статья": clean(s.get("anchor")), "url": s.get("link"),
                                        "описание": clean(s.get("description")), "фрагмент": clean(s.get("snippet")), "размер, байт": val(s.get("size"))})
        report_failed(rows)
    if articles:
        rows = run("SE::Wikipedia::Article", articles, {}, threads=a.threads)
        raw["articles"] = rows
        for r in rows:
            text = str(val(r.get("text")) or "")
            sheets["статьи"].append({"запрос": r["query"], "получено": ok(r), "название": val(r.get("title")), "url": val(r.get("link")),
                                     "язык": val(r.get("lang")), "описание": val(r.get("description")), "размер, байт": val(r.get("size")),
                                     "изменена": val(r.get("timestamp")), "Wikidata": val(r.get("wikidata")),
                                     "языковых версий": len(r.get("langlinks") or []), "категорий": len(r.get("categories") or []),
                                     "символов текста": len(text), "текст": text})
            for c in r.get("categories") or []:
                sheets["категории"].append({"статья": val(r.get("title")), "категория": c.get("name")})
        report_failed(rows)
    first = {"статьи": sheets["статьи"]} if articles else {"поиск": sheets["поиск"]}
    save("wiki", label_of(a, items, "wiki"), {**first, **{k: v for k, v in sheets.items() if k not in first}}, raw)


# ---------- регистрация подкоманд ----------

def register(sub):
    p = sub.add_parser("serp", help="выдача поисковиков: Яндекс, Google, Bing, DuckDuckGo, Yahoo, AOL, Рамблер, Seznam, You")
    add_io(p); serp_args(p); p.set_defaults(func=cmd_serp)

    p = sub.add_parser("positions", help="позиции сайтов по запросам (по выдаче, поддомены засчитываются)")
    add_io(p); serp_args(p)
    p.add_argument("--site", required=True, help="домены через запятую")
    p.set_defaults(func=cmd_positions)

    p = sub.add_parser("suggest", help="поисковые подсказки: Яндекс, Google, Bing, Yahoo, YouTube, Pinterest, Google Trends")
    add_io(p)
    p.add_argument("--engine", default="yandex", help=f"источник или несколько через запятую: {', '.join(SUGGEST)}; all — все")
    p.add_argument("--lr", type=int, default=213, help="Яндекс: регион")
    p.set_defaults(func=cmd_suggest)

    p = sub.add_parser("index", help="индексация в Яндексе: страница в индексе (url:) или сколько страниц у сайта (--site)")
    add_io(p, "адреса страниц")
    p.add_argument("--site", help="режим site: — домены через запятую")
    p.add_argument("--lr", type=int, default=213)
    p.set_defaults(func=cmd_index)

    p = sub.add_parser("images", help="поиск картинок: Яндекс, DuckDuckGo, Pinterest")
    add_io(p)
    p.add_argument("--engine", default="yandex", help=f"источник или несколько через запятую: {', '.join(IMAGES)}")
    p.add_argument("--pages", type=int, default=1)
    p.set_defaults(func=cmd_images)

    p = sub.add_parser("video", help="поиск видео: Яндекс Видео, YouTube")
    add_io(p)
    p.add_argument("--engine", default="yandex", help=f"источник или несколько через запятую: {', '.join(VIDEO)}")
    p.add_argument("--pages", type=int, default=1)
    p.set_defaults(func=cmd_video)

    p = sub.add_parser("byimage", help="поиск по картинке в Яндексе: где встречается и что на ней")
    add_io(p, "адреса картинок")
    p.set_defaults(func=cmd_byimage)

    p = sub.add_parser("wiki", help="Википедия: поиск статей по фразе или данные статьи по адресу")
    add_io(p, "фразы или адреса статей")
    p.add_argument("--lang", default="ru", help="язык раздела для поиска (ru)")
    p.add_argument("--pages", type=int, default=1)
    p.set_defaults(func=cmd_wiki)
