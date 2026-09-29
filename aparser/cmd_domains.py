"""Домены и ссылки: сводная проверка доменов, Ahrefs (DR, доноры, трафик, битые ссылки), MOZ,
Majestic, Mustat, SecurityTrails, IP и хостинг, проверка ссылки на странице-доноре."""

from __future__ import annotations

import json
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pandas as pd
import requests

import aparser as ap
import donors as donors_rules
import progress as pg
from common import add_io, clean, domain_of, is_ip, joined, label_of, num_short, ok, read_items, report_failed, run, save, val

RKN_LIST = "https://antifilter.download/list/domains.lst"   # выгрузка реестра РКН, ~1,7 млн доменов


def _d(d):
    return d


def _http(d):
    return f"http://{d}/"


def _https(d):
    return f"https://{d}/"


def _dns_ips(r) -> str:
    """Все IP домена; ip — первый из них, поэтому берётся, только если списка нет."""
    one, many = val(r.get("ip")), joined(r.get("ips"), "ip")
    return many or one


def _dns_records(r) -> str:
    """DNS-записи Net::DNS (JSON-строка в поле entry) → «A 1.2.3.4 (TTL 300), …». Тип записей — опция
    query_type парсера, по умолчанию A."""
    out = []
    for x in r.get("records") or []:
        try:
            e = json.loads(x.get("entry") or "{}")
        except (ValueError, TypeError, AttributeError):
            continue
        data = e.get("data")
        out.append(f"{e.get('type')} {json.dumps(data, ensure_ascii=False) if isinstance(data, (dict, list)) else data} (TTL {e.get('ttl')})")
    return ", ".join(out)


