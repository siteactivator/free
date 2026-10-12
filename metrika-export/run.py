"""Выгрузка отчётов Яндекс Метрики в Excel через API — без веб-интерфейса.

Когда интерфейс Метрики не открывается или нужна выгрузка в Excel, данные
можно забрать напрямую из API: оно живёт отдельно от интерфейса.

    python run.py counters                         список ваших счётчиков
    python run.py report 12345678                  отчёт за 30 дней
    python run.py report site.ru --days 90         счётчик можно назвать по сайту
    python run.py report 12345678 --from 2026-09-01 --to 2026-09-30
    python run.py raw 12345678 --metrics ym:s:visits --dimensions ym:s:lastsignTrafficSource
    python run.py direct 12345678                  отчёт по Яндекс Директу
    python run.py seo 12345678                     SEO: поиск год к году, фразы, длинные клики
    python run.py panel                            все панели в браузере: http://127.0.0.1:8790/

Токен — в файле .env рядом со скриптом (METRIKA_TOKEN=...), как его получить —
README.md. Результаты — в data/ рядом со скриптом.
"""
import argparse
import os
import re
import sys
import time
from datetime import date, datetime, timedelta
from pathlib import Path

# Консоль Windows по умолчанию не в UTF-8: без этого кириллица в выводе
# роняет скрипт, если вывод перенаправлен в файл
for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding='utf-8', errors='replace')
    except (AttributeError, ValueError):
        pass

try:
    import requests
    from openpyxl import Workbook
    from openpyxl.styles import Alignment, Font, PatternFill
    from openpyxl.utils import get_column_letter
except ImportError as e:
    sys.exit(f'Нет библиотеки ({e.name}). Выполните в этой папке:\n'
             '  python -m pip install -r requirements.txt')

import charts  # noqa: E402  панель с графиками; seaborn подгружается только при сборке

BASE = Path(__file__).resolve().parent
ENV_PATH = BASE / '.env'
# Папку с отчётами можно подменить: панель запускает отчёты в свою папку (--data)
DATA = Path(os.environ['METRIKA_DATA']) if os.environ.get('METRIKA_DATA') else BASE / 'data'
API = 'https://api-metrika.yandex.net'

# Лимиты API: 30 запросов в секунду с IP, 3 параллельных, 5000 в сутки,
# 200 отчётов за 5 минут на счётчик. Полный отчёт — 20–30 запросов, пауза
# между ними держит нас далеко от любого из лимитов
PAUSE = 0.3
RETRIES = 4              # повторы при сбое сети, 5xx и 429
PAGE = 10000             # строк за запрос (API даёт до 100 000)
TOP = 10000              # строк на лист по умолчанию
# «Запрос слишком сложный» (400) при перегрузке API: сначала повтор с паузой,
# потом выборка — 50 %, затем 10 %. Лист с выборкой помечается в Сводке
COMPLEX_RETRIES = 3     # под перегрузкой Яндекс режет запрос через ~10 с; через полминуты тот же проходит
SAMPLED_FALLBACK = ('0.5', '0.1')
GOALS_PER_REQUEST = 10   # 2 метрики на цель; в одном запросе — до 20 метрик
GOALS_BY_SOURCE = 9      # то же по источникам: визиты + 2 × 9 = 19 метрик

# (метрика, заголовок, вид): int — целое, pct — проценты, float — дробное, dur — время
M_SESSION = [
    ('ym:s:visits', 'Визиты', 'int'),
    ('ym:s:users', 'Посетители', 'int'),
    ('ym:s:pageviews', 'Просмотры', 'int'),
    ('ym:s:bounceRate', 'Отказы, %', 'pct'),
    ('ym:s:pageDepth', 'Глубина просмотра', 'float'),
    ('ym:s:avgVisitDurationSeconds', 'Время на сайте', 'dur'),
    ('ym:s:percentNewVisitors', 'Новые посетители, %', 'pct'),
]
M_GOALS = [
    ('ym:s:sumGoalReachesAny', 'Достижения целей', 'int'),
    ('ym:s:anyGoalConversionRate', 'Конверсия в любую цель, %', 'pct'),
]
M_SHORT = [m for m in M_SESSION if m[0] not in ('ym:s:pageviews', 'ym:s:percentNewVisitors')]

# ключ: (лист, [(группировка, заголовок)], метрики, что это)
REPORTS = {
    'sources':   ('Источники', [('ym:s:lastsignTrafficSource', 'Источник')], M_SESSION + M_GOALS,
                  'откуда пришли: поиск, реклама, ссылки, прямые заходы — по последнему значимому источнику'),
    'engines':   ('Поисковые системы', [('ym:s:lastsignSearchEngineRoot', 'Поисковая система')],
                  M_SESSION + M_GOALS, 'переходы из Яндекса, Google и других поисковиков'),
    'phrases':   ('Поисковые фразы', [('ym:s:lastsignSearchPhrase', 'Поисковая фраза')], M_SHORT + M_GOALS[:1],
                  'по каким запросам пришли; большую часть фраз поисковики скрывают — «(не определено)»'),
    'referrals': ('Сайты-источники', [('ym:s:lastsignReferalSource', 'Сайт')], M_SESSION + M_GOALS,
                  'переходы по ссылкам с других сайтов'),
    'ads':       ('Рекламные системы', [('ym:s:lastsignAdvEngine', 'Рекламная система')], M_SESSION + M_GOALS,
                  'переходы по рекламе: Директ, Google Ads и другие'),
    'social':    ('Соцсети', [('ym:s:lastsignSocialNetwork', 'Соцсеть')], M_SESSION + M_GOALS,
                  'переходы из социальных сетей'),
    'utm':       ('UTM-метки', [('ym:s:lastsignUTMSource', 'utm_source'), ('ym:s:lastsignUTMMedium', 'utm_medium'),
                                ('ym:s:lastsignUTMCampaign', 'utm_campaign')], M_SESSION + M_GOALS,
                  'переходы с UTM-метками'),
    'entry':     ('Страницы входа', [('ym:s:startURL', 'Страница входа')], M_SESSION + M_GOALS,
                  'с каких страниц начинались визиты'),
    'pages':     ('Популярные страницы', [('ym:pv:URL', 'Страница')],
                  [('ym:pv:pageviews', 'Просмотры', 'int'), ('ym:pv:users', 'Посетители', 'int')],
                  'самые просматриваемые страницы'),
    'devices':   ('Устройства', [('ym:s:deviceCategory', 'Устройство')], M_SESSION + M_GOALS,
                  'ПК, смартфоны, планшеты'),
    'os':        ('ОС', [('ym:s:operatingSystemRoot', 'Операционная система')], M_SESSION + M_GOALS,
                  'операционные системы'),
    'browsers':  ('Браузеры', [('ym:s:browser', 'Браузер')], M_SESSION + M_GOALS, 'браузеры'),
    'countries': ('Страны', [('ym:s:regionCountry', 'Страна')], M_SESSION + M_GOALS, 'страны'),
    'cities':    ('Города', [('ym:s:regionCity', 'Город')], M_SESSION + M_GOALS, 'города'),
}
SPECIAL = {'dynamics': 'Динамика', 'goals': 'Цели', 'goal_sources': 'Цели по источникам'}
ORDER = ['dynamics', 'sources', 'engines', 'phrases', 'referrals', 'ads', 'social', 'utm',
         'entry', 'pages', 'goals', 'goal_sources', 'devices', 'os', 'browsers', 'countries', 'cities']
# Типы целей из API — на русский, как в интерфейсе; неизвестный тип остаётся как есть
GOAL_TYPES = {
    'url': 'Посещение страниц', 'number': 'Количество просмотров', 'step': 'Составная цель',
    'action': 'JavaScript-событие', 'visit_duration': 'Время на сайте', 'phone': 'Клик по номеру телефона',
    'email': 'Клик по email', 'form': 'Отправка формы', 'messenger': 'Переход в мессенджер',
    'file': 'Скачивание файла', 'search': 'Поиск по сайту', 'button': 'Клик по кнопке',
    'social': 'Переход в соцсеть', 'payment_system': 'Платёжная система', 'conditional_call': 'Целевой звонок',
    'call': 'Звонок', 'offline': 'Офлайн-конверсия', 'site': 'Посещение сайта',
    'e_purchase': 'Ecommerce: покупка', 'e_cart': 'Ecommerce: корзина',
    'contact_data': 'Контактные данные', 'contact_data_sent': 'Отправка контактных данных',
    'a_begin_checkout': 'Автоцель: начало оформления заказа', 'a_create_order': 'Автоцель: оформление заказа',
}
GROUPS = {'day': ('ym:s:date', 'День'), 'week': ('ym:s:startOfWeek', 'Неделя (с понедельника)'),
          'month': ('ym:s:startOfMonth', 'Месяц')}
# С чем сравнивать: подпись блока на панели, «с чем» в тексте, «к чему» в плитках
COMPARE = {'prev': ('Сравнение', 'прошлый период', 'к прошлому периоду'),
           'yoy': ('Год назад', 'год назад', 'к году назад'),
           'mom': ('Прошлый месяц', 'прошлый месяц', 'к прошлому месяцу'),
           'qoq': ('Прошлый квартал', 'прошлый квартал', 'к прошлому кварталу')}
YEAR = timedelta(days=364)   # «год назад» — ровно 52 недели: дни недели совпадают
# Роботность «Только люди», как в интерфейсе Метрики: роботы — до 15–20 % визитов
# и сильнее всего искажают отказы и время. --with-robots — считать всех
HUMANS_ONLY = True
# Главная цель (--goal): вместо «любой цели» достижения и конверсия везде считаются по
# ней — и цена цели в сегментах тоже. Цели вовлечённости («3 страницы») иначе делают
# конверсию 80–90 % и цену цели в рублях — бессмысленной
FOCUS_GOAL = None        # (номер, название)


def log(*a):
    print(*a, flush=True)


# ─── токен и запросы ────────────────────────────────────────────────────────
def load_env(path):
    """KEY=VALUE из .env в окружение; уже заданные переменные не трогает."""
    if not path.is_file():
        return
    for line in path.read_text(encoding='utf-8-sig').splitlines():
        line = line.strip()
        if not line or line.startswith('#') or '=' not in line:
            continue
        key, _, val = line.partition('=')
        os.environ.setdefault(key.strip(), val.strip().strip('"').strip("'"))


def token():
    load_env(ENV_PATH)
    t = os.environ.get('METRIKA_TOKEN') or os.environ.get('METRICA_TOKEN')
    if not t:
        sys.exit(f'METRIKA_TOKEN не найден. Создайте файл {ENV_PATH}\n'
                 'со строкой METRIKA_TOKEN=ваш_токен (образец — .env.example, README, шаг 3).')
    return t


class TooComplex(Exception):
    """API перегружен или запрос тяжёлый: «Запрос слишком сложный»."""


class QueryError(Exception):
    """Ошибка в самом запросе: неизвестная метрика, нет доступа к счётчику."""


SESSION = requests.Session()


def api_get(path, params=None):
    """GET к API Метрики: повторы при сбоях сети, 5xx и 429; понятные ошибки."""
    SESSION.headers['Authorization'] = f'OAuth {token()}'
    for attempt in range(1, RETRIES + 1):
        time.sleep(PAUSE)
        try:
            r = SESSION.get(API + path, params=params, timeout=120)
        except requests.exceptions.RequestException as e:
            if attempt < RETRIES:
                log(f'   сеть: {type(e).__name__}, повтор через {5 * attempt} с…')
                time.sleep(5 * attempt)
                continue
            sys.exit(f'API Метрики недоступно: {e}\nПроверьте интернет и попробуйте позже.')
        if r.status_code == 200:
            return r.json()
        try:
            msg = '; '.join(e.get('message', '') for e in r.json().get('errors', [])) or r.text[:300]
        except ValueError:
            msg = r.text[:300]
        # Неверный токен Метрика отдаёт как 403 «Invalid oauth_token», не 401
        if r.status_code == 401 or (r.status_code == 403 and 'oauth' in msg.lower()):
            sys.exit(f'{r.status_code}: токен не подошёл — истёк, отозван или скопирован не полностью.\n'
                     'Получите новый (README, шаг 3) и замените его в .env.')
        if r.status_code == 403:
            raise QueryError(f'нет доступа (403): {msg}')
        if r.status_code == 429 or r.status_code >= 500:
            if attempt < RETRIES:
                wait = float(r.headers.get('Retry-After', 0) or 0) or 10 * attempt
                log(f'   API ответило {r.status_code}, жду {wait:.0f} с…')
                time.sleep(wait)
                continue
            raise QueryError(f'API ответило {r.status_code}: {msg}')
        if r.status_code == 400 and 'слишком сложн' in msg.lower():
            raise TooComplex(msg)
        raise QueryError(f'{r.status_code}: {msg}')
    raise QueryError('не удалось получить ответ')


