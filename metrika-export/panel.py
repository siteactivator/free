"""Панель в браузере: счётчики аккаунта с кнопками отчётов и списки готовых отчётов.

    python run.py panel                    → http://127.0.0.1:8790/

/                счётчики: у каждого кнопки «Сформировать SEO-отчёт» и «Сформировать
                 Директ» с прогрессом; готовый отчёт — ссылкой рядом и на своей вкладке
/seo/, /direct/  SEO-отчёты (поиск и весь трафик) и отчёты по Директу

Кнопки «Год к году», «Месяц к месяцу», «Квартал к кварталу» на странице отчёта
запускают тот же отчёт с --compare и открывают новый, когда он готов.

Отчёт строит тот же run.py в отдельном процессе: панель только запускает его и
читает вывод — по строкам с названиями листов считает прогресс. Одновременно —
не больше двух отчётов: у API Метрики лимит три параллельных запроса, остальные
ждут в очереди. Страница открывается только на этом компьютере (127.0.0.1).
"""
import html
import json
import os
import re
import subprocess
import sys
import threading
import time
import webbrowser
from collections import deque
from datetime import date, datetime
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import quote, urlparse

# вид: (вкладка, адрес, заголовок, префикс панели, префикс Excel, команда run.py)
KINDS = {
    'seo': ('SEO', '/seo/', 'SEO: отчёты', 'dashboard_seo_', 'seo_', 'seo'),
    'direct': ('Директ', '/direct/', 'Яндекс Директ: отчёты', 'dashboard_direct_', 'direct_', 'direct'),
}
BUTTONS = [('seo', 'Сформировать SEO-отчёт'), ('direct', 'Сформировать Директ')]
PARALLEL = 2                 # отчётов одновременно
DIRECT_CACHE_HOURS = 24      # как долго верить списку счётчиков с Директом

CSS = """
:root{--page:#f5f5f2;--surface:#fcfcfb;--ink:#0b0b0b;--ink2:#52514e;--muted:#898781;--line:#e1e0d9;
  --accent:#2a78d6;--good:#006300;--bad:#c0392b}
*{box-sizing:border-box}
body{margin:0;background:var(--page);color:var(--ink);font:15px/1.5 system-ui,-apple-system,"Segoe UI",sans-serif}
.wrap{max-width:1100px;margin:0 auto;padding:24px 16px 56px}
nav{display:flex;flex-wrap:wrap;gap:6px;margin:0 0 20px}
.tab{padding:6px 14px;border-radius:8px;color:var(--ink2);font-weight:600;border:1px solid var(--line);
  background:var(--surface);text-decoration:none}
.tab i{font-style:normal;font-weight:400;color:var(--muted);margin-left:6px}
.tab.on{background:var(--accent);border-color:var(--accent);color:#fff} .tab.on i{color:#dbe8f9}
h1{font-size:26px;font-weight:600;margin:0 0 4px} .sub{color:var(--ink2);margin:0 0 18px}
a{color:var(--accent);text-decoration:none} a:hover{text-decoration:underline}
ul{list-style:none;margin:0;padding:0;display:grid;gap:10px}
li{background:var(--surface);border:1px solid var(--line);border-radius:12px;padding:14px 16px}
li>span{display:block;color:var(--muted);font-size:13.5px} .site{font-size:18px;font-weight:600}
.empty{background:var(--surface);border:1px solid var(--line);border-radius:12px;padding:16px}
.tools{display:flex;gap:10px;align-items:center;margin:0 0 14px;flex-wrap:wrap}
#q{flex:1;min-width:220px;padding:9px 12px;border:1px solid var(--line);border-radius:10px;font:inherit;
  background:var(--surface)}
.tools label{color:var(--ink2);font-size:14px;white-space:nowrap;display:flex;align-items:center;gap:6px}
.ctr{display:grid;grid-template-columns:minmax(220px,1fr) auto;gap:14px;align-items:start}
.who b{font-size:17px;font-weight:600;word-break:break-all} .who span{display:block;color:var(--muted);font-size:13px}
.who .dmark{display:inline-block;margin-left:8px;font-size:11.5px;font-weight:600;color:var(--good);background:#e6f2e6;
  border-radius:6px;padding:1px 7px;vertical-align:2px}
.acts{display:flex;gap:12px;flex-wrap:wrap;justify-content:flex-end}
.act{width:250px}
.act button{width:100%;padding:8px 10px;border-radius:8px;border:1px solid var(--accent);background:#fff;
  color:var(--accent);font:600 13.5px system-ui,-apple-system,"Segoe UI",sans-serif;cursor:pointer}
.act button:hover{background:#eef4fc} .act button:disabled{border-color:var(--line);color:var(--muted);
  background:var(--page);cursor:default}
.bar{height:6px;border-radius:3px;background:var(--line);margin:7px 0 4px;overflow:hidden;display:none}
.bar i{display:block;height:100%;width:0;background:var(--accent);transition:width .6s}
.act.run .bar,.act.wait .bar{display:block} .act.wait .bar i{background:var(--muted)}
.act.err .bar{display:block} .act.err .bar i{background:var(--bad)}
.st{display:block;font-size:12.5px;color:var(--ink2);min-height:18px;overflow:hidden;text-overflow:ellipsis;
  white-space:nowrap} .act.err .st{color:var(--bad);white-space:normal}
.done{font-size:13px} .hidden{display:none}
@media (max-width:640px){.ctr{grid-template-columns:1fr}.acts{justify-content:flex-start}.act{width:100%}}
"""

