"""Панель с диаграммами: графики на seaborn → лист «Панель» в Excel и dashboard.html.

Правила оформления — сдержанные: один цвет на рейтинг (у источников и страниц
нет порядка, поэтому «чем больше, тем темнее» здесь вредно), прошлый период —
серым рядом с текущим, тонкие сетки, никаких двух осей на одном графике.
Изменения окрашены по смыслу: рост отказов — плохо, хотя число растёт.
"""
import base64
from pathlib import Path

# Цвета: акцент — синий, прошлый период — серый, «лучше / хуже» — зелёный / красный
ACCENT = '#2a78d6'
PREVIOUS = '#b9b8b1'
GOOD = '#0ca30c'
BAD = '#d03b3b'
INK = '#0b0b0b'
INK_2 = '#52514e'
MUTED = '#898781'
GRID = '#e1e0d9'
BASELINE = '#c3c2b7'
SURFACE = '#fcfcfb'

# Для каких показателей рост — это хорошо
UP_IS_GOOD = {
    'Визиты': True, 'Посетители': True, 'Просмотры': True, 'Отказы, %': False,
    'Глубина просмотра': True, 'Время на сайте': True, 'Новые посетители, %': None,
    'Достижения целей': True, 'Конверсия в любую цель, %': True,
}


def available():
    try:
        import matplotlib  # noqa: F401
        import seaborn  # noqa: F401
        return True
    except ImportError:
        return False


def _setup():
    import matplotlib
    matplotlib.use('Agg')                       # без окон: рисуем в файлы
    import matplotlib.pyplot as plt
    import seaborn as sns
    sns.set_theme(style='whitegrid', rc={
        'figure.facecolor': SURFACE, 'axes.facecolor': SURFACE, 'savefig.facecolor': SURFACE,
        'axes.edgecolor': BASELINE, 'axes.labelcolor': INK_2, 'axes.titlecolor': INK,
        'axes.titlesize': 13, 'axes.titleweight': 'semibold', 'axes.titlelocation': 'left',
        'axes.titlepad': 12, 'grid.color': GRID, 'grid.linewidth': 0.8, 'grid.linestyle': '-',
        'xtick.color': MUTED, 'ytick.color': INK_2, 'xtick.labelsize': 9.5, 'ytick.labelsize': 10.5,
        'font.family': ['Segoe UI', 'DejaVu Sans', 'Arial', 'sans-serif'], 'legend.frameon': False,
    })
    return plt, sns


def _num(v):
    """1 284 / 12,9 тыс. / 4,2 млн — в подписях."""
    if v is None:
        return ''
    a = abs(v)
    if a >= 1_000_000:
        return f'{v / 1_000_000:.1f} млн'.replace('.', ',')
    if a >= 10_000:
        return f'{v / 1_000:.1f}'.replace('.', ',') + ' тыс.'
    if float(v).is_integer() or a >= 100:
        return f'{v:,.0f}'.replace(',', ' ')
    return f'{v:.1f}'.replace('.', ',')


def _tick(v, _=None):
    """Подписи оси — круглые: 10 тыс. вместо 10,0 тыс., чтобы не слипались."""
    if abs(v) >= 1_000_000:
        return f'{v / 1_000_000:g} млн'.replace('.', ',')
    if abs(v) >= 10_000:
        return f'{v / 1_000:g}'.replace('.', ',') + ' тыс.'
    return f'{v:,.0f}'.replace(',', ' ')


def _short(text, limit=38, site=''):
    """Подпись полосы. Адрес страницы — без протокола и домена сайта:
    «msk · /blog/kak-vybrat-…» вместо полного URL."""
    t = str(text)
    if t.startswith(('http://', 'https://')):
        from urllib.parse import urlparse
        u = urlparse(t)
        host = u.netloc[4:] if u.netloc.startswith('www.') else u.netloc
        path = u.path or '/'
        site = (site or '').lower().replace('www.', '')
        if site and host == site:
            t = path
        elif site and host.endswith('.' + site):
            t = f'{host[:-len(site) - 1]} · {path}'
        else:
            t = host + path
    return t if len(t) <= limit else t[:limit - 1] + '…'