# проверка → (парсер, запрос из домена, настройки, колонки из результата)
CHECKS = {
    "whois": ("Net::Whois", _d, {}, lambda r: {
        "зарегистрирован": val(r.get("registered")), "регистратор": val(r.get("registrar")), "создан": val(r.get("creation_date")),
        "обновлён": val(r.get("updated_date")), "оплачен до": val(r.get("expire_date")), "освобождается": val(r.get("free_date")),
        # статусы домена (clientHold, redemptionPeriod, pendingDelete…) — для дропов важно, на каком он этапе
        "статусы": joined(r.get("statuses"), "status"), "NS": joined(r.get("ns"), "server")}),
    "archive": ("Rank::Archive", _d, {}, lambda r: {
        "архив: первый снимок": val(r.get("first")), "архив: последний": val(r.get("last")), "архив: снимков": val(r.get("times"))}),
    "dns": ("Net::DNS", _d, {}, lambda r: {"IP": _dns_ips(r), "DNS: записи": _dns_records(r)}),
    "cms": ("Rank::CMS", _http, {"useproxy": 0}, lambda r: {"CMS": joined(r.get("list"), "cms") or val(r.get("cms"))}),
    "sqi": ("SE::Yandex::SQI", _d, {}, lambda r: {
        "ИКС": val(r.get("sqi")), "Яндекс: рейтинг": val(r.get("rating")), "Яндекс: отзывов": val(r.get("reviews")),
        "Яндекс: официальный сайт": val(r.get("official"))}),
    "rkn_ap": ("Check::RosKomNadzor", _d, {}, lambda r: {
        "РКН (сайт РКН)": val(r.get("exists")), "РКН: основание": clean(r.get("number")), "РКН: дата": val(r.get("date")),
        "РКН: орган": clean(r.get("maker"))}),
    # при GOOGLE_SAFEBROWSING_KEY в .env — напрямую в API Google своим ключом (safe_google_api), иначе A-Parser
    "safe_google": ("SE::Google::SafeBrowsing", _d, {}, lambda r: {"Google SafeBrowsing": val(r.get("exists")),
                                                                   **({"Google SafeBrowsing: угрозы": r["types"]} if r.get("types") else {})}),
    "safe_yandex": ("SE::Yandex::SafeBrowsing", _d, {}, lambda r: {"Яндекс SafeBrowsing": val(r.get("exists"))}),
    "hacked": ("SE::Google::Compromised", _http, {}, lambda r: {"Google: «сайт взломан»": val(r.get("compromised"))}),
    "ahrefs": ("Rank::Ahrefs", _d, {}, lambda r: {
        "Ahrefs DR": val(r.get("rating")), "Ahrefs: беклинков": val(r.get("bl")), "Ahrefs: ссылающихся доменов": val(r.get("domains")),
        "Ahrefs: доменов dofollow, %": val(r.get("domains_dofollow"))}),
    "traffic": ("Rank::Ahrefs::TrafficChecker", _d, {}, lambda r: {
        "Ahrefs: трафик в месяц": val(r.get("traffic")), "Ahrefs: стоимость трафика, $": val(r.get("cost"))}),
    "moz": ("Rank::MOZ", _d, {}, lambda r: {
        "MOZ DA": num_short(r.get("authority")), "MOZ Spam Score, %": num_short(r.get("spam")),
        "MOZ: ссылающихся доменов": num_short(r.get("linking")), "MOZ: ключей": num_short(r.get("keywords"))}),
    "majestic": ("Rank::MajesticSEO", _d, {}, lambda r: {
        "Majestic TF": val(r.get("trustflow")), "Majestic CF": val(r.get("citationflow")), "Majestic: беклинков": val(r.get("backlinks")),
        "Majestic: ссылающихся доменов": val(r.get("domains")), "Majestic: URL в индексе": val(r.get("indexed"))}),
    "mustat": ("Rank::Mustat", _d, {}, lambda r: {
        "Mustat: визитов в день": val(r.get("traffic")), "Mustat: визитов в неделю": val(r.get("trafficWeek")),
        "Mustat: визитов в месяц": val(r.get("trafficMonth")), "Mustat: визитов в год": val(r.get("trafficYear")),
        "Mustat: стоимость сайта, $": val(r.get("worth")), "Mustat: рейтинг": val(r.get("rating"))}),
    "curlie": ("Rank::Curlie", _d, {}, lambda r: {"в каталоге Curlie": val(r.get("exists"))}),
    "social": ("Rank::Social::Signal", _https, {}, lambda r: {
        "VK: репостов главной": val(r.get("vk_share")), "Pinterest: сохранений главной": val(r.get("pinterest_like"))}),
    "radar": ("Cloudflare::Radar", _d, {}, lambda r: {"Cloudflare: категории": joined(r.get("categories"), "name")}),
    "trails": ("SecurityTrails::Domain", _d, {}, lambda r: {
        "SecurityTrails: A": joined(r.get("aRecords"), "ip"), "SecurityTrails: NS": joined(r.get("nsRecords"), "ns"),
        "SecurityTrails: MX": joined(r.get("mxRecords"), "host"), "SecurityTrails: поддоменов": val(r.get("subdomain_count"))}),
}
SPECIAL = ("rkn", "hosting")                    # реестр antifilter и хостинг по IP — не парсер «домен → ответ»
ALIASES = {"safe": ["safe_google", "safe_yandex"]}
PRESETS = {
    "default": ["whois", "archive", "rkn", "sqi", "dns"],
    "pbn": ["whois", "archive", "rkn", "safe_google", "safe_yandex", "hacked", "ahrefs", "moz", "majestic", "mustat", "sqi"],
    "all": [c for c in CHECKS if c != "rkn_ap"] + ["rkn", "hosting"],
}


def parse_checks(s: str) -> list[str]:
    out = []
    for c in [x.strip() for x in s.split(",") if x.strip()]:
        out += PRESETS.get(c) or ALIASES.get(c) or [c]
    bad = [c for c in out if c not in CHECKS and c not in SPECIAL]
    if bad:
        raise SystemExit(f"Неизвестные проверки {bad}. Есть: {', '.join(list(CHECKS) + list(SPECIAL))}; "
                         f"наборы: {', '.join(PRESETS)}; safe = safe_google + safe_yandex")
    return list(dict.fromkeys(out))


