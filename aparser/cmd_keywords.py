"""Ключевые слова: Букварикс, Ahrefs (идеи, вопросы, сложность), Google Trends, объявления Яндекс Директа."""

from __future__ import annotations

from datetime import datetime, timezone

from common import add_io, clean, domain_of, label_of, ok, read_items, report_failed, run, save, val


def cmd_keywords(a):
    q = read_items(a)
    sources = ["bukvarix", "ahrefs"] if a.source == "all" else [a.source]
    flat, raw = [], {}
    if "bukvarix" in sources:
        rows = run("Rank::Bukvarix::Keyword", q, {"num_results": a.limit}, threads=a.threads)
        raw["bukvarix"] = rows
        for r in rows:
            for s in r.get("keywords") or []:
                flat.append({"источник": "Букварикс", "запрос": r["query"], "фраза": clean(s.get("key")),
                             "частотность": val(s.get("frequency")), "частотность 2": val(s.get("frequency2")),
                             "слов": val(s.get("wordscount")), "символов": val(s.get("symbolscount"))})
        report_failed(rows)
    if "ahrefs" in sources:
        rows = run("Rank::Ahrefs::KeywordGenerator", q, {"country": a.country}, threads=a.threads)
        raw["ahrefs"] = rows
        for r in rows:
            for kind, arr in (("идея", "ideas"), ("вопрос", "questions")):
                for s in r.get(arr) or []:
                    flat.append({"источник": "Ahrefs", "запрос": r["query"], "фраза": clean(s.get("key")), "тип": kind,
                                 "сложность Ahrefs": val(s.get("difficulty")), "частота Ahrefs": val(s.get("volume")),
                                 "обновлено": str(val(s.get("updated")) or "")[:10]})
        report_failed(rows)
    save("keywords", a.label or a.source, {"фразы": flat}, raw)


def cmd_domain_keywords(a):
    doms = [domain_of(d) for d in read_items(a)]
    rows = run("Rank::Bukvarix::Domain", doms, {"num_results": a.limit, "region": a.region}, threads=a.threads)
    flat, summary = [], []
    for d, r in zip(doms, rows):
        kws = r.get("keywords") or []
        summary.append({"домен": d, "получено": ok(r), "фраз у домена (всего)": val(r.get("totalcount")), "выгружено": len(kws)})
        for s in kws:
            flat.append({"домен": d, "фраза": clean(s.get("key")), "позиция": val(s.get("position")),
                         "частотность": val(s.get("frequency")), "частотность 2": val(s.get("frequency2")),
                         "результатов в поиске": val(s.get("totalcount"))})
    report_failed(rows)
    save("domain-keywords", label_of(a, doms, "domains"), {"фразы": flat, "сводка": summary}, rows)


def cmd_kd(a):
    q = read_items(a)
    rows = run("Rank::Ahrefs::KeywordDifficulty", q, {"country": a.country}, threads=a.threads)
    summary, top = [], []
    for r in rows:
        summary.append({"фраза": r["query"], "получено": ok(r), "сложность (KD)": val(r.get("difficulty")),
                        "shortage": val(r.get("shortage")), "страниц в ТОПе": len(r.get("serp") or [])})
        for i, s in enumerate(r.get("serp") or [], 1):
            top.append({"фраза": r["query"], "№": i, "url": s.get("link"), "домен": domain_of(s.get("link", "")),
                        "заголовок": clean(s.get("anchor")), "DR": val(s.get("dr")), "UR": val(s.get("ur")), "AR": val(s.get("ar")),
                        "беклинков": val(s.get("bl")), "ссылающихся доменов": val(s.get("domains")),
                        "трафик": val(s.get("traffic")), "ключей": val(s.get("keywords"))})
    report_failed(rows)
    save("kd", label_of(a, q, "kd"), {"сложность": summary, "ТОП": top}, rows)


def _ts(x):
    try:
        return datetime.fromtimestamp(int(x), tz=timezone.utc).strftime("%Y-%m-%d")
    except (TypeError, ValueError, OSError):
        return x


