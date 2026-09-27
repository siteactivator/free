"""Страницы и тексты: HTTP, извлечение статьи/текста/ссылок/языка, скриншоты, переводы,
орфография, правка текста, ответы нейросетей."""

from __future__ import annotations

import re
from datetime import datetime

import aparser as ap
from common import add_io, clean, domain_of, label_of, ok, read_items, report_failed, run, save, val

SAFE_NAME = re.compile(r"[^\w.-]+")


def cmd_http(a):
    urls = read_items(a)
    # --browser: engine=chrome проходит JS-проверки анти-ботов («Идёт проверка браузера», 503)
    ov = {"engine": "chrome"} if a.browser else {}
    rows = run("Net::HTTP", urls, ov, threads=a.threads, keep=("data",))
    # Chrome иногда снимает DOM посреди перезагрузки после JS-проверки сайта: приходит
    # <head> без <body> и часто без <title> — такие адреса переспрашиваем (до 2 раз)
    for _ in range(2 if a.browser else 0):
        idx = [i for i, r in enumerate(rows) if r.get("code") == 200 and "<body" not in str(r.get("data") or "").lower()]
        if not idx:
            break
        print(f"  HTML без <body> у {len(idx)} адресов — повтор")
        for i, r in zip(idx, run("Net::HTTP", [urls[i] for i in idx], ov, threads=a.threads, keep=("data",), progress=None)):
            if "<body" in str(r.get("data") or "").lower():
                rows[i] = r
    out = []
    for u, r in zip(urls, rows):
        resp = r.get("response") if isinstance(r.get("response"), dict) else {}
        html = str(r.get("data") or "")
        m = (re.search(r"<title[^>]*>(.*?)</title>", html, re.I | re.S)
             or re.search(r'property="og:title"\s+content="([^"]*)"', html, re.I)
             or re.search(r"<h1[^>]*>(.*?)</h1>", html, re.I | re.S))
        desc = re.search(r'<meta[^>]+name="description"[^>]+content="([^"]*)"', html, re.I)
        robots = re.search(r'<meta[^>]+name="robots"[^>]+content="([^"]*)"', html, re.I)
        canon = re.search(r'<link[^>]+rel="canonical"[^>]+href="([^"]*)"', html, re.I)
        redirects = resp.get("Redirects") or []
        out.append({"url": u, "код": val(r.get("code")), "статус": r.get("reason"), "конечный адрес": resp.get("URI") or "",
                    "редиректов": len(redirects) if isinstance(redirects, list) else redirects,
                    "title": clean(m.group(1)) if m else "", "description": clean(desc.group(1)) if desc else "",
                    "meta robots": robots.group(1) if robots else "", "canonical": canon.group(1) if canon else "",
                    "размер HTML, байт": len(html.encode("utf-8", "ignore")), "HTML полный": "<body" in html.lower(), "получено": ok(r)})
        r.pop("data", None)
    report_failed(rows)
    save("http", label_of(a, urls, "urls"), {"http": out}, rows)


def cmd_extract(a):
    urls = read_items(a)
    ov = {"engine": "chrome"} if a.browser else {}
    sheets, raw = {}, {}
    if a.what == "article":
        rows = run("HTML::ArticleExtractor", urls, ov, threads=a.threads)
        sheets["статьи"] = [{"url": r["query"], "получено": ok(r), "код": val(r.get("code")), "заголовок": clean(r.get("title")),
                             "автор": clean(r.get("byline")), "сайт": clean(r.get("siteName")), "символов": val(r.get("length")),
                             "анонс": clean(r.get("excerpt")), "текст": re.sub(r"\n{3,}", "\n\n", str(val(r.get("textContent")) or "")).strip()}
                            for r in rows]
        for r in rows:
            r.pop("content", None)       # HTML статьи — только текст, чтобы json не раздувался
    elif a.what == "text":
        rows = run("HTML::TextExtractor", urls, ov, threads=a.threads)
        sheets["блоки текста"] = [{"url": r["query"], "№": i, "текст": clean(t.get("text"))}
                                  for r in rows for i, t in enumerate(r.get("texts") or [], 1)]
        sheets["сводка"] = [{"url": r["query"], "получено": ok(r), "код": val(r.get("code")), "блоков": len(r.get("texts") or []),
                             "символов": sum(len(clean(t.get("text"))) for t in r.get("texts") or [])} for r in rows]
    elif a.what == "links":
        rows = run("HTML::LinkExtractor", urls, ov, threads=a.threads)
        for kind, arr in (("внешние", "extlinks"), ("внутренние", "intlinks")):
            sheets[kind] = [{"страница": r["query"], "ссылка": s.get("link"), "домен": domain_of(s.get("link", "")),
                             "анкор": clean(s.get("cleananchor") or s.get("anchor")), "nofollow": val(s.get("nofollow"))}
                            for r in rows for s in r.get(arr) or []]
        sheets["сводка"] = [{"страница": r["query"], "получено": ok(r), "код": val(r.get("code")), "внешних": val(r.get("extcount")),
                             "внутренних": val(r.get("intcount"))} for r in rows]
        sheets = {"сводка": sheets.pop("сводка"), **sheets}
    else:
        rows = run("HTML::TextExtractor::LangDetect", urls, ov, threads=a.threads)
        sheets["язык"] = [{"url": r["query"], "получено": ok(r), "язык": val(r.get("lang")), "доля текста, %": val(r.get("percent"))} for r in rows]
    raw[a.what] = rows
    report_failed(rows)
    save(f"extract-{a.what}", label_of(a, urls, "urls"), sheets, raw)


