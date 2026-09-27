"""Приложения, магазины, карты, YouTube, Reddit, TikTok, Telegram, курс криптовалют.

Из соцсетей берём контент и счётчики. Имена авторов комментариев, списки подписчиков
и email не выгружаем — это персональные данные посторонних людей, для SEO они не нужны.
"""

from __future__ import annotations

import re

from common import add_io, clean, date_of, domain_of, duration, joined, label_of, ok, read_items, report_failed, run, save, val

# ---------- приложения и магазины ----------

APPS = {
    "appstore": ("AppStore::Apps", lambda a: {"country": a.country, "limit": a.limit}),
    "googleplay": ("GooglePlay::Apps", lambda a: {"country": a.country, "lang": a.lang}),
}


def cmd_apps(a):
    q = read_items(a)
    flat, raw = [], {}
    for e in [x.strip() for x in a.store.split(",")] if a.store != "all" else list(APPS):
        parser, opts = APPS[e]
        rows = run(parser, q, opts(a), threads=a.threads)
        raw[e] = rows
        for r in rows:
            for i, s in enumerate(r.get("serp") or [], 1):
                flat.append({"магазин": e, "запрос": r["query"], "№": i, "приложение": clean(s.get("name")), "ссылка": s.get("link"),
                             "разработчик": clean(s.get("author")), "рейтинг": val(s.get("rating")), "оценок": val(s.get("rcount")),
                             "загрузок": val(s.get("downloads")), "цена": val(s.get("price")),
                             "категория": clean(s.get("category") or s.get("genre")), "подзаголовок": clean(s.get("subtitle")),
                             "версия": val(s.get("version")), "описание": clean(s.get("desc"))[:500]})
        report_failed(rows)
    save("apps", a.label or a.store, {"приложения": flat}, raw)


SHOPS = {
    "amazon": ("Shop::Amazon", "products", lambda a: {"pagecount": a.pages, "domain": a.amazon_domain}),
    "aliexpress": ("Shop::AliExpress", "serp", lambda a: {"pagecount": a.pages}),
    "market": ("Shop::Yandex::Market", "products", lambda a: {"page_count": a.pages}),
}


def cmd_shop(a):
    q = read_items(a)
    flat, raw = [], {}
    for e in [x.strip() for x in a.store.split(",")] if a.store != "all" else list(SHOPS):
        parser, arr, opts = SHOPS[e]
        rows = run(parser, q, opts(a), threads=a.threads)
        raw[e] = rows
        for r in rows:
            for i, s in enumerate(r.get(arr) or [], 1):
                flat.append({"магазин": e, "запрос": r["query"], "№": i, "товар": clean(s.get("title")),
                             "ссылка": s.get("link") or s.get("cardlink"), "цена": val(s.get("price") or s.get("amountfrom")),
                             "старая цена": val(s.get("oldprice") or s.get("old_price") or s.get("price_old")),
                             "цена до": val(s.get("amountto")), "валюта": val(s.get("currency")), "рейтинг": val(s.get("rating")),
                             "отзывов": val(s.get("commentscount")), "продано / купили": val(s.get("sold_count") or s.get("boughtCount") or s.get("bought")),
                             "продавец / магазин": clean(s.get("owner") or s.get("store")), "предложений": val(s.get("proposalscount") or s.get("sellerscount")),
                             "доставка": clean(s.get("ship")), "артикул": val(s.get("asin")), "картинка": s.get("imagelink") or s.get("img")})
        report_failed(rows)
    save("shop", a.label or a.store, {"товары": flat}, raw)


# ---------- карты ----------

