"""Текст страницы и пометки на нём: второй шаг разбора видео-вычитки.

Замечания из видео ложатся не на кадры, а на сам текст статьи — видно, какой
фрагмент убрать, что переписать, куда дописать и где нужна информация от
владельца.

    python page.py fetch <url> --session data/<проект>/<дата>_<ЧЧММ>
    python page.py render --session data/<проект>/<дата>_<ЧЧММ> [--slug <имя>]

fetch  — скачивает страницу и сохраняет текст статьи без меню и подвала:
         pages/<slug>.json (блоки для render) и pages/<slug>.md (для агента:
         из него берутся точные цитаты).
render — берёт пометки агента marks/<slug>.json и собирает
         annotated_<slug>.html: весь текст, фрагменты подсвечены по действию,
         комментарии рядом, вверху — что нужно от владельца.

Что пометить, решает агент (формат marks — в SKILL.md), а скачивание, поиск
цитат в тексте и вёрстка — здесь. Цитата, которой нет в тексте, не
пропадает молча: render перечисляет её в конце файла и в консоли.
"""
import argparse
import difflib
import html
import json
import re
import sys
from datetime import date
from html.parser import HTMLParser
from pathlib import Path
from urllib.parse import urlparse

try:
    import requests
except ImportError:
    sys.exit('Нет библиотеки requests. Выполните в этой папке:\n'
             '  python -m pip install -r requirements.txt')

# Полная строка Chrome: сайты за DDoS-Guard отдают 403 на урезанную
UA = ('Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 '
      '(KHTML, like Gecko) Chrome/128.0.0.0 Safari/537.36')
SKIP = {'script', 'style', 'noscript', 'svg', 'template', 'iframe', 'form', 'button',
        'select', 'nav', 'header', 'footer', 'head'}
VOID = {'area', 'base', 'br', 'col', 'embed', 'hr', 'img', 'input', 'link', 'meta',
        'source', 'track', 'wbr'}
BLOCKS = {'h1', 'h2', 'h3', 'h4', 'h5', 'h6', 'p', 'li', 'td', 'th', 'blockquote',
          'figcaption', 'dt', 'dd'}
CONTAINER_SHARE = 0.85   # статья — самый узкий узел, где лежит 85 % текста абзацев
FUZZY_SHARE = 0.85       # цитата засчитывается, если совпало 85 % её длины

ACTIONS = {
    'убрать':     ('remove',  'Убрать'),
    'переписать': ('rewrite', 'Переписать'),
    'добавить':   ('add',     'Добавить'),
    'запросить':  ('ask',     'Нужна информация'),
    'уточнить':   ('unclear', 'Уточнить'),
    'оставить':   ('keep',    'Оставить'),
}


# ─── 1. разбор HTML в дерево ────────────────────────────────────────────────
class Node:
    __slots__ = ('tag', 'attrs', 'children', 'parent')

    def __init__(self, tag, attrs=None, parent=None):
        self.tag, self.attrs, self.children, self.parent = tag, dict(attrs or []), [], parent


class TreeBuilder(HTMLParser):
    """Терпимое к кривой разметке дерево: лишние закрывающие теги игнорируются."""

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.root = Node('#root')
        self.cur = self.root

    def handle_starttag(self, tag, attrs):
        if tag == 'p' and self.cur.tag == 'p':            # <p> внутри <p> закрывает прежний
            self.cur = self.cur.parent
        node = Node(tag, attrs, self.cur)
        self.cur.children.append(node)
        if tag not in VOID:
            self.cur = node

    def handle_startendtag(self, tag, attrs):
        self.cur.children.append(Node(tag, attrs, self.cur))

    def handle_endtag(self, tag):
        n = self.cur
        while n is not None and n.tag != tag:
            n = n.parent
        if n is not None and n.parent is not None:
            self.cur = n.parent

    def handle_data(self, data):
        self.cur.children.append(data)


def text_of(node):
    if isinstance(node, str):
        return node
    if node.tag in SKIP:
        return ''
    if node.tag == 'br':
        return ' '
    return ''.join(text_of(c) for c in node.children)