def cmd_screenshot(a):
    urls = read_items(a)
    ov = {"width": a.width, "height": a.height}
    if a.full:
        ov["fullScreenshot"] = 1
    rows = run("Browser::ScreenshotsMaker", urls, ov, threads=a.threads)
    safe = lambda s: SAFE_NAME.sub("_", s)
    folder = ap.DATA / "screenshot" / f"{datetime.now():%Y-%m-%d_%H%M}_{safe(label_of(a, urls, 'urls'))[:40]}"
    folder.mkdir(parents=True, exist_ok=True)
    out = []
    for i, (u, r) in enumerate(zip(urls, rows), 1):
        png = r.pop("screenshot", None)
        path = ""
        if ok(r) and isinstance(png, str) and png:
            # A-Parser отдаёт PNG строкой, байт на символ — обратно в байты через latin-1
            path = folder / f"{i:03d}_{safe(domain_of(u))[:50]}.png"
            path.write_bytes(png.encode("latin-1"))
        out.append({"url": u, "получено": ok(r), "файл": str(path), "размер, КБ": round(path.stat().st_size / 1024) if path else None})
    report_failed(rows)
    print(f"  скриншоты: {folder}")
    save("screenshot", label_of(a, urls, "urls"), {"скриншоты": out}, rows)


TRANSLATE = {
    "google": ("SE::Google::Translate", lambda a: {"to_language": a.to, "from_language": a.src}),
    "bing": ("SE::Bing::Translator", lambda a: {"to": a.to, "from": "auto-detect" if a.src == "auto" else a.src}),
    "deepl": ("DeepL::Translator", lambda a: {"to_language": a.to.upper(), "from_language": a.src}),
}


def cmd_translate(a):
    texts = read_items(a, dedupe=False)
    out, raw = [], {}
    for e in [x.strip() for x in a.engine.split(",")]:
        if e not in TRANSLATE:
            raise SystemExit(f"Переводчики: {', '.join(TRANSLATE)}")
        parser, opts = TRANSLATE[e]
        rows = run(parser, texts, opts(a), threads=a.threads)
        raw[e] = rows
        for t, r in zip(texts, rows):
            out.append({"переводчик": e, "текст": t, "перевод": val(r.get("translated")), "язык оригинала": val(r.get("detected")),
                        "варианты": "; ".join(clean(v.get("text")) for v in r.get("variants") or []), "получено": ok(r)})
        report_failed(rows)
    save("translate", a.label or f"{a.engine}-{a.to}", {"переводы": out}, raw)


def cmd_speller(a):
    items = read_items(a, dedupe=False)
    rows = run("SE::Yandex::Speller", items, {}, threads=a.threads)
    errs, summary = [], []
    for r in rows:
        summary.append({"текст или страница": r["query"][:200], "получено": ok(r), "ошибок": val(r.get("total"))})
        for e in r.get("errors") or []:
            errs.append({"текст или страница": r["query"][:200], "слово": e.get("word"), "варианты": clean(e.get("suggest")), "тип": val(e.get("type"))})
    report_failed(rows)
    save("speller", a.label or f"{len(items)}", {"ошибки": errs, "сводка": summary}, rows)


def cmd_proofread(a):
    texts = read_items(a, dedupe=False)
    rows = run("DeepL::Write", texts, {"language": a.lang}, threads=a.threads)
    out = [{"текст": t, "исправлено": val(r.get("corrected")), "язык": val(r.get("detected")),
            "варианты": "; ".join(clean(v.get("text")) for v in r.get("variants") or []), "получено": ok(r)}
           for t, r in zip(texts, rows)]
    report_failed(rows)
    save("proofread", a.label or f"{len(texts)}", {"правка": out}, rows)


def _openai(a) -> dict:
    # официальный API OpenAI через A-Parser: ключ — OPENAI_API_KEY в .env скилла,
    # модель — --model или OPENAI_MODEL (gpt-5-mini); у моделей gpt-5 в max_tokens входят и рассуждения
    key = ap.ENV.get("OPENAI_API_KEY")
    if not key:
        raise SystemExit("Для --engine openai нужен OPENAI_API_KEY в .env (ключ — platform.openai.com → API keys)")
    return {"api_key": key, "another_model": a.model or ap.ENV.get("OPENAI_MODEL") or "gpt-5-mini", "max_tokens": a.max_tokens}


AI = {
    "googleai": ("FreeAI::GoogleAI", lambda a: {"hl": "ru", "gl": "ru", "domain": "www.google.ru"}),
    "openai": ("OpenAI::ChatGPT", _openai),
    "duckai": ("FreeAI::DuckAI", lambda a: {"model": a.model} if a.model else {}),
    "deepai": ("FreeAI::DeepAI", lambda a: {"model": a.model} if a.model else {}),
}