def cmd_maps(a):
    q = read_items(a)
    lat, lng = [x.strip() for x in a.at.split(",")]
    engines = [x.strip() for x in a.engine.split(",")] if a.engine != "all" else ["google", "yandex"]
    flat, raw = [], {}
    for e in engines:
        if e == "google":
            rows = run("Maps::Google", q, {"at": f"{lat},{lng}", "z": a.zoom, "hl": "ru", "gl": "ru"}, threads=a.threads)
        elif e == "yandex":
            # у Яндекса порядок обратный: долгота,широта
            rows = run("Maps::Yandex", q, {"ll": f"{lng},{lat}", "z": a.zoom, "lang": "ru", "pagecount": a.pages}, threads=a.threads)
        else:
            raise SystemExit("Карты: google, yandex или all")
        raw[e] = rows
        for r in rows:
            for i, s in enumerate(r.get("serp") or [], 1):
                flat.append({"карты": e, "запрос": r["query"], "№": i, "название": clean(s.get("name")), "адрес": clean(s.get("address")),
                             "рейтинг": val(s.get("rating")), "отзывов": val(s.get("reviews")), "категории": clean(s.get("categories")),
                             "сайт": val(s.get("site")), "телефоны": clean(s.get("phones")), "часы работы": clean(s.get("hours") or s.get("worktime")),
                             "статус": clean(s.get("timestatus") or s.get("status")), "цены": clean(s.get("price")),
                             "координаты": clean(s.get("coordinates")), "карточка": s.get("link"),
                             "владелец подтвердил": val(s.get("claim")), "соцсети": clean(s.get("social"))})
        report_failed(rows)
    save("maps", a.label or f"{a.engine}", {"организации": flat}, raw)


# ---------- YouTube ----------

def cmd_youtube(a):
    urls = read_items(a)
    ov = {"commentPages": a.comments, "replyPages": 0, "relatedPages": 1 if a.related else 0, "subtitlesLang": a.subs, "hl": "ru"}
    rows = run("SE::YouTube::Video", urls, ov, threads=a.threads)
    sh = {"видео": [], "субтитры": [], "главы": [], "комментарии": [], "похожие": []}
    for r in rows:
        subs = r.get("subtitles") or []
        sh["видео"].append({"url": r["query"], "получено": ok(r), "название": clean(r.get("title")), "канал": clean(r.get("author")),
                            "подписчиков": val(r.get("subscribers")), "просмотров": val(r.get("viewsCount")), "лайков": val(r.get("likesCount")),
                            "комментариев": val(r.get("commentsCount")), "дата": date_of(r.get("date")), "длительность": duration(r.get("duration")),
                            "теги": joined(r.get("tags"), "tag"), "глав": len(r.get("chapters") or []),
                            "описание": str(val(r.get("description")) or ""), "расшифровка": " ".join(clean(s.get("text")) for s in subs)})
        for s in subs:
            sh["субтитры"].append({"url": r["query"], "начало, с": val(s.get("start")), "длит., с": val(s.get("duration")), "текст": clean(s.get("text"))})
        for s in r.get("chapters") or []:
            sh["главы"].append({"url": r["query"], "глава": clean(s.get("title")), "начало": val(s.get("start"))})
        for s in r.get("comments") or []:
            sh["комментарии"].append({"url": r["query"], "комментарий": clean(s.get("text")), "когда": val(s.get("time")),
                                      "ответ на": "да" if s.get("pid") else ""})
        for s in r.get("related") or []:
            sh["похожие"].append({"url": r["query"], "похожее видео": s.get("link"), "название": clean(s.get("title")),
                                  "канал": clean(s.get("author")), "просмотров": val(s.get("viewsCount")), "длительность": duration(s.get("duration"))})
        r.pop("comments", None)        # авторы комментариев — не храним и в json
    report_failed(rows)
    save("youtube", label_of(a, urls, "videos"), sh, rows)


# ---------- Reddit ----------