def clean(s):
    return re.sub(r'\s+', ' ', s.replace('\xa0', ' ')).strip()


def iter_nodes(node):
    for c in node.children:
        if isinstance(c, Node) and c.tag not in SKIP:
            yield c
            yield from iter_nodes(c)


def p_text_len(node, memo):
    """Сколько текста абзацев внутри узла — по этому ищется статья."""
    if id(node) in memo:
        return memo[id(node)]
    total = 0
    for c in node.children:
        if isinstance(c, Node) and c.tag not in SKIP:
            total += len(clean(text_of(c))) if c.tag == 'p' else p_text_len(c, memo)
    memo[id(node)] = total
    return total


def find_article(root):
    """Самый узкий узел, где лежит CONTAINER_SHARE всего текста абзацев."""
    memo = {}
    whole = p_text_len(root, memo)
    best = root
    while True:
        nxt = next((c for c in best.children if isinstance(c, Node) and c.tag not in SKIP
                    and p_text_len(c, memo) >= CONTAINER_SHARE * whole), None)
        if nxt is None:
            return best
        best = nxt


def extract_blocks(root):
    """Блоки статьи по порядку: заголовки, абзацы, пункты списков, ячейки таблиц."""
    art = find_article(root)
    blocks = []
    h1_all = [clean(text_of(n)) for n in iter_nodes(root) if n.tag == 'h1']
    h1_in = any(n.tag == 'h1' for n in iter_nodes(art))
    if h1_all and not h1_in:
        blocks.append({'type': 'h1', 'text': h1_all[0]})
    table_no = 0

    def walk(node, list_depth=0, table=None):
        nonlocal table_no
        for c in node.children:
            if not isinstance(c, Node) or c.tag in SKIP:
                continue
            if c.tag == 'table':
                table_no += 1
                walk(c, list_depth, {'id': table_no, 'row': -1})
                continue
            if c.tag == 'tr' and table is not None:
                table['row'] += 1
                table['col'] = -1
                walk(c, list_depth, table)
                continue
            if c.tag in ('ul', 'ol'):
                walk(c, list_depth + 1, table)
                continue
            if c.tag in BLOCKS:
                txt = clean(text_of(c))
                if not txt:
                    continue
                b = {'type': c.tag, 'text': txt}
                if c.tag in ('td', 'th') and table is not None:
                    table['col'] += 1
                    b.update(table=table['id'], row=table['row'], col=table['col'])
                if c.tag == 'li':
                    b['depth'] = max(list_depth, 1)
                    # вложенный список внутри пункта — отдельными пунктами
                    nested = [g for g in c.children if isinstance(g, Node) and g.tag in ('ul', 'ol')]
                    if nested:
                        own = clean(''.join(text_of(g) for g in c.children
                                            if not (isinstance(g, Node) and g.tag in ('ul', 'ol'))))
                        b['text'] = own
                        if own:
                            blocks.append(b)
                        for g in nested:
                            walk(g, list_depth + 1, table)
                        continue
                blocks.append(b)
                continue
            walk(c, list_depth, table)

    walk(art)
    for i, b in enumerate(blocks):
        b['n'] = i
    return blocks


def slug_of(url):
    path = urlparse(url).path.strip('/')
    return (path.split('/')[-1] if path else urlparse(url).netloc) or 'page'


