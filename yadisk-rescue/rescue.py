# -*- coding: utf-8 -*-
"""yadisk-rescue — спасение файлов с Яндекс Диска через открытую вкладку браузера.

Скрипт подключается к Chrome, в котором вы вошли в Яндекс Диск (start_chrome.py),
и делает внутри вкладки Диска те же запросы, что и сам сайт: список файлов и
ссылка «Скачать». Файлы качаются строго по одному, с паузами.

Команды (подробно — README.md):
  python rescue.py setup --out "D:\\YandexDisk_rescue"   куда сохранять
  python rescue.py ping                                  есть ли вкладка Диска, кто вошёл
  python rescue.py index  --root "/disk/Фото"            собрать список файлов
  python rescue.py report --root "/disk/Фото"            таблица по папкам: файлов, ГБ, видео
  python rescue.py check  --root "/disk/Фото" --dirs "2013"   проверка без скачивания
  python rescue.py fast   --root "/disk/Фото" [--dirs ...] [--no-video]
  python rescue.py slow   --root "/disk/Фото" [--dirs ...]
  python rescue.py all    --root "/disk/Фото" [--dirs ...] [--no-video]
  python rescue.py stats  --root "/disk/Фото" [--dirs ...]
  python rescue.py start all --root "/disk/Фото"         то же в фоне, окно можно закрыть
  python rescue.py status | stop
"""
import argparse, json, os, random, re, signal, subprocess, sys, time
from collections import Counter, defaultdict
from datetime import datetime

try:
    sys.stdout.reconfigure(encoding='utf-8', line_buffering=True)
    sys.stderr.reconfigure(encoding='utf-8')
except Exception:
    pass

os.environ.setdefault('NODE_OPTIONS', '--no-deprecation')   # тишина от служебного node Playwright

try:
    import requests
    from playwright.sync_api import sync_playwright
except ImportError:
    sys.exit('Не хватает библиотек. Выполните:  pip install -r requirements.txt')

SKILL_DIR = os.path.dirname(os.path.abspath(__file__))
HOME_DIR = os.environ.get('YADISK_RESCUE_HOME') or os.path.join(os.path.expanduser('~'), '.yadisk-rescue')
CONFIG = os.path.join(HOME_DIR, 'config.json')
PREVIEW_DIR = '_превью_1280'
WORK_DIR_NAME = '_rescue'

API_JS = """async ([method, params]) => {
    const el = document.getElementById('preloaded-data');
    if (!el) return {status: 0, text: 'NO_PRELOADED_DATA'};
    const d = JSON.parse(el.textContent);
    const r = await fetch('/models-v2?m=' + method, {method:'POST', credentials:'include',
        headers:{'Content-Type':'application/json'},
        body: JSON.stringify({sk: d.config.sk, apiMethod: method, requestParams: params})});
    return {status: r.status, text: (await r.text())};
}"""

WHO_JS = """() => {
    const el = document.getElementById('preloaded-data');
    if (!el) return null;
    const d = JSON.parse(el.textContent);
    const u = d.userCurrent || {}, s = d.space || {};
    return {login: u.login, name: u.name, used: s.used, limit: s.limit, files: s.files_count};
}"""


# ---------------------------------------------------------------- настройки

def load_config():
    if os.path.exists(CONFIG):
        with open(CONFIG, encoding='utf-8') as f:
            return json.load(f)
    return {}


def save_config(cfg):
    os.makedirs(HOME_DIR, exist_ok=True)
    with open(CONFIG, 'w', encoding='utf-8') as f:
        json.dump(cfg, f, ensure_ascii=False, indent=1)


CFG = load_config()
OUT_ROOT = CFG.get('out') or os.path.join(os.path.expanduser('~'), 'YandexDisk_rescue')
PORT = CFG.get('port', 9222)
WORK = os.path.join(OUT_ROOT, WORK_DIR_NAME)


class Stop(Exception):
    """Капча, выход из аккаунта или отказ — дальше не идём, чтобы не злить Яндекс."""


class ApiError(Exception):
    """Ошибка по конкретному файлу — его пропускаем, идём дальше."""