def stat(counter, metrics, dims, d1, d2, top=TOP, sort=None, extra=None, ids=False):
    """Отчёт /stat/v1/data со страницами. Слишком сложный — повтор, потом выборка.

    Возвращает строки [(значения группировок, значения метрик)], итоги и пометку
    о выборке: None — данные полные, иначе доля выборки. extra — добавочные
    параметры запроса (filters, direct_client_logins); ids=True — значения
    группировок парами (название, id): номер кампании Директа и т. п."""
    if FOCUS_GOAL and metrics and metrics[0].startswith('ym:s:'):
        g = FOCUS_GOAL[0]
        metrics = [f'ym:s:goal{g}reaches' if m == 'ym:s:sumGoalReachesAny' else
                   f'ym:s:goal{g}conversionRate' if m == 'ym:s:anyGoalConversionRate' else m for m in metrics]
    accuracies = ['full'] * (1 + COMPLEX_RETRIES) + list(SAMPLED_FALLBACK)
    last = None
    for i, acc in enumerate(accuracies):
        try:
            return _stat_pages(counter, metrics, dims, d1, d2, top, sort, acc, extra, ids)
        except TooComplex as e:
            last = e
            if i + 1 < len(accuracies):
                nxt = accuracies[i + 1]
                if nxt == 'full':
                    log(f'   API перегружено («запрос слишком сложный»), повтор через {15 * (i + 1)} с…')
                    time.sleep(15 * (i + 1))
                else:
                    log(f'   всё ещё «слишком сложный» — беру выборку {float(nxt):.0%}')
    raise QueryError(f'API не отдало отчёт даже по выборке 10 %: {last}')


def _stat_pages(counter, metrics, dims, d1, d2, top, sort, accuracy, extra=None, ids=False):
    rows, totals, share, total_rows = [], None, 1.0, 0
    offset = 1
    while len(rows) < top:
        params = {'ids': counter, 'metrics': ','.join(metrics), 'date1': d1, 'date2': d2,
                  'limit': min(PAGE, top - len(rows)), 'offset': offset, 'accuracy': accuracy, 'lang': 'ru'}
        if dims:
            params['dimensions'] = ','.join(dims)
        if sort:
            params['sort'] = sort
        params.update(extra or {})
        ns = metrics[0].rsplit(':', 1)[0] if metrics else ''
        if HUMANS_ONLY and ns in ('ym:s', 'ym:pv'):          # у расходов Директа (ym:ad) роботности нет
            robot = f"{ns}:isRobot=='No'"
            params['filters'] = f"{robot} AND ({params['filters']})" if params.get('filters') else robot
        j = api_get('/stat/v1/data', params)
        if totals is None:
            totals = j.get('totals') or []
            total_rows = j.get('total_rows', 0)
            if j.get('sampled'):
                share = float(j.get('sample_share') or 1.0)
        data = j.get('data') or []
        for x in data:
            rows.append(([(d.get('name'), d.get('id')) if ids else d.get('name') for d in x.get('dimensions', [])],
                         x.get('metrics', [])))
        offset += len(data)
        if not data or offset > total_rows:
            break
    return rows, totals, (None if share >= 1.0 else share), total_rows


# ─── счётчики ───────────────────────────────────────────────────────────────
def all_counters():
    out, offset = [], 1
    while True:
        j = api_get('/management/v1/counters', {'per_page': 1000, 'offset': offset})
        chunk = j.get('counters') or []
        out += chunk
        offset += len(chunk)
        if not chunk or offset > j.get('rows', 0):
            return out


PERMISSION = {'own': 'свой', 'view': 'просмотр', 'edit': 'редактирование', 'analyst': 'аналитик'}


def resolve_counter(arg):
    """Номер счётчика или адрес сайта → счётчик из списка доступных."""
    counters = all_counters()
    if arg.isdigit():
        c = next((c for c in counters if str(c['id']) == arg), None)
        if c is None:
            sys.exit(f'Счётчика {arg} нет среди доступных этому токену. Список — python run.py counters')
        return c
    needle = arg.lower().replace('https://', '').replace('http://', '').strip('/')
    def names(c):
        s2 = c.get('site2')
        s2 = s2.get('site', '') if isinstance(s2, dict) else (s2 or '')
        return ' '.join([c.get('site') or '', s2, c.get('name') or '']).lower()
    found = [c for c in counters if needle in names(c)]
    if len(found) > 1:
        # Точное совпадение адреса сайта выигрывает: «site.ru» — это сайт, а не
        # карточки на Картах, у которых в названии тоже есть «site»
        exact = [c for c in found if (c.get('site') or '').lower().strip('/') in (needle, 'www.' + needle)]
        if len(exact) == 1:
            return exact[0]
    if len(found) == 1:
        return found[0]
    if not found:
        sys.exit(f'Не нашёл счётчик по «{arg}». Список — python run.py counters')
    lines = '\n'.join(f'  {c["id"]}  {c.get("site", "")}  {c.get("name", "")}' for c in found[:20])
    sys.exit(f'По «{arg}» подходит несколько счётчиков — укажите номер:\n{lines}')


# ─── Excel ──────────────────────────────────────────────────────────────────
HEAD_FONT = Font(bold=True, color='FFFFFF')
HEAD_FILL = PatternFill('solid', fgColor='4A5D7E')
TOTAL_FONT = Font(bold=True)
TOTAL_FILL = PatternFill('solid', fgColor='EEF1F6')
FORMATS = {'int': '#,##0', 'pct': '0.0', 'float': '0.00', 'dur': '[h]:mm:ss', 'money': '#,##0.00',
           'chg': '+0.0"%";-0.0"%";0"%"'}


def cell_value(v, kind):
    if v is None:
        return None
    if kind == 'int':
        return int(round(v))
    if kind == 'dur':
        return v / 86400          # время Excel — доля суток
    return round(v, 2)


def dim_value(v, header):
    if v is None or v == '':
        return '(не определено)'
    if header in ('День', 'Неделя (с понедельника)', 'Месяц') and re.match(r'\d{4}-\d{2}-\d{2}$', str(v)):
        return datetime.strptime(v, '%Y-%m-%d').date()
    return v


def write_table(ws, dim_headers, metrics, rows, totals):
    """Итоги — первой строкой, над шапкой: так они не попадают в автофильтр и не
    уезжают при сортировке."""
    heads = dim_headers + [charts.lbl(m[1]) for m in metrics]
    if totals:
        ws.append(['Итого и в среднем'] + [None] * (len(dim_headers) - 1)
                  + [cell_value(v, m[2]) for v, m in zip(totals, metrics)])
        for c in ws[1]:
            c.font, c.fill = TOTAL_FONT, TOTAL_FILL
    ws.append(heads)
    hr = ws.max_row
    for c in ws[hr]:
        c.font, c.fill = HEAD_FONT, HEAD_FILL
        c.alignment = Alignment(horizontal='center', vertical='center', wrap_text=True)
    ws.row_dimensions[hr].height = 32
    for dims, vals in rows:
        ws.append([dim_value(v, h) for v, h in zip(dims, dim_headers)]
                  + [cell_value(v, m[2]) for v, m in zip(vals, metrics)])
    nd = len(dim_headers)
    for i, m in enumerate(metrics, nd + 1):
        for col in ws.iter_cols(min_col=i, max_col=i):
            for c in col:
                if c.row != hr:
                    c.number_format = FORMATS[m[2]]
    for i, h in enumerate(dim_headers, 1):
        width = 14 if h in ('День', 'Неделя (с понедельника)', 'Месяц') else (60 if h in (
            'Страница входа', 'Страница', 'Поисковая фраза', 'Сайт') else 28)
        ws.column_dimensions[get_column_letter(i)].width = width
        if width == 14:
            for col in ws.iter_cols(min_col=i, max_col=i, min_row=hr + 1):
                for c in col:
                    c.number_format = 'DD.MM.YYYY'
    if totals and nd and ws.column_dimensions['A'].width < 20:
        ws.column_dimensions['A'].width = 20            # чтобы «Итого и в среднем» читалось
    for i in range(nd + 1, nd + len(metrics) + 1):
        ws.column_dimensions[get_column_letter(i)].width = 15
    ws.freeze_panes = ws.cell(row=hr + 1, column=nd + 1)
    ws.auto_filter.ref = f'A{hr}:{get_column_letter(len(heads))}{ws.max_row}'


def note_sheet(ws, text):
    ws['A1'] = text
    ws['A1'].alignment = Alignment(wrap_text=True, vertical='top')
    ws.column_dimensions['A'].width = 100


# ─── отчёт ──────────────────────────────────────────────────────────────────
def period(a):
    if a.date_from or a.date_to:
        if not (a.date_from and a.date_to):
            sys.exit('Нужны обе даты: --from 2026-09-01 --to 2026-09-30')
        d1, d2 = (datetime.strptime(x, '%Y-%m-%d').date() for x in (a.date_from, a.date_to))
    else:
        d2 = date.today() - timedelta(days=1)        # вчера: сегодняшний день неполный
        d1 = d2 - timedelta(days=a.days - 1)
    if d1 > d2:
        sys.exit('Дата начала позже даты конца')
    return d1, d2