def to_markdown(blocks, url):
    L = [f'<!-- {url} · выгружено {date.today().isoformat()} · цитаты для пометок брать отсюда -->', '']
    rows = {}
    for b in blocks:
        t = b['type']
        if t in ('td', 'th'):
            rows.setdefault((b['table'], b['row']), []).append(b['text'])
            nxt = blocks[b['n'] + 1] if b['n'] + 1 < len(blocks) else None
            if nxt and nxt.get('table') == b['table'] and nxt.get('row') == b['row']:
                continue
            cells = rows.pop((b['table'], b['row']))
            L.append('| ' + ' | '.join(cells) + ' |')
            if b['row'] == 0:
                L.append('|' + '---|' * len(cells))
            if not (nxt and nxt.get('table') == b['table']):
                L.append('')
            continue
        if t[0] == 'h' and t[1:].isdigit():
            L += ['#' * int(t[1]) + ' ' + b['text'], '']
        elif t == 'li':
            L.append('  ' * (b['depth'] - 1) + '- ' + b['text'])
            nxt = blocks[b['n'] + 1] if b['n'] + 1 < len(blocks) else None
            if not (nxt and nxt['type'] == 'li'):
                L.append('')
        elif t == 'blockquote':
            L += ['> ' + b['text'], '']
        else:
            L += [b['text'], '']
    return '\n'.join(L)


def cmd_fetch(a):
    session = Path(a.session)
    if not session.is_dir():
        sys.exit(f'Нет папки записи: {session}')
    slug = a.slug or slug_of(a.url)
    pdir = session / 'pages'
    pj = pdir / f'{slug}.json'
    if pj.exists() and not a.force:
        print(f'   уже выгружено: {pj} (заново — --force)')
        return
    if a.html:
        # Сайт не отдаёт страницу скрипту — её сохраняют из браузера (Ctrl+S)
        raw = Path(a.html).read_bytes()
        try:
            page_html = raw.decode('utf-8')
        except UnicodeDecodeError:
            page_html = raw.decode('cp1251', errors='replace')
    else:
        r = requests.get(a.url, headers={'User-Agent': UA, 'Accept-Language': 'ru-RU,ru;q=0.9'},
                         timeout=30)
        if r.status_code != 200:
            sys.exit(f'{a.url} ответил {r.status_code}. Если сайт не пускает скрипты — '
                     f'сохраните страницу из браузера и передайте её через --html')
        r.encoding = (r.encoding if r.encoding and r.encoding.lower() != 'iso-8859-1'
                      else r.apparent_encoding)
        page_html = r.text
    tb = TreeBuilder()
    tb.feed(page_html)
    blocks = extract_blocks(tb.root)
    if not blocks:
        sys.exit('Текст статьи не найден — страница собирается скриптами?')
    pdir.mkdir(exist_ok=True)
    (pdir / f'{slug}.html').write_text(page_html, encoding='utf-8')
    pj.write_text(json.dumps({'url': a.url, 'fetched': date.today().isoformat(), 'blocks': blocks},
                             ensure_ascii=False, indent=1), encoding='utf-8')
    (pdir / f'{slug}.md').write_text(to_markdown(blocks, a.url), encoding='utf-8')
    kinds = {}
    for b in blocks:
        kinds[b['type']] = kinds.get(b['type'], 0) + 1
    words = sum(len(b['text'].split()) for b in blocks)
    print(f'✅ {slug}: блоков {len(blocks)}, слов {words} · ' +
          ', '.join(f'{k} {v}' for k, v in sorted(kinds.items())))
    print(f'✅ текст для агента: {pdir / (slug + ".md")}')


# ─── 2. поиск цитат ─────────────────────────────────────────────────────────
TRANS = str.maketrans({'ё': 'е', 'Ё': 'Е', '«': '"', '»': '"', '“': '"', '”': '"', '„': '"',
                       '’': "'", '‘': "'", '—': '-', '–': '-', '‑': '-', '−': '-', '\xa0': ' '})


def normalize(s):
    """Нормализованная строка и карта: позиция в ней → позиция в исходной."""
    out, idx = [], []
    prev_space = True
    for i, ch in enumerate(s.translate(TRANS).lower()):
        if ch.isspace():
            if prev_space:
                continue
            ch, prev_space = ' ', True
        else:
            prev_space = False
        out.append(ch)
        idx.append(i)
    while out and out[-1] == ' ':
        out.pop()
        idx.pop()
    return ''.join(out), idx