def log(msg):
    line = f"{datetime.now():%Y-%m-%d %H:%M:%S} {msg}"
    print(line)
    os.makedirs(WORK, exist_ok=True)
    with open(os.path.join(WORK, 'rescue.log'), 'a', encoding='utf-8') as f:
        f.write(line + '\n')


def fix_root(root):
    """'/disk/Фото', 'disk/Фото', 'Фото' → '/disk/Фото'. Чинит путь, испорченный Git Bash
    ('C:/Program Files/Git/disk/Фото')."""
    root = root.replace('\\', '/').strip()
    i = root.find('/disk')
    if i > 0 and ':' in root[:i]:
        root = root[i:]
    if root.startswith('disk/') or root == 'disk':
        root = '/' + root
    if not root.startswith('/disk'):
        root = '/disk/' + root.lstrip('/')
    return root.rstrip('/') or '/disk'


def state_path(root):
    name = ''.join('_' if ch in '<>:"/\\|?* ' else ch for ch in root.strip('/'))
    return os.path.join(WORK, 'state_' + name + '.json')


def load_state(root):
    p = state_path(root)
    if os.path.exists(p):
        with open(p, encoding='utf-8') as f:
            return json.load(f)
    return {'root': root, 'files': {}, 'dirs_done': []}


def save_state(st):
    os.makedirs(WORK, exist_ok=True)
    p = state_path(st['root'])
    with open(p + '.tmp', 'w', encoding='utf-8') as f:
        json.dump(st, f, ensure_ascii=False, indent=0)
    os.replace(p + '.tmp', p)


def local_path(disk_path, *sub):
    rel = disk_path[len('/disk/'):] if disk_path.startswith('/disk/') else disk_path.lstrip('/')
    bad = '<>:"|?*'
    parts = [''.join('_' if ch in bad else ch for ch in part).rstrip(' .') or '_' for part in rel.split('/')]
    path = os.path.join(OUT_ROOT, *sub, *parts)
    if os.name == 'nt' and len(path) > 250 and not path.startswith('\\\\?\\'):
        path = '\\\\?\\' + os.path.abspath(path)
    return path


def set_time(path, info):
    ts = info.get('etime') or info.get('mtime')
    if ts:
        try:
            os.utime(path, (ts, ts))
        except OSError:
            pass


def node_kind(node):
    """'s466klg' → 'klg': буквы в имени сервера хранилища."""
    m = re.match(r's\d+([a-z]+)$', node or '')
    return m.group(1) if m else (node or '?')


def check_block(resp, what):
    """403/429 или HTML-страница вместо файла — похоже на капчу/блок: стоп."""
    if resp.status_code in (403, 429):
        raise Stop(f'{what}: HTTP {resp.status_code}')
    if 'text/html' in resp.headers.get('Content-Type', ''):
        raise Stop(f'{what}: вместо файла пришла HTML-страница (капча?)')


# ---------------------------------------------------------------- вкладка Диска