def cmd_trends(a):
    q = read_items(a)
    rows = run("SE::Google::Trends", q, {}, threads=a.threads)
    sheets = {"связанные запросы": [], "растущие запросы": [], "по регионам": [], "по времени": [], "темы": []}
    for r in rows:
        for s in r.get("related_queries") or []:
            sheets["связанные запросы"].append({"запрос": r["query"], "связанный запрос": clean(s.get("rquery")), "интерес": val(s.get("interest"))})
        for s in r.get("related_queries_rising") or []:
            sheets["растущие запросы"].append({"запрос": r["query"], "связанный запрос": clean(s.get("rquery")), "рост": val(s.get("interest"))})
        for s in r.get("interest_byregion") or []:
            sheets["по регионам"].append({"запрос": r["query"], "регион": s.get("region"), "интерес": val(s.get("interest"))})
        for s in r.get("interest_bytime") or []:
            sheets["по времени"].append({"запрос": r["query"], "дата": _ts(s.get("time")), "интерес": val(s.get("interest"))})
        for kind, arr in (("популярная", "related_topics"), ("растущая", "related_topics_rising")):
            for s in r.get(arr) or []:
                sheets["темы"].append({"запрос": r["query"], "тема": clean(s.get("topic")), "категория": clean(s.get("category")),
                                       "тип": kind, "интерес": val(s.get("interest"))})
    report_failed(rows)
    save("trends", label_of(a, q, "trends"), sheets, rows)


def cmd_ads(a):
    q = read_items(a)
    rows = run("SE::Yandex::Direct", q, {"geo": a.lr, "pagecount": a.pages}, threads=a.threads)
    flat, summary = [], []
    for r in rows:
        ads = r.get("ads") or []
        summary.append({"запрос": r["query"], "получено": ok(r), "объявлений": len(ads),
                        "доменов": len({x.get("domain") for x in ads if x.get("domain")})})
        for i, s in enumerate(ads, 1):
            links = [f"{clean(s.get(f'anchor{n}'))} — {s.get(f'link{n}')}" for n in range(1, 5) if s.get(f"link{n}")]
            tags = [clean(s.get(f"tag{n}")) for n in range(1, 5) if s.get(f"tag{n}")]
            flat.append({"запрос": r["query"], "№": i, "домен": s.get("domain"), "заголовок": clean(s.get("title")),
                         "текст": clean(s.get("text")), "быстрые ссылки": "; ".join(links), "уточнения": "; ".join(tags),
                         "сайт в органике": val(s.get("organic"))})
    report_failed(rows)
    save("ads", label_of(a, q, "ads"), {"объявления": flat, "сводка": summary}, rows)


def register(sub):
    p = sub.add_parser("keywords", help="расширение фраз: Букварикс (частотности) и Ahrefs (идеи, вопросы, сложность)")
    add_io(p, "фразы")
    p.add_argument("--source", default="all", choices=["all", "bukvarix", "ahrefs"])
    p.add_argument("--limit", type=int, default=1000, help="Букварикс: сколько фраз на запрос (до 1000 на бесплатном ключе)")
    p.add_argument("--country", default="ru", help="Ahrefs: страна (ru, us, kz …)")
    p.set_defaults(func=cmd_keywords)

    p = sub.add_parser("domain-keywords", help="по каким фразам домен в поиске (Букварикс)")
    add_io(p, "домены")
    p.add_argument("--limit", type=int, default=1000)
    p.add_argument("--region", default="msk", help="регион базы Букварикса (msk)")
    p.set_defaults(func=cmd_domain_keywords)

    p = sub.add_parser("kd", help="сложность фразы (KD) и ТОП по Ahrefs")
    add_io(p, "фразы")
    p.add_argument("--country", default="ru")
    p.set_defaults(func=cmd_kd)

    p = sub.add_parser("trends", help="Google Trends: связанные и растущие запросы, интерес по регионам и по времени (мир, 5 лет)")
    add_io(p, "фразы")
    p.set_defaults(func=cmd_trends)

    p = sub.add_parser("ads", help="объявления Яндекс Директа по запросам")
    add_io(p)
    p.add_argument("--lr", type=int, default=213, help="регион (213 Москва)")
    p.add_argument("--pages", type=int, default=1)
    p.set_defaults(func=cmd_ads)
