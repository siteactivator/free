"""Разбор видео-вычитки: запись экрана со звуком → журнал реплик с кадрами.

Вход — выгрузка Zoom (zip), папка с записью или сам видеофайл. Выход — в
data/<проект>/<дата>_<ЧЧММ>/ рядом с этим скриптом:

    video.mp4            исходник (из zip — распакованный)
    audio.mp3            звук для распознавания (моно 16 кГц)
    transcript.json      сегменты Whisper с абсолютным временем
    transcript.srt/.txt  то же для чтения
    scenes.json          моменты смены экрана (оценка сцены ffmpeg, 2 кадра/с)
    utterances.json      реплики: склеенные сегменты + кадры + смены экрана внутри
    frames/              u_<№>_<ммсс>.jpg — кадр на середине реплики,
                         s_<№>_<ммсс>.jpg — кадр после смены экрана
                         a_<№>_<ммсс>.jpg — кадр для листа адресов
    review.md            журнал для агента: время · текст · кадры
    urls_<N>.jpg         листы адресных строк браузера с таймкодами

Замечания из журнала агент фиксирует сам (remarks.md) — см. SKILL.md. Все
шаги кэшируются: повторный запуск ничего не пересчитывает, кроме того, что
попросили через --force.

Всё нужное лежит в этой папке: ключ Groq — в файле .env рядом со скриптом
(GROQ_API_KEY=...). Нужны ffmpeg/ffprobe и библиотеки из requirements.txt.
Подробная установка — README.md.

    python run.py "<zip|видео|папка>" --project my-site
    python run.py "<...>" --project my-site --force frames
"""
import argparse
import io
import json
import os
import re
import shutil
import subprocess
import sys
import time
import zipfile
from datetime import date
from pathlib import Path

# Консоль Windows по умолчанию не в UTF-8: без этого кириллица и значки в
# выводе роняют скрипт, если вывод перенаправлен в файл
for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding='utf-8', errors='replace')
    except (AttributeError, ValueError):
        pass

try:
    import requests
except ImportError:
    sys.exit('Нет библиотеки requests. Выполните в этой папке:\n'
             '  python -m pip install -r requirements.txt')

BASE = Path(__file__).resolve().parent               # папка скилла
ENV_PATH = BASE / '.env'
DATA = BASE / 'data'

# Groq Whisper: бесплатный тариф, OpenAI-совместимый метод
GROQ_URL = 'https://api.groq.com/openai/v1/audio/transcriptions'
GROQ_MODEL = 'whisper-large-v3-turbo'
MB = 1024 * 1024
GROQ_FILE_LIMIT = 25 * MB    # предел загрузки на бесплатном тарифе
MAX_RETRIES = 5              # повторы на 429: Groq присылает Retry-After

CHUNK_SEC = 20 * 60          # кусок звука, если файл не влезает в лимит провайдера
GAP_JOIN = 1.0               # пауза короче — сегменты одной реплики
MAX_UTTER = 25.0             # но реплика не длиннее, иначе кадр в середине ни о чём
VIDEO_EXT = {'.mp4', '.webm', '.mkv', '.mov'}   # что искать в переданной папке
SCENE_FPS = 2                # частота оценки смены экрана
SCENE_PAGE = 0.30            # весь кадр: выше — сменилась страница целиком
# Полоса браузера сверху (вкладки + адрес). Переход «белая страница → белая
# страница» по всему кадру почти не виден, а адресная строка меняется всегда.
# Откалибровано на записи окна Chrome 1440×932 из Zoom: порог 0.04 дал 21
# событие за 18 минут и поймал все переходы, которые весь кадр пропустил
NAV_STRIP = 0.13             # доля высоты кадра сверху
SCENE_NAV = 0.04
SCENE_MIN_GAP = 2.0          # смены ближе друг к другу — одна смена
# Строка адреса для листов urls_*.jpg: (левый край, верх, правый край, низ)
# в долях ширины и высоты кадра. Для другой раскладки — см. README
ADDR_BOX = (0.0, 0.055, 0.62, 0.125)
# Полоса адреса для листов снимается позже кадра смены экрана: медленная
# страница через секунду ещё показывает прежний адрес (так было на записи
# 23.09.2026: в 05:11 ещё /blog/seo, в 05:13 уже адрес статьи)
ADDR_DELAY = 5.0