class Tab:
    def __init__(self, pw):
        self.pw = pw
        self.browser = None
        self.api_errors = 0
        self._find(wait=False)

    def _find(self, wait=True):
        """Найти вкладку disk.yandex.*/client. При обрыве — ждать до 30 минут."""
        for _ in range(60 if wait else 1):
            try:
                if self.browser is None or not self.browser.is_connected():
                    self.browser = self.pw.chromium.connect_over_cdp(f'http://127.0.0.1:{PORT}')
                pages = [pg for c in self.browser.contexts for pg in c.pages
                         if 'disk.yandex.' in pg.url and '/client/' in pg.url]
                if pages:
                    self.page = pages[0]
                    return
                msg = 'в Chrome нет вкладки Яндекс Диска (адрес disk.yandex.ru/client/...)'
            except Exception as e:
                self.browser = None
                msg = f'Chrome на порту {PORT} недоступен ({type(e).__name__}) — запустите start_chrome.py'
            if not wait:
                raise Stop(msg)
            log(msg + ' — жду 30 с')
            time.sleep(30)
        raise Stop('вкладка Диска не появилась за 30 минут')

    def who(self):
        return self.page.evaluate(WHO_JS)

    def call(self, method, params):
        for attempt in range(6):
            try:
                r = self.page.evaluate(API_JS, [method, params])
            except Exception as e:
                log(f'{method}: {type(e).__name__} {str(e)[:150]} — повтор через 30 с')
                time.sleep(30)
                self._find()
                continue
            txt = r['text']
            if txt == 'NO_PRELOADED_DATA':
                log('вкладка Диска ещё грузится или открыта не та страница — повтор через 30 с')
                time.sleep(30)
                self._find()
                continue
            if 'captcha' in txt.lower()[:2000] or r['status'] == 401:
                raise Stop(f'{method}: HTTP {r["status"]} {txt[:300]}')
            if r['status'] == 429 or r['status'] >= 500:
                log(f'{method}: HTTP {r["status"]} — пауза {60 * (attempt + 1)} с')
                time.sleep(60 * (attempt + 1))
                continue
            try:
                data = json.loads(txt)
            except ValueError:
                raise Stop(f'{method}: ответ не JSON: {txt[:300]}')
            if r['status'] != 200 or (isinstance(data, dict) and data.get('error')):
                self.api_errors += 1
                if self.api_errors >= 10:
                    raise Stop(f'{method}: 10 ошибок подряд, последняя HTTP {r["status"]} {txt[:300]}')
                raise ApiError(f'HTTP {r["status"]} {txt[:200]}')
            self.api_errors = 0
            return data
        raise Stop(f'{method}: 6 неудачных попыток подряд')

    def listing(self, d):
        """Содержимое папки (без вложенных), по 100 за запрос, с паузами."""
        out, offset = [], 0
        while True:
            data = self.call('mpfs/resources', {'idContext': d, 'order': 1, 'sort': 'name',
                                                'offset': offset, 'amount': 100})
            res = [x for x in data.get('resources', []) if x.get('path') != d]
            out += res
            time.sleep(random.uniform(1.5, 3.5))
            if len(res) < 100:
                return out
            offset += 100

    def session(self):
        s = requests.Session()
        s.headers['User-Agent'] = self.page.evaluate('navigator.userAgent')
        s.headers['Referer'] = 'https://disk.yandex.ru/'
        for c in self.page.context.cookies():
            if 'yandex' in c['domain']:
                s.cookies.set(c['name'], c['value'], domain=c['domain'], path=c['path'])
        return s


# ---------------------------------------------------------------- один прогон за раз

def pid_alive(pid):
    if os.name == 'nt':
        import ctypes
        k = ctypes.windll.kernel32
        h = k.OpenProcess(0x1000, False, int(pid))
        if not h:
            return False
        code = ctypes.c_ulong()
        ok = k.GetExitCodeProcess(h, ctypes.byref(code))
        k.CloseHandle(h)
        return bool(ok) and code.value == 259
    try:
        os.kill(int(pid), 0)
        return True
    except OSError:
        return False


LOCK = os.path.join(WORK, 'run.lock')


def refuse_if_running():
    other = running_pid()
    if other and other['pid'] != os.getpid():
        sys.exit(f'Уже идёт прогон (PID {other["pid"]}, {other.get("cmd")}). '
                 f'Два сразу запускать нельзя — Яндекс начнёт показывать капчу. '
                 f'Посмотреть: python rescue.py status   Остановить: python rescue.py stop')


def take_lock(cmd):
    refuse_if_running()
    os.makedirs(WORK, exist_ok=True)
    with open(LOCK, 'w', encoding='utf-8') as f:
        json.dump({'pid': os.getpid(), 'cmd': cmd, 'started': f'{datetime.now():%Y-%m-%d %H:%M}'}, f, ensure_ascii=False)


def running_pid():
    """PID идущего прогона или None."""
    try:
        with open(LOCK, encoding='utf-8') as f:
            other = json.load(f)
    except (OSError, ValueError):
        return None
    return other if other.get('pid') and pid_alive(other['pid']) else None


def release_lock():
    try:
        with open(LOCK, encoding='utf-8') as f:
            mine = json.load(f).get('pid') == os.getpid()
        if mine:
            os.remove(LOCK)
    except (OSError, ValueError):
        pass