def cmd_ai(a):
    q = read_items(a)
    sites = [domain_of(s) for s in (a.site or "").split(",") if s.strip()]
    brands = [b.strip() for b in (a.brand or "").split(",") if b.strip()]
    answers, sources, raw = [], [], {}
    names = [x.strip() for x in a.engine.split(",")] if a.engine != "all" else \
        [e for e in AI if e != "openai" or ap.ENV.get("OPENAI_API_KEY")]
    for e in names:
        if e not in AI:
            raise SystemExit(f"Нейросети: {', '.join(AI)}; all — все")
        parser, opts = AI[e]
        rows = run(parser, q, opts(a), threads=a.threads)
        raw[e] = rows
        for r in rows:
            ans = clean(r.get("answer"))
            src = r.get("sources") or []
            row = {"нейросеть": e, "вопрос": r["query"], "получено": ok(r), "ответ": ans, "источников": len(src)}
            if e == "openai":
                row["модель"] = a.model or ap.ENV.get("OPENAI_MODEL") or "gpt-5-mini"
                row["токенов"] = val(r.get("total_tokens"))
            for s in sites:
                brand = s.split(".")[0]
                row[f"{s}: в источниках"] = any(domain_of(x.get("link", "")).endswith(s) for x in src)
                row[f"{s}: в тексте ответа"] = bool(re.search(re.escape(s) + "|" + re.escape(brand), ans, re.I)) if ans else False
            for b in brands:
                row[f"«{b}» в ответе"] = bool(re.search(re.escape(b), ans, re.I)) if ans else False
            answers.append(row)
            for i, s in enumerate(src, 1):
                sources.append({"нейросеть": e, "вопрос": r["query"], "№": i, "url": s.get("link"), "домен": domain_of(s.get("link", "")),
                                "заголовок": clean(s.get("anchor")), "фрагмент": clean(s.get("snippet"))})
        report_failed(rows)
    save("ai", a.label or a.engine.replace(",", "-"), {"ответы": answers, "источники": sources}, raw)


def register(sub):
    p = sub.add_parser("http", help="код ответа, редиректы, title, description, robots, canonical (--browser — сайты с защитой)")
    add_io(p, "адреса")
    p.add_argument("--browser", action="store_true", help="через Chrome внутри A-Parser")
    p.set_defaults(func=cmd_http)

    p = sub.add_parser("extract", help="со страницы: статья (article), блоки текста (text), ссылки (links), язык (lang)")
    add_io(p, "адреса")
    p.add_argument("--what", default="article", choices=["article", "text", "links", "lang"])
    p.add_argument("--browser", action="store_true", help="через Chrome (для сайтов с защитой от ботов)")
    p.set_defaults(func=cmd_extract)

    p = sub.add_parser("screenshot", help="скриншоты страниц в PNG")
    add_io(p, "адреса")
    p.add_argument("--width", type=int, default=1366)
    p.add_argument("--height", type=int, default=768)
    p.add_argument("--full", action="store_true", help="вся страница целиком, а не первый экран")
    p.set_defaults(func=cmd_screenshot)

    p = sub.add_parser("translate", help="перевод: Google, Bing, DeepL")
    add_io(p, "тексты")
    p.add_argument("--engine", default="google", help="google, bing, deepl или несколько через запятую")
    p.add_argument("--to", default="ru", help="на какой язык (ru, en, de …)")
    p.add_argument("--from", dest="src", default="auto", help="с какого языка (auto — определить)")
    p.set_defaults(func=cmd_translate)

    p = sub.add_parser("speller", help="орфография и опечатки: Яндекс Спеллер (текст или адрес страницы)")
    add_io(p, "тексты или адреса")
    p.set_defaults(func=cmd_speller)

    p = sub.add_parser("proofread", help="правка текста DeepL Write: грамматика и стиль (английский, немецкий и др.)")
    add_io(p, "тексты")
    p.add_argument("--lang", default="en-GB", help="язык текста: en-GB, en-US, de-DE, fr-FR …")
    p.set_defaults(func=cmd_proofread)

    p = sub.add_parser("ai", help="ответы нейросетей: Google AI Mode (с источниками), OpenAI, Duck.ai, DeepAI; --site — упоминается ли сайт")
    add_io(p, "вопросы")
    p.add_argument("--engine", default="googleai", help="googleai, openai, duckai, deepai, несколько через запятую или all")
    p.add_argument("--site", help="домены через запятую: отметить упоминание в ответе и в источниках")
    p.add_argument("--brand", help="названия бренда через запятую в любом написании («Окна Века,okna-veka»): упоминание в ответе")
    p.add_argument("--model", help="модель: openai — gpt-5-mini (по умолчанию), gpt-5, gpt-4.1-mini …; duckai/deepai — как в A-Parser")
    p.add_argument("--max-tokens", type=int, default=2000, help="openai: предел токенов ответа вместе с рассуждениями (2000)")
    p.set_defaults(func=cmd_ai)