def cmd_reddit(a):
    items = read_items(a)
    posts_q = [x for x in items if "/comments/" not in x]
    post_urls = [x for x in items if "/comments/" in x]
    sh, raw = {"посты": [], "комментарии": []}, {}
    if posts_q:
        rows = run("Reddit::Posts", posts_q, {"sort": a.sort, "time": a.time, "pagecount": a.pages}, threads=a.threads)
        raw["posts"] = rows
        for r in rows:
            for s in r.get("posts") or []:
                sh["посты"].append({"запрос": r["query"], "заголовок": clean(s.get("title")), "сабреддит": s.get("subreddit"),
                                    "голосов": val(s.get("upvotes")), "комментариев": val(s.get("comments")), "создан": date_of(s.get("created")),
                                    "метка": clean(s.get("flair")), "ссылка": s.get("link"), "внешняя ссылка": val(s.get("url")),
                                    "реклама": val(s.get("promoted")), "текст": clean(s.get("content"))[:2000]})
            for s in r.get("posts") or []:
                s.pop("author", None); s.pop("authorFlair", None)
        report_failed(rows)
    if post_urls:
        rows = run("Reddit::PostInfo", post_urls, {}, threads=a.threads)
        raw["post"] = rows
        for r in rows:
            sh["посты"].append({"запрос": r["query"], "заголовок": clean(r.get("title")), "сабреддит": val(r.get("subreddit")),
                                "голосов": val(r.get("upvotes")), "комментариев": val(r.get("commentsCount")), "создан": date_of(r.get("created")),
                                "метка": clean(r.get("flair")), "ссылка": val(r.get("link")), "внешняя ссылка": val(r.get("url")),
                                "реклама": val(r.get("promoted")), "текст": clean(r.get("content"))[:2000]})
            for c in r.get("comments") or []:
                sh["комментарии"].append({"пост": r["query"], "комментарий": clean(c.get("text")), "ответ на": "да" if c.get("parentId") else ""})
            r.pop("comments", None); r.pop("author", None); r.pop("authorFlair", None)
        report_failed(rows)
    save("reddit", label_of(a, items, "reddit"), sh, raw)


# ---------- TikTok ----------

def cmd_tiktok(a):
    items = read_items(a)
    urls = [x if x.startswith("http") else f"https://www.tiktok.com/@{x.lstrip('@')}" for x in items]
    rows = run("Social::TikTok::Profile", urls, {"subscribersPageCount": 0, "subscriptionsPageCount": 0}, threads=a.threads)
    out = []
    for u, r in zip(urls, rows):
        out.append({"профиль": u, "получено": ok(r), "ник": val(r.get("username")), "имя": clean(r.get("name")),
                    "подписчиков": val(r.get("followers")), "подписок": val(r.get("followings")), "видео": val(r.get("videos")),
                    "тип": val(r.get("type")), "ссылка в профиле": val(r.get("link")), "описание": clean(r.get("bio"))})
        for k in ("subscribers", "subscriptions", "email"):
            r.pop(k, None)
    report_failed(rows)
    save("tiktok", label_of(a, items, "profiles"), {"профили": out}, rows)


# ---------- Telegram ----------

def channel_of(s: str) -> str:
    s = s.strip()
    m = re.search(r"t\.me/(?:s/)?([A-Za-z0-9_]{4,})", s)
    return (m.group(1) if m else s.lstrip("@")).strip("/")


def cmd_telegram(a):
    chans = [channel_of(x) for x in read_items(a)]
    # номер последнего поста — со страницы t.me/s/<канал> (там 20 последних постов)
    pages = run("Net::HTTP", [f"https://t.me/s/{c}" for c in chans], {}, threads=a.threads, keep=("data",), progress=None)
    queries, info = [], []
    for c, r in zip(chans, pages):
        ids = [int(x) for x in re.findall(rf'data-post="{re.escape(c)}/(\d+)"', str(r.get("data") or ""), re.I)]
        last = max(ids) if ids else None
        info.append({"канал": c, "последний пост": last})
        if last:
            queries += [f"https://t.me/{c}/{n}" for n in range(last, max(0, last - a.last), -1)]
    rows = run("Telegram::GroupScraper", queries, {"checkPostsCount": 1, "skipForwards": 1 if a.skip_forwards else 0}, threads=a.threads) if queries else []
    posts, chan = [], {}
    for r in rows:
        if not ok(r) or not val(r.get("url")):
            continue
        c = channel_of(r["query"])
        chan.setdefault(c, {"канал": c, "название": clean(r.get("chat_title")), "подписчиков": val(r.get("chat_members")),
                            "описание": clean(r.get("chat_description")), "фото": val(r.get("chat_photos")), "видео": val(r.get("chat_videos")),
                            "файлов": val(r.get("chat_files")), "ссылок": val(r.get("chat_links"))})
        posts.append({"канал": c, "пост": r.get("url"), "дата": str(val(r.get("message_date")) or "")[:19].replace("T", " "),
                      "просмотров": val(r.get("views")), "реакций": val(r.get("reactions_total")),
                      "реакции": ", ".join(f"{x.get('emoji')} {x.get('count')}" for x in r.get("reactions") or []),
                      "текст": str(val(r.get("message_text_plain")) or ""), "ссылки": joined(r.get("links"), "url"),
                      "фото": len(r.get("message_photos") or []), "видео": len(r.get("message_videos") or []),
                      "переслано из": val(r.get("forward_from")) or val(r.get("forward_from_url")), "ответ на": val(r.get("reply_to_url"))})
    for i in info:
        chan.setdefault(i["канал"], {"канал": i["канал"]})["последний пост"] = i["последний пост"]
    print(f"  постов получено: {len(posts)} из {len(queries)} запрошенных (удалённые и служебные номера пропускаются)")
    save("telegram", label_of(a, chans, "channels"), {"посты": posts, "каналы": list(chan.values())}, rows)