# ---------------------------------------------------------------- команды

def cmd_index(tab, st):
    if st.get('indexed'):            # список уже собирали — обходим заново, статусы файлов сохраняются
        st['dirs_done'] = []
        st.pop('indexed')
    queue = [st['root']]
    while queue:
        d = queue.pop(0)
        if d in st['dirs_done']:
            continue
        try:
            items = tab.listing(d)
        except ApiError as e:
            log(f'список: {d} — не получен ({e}), пропускаю')
            continue
        n_files = n_dirs = 0
        for x in items:
            if x['type'] == 'dir':
                queue.append(x['path']); n_dirs += 1
            else:
                m = x.get('meta', {})
                f = st['files'].setdefault(x['path'], {'status': 'new'})
                f.update(size=m.get('size'), mtime=x.get('mtime'), etime=x.get('etime'),
                         mediatype=m.get('mediatype'))
                n_files += 1
        st['dirs_done'].append(d)
        save_state(st)
        log(f'список: {d} — файлов {n_files}, папок {n_dirs}; всего файлов {len(st["files"])}')
    st['indexed'] = f'{datetime.now():%Y-%m-%d %H:%M}'
    save_state(st)
    log(f'список готов: {len(st["files"])} файлов')


def cmd_report(st):
    if not st.get('files'):
        sys.exit('Список пуст — сначала: python rescue.py index --root "..."')
    root = st['root'].rstrip('/') + '/'
    agg = defaultdict(lambda: [0, 0, 0, 0, 0])
    for p, f in st['files'].items():
        rest = p[len(root):]
        top = rest.split('/')[0] if '/' in rest else '(файлы прямо в папке)'
        a = agg[top]
        a[0] += 1; a[1] += f.get('size') or 0
        if f.get('mediatype') == 'video':
            a[2] += 1; a[3] += f.get('size') or 0
        a[4] += f['status'] == 'done'
    print(f'Папка {st["root"]}: {len(st["files"])} файлов, '
          f'{sum(a[1] for a in agg.values()) / 1e9:.1f} ГБ\n')
    print('| № | Папка | Файлов | ГБ | Из них видео | Уже скачано |')
    print('|---|---|---|---|---|---|')
    for i, (k, a) in enumerate(sorted(agg.items(), key=lambda x: x[0].lower()), 1):
        video = f'{a[2]} шт., {a[3] / 1e9:.2f} ГБ' if a[2] else '—'
        print(f'| {i} | {k} | {a[0]} | {a[1] / 1e9:.2f} | {video} | {a[4]} |')
    print('\nКак читать: «Файлов» и «ГБ» — всё в папке вместе с вложенными; '
          '«Из них видео» — сколько среди них видео и их вес; «Уже скачано» — оригиналы на вашем диске.')