JS = """
const POLL = 1500;
async function api(path, body) {
  const r = await fetch(path, body ? {method: 'POST', headers: {'Content-Type': 'application/json', 'X-Panel': '1'},
                                      body: JSON.stringify(body)} : {});
  return r.json();
}
function paint(el, job) {
  el.classList.remove('run', 'wait', 'err');
  const btn = el.querySelector('button'), st = el.querySelector('.st'), bar = el.querySelector('.bar i');
  const done = el.querySelector('.done');
  if (!job) return;
  if (job.state === 'queued') { el.classList.add('wait'); btn.disabled = true; st.textContent = 'в очереди…'; }
  if (job.state === 'running') { el.classList.add('run'); btn.disabled = true; st.textContent = job.status || ''; }
  if (job.state === 'error') { el.classList.add('err'); btn.disabled = false; st.textContent = job.error || 'ошибка'; }
  if (job.state === 'done') {
    btn.disabled = false; st.textContent = job.status || 'готово';
    if (job.link) done.innerHTML = '<a href="' + job.link + '">Открыть отчёт</a> · только что';
  }
  bar.style.width = Math.round((job.progress || 0) * 100) + '%';
}
let timer = null;
async function poll() {
  const jobs = await api('/api/jobs');
  let busy = false;
  document.querySelectorAll('.act').forEach(el => {
    const job = jobs[el.dataset.cid + ':' + el.dataset.kind + ':'];
    paint(el, job);
    if (job && (job.state === 'running' || job.state === 'queued')) busy = true;
  });
  clearTimeout(timer);
  if (busy) timer = setTimeout(poll, POLL);
}
document.addEventListener('click', async e => {
  const btn = e.target.closest('.act button');
  if (!btn) return;
  const el = btn.closest('.act');
  btn.disabled = true;
  el.querySelector('.st').textContent = 'запуск…';
  await api('/api/run', {cid: el.dataset.cid, kind: el.dataset.kind});
  poll();
});
const q = document.getElementById('q'), onlyDirect = document.getElementById('od');
function filter() {
  const t = q.value.trim().toLowerCase();
  document.querySelectorAll('.ctr').forEach(li => {
    const hit = !t || li.dataset.text.includes(t);
    const d = !onlyDirect.checked || li.classList.contains('hasdirect');
    li.classList.toggle('hidden', !(hit && d));
  });
}
q.addEventListener('input', filter); onlyDirect.addEventListener('change', filter);
async function marks() {
  const r = await api('/api/direct');
  (r.ids || []).forEach(id => {
    const li = document.getElementById('c' + id);
    if (li && !li.classList.contains('hasdirect')) {
      li.classList.add('hasdirect');
      li.querySelector('.who b').insertAdjacentHTML('afterend', '<span class="dmark">Директ подключён</span>');
    }
  });
  document.getElementById('dstate').textContent = r.ready ? '' : ' (проверяю, где подключён Директ…)';
  if (!r.ready) setTimeout(marks, 4000); else filter();
}
poll(); marks();
"""