def locate(quote, blocks, find_all=False):
    """[(блок, начало, конец)] в исходном тексте блока; [] — не найдено."""
    q, _ = normalize(quote.strip().strip('.…'))
    if not q:
        return []
    hits = []
    for b in blocks:
        t, idx = normalize(b['text'])
        start = t.find(q)
        while start >= 0:
            end = start + len(q) - 1
            hits.append((b['n'], idx[start], idx[end] + 1))
            if not find_all:
                return hits
            start = t.find(q, start + 1)
    if hits:
        return hits
    # Не дословно: самый длинный общий кусок, если он почти вся цитата
    best = None
    for b in blocks:
        t, idx = normalize(b['text'])
        m = difflib.SequenceMatcher(None, q, t, autojunk=False).find_longest_match(0, len(q), 0, len(t))
        if m.size and (best is None or m.size > best[0]):
            best = (m.size, b['n'], idx[m.b], idx[m.b + m.size - 1] + 1)
    if best and best[0] >= FUZZY_SHARE * len(q):
        return [(best[1], best[2], best[3])]
    return []


def section_range(blocks, n):
    """Блоки раздела: от заголовка до следующего заголовка того же уровня или выше."""
    def level(b):
        t = b['type']
        return int(t[1]) if t[0] == 'h' and t[1:].isdigit() else None
    start = n
    while start > 0 and level(blocks[start]) is None:
        start -= 1
    lv = level(blocks[start]) or 7
    end = start + 1
    while end < len(blocks) and not (level(blocks[end]) and level(blocks[end]) <= lv):
        end += 1
    return start, end - 1


# ─── 3. вёрстка ─────────────────────────────────────────────────────────────
CSS = """
:root{--ink:#1d1d1f;--muted:#6e6e73;--line:#e3e3e8;--bg:#fff;
--remove:#fde2e1;--remove-b:#d93b30;--rewrite:#fff1c2;--rewrite-b:#c98a00;
--add:#dff5e3;--add-b:#23863a;--ask:#dde9ff;--ask-b:#2f6fdb;
--unclear:#efe3fb;--unclear-b:#8a4fc7;--keep:#eef7ee;--keep-b:#5a9c5e}
*{box-sizing:border-box}
body{margin:0;background:#f5f5f7;color:var(--ink);font:16px/1.6 -apple-system,"Segoe UI",Roboto,Arial,sans-serif}
.wrap{max-width:860px;margin:0 auto;padding:32px 20px 80px}
header.doc{background:var(--bg);border:1px solid var(--line);border-radius:14px;padding:22px 26px;margin-bottom:22px}
header.doc h1{font-size:22px;line-height:1.3;margin:0 0 6px}
.meta{color:var(--muted);font-size:14px}
.meta a{color:inherit}
.verdict{margin:14px 0 0;font-weight:600}
.legend{display:flex;flex-wrap:wrap;gap:8px;margin-top:14px}
.chip{font-size:13px;padding:2px 10px;border-radius:999px;border:1px solid}
.need{background:var(--bg);border:1px solid var(--ask-b);border-radius:14px;padding:18px 24px;margin-bottom:22px}
.need h2{font-size:17px;margin:0 0 8px}
.need ol{margin:0;padding-left:22px}
.need li{margin:6px 0}
article{background:var(--bg);border:1px solid var(--line);border-radius:14px;padding:26px 34px}
article h1{font-size:28px;line-height:1.25}
article h2{font-size:22px;margin-top:34px}
article h3{font-size:18px;margin-top:26px}
article table{border-collapse:collapse;width:100%;margin:14px 0;font-size:15px}
article td,article th{border:1px solid var(--line);padding:8px 10px;vertical-align:top;text-align:left}
article th{background:#fafafa}
mark{padding:1px 2px;border-radius:3px;color:inherit}
mark.remove{background:var(--remove);text-decoration:line-through;text-decoration-color:var(--remove-b)}
mark.rewrite{background:var(--rewrite)}
mark.add{background:var(--add)}
mark.ask{background:var(--ask)}
mark.unclear{background:var(--unclear)}
mark.keep{background:var(--keep)}
sup.n{font-size:11px;font-weight:700;margin-left:2px}
.blk{border-left:4px solid transparent;padding-left:10px;margin-left:-14px}
.blk.remove{border-color:var(--remove-b);background:var(--remove)}
.blk.rewrite{border-color:var(--rewrite-b);background:var(--rewrite)}
.blk.ask{border-color:var(--ask-b);background:var(--ask)}
.blk.unclear{border-color:var(--unclear-b);background:var(--unclear)}
.blk.keep{border-color:var(--keep-b)}
.blk.remove>.t{text-decoration:line-through;text-decoration-color:var(--remove-b)}
.note{font-size:14px;line-height:1.45;border-radius:10px;padding:10px 14px;margin:8px 0 16px;border:1px solid}
.note b.k{display:inline-block;min-width:22px}
.note .src{color:var(--muted);font-size:12.5px;margin-left:6px}
.note.remove{border-color:var(--remove-b);background:#fff7f6}
.note.rewrite{border-color:var(--rewrite-b);background:#fffbef}
.note.add{border-color:var(--add-b);background:#f3fcf5;border-style:dashed}
.note.ask{border-color:var(--ask-b);background:#f5f8ff}
.note.unclear{border-color:var(--unclear-b);background:#faf5ff}
.note.keep{border-color:var(--keep-b);background:#f7fcf7}
.lost{background:var(--bg);border:1px solid var(--remove-b);border-radius:14px;padding:18px 24px;margin-top:22px}
@media print{body{background:#fff}.wrap{max-width:none;padding:0}article,header.doc,.need{border:none}}
"""