def safe_google_api(doms: list[str]) -> list[dict]:
    """Google Safe Browsing напрямую, своим ключом (GOOGLE_SAFEBROWSING_KEY в .env).

    Встроенный в A-Parser общий ключ к вечеру упирается в квоту Google — 429 на каждый запрос
    (27.09.2026), и повторы через другие прокси не помогают: лимит на ключе, а не на IP.
    Ответ — в том же виде, что у парсера: exists = 1 (в чёрном списке) или 0."""
    key = ap.ENV["GOOGLE_SAFEBROWSING_KEY"]
    run_ = pg.CURRENT
    idx = run_.stage("Google Safe Browsing API", len(doms)) if run_ else None
    out = []
    for i in range(0, len(doms), 500):
        part = doms[i:i + 500]
        body = {"client": {"clientId": "aparser-skill", "clientVersion": "1.0"},
                "threatInfo": {"threatTypes": ["MALWARE", "SOCIAL_ENGINEERING", "UNWANTED_SOFTWARE", "POTENTIALLY_HARMFUL_APPLICATION"],
                               "platformTypes": ["ANY_PLATFORM"], "threatEntryTypes": ["URL"],
                               "threatEntries": [{"url": f"http://{d}/"} for d in part]}}
        try:
            r = requests.post("https://safebrowsing.googleapis.com/v4/threatMatches:find", params={"key": key}, json=body, timeout=60)
            r.raise_for_status()
            hits = {m["threat"]["url"] for m in r.json().get("matches", [])}
            out += [{"query": d, "success": 1, "exists": int(f"http://{d}/" in hits), "types": ", ".join(sorted(
                {m["threatType"] for m in r.json().get("matches", []) if m["threat"]["url"] == f"http://{d}/"}))} for d in part]
        except (requests.RequestException, ValueError) as e:
            code = getattr(getattr(e, "response", None), "status_code", "")
            print(f"  Google Safe Browsing API: ошибка {code or type(e).__name__} — эти домены не получены")   # без адреса: в нём ключ
            out += [{"query": d, "success": 0} for d in part]
        if run_:
            run_.advance(idx, len(out), sum(1 for x in out if x["success"] == 1))
    print(f"  Google Safe Browsing API (свой ключ): {sum(1 for x in out if x['success'] == 1)}/{len(doms)}")
    return out


def rkn_registry(doms: list[str]) -> dict[str, int]:
    blocked = {l.strip().lower() for l in requests.get(RKN_LIST, timeout=180).text.splitlines() if l.strip()}
    print(f"  реестр РКН (antifilter): {len(blocked):,} доменов".replace(",", " "))
    return {d: int(d in blocked) for d in doms}


def hosting(ips: dict[str, str], threads: int) -> dict[str, dict]:
    """Хостинг по первому IP домена (IP::Info): компания, ASN, страна."""
    uniq = sorted({ip for ip in ips.values() if ip})
    if not uniq:
        return {}
    rows = run("IP::Info", uniq, {}, threads=threads)
    info = {r["query"]: r for r in rows}
    out = {}
    for d, ip in ips.items():
        r = info.get(ip) or {}
        out[d] = {"хостинг: компания": val(r.get("companyName")) or val(r.get("name")), "хостинг: ASN": val(r.get("asn")),
                  "хостинг: страна": val(r.get("country")), "хостинг: тип": val(r.get("companyType")) or val(r.get("type"))}
    return out


