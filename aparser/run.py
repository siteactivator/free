"""aparser — все рабочие парсеры A-Parser 1.2.3656 командами из терминала, результат — xlsx.

    python run.py <команда> -q "а;б;в" | -f файл [параметры]
    python run.py <команда> -h          # параметры команды
    python run.py parsers               # 137 парсеров: что работает и чем вызывается
    python run.py status                # A-Parser жив? прокси? капча?

Команды:
  поиск          serp, positions, suggest, index, images, video, byimage, wiki
  ключевые слова keywords, domain-keywords, kd, trends, ads
  домены, ссылки domains, ahrefs, traffic, broken, moz, backlink, ip, trails
  страницы       http, extract, screenshot, translate, speller, proofread, ai
  прочее         apps, shop, maps, youtube, reddit, tiktok, telegram, crypto, raw
  заготовки      stub — 37 непроверенных парсеров: python run.py stub
  проверка       check — прогнать все методы → кнопки методов в панели задач

Выход: data\\<команда>\\<дата_время>_<метка>.xlsx в папке скилла + .json с сырьём.
Панель задач: python panel.py → http://127.0.0.1:8770/
"""

from __future__ import annotations

import argparse
import json
import sys
from collections import Counter

import aparser as ap
import progress
import cmd_check
import cmd_content
import cmd_domains
import cmd_keywords
import cmd_more
import cmd_search
import cmd_stubs
from common import add_io, label_of, read_items, report_failed, run, save


def cmd_status(a):
    import server
    return server.status()


def cmd_parsers(a):
    from catalog import PARSERS, STATUS
    have = set(ap.available_parsers()) if ap.ping() else set(PARSERS)
    rows = sorted(PARSERS.items(), key=lambda kv: (list(STATUS).index(kv[1][0]), kv[0]))
    for code in STATUS:
        items = [(p, cmd, note) for p, (st, cmd, note) in rows if st == code and (not a.status or a.status == code)]
        if not items:
            continue
        print(f"\n{STATUS[code]} — {len(items)}")
        for p, cmd, note in items:
            mark = "" if p in have else "  [нет в этой версии A-Parser]"
            print(f"  {p:<42} {cmd:<38} {note}{mark}")
    print("\nИтого:", ", ".join(f"{STATUS[k]} {v}" for k, v in Counter(s for s, _, _ in PARSERS.values()).items()))
    new = sorted(have - set(PARSERS))
    if new:
        print("Не проверялись (появились позже):", ", ".join(new))


def cmd_raw(a):
    q = read_items(a)
    ov = {}
    for kv in a.set or []:
        k, v = kv.split("=", 1)
        ov[k] = int(v) if v.lstrip("-").isdigit() else v
    # ключ OpenAI — из .env, чтобы не писать его в командной строке
    if a.parser.startswith("OpenAI::") and "api_key" not in ov and ap.ENV.get("OPENAI_API_KEY"):
        ov["api_key"] = ap.ENV["OPENAI_API_KEY"]
    rows = run(a.parser, q, ov, threads=a.threads)
    arrays = set(ap.schema(a.parser)["arrays"])
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
    save("raw", a.label or a.parser.replace("::", "-"), {"результаты": flat, **sheets}, rows)


def main() -> int:
    p = argparse.ArgumentParser(description="aparser: рабочие парсеры A-Parser командами, результат — xlsx",
                                formatter_class=argparse.RawDescriptionHelpFormatter, epilog=__doc__.split("Команды:")[1])
    sub = p.add_subparsers(dest="cmd", required=True, metavar="<команда>")
    s = sub.add_parser("status", help="A-Parser: жив ли, версия, живые прокси, баланс CapMonster")
    s.set_defaults(func=cmd_status, offline=True)
    s = sub.add_parser("parsers", help="все парсеры: что работает, чем вызывается, почему нет")
    s.add_argument("--status", choices=["ok", "part", "bad", "key", "skip", "util"], help="только с этим итогом")
    s.set_defaults(func=cmd_parsers, offline=True)
    for m in (cmd_search, cmd_keywords, cmd_domains, cmd_content, cmd_more, cmd_stubs, cmd_check):
        m.register(sub)
    s = sub.add_parser("raw", help="любой парсер A-Parser с опциями --set id=значение")
    add_io(s)
    s.add_argument("--parser", required=True, help="имя парсера, например Rank::MOZ")
    s.add_argument("--set", action="append", help="опция=значение (можно несколько)")
    s.set_defaults(func=cmd_raw)

    a = p.parse_args()
    if getattr(a, "offline", False) or (a.cmd == "stub" and not a.name):
        return a.func(a) or 0
    # запуск виден в панели задач (python panel.py)
    import common
    if getattr(a, "retry", None) is not None:
        common.RETRY["n"] = a.retry
    elif a.cmd == "stub":
        common.RETRY["n"] = 0                  # заготовки и так часто падают — повтор только удвоит время
    common.RETRY["pause"] = getattr(a, "pause", 30) or 0
    run_ = progress.start("aparser", a.cmd, sys.argv[1:])
    try:
        ap.call("ping", timeout=10)
        code = a.func(a) or 0
    except ap.APError as e:
        run_.finish("error", str(e))
        sys.exit(str(e))
    except SystemExit as e:
        run_.finish("error" if e.code not in (0, None) else "done", str(e.code) if e.code not in (0, None) else None)
        raise
    except KeyboardInterrupt:
        run_.finish("error", "прервано (Ctrl+C)")
        raise
    except Exception as e:
        run_.finish("error", f"{type(e).__name__}: {e}")
        raise
    run_.finish("done")
    return code


if __name__ == "__main__":
    sys.exit(main())