def _figure(plt, width, height, title, note=None):
    """Холст фиксированной ширины: подписи ужимают область графика, а не
    расширяют картинку. Тогда все графики панели в одном масштабе — на узкой
    карточке шрифт не мельчает из-за длинных адресов страниц."""
    fig, ax = plt.subplots(figsize=(width, height), layout='constrained')
    fig.suptitle(title, x=0.01, ha='left', fontsize=13, fontweight='semibold', color=INK)
    if note:
        import textwrap
        fig.supxlabel(textwrap.fill(note, int(width * 15)), x=0.01, ha='left', fontsize=8.5, color=MUTED)
    return fig, ax


def _save(fig, out_dir, name):
    path = out_dir / f'{name}.png'
    fig.savefig(path, dpi=130)
    import matplotlib.pyplot as plt
    plt.close(fig)
    return path


def _bars(plt, sns, labels, values, title, out_dir, name, fmt=_num, color=ACCENT, note=None):
    """Горизонтальные полосы одного цвета, значение — на конце полосы."""
    # Полосы — напрямую через matplotlib: seaborn усреднил бы строки с одинаковой
    # подписью (две длинные страницы после сокращения), и график бы соврал
    n = len(labels)
    fig, ax = _figure(plt, 6.4, 0.44 * n + 1.2 + (0.35 if note else 0), title, note)
    ax.barh(range(n), values, color=color, height=0.6)
    ax.set_yticks(range(n))
    ax.set_yticklabels(labels)
    ax.invert_yaxis()
    ax.set_xlabel('')
    ax.set_ylabel('')
    ax.grid(axis='y', visible=False)
    ax.xaxis.grid(True)
    sns.despine(ax=ax, left=False, bottom=True)
    ax.tick_params(axis='x', length=0)
    ax.tick_params(axis='y', length=0)
    top = max(values) if values else 0
    ax.set_xlim(0, top * 1.24 if top else 1)    # запас справа под подпись «32,3 тыс.»
    for i, v in enumerate(values):
        ax.text(v + top * 0.012, i, fmt(v), va='center', ha='left', fontsize=10, color=INK_2)
    ax.xaxis.set_major_locator(plt.MaxNLocator(5))
    ax.xaxis.set_major_formatter(plt.FuncFormatter(_tick))
    return _save(fig, out_dir, name)