def cmd_domains(a):
    doms = [domain_of(d) for d in read_items(a)]
    checks = parse_checks(a.checks)
    table = {d: {"домен": d} for d in doms}
    raw = {}
    parsers = [c for c in checks if c in CHECKS]
    if "hosting" in checks and "dns" not in parsers:
        parsers.append("dns")

    def one_check(c):
        if c == "safe_google" and ap.ENV.get("GOOGLE_SAFEBROWSING_KEY"):
            return c, safe_google_api(doms)
        parser, qf, ov, _ = CHECKS[c]
        return c, run(parser, [qf(d) for d in doms], ov, threads=a.threads)

    with ThreadPoolExecutor(min(6, max(1, len(parsers)))) as ex:
        results = list(ex.map(one_check, parsers))
    for c, rows in results:
        raw[c] = rows
        extract = CHECKS[c][3]
        for d, r in zip(doms, rows):
            if ok(r):
                table[d].update(extract(r))
            else:
                table[d][f"{c}: ошибка"] = "не получено"
        bad = [d for d, r in zip(doms, rows) if not ok(r)]
        if bad:
            print(f"  {c}: не получено {len(bad)} — {bad[:5]}")
    if "rkn" in checks:
        for d, v in rkn_registry(doms).items():
            table[d]["РКН (реестр)"] = v
    if "hosting" in checks:
        first_ip = {d: next(iter((x.get("ip") for x in (r.get("ips") or []) if x.get("ip"))), None)
                    for d, r in zip(doms, dict(results)["dns"])}
        for d, v in hosting(first_ip, a.threads).items():
            table[d].update(v)
    save("domains", a.label or f"{len(doms)}_{a.checks.replace(',', '-')}"[:40], {"домены": list(table.values())}, raw)


def cmd_ahrefs(a):
    # тот же ответ, что у бесплатного ahrefs.com/backlink-checker: DR, беклинки, домены,
    # доля dofollow и 20 доноров (по одной ссылке с домена); капча Turnstile — CapMonster
    items = read_items(a)
    q = items if a.exact else list(dict.fromkeys(domain_of(x) for x in items))
    rows = run("Rank::Ahrefs", q, {"mode": "exact"} if a.exact else {}, threads=a.threads)
    summary, donors = [], []
    for r in rows:
        found = [{"сайт": r["query"], "#": i, "донор": domain_of(b.get("page", "")), "DR донора": val(b.get("dr")),
                  "страница донора": b.get("page"), "title донора": clean(b.get("title")), "анкор": clean(b.get("anchor")),
                  "текст до анкора": clean(b.get("preAnchor")), "текст после анкора": clean(b.get("postAnchor")),
                  "куда ведёт": b.get("url"), "редирект": b.get("redirect_code") or ""} for i, b in enumerate(r.get("backlinks") or [], 1)]
        site = domain_of(r["query"])
        seo_site = donors_rules.is_seo_site(site)
        reasons = donors_rules.classify(found, site, seo_site)
        for d, why in zip(found, reasons):
            d["ферма"] = bool(why)
            d["признак фермы"] = why
        summary.append({"сайт": r["query"], "DR": val(r.get("rating")) if ok(r) else None,
                        "ASpamRank": donors_rules.aspamrank(reasons) if ok(r) else None,
                        "доноров в выборке": len(found) if ok(r) else None, "SEO-тематика": seo_site,
                        "беклинков": val(r.get("bl")), "беклинков dofollow, %": val(r.get("bl_dofollow")),
                        "ссылающихся доменов": val(r.get("domains")), "доменов dofollow, %": val(r.get("domains_dofollow")), "получено": ok(r)})
        donors += found
    report_failed(rows)
    save("ahrefs", label_of(a, q, "sites"), {"сводка": summary, "доноры": donors}, rows)


def cmd_traffic(a):
    doms = [domain_of(d) for d in read_items(a)]
    rows = run("Rank::Ahrefs::TrafficChecker", doms, {}, threads=a.threads)
    sh = {"сводка": [], "ключи": [], "страницы": [], "страны": [], "история": []}
    for d, r in zip(doms, rows):
        sh["сводка"].append({"домен": d, "получено": ok(r), "трафик в месяц": val(r.get("traffic")), "стоимость трафика, $": val(r.get("cost"))})
        for s in r.get("keywords") or []:
            sh["ключи"].append({"домен": d, "фраза": clean(s.get("keyword")), "позиция": val(s.get("position")), "трафик": val(s.get("traffic"))})
        for s in r.get("top_pages") or []:
            sh["страницы"].append({"домен": d, "url": s.get("url"), "трафик": val(s.get("traffic")), "доля": val(s.get("share"))})
        for s in r.get("countries") or []:
            sh["страны"].append({"домен": d, "страна": s.get("country"), "доля": val(s.get("share"))})
        for s in r.get("history") or []:
            sh["история"].append({"домен": d, "дата": s.get("date"), "органический трафик": val(s.get("organic"))})
    report_failed(rows)
    save("traffic", label_of(a, doms, "domains"), sh, rows)