# Whisper дописывает эти фразы на тишине и в конце записи — это не речь.
# «Корректор» — только с инициалом («Корректор А.Егорова»): само слово в
# вычитке статей звучит и всерьёз, а сегмент с ним отбрасывается целиком
HALLUCINATIONS = re.compile(
    r'субтитры (создавал|делал|сделал)|DimaTorzok|продолжение следует|'
    r'спасибо за просмотр|редактор субтитров|корректор\s+[А-ЯЁA-Z]\.', re.I)

# Подсказка Whisper: без неё «тайтл», «аш один», «сниппет» превращаются в кашу.
# Меняйте под свою тему; предел Groq — 224 токена (примерно 150 слов)
PROMPT = ('Вычитка статей блога по SEO. Термины: SEO, GEO, AEO, E-E-A-T, Title, '
          'Description, H1, H2, H3, сниппет, выдача, Яндекс, Google, Wordstat, '
          'семантическое ядро, перелинковка, анкор, CTA, FAQ, кейс, Тильда, '
          'нейросеть, ChatGPT, Claude, вайбкодинг, лендинг.')


def run(cmd, **kw):
    return subprocess.run(cmd, capture_output=True, text=True, encoding='utf-8',
                          errors='replace', **kw)


def mmss(t):
    t = int(round(t))
    return '%02d:%02d' % divmod(t, 60) if t < 3600 else '%d:%02d:%02d' % (
        t // 3600, (t % 3600) // 60, t % 60)


def tag(t):
    return mmss(t).replace(':', '')


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


def check_tools():
    """ffmpeg и ffprobe должны запускаться из командной строки."""
    missing = [t for t in ('ffmpeg', 'ffprobe') if not shutil.which(t)]
    if missing:
        sys.exit(f'Не найдено: {", ".join(missing)}.\n'
                 'Установите ffmpeg (README, шаг 2) и откройте терминал заново — '
                 'новый PATH видят только новые окна.')


# ─── 1. исходник ────────────────────────────────────────────────────────────
def session_name(src):
    """«2026-09-23 23.47.13 Zoom Meeting …» → «2026-09-23_2347»,
    «Запись встречи 25.09.2026 13_18_12.webm» (Телемост) → «2026-09-25_1318».

    Папка по одной дате не годится: две записи за день легли бы в одну, и
    вторая молча получила бы кэш первой."""
    name = Path(src).name
    m = re.search(r'(\d{4}-\d{2}-\d{2})[ _](\d{2})\.(\d{2})\.\d{2}', name)
    if m:
        return f'{m.group(1)}_{m.group(2)}{m.group(3)}'
    # Яндекс Телемост: «Запись встречи 25.09.2026 13_18_12.webm»
    m = re.search(r'(\d{2})\.(\d{2})\.(\d{4})[ _](\d{2})_(\d{2})_\d{2}', name)
    if m:
        return f'{m.group(3)}-{m.group(2)}-{m.group(1)}_{m.group(4)}{m.group(5)}'
    return None


def check_source(src, out):
    """Папка уже занята другой записью — стоп, а не чужой кэш."""
    meta = out / 'source.json'
    src = str(Path(src).resolve())
    if meta.exists():
        was = json.loads(meta.read_text(encoding='utf-8')).get('src')
        if was and was != src:
            sys.exit(f'Папка {out} уже занята другой записью:\n  {was}\n'
                     f'Передайте --date с другим именем, например --date 2026-09-23_2',)
    elif (out / 'video.mp4').exists():
        sys.exit(f'В {out} уже лежит video.mp4 неизвестного происхождения. '
                 f'Передайте --out с другим именем.')
    meta.write_text(json.dumps({'src': src}, ensure_ascii=False), encoding='utf-8')


def prepare(src, out):
    """Кладёт видео в out/video.mp4. Zip Zoom: recording.conf + video*.mp4."""
    src = Path(src)
    dst = out / 'video.mp4'
    if dst.exists():
        return dst
    if not src.exists():
        sys.exit(f'Файл не найден: {src}\nПуть с пробелами берите в кавычки.')
    out.mkdir(parents=True, exist_ok=True)
    if src.is_dir():
        vids = sorted((p for p in src.rglob('*') if p.suffix.lower() in VIDEO_EXT),
                      key=lambda p: -p.stat().st_size)
        if not vids:
            sys.exit(f'В папке нет видео ({", ".join(sorted(VIDEO_EXT))}): {src}')
        # webm Телемоста, mkv OBS, mov macOS — ffmpeg читает содержимое, имя ему не важно
        shutil.copyfile(vids[0], dst)
    elif src.suffix.lower() == '.zip':
        z = zipfile.ZipFile(src)
        vids = sorted((i for i in z.infolist() if i.filename.lower().endswith('.mp4')),
                      key=lambda i: -i.file_size)
        if not vids:
            # Zoom ещё конвертирует или облако не догрузило — в архиве один conf
            sys.exit(f'В архиве нет .mp4, только {[i.filename for i in z.infolist()]}.\n'
                     'Zoom ещё не собрал видео или облако не догрузило его — '
                     'дождитесь video*.mp4 в папке записи и выгрузите заново.')
        with z.open(vids[0]) as f, open(dst, 'wb') as o:
            shutil.copyfileobj(f, o)
        for i in z.infolist():                      # recording.conf — для истории
            if i.filename.endswith('recording.conf'):
                (out / 'recording.conf').write_bytes(z.read(i))
    else:
        # mp4, mkv, mov — ffmpeg читает содержимое, расширение ему не важно
        shutil.copyfile(src, dst)
    print(f'✅ видео: {dst}')
    return dst


def probe(video):
    r = run(['ffprobe', '-v', 'error', '-show_entries', 'format=duration:stream=codec_type,'
             'width,height', '-of', 'json', str(video)])
    if r.returncode:
        sys.exit('ffprobe не прочитал видео — файл повреждён или это не видео:\n'
                 + r.stderr[-800:])
    info = json.loads(r.stdout)
    dur = float(info['format']['duration'])
    v = next((s for s in info['streams'] if s['codec_type'] == 'video'), {})
    has_audio = any(s['codec_type'] == 'audio' for s in info['streams'])
    return dur, v.get('width'), v.get('height'), has_audio


# ─── 2. звук и распознавание ────────────────────────────────────────────────
def extract_audio(video, out, dur):
    """Моно 16 кГц 48 кбит/с — речь читается, час ≈ 21 МБ. Длиннее — куски."""
    audio = out / 'audio.mp3'
    if not audio.exists():
        r = run(['ffmpeg', '-y', '-v', 'error', '-i', str(video), '-vn', '-ac', '1',
                 '-ar', '16000', '-b:a', '48k', str(audio)])
        if r.returncode:
            sys.exit('ffmpeg не извлёк звук:\n' + r.stderr[-800:])
    if audio.stat().st_size <= GROQ_FILE_LIMIT - MB:
        return [(audio, 0.0)]
    parts = []
    t = 0.0
    while t < dur:
        p = out / f'audio_{int(t):05d}.mp3'
        if not p.exists():
            run(['ffmpeg', '-y', '-v', 'error', '-ss', str(t), '-t', str(CHUNK_SEC),
                 '-i', str(audio), '-c', 'copy', str(p)])
        parts.append((p, t))
        t += CHUNK_SEC
    return parts


def post_with_retry(headers, files, data):
    """POST в Groq с ожиданием на 429 (Groq присылает Retry-After)."""
    for attempt in range(MAX_RETRIES + 1):
        for _, fh in files.values():               # файл перечитывается с начала
            fh.seek(0)
        try:
            resp = requests.post(GROQ_URL, headers=headers, files=files, data=data,
                                 timeout=600)
        except requests.exceptions.RequestException as e:
            if attempt < MAX_RETRIES:
                print(f'   сеть: {type(e).__name__}, повтор через {2 ** attempt} с…', flush=True)
                time.sleep(2 ** attempt)
                continue
            sys.exit(f'Groq недоступен: {e}\nПроверьте интернет и что console.groq.com '
                     f'открывается в браузере.')
        if resp.status_code == 429 and attempt < MAX_RETRIES:
            wait = float(resp.headers.get('Retry-After', 0)) or 2 ** attempt
            print(f'   лимит Groq, жду {wait:.0f} с…', flush=True)
            time.sleep(wait)
            continue
        return resp
    return resp


def recognize(parts, out, language):
    """Сегменты Whisper с абсолютным временем. Кэш — transcript.json."""
    tj = out / 'transcript.json'
    if tj.exists():
        return json.loads(tj.read_text(encoding='utf-8'))
    load_env(ENV_PATH)
    key = os.environ.get('GROQ_API_KEY')
    if not key:
        sys.exit(f'GROQ_API_KEY не найден. Создайте файл {ENV_PATH}\n'
                 f'со строкой GROQ_API_KEY=ваш_ключ (образец — .env.example, README, шаг 4).')
    segs = []
    for path, offset in parts:
        print(f'   распознаю {path.name} (с {mmss(offset)})…', flush=True)
        data = {'model': GROQ_MODEL, 'response_format': 'verbose_json', 'prompt': PROMPT}
        if language:
            data['language'] = language
        with open(path, 'rb') as fh:
            resp = post_with_retry({'Authorization': f'Bearer {key}'},
                                   {'file': (path.name, fh)}, data)
        if resp.status_code == 401:
            sys.exit('Groq 401: ключ не подошёл. Создайте новый на '
                     'console.groq.com/keys и замените его в .env.')
        if resp.status_code >= 400:
            sys.exit(f'Groq {resp.status_code}: {resp.text[:400]}')
        for s in resp.json().get('segments', []):
            txt = s.get('text', '').strip()
            if txt and not HALLUCINATIONS.search(txt):
                segs.append({'start': round(s['start'] + offset, 2),
                             'end': round(s['end'] + offset, 2), 'text': txt})
    tj.write_text(json.dumps(segs, ensure_ascii=False, indent=1), encoding='utf-8')
    (out / 'transcript.txt').write_text(
        '\n'.join(f"[{mmss(s['start'])}] {s['text']}" for s in segs), encoding='utf-8')
    srt = []
    for i, s in enumerate(segs, 1):
        f = lambda x: '%02d:%02d:%02d,%03d' % (x // 3600, x % 3600 // 60, x % 60,
                                               round((x % 1) * 1000))
        srt.append(f"{i}\n{f(s['start'])} --> {f(s['end'])}\n{s['text']}\n")
    (out / 'transcript.srt').write_text('\n'.join(srt), encoding='utf-8')
    print(f'✅ распознано сегментов: {len(segs)}')
    return segs


# ─── 3. смены экрана ────────────────────────────────────────────────────────
def scene_scores(video, out, name, crop=''):
    """Оценки сцены ffmpeg на SCENE_FPS кадрах в секунду: [(время, оценка)]."""
    log = out / f'_scores_{name}.txt'
    if not log.exists():
        # путь к файлу — внутри фильтра, двоеточия надо экранировать
        fpath = str(log).replace('\\', '/').replace(':', r'\:')
        vf = f"fps={SCENE_FPS},{crop + ',' if crop else ''}select='gte(scene\\,0)'," \
             f"metadata=print:file='{fpath}'"
        r = run(['ffmpeg', '-v', 'error', '-i', str(video), '-vf', vf, '-an', '-f', 'null', '-'])
        if r.returncode or not log.exists():
            sys.exit('ffmpeg не посчитал смены экрана:\n' + r.stderr[-800:])
    scores, t = [], None
    for line in io.open(log, encoding='utf-8', errors='replace'):
        m = re.search(r'pts_time:([\d.]+)', line)
        if m:
            t = float(m.group(1))
            continue
        m = re.search(r'scene_score=([\d.]+)', line)
        if m and t is not None:
            scores.append((t, float(m.group(1))))
    return scores


def detect_scenes(video, out):
    """Два детектора: смена всего кадра (страница) и полосы браузера (переход).

    Моменты ближе SCENE_MIN_GAP склеиваются; «страница» важнее «перехода»."""
    sj = out / 'scenes.json'
    if sj.exists():
        return json.loads(sj.read_text(encoding='utf-8'))
    raw = [(t, sc, 'страница') for t, sc in scene_scores(video, out, 'full')
           if sc >= SCENE_PAGE]
    raw += [(t, sc, 'переход') for t, sc in scene_scores(
            video, out, 'nav', f'crop=iw:ih*{NAV_STRIP}:0:0') if sc >= SCENE_NAV]
    raw.sort()
    scenes = []
    for t, sc, kind in raw:
        if scenes and t - scenes[-1]['t'] < SCENE_MIN_GAP:
            if kind == 'страница':
                scenes[-1]['kind'] = kind
            scenes[-1]['score'] = max(scenes[-1]['score'], round(sc, 3))
            continue
        scenes.append({'t': round(t, 2), 'score': round(sc, 3), 'kind': kind})
    sj.write_text(json.dumps(scenes, ensure_ascii=False, indent=1), encoding='utf-8')
    n_page = sum(s['kind'] == 'страница' for s in scenes)
    print(f'✅ смен экрана: {len(scenes)} (страница {n_page}, переход {len(scenes) - n_page})')
    return scenes


# ─── 4. реплики и кадры ─────────────────────────────────────────────────────
def build_utterances(segs):
    out, cur = [], None
    for s in segs:
        if HALLUCINATIONS.search(s['text']):      # кэш старых прогонов
            continue
        if cur and s['start'] - cur['end'] < GAP_JOIN and s['end'] - cur['start'] <= MAX_UTTER:
            cur['end'] = s['end']
            cur['text'] += ' ' + s['text']
        else:
            cur = dict(s)
            out.append(cur)
    return out


def grab(video, t, path, width):
    if path.exists():
        return
    vf = f'scale={width}:-2' if width else 'null'
    run(['ffmpeg', '-y', '-v', 'error', '-ss', f'{max(t, 0):.2f}', '-i', str(video),
         '-frames:v', '1', '-vf', vf, '-q:v', '3', str(path)])


def frames(video, out, utt, scenes, dur, width, force=False):
    fd = out / 'frames'
    if force and fd.exists():
        # Только свои кадры: x_*.jpg агент снимает вручную, их не трогаем
        for p in [p for pref in ('u_', 's_', 'a_') for p in fd.glob(pref + '*.jpg')]:
            p.unlink()
    fd.mkdir(exist_ok=True)
    for i, s in enumerate(scenes, 1):
        # сам момент смены — переход; через секунду страница уже отрисована
        t = min(s['t'] + 1.0, dur - 0.1)
        s['frame'] = f'frames/s_{i:03d}_{tag(t)}.jpg'
        grab(video, t, out / s['frame'], width)
        # для листа адресов — перед следующей сменой, но не позже ADDR_DELAY
        nxt = scenes[i]['t'] if i < len(scenes) else dur
        ta = max(t, min(s['t'] + ADDR_DELAY, nxt - 0.3, dur - 0.1))
        s['addr_frame'] = f'frames/a_{i:03d}_{tag(ta)}.jpg'
        grab(video, ta, out / s['addr_frame'], width)
    for i, u in enumerate(utt, 1):
        u['n'] = i
        mid = (u['start'] + u['end']) / 2
        u['frame'] = f'frames/u_{i:03d}_{tag(mid)}.jpg'
        grab(video, mid, out / u['frame'], width)
        u['scenes'] = [s for s in scenes if u['start'] <= s['t'] <= u['end']]
    return utt


def url_sheets(out, scenes, per_sheet=20):
    """Листы из адресных строк: какой URL был на экране после каждой смены.

    Статью узнаём по адресу, а открывать ради этого полсотни полных кадров —
    дорого. Область строки адреса — ADDR_BOX."""
    try:
        from PIL import Image, ImageDraw, ImageFont
    except ImportError:
        print('   (Pillow нет — листы адресов пропущены: python -m pip install Pillow)')
        return []
    try:
        font = ImageFont.truetype('arial.ttf', 20)
    except OSError:
        font = ImageFont.load_default()
    x0, y0, x1, y1 = ADDR_BOX
    sheets = []
    for k in range(0, len(scenes), per_sheet):
        chunk = scenes[k:k + per_sheet]
        strips = []
        for s in chunk:
            im = Image.open(out / s.get('addr_frame', s['frame']))
            w, h = im.size
            strips.append(im.crop((int(w * x0), int(h * y0), int(w * x1), int(h * y1))))
        lab = 150
        sw = max(i.size[0] for i in strips)
        sh = sum(i.size[1] for i in strips) + 2 * len(strips)
        sheet = Image.new('RGB', (sw + lab, sh), 'white')
        d = ImageDraw.Draw(sheet)
        y = 0
        for n, (s, im) in enumerate(zip(chunk, strips), k + 1):
            d.text((6, y + im.size[1] // 2 - 11), f'{n:>2}. {mmss(s["t"])}', fill='black', font=font)
            sheet.paste(im, (lab, y))
            y += im.size[1]
            d.line((0, y, sw + lab, y), fill='#cccccc', width=2)
            y += 2
        p = out / f'urls_{k // per_sheet + 1}.jpg'
        sheet.save(p, quality=88)
        sheets.append(p)
    return sheets


# ─── 5. журнал ──────────────────────────────────────────────────────────────
def write_review(out, src, dur, wh, utt, scenes):
    (out / 'utterances.json').write_text(json.dumps(utt, ensure_ascii=False, indent=1),
                                         encoding='utf-8')
    L = [f'# Разбор видео — {out.parent.name}, {out.name}', '',
         f'Исходник: `{src}`', '',
         f'Длительность {mmss(dur)} · экран {wh[0]}×{wh[1]} · реплик {len(utt)} · '
         f'смен экрана {len(scenes)}', '',
         'Каждая реплика — время, текст, кадр на её середине. «Смена экрана» — '
         'момент, когда сменилась страница целиком или только полоса браузера '
         '(другая вкладка, адрес); кадр снят через секунду после неё.', '']
    events = [('u', u['start'], u) for u in utt] + [('s', s['t'], s) for s in scenes]
    events.sort(key=lambda e: (e[1], e[0] == 'u'))
    si = 0
    for kind, t, e in events:
        if kind == 's':
            si += 1
            L += [f'### ▸ {si}. {e.get("kind", "смена экрана")} — {mmss(t)}',
                  f'`{e["frame"]}`', '']
        else:
            L += [f'**{e["n"]}. {mmss(e["start"])}–{mmss(e["end"])}**',
                  f'> {e["text"]}', f'`{e["frame"]}`', '']
    (out / 'review.md').write_text('\n'.join(L) + '\n', encoding='utf-8')
    return out / 'review.md'


def main():
    ap = argparse.ArgumentParser(description='Видео-вычитка → журнал реплик с кадрами')
    ap.add_argument('src', help='zip выгрузки Zoom, папка с записью или видеофайл')
    ap.add_argument('--project', default='my-site',
                    help='папка проекта внутри data/ (по умолчанию my-site)')
    ap.add_argument('--date', help='имя папки записи; по умолчанию дата и время '
                                   'из имени Zoom («2026-09-23_2347»), иначе сегодня')
    ap.add_argument('--out', help='своя папка вместо data/<проект>/<дата>')
    ap.add_argument('--language', default='ru', help='язык речи, ISO-код (ru, en, uk…)')
    ap.add_argument('--width', type=int, default=0,
                    help='ширина кадров, px (0 — как в записи; текст статьи должен читаться)')
    ap.add_argument('--force', nargs='*', default=[],
                    choices=['transcript', 'scenes', 'frames'],
                    help='пересчитать шаг заново')
    a = ap.parse_args()

    check_tools()
    name = a.date or session_name(a.src) or date.today().isoformat()
    out = Path(a.out) if a.out else DATA / a.project / name
    out.mkdir(parents=True, exist_ok=True)
    check_source(a.src, out)
    print(f'   папка: {out}')
    for step, fname in (('transcript', 'transcript.json'), ('scenes', 'scenes.json')):
        if step in a.force and (out / fname).exists():
            (out / fname).unlink()

    video = prepare(a.src, out)
    dur, w, h, has_audio = probe(video)
    print(f'   {mmss(dur)} · {w}×{h} · звук: {"есть" if has_audio else "НЕТ"}')
    if not has_audio:
        sys.exit('В видео нет звуковой дорожки. У Zoom звук бывает отдельным '
                 'audio*.m4a — положите его рядом и передайте папку. В OBS проверьте, '
                 'что включён источник «Микрофон».')

    parts = extract_audio(video, out, dur)
    segs = recognize(parts, out, a.language)
    scenes = detect_scenes(video, out)
    utt = build_utterances(segs)
    utt = frames(video, out, utt, scenes, dur, a.width, force='frames' in a.force)
    review = write_review(out, a.src, dur, (w, h), utt, scenes)
    sheets = url_sheets(out, scenes)
    if sheets:
        print(f'✅ листы адресов: {", ".join(p.name for p in sheets)}')

    words = sum(len(u['text'].split()) for u in utt)
    print(f'✅ реплик: {len(utt)} · слов: {words} · кадров: {len(utt) + len(scenes)}')
    print(f'✅ журнал: {review}')
    print(f'\nДальше — шаг агента: откройте Claude Code в папке {BASE}\n'
          f'и попросите разобрать запись {out} по SKILL.md (README, шаг 8).')


if __name__ == '__main__':
    main()
