"""Доноры-линкфермы в выборке бесплатного Ahrefs (20 доноров, по одному с домена) и ASpamRank.

ASpamRank — доля доноров-ферм в процентах: 10 из 20 → 50. Донор — ферма, если
сработал хотя бы один признак:

- шаблон title: у 3+ доноров сайта одинаковое начало или конец заголовка (4 слова
  после чистки от имени донора, чисел и годов) — «… (2017) Смотреть Онлайн Фильм В
  Хорошем Качестве», «Before I Discovered <сервис>.org …»;
- эмодзи в заголовке (🚀🔥🏆★);
- карточка домена: автостраницы на каждый домен зоны — «Domain Report», «Website Stats»,
  «Top Domains – Page 99159», «URL Shared»;
- title начинается с имени самого донора: «donor.shop — How Niche Edits …»;
- анкор-фраза с доменом: анкор из 5+ слов, внутри которого домен проверяемого сайта —
  «Scale site.ru with guest posts, backlinks …»;
- дешёвая зона (.shop, .store, .xyz …) и SEO-слово в имени донора или в title —
  linkrank.shop, seogrowthprovider.shop;
- SEO-продажа по-английски: SEO-слова в title без кириллицы — **только если сам сайт не
  про SEO**. У SEO-сайтов нормальные доноры тоже пишут про продвижение и ссылки
  (проверено 26.09.2026 на трёх крупных русскоязычных SEO-порталах).

Проверено 26.09.2026: три дропа с фермами — 100%, живой коммерческий сайт — 0%.
Возможный ложный случай — каталоги компаний с одинаковыми заголовками
(«ООО … — адрес, телефон, отзывы»).
"""

from __future__ import annotations

import re
from collections import Counter

SEO = re.compile(r"backlink|link building|guest post|niche edit|seoexpress|\bseo\b|\bpbn\b|dofollow|"
                 r"\bserp\b|rankings?|organic (?:traffic|visibility)|aged domain|e-commerce site|link velocity|"
                 r"white hat", re.I)
# DA/DR/TF — только заглавными и рядом с числом или друг с другом («DA 50», «DA and DR»):
# иначе ловятся «Dr.» (доктор) и итальянское/немецкое «da»
SEO_METRIC = re.compile(r"\b(?:DA|DR|PA|TF|CF)\s?\d{1,3}\b|\b(?:DA|DR)\s?(?:,|and|&|/)\s?(?:DA|DR|TF)\b|"
                        r"\b[Hh]igh[- ](?:DA|DR)\b")
# Автоматические карточки доменов: одна страница на каждый домен из зоны
DOMAIN_CARD = re.compile(r"domain report|website stats|web ?stats|top domains|url shared|site ?info\b|"
                         r"website worth|site worth|similar sites|seo report|traffic estimate|domain stats|"
                         r"whois (?:lookup|info)", re.I)
SEO_NAME = re.compile(r"seo|rank|link|backlink|serp|authority|traffic|index", re.I)
SEO_SITE = re.compile(r"seo|serp|rank|backlink|linkbuild|prodvizh|raskrut|promo|optimiz|sape|seonews|searchengine",
                      re.I)
CHEAP_TLD = (".shop", ".store", ".xyz", ".art", ".top", ".online", ".site", ".click", ".buzz", ".icu", ".cfd")
EMOJI = re.compile(r"[\U0001F300-\U0001FAFF☀-➿]")
CYR = re.compile(r"[а-яё]", re.I)


def is_seo_site(site: str, titles: list[str] = ()) -> bool:
    """Сайт про SEO: по имени домена или по его собственным title (с главной или из архива)."""
    return bool(SEO_SITE.search(site or "")) or any(SEO.search(t or "") or SEO_METRIC.search(t or "") or
                                                    re.search(r"продвижени|seo|раскрутк|оптимизаци", t or "", re.I)
                                                    for t in titles)


def _words(title: str, donor: str) -> list[str]:
    t = str(title or "").lower().replace(str(donor or "").lower(), " ")
    t = EMOJI.sub(" ", t)
    t = re.sub(r"\(\d{4}\)|\b\d+\b", " ", t)
    return re.sub(r"[^\w\s]", " ", t).split()


def classify(donors: list[dict], site: str = "", seo_site: bool | None = None) -> list[str]:
    """Для доноров одного сайта (ключи «донор», «title донора», «анкор») — причина «фермы» или ''."""
    if seo_site is None:
        seo_site = is_seo_site(site)
    words = [_words(d.get("title донора"), d.get("донор")) for d in donors]
    heads = Counter(" ".join(w[:4]) for w in words if len(w) >= 6)
    tails = Counter(" ".join(w[-4:]) for w in words if len(w) >= 6)
    out = []
    for d, w in zip(donors, words):
        title = str(d.get("title донора") or "")
        donor = str(d.get("донор") or "").lower()
        anchor = str(d.get("анкор") or "")
        seo_title = bool(SEO.search(title) or SEO_METRIC.search(title))
        why = []
        if DOMAIN_CARD.search(title):
            why.append("карточка домена")
        if len(w) >= 6 and (heads[" ".join(w[:4])] >= 3 or tails[" ".join(w[-4:])] >= 3):
            why.append("шаблон title")
        if EMOJI.search(title):
            why.append("эмодзи")
        if donor and title.lower().startswith(donor):
            why.append("title с имени донора")
        if site and site.lower() in anchor.lower() and len(anchor.split()) >= 5:
            why.append("анкор-фраза с доменом")
        if donor.endswith(CHEAP_TLD) and (SEO_NAME.search(donor) or seo_title):
            why.append("дешёвая зона + SEO")
        if seo_title and not CYR.search(title) and not seo_site:
            why.append("SEO-продажа по-английски")
        out.append(", ".join(why))
    return out


def aspamrank(reasons: list[str]) -> int | None:
    """Процент доноров-ферм; None, если доноров нет."""
    return round(100 * sum(1 for r in reasons if r) / len(reasons)) if reasons else None