def _dynamics(plt, sns, dates, cur, prev, group_label, out_dir, partial_tail=0):
    """Визиты текущего периода и прошлого — на одной оси, прошлый серым.

    partial_tail — сколько последних точек Метрика, похоже, ещё не досчитала:
    они идут пунктиром с подписью, чтобы недосчёт не читался как обвал трафика."""
    fig, ax = _figure(plt, 11, 3.8, f'Визиты по {group_label}')
    x = list(range(len(cur)))
    if prev and any(v is not None for v in prev):
        px = x[:len(prev)]
        ax.plot(px, prev[:len(px)], color=PREVIOUS, linewidth=2, solid_capstyle='round', label='прошлый период')
    k = max(1, len(cur) - partial_tail)          # до этой точки данные полные
    ax.plot(x[:k], cur[:k], color=ACCENT, linewidth=2, solid_capstyle='round', label='текущий период')
    ax.fill_between(x[:k], cur[:k], color=ACCENT, alpha=0.08, linewidth=0)
    if partial_tail:
        ax.plot(x[k - 1:], cur[k - 1:], color=ACCENT, linewidth=2, linestyle=(0, (1.5, 2.5)), alpha=0.6)
        ax.scatter([x[-1]], [cur[-1]], s=46, color=SURFACE, edgecolor=ACCENT, linewidth=2, zorder=3)
        ax.annotate(f'{_num(cur[-1])} — данные, вероятно, неполные', (x[-1], cur[-1]), xytext=(-10, 4),
                    textcoords='offset points', va='bottom', ha='right', fontsize=9.5, color=INK_2)
    else:
        ax.scatter([x[-1]], [cur[-1]], s=46, color=ACCENT, edgecolor=SURFACE, linewidth=2, zorder=3)
        ax.annotate(_num(cur[-1]), (x[-1], cur[-1]), xytext=(8, 0), textcoords='offset points',
                    va='center', fontsize=9.5, color=INK_2)
    step = max(1, len(x) // 10)
    ax.set_xticks(x[::step])
    ax.set_xticklabels([d.strftime('%d.%m') for d in dates[::step]])
    ax.set_ylim(bottom=0)
    ax.yaxis.set_major_formatter(plt.FuncFormatter(_tick))
    ax.grid(axis='x', visible=False)
    sns.despine(ax=ax, left=True)
    ax.tick_params(length=0)
    ax.legend(loc='upper left', fontsize=9, ncol=2, labelcolor=INK_2)
    return _save(fig, out_dir, 'dynamics')


def _changes(plt, sns, kpis, out_dir):
    """Что изменилось к прошлому периоду: зелёным — лучше, красным — хуже."""
    rows = [(label, ch, unit) for label, _, _, ch, unit in kpis if ch is not None]
    if not rows:
        return None
    fig, ax = _figure(plt, 6.4, 0.44 * len(rows) + 1.55, 'Что изменилось к прошлому периоду',
                      'Зелёным — стало лучше, красным — хуже. Для долей в процентах — разница в процентных пунктах.')
    labels = [r[0] for r in rows]
    vals = [r[1] for r in rows]
    colors = []
    for label, ch, _ in rows:
        good = UP_IS_GOOD.get(label)
        colors.append(MUTED if good is None or ch == 0 else (GOOD if (ch > 0) == good else BAD))
    ax.barh(range(len(rows)), vals, color=colors, height=0.6)
    ax.set_yticks(range(len(rows)))
    ax.set_yticklabels(labels)
    ax.invert_yaxis()
    ax.axvline(0, color=BASELINE, linewidth=1)
    lim = max(abs(v) for v in vals) or 1
    ax.set_xlim(-lim * 1.45, lim * 1.45)
    for i, (label, ch, unit) in enumerate(rows):
        txt = f'{ch:+.1f}'.replace('.', ',') + (' п.п.' if unit == 'pp' else '%')
        ax.text(ch + (lim * 0.03 if ch >= 0 else -lim * 0.03), i, txt, va='center',
                ha='left' if ch >= 0 else 'right', fontsize=10, color=INK_2)
    ax.grid(axis='y', visible=False)
    ax.xaxis.set_visible(False)
    sns.despine(ax=ax, left=True, bottom=True)
    ax.tick_params(length=0)
    return _save(fig, out_dir, 'changes')


def build(ctx, out_dir):
    """Рисует графики по собранным данным. Возвращает [(заголовок, путь, подпись)]."""
    plt, sns = _setup()
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    charts = {}
    t = ctx['tables']

    dyn = ctx.get('dynamics')
    if dyn and dyn['cur']:
        p = _dynamics(plt, sns, dyn['dates'], dyn['cur'], dyn.get('prev'), dyn['label'], out_dir,
                      dyn.get('partial_tail', 0))
        charts['dynamics'] = ('Динамика визитов', p, 'Прошлый период совмещён по порядку дней — лист «Динамика»')

    p = _changes(plt, sns, ctx['kpis'], out_dir)
    if p:
        charts['changes'] = ('Что изменилось', p, 'Сравнение с прошлым периодом такой же длины — лист «Сводка»')

    def rows_of(key, n, skip_undefined=False, min_visits=0, metric=0):
        if key not in t:
            return [], []
        dims, metrics, rows, _ = t[key]
        out = [(r[0][0], r[1][metric]) for r in rows
               if r[1][metric] is not None and (not skip_undefined or r[0][0] not in (None, ''))
               and (r[1][0] or 0) >= min_visits]
        out = out[:n]
        return ([_short(x[0] if x[0] not in (None, '') else '(не определено)', site=ctx['site']) for x in out],
                [x[1] for x in out])

    for key, title, name, caption, kw in (
        ('sources', 'Визиты по источникам', 'sources', 'Последний значимый источник — лист «Источники»', {}),
        ('engines', 'Визиты из поисковых систем', 'engines', 'Лист «Поисковые системы»', {}),
        ('entry', 'Страницы входа — топ-10', 'entry', 'С каких страниц начинались визиты — лист «Страницы входа»', {}),
        ('phrases', 'Поисковые фразы — топ-12', 'phrases',
         'Большую часть фраз поисковики скрывают — лист «Поисковые фразы»', {'skip_undefined': True}),
        ('devices', 'Визиты по устройствам', 'devices', 'Лист «Устройства»', {}),
        ('cities', 'Города — топ-10', 'cities', 'Лист «Города»', {'skip_undefined': True}),
    ):
        n = 12 if key == 'phrases' else 10
        labels, values = rows_of(key, n, **kw)
        if len(labels) >= 2:
            charts[name] = (title, _bars(plt, sns, labels, values, title, out_dir, name), caption)

    # Конверсия по источникам — только источники от 100 визитов, иначе шум
    if 'sources' in t:
        dims, metrics, rows, _ = t['sources']
        idx = next((i for i, m in enumerate(metrics) if m[0] == 'ym:s:anyGoalConversionRate'), None)
        if idx is not None:
            sel = [(r[0][0], r[1][idx]) for r in rows if (r[1][0] or 0) >= 100 and r[1][idx] is not None]
            sel.sort(key=lambda x: -x[1])
            if len(sel) >= 2:
                p = _bars(plt, sns, [_short(s[0], site=ctx['site']) for s in sel], [s[1] for s in sel],
                          'Конверсия в любую цель по источникам, %', out_dir, 'conversion',
                          fmt=lambda v: f'{v:.1f}%'.replace('.', ','),
                          note='Источники от 100 визитов. Цели вовлечённости («3 страницы», «время на сайте») '
                               'тоже входят в конверсию.')
                charts['conversion'] = ('Конверсия по источникам', p, 'Лист «Источники»')

    goals = ctx.get('goals') or []
    goals = [g for g in goals if g[1]][:10]
    if len(goals) >= 2:
        p = _bars(plt, sns, [_short(g[0]) for g in goals], [g[1] for g in goals],
                  'Цели — топ-10 по достижениям', out_dir, 'goals')
        charts['goals'] = ('Цели', p, 'Достижения и конверсия каждой цели — лист «Цели»')
    # Порядок на панели: соседние графики в паре дополняют друг друга
    order = ['dynamics', 'changes', 'sources', 'conversion', 'engines', 'devices',
             'entry', 'phrases', 'cities', 'goals']
    return [charts[k] for k in order if k in charts]


def _b64(path):
    return base64.b64encode(Path(path).read_bytes()).decode()


def excel_panel(wb, ctx, charts):
    """Лист «Панель» первым: плитки с цифрами и графики в две колонки."""
    from openpyxl.drawing.image import Image
    from openpyxl.styles import Alignment, Font, PatternFill
    from openpyxl.utils import get_column_letter
    ws = wb.create_sheet('Панель', 0)
    ws.sheet_view.showGridLines = False
    ws['A1'] = f'Яндекс Метрика: {ctx["site"]}'
    ws['A1'].font = Font(bold=True, size=16)
    ws['A2'] = ctx['period_text']
    ws['A2'].font = Font(color='52514E')
    if ctx.get('warnings'):
        ws['A3'] = ' '.join(ctx['warnings'])
        ws['A3'].font = Font(color='8A5A00')
    tile_fill = PatternFill('solid', fgColor='F3F3F0')
    col = 1
    for label, value, shown, ch, unit in ctx['kpis'][:6]:
        c1 = ws.cell(row=4, column=col, value=label)
        c2 = ws.cell(row=5, column=col, value=shown)
        txt = '' if ch is None else (f'{ch:+.1f}'.replace('.', ',') + (' п.п.' if unit == 'pp' else '%'))
        good = UP_IS_GOOD.get(label)
        color = '898781' if ch is None or good is None or ch == 0 else (
            '006300' if (ch > 0) == good else 'D03B3B')
        c3 = ws.cell(row=6, column=col, value=f'{txt} к прошлому периоду' if txt else '')
        c1.font, c2.font, c3.font = Font(color='52514E', size=9), Font(bold=True, size=15), Font(color=color, size=9)
        for c in (c1, c2, c3):
            c.fill = tile_fill
            c.alignment = Alignment(horizontal='left', vertical='center')
        ws.merge_cells(start_row=4, start_column=col, end_row=4, end_column=col + 2)
        ws.merge_cells(start_row=5, start_column=col, end_row=5, end_column=col + 2)
        ws.merge_cells(start_row=6, start_column=col, end_row=6, end_column=col + 2)
        ws.column_dimensions[get_column_letter(col + 3)].width = 2   # узкий промежуток между плитками
        col += 4
    ws.row_dimensions[5].height = 26
    # Графики — по два в ряд: левый от колонки A, правый от M (≈ 630 px: промежутки
    # между плитками узкие). Ряд занимает высоту большего из двух графиков
    # (строка Excel — 20 пикселей)
    layout, pair = [], []
    for ch in charts:
        if Path(ch[1]).stem == 'dynamics':      # широкий график — один в ряду
            if pair:
                layout.append(pair)
                pair = []
            layout.append([ch])
        else:
            pair.append(ch)
            if len(pair) == 2:
                layout.append(pair)
                pair = []
    if pair:
        layout.append(pair)
    row = 8
    for line in layout:
        tallest = 0
        for j, (_, path, _) in enumerate(line):
            img = Image(str(path))
            width = 1220 if Path(path).stem == 'dynamics' else 600
            scale = width / img.width
            img.width, img.height = int(img.width * scale), int(img.height * scale)
            ws.add_image(img, f'{"A" if j == 0 else "M"}{row}')
            tallest = max(tallest, img.height)
        row += tallest // 20 + 2
    return ws


def html_dashboard(path, ctx, charts):
    """Один самодостаточный HTML: картинки встроены, открывается двойным щелчком."""
    def tile(label, shown, ch, unit):
        good = UP_IS_GOOD.get(label)
        if ch is None:
            delta = ''
        else:
            arrow = '▲' if ch > 0 else ('▼' if ch < 0 else '•')
            cls = 'flat' if good is None or ch == 0 else ('up' if (ch > 0) == good else 'down')
            val = f'{ch:+.1f}'.replace('.', ',') + (' п.п.' if unit == 'pp' else '%')
            delta = f'<div class="delta {cls}">{arrow} {val} <span>к прошлому периоду</span></div>'
        return f'<div class="tile"><div class="label">{label}</div><div class="value">{shown}</div>{delta}</div>'

    tiles = ''.join(tile(label, shown, ch, unit) for label, _, shown, ch, unit in ctx['kpis'])
    cards = ''.join(
        f'<figure class="card{" wide" if Path(p).stem == "dynamics" else ""}">'
        f'<img src="data:image/png;base64,{_b64(p)}" alt="{title}"><figcaption>{cap}</figcaption></figure>'
        for title, p, cap in charts)
    sampled = ctx.get('sampled_note') or ''
    warn = ''.join(f'<p class="warn">{w}</p>' for w in ctx.get('warnings', []))
    html = f"""<!doctype html><html lang="ru"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Метрика: {ctx['site']} · {ctx['period_short']}</title>
<style>
:root{{--surface:#fcfcfb;--page:#f5f5f2;--ink:#0b0b0b;--ink2:#52514e;--muted:#898781;--line:#e1e0d9;--good:#006300;--bad:#d03b3b}}
*{{box-sizing:border-box}}
body{{margin:0;background:var(--page);color:var(--ink);font:15px/1.5 system-ui,-apple-system,"Segoe UI",sans-serif}}
.wrap{{max-width:1240px;margin:0 auto;padding:32px 24px 56px}}
header h1{{font-size:26px;margin:0 0 4px;font-weight:600}}
header p{{margin:0;color:var(--ink2)}}
.tiles{{display:grid;grid-template-columns:repeat(auto-fit,minmax(170px,1fr));gap:12px;margin:24px 0}}
.tile{{background:var(--surface);border:1px solid var(--line);border-radius:12px;padding:14px 16px}}
.tile .label{{color:var(--ink2);font-size:13px}}
.tile .value{{font-size:28px;font-weight:600;margin:2px 0}}
.delta{{font-size:13px;font-weight:600}}
.delta span{{font-weight:400;color:var(--muted)}}
.warn{{margin:12px 0 0;padding:10px 14px;background:#fff6e6;border:1px solid #f1d39a;border-radius:10px;color:#5c4400}}
.delta.up{{color:var(--good)}} .delta.down{{color:var(--bad)}} .delta.flat{{color:var(--muted)}}
.grid{{display:grid;grid-template-columns:repeat(auto-fit,minmax(520px,1fr));gap:16px}}
.card{{margin:0;background:var(--surface);border:1px solid var(--line);border-radius:12px;padding:12px 12px 10px}}
.card.wide{{grid-column:1/-1}}
.card img{{width:100%;height:auto;display:block}}
.card figcaption{{color:var(--muted);font-size:12.5px;padding:6px 4px 0}}
footer{{margin-top:24px;color:var(--muted);font-size:13px}}
@media (max-width:600px){{.grid{{grid-template-columns:1fr}}}}
</style></head><body><div class="wrap">
<header><h1>Яндекс Метрика: {ctx['site']}</h1><p>{ctx['period_text']}</p>{warn}</header>
<section class="tiles">{tiles}</section>
<section class="grid">{cards}</section>
<footer>Выгружено {ctx['exported']} через API Метрики. Все цифры — в Excel-файле рядом: {ctx['xlsx_name']}.{(' ' + sampled) if sampled else ''}</footer>
</div></body></html>"""
    Path(path).write_text(html, encoding='utf-8')
    return path