def serve(a, core):
    """a — аргументы команды panel, core — модуль run.py (запросы к API, счётчики, порт)."""
    root = Path(a.data).resolve() if a.data else core.DATA
    root.mkdir(parents=True, exist_ok=True)
    jobs, lock, slots = {}, threading.Lock(), threading.Semaphore(PARALLEL)
    state = {'counters': None, 'counters_at': 0, 'direct': None}

    # ─── готовые отчёты ────────────────────────────────────────────────────
    def kind_of(name):
        return next((k for k in KINDS if name.startswith(KINDS[k][3])), None)

    def reports(kind):
        found = [p for p in root.glob('*/dashboard_*.html') if kind_of(p.name) == kind]
        return sorted(found, key=lambda f: f.stat().st_mtime, reverse=True)

    def link(p):
        return '/' + quote(f'{p.parent.name}/{p.name}')

    def latest_by_counter():
        """{(номер счётчика, вид): (ссылка, когда)} — последний отчёт каждого вида."""
        out = {}
        for p in sorted(root.glob('*/dashboard_*.html'), key=lambda f: f.stat().st_mtime):
            cid = p.parent.name.partition('_')[0]
            if kind_of(p.name):
                out[(cid, kind_of(p.name))] = (link(p), datetime.fromtimestamp(p.stat().st_mtime))
        return out

    # ─── счётчики и Директ ─────────────────────────────────────────────────
    def counters(refresh=False):
        if refresh or state['counters'] is None or time.time() - state['counters_at'] > 600:
            got = core.all_counters()
            got.sort(key=lambda c: ((c.get('site') or c.get('name') or '').lower(), c['id']))
            state['counters'], state['counters_at'] = got, time.time()
        return state['counters']

    def scan_direct():
        """Какие счётчики связаны с аккаунтами Директа, доступными токену. Пачками по
        10: пустая пачка — сразу дальше, в непустой — по одному. Результат — в кэш на сутки."""
        cache = root / '_direct_counters.json'
        try:
            j = json.loads(cache.read_text(encoding='utf-8'))
            if time.time() - j['at'] < DIRECT_CACHE_HOURS * 3600:
                state['direct'] = set(j['ids'])
                return
        except (OSError, ValueError, KeyError):
            pass
        try:
            ids = [str(c['id']) for c in counters()]
            found = []
            for i in range(0, len(ids), 10):
                batch = ids[i:i + 10]
                if core.api_get('/management/v1/clients', {'counters': ','.join(batch)}).get('clients'):
                    found += [cid for cid in batch
                              if core.api_get('/management/v1/clients', {'counters': cid}).get('clients')]
            state['direct'] = set(found)
            cache.write_text(json.dumps({'at': time.time(), 'ids': found}), encoding='utf-8')
        except BaseException as e:  # noqa: BLE001 — сбой проверки не должен ронять панель
            core.log(f'   проверка Директа не удалась: {type(e).__name__}: {e}')
            state['direct'] = set()

    # ─── запуск отчётов ────────────────────────────────────────────────────
    def titles(kind):
        traffic = [core.SPECIAL.get(k) or core.REPORTS[k][0] for k in core.ORDER]
        names = {'seo': list(core.SEO_TITLES.values()) + traffic,      # в SEO-отчёте и весь трафик
                 'direct': [core.DIRECT_SPECIAL.get(k) or core.DIRECT_REPORTS[k][0] for k in core.DIRECT_ORDER]}[kind]
        return set(names) | {'панель с графиками'}

    def run_job(key):
        cid, kind, mode = key
        job = jobs[key]
        with slots:
            job.update(state='running', status='запуск…', progress=0.02)
            steps, total, tail = 0, len(titles(kind)) + 1, deque(maxlen=6)
            env = dict(os.environ, METRIKA_DATA=str(root), PYTHONIOENCODING='utf-8', PYTHONDONTWRITEBYTECODE='1')
            try:
                cmd = [sys.executable, '-u', str(core.BASE / 'run.py'), KINDS[kind][5], cid]
                p = subprocess.Popen(cmd + (['--compare', mode] if mode else []),
                                     cwd=str(core.BASE), env=env, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                                     text=True, encoding='utf-8', errors='replace',
                                     creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
            except OSError as e:
                job.update(state='error', error=f'не запустился: {e}')
                return
            names = titles(kind)
            for line in p.stdout:
                text = line.strip()
                if not text:
                    continue
                tail.append(text)
                if text.endswith('…') and text[:-1] in names:
                    steps += 1
                    job['progress'] = min(0.97, 0.05 + steps / total)
                job['status'] = text.lstrip('✅⚠ ')[:180]
                m = re.match(r'✅ панель в браузере: (.+)$', text)
                if m and Path(m[1]).resolve().parent.parent == root.resolve():
                    job['link'] = link(Path(m[1]))
                m = re.match(r'✅ (листов: .+)$', text)
                if m:
                    job['summary'] = m[1]
            code = p.wait()
            if code == 0:
                job.update(state='done', progress=1.0, status='готово · ' + job.get('summary', ''),
                           finished=time.time())
            else:
                job.update(state='error', error=' / '.join(list(tail)[-2:]) or f'код выхода {code}')

    def start(cid, kind, mode=''):
        key = (cid, kind, mode)
        with lock:
            if key in jobs and jobs[key]['state'] in ('queued', 'running'):
                return jobs[key]
            jobs[key] = {'state': 'queued', 'progress': 0, 'status': 'в очереди…', 'started': time.time()}
        threading.Thread(target=run_job, args=(key,), daemon=True).start()
        return jobs[key]

    # ─── страницы ──────────────────────────────────────────────────────────
    def tabs(on):
        items = [('home', '/', 'Счётчики', len(state['counters'] or []))]
        items += [(k, v[1], v[0], len(reports(k))) for k, v in KINDS.items()]
        return '<nav>' + ''.join(f'<a class="tab{" on" if k == on else ""}" href="{url}">{name}<i>{n}</i></a>'
                                 for k, url, name, n in items) + '</nav>'

    def page(title, body):
        return (f'<!doctype html><html lang="ru"><head><meta charset="utf-8">'
                f'<meta name="viewport" content="width=device-width,initial-scale=1"><title>{title}</title>'
                f'<style>{CSS}</style></head><body><div class="wrap">{body}</div></body></html>')

    def home(refresh=False):
        try:
            items = counters(refresh)
        except BaseException as e:  # noqa: BLE001 — например, нет токена
            return page('Метрика: счётчики', tabs('home') + '<h1>Счётчики</h1>'
                        f'<p class="empty">Список счётчиков не получен: {html.escape(str(e))}</p>')
        last = latest_by_counter()
        rows = []
        for c in items:
            cid = str(c['id'])
            site = c.get('site') or c.get('name') or cid
            name = c.get('name') if c.get('name') and c.get('name') != site else ''
            access = core.PERMISSION.get(c.get('permission'), c.get('permission') or '')
            acts = []
            for kind, label in BUTTONS:
                prev = last.get((cid, kind))
                was = (f'<a href="{prev[0]}">Последний отчёт</a> · {prev[1]:%d.%m %H:%M}' if prev else '')
                acts.append(f'<div class="act" data-cid="{cid}" data-kind="{kind}"><button>{label}</button>'
                            f'<div class="bar"><i></i></div><span class="st"></span>'
                            f'<span class="done">{was}</span></div>')
            info = ' · '.join(x for x in (html.escape(name), f'№ {cid}', access) if x)
            text = html.escape(f'{site} {name} {cid}'.lower())
            rows.append(f'<li class="ctr" id="c{cid}" data-text="{text}"><div class="who"><b>{html.escape(site)}</b>'
                        f'<span>{info}</span></div><div class="acts">{"".join(acts)}</div></li>')
        body = (tabs('home') + '<h1>Счётчики</h1>'
                f'<p class="sub">Все счётчики, к которым есть доступ у токена: {len(items)}. Кнопка строит отчёт — '
                f'готовый появится ссылкой рядом и на своей вкладке. SEO-отчёт — поиск и весь трафик, 4 полные '
                f'недели к году назад; Директ — 14 дней к прошлым 14; на странице отчёта период можно '
                f'переключить: год, месяц, квартал. Только люди, без роботов.<span id="dstate"></span> '
                f'<a href="/?refresh=1">Обновить список</a></p>'
                '<div class="tools"><input id="q" placeholder="Поиск: сайт, название или номер счётчика" autofocus>'
                '<label><input type="checkbox" id="od"> только с Директом</label></div>'
                f'<ul>{"".join(rows)}</ul><script>{JS}</script>')
        return page('Метрика: счётчики', body)

    def listing(kind):
        tab, _, h1, head, xl, verb = KINDS[kind]
        cards = []
        for p in reports(kind):
            cid, _, site = p.parent.name.partition('_')
            m = re.search(r'_(\d{4}-\d{2}-\d{2})_(\d{4}-\d{2}-\d{2})\.html$', p.name)
            per = f'{date.fromisoformat(m[1]):%d.%m.%Y}–{date.fromisoformat(m[2]):%d.%m.%Y}' if m else ''
            made = datetime.fromtimestamp(p.stat().st_mtime).strftime('%d.%m.%Y %H:%M')
            xlsx = p.with_name(xl + p.name[len(head):]).with_suffix('.xlsx')
            excel = f' · <a href="/{quote(f"{p.parent.name}/{xlsx.name}")}">Excel</a>' if xlsx.is_file() else ''
            cards.append(f'<li><a class="site" href="{link(p)}">{html.escape(site)}</a>'
                         f'<span>{per} · счётчик {html.escape(cid)} · выгружено {made}{excel}</span></li>')
        body = (f'<ul>{"".join(cards)}</ul>' if cards else
                f'<p class="empty">Отчётов пока нет. Сформируйте на вкладке <a href="/">Счётчики</a> или командой '
                f'<code>python run.py {verb} &lt;номер счётчика или сайт&gt;</code>.</p>')
        return page(h1.replace('Яндекс ', ''), tabs(kind) + f'<h1>{h1}</h1>'
                    '<p class="sub">Все выгруженные отчёты, свежие сверху.</p>' + body)

    # ─── сервер ────────────────────────────────────────────────────────────
    class Handler(SimpleHTTPRequestHandler):
        def __init__(self, *args, **kw):
            super().__init__(*args, directory=str(root), **kw)   # наружу из папки отчётов не отдаёт

        def send(self, body, ctype='text/html; charset=utf-8', code=200):
            data = body.encode('utf-8')
            self.send_response(code)
            self.send_header('Content-Type', ctype)
            self.send_header('Content-Length', str(len(data)))
            self.send_header('Cache-Control', 'no-store')
            self.end_headers()
            self.wfile.write(data)

        def do_GET(self):
            u = urlparse(self.path)
            route = u.path.rstrip('/') or '/'
            if route in ('/', '/index.html'):
                return self.send(home('refresh=1' in u.query))
            for kind, (_, url, *_rest) in KINDS.items():
                if route == url.rstrip('/'):
                    return self.send(listing(kind))
            if route == '/api/jobs':
                with lock:
                    out = {f'{c}:{k}:{m}': {x: v for x, v in j.items() if x in
                                            ('state', 'progress', 'status', 'link', 'error')}
                           for (c, k, m), j in jobs.items()}
                return self.send(json.dumps(out, ensure_ascii=False), 'application/json; charset=utf-8')
            if route == '/api/direct':
                d = state['direct']
                return self.send(json.dumps({'ready': d is not None, 'ids': sorted(d or [])}),
                                 'application/json; charset=utf-8')
            return super().do_GET()

        def do_POST(self):
            # Свой заголовок и JSON: чужой сайт в браузере не сможет нажать кнопку за вас —
            # такой запрос браузер сначала спросит у сервера (preflight), а тот не ответит
            if urlparse(self.path).path != '/api/run' or self.headers.get('X-Panel') != '1':
                return self.send('{"error": "forbidden"}', 'application/json', 403)
            try:
                body = json.loads(self.rfile.read(int(self.headers.get('Content-Length') or 0)) or b'{}')
                cid, kind, mode = str(int(body['cid'])), body['kind'], body.get('mode') or ''
                assert kind in KINDS and mode in ('', 'yoy', 'mom', 'qoq')
            except (ValueError, KeyError, AssertionError, TypeError):
                return self.send('{"error": "bad request"}', 'application/json', 400)
            job = start(cid, kind, mode)
            return self.send(json.dumps({'state': job['state']}), 'application/json')

        def log_message(self, *args):            # без строки в терминале на каждый запрос
            pass

    try:
        srv = ThreadingHTTPServer(('127.0.0.1', a.port), Handler)
    except OSError as e:
        sys.exit(f'Порт {a.port} занят ({e}). Если панель уже запущена — откройте '
                 f'http://127.0.0.1:{a.port}/, иначе укажите другой порт: --port {a.port + 1}')
    threading.Thread(target=scan_direct, daemon=True).start()
    url = f'http://127.0.0.1:{a.port}/'
    core.log(f'✅ панель: {url}  (только на этом компьютере; остановить — Ctrl+C)')
    core.log(f'   счётчики и кнопки отчётов — на главной; отчёты: {url}seo/ · {url}direct/')
    core.log(f'   отчёты лежат в {root}')
    if not a.no_browser:
        webbrowser.open(url)
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        core.log('панель остановлена')