def esc(s):
    return html.escape(s, quote=False)


def cmd_render(a):
    session = Path(a.session)
    mdir = session / 'marks'
    slugs = [a.slug] if a.slug else sorted(p.stem for p in mdir.glob('*.json'))
    if not slugs:
        sys.exit(f'Нет пометок в {mdir} — их пишет агент, формат в SKILL.md')
    for slug in slugs:
        page_f = session / 'pages' / f'{slug}.json'
        if not page_f.exists():
            sys.exit(f'Нет текста страницы {page_f} — сначала: page.py fetch <url> --session ...')
        page = json.loads(page_f.read_text(encoding='utf-8'))
        spec = json.loads((mdir / f'{slug}.json').read_text(encoding='utf-8'))
        out = render_one(session, slug, page, spec)
        print(f'✅ {out}')


def render_one(session, slug, page, spec):
    blocks = page['blocks']
    marks, lost = [], []
    for m in spec.get('marks', []):
        act = m.get('action', '').strip().lower()
        if act not in ACTIONS:
            lost.append((m, f'неизвестное действие «{act}»'))
            continue
        hits = locate(m.get('quote', ''), blocks, find_all=m.get('all', False))
        if not hits:
            lost.append((m, 'цитата не найдена в тексте'))
            continue
        for bn, s, e in hits:
            scope = m.get('scope', 'фрагмент')
            if scope == 'раздел':
                first, last = section_range(blocks, bn)
            elif scope == 'абзац':
                first = last = bn
            else:
                first = last = bn
            marks.append({**m, 'act': act, 'cls': ACTIONS[act][0], 'block': bn, 's': s, 'e': e,
                          'first': first, 'last': last, 'scope': scope})
    marks.sort(key=lambda m: (m['first'], m['block'], m['s']))
    for i, m in enumerate(marks, 1):
        m['k'] = i

    # Номера раздаются по месту в тексте, а не по порядку в marks — поэтому
    # в комментариях ссылаются на id: «то же, что [[ctr]]» → «то же, что №4»
    by_id = {}
    for m in marks:
        if m.get('id'):
            by_id.setdefault(m['id'], m['k'])
    unknown = set()

    def comment_html(text):
        def ref(mm):
            k = by_id.get(mm.group(1))
            if k is None:
                unknown.add(mm.group(1))
                return mm.group(0)
            return f'<a href="#m{k}">№{k}</a>'
        return re.sub(r'\[\[([\w-]+)\]\]', ref, esc(text))

    # что к какому блоку относится
    inline = {}      # блок -> пометки-фрагменты
    whole = {}       # блок -> класс пометки на весь блок
    notes_after = {}  # блок -> пометки, чьи комментарии идут после него
    for m in marks:
        if m['scope'] == 'фрагмент':
            inline.setdefault(m['block'], []).append(m)
        else:
            for bn in range(m['first'], m['last'] + 1):
                whole.setdefault(bn, m['cls'])
        anchor = m['last'] if (m['act'] == 'добавить' or m['scope'] == 'фрагмент') else m['first']
        if m['scope'] == 'фрагмент':
            anchor = m['block']
        notes_after.setdefault(anchor, []).append(m)

    def block_text(b):
        t = b['text']
        ms = [m for m in inline.get(b['n'], []) if m['act'] != 'добавить']
        if not ms:
            body = esc(t)
        else:
            cuts = sorted({0, len(t)} | {m['s'] for m in ms} | {m['e'] for m in ms})
            parts = []
            for x, y in zip(cuts, cuts[1:]):
                cover = [m for m in ms if m['s'] <= x and y <= m['e']]
                seg = esc(t[x:y])
                if cover:
                    seg = f'<mark class="{cover[-1]["cls"]}">{seg}</mark>'
                ends = [m for m in ms if m['e'] == y]
                seg += ''.join(f'<sup class="n">{m["k"]}</sup>' for m in ends)
                parts.append(seg)
            body = ''.join(parts)
        # «добавить» на абзац или раздел — метка у первого блока, текст пометки в конце
        adds = ([m for m in inline.get(b['n'], []) if m['act'] == 'добавить']
                + [m for m in marks if m['act'] == 'добавить' and m['scope'] != 'фрагмент'
                   and m['first'] == b['n']])
        body += ''.join(f'<sup class="n">+{m["k"]}</sup>' for m in adds)
        return body

    def note_html(ms):
        out = []
        for m in ms:
            src = ' · '.join(x for x in (m.get('time'), m.get('frame')) if x)
            label = ACTIONS[m['act']][1]
            if m['scope'] in ('абзац', 'раздел') and m['act'] not in ('добавить',):
                label += f' — {m["scope"]}'
            out.append(f'<div class="note {m["cls"]}" id="m{m["k"]}"><b class="k">{m["k"]}.</b> '
                       f'<b>{label}.</b> {comment_html(m.get("comment", ""))}'
                       + (f'<span class="src">{esc(src)}</span>' if src else '') + '</div>')
        return ''.join(out)

    body, i = [], 0
    while i < len(blocks):
        b = blocks[i]
        t = b['type']
        if t in ('td', 'th'):                      # таблица целиком
            tid, rows, j = b['table'], {}, i
            while j < len(blocks) and blocks[j].get('table') == tid:
                rows.setdefault(blocks[j]['row'], []).append(blocks[j])
                j += 1
            trs = []
            for r in sorted(rows):
                cells = ''.join(
                    f'<{c["type"]} class="blk {whole.get(c["n"], "")}"><span class="t">{block_text(c)}</span></{c["type"]}>'
                    for c in rows[r])
                trs.append(f'<tr>{cells}</tr>')
            body.append(f'<table>{"".join(trs)}</table>')
            body.append(note_html([m for n in range(i, j) for m in notes_after.get(n, [])]))
            i = j
            continue
        if t == 'li':                              # список целиком
            j = i
            items = []
            while j < len(blocks) and blocks[j]['type'] == 'li':
                c = blocks[j]
                pad = (c['depth'] - 1) * 22
                items.append(f'<li class="blk {whole.get(c["n"], "")}" style="margin-left:{pad}px">'
                             f'<span class="t">{block_text(c)}</span></li>')
                j += 1
            body.append(f'<ul>{"".join(items)}</ul>')
            body.append(note_html([m for n in range(i, j) for m in notes_after.get(n, [])]))
            i = j
            continue
        tag = t if (t[0] == 'h' and t[1:].isdigit()) else ('blockquote' if t == 'blockquote' else 'p')
        body.append(f'<{tag} class="blk {whole.get(b["n"], "")}"><span class="t">{block_text(b)}</span></{tag}>')
        body.append(note_html(notes_after.get(b['n'], [])))
        i += 1

    counts = {}
    for m in marks:
        counts[m['act']] = counts.get(m['act'], 0) + 1
    legend = ''.join(
        f'<span class="chip" style="background:var(--{cls});border-color:var(--{cls}-b)">'
        f'{label}: {counts.get(act, 0)}</span>'
        for act, (cls, label) in ACTIONS.items() if counts.get(act))
    need = [m for m in marks if m['act'] in ('запросить', 'уточнить')]
    need_html = ''
    if need or spec.get('questions'):
        items = ''.join(f'<li><a href="#m{m["k"]}">№{m["k"]}</a> — {comment_html(m.get("comment", ""))}</li>'
                        for m in need)
        items += ''.join(f'<li>{comment_html(q)}</li>' for q in spec.get('questions', []))
        need_html = f'<section class="need"><h2>Что нужно от вас</h2><ol>{items}</ol></section>'
    lost_html = ''
    if lost:
        rows = ''.join(f'<li><b>{esc(m.get("action", ""))}</b>: «{esc(m.get("quote", ""))}» — {why}</li>'
                       for m, why in lost)
        lost_html = (f'<section class="lost"><h2>Не удалось привязать к тексту</h2>'
                     f'<p>Этих цитат нет в выгруженном тексте — возможно, текст на странице '
                     f'изменился или цитата неточная.</p><ul>{rows}</ul></section>')
        for m, why in lost:
            print(f'   ⚠ не привязано ({why}): «{m.get("quote", "")[:60]}»')

    for u in sorted(unknown):
        print(f'   ⚠ ссылка [[{u}]] ни на что не указывает — нет пометки с таким id')
    title = next((b['text'] for b in blocks if b['type'] == 'h1'), slug)
    src = spec.get('source', '')
    doc = f"""<!doctype html><html lang="ru"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Пометки: {esc(title)}</title><style>{CSS}</style></head><body><div class="wrap">
<header class="doc"><h1>{esc(title)}</h1>
<div class="meta"><a href="{esc(page['url'])}">{esc(page['url'])}</a> · текст от {page['fetched']}{' · ' + esc(src) if src else ''}</div>
{f'<p class="verdict">Вердикт: {esc(spec["verdict"])}</p>' if spec.get('verdict') else ''}
<div class="legend">{legend}</div></header>
{need_html}
<article>{''.join(body)}</article>
{lost_html}
</div></body></html>"""
    out = session / f'annotated_{slug}.html'
    out.write_text(doc, encoding='utf-8')
    print(f'   пометок {len(marks)}' + (f', не привязано {len(lost)}' if lost else ''))
    return out


def main():
    ap = argparse.ArgumentParser(description='Текст страницы и пометки на нём')
    sub = ap.add_subparsers(dest='cmd', required=True)
    f = sub.add_parser('fetch', help='скачать страницу и сохранить текст статьи')
    f.add_argument('url')
    f.add_argument('--session', required=True, help='папка записи: data/<проект>/<дата>_<ЧЧММ>')
    f.add_argument('--slug', help='имя файла; по умолчанию — последний кусок адреса')
    f.add_argument('--force', action='store_true', help='выгрузить заново')
    f.add_argument('--html', help='сохранённая из браузера страница вместо скачивания')
    r = sub.add_parser('render', help='собрать annotated_<slug>.html из пометок агента')
    r.add_argument('--session', required=True)
    r.add_argument('--slug', help='одна страница; по умолчанию — все из marks/')
    a = ap.parse_args()
    cmd_fetch(a) if a.cmd == 'fetch' else cmd_render(a)


if __name__ == '__main__':
    main()