def cmd_broken(a):
    doms = [domain_of(d) for d in read_items(a)]
    rows = run("Rank::Ahrefs::BrokenLinks", doms, {}, threads=a.threads)
    sh = {"сводка": [], "битые входящие": [], "битые исходящие": []}
    for d, r in zip(doms, rows):
        sh["сводка"].append({"домен": d, "получено": ok(r), "битых входящих": val(r.get("in")), "из них dofollow": val(r.get("in_dofollow")),
                             "битых исходящих": val(r.get("out")), "исходящих dofollow": val(r.get("out_dofollow"))})
        for s in r.get("inbound") or []:
            sh["битые входящие"].append({"домен": d, "откуда": s.get("from"), "title донора": clean(s.get("title")), "DR донора": val(s.get("rating")),
                                         "трафик донора": val(s.get("traffic")), "куда (битая)": s.get("to"), "анкор": clean(s.get("anchor")),
                                         "текст до": clean(s.get("textPre")), "текст после": clean(s.get("textPost")),
                                         "dofollow": val(s.get("follow")), "код ответа": val(s.get("code")), "редирект": val(s.get("redirect_code"))})
        for s in r.get("outbound") or []:
            sh["битые исходящие"].append({"домен": d, "страница": s.get("from"), "title": clean(s.get("title")), "куда (битая)": s.get("to"),
                                          "анкор": clean(s.get("anchor")), "dofollow": val(s.get("follow")), "код ответа": val(s.get("code"))})
    report_failed(rows)
    save("broken", label_of(a, doms, "domains"), sh, rows)


def cmd_moz(a):
    doms = [domain_of(d) for d in read_items(a)]
    rows = run("Rank::MOZ", doms, {}, threads=a.threads)
    sh = {"сводка": [], "ссылающиеся домены": [], "страницы": [], "ключи": [], "ключи по кликам": [], "брендовые ключи": [],
          "featured snippets": [], "позиции ключей": [], "конкуренты": [], "вопросы": [], "динамика доменов": []}
    for d, r in zip(doms, rows):
        sh["сводка"].append({"домен": d, "получено": ok(r), "DA": num_short(r.get("authority")), "Spam Score, %": num_short(r.get("spam")),
                             "ссылающихся доменов": num_short(r.get("linking")), "ключей в ТОПе": num_short(r.get("keywords"))})
        for s in r.get("topLinkingDomains") or []:
            sh["ссылающиеся домены"].append({"домен": d, "донор": s.get("domain"), "DA донора": val(s.get("da"))})
        for s in r.get("topPagesByLinks") or []:
            sh["страницы"].append({"домен": d, "url": s.get("url"), "PA": val(s.get("pa"))})
        for s in r.get("topRankingKeywords") or []:
            sh["ключи"].append({"домен": d, "фраза": clean(s.get("keyword")), "позиция": val(s.get("rank"))})
        for s in r.get("keywordsByClicks") or []:
            sh["ключи по кликам"].append({"домен": d, "фраза": clean(s.get("keyword")), "видимость": val(s.get("visibility"))})
        for s in r.get("brandedKeywords") or []:
            sh["брендовые ключи"].append({"домен": d, "фраза": clean(s.get("keyword")), "частота": num_short(s.get("volume"))})
        for s in r.get("topFeaturedSnippets") or []:
            sh["featured snippets"].append({"домен": d, "фраза": clean(s.get("keyword")), "сниппет у домена": val(s.get("owned"))})
        for s in r.get("keywordRankingDistribution") or []:
            sh["позиции ключей"].append({"домен": d, "позиции": val(s.get("position")), "ключей": num_short(s.get("keywords"))})
        for s in r.get("topSearchCompetitors") or []:
            sh["конкуренты"].append({"домен": d, "конкурент": s.get("domain"), "DA": val(s.get("da")), "видимость": val(s.get("visibility"))})
        for s in r.get("topQuestions") or []:
            sh["вопросы"].append({"домен": d, "вопрос": clean(s.get("question")), "релевантность": val(s.get("relevance"))})
        for s in r.get("discoveredAndLostLinkingDomains") or []:
            sh["динамика доменов"].append({"домен": d, "дата": s.get("date"), "новых": val(s.get("discovered")), "потеряно": val(s.get("lost"))})
    report_failed(rows)
    save("moz", label_of(a, doms, "domains"), sh, rows)