def pick_periods(a, default='prev'):
    """Период и с чем сравнить: (начало, конец, начало сравнения, конец сравнения, режим).

    prev — столько же дней прямо перед периодом; yoy — полные недели по прошлое
    воскресенье против тех же недель год назад; mom, qoq — последний полный месяц
    или квартал против предыдущего. С явными --from/--to: yoy — те же даты минус
    52 недели, остальное — как prev."""
    mode = getattr(a, 'compare', None) or default
    today = date.today()
    quarter = lambda d: date(d.year, 3 * ((d.month - 1) // 3) + 1, 1)   # noqa: E731 — начало квартала
    if a.date_from or a.date_to:
        d1, d2 = period(a)
        if mode == 'yoy':
            return d1, d2, d1 - YEAR, d2 - YEAR, mode
        mode = 'prev'
    elif mode == 'mom':
        d2 = today.replace(day=1) - timedelta(days=1)
        d1 = d2.replace(day=1)
        p2 = d1 - timedelta(days=1)
        return d1, d2, p2.replace(day=1), p2, mode
    elif mode == 'qoq':
        d2 = quarter(today) - timedelta(days=1)
        d1 = quarter(d2)
        p2 = d1 - timedelta(days=1)
        return d1, d2, quarter(p2), p2, mode
    elif mode == 'yoy':
        d2 = today - timedelta(days=1)
        d2 -= timedelta(days=(d2.weekday() + 1) % 7)            # последнее воскресенье
        d1 = d2 - timedelta(days=7 * max(1, -(-a.days // 7)) - 1)
        return d1, d2, d1 - YEAR, d2 - YEAR, mode
    else:
        d1, d2 = period(a)
    p2 = d1 - timedelta(days=1)
    return d1, d2, p2 - (d2 - d1), p2, 'prev'


# Порядок целей в выборе главной: заявки, покупки, звонки — первыми, вовлечённость — последней
GOAL_FIRST = ['e_purchase', 'a_create_order', 'payment_system', 'form', 'contact_data_sent', 'contact_data',
              'conditional_call', 'call', 'phone', 'messenger', 'email', 'a_begin_checkout', 'e_cart', 'button',
              'action', 'file', 'social', 'search', 'site', 'offline', 'step', 'url', 'number', 'visit_duration']


def goal_choices(cid):
    """Цели счётчика для выбора главной: [(номер, название, тип по-русски)]."""
    goals = api_get(f'/management/v1/counter/{cid}/goals').get('goals') or []
    rank = {t: i for i, t in enumerate(GOAL_FIRST)}
    goals.sort(key=lambda g: (rank.get(g.get('type'), len(rank) - 3), (g.get('name') or '').lower()))
    return [(g['id'], g.get('name') or str(g['id']), GOAL_TYPES.get(g.get('type'), g.get('type') or ''))
            for g in goals]


def set_focus_goal(cid, arg, required=False):
    """--goal: номер цели или часть названия. Возвращает (номер, название) или None.

    required — для SEO и Директа: без главной цели конверсию и цену цели «делают» цели
    вовлечённости, и цифры вводят в заблуждение. Целей в счётчике нет — отчёт и без неё."""
    global FOCUS_GOAL
    if not arg:
        if required:
            choices = goal_choices(cid)
            if choices:
                lines = '\n'.join(f'  {gid:>12}  {name} ({kind})' for gid, name, kind in choices[:60])
                sys.exit('Выберите главную цель — по ней считаются конверсия и цена цели. Без неё цели\n'
                         'вовлечённости («3 страницы», «время на сайте») искажают данные. Добавьте к команде\n'
                         f'--goal <номер> (или часть названия). Цели счётчика, заявки и покупки — сверху:\n\n{lines}')
        return None
    goals = api_get(f'/management/v1/counter/{cid}/goals').get('goals') or []
    hit = ([g for g in goals if str(g['id']) == str(arg)]
           or [g for g in goals if str(arg).lower() in (g.get('name') or '').lower()])
    if len(hit) != 1:
        names = '\n'.join(f'  {g["id"]}  {g.get("name")}' for g in (hit or goals)[:40])
        sys.exit(f'Цель «{arg}» ' + ('не найдена' if not hit else 'подходит не одна') +
                 f'. Укажите номер:\n{names}')
    FOCUS_GOAL = (hit[0]['id'], hit[0].get('name') or str(hit[0]['id']))
    charts.RELABEL.update({'Достижения целей': 'Достижения цели', 'Конверсия в любую цель, %': 'Конверсия в цель, %'})
    log(f'   главная цель: «{FOCUS_GOAL[1]}» (№ {FOCUS_GOAL[0]})')
    return FOCUS_GOAL


def goal_note():
    return f' · главная цель: «{FOCUS_GOAL[1]}»' if FOCUS_GOAL else ''


def goal_options(*sources):
    """Цели для выбора на странице отчёта — из первого непустого списка, частые сверху."""
    for src in sources:
        if src and src.get('goals'):
            return [(gid, name) for gid, name, _ in sorted(src['goals'], key=lambda g: -(g[2] or 0))]
    return []


def build_pages(path, ctx, pics):
    """Страница отчёта и её PDF — если есть Chrome или Edge, они печатают страницу в PDF."""
    ctx['pdf_name'] = path.with_suffix('.pdf').name if charts.find_browser() else None
    page = charts.html_dashboard(path, ctx, pics)
    if ctx['pdf_name']:
        pdf = charts.html_to_pdf(page)
        log(f'✅ PDF: {pdf}' if pdf else '   PDF не собран: браузер не напечатал страницу')
    else:
        log('   PDF не собран: для него нужен Chrome или Edge')
    return page


def humans_note():
    return ' · только люди, без роботов' if HUMANS_ONLY else ' · вместе с роботами'


def pick_group(a, d1, d2):
    if getattr(a, 'group', None):
        return a.group
    days = (d2 - d1).days + 1
    return 'day' if days <= 62 else ('week' if days <= 370 else 'month')


def slug(s):
    return re.sub(r'[^\w.-]+', '_', s or '').strip('_')[:60] or 'counter'


def cmd_counters(a):
    counters = all_counters()
    log(f'Доступно счётчиков: {len(counters)}\n')
    log(f'{"Номер":>10}  {"Доступ":<14} Сайт')
    for c in sorted(counters, key=lambda c: (c.get('site') or '')):
        log(f'{c["id"]:>10}  {PERMISSION.get(c.get("permission"), c.get("permission", "")):<14} '
            f'{c.get("site", "")}  {("— " + c["name"]) if c.get("name") else ""}')
    DATA.mkdir(exist_ok=True)
    wb = Workbook()
    ws = wb.active
    ws.title = 'Счётчики'
    ws.append(['Номер', 'Сайт', 'Название', 'Доступ', 'Владелец', 'Статус', 'Создан'])
    for c in ws[1]:
        c.font, c.fill = HEAD_FONT, HEAD_FILL
    for c in counters:
        ws.append([c['id'], c.get('site'), c.get('name'), PERMISSION.get(c.get('permission'), c.get('permission')),
                   c.get('owner_login'), c.get('status'), (c.get('create_time') or '')[:10]])
    for col, w in zip('ABCDEFG', (12, 36, 36, 14, 22, 10, 12)):
        ws.column_dimensions[col].width = w
    ws.freeze_panes = 'A2'
    out = DATA / 'counters.xlsx'
    wb.save(out)
    log(f'\n✅ список сохранён: {out}')


def collect_traffic(wb, cid, d1, d2, p1, p2, group, only, top, no_compare):
    """Листы обычного отчёта — весь трафик сайта — в книгу wb. Им пользуются и
    отчёт report, и SEO-отчёт (раздел «Весь трафик»). Нет итогов — QueryError."""
    status = []      # (лист, строк, всего строк, выборка, ошибка)
    tables = {}      # данные листов — для панели с графиками
    goal_list = []   # (цель, достижения)
    goal_src = {}    # цели × источники — для тепловой карты
    mtot = M_SESSION + M_GOALS
    _, cur, s_cur, _ = stat(cid, [m[0] for m in mtot], [], d1.isoformat(), d2.isoformat(), top=1)
    prev = [None] * len(mtot)
    if not no_compare:
        _, prev, _, _ = stat(cid, [m[0] for m in mtot], [], p1.isoformat(), p2.isoformat(), top=1)

    for key in only:
        if key == 'dynamics':
            dim, head = GROUPS[group]
            title, dims, metrics = SPECIAL[key], [(dim, head)], M_SESSION + M_GOALS
            sort = dim
        elif key == 'goals':
            title = SPECIAL[key]
            status.append(goals_sheet(wb.create_sheet(title), cid, d1, d2, goal_list))
            continue
        elif key == 'goal_sources':
            title = SPECIAL[key]
            status.append(goal_sources_sheet(wb.create_sheet(title), cid, d1, d2, goal_list, goal_src))
            continue
        else:
            title, dims, metrics, _ = REPORTS[key]
            sort = '-' + metrics[0][0]
        log(f'   {title}…')
        ws = wb.create_sheet(title)
        try:
            rows, totals, sampled, total_rows = stat(cid, [m[0] for m in metrics], [d for d, _ in dims],
                                                     d1.isoformat(), d2.isoformat(), top=top, sort=sort)
        except QueryError as e:
            note_sheet(ws, f'Не получено: {e}')
            status.append((title, 0, 0, None, str(e)))
            log(f'   ⚠ {title}: {e}')
            continue
        write_table(ws, [h for _, h in dims], metrics, rows, totals)
        status.append((title, len(rows), total_rows, sampled, None))
        tables[key] = ([h for _, h in dims], metrics, rows, totals)
    return {'status': status, 'tables': tables, 'goal_list': goal_list, 'goal_src': goal_src,
            'mtot': mtot, 'cur': cur, 'prev': prev, 'sampled': s_cur}


def cmd_report(a):
    counter = resolve_counter(a.counter)
    cid, site = counter['id'], counter.get('site') or counter.get('name') or ''
    set_focus_goal(cid, getattr(a, 'goal', None), required=a.cmd in ('seo', 'direct'))
    d1, d2, p1, p2, mode = pick_periods(a)
    days = (d2 - d1).days + 1
    only = [k.strip() for k in a.only.split(',')] if a.only else ORDER
    bad = [k for k in only if k not in REPORTS and k not in SPECIAL]
    if bad:
        sys.exit(f'Неизвестные отчёты: {", ".join(bad)}. Доступны: {", ".join(ORDER)}')
    group = pick_group(a, d1, d2)
    log(f'   счётчик {cid} · {site} · {d1:%d.%m.%Y}–{d2:%d.%m.%Y} ({days} дн.) · '
        f'{COMPARE[mode][1]}: {p1:%d.%m.%Y}–{p2:%d.%m.%Y}{humans_note()}')

    want_charts = not a.no_charts
    if want_charts and not charts.available():
        log('   панель с графиками пропущена: нет seaborn — python -m pip install -r requirements.txt')
        want_charts = False

    wb = Workbook()
    summary = wb.active
    summary.title = 'Сводка'
    try:
        t = collect_traffic(wb, cid, d1, d2, p1, p2, group, only, a.top, a.no_compare)
    except QueryError as e:
        sys.exit(f'Не удалось получить итоги: {e}')
    status = t['status']
    write_summary(summary, counter, d1, d2, p1, p2, t['mtot'], t['cur'], t['prev'], t['sampled'], status,
                  a.no_compare, group, prev_name=COMPARE[mode][1])
    folder = DATA / f'{cid}_{slug(site)}'
    folder.mkdir(parents=True, exist_ok=True)
    stem = f'{slug(site)}_{d1.isoformat()}_{d2.isoformat()}'
    out = folder / f'metrika_{stem}.xlsx'

    dashboard = None
    if want_charts:
        # Панель — дополнение: если она не соберётся, Excel всё равно сохранится
        try:
            log('   панель с графиками…')
            ctx = panel_context(counter, site, d1, d2, p1, p2, group, t['mtot'], t['cur'], t['prev'], t['tables'],
                                t['goal_list'], status, out.name, cid, a.no_compare, t['goal_src'], mode)
            pics = charts.build(ctx, folder / f'charts_{d1.isoformat()}_{d2.isoformat()}')
            charts.excel_panel(wb, ctx, pics)
            dashboard = build_pages(folder / f'dashboard_{stem}.html', ctx, pics)
        except Exception as e:  # noqa: BLE001
            log(f'   ⚠ панель с графиками не собрана: {type(e).__name__}: {e}')
            if 'Панель' in wb.sheetnames:
                del wb['Панель']
    wb.save(out)
    got = sum(1 for s in status if not s[4])
    log(f'✅ листов: {got + 1} из {len(status) + 1}' + (f', не получено: {len(status) - got}' if got < len(status) else '')
        + (' + Панель' if dashboard else ''))
    log(f'✅ файл: {out}')
    if dashboard:
        log(f'✅ панель в браузере: {dashboard}')


def shown(v, kind):
    """Значение для плитки панели: 36 720 / 31,5% / 1,40 / 0:50."""
    if v is None:
        return '—'
    if kind == 'int':
        return f'{v:,.0f}'.replace(',', ' ')
    if kind == 'pct':
        return f'{v:.1f}%'.replace('.', ',')
    if kind == 'dur':
        s = int(round(v))
        return f'{s // 60}:{s % 60:02d}'
    if kind == 'money':
        return (f'{v:,.0f}'.replace(',', ' ') if v >= 100 else f'{v:.2f}'.replace('.', ',')) + ' ₽'
    return f'{v:.2f}'.replace('.', ',')


def partial_tail(values, group):
    """1, если последняя точка динамики подозрительно мала — меньше трети медианы.

    Так выглядит день, который Метрика ещё не досчитала (бывает при сбоях и
    в первые часы после полуночи). Для недель и месяцев не проверяем: крайние
    неполные по календарю, это нормально."""
    if group != 'day' or len(values) < 8:
        return 0
    rest = sorted(values[:-1])
    median = rest[len(rest) // 2]
    return 1 if median and values[-1] < median / 3 else 0


def panel_context(counter, site, d1, d2, p1, p2, group, mtot, cur, prev, tables, goal_list, status,
                  xlsx_name, cid, no_compare, goal_src=None, mode='prev'):
    """Всё, что нужно панели: плитки, динамика текущего и прошлого периода, таблицы."""
    kpis = make_kpis(mtot, cur, prev)
    dynamics = None
    if 'dynamics' in tables:
        _, _, rows, _ = tables['dynamics']
        dates = [datetime.strptime(r[0][0], '%Y-%m-%d').date() for r in rows]
        dynamics = {'dates': dates, 'cur': [r[1][0] or 0 for r in rows], 'prev': None,
                    'label': {'day': 'дням', 'week': 'неделям', 'month': 'месяцам'}[group]}
        dynamics['partial_tail'] = partial_tail(dynamics['cur'], group)
        if not no_compare:
            dim = GROUPS[group][0]
            try:
                prev_rows, _, _, _ = stat(cid, ['ym:s:visits'], [dim], p1.isoformat(), p2.isoformat(), sort=dim)
                dynamics['prev'] = [r[1][0] or 0 for r in prev_rows]
            except QueryError as e:
                log(f'   прошлый период для графика динамики не получен: {e}')
    sampled = [s[0] for s in status if s[3]]
    warnings = []
    if dynamics and dynamics.get('partial_tail'):
        last = dynamics['dates'][-1]
        warnings.append(f'Данные за {last:%d.%m.%Y}, похоже, неполные: визитов в разы меньше обычного. '
                        f'Метрика могла ещё не обработать этот день — перевыгрузите отчёт позже.')
        log(f'   ⚠ {warnings[-1]}')
    return {
        'warnings': warnings,
        'site': site, 'kpis': kpis, 'tables': tables, 'dynamics': dynamics,
        'goals': [(g.get('name'), reach) for g, reach in goal_list],
        'goal_sources': goal_src or {},
        'periods': [('Период', d1, d2)] + ([] if no_compare else [(COMPARE[mode][0], p1, p2)]),
        'meta': f'счётчик {cid}{humans_note()}{goal_note()}', 'vs': COMPARE[mode][2],
        'prev_label': COMPARE[mode][1], 'cur_label': 'этот год' if mode == 'yoy' else 'текущий период',
        'period_short': f'{d1:%d.%m}–{d2:%d.%m.%Y}',
        'exported': datetime.now().strftime('%d.%m.%Y %H:%M'),
        'xlsx_name': xlsx_name,
        'sampled_note': ('По выборке: ' + ', '.join(sampled) + '.') if sampled else '',
    }


def goals_sheet(ws, cid, d1, d2, collect=None):
    title = ws.title
    log(f'   {title}…')
    try:
        goals = api_get(f'/management/v1/counter/{cid}/goals').get('goals') or []
    except QueryError as e:
        note_sheet(ws, f'Не получено: {e}')
        return (title, 0, 0, None, str(e))
    if not goals:
        note_sheet(ws, 'Целей в счётчике нет.')
        return (title, 0, 0, None, None)
    results, sampled = [], None
    for i in range(0, len(goals), GOALS_PER_REQUEST):
        chunk = goals[i:i + GOALS_PER_REQUEST]
        metrics = [f'ym:s:goal{g["id"]}{m}' for g in chunk for m in ('reaches', 'conversionRate')]
        try:
            _, totals, s, _ = stat(cid, metrics, [], d1.isoformat(), d2.isoformat(), top=1)
        except QueryError as e:
            note_sheet(ws, f'Не получено: {e}')
            return (title, 0, 0, None, str(e))
        sampled = sampled or s
        for j, g in enumerate(chunk):
            results.append((g, totals[2 * j] if totals else None, totals[2 * j + 1] if totals else None))
    results.sort(key=lambda x: -(x[1] or 0))
    if collect is not None:
        collect.extend((g, reach) for g, reach, _ in results)
    heads = ['Номер цели', 'Цель', 'Тип', 'Достижения', 'Конверсия, %']
    ws.append(heads)
    for c in ws[1]:
        c.font, c.fill = HEAD_FONT, HEAD_FILL
        c.alignment = Alignment(horizontal='center', wrap_text=True)
    for g, reach, conv in results:
        ws.append([g['id'], g.get('name'), GOAL_TYPES.get(g.get('type'), g.get('type')),
                   cell_value(reach, 'int'), cell_value(conv, 'pct')])
    for row in ws.iter_rows(min_row=2, min_col=4, max_col=5):
        row[0].number_format, row[1].number_format = FORMATS['int'], FORMATS['pct']
    for col, w in zip('ABCDE', (14, 50, 16, 14, 14)):
        ws.column_dimensions[col].width = w
    ws.freeze_panes = 'A2'
    ws.auto_filter.ref = f'A1:E{ws.max_row}'
    return (title, len(results), len(results), sampled, None)


def goal_sources_sheet(ws, cid, d1, d2, known=None, collect=None, dim='ym:s:lastsignTrafficSource',
                       head='Источник', extra=None, cost=None):
    """Каждая цель по источникам трафика: сколько достижений дал источник, его
    доля в цели и конверсия визитов из него в эту цель. Длинная таблица, а не
    матрица: фильтр по колонке «Цель» показывает источники одной цели.

    known — цели, уже полученные для листа «Цели» (иначе запрашиваются заново);
    collect — сюда кладутся данные для тепловой карты на панели. Та же функция
    строит «Цели по кампаниям» Директа: dim/head — кампания, extra — фильтр
    визитов из Директа, cost — расход по кампаниям для колонки «Цена цели»."""
    title = ws.title
    log(f'   {title}…')
    try:
        goals = ([g for g, _ in known] if known else
                 api_get(f'/management/v1/counter/{cid}/goals').get('goals') or [])
    except QueryError as e:
        note_sheet(ws, f'Не получено: {e}')
        return (title, 0, 0, None, str(e))
    if not goals:
        note_sheet(ws, 'Целей в счётчике нет.')
        return (title, 0, 0, None, None)
    visits, cells, total, sampled = {}, {}, {}, None   # источник → визиты; (цель, источник) → (достижения, конверсия)
    for i in range(0, len(goals), GOALS_BY_SOURCE):
        chunk = goals[i:i + GOALS_BY_SOURCE]
        metrics = ['ym:s:visits'] + [f'ym:s:goal{g["id"]}{m}' for g in chunk for m in ('reaches', 'conversionRate')]
        try:
            rows, totals, s, _ = stat(cid, metrics, [dim], d1.isoformat(), d2.isoformat(),
                                      top=1000, sort='-ym:s:visits', extra=extra)
        except QueryError as e:
            note_sheet(ws, f'Не получено: {e}')
            return (title, 0, 0, None, str(e))
        sampled = sampled or s
        for j, g in enumerate(chunk):
            total[g['id']] = (totals[1 + 2 * j] if totals else None) or 0
        for dims, vals in rows:
            src = dims[0] or '(не определено)'
            visits[src] = vals[0]
            for j, g in enumerate(chunk):
                cells[(g['id'], src)] = (vals[1 + 2 * j], vals[2 + 2 * j])
    goals.sort(key=lambda g: -total[g['id']])
    sources = sorted(visits, key=lambda x: -(visits[x] or 0))

    heads = ['Цель', head, 'Достижения', 'Доля в достижениях цели, %', 'Конверсия, %',
             {'Источник': 'Визиты источника', 'Кампания': 'Визиты кампании'}.get(head, 'Визиты'), 'Номер цели']
    if cost:
        heads.append('Цена цели, ₽')
    ws.append(heads)
    for c in ws[1]:
        c.font, c.fill = HEAD_FONT, HEAD_FILL
        c.alignment = Alignment(horizontal='center', vertical='center', wrap_text=True)
    ws.row_dimensions[1].height = 32
    band = PatternFill('solid', fgColor='F5F6F8')      # каждая вторая цель — с подложкой
    for n, g in enumerate(goals):
        for src in sorted(sources, key=lambda x: (-((cells.get((g['id'], x)) or (0, 0))[0] or 0), -(visits[x] or 0))):
            reach, conv = cells.get((g['id'], src)) or (None, None)
            share = reach / total[g['id']] * 100 if reach is not None and total[g['id']] else None
            line = [g.get('name'), src, cell_value(reach, 'int'), cell_value(share, 'pct'),
                    cell_value(conv, 'pct'), cell_value(visits[src], 'int'), g['id']]
            if cost:
                line.append(cell_value(cost[src] / reach, 'money') if reach and cost.get(src) else None)
            ws.append(line)
            if n % 2:
                for c in ws[ws.max_row]:
                    c.fill = band
    for row in ws.iter_rows(min_row=2, min_col=3, max_col=8):
        for c, kind in zip(row, ('int', 'pct', 'pct', 'int', 'id', 'money')):
            c.number_format = FORMATS.get(kind, 'General')
    for col, w in zip('ABCDEFGH', (44, 34, 13, 16, 13, 14, 12, 14)):
        ws.column_dimensions[col].width = w
    ws.freeze_panes = 'C2'
    ws.auto_filter.ref = f'A1:{get_column_letter(len(heads))}{ws.max_row}'
    if collect is not None:
        collect.update(goals=[(g['id'], g.get('name'), total[g['id']]) for g in goals],
                       sources=[(x, visits[x]) for x in sources], cells=cells)
    return (title, ws.max_row - 1, ws.max_row - 1, sampled, None)


def traffic_about(group):
    """Что на каждом листе обычного отчёта — для списка листов в Сводке."""
    about = {v[0]: v[3] for v in REPORTS.values()}
    about.update({'Динамика': f'визиты и поведение по {"дням" if group == "day" else "неделям" if group == "week" else "месяцам"}',
                  'Цели': 'достижения и конверсия каждой цели',
                  'Цели по источникам': 'каждая цель по источникам трафика: достижения, доля, конверсия'})
    return about


def write_summary(ws, counter, d1, d2, p1, p2, metrics, cur, prev, sampled, status, no_compare, group,
                  title=None, about=None, prev_name='прошлый период'):
    ws.column_dimensions['A'].width = 34
    for col in 'BCD':
        ws.column_dimensions[col].width = 18
    ws.column_dimensions['E'].width = 60
    big = Font(bold=True, size=14)
    ws['A1'] = title or f'Яндекс Метрика: {counter.get("site") or counter.get("name")}'
    ws['A1'].font = big
    ws['A2'] = f'Счётчик {counter["id"]}' + (f' · {counter["name"]}' if counter.get('name') else '')
    ws['A3'] = (f'Период: {d1:%d.%m.%Y}–{d2:%d.%m.%Y}'
                + ('' if no_compare else f' · {prev_name}: {p1:%d.%m.%Y}–{p2:%d.%m.%Y}'))
    ws['A4'] = f'Выгружено {datetime.now():%d.%m.%Y %H:%M} через API Метрики' + humans_note() + (
        f' · итоги по выборке {sampled:.0%}' if sampled else '')
    ws.append([])
    ws.append(['Показатель', 'Период', prev_name.capitalize(), 'Изменение'])
    hr = ws.max_row
    for c in ws[hr][:4]:
        c.font, c.fill = HEAD_FONT, HEAD_FILL
    for (mid, label, kind), v, pv in zip(metrics, cur or [], prev or []):
        # Доли в процентах сравниваются разницей в пунктах: отказы 20 % → 22 % —
        # это +2 п.п., а не «+10 %»
        change, fmt = None, '+0.0"%";-0.0"%";0"%"'
        if kind == 'pct':
            fmt = '+0.0" п.п.";-0.0" п.п.";0" п.п."'
            if v is not None and pv is not None:
                change = round(v - pv, 1)
        elif v is not None and pv:
            change = round((v - pv) / pv * 100, 1)
        ws.append([charts.lbl(label), cell_value(v, kind), cell_value(pv, kind), change])
        r = ws.max_row
        ws.cell(r, 2).number_format = ws.cell(r, 3).number_format = FORMATS[kind]
        ws.cell(r, 4).number_format = fmt
    ws.append([])
    ws.append(['Лист', 'Строк в файле', 'Всего в Метрике', 'Данные', 'Что это'])
    hr = ws.max_row
    for c in ws[hr][:5]:
        c.font, c.fill = HEAD_FONT, HEAD_FILL
    if about is None:
        about = traffic_about(group)
    for title, n, total, s, err in status:
        state = f'не получено: {err}' if err else (f'выборка {s:.0%}' if s else 'полные')
        ws.append([title, n, total, state, about.get(title, '')])
        if err:
            ws.cell(ws.max_row, 4).font = Font(color='C0392B')
    ws.freeze_panes = 'A6'


def cmd_raw(a):
    counter = resolve_counter(a.counter)
    d1, d2 = period(a)
    metrics = [m.strip() for m in a.metrics.split(',') if m.strip()]
    dims = [d.strip() for d in (a.dimensions or '').split(',') if d.strip()]
    log(f'   счётчик {counter["id"]} · {d1:%d.%m.%Y}–{d2:%d.%m.%Y}')
    try:
        rows, totals, sampled, total_rows = stat(counter['id'], metrics, dims, d1.isoformat(), d2.isoformat(),
                                                 top=a.top, sort=a.sort)
    except QueryError as e:
        sys.exit(f'Не получено: {e}')
    wb = Workbook()
    ws = wb.active
    ws.title = 'Отчёт'
    write_table(ws, dims, [(m, m, 'float') for m in metrics], rows, totals)
    folder = DATA / f'{counter["id"]}_{slug(counter.get("site"))}'
    folder.mkdir(parents=True, exist_ok=True)
    out = folder / f'raw_{datetime.now():%Y-%m-%d_%H%M%S}.xlsx'
    wb.save(out)
    log(f'✅ строк: {len(rows)} из {total_rows}' + (f' · выборка {sampled:.0%}' if sampled else ''))
    log(f'✅ файл: {out}')


# ─── отчёт по Яндекс Директу ────────────────────────────────────────────────
# Визиты из Директа — те, у которых последний значимый переход — реклама
# Директа. «Яндекс Директ: не определено» — тоже Директ, только кампанию
# Метрика не распознала (потерялась метка yclid, кампания удалена и т. п.)
DIRECT_FILTER = "ym:s:lastsignAdvEngine=='ya_direct' OR ym:s:lastsignAdvEngine=='ya_undefined'"
M_DIRECT = M_SHORT + M_GOALS
# Расходы — из пространства ym:ad. Нужен логин аккаунта Директа, к которому у
# владельца токена есть доступ (параметр direct_client_logins); валюта — рубли
AD_CLICKS, AD_COST = 'ym:ad:clicks', 'ym:ad:RUBConvertedAdCost'
M_COST = [('cost:clicks', 'Клики', 'int'), ('cost:cost', 'Расход, ₽', 'money'),
          ('cost:cpc', 'Цена клика, ₽', 'money'), ('cost:cpa', 'Цена цели, ₽', 'money')]
# ключ: (лист, группировка визитов, заголовок, группировка расходов или None, что это)
DIRECT_REPORTS = {
    'campaigns':  ('Кампании', 'ym:s:lastsignDirectClickOrder', 'Кампания', 'ym:ad:lastsignDirectOrder',
                   'кампании: визиты, поведение, цели, расходы'),
    'placetype':  ('Поиск и сети', 'ym:s:lastsignDirectPlatformType', 'Тип площадки',
                   'ym:ad:lastsignDirectPlatformType', 'поиск Яндекса или Рекламная сеть'),
    'ads':        ('Объявления', 'ym:s:lastsignDirectClickBanner', 'Объявление', 'ym:ad:lastsignDirectBanner',
                   'объявления'),
    'conditions': ('Ключевые фразы', 'ym:s:lastsignDirectPhraseOrCond', 'Ключевая фраза или условие',
                   'ym:ad:lastsignDirectPhraseOrCond', 'ключевые фразы и условия ретаргетинга, по которым показано объявление'),
    'queries':    ('Поисковые запросы', 'ym:s:lastsignDirectSearchPhrase', 'Поисковый запрос',
                   'ym:ad:lastsignDirectSearchPhrase', 'что человек на самом деле искал перед кликом'),
    'platforms':  ('Площадки', 'ym:s:lastsignDirectPlatform', 'Площадка', 'ym:ad:lastsignDirectPlatform',
                   'где показано объявление: поиск, сайты и приложения Рекламной сети'),
    'entry':      ('Страницы входа', 'ym:s:startURL', 'Страница входа', None, 'куда приводит реклама'),
    'devices':    ('Устройства', 'ym:s:deviceCategory', 'Устройство', None, 'ПК, смартфоны, планшеты'),
    'cities':     ('Города', 'ym:s:regionCity', 'Город', 'ym:ad:regionCity', 'города'),
    # Метки и аудитория — обычные группировки Метрики: работают, даже когда справочники
    # Директа недоступны (после сбоя 12.10.2026 кампании отвечали 503, а UTM — нет)
    'utm_campaign': ('Кампании по UTM', 'ym:s:lastsignUTMCampaign', 'utm_campaign', None,
                     'кампании по метке utm_campaign: имя или номер кампании, как его подставляет Директ'),
    'utm_content': ('Объявления по UTM', 'ym:s:lastsignUTMContent', 'utm_content', None,
                    'объявления по метке utm_content'),
    'utm_term':   ('Фразы по UTM', 'ym:s:lastsignUTMTerm', 'utm_term', None,
                   'ключевые фразы по метке utm_term; «---autotargeting» — автотаргетинг'),
    # у часов, дней, пола, возраста и городов расходы есть и в ym:ad — деньги по сегментам;
    # у устройств, новых/вернувшихся, UTM и страниц входа API расходов не отдаёт (ошибка 4001)
    'hours':      ('Часы', 'ym:s:hour', 'Час', 'ym:ad:hour', 'по часам суток — для корректировок ставок по времени'),
    'weekdays':   ('Дни недели', 'ym:s:dayOfWeek', 'День недели', 'ym:ad:dayOfWeek', 'по дням недели'),
    'gender':     ('Пол', 'ym:s:gender', 'Пол', 'ym:ad:gender', 'мужчины и женщины'),
    'age':        ('Возраст', 'ym:s:ageInterval', 'Возраст', 'ym:ad:ageInterval', 'возрастные группы'),
    'newold':     ('Новые и вернувшиеся', 'ym:s:isNewUser', 'Посетители', None,
                   'впервые на сайте или уже были'),
}
DIRECT_SPECIAL = {'dynamics': 'Динамика', 'goals': 'Цели по кампаниям', 'goals_utm': 'Цели по кампаниям UTM',
                  'goal_cost': 'Цена целей', 'autotarget': 'Автотаргетинг и фразы'}
DIRECT_ORDER = ['dynamics', 'campaigns', 'utm_campaign', 'placetype', 'ads', 'utm_content', 'conditions',
                'queries', 'utm_term', 'autotarget', 'platforms', 'goals', 'goals_utm', 'goal_cost', 'hours', 'weekdays',
                'gender', 'age', 'newold', 'entry', 'devices', 'cities']
DIRECT_BY_DIM = {'hours', 'weekdays', 'age'}     # по порядку группировки, а не по числу визитов
WITH_ID = {'Кампания', 'Объявление'}   # рядом с названием — номер из Директа


def autotarget_rows(rows, reach_i, total_visits):
    """Строки utm_term → автотаргетинг, ключевые фразы и визиты без метки. Средние
    (отказы, глубина, время, конверсия) пересчитываются с весом по визитам."""
    groups = {'Автотаргетинг': [], 'Ключевые фразы': []}
    for dims, vals in rows:
        term = str(dims[0][0] if isinstance(dims[0], tuple) else dims[0] or '')
        if term:
            groups['Автотаргетинг' if 'autotarget' in term.lower() else 'Ключевые фразы'].append(vals)
    out, tagged = [], 0
    for name, items in groups.items():
        v = sum(x[0] or 0 for x in items)
        if not v:
            continue
        tagged += v
        line = []
        for i, (mid, _, kind) in enumerate(M_DIRECT):
            if kind == 'int':
                line.append(sum(x[i] or 0 for x in items))
            else:
                line.append(sum((x[i] or 0) * (x[0] or 0) for x in items) / v)
        out.append(([name], line))
    if total_visits and total_visits > tagged:
        out.append((['Без метки utm_term'], [total_visits - tagged] + [None] * (len(M_DIRECT) - 1)))
    return out


def direct_logins(cid, given):
    """Логины Директа для расходов: заданные в --login или все аккаунты, которые
    Метрика связала со счётчиком и к которым у владельца токена есть доступ."""
    if given:
        return [x.strip() for x in given.split(',') if x.strip()]
    try:
        j = api_get('/management/v1/clients', {'counters': cid})
    except QueryError:
        return []
    return [c['chief_login'] for c in j.get('clients') or [] if c.get('chief_login')]


def ad_costs(cid, dim, d1, d2, logins, top=TOP):
    """Клики и расход Директа: {('id'|'name', ключ): (клики, расход)} и итоги.
    Нет доступа к аккаунту — QueryError."""
    rows, totals, _, _ = stat(cid, [AD_CLICKS, AD_COST], [dim] if dim else [], d1, d2, top=top,
                              sort=f'-{AD_COST}' if dim else None,
                              extra={'direct_client_logins': ','.join(logins)}, ids=True)
    out = {}
    for dims, vals in rows:
        if not dims:                     # без группировки API отдаёт одну строку-итог
            continue
        name, did = dims[0]
        pair = (vals[0], vals[1])
        if did is not None:
            out[('id', str(did))] = pair
        out.setdefault(('name', name), pair)
    return out, totals


def cost_values(clicks, cost, reaches):
    """Клики, расход, цена клика, цена цели — для строки или итога."""
    if clicks is None and cost is None:
        return [None] * len(M_COST)
    return [clicks, cost, cost / clicks if clicks and cost is not None else None,
            cost / reaches if reaches and cost is not None else None]


def bucket(day, group):
    """Дата дня → начало недели или месяца: расходы приходят по дням."""
    d = datetime.strptime(day, '%Y-%m-%d').date()
    if group == 'week':
        d -= timedelta(days=d.weekday())
    elif group == 'month':
        d = d.replace(day=1)
    return d.isoformat()


def make_kpis(mtot, cur, prev, order=None):
    """Плитки панели: (подпись, значение, как показать, изменение, единица)."""
    kpis = []
    for (mid, label, kind), v, pv in zip(mtot, cur or [], prev or []):
        if kind == 'pct':
            ch, unit = (round(v - pv, 1) if v is not None and pv is not None else None), 'pp'
        else:
            ch, unit = (round((v - pv) / pv * 100, 1) if v is not None and pv else None), 'pct'
        kpis.append((label, v, shown(v, kind), ch, unit))
    if order:
        rank = {label: i for i, label in enumerate(order)}
        kpis = sorted((k for k in kpis if k[0] in rank), key=lambda k: rank[k[0]])
    return kpis


def cmd_direct(a):
    counter = resolve_counter(a.counter)
    cid, site = counter['id'], counter.get('site') or counter.get('name') or ''
    set_focus_goal(cid, getattr(a, 'goal', None), required=a.cmd in ('seo', 'direct'))
    d1, d2, p1, p2, mode = pick_periods(a)
    days = (d2 - d1).days + 1
    only = [k.strip() for k in a.only.split(',')] if a.only else DIRECT_ORDER
    bad = [k for k in only if k not in DIRECT_REPORTS and k not in DIRECT_SPECIAL]
    if bad:
        sys.exit(f'Неизвестные листы: {", ".join(bad)}. Доступны: {", ".join(DIRECT_ORDER)}')
    group = pick_group(a, d1, d2)
    flt = {'filters': DIRECT_FILTER}
    log(f'   Директ · счётчик {cid} · {site} · {d1:%d.%m.%Y}–{d2:%d.%m.%Y} ({days} дн.) · '
        f'{COMPARE[mode][1]}: {p1:%d.%m.%Y}–{p2:%d.%m.%Y}{humans_note()}')

    logins = direct_logins(cid, a.login)
    cost_note = '' if logins else (
        'Расходов в отчёте нет: у владельца токена нет доступа к аккаунту Директа этого счётчика. '
        'Как открыть доступ — README, раздел «Отчёт по Яндекс Директу».')
    log(f'   расходы: {len(logins)} аккаунт(а) Директа' if logins else f'   ⚠ {cost_note}')

    want_charts = not a.no_charts
    if want_charts and not charts.available():
        log('   панель с графиками пропущена: нет seaborn — python -m pip install -r requirements.txt')
        want_charts = False

    wb = Workbook()
    summary = wb.active
    summary.title = 'Сводка'
    status, tables, goal_src, camp_cost, goal_src_utm = [], {}, {}, {}, {}
    reach_i = [m[0] for m in M_DIRECT].index('ym:s:sumGoalReachesAny')

    mtot = list(M_DIRECT)
    try:
        _, cur, s_cur, _ = stat(cid, [m[0] for m in mtot], [], d1.isoformat(), d2.isoformat(), top=1, extra=flt)
        prev = [None] * len(mtot)
        if not a.no_compare:
            _, prev, _, _ = stat(cid, [m[0] for m in mtot], [], p1.isoformat(), p2.isoformat(), top=1, extra=flt)
    except QueryError as e:
        sys.exit(f'Не удалось получить итоги: {e}')
    cur, prev = list(cur or [None] * len(mtot)), list(prev or [None] * len(mtot))
    if not cur[0]:
        log('   ⚠ визитов из Директа за этот период нет')
    if logins:
        try:
            _, ct = ad_costs(cid, None, d1.isoformat(), d2.isoformat(), logins)
            cp = [None, None]
            if not a.no_compare:
                _, cp = ad_costs(cid, None, p1.isoformat(), p2.isoformat(), logins)
            cur += cost_values(*(ct or [None, None])[:2], cur[reach_i])
            prev += cost_values(*(cp or [None, None])[:2], prev[reach_i])
            mtot += M_COST
        except QueryError as e:
            cost_note = f'Расходы не получены: {e}. Проверьте доступ к аккаунту Директа (README, «Отчёт по Яндекс Директу»).'
            log(f'   ⚠ {cost_note}')
            logins = []

    for key in only:
        if key == 'goals':
            status.append(goal_sources_sheet(
                wb.create_sheet(DIRECT_SPECIAL[key]), cid, d1, d2, None, goal_src,
                dim='ym:s:lastsignDirectClickOrder', head='Кампания', extra=flt, cost=camp_cost or None))
            continue
        if key == 'goals_utm':
            status.append(goal_sources_sheet(
                wb.create_sheet(DIRECT_SPECIAL[key]), cid, d1, d2, None, goal_src_utm,
                dim='ym:s:lastsignUTMCampaign', head='Кампания', extra=flt))
            continue
        if key == 'goal_cost':
            src = goal_src if goal_src.get('goals') else goal_src_utm
            spent = cur[len(M_DIRECT) + 1] if len(mtot) > len(M_DIRECT) else None
            if not (src.get('goals') and spent):
                continue                     # нет расходов или целей — листа нет
            title = DIRECT_SPECIAL[key]
            log(f'   {title}…')
            ws = wb.create_sheet(title)
            # достижения каждой цели — по всем визитам из Директа, а не из разбивки по кампаниям:
            # в разбивку не попадают визиты без метки, и цена цели вышла бы завышенной
            reach_of = {}
            ids = [gid for gid, _, _ in src['goals']]
            try:
                for i in range(0, len(ids), 2 * GOALS_PER_REQUEST):
                    part = ids[i:i + 2 * GOALS_PER_REQUEST]
                    _, tot, _, _ = stat(cid, [f'ym:s:goal{g}reaches' for g in part], [], d1.isoformat(),
                                        d2.isoformat(), top=1, extra=flt)
                    reach_of.update(zip(part, tot or []))
            except QueryError as e:
                log(f'   ⚠ {title}: достижения целей не получены ({e}) — беру из разбивки по кампаниям')
                reach_of = {gid: reach for gid, _, reach in src['goals']}
            rows = [(name, reach_of.get(gid), spent / reach_of[gid], gid) for gid, name, _ in src['goals']
                    if reach_of.get(gid)]
            ws.append(['Цель', 'Достижения', 'Цена цели, ₽', 'Номер цели'])
            for c in ws[1]:
                c.font, c.fill = HEAD_FONT, HEAD_FILL
                c.alignment = Alignment(horizontal='center', vertical='center', wrap_text=True)
            for name, reach, cpa, gid in rows:
                ws.append([name, cell_value(reach, 'int'), cell_value(cpa, 'money'), gid])
                ws.cell(ws.max_row, 2).number_format = FORMATS['int']
                ws.cell(ws.max_row, 3).number_format = FORMATS['money']
            for col, w in zip('ABCD', (50, 14, 14, 14)):
                ws.column_dimensions[col].width = w
            ws.freeze_panes = 'B2'
            ws.auto_filter.ref = f'A1:D{ws.max_row}'
            status.append((title, len(rows), len(rows), None, None))
            tables[key] = rows
            continue
        if key == 'autotarget':
            title = DIRECT_SPECIAL[key]
            log(f'   {title}…')
            ws = wb.create_sheet(title)
            try:
                rows = (tables['utm_term'][2] if 'utm_term' in tables else
                        stat(cid, [m[0] for m in M_DIRECT], ['ym:s:lastsignUTMTerm'], d1.isoformat(), d2.isoformat(),
                             top=a.top, sort='-ym:s:visits', extra=flt)[0])
            except QueryError as e:
                note_sheet(ws, f'Не получено: {e}')
                status.append((title, 0, 0, None, str(e)))
                continue
            out = autotarget_rows(rows, reach_i, cur[0])
            write_table(ws, ['Тип показа'], M_DIRECT, out, None)
            status.append((title, len(out), len(out), None, None))
            tables[key] = (['Тип показа'], M_DIRECT, out, None)
            continue
        if key == 'dynamics':
            dim, head = GROUPS[group]
            title, ad_dim, sort = DIRECT_SPECIAL[key], 'ym:ad:date', dim
        else:
            title, dim, head, ad_dim, _ = DIRECT_REPORTS[key]
            sort = dim if key in DIRECT_BY_DIM else '-ym:s:visits'
        log(f'   {title}…')
        ws = wb.create_sheet(title)
        try:
            rows, totals, sampled, total_rows = stat(cid, [m[0] for m in M_DIRECT], [dim], d1.isoformat(),
                                                     d2.isoformat(), top=a.top, sort=sort, extra=flt, ids=True)
        except QueryError as e:
            costs = None
            if logins and ad_dim:
                try:
                    costs, ct = ad_costs(cid, ad_dim, d1.isoformat(), d2.isoformat(), logins, top=max(a.top, 1000))
                except QueryError:
                    costs = None
            if not costs:
                note_sheet(ws, f'Не получено: {e}')
                status.append((title, 0, 0, None, str(e)))
                log(f'   ⚠ {title}: {e}')
                continue
            # только деньги: клики, расход, цена клика — визиты и цели Яндекс не отдал
            rows = list(([k], [cl, co, co / cl if cl else None]) for (kind, k), (cl, co) in costs.items()
                          if kind == 'name')
            rows.sort(key=lambda r: -(r[1][1] or 0))
            mets = M_COST[:3]
            tot = [ct[0], ct[1], ct[1] / ct[0] if ct and ct[0] else None] if ct else None
            write_table(ws, [head], mets, rows, tot)
            status.append((title, len(rows), len(rows), None, None))
            tables[key] = ([head], mets, rows, tot)
            log(f'   ⚠ {title}: визиты не получены ({e}) — на листе только клики и расход')
            continue
        costs, ct = None, None
        if logins and ad_dim:
            try:
                costs, ct = ad_costs(cid, ad_dim, d1.isoformat(), d2.isoformat(), logins, top=max(a.top, 1000))
            except QueryError as e:
                log(f'   ⚠ {title}: расходы не получены: {e}')
        if costs is not None and key == 'dynamics' and group != 'day':
            merged = {}
            for (kind, k), (cl, co) in costs.items():
                if kind == 'name' and k:
                    b = merged.setdefault(('name', bucket(k, group)), [0, 0])
                    b[0] += cl or 0
                    b[1] += co or 0
            costs = {k: tuple(v) for k, v in merged.items()}
        heads = [head] + (['Номер'] if head in WITH_ID else [])
        out = []
        for dims, vals in rows:
            name, did = dims[0]
            vals = list(vals)
            if costs is not None:
                cl, co = (costs.get(('id', str(did))) if did is not None else None) or costs.get(('name', name)) \
                    or (None, None)
                vals += cost_values(cl, co, vals[reach_i])
                if key == 'campaigns' and co:
                    camp_cost[name or '(не определено)'] = co
            out.append(([name] + ([did] if head in WITH_ID else []), vals))
        mets = M_DIRECT + (M_COST if costs is not None else [])
        tot = list(totals or [])
        if costs is not None and tot:
            tot += cost_values(*(ct or [None, None])[:2], tot[reach_i])
        write_table(ws, heads, mets, out, tot)
        status.append((title, len(out), total_rows, sampled, None))
        tables[key] = (heads, mets, out, tot)

    about = {v[0]: v[4] for v in DIRECT_REPORTS.values()}
    about.update({'Динамика': f'визиты из Директа по {"дням" if group == "day" else "неделям" if group == "week" else "месяцам"}'
                  + (', расход' if logins else ''),
                  'Цели по кампаниям': 'каждая цель по кампаниям: достижения, доля, конверсия'
                  + (', цена цели' if logins else '')})
    write_summary(summary, counter, d1, d2, p1, p2, mtot, cur, prev, s_cur, status, a.no_compare, group,
                  title=f'Яндекс Директ: {site}', about=about, prev_name=COMPARE[mode][1])
    summary['A5'] = cost_note or ('Визиты и цели — по Метрике, последний значимый переход из Директа. '
                                  'Клики и расход — по данным Директа; с визитами они не совпадают: повторные заходы '
                                  'после рекламы Метрика тоже засчитывает Директу.')
    summary['A5'].font = Font(color='8A5A00' if cost_note else '52514E')

    folder = DATA / f'{cid}_{slug(site)}'
    folder.mkdir(parents=True, exist_ok=True)
    stem = f'{slug(site)}_{d1.isoformat()}_{d2.isoformat()}'
    out_file = folder / f'direct_{stem}.xlsx'

    dashboard = None
    if want_charts:
        try:
            log('   панель с графиками…')
            order = (['Визиты', 'Клики', 'Расход, ₽', 'Цена клика, ₽', 'Достижения целей', 'Цена цели, ₽',
                      'Конверсия в любую цель, %', 'Отказы, %', 'Глубина просмотра', 'Время на сайте']
                     if logins else ['Визиты', 'Достижения целей', 'Конверсия в любую цель, %', 'Отказы, %',
                                     'Глубина просмотра', 'Время на сайте', 'Посетители'])
            ctx = {'title': f'Яндекс Директ: {site}', 'site': site, 'tables': tables, 'costs': bool(logins),
                   'kpis': make_kpis(mtot, cur, prev, order), 'goal_sources': goal_src,
                   'goal_sources_utm': goal_src_utm,
                   'periods': [('Период', d1, d2)] + ([] if a.no_compare else [(COMPARE[mode][0], p1, p2)]),
                   'meta': f'счётчик {cid} · только реклама в Директе{humans_note()}{goal_note()}',
                   'vs': COMPARE[mode][2],
                   'switch': {'cid': cid, 'kind': 'direct', 'mode': mode,
                              'goal': str(FOCUS_GOAL[0]) if FOCUS_GOAL else ''},
                   'goal_options': goal_options(goal_src, goal_src_utm),
                   'prev_label': COMPARE[mode][1], 'cur_label': 'этот год' if mode == 'yoy' else 'текущий период',
                   'period_short': f'{d1:%d.%m}–{d2:%d.%m.%Y}', 'exported': datetime.now().strftime('%d.%m.%Y %H:%M'),
                   'xlsx_name': out_file.name, 'warnings': [cost_note] if cost_note else [],
                   'sampled_note': ('По выборке: ' + ', '.join(s[0] for s in status if s[3]) + '.')
                   if any(s[3] for s in status) else ''}
            if 'dynamics' in tables:
                _, mets, rows, _ = tables['dynamics']
                dyn = {'dates': [datetime.strptime(r[0][0], '%Y-%m-%d').date() for r in rows],
                       'cur': [r[1][0] or 0 for r in rows], 'prev': None,
                       'label': {'day': 'дням', 'week': 'неделям', 'month': 'месяцам'}[group]}
                dyn['partial_tail'] = partial_tail(dyn['cur'], group)
                if len(mets) > len(M_DIRECT):
                    dyn['cost'] = [r[1][len(M_DIRECT) + 1] or 0 for r in rows]
                if not a.no_compare:
                    try:
                        prev_rows, _, _, _ = stat(cid, ['ym:s:visits'], [GROUPS[group][0]], p1.isoformat(),
                                                  p2.isoformat(), sort=GROUPS[group][0], extra=flt)
                        dyn['prev'] = [r[1][0] or 0 for r in prev_rows]
                    except QueryError as e:
                        log(f'   прошлый период для графика динамики не получен: {e}')
                if dyn['partial_tail']:
                    ctx['warnings'].insert(0, f'Данные за {dyn["dates"][-1]:%d.%m.%Y}, похоже, неполные: визитов в '
                                              f'разы меньше обычного. Перевыгрузите отчёт позже.')
                ctx['dynamics'] = dyn
            pics = charts.build_direct(ctx, folder / f'charts_direct_{d1.isoformat()}_{d2.isoformat()}')
            charts.excel_panel(wb, ctx, pics)
            dashboard = build_pages(folder / f'dashboard_direct_{stem}.html', ctx, pics)
        except Exception as e:  # noqa: BLE001
            log(f'   ⚠ панель с графиками не собрана: {type(e).__name__}: {e}')
            if 'Панель' in wb.sheetnames:
                del wb['Панель']
    wb.save(out_file)
    got = sum(1 for s in status if not s[4])
    log(f'✅ листов: {got + 1} из {len(status) + 1}' + (f', не получено: {len(status) - got}' if got < len(status) else '')
        + (' + Панель' if dashboard else ''))
    log(f'✅ файл: {out_file}')
    if dashboard:
        log(f'✅ панель в браузере: {dashboard}')
        log(f'   все панели списком: python run.py panel → http://127.0.0.1:{PORT}/')


# ─── SEO-отчёт: пять срезов Метрики для поискового трафика ──────────────────
# По статье Pixelplus «Яндекс Метрика для SEO»: источники год к году, страницы
# входа из поиска, фразы с привязкой к странице, длинные клики по фразам, цели
# из поиска. Год назад — со сдвигом на 364 дня: те же дни недели и те же
# полные недели, иначе выходные съедут и сравнение соврёт
ORGANIC = "ym:s:lastsignTrafficSource=='organic'"
FROM_YANDEX = "ym:s:lastsignSearchEngineRoot=='yandex'"
LONG_CLICK = 59          # «длинный» визит — дольше 59 секунд
LONG_NORM = 10           # норма доли длинных визитов по фразе — от 10–15 %
LONG_MIN_VISITS = 10     # по фразе с меньшим числом визитов доля — шум
SEO_TITLES = {'sources': 'Источники — сравнение', 'weeks': 'Поиск в динамике', 'entry': 'Страницы входа из поиска',
              'phrases': 'Фразы и страницы', 'long': 'Длинные клики', 'goals': 'Цели из поиска'}
SEO_ORDER = list(SEO_TITLES)
SEO_ABOUT = {
    'Источники — сравнение': 'каждый источник трафика: сейчас, в сравниваемом периоде и изменение',
    'Поиск в динамике': 'визиты из поиска по неделям (при «месяц к месяцу» — по дням) — всего, Яндекс, Google',
    'Страницы входа из поиска': 'страницы, на которые приходят из поиска: поведение, цели и визиты для сравнения',
    'Фразы и страницы': 'поисковая фраза и страница, на которую по ней пришли',
    'Длинные клики': f'доля визитов из Яндекса дольше {LONG_CLICK} секунд по каждой фразе; норма — от 10–15 %',
    'Цели из поиска': 'каждая цель по поисковым системам: достижения, доля, конверсия',
}
CHANGE_FMT = {'pp': '+0.0" п.п.";-0.0" п.п.";0" п.п."', 'pct': '+0.0"%";-0.0"%";0"%"'}


def change(v, pv, kind):
    """Изменение: для долей — разница в п.п., для остального — в процентах."""
    if v is None or pv is None:
        return None
    if kind == 'pct':
        return round(v - pv, 1)
    return round((v - pv) / pv * 100, 1) if pv else None


def yoy_merge(cur_rows, prev_rows, n):
    """Строки «сейчас» и «год назад» по значению группировки. Что было год
    назад и пропало сейчас — в конце: это потери."""
    prev = {tuple(d): v for d, v in prev_rows}
    out = [(d, v, prev.pop(tuple(d), [None] * n)) for d, v in cur_rows]
    out += [(list(d), [None] * n, v) for d, v in prev.items()]
    return out


def write_yoy(ws, heads, metrics, merged, cur_tot, prev_tot, prev_name='год назад'):
    """По каждой метрике три колонки: сейчас, для сравнения, изменение. Итоги — над шапкой."""
    def line(v, pv):
        out = []
        for (_, _, kind), x, px in zip(metrics, v, pv):
            out += [cell_value(x, kind), cell_value(px, kind), change(x, px, kind)]
        return out

    nd = len(heads)
    cols = list(heads)
    for _, h, _ in metrics:
        h = charts.lbl(h)
        cols += [h, f'{h} — {prev_name}', f'{h} — изменение']
    if cur_tot:
        ws.append(['Итого и в среднем'] + [None] * (nd - 1) + line(cur_tot, prev_tot or [None] * len(metrics)))
        for c in ws[1]:
            c.font, c.fill = TOTAL_FONT, TOTAL_FILL
    ws.append(cols)
    hr = ws.max_row
    for c in ws[hr]:
        c.font, c.fill = HEAD_FONT, HEAD_FILL
        c.alignment = Alignment(horizontal='center', vertical='center', wrap_text=True)
    ws.row_dimensions[hr].height = 46
    for d, v, pv in merged:
        ws.append([dim_value(x, h) for x, h in zip(d, heads)] + line(v, pv))
    for i, (_, _, kind) in enumerate(metrics):
        base = nd + 1 + 3 * i
        for col in ws.iter_cols(min_col=base, max_col=base + 2):
            for c in col:
                if c.row != hr:
                    c.number_format = (FORMATS[kind] if c.column < base + 2
                                       else CHANGE_FMT['pp' if kind == 'pct' else 'pct'])
    for i in range(1, nd + 1):
        ws.column_dimensions[get_column_letter(i)].width = 30
    for i in range(nd + 1, len(cols) + 1):
        ws.column_dimensions[get_column_letter(i)].width = 13
    ws.freeze_panes = ws.cell(row=hr + 1, column=nd + 1)
    ws.auto_filter.ref = f'A{hr}:{get_column_letter(len(cols))}{ws.max_row}'


def weekly(rows):
    """[(неделя, всего из поиска, Яндекс, Google)] из строк «неделя × поисковик»."""
    w = {}
    for dims, vals in rows:
        week, engine = dims[0][0], dims[1][1]
        x = w.setdefault(week, [0, 0, 0])
        v = vals[0] or 0
        x[0] += v
        if engine == 'yandex':
            x[1] += v
        elif engine == 'google':
            x[2] += v
    return [(k, *v) for k, v in sorted(w.items())]


def write_weeks(ws, cw, pw, prev_name='год назад', step='Неделя (с понедельника)'):
    unit = 'День' if step == 'День' else 'Неделя'
    heads = [step, 'Поиск', 'Яндекс', 'Google', f'{unit} — {prev_name}', f'Поиск — {prev_name}',
             f'Яндекс — {prev_name}', f'Google — {prev_name}', 'Поиск: изменение', 'Яндекс: изменение',
             'Google: изменение']
    ws.append(heads)
    for c in ws[1]:
        c.font, c.fill = HEAD_FONT, HEAD_FILL
        c.alignment = Alignment(horizontal='center', vertical='center', wrap_text=True)
    ws.row_dimensions[1].height = 32
    for i, cur in enumerate(cw):
        prev = pw[i] if i < len(pw) else (None, None, None, None)
        ws.append([dim_value(cur[0], step), *cur[1:],
                   dim_value(prev[0], step) if prev[0] else None, *prev[1:],
                   *(change(c, p, 'int') for c, p in zip(cur[1:], prev[1:]))])
    for row in ws.iter_rows(min_row=2):
        for c in row:
            c.number_format = ('DD.MM.YYYY' if c.column in (1, 5) else FORMATS['int'] if c.column < 9
                               else CHANGE_FMT['pct'])
    for i, w in enumerate((14, 11, 11, 11, 14, 13, 13, 13, 13, 13, 13), 1):
        ws.column_dimensions[get_column_letter(i)].width = w
    ws.freeze_panes = 'B2'
    return len(cw)


def long_clicks(all_rows, long_rows):
    """Склейка фраз без учёта регистра: Метрика отдаёт «Как…» и «как…» разными
    строками. Возвращает [(фраза, визиты, длинные, доля %)] по убыванию визитов."""
    agg = {}
    for rows, i in ((all_rows, 0), (long_rows, 1)):
        for dims, vals in rows:
            phrase = (dims[0] or '').strip()
            if not phrase:
                continue
            x = agg.setdefault(phrase.lower(), [phrase, 0, 0])
            x[1 + i] += vals[0] or 0
    out = [(p, v, lv, lv / v * 100 if v else None) for p, v, lv in agg.values()]
    return sorted(out, key=lambda x: -x[1])


def write_long(ws, rows, total, total_long):
    heads = ['Поисковая фраза', 'Визиты из Яндекса', f'Длинные визиты (дольше {LONG_CLICK} с)',
             'Доля длинных, %', 'Оценка']
    ws.append(['Весь трафик из Яндекса', cell_value(total, 'int'), cell_value(total_long, 'int'),
               cell_value(total_long / total * 100 if total else None, 'pct'),
               f'норма — от {LONG_NORM}–15 %'])
    for c in ws[1]:
        c.font, c.fill = TOTAL_FONT, TOTAL_FILL
    ws.append(heads)
    for c in ws[2]:
        c.font, c.fill = HEAD_FONT, HEAD_FILL
        c.alignment = Alignment(horizontal='center', vertical='center', wrap_text=True)
    ws.row_dimensions[2].height = 32
    low = Font(color='C0392B')
    for phrase, v, lv, share in rows:
        grade = ('мало визитов' if v < LONG_MIN_VISITS else
                 'ниже нормы' if (share or 0) < LONG_NORM else 'норма')
        ws.append([phrase, v, lv, cell_value(share, 'pct'), grade])
        if grade == 'ниже нормы':
            ws.cell(ws.max_row, 5).font = low
    for row in ws.iter_rows(min_row=1, min_col=2, max_col=4):
        row[0].number_format, row[1].number_format, row[2].number_format = (FORMATS['int'], FORMATS['int'],
                                                                             FORMATS['pct'])
    for col, w in zip('ABCDE', (60, 16, 18, 14, 16)):
        ws.column_dimensions[col].width = w
    ws.freeze_panes = 'B3'
    ws.auto_filter.ref = f'A2:E{ws.max_row}'


def cmd_seo(a):
    counter = resolve_counter(a.counter)
    cid, site = counter['id'], counter.get('site') or counter.get('name') or ''
    set_focus_goal(cid, getattr(a, 'goal', None), required=a.cmd in ('seo', 'direct'))
    d1, d2, p1, p2, mode = pick_periods(a, 'yoy')
    prev_name = COMPARE[mode][1]
    days = (d2 - d1).days + 1
    only = [k.strip() for k in a.only.split(',')] if a.only else SEO_ORDER
    bad = [k for k in only if k not in SEO_TITLES]
    if bad:
        sys.exit(f'Неизвестные листы: {", ".join(bad)}. Доступны: {", ".join(SEO_ORDER)}')
    org = {'filters': ORGANIC}
    iso = date.isoformat
    log(f'   SEO · счётчик {cid} · {site} · {d1:%d.%m.%Y}–{d2:%d.%m.%Y} ({days} дн.) · '
        f'{prev_name}: {p1:%d.%m.%Y}–{p2:%d.%m.%Y}{humans_note()}')

    want_charts = not a.no_charts
    if want_charts and not charts.available():
        log('   панель с графиками пропущена: нет seaborn — python -m pip install -r requirements.txt')
        want_charts = False

    wb = Workbook()
    summary = wb.active
    summary.title = 'Сводка'
    status, tables, goal_src, warnings = [], {}, {}, []
    mtot = M_SESSION + M_GOALS
    mids = [m[0] for m in mtot]
    try:
        _, cur, s_cur, _ = stat(cid, mids, [], iso(d1), iso(d2), top=1, extra=org)
        _, prev, _, _ = stat(cid, mids, [], iso(p1), iso(p2), top=1, extra=org)
        tail, _, _, _ = stat(cid, ['ym:s:visits'], ['ym:s:date'], iso(max(d1, d2 - timedelta(days=13))), iso(d2),
                             sort='ym:s:date', extra=org)
    except QueryError as e:
        sys.exit(f'Не удалось получить итоги: {e}')
    if partial_tail([r[1][0] or 0 for r in tail], 'day'):
        warnings.append(f'Данные за {d2:%d.%m.%Y}, похоже, неполные: визитов из поиска в разы меньше обычного. '
                        f'Последняя неделя может выглядеть провалом — перевыгрузите отчёт позже.')
        log(f'   ⚠ {warnings[-1]}')

    for key in only:
        title = SEO_TITLES[key]
        if key == 'goals':
            status.append(goal_sources_sheet(wb.create_sheet(title), cid, d1, d2, None, goal_src,
                                             dim='ym:s:lastsignSearchEngineRoot', head='Поисковая система',
                                             extra=org))
            continue
        log(f'   {title}…')
        ws = wb.create_sheet(title)
        try:
            if key == 'sources':
                dim = ['ym:s:lastsignTrafficSource']
                cr, ct, sampled, n = stat(cid, mids, dim, iso(d1), iso(d2), top=100, sort='-ym:s:visits')
                pr, pt, _, _ = stat(cid, mids, dim, iso(p1), iso(p2), top=100, sort='-ym:s:visits')
                merged = yoy_merge(cr, pr, len(mids))
                write_yoy(ws, ['Источник'], mtot, merged, ct, pt, prev_name)
                tables[key] = merged
                n = len(merged)
            elif key == 'weeks':
                # месяц к месяцу — по дням: недели на краях месяца неполные и выглядели бы провалами
                step = 'ym:s:date' if mode == 'mom' else 'ym:s:startOfWeek'
                dims = [step, 'ym:s:lastsignSearchEngineRoot']
                cr, _, sampled, _ = stat(cid, ['ym:s:visits'], dims, iso(d1), iso(d2), top=2000,
                                         sort=step, extra=org, ids=True)
                pr, _, _, _ = stat(cid, ['ym:s:visits'], dims, iso(p1), iso(p2), top=2000,
                                   sort=step, extra=org, ids=True)
                cw, pw = weekly(cr), weekly(pr)
                n = write_weeks(ws, cw, pw, prev_name, 'День' if mode == 'mom' else 'Неделя (с понедельника)')
                tables[key] = (cw, pw)
            elif key == 'entry':
                cr, ct, sampled, n = stat(cid, mids, ['ym:s:startURL'], iso(d1), iso(d2), top=a.top,
                                          sort='-ym:s:visits', extra=org)
                pr, pt, _, _ = stat(cid, ['ym:s:visits'], ['ym:s:startURL'], iso(p1), iso(p2), top=a.top,
                                    sort='-ym:s:visits', extra=org)
                was = {r[0][0]: r[1][0] for r in pr}
                more = [('yoy:visits', f'Визиты — {prev_name}', 'int'), ('yoy:change', 'Изменение визитов, %', 'chg')]
                rows = [(d, list(v) + [was.get(d[0]), change(v[0], was.get(d[0]), 'int')]) for d, v in cr]
                tot = list(ct or []) + ([pt[0] if pt else None, change(ct[0], pt[0], 'int')] if ct and pt else [None, None])
                write_table(ws, ['Страница входа'], mtot + more, rows, tot)
                tables[key] = (mtot + more, rows, was)
            elif key == 'phrases':
                mets = M_SHORT + M_GOALS[:1]
                cr, ct, sampled, n = stat(cid, [m[0] for m in mets], ['ym:s:lastsignSearchPhrase', 'ym:s:startURL'],
                                          iso(d1), iso(d2), top=a.top, sort='-ym:s:visits', extra=org)
                rows = [r for r in cr if r[0][0]]
                write_table(ws, ['Поисковая фраза', 'Страница входа'], mets, rows, ct)
            elif key == 'long':
                ya = {'filters': FROM_YANDEX}
                longf = {'filters': f'{FROM_YANDEX} AND ym:s:visitDuration>{LONG_CLICK}'}
                ar, at, sampled, _ = stat(cid, ['ym:s:visits'], ['ym:s:lastsignSearchPhrase'], iso(d1), iso(d2),
                                          top=a.top, sort='-ym:s:visits', extra=ya)
                lr, lt, _, _ = stat(cid, ['ym:s:visits'], ['ym:s:lastsignSearchPhrase'], iso(d1), iso(d2),
                                    top=a.top, sort='-ym:s:visits', extra=longf)
                rows = long_clicks(ar, lr)
                write_long(ws, rows, (at or [0])[0], (lt or [0])[0])
                tables[key] = rows
                n = len(rows)
        except QueryError as e:
            note_sheet(ws, f'Не получено: {e}')
            status.append((title, 0, 0, None, str(e)))
            log(f'   ⚠ {title}: {e}')
            continue
        status.append((title, ws.max_row - (2 if key in ('sources', 'entry', 'phrases', 'long') else 1), n,
                       sampled, None))

    # Весь трафик — листы обычного отчёта Метрики за те же периоды, после листов поиска
    group = pick_group(a, d1, d2)
    traffic_summary = wb.create_sheet('Весь трафик — сводка')
    try:
        tr = collect_traffic(wb, cid, d1, d2, p1, p2, group, ORDER, a.top, False)
        status += tr['status']
        write_summary(traffic_summary, counter, d1, d2, p1, p2, tr['mtot'], tr['cur'], tr['prev'], tr['sampled'],
                      [], False, group, title=f'Весь трафик: {site}', prev_name=prev_name)
    except QueryError as e:
        tr = None
        note_sheet(traffic_summary, f'Весь трафик не получен: {e}')
        log(f'   ⚠ весь трафик: {e}')

    write_summary(summary, counter, d1, d2, p1, p2, mtot, cur, prev, s_cur, status, False, group,
                  title=f'SEO: {site} — трафик из поиска', about={**traffic_about(group), **SEO_ABOUT},
                  prev_name=prev_name)
    summary['A5'] = ('Итоги — только визиты из поисковых систем (последний значимый переход); весь трафик — '
                     'на листе «Весь трафик — сводка».' + (' Год назад — те же дни недели: период сдвинут на 52 недели.'
                                                         if mode == 'yoy' else ''))
    summary['A5'].font = Font(color='52514E')

    folder = DATA / f'{cid}_{slug(site)}'
    folder.mkdir(parents=True, exist_ok=True)
    stem = f'{slug(site)}_{d1.isoformat()}_{d2.isoformat()}'
    out_file = folder / f'seo_{stem}.xlsx'

    dashboard = None
    if want_charts:
        try:
            log('   панель с графиками…')
            sampled = [s[0] for s in status if s[3]]
            ctx = {'title': f'SEO: {site}', 'site': site, 'tables': tables, 'goal_sources': goal_src,
                   'kpis': make_kpis(mtot, cur, prev, ['Визиты', 'Посетители', 'Достижения целей',
                                                        'Конверсия в любую цель, %', 'Отказы, %', 'Глубина просмотра',
                                                        'Время на сайте', 'Новые посетители, %']),
                   'periods': [('Период', d1, d2), (COMPARE[mode][0], p1, p2)],
                   'meta': f'счётчик {cid}{humans_note()}{goal_note()}'
                           + (' · год назад — те же дни недели' if mode == 'yoy' else ''),
                   'prev_label': prev_name, 'cur_label': 'этот год' if mode == 'yoy' else 'сейчас',
                   'step': 'дням' if mode == 'mom' else 'неделям',
                   'switch': {'cid': cid, 'kind': 'seo', 'mode': mode,
                              'goal': str(FOCUS_GOAL[0]) if FOCUS_GOAL else ''},
                   'goal_options': goal_options(goal_src),
                   'period_short': f'{d1:%d.%m}–{d2:%d.%m.%Y}', 'exported': datetime.now().strftime('%d.%m.%Y %H:%M'),
                   'xlsx_name': out_file.name, 'warnings': warnings, 'long_norm': LONG_NORM, 'vs': COMPARE[mode][2],
                   'long_min': LONG_MIN_VISITS, 'long_click': LONG_CLICK,
                   'sampled_note': ('По выборке: ' + ', '.join(sampled) + '.') if sampled else ''}
            pics_dir = folder / f'charts_seo_{d1.isoformat()}_{d2.isoformat()}'
            pics = charts.build_seo(ctx, pics_dir)
            ctx['sections'] = [('Поиск', ctx['kpis'], pics)]
            if tr:
                tctx = panel_context(counter, site, d1, d2, p1, p2, group, tr['mtot'], tr['cur'], tr['prev'],
                                     tr['tables'], tr['goal_list'], tr['status'], out_file.name, cid, False,
                                     tr['goal_src'], mode)
                tpics = charts.build(tctx, pics_dir / 'traffic')
                ctx['sections'].append(('Весь трафик', tctx['kpis'], tpics))
                if not warnings:
                    warnings.extend(tctx['warnings'])
                pics = pics + tpics
            charts.excel_panel(wb, ctx, pics)
            dashboard = build_pages(folder / f'dashboard_seo_{stem}.html', ctx, pics)
        except Exception as e:  # noqa: BLE001
            log(f'   ⚠ панель с графиками не собрана: {type(e).__name__}: {e}')
            if 'Панель' in wb.sheetnames:
                del wb['Панель']
    wb.save(out_file)
    got = sum(1 for s in status if not s[4])
    log(f'✅ листов: {got + 1} из {len(status) + 1}' + (f', не получено: {len(status) - got}' if got < len(status) else '')
        + (' + Панель' if dashboard else ''))
    log(f'✅ файл: {out_file}')
    if dashboard:
        log(f'✅ панель в браузере: {dashboard}')
        log(f'   все панели списком: python run.py panel → http://127.0.0.1:{PORT}/seo/')


# ─── панели в браузере ──────────────────────────────────────────────────────
PORT = 8790


def cmd_panel(a):
    """Панель в браузере: счётчики с кнопками отчётов и готовые отчёты — panel.py."""
    import panel
    panel.serve(a, sys.modules[__name__])


def main():
    ap = argparse.ArgumentParser(description='Отчёты Яндекс Метрики в Excel через API')
    sub = ap.add_subparsers(dest='cmd', required=True)
    sub.add_parser('counters', help='список счётчиков, доступных токену')

    def dates(p, days=30):
        p.add_argument('--days', type=int, default=days, help=f'последние N дней до вчера (по умолчанию {days})')
        p.add_argument('--from', dest='date_from', help='начало периода, ГГГГ-ММ-ДД')
        p.add_argument('--to', dest='date_to', help='конец периода, ГГГГ-ММ-ДД')
        p.add_argument('--top', type=int, default=TOP, help=f'строк на лист, по умолчанию {TOP}')
        p.add_argument('--compare', choices=['yoy', 'mom', 'qoq'],
                       help='сравнение: yoy — год к году, mom — месяц к месяцу, qoq — квартал к кварталу')
        p.add_argument('--with-robots', action='store_true', help='считать и роботов (по умолчанию — только люди)')
        p.add_argument('--goal', help='главная цель: номер или часть названия — конверсия и цена цели по ней '
                                      '(для seo и direct обязательна)')

    r = sub.add_parser('report', help='полный отчёт в Excel')
    r.add_argument('counter', help='номер счётчика или адрес сайта')
    dates(r)
    r.add_argument('--group', choices=list(GROUPS), help='динамика по дням, неделям или месяцам (по умолчанию сам)')
    r.add_argument('--only', help=f'только эти листы через запятую: {",".join(ORDER)}')
    r.add_argument('--no-compare', action='store_true', help='без сравнения с прошлым периодом')
    r.add_argument('--no-charts', action='store_true', help='без панели с графиками')

    w = sub.add_parser('raw', help='свой отчёт: любые метрики и группировки')
    w.add_argument('counter', help='номер счётчика или адрес сайта')
    w.add_argument('--metrics', required=True, help='метрики через запятую, например ym:s:visits,ym:s:users')
    w.add_argument('--dimensions', help='группировки через запятую, например ym:s:lastsignTrafficSource')
    w.add_argument('--sort', help='сортировка, например -ym:s:visits')
    dates(w)

    dr = sub.add_parser('direct', help='отчёт по Яндекс Директу: кампании, фразы, запросы, площадки, цели, расходы')
    dr.add_argument('counter', help='номер счётчика или адрес сайта')
    dates(dr, days=14)                   # Директ смотрят короткими периодами: 14 дней к прошлым 14
    dr.add_argument('--group', choices=list(GROUPS), help='динамика по дням, неделям или месяцам (по умолчанию сам)')
    dr.add_argument('--only', help=f'только эти листы через запятую: {",".join(DIRECT_ORDER)}')
    dr.add_argument('--login', help='логины Директа через запятую — если расходы нужны не из всех аккаунтов')
    dr.add_argument('--no-compare', action='store_true', help='без сравнения с прошлым периодом')
    dr.add_argument('--no-charts', action='store_true', help='без панели с графиками')

    so = sub.add_parser('seo', help='SEO: источники год к году, страницы входа и фразы из поиска, длинные клики, цели')
    so.add_argument('counter', help='номер счётчика или адрес сайта')
    dates(so, days=28)
    so.add_argument('--only', help=f'только эти листы через запятую: {",".join(SEO_ORDER)}')
    so.add_argument('--no-charts', action='store_true', help='без панели с графиками')

    pn = sub.add_parser('panel', help=f'все панели в браузере: http://127.0.0.1:{PORT}/')
    pn.add_argument('--port', type=int, default=PORT, help=f'порт, по умолчанию {PORT}')
    pn.add_argument('--no-browser', action='store_true', help='не открывать браузер самому')
    pn.add_argument('--data', help='папка с отчётами, по умолчанию data рядом со скриптом')

    a = ap.parse_args()
    global HUMANS_ONLY
    HUMANS_ONLY = not getattr(a, 'with_robots', False)
    if getattr(a, 'days', 1) < 1:
        sys.exit('--days должно быть больше нуля')
    try:
        {'counters': cmd_counters, 'report': cmd_report, 'raw': cmd_raw, 'direct': cmd_direct, 'seo': cmd_seo,
         'panel': cmd_panel}[a.cmd](a)
    except QueryError as e:
        sys.exit(f'Ошибка API Метрики: {e}')


if __name__ == '__main__':
    main()