def cmd_check(tab, st, dirs, sample):
    """Без скачивания: живо ли превью и на какой сервер хранилища ведёт «Скачать»."""
    targets = [st['root'].rstrip('/') + '/' + d.strip('/') for d in dirs] if dirs else [st['root']]
    s = tab.session()
    for d in targets:
        try:
            files = [x for x in tab.listing(d) if x['type'] == 'file']
        except ApiError as e:
            print(f'{d}: папка не открылась ({e}) — проверьте название')
            continue
        if not files:
            print(f'{d}: файлов нет (или такой папки нет)')
            continue
        step = max(1, len(files) // sample) if sample else 1
        pick = files[::step][:sample] if sample else files
        rows = []
        for x in pick:
            xxs = [u for u in x.get('meta', {}).get('sizes', []) if u['name'] == 'XXS']
            pv = 0
            if xxs:
                try:
                    pv = s.get('https:' + xxs[0]['url'], timeout=(10, 20)).status_code
                except requests.exceptions.RequestException:
                    pv = -1
            node = '?'
            try:
                link = tab.call('mpfs/url', {'path': x['path']}).get('file', '')
                with s.get('https:' + link if link.startswith('//') else link, allow_redirects=False,
                           timeout=(20, 30), stream=True) as h:
                    loc = h.headers.get('Location', '')
                    node = loc.split('/')[2].split('.')[0] if loc else f'HTTP{h.status_code}'
            except (ApiError, requests.exceptions.RequestException) as e:
                node = type(e).__name__
            rows.append((x['name'], pv, node))
            time.sleep(random.uniform(0.8, 1.6))
        kinds = Counter(node_kind(r[2]) for r in rows)
        pvs = Counter('живо' if r[1] == 200 else 'нет' for r in rows)
        print(f'\n{d}: файлов {len(files)}, проверено {len(rows)}')
        print(f'  превью: {dict(pvs)}')
        print(f'  серверы хранилища (буквы в имени): {dict(kinds)}')
        for r in rows:
            print(f'    {r[0]:40} превью {"живо" if r[1] == 200 else "нет":4}  сервер {r[2]}')


def save_previews(tab, s, st, d, paths):
    """Страховые превью 1280 px для файлов папки d. Пишет f['preview'] = HTTP-код."""
    need = [p for p in paths if 'preview' not in st['files'][p]]
    if not need:
        return
    try:
        items = tab.listing(d)
    except ApiError as e:
        log(f'превью {d}: список не получен — {e}')
        return
    urls = {}
    for x in items:
        if x['type'] == 'file':
            big = [u for u in x.get('meta', {}).get('sizes', []) if u['name'] == 'XXXL']
            if big:
                u = big[0]['url']
                urls[x['path']] = 'https:' + u if u.startswith('//') else u
    ok = 0
    for p in need:
        f = st['files'][p]
        if p not in urls:
            f['preview'] = 0
            continue
        try:
            r = s.get(urls[p], timeout=(15, 60))
        except requests.exceptions.RequestException as e:
            log(f'превью {p}: {type(e).__name__}')
            continue
        if r.status_code == 200:
            check_block(r, 'превью')
        f['preview'] = r.status_code
        if r.status_code == 200 and r.content:
            dst = local_path(p, PREVIEW_DIR)
            if not dst.lower().endswith(('.jpg', '.jpeg')):
                dst += '.jpg'
            try:
                os.makedirs(os.path.dirname(dst), exist_ok=True)
                with open(dst, 'wb') as fh:
                    fh.write(r.content)
                set_time(dst, f)
                ok += 1
            except OSError as e:
                log(f'превью {p}: не записать на диск — {e}')
        time.sleep(random.uniform(1, 2.5))
    save_state(st)
    log(f'превью {d}: живых {ok} из {len(need)}')


def download_one(tab, s, disk_path, info, read_timeout):
    """(статус, пояснение). Недокачанное остаётся в .part и докачивается с места обрыва."""
    dst = local_path(disk_path)
    os.makedirs(os.path.dirname(dst), exist_ok=True)
    size = info.get('size')
    if os.path.exists(dst) and os.path.getsize(dst) == size:
        return 'done', 'уже на диске'
    link = tab.call('mpfs/url', {'path': disk_path}).get('file')
    if not link:
        return 'error', 'нет ссылки'
    url = 'https:' + link if link.startswith('//') else link
    part = dst + '.part'
    have = os.path.getsize(part) if os.path.exists(part) else 0
    if size and have >= size:
        have = 0
    t0, got, node = time.time(), 0, '?'
    try:
        with s.get(url, allow_redirects=False, timeout=(30, 60), stream=True) as h:
            loc = h.headers.get('Location')
            if h.status_code in (301, 302, 303, 307, 308) and loc:
                node = loc.split('/')[2].split('.')[0]
                url = loc
            elif h.status_code != 200:
                check_block(h, 'downloader')
                return 'error', f'HTTP {h.status_code}'
        info['node'] = node
        hdr = {'Range': f'bytes={have}-'} if have else {}
        with s.get(url, headers=hdr, timeout=(30, read_timeout), stream=True) as r:
            if r.status_code not in (200, 206):
                check_block(r, 'хранилище')
                return 'error', f'HTTP {r.status_code}, сервер {node}'
            if r.status_code == 200:
                check_block(r, 'хранилище')
                have = 0
            with open(part, 'ab' if have else 'wb') as fh:
                for chunk in r.iter_content(256 * 1024):
                    fh.write(chunk); got += len(chunk)
    except requests.exceptions.RequestException as e:
        total = have + got
        return ('partial' if total else 'timeout'), \
            f'{type(e).__name__}, сервер {node}, {time.time()-t0:.0f}с, есть {total/1e6:.1f} из {(size or 0)/1e6:.1f} МБ'
    total = have + got
    if size and total != size:
        return 'partial', f'{total} из {size} байт, сервер {node}'
    os.replace(part, dst)
    set_time(dst, info)
    return 'done', f'{got/1e6:.1f} МБ за {time.time()-t0:.0f}с, сервер {node}'


def select(st, dirs, no_video, only_video=False):
    paths = list(st['files'])
    if dirs:
        prefixes = [st['root'].rstrip('/') + '/' + d.strip('/') + '/' for d in dirs]
        paths = [p for p in paths if any(p.startswith(x) for x in prefixes)]
    if no_video:
        paths = [p for p in paths if st['files'][p].get('mediatype') != 'video']
    if only_video:
        paths = [p for p in paths if st['files'][p].get('mediatype') == 'video']
    return paths


def cmd_download(tab, st, phase, a, only_video=False):
    s = tab.session()
    paths = select(st, a.dirs, a.no_video, only_video)
    if phase == 'fast':
        read_timeout, statuses = a.fast_timeout, ('new', 'error')
    else:
        read_timeout, statuses = a.slow_timeout, ('timeout', 'partial', 'error')
    by_dir = {}
    for p in sorted(paths):
        by_dir.setdefault(p.rsplit('/', 1)[0], []).append(p)
    total_done = total_tried = 0
    for d, dpaths in by_dir.items():
        if phase == 'fast' and not a.no_preview:
            save_previews(tab, s, st, d, dpaths)
        todo = [p for p in dpaths if st['files'][p]['status'] in statuses
                and st['files'][p].get('tries_' + phase, 0) < a.max_tries]
        # сначала недокачанные куски, потом файлы с живым превью, потом остальные
        todo.sort(key=lambda p: (st['files'][p]['status'] != 'partial',
                                 st['files'][p].get('preview') != 200, p))
        if a.limit:
            todo = todo[:max(0, a.limit - total_tried)]
        if not todo:
            continue
        log(f'проход {phase}: {d} — в очереди {len(todo)}')
        for i, p in enumerate(todo, 1):
            f = st['files'][p]
            f['tries_' + phase] = f.get('tries_' + phase, 0) + 1
            if total_tried and total_tried % 300 == 0:
                s = tab.session()
            try:
                status, note = download_one(tab, s, p, f, read_timeout)
            except ApiError as e:
                status, note = 'error', f'API: {e}'
            except OSError as e:
                status, note = 'error', f'диск: {e}'
            f['status'], f['note'] = status, note
            save_state(st)
            total_done += status == 'done'; total_tried += 1
            log(f'[{phase} {i}/{len(todo)}] {status:7} {p} — {note}')
            # после зависшего сервера мы и так простояли read_timeout — короткой паузы хватит
            if status == 'timeout' and 'ReadTimeout' in note:
                time.sleep(random.uniform(1, 2))
            else:
                time.sleep(random.uniform(a.pause_min, a.pause_max))
        if a.limit and total_tried >= a.limit:
            break
    log(f'проход {phase} закончен: скачано {total_done} из {total_tried}')


def cmd_stats(st, dirs=(), no_video=False):
    paths = select(st, dirs, no_video)
    files = [st['files'][p] for p in paths]
    names = {'done': 'скачано', 'partial': 'частично', 'timeout': 'не ответил',
             'error': 'ошибка', 'new': 'ещё не пробовали'}
    c = Counter(names.get(f['status'], f['status']) for f in files)
    pv = sum(1 for f in files if f.get('preview') == 200)
    size_done = sum(f.get('size') or 0 for f in files if f['status'] == 'done')
    size_all = sum(f.get('size') or 0 for f in files)
    kinds = Counter(node_kind(f['node']) for f in files if f.get('node'))
    print(f'{st["root"]}{" → " + ", ".join(dirs) if dirs else ""}: файлов {len(files)} '
          f'({size_all/1e9:.2f} ГБ); оригиналы: {dict(c)}; скачано {size_done/1e9:.2f} ГБ; '
          f'превью 1280 сохранено: {pv}; серверы: {dict(kinds)}')


def cmd_ping(tab):
    who = tab.who()
    if not who:
        sys.exit('Вкладка Диска найдена, но страница ещё не загрузилась — обновите её и повторите.')
    print(f'Вкладка: {tab.page.url}')
    print(f'Аккаунт: {who.get("login")} ({who.get("name")})')
    if who.get('used'):
        print(f'На Диске занято {who["used"]/1e9:.1f} ГБ из {who["limit"]/1e9:.0f} ГБ, файлов {who.get("files")}')
    print(f'Сохранять буду сюда: {OUT_ROOT}')
    tab.call('mpfs/resources', {'idContext': '/disk', 'order': 1, 'sort': 'name', 'offset': 0, 'amount': 1})
    print('Запрос к Диску прошёл — всё готово.')


def cmd_start(argv):
    """Запустить команду фоновым процессом, независимым от окна терминала."""
    if not argv or argv[0] not in ('index', 'fast', 'slow', 'all'):
        sys.exit('Пример: python rescue.py start all --root "/disk/Фото"')
    refuse_if_running()
    os.makedirs(WORK, exist_ok=True)
    out = open(os.path.join(WORK, 'run.out'), 'ab')
    args = [sys.executable, os.path.abspath(__file__)] + argv
    kw = dict(stdout=out, stderr=out, stdin=subprocess.DEVNULL, cwd=SKILL_DIR)
    env = dict(os.environ, PYTHONIOENCODING='utf-8')
    if os.name == 'nt':
        flags = 0x08000000 | 0x00000200          # CREATE_NO_WINDOW | CREATE_NEW_PROCESS_GROUP
        try:
            p = subprocess.Popen(args, creationflags=flags | 0x01000000, env=env, **kw)   # + BREAKAWAY_FROM_JOB
        except OSError:
            p = subprocess.Popen(args, creationflags=flags, env=env, **kw)
    else:
        p = subprocess.Popen(args, start_new_session=True, env=env, **kw)
    time.sleep(3)
    if p.poll() is not None:
        with open(os.path.join(WORK, 'run.out'), encoding='utf-8', errors='replace') as f:
            tail = f.read()[-1500:]
        sys.exit(f'Процесс сразу завершился (код {p.returncode}):\n{tail}')
    print(f'Запущено в фоне, PID {p.pid}. Журнал: {os.path.join(WORK, "rescue.log")}')
    print('Проверить: python rescue.py status   Остановить: python rescue.py stop')


def cmd_status():
    running = running_pid()
    if running:
        print(f'Идёт прогон: PID {running["pid"]}, {running.get("cmd")}, с {running.get("started")}')
    else:
        print('Сейчас ничего не запущено.')
    logf = os.path.join(WORK, 'rescue.log')
    if os.path.exists(logf):
        with open(logf, encoding='utf-8') as f:
            lines = f.readlines()[-8:]
        print('Последние строки журнала:')
        print(''.join(lines).rstrip())
    for name in sorted(os.listdir(WORK)) if os.path.isdir(WORK) else []:
        if name.startswith('state_') and name.endswith('.json'):
            with open(os.path.join(WORK, name), encoding='utf-8') as f:
                cmd_stats(json.load(f))


def cmd_stop():
    other = running_pid()
    if not other:
        print('Сейчас ничего не запущено.')
        return
    os.kill(int(other['pid']), signal.SIGTERM)
    print(f'Остановлен PID {other["pid"]}. Продолжить можно той же командой — начнёт с места остановки.')


def main():
    if len(sys.argv) > 1 and sys.argv[1] == 'start':
        return cmd_start(sys.argv[2:])
    ap =argparse.ArgumentParser(description='Спасение файлов с Яндекс Диска через вкладку браузера')
    ap.add_argument('cmd', choices=['setup', 'ping', 'index', 'report', 'check', 'fast', 'slow',
                                    'all', 'stats', 'status', 'stop'])
    ap.add_argument('--root', default='/disk', help='папка на Диске, например "/disk/Фото"')
    ap.add_argument('--dirs', nargs='*', default=[], help='подпапки внутри --root: "2013" "Отпуск 2015"')
    ap.add_argument('--out', help='setup: куда сохранять файлы')
    ap.add_argument('--port', type=int, help='setup: порт отладки Chrome (по умолчанию 9222)')
    ap.add_argument('--no-video', action='store_true', help='пропустить видео')
    ap.add_argument('--no-preview', action='store_true', help='не сохранять страховые превью 1280 px')
    ap.add_argument('--pause-min', type=float, default=3, help='пауза между файлами, от, с')
    ap.add_argument('--pause-max', type=float, default=7, help='пауза между файлами, до, с')
    ap.add_argument('--fast-timeout', type=int, default=10, help='fast: сколько ждать ответа сервера, с')
    ap.add_argument('--slow-timeout', type=int, default=60, help='slow: сколько ждать ответа сервера, с')
    ap.add_argument('--max-tries', type=int, default=3, help='попыток на файл в одном виде прохода')
    ap.add_argument('--limit', type=int, default=0, help='не больше N файлов (для пробы)')
    ap.add_argument('--sample', type=int, default=40, help='check: сколько файлов проверить в папке, 0 — все')
    a = ap.parse_args()

    if a.cmd == 'setup':
        cfg = load_config()
        if a.out:
            cfg['out'] = os.path.abspath(a.out)
        if a.port:
            cfg['port'] = a.port
        save_config(cfg)
        os.makedirs(cfg.get('out', OUT_ROOT), exist_ok=True)
        print(f'Сохранять буду сюда: {cfg.get("out", OUT_ROOT)}\nНастройки: {CONFIG}')
        return
    if a.cmd == 'status':
        return cmd_status()
    if a.cmd == 'stop':
        return cmd_stop()

    a.root = fix_root(a.root)
    st = load_state(a.root)
    if a.cmd == 'stats':
        return cmd_stats(st, a.dirs, a.no_video)
    if a.cmd == 'report':
        return cmd_report(st)
    if a.cmd in ('fast', 'slow', 'all') and not st['files']:
        sys.exit(f'Для {a.root} ещё нет списка файлов — сначала: python rescue.py index --root "{a.root}"')

    long_run = a.cmd in ('index', 'fast', 'slow', 'all')
    if long_run:
        take_lock(f'{a.cmd} {a.root} {" ".join(a.dirs)}'.strip())
    try:
        with sync_playwright() as pw:
            tab = Tab(pw)
            if a.cmd == 'ping':
                return cmd_ping(tab)
            if a.cmd == 'check':
                return cmd_check(tab, st, a.dirs, a.sample)
            log(f'старт: {a.cmd} {a.root} {" ".join(a.dirs)} → {OUT_ROOT}')
            if a.cmd == 'index':
                cmd_index(tab, st)
            elif a.cmd in ('fast', 'slow'):
                cmd_download(tab, st, a.cmd, a)
            else:                                   # all: фото → видео → повтор зависших
                video_wanted = not a.no_video
                a.no_video = True
                cmd_download(tab, st, 'fast', a)
                if video_wanted:
                    a.no_video = False
                    cmd_download(tab, st, 'fast', a, only_video=True)
                    a.no_video = not video_wanted
                cmd_download(tab, st, 'slow', a)
            cmd_stats(st, a.dirs, a.no_video)
    except Stop as e:
        save_state(st)
        log(f'СТОП: {e}')
        log('Откройте вкладку Диска: если там капча — пройдите её руками, подождите 15–30 минут '
            'и запустите ту же команду снова, она продолжит с места остановки.')
        sys.exit(2)
    except KeyboardInterrupt:
        save_state(st)
        log('остановлено вручную')
    finally:
        if long_run:
            release_lock()


if __name__ == '__main__':
    main()