def read_pairs(a) -> list[str]:
    """Пары «страница-донор ссылка»: через пробел в строке, или две первые колонки xlsx/csv."""
    if a.f and Path(a.f).suffix.lower() in (".xlsx", ".xls", ".csv"):
        df = pd.read_excel(a.f) if a.f.lower().endswith((".xlsx", ".xls")) else pd.read_csv(a.f)
        pairs = [f"{str(x).strip()} {str(y).strip()}" for x, y in df.iloc[:, :2].dropna().itertuples(index=False)]
        return pairs + (read_items(a) if a.q else [])
    return read_items(a)


def cmd_backlink(a):
    pairs = read_pairs(a)
    bad = [p for p in pairs if len(p.split()) != 2]
    if bad:
        raise SystemExit(f"Нужна пара «страница-донор адрес-ссылки» через пробел: {bad[:3]}")
    rows = run("Check::BackLink", pairs, {"headless": 1}, threads=a.threads)
    out = []
    for p, r in zip(pairs, rows):
        donor, target = p.split()
        out.append({"страница-донор": donor, "ссылка": target, "получено": ok(r), "ссылка найдена": val(r.get("exists")),
                    "анкор": clean(r.get("anchor")), "nofollow": val(r.get("nofollow")), "noindex": val(r.get("noindex")),
                    "meta robots": val(r.get("robots")), "код ответа донора": val(r.get("code")), "редирект донора": val(r.get("redirect")),
                    "фактический адрес донора": val(r.get("actualbacklink")), "найденная ссылка": val(r.get("actualchecklink")),
                    "внешних ссылок на странице": val(r.get("extcount")), "внутренних ссылок": val(r.get("intcount"))})
    report_failed(rows)
    save("backlink", a.label or f"{len(pairs)}", {"ссылки": out}, rows)


def cmd_ip(a):
    items = read_items(a)
    doms = [domain_of(x) for x in items if not is_ip(x)]
    ip_of = {x: x for x in items if is_ip(x)}
    if doms:
        for d, r in zip(doms, run("Net::DNS", doms, {}, threads=a.threads)):
            ip_of[d] = next(iter(x.get("ip") for x in (r.get("ips") or []) if x.get("ip")), None) or val(r.get("ip"))
    ips = sorted({ip for ip in ip_of.values() if ip})
    info = {r["query"]: r for r in run("IP::Info", ips, {}, threads=a.threads)} if ips else {}
    geo = {r["query"]: r for r in run("IP::Geo", ips, {}, threads=a.threads)} if ips else {}
    out = []
    for x, ip in ip_of.items():
        i, g = info.get(ip) or {}, geo.get(ip) or {}
        out.append({"запрос": x, "IP": ip, "компания": val(i.get("companyName")) or val(g.get("org")), "домен компании": val(i.get("companyDomain")),
                    "тип": val(i.get("companyType")) or val(i.get("type")), "ASN": val(i.get("asn")) or val(g.get("as")),
                    "сеть": val(i.get("network")) or val(i.get("route")), "провайдер": val(g.get("isp")),
                    "страна": val(g.get("country")) or val(i.get("country")), "регион": val(g.get("regionName")), "город": val(g.get("city")),
                    "VPN": val(i.get("vpn")), "прокси": val(i.get("proxy")), "Tor": val(i.get("tor")), "хостинг": val(i.get("hosting")),
                    "абузы: email": val(i.get("email"))})
    save("ip", label_of(a, items, "ip"), {"IP": out}, {"info": list(info.values()), "geo": list(geo.values())})


