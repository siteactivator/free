"""Общее для команд: ввод запросов, запуск парсера, сохранение xlsx + json, чистка текста."""

from __future__ import annotations

import json
import re
import sys
import time
from datetime import datetime
from pathlib import Path
from urllib.parse import urlsplit

import pandas as pd

import aparser as ap
import progress as pg

XLSX_CELL = 32000   # в ячейке Excel не больше 32 767 символов — длинное режем, целиком оно в .json


# ---------- ввод ----------

def add_io(p, what: str = "запросы") -> None:
    """Общие параметры входа и выхода для подкоманды."""
    p.add_argument("-q", help=f"{what} через точку с запятой")
    p.add_argument("-f", help="файл: .txt (по строке) или .xlsx/.csv (первая колонка)")
    p.add_argument("--threads", type=int, default=5, help="сколько запросов A-Parser выполняет параллельно (5)")
    p.add_argument("--label", help="метка в имени файла результата")
    p.add_argument("--retry", type=int, help="повторов для неполученных запросов (по умолчанию 1)")
    p.add_argument("--pause", type=int, default=30, help="пауза перед повтором, секунд (30)")


def read_items(a, dedupe: bool = True) -> list[str]:
    items = []
    if getattr(a, "q", None):
        items += [x.strip() for x in re.split(r"[;\n]", a.q) if x.strip()]
    if getattr(a, "f", None):
        p = Path(a.f)
        if p.suffix.lower() in (".xlsx", ".xls"):
            items += pd.read_excel(p).iloc[:, 0].dropna().astype(str).str.strip().tolist()
        elif p.suffix.lower() == ".csv":
            items += pd.read_csv(p).iloc[:, 0].dropna().astype(str).str.strip().tolist()
        else:
            items += [x.strip() for x in p.read_text(encoding="utf-8-sig").splitlines() if x.strip()]
    if dedupe:
        items = list(dict.fromkeys(items))
    if not items:
        sys.exit("Нет входных данных: -q «а;б» или -f файл")
    return items


# ---------- запуск ----------

RETRY = {"n": 1, "pause": 30}      # повтор неполученных с паузой: run.py ставит из --retry/--pause


def run(parser: str, queries: list[str], overrides: dict | None = None, threads: int = 5,
        keep: tuple = (), progress=print, retry: int | None = None) -> list[dict]:
    """Пачка запросов с пресетами капчи, где они нужны. Строки — в порядке запросов.

    A-Parser сам делает до 10 попыток на запрос, но подряд, без пауз — короткий сбой (капча, лимит
    сервиса) их съедает. Поэтому неполученные запросы переспрашиваем ещё раз после паузы."""
    ov = {**ap.captcha(parser), **(overrides or {})}
    run_ = pg.CURRENT
    idx = run_.stage(parser, len(queries)) if run_ else None
    on_batch = (lambda done, good: run_.advance(idx, done, good)) if run_ else None
    rows = ap.bulk(parser, queries, ov, threads=threads, keep=keep, progress=progress, on_batch=on_batch)
    tries = RETRY["n"] if retry is None else retry
    for attempt in range(1, tries + 1):
        bad = [i for i, r in enumerate(rows) if not ok(r)]
        if not bad:
            break
        if progress:
            progress(f"  {parser}: не получено {len(bad)} — повтор {attempt}/{tries} через {RETRY['pause']} с")
        time.sleep(RETRY["pause"])
        again = ap.bulk(parser, [queries[i] for i in bad], ov, threads=threads, keep=keep, progress=None)
        for i, r in zip(bad, again):
            if ok(r):
                rows[i] = r
        if run_:
            run_.advance(idx, len(rows), sum(1 for r in rows if ok(r)))
    return rows


def ok(r: dict) -> bool:
    return r.get("success") == 1


def report_failed(rows: list[dict]) -> None:
    bad = [r.get("query") for r in rows if not ok(r)]
    if bad:
        print(f"  НЕ получено ({len(bad)}): {bad[:10]}{' …' if len(bad) > 10 else ''} — перезапустите их отдельно")


# ---------- вывод ----------

def _cell(v):
    if isinstance(v, str):
        v = CONTROL.sub("", re.sub(r"\x07[\[\]]", "", v))
        if len(v) > XLSX_CELL:
            return v[:XLSX_CELL] + " …[обрезано, целиком — в .json]"
    return v