# ---------- криптовалюты ----------

def cmd_crypto(a):
    q = [x.lower() for x in read_items(a)]
    rows = run("CoinMarketCap::LastPrice", q, {}, threads=a.threads)
    out = [{"slug": r["query"], "название": val(r.get("name")), "тикер": val(r.get("currency")), "цена, $": val(r.get("price")), "получено": ok(r)}
           for r in rows]
    report_failed(rows)
    save("crypto", label_of(a, q, "coins"), {"цены": out}, rows)


def register(sub):
    p = sub.add_parser("apps", help="поиск приложений: App Store, Google Play")
    add_io(p)
    p.add_argument("--store", default="all", help="appstore, googleplay или all")
    p.add_argument("--country", default="ru")
    p.add_argument("--lang", default="ru", help="Google Play: язык")
    p.add_argument("--limit", type=int, default=50, help="App Store: сколько приложений")
    p.set_defaults(func=cmd_apps)

    p = sub.add_parser("shop", help="товары: Amazon, AliExpress, Яндекс Маркет")
    add_io(p)
    p.add_argument("--store", default="market", help="amazon, aliexpress, market, несколько через запятую или all")
    p.add_argument("--pages", type=int, default=1)
    p.add_argument("--amazon-domain", default="www.amazon.com")
    p.set_defaults(func=cmd_shop)

    p = sub.add_parser("maps", help="организации с Google Карт и Яндекс Карт: адрес, телефоны, сайт, рейтинг")
    add_io(p)
    p.add_argument("--engine", default="yandex", help="google, yandex или all")
    p.add_argument("--at", default="55.7558,37.6173", help="центр поиска «широта,долгота» (Москва)")
    p.add_argument("--zoom", type=int, default=12, help="масштаб карты: 11–12 — город, 14–15 — район")
    p.add_argument("--pages", type=int, default=5, help="Яндекс: страниц списка")
    p.set_defaults(func=cmd_maps)

    p = sub.add_parser("youtube", help="видео YouTube: данные, расшифровка (субтитры), главы, комментарии без авторов")
    add_io(p, "адреса видео")
    p.add_argument("--subs", default="ru", help="язык субтитров (ru, en …)")
    p.add_argument("--comments", type=int, default=0, help="страниц комментариев (0 — не собирать)")
    p.add_argument("--related", action="store_true", help="собрать похожие видео")
    p.set_defaults(func=cmd_youtube)

    p = sub.add_parser("reddit", help="Reddit: посты по запросу или пост с комментариями (по адресу)")
    add_io(p, "запросы или адреса постов")
    p.add_argument("--sort", default="relevance", help="relevance, hot, top, new, comments")
    p.add_argument("--time", default="all", help="all, year, month, week, day, hour")
    p.add_argument("--pages", type=int, default=1)
    p.set_defaults(func=cmd_reddit)

    p = sub.add_parser("tiktok", help="профиль TikTok: подписчики, видео, описание (без списков подписчиков)")
    add_io(p, "профили (@ник или адрес)")
    p.set_defaults(func=cmd_tiktok)

    p = sub.add_parser("telegram", help="последние посты публичного канала Telegram: текст, просмотры, реакции, ссылки")
    add_io(p, "каналы (@канал или t.me/канал)")
    p.add_argument("--last", type=int, default=20, help="сколько последних постов (20)")
    p.add_argument("--skip-forwards", action="store_true", help="без пересланных постов")
    p.set_defaults(func=cmd_telegram)

    p = sub.add_parser("crypto", help="курс криптовалюты в долларах (CoinMarketCap, по slug: bitcoin, ethereum …)")
    add_io(p, "slug монет")
    p.set_defaults(func=cmd_crypto)