def cmd_trails(a):
    doms = [domain_of(d) for d in read_items(a)]
    rows = run("SecurityTrails::Domain", doms, {}, threads=a.threads)
    sh = {"сводка": [], "записи": [], "поддомены": []}
    rec = (("A", "aRecords", "ip"), ("AAAA", "aaaaRecords", "ip"), ("NS", "nsRecords", "ns"), ("MX", "mxRecords", "host"),
           ("TXT", "txt", "record"), ("SOA", "soaRecords", "email"))
    for d, r in zip(doms, rows):
        sh["сводка"].append({"домен": d, "получено": ok(r), "поддоменов": val(r.get("subdomain_count")), "Alexa": val(r.get("alexa")),
                             "A": joined(r.get("aRecords"), "ip"), "NS": joined(r.get("nsRecords"), "ns"), "MX": joined(r.get("mxRecords"), "host"),
                             "доменов с теми же NS": len(r.get("nsRecordsPointed") or []), "доменов с теми же MX": len(r.get("mxRecordsPointed") or [])})
        for kind, arr, key in rec:
            for s in r.get(arr) or []:
                sh["записи"].append({"домен": d, "тип": kind, "значение": clean(s.get(key)), "статистика": val(s.get("stats"))})
        for s in r.get("subdomains") or []:
            sh["поддомены"].append({"домен": d, "поддомен": s.get("domain"), "хостинг": val(s.get("hosting")), "почта": val(s.get("mail"))})
    report_failed(rows)
    save("trails", label_of(a, doms, "domains"), sh, rows)


def register(sub):
    p = sub.add_parser("domains", help="сводная проверка доменов: Whois, архив, РКН, ИКС, DNS, Ahrefs, MOZ, Majestic, Mustat, чёрные списки …")
    add_io(p, "домены")
    p.add_argument("--checks", default="default",
                   help=f"проверки через запятую: {', '.join(list(CHECKS) + list(SPECIAL))}; safe = обе SafeBrowsing; "
                        f"наборы: default ({','.join(PRESETS['default'])}), pbn, all")
    p.set_defaults(func=cmd_domains)

    p = sub.add_parser("ahrefs", help="бесплатный чекер Ahrefs: DR, ASpamRank, беклинки, 20 доноров с анкорами")
    add_io(p, "домены или адреса")
    p.add_argument("--exact", action="store_true", help="по точному адресу страницы, а не по домену")
    p.set_defaults(func=cmd_ahrefs)

    p = sub.add_parser("traffic", help="трафик по Ahrefs: оценка, стоимость, топ-ключи, страницы, страны, история")
    add_io(p, "домены"); p.set_defaults(func=cmd_traffic)

    p = sub.add_parser("broken", help="битые ссылки по Ahrefs: входящие (на ваш сайт) и исходящие")
    add_io(p, "домены"); p.set_defaults(func=cmd_broken)

    p = sub.add_parser("moz", help="MOZ подробно: DA, Spam Score, доноры, страницы, ключи, конкуренты")
    add_io(p, "домены"); p.set_defaults(func=cmd_moz)

    p = sub.add_parser("backlink", help="стоит ли ссылка на странице-доноре: анкор, nofollow, noindex, robots")
    add_io(p, "пары «страница-донор адрес-ссылки» через пробел")
    p.set_defaults(func=cmd_backlink)

    p = sub.add_parser("ip", help="IP и хостинг: компания, ASN, страна, город, VPN/прокси/Tor (по IP или домену)")
    add_io(p, "IP или домены"); p.set_defaults(func=cmd_ip)

    p = sub.add_parser("trails", help="SecurityTrails без логина: DNS-записи, поддомены, соседи по NS/MX")
    add_io(p, "домены"); p.set_defaults(func=cmd_trails)