def save(cmd: str, label: str, sheets: dict[str, list[dict] | pd.DataFrame], raw=None) -> Path:
    """Листы xlsx (пустые, кроме первого, пропускаются) + сырой ответ в .json рядом."""
    out_dir = ap.DATA / cmd
    out_dir.mkdir(parents=True, exist_ok=True)
    safe = re.sub(r"[^\w.-]+", "_", label or cmd)[:40]
    # не with_suffix: у меток-доменов («site.ru») он принял бы «.ru» за расширение
    base = f"{datetime.now():%Y-%m-%d_%H%M}_{safe}"
    xlsx = out_dir / f"{base}.xlsx"
    items = list(sheets.items())
    with pd.ExcelWriter(xlsx, engine="xlsxwriter", engine_kwargs={"options": {"strings_to_urls": False}}) as w:
        for i, (name, data) in enumerate(items):
            df = data if isinstance(data, pd.DataFrame) else pd.DataFrame(data)
            if df.empty and i > 0:
                continue
            df = df.map(_cell) if hasattr(df, "map") else df.applymap(_cell)
            df.to_excel(w, sheet_name=name[:31], index=False)
            ws = w.sheets[name[:31]]
            ws.freeze_panes(1, 0)
            for j, col in enumerate(df.columns):
                width = max([len(str(col))] + [len(str(x)) for x in df[col].head(200).tolist()])
                ws.set_column(j, j, min(max(width + 2, 8), 60))
    if raw is not None:
        (out_dir / f"{base}.json").write_text(json.dumps(raw, ensure_ascii=False, indent=1, default=str), encoding="utf-8")
    if pg.CURRENT:
        pg.CURRENT.output(xlsx)
    print(f"→ {xlsx}")
    return xlsx


def label_of(a, items: list[str], default: str) -> str:
    """Метка файла: --label, иначе сам запрос (для адреса — его домен), иначе «что_сколько»."""
    if a.label:
        return a.label
    if len(items) == 1:
        return domain_of(items[0]) if items[0].startswith("http") else items[0]
    return f"{default}_{len(items)}"


# ---------- текст и значения ----------

CONTROL = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f]")


def clean(html) -> str:
    """HTML → текст в одну строку. Метки подсветки Яндекса (\\x07[ … \\x07]) и управляющие символы убираются;
    «none»/«null» (так A-Parser пишет пустое поле) → пустая строка."""
    if html is None or str(html).strip().lower() in ("none", "null", "undefined"):
        return ""
    s = CONTROL.sub("", re.sub(r"\x07[\[\]]", "", str(html)))
    s = re.sub(r"<[^>]+>", " ", s)
    s = s.replace("&amp;", "&").replace("&quot;", '"').replace("&#39;", "'").replace("&lt;", "<").replace("&gt;", ">").replace("&nbsp;", " ")
    return re.sub(r"\s+", " ", s).strip()


def domain_of(url: str) -> str:
    host = urlsplit(url if "://" in url else "http://" + url).hostname or ""
    return host.lower().removeprefix("www.")


def val(v):
    """«none», пустые строки и None → None; числа в строках — в числа (для сортировки в Excel)."""
    if v is None or (isinstance(v, str) and v.strip().lower() in ("", "none", "null", "undefined")):
        return None
    if isinstance(v, str) and re.fullmatch(r"-?\d+", v.strip()):
        return int(v)
    if isinstance(v, str) and re.fullmatch(r"-?\d+\.\d+", v.strip()):
        return float(v)
    return v


def num_short(v):
    """«248.4k», «28.5m», «1%» (так отдаёт MOZ) → число."""
    v = val(v)
    if not isinstance(v, str):
        return v
    m = re.fullmatch(r"\s*(-?[\d.,]+)\s*([kmb%]?)\s*", v.lower())
    if not m:
        return v
    x = float(m.group(1).replace(",", ""))
    x *= {"k": 1e3, "m": 1e6, "b": 1e9}.get(m.group(2), 1)
    return int(x) if x == int(x) else round(x, 2)


def joined(items, key: str | None = None, sep: str = ", ") -> str:
    """Список значений или словарей → строка через запятую."""
    out = []
    for x in items or []:
        v = x.get(key) if (key and isinstance(x, dict)) else x
        if v not in (None, "", "none"):
            out.append(clean(v))
    return sep.join(out)


def duration(v):
    """Секунды (число) → «Ч:ММ:СС» / «М:СС»; строки вроде «12:34» — как есть."""
    v = val(v)
    if not isinstance(v, (int, float)):
        return v
    h, rest = divmod(int(v), 3600)
    m, s = divmod(rest, 60)
    return f"{h}:{m:02d}:{s:02d}" if h else f"{m}:{s:02d}"


def date_of(v):
    """Unix-время в секундах или миллисекундах → «ГГГГ-ММ-ДД»; остальное — как есть."""
    v = val(v)
    if isinstance(v, (int, float)) and v > 10 ** 8:
        from datetime import timezone
        return datetime.fromtimestamp(v / 1000 if v > 10 ** 11 else v, tz=timezone.utc).strftime("%Y-%m-%d")
    return v


def is_ip(s: str) -> bool:
    return bool(re.fullmatch(r"\d{1,3}(\.\d{1,3}){3}", s.strip()))
