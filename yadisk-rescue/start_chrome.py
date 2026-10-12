# -*- coding: utf-8 -*-
"""Открыть отдельное окно Chrome (или Яндекс Браузера / Edge) с портом отладки
и страницей Яндекс Диска. Профиль отдельный — ваш обычный браузер закрывать не нужно.

  python start_chrome.py                    открыть (или сказать, что уже открыт)
  python start_chrome.py --browser "C:\\путь\\к\\browser.exe"   свой браузер
  python start_chrome.py --check            только проверить, открыт ли

В открывшемся окне один раз войдите в свой аккаунт Яндекса и откройте Диск.
Вход запоминается в профиле ~/.yadisk-rescue/chrome-profile.
"""
import argparse, json, os, shutil, subprocess, sys, time, urllib.request

try:
    sys.stdout.reconfigure(encoding='utf-8')
except Exception:
    pass

HOME_DIR = os.environ.get('YADISK_RESCUE_HOME') or os.path.join(os.path.expanduser('~'), '.yadisk-rescue')
CONFIG = os.path.join(HOME_DIR, 'config.json')
PROFILE = os.path.join(HOME_DIR, 'chrome-profile')
DISK_URL = 'https://disk.yandex.ru/client/disk'


def port_from_config():
    try:
        with open(CONFIG, encoding='utf-8') as f:
            return json.load(f).get('port', 9222)
    except (OSError, ValueError):
        return 9222


def find_browser():
    if os.name == 'nt':
        pf = os.environ.get('ProgramFiles', r'C:\Program Files')
        pf86 = os.environ.get('ProgramFiles(x86)', r'C:\Program Files (x86)')
        local = os.environ.get('LOCALAPPDATA', '')
        cands = [
            os.path.join(pf, r'Google\Chrome\Application\chrome.exe'),
            os.path.join(pf86, r'Google\Chrome\Application\chrome.exe'),
            os.path.join(local, r'Google\Chrome\Application\chrome.exe'),
            os.path.join(local, r'Yandex\YandexBrowser\Application\browser.exe'),
            os.path.join(pf86, r'Microsoft\Edge\Application\msedge.exe'),
            os.path.join(pf, r'Microsoft\Edge\Application\msedge.exe'),
        ]
    elif sys.platform == 'darwin':
        cands = [
            '/Applications/Google Chrome.app/Contents/MacOS/Google Chrome',
            os.path.expanduser('~/Applications/Google Chrome.app/Contents/MacOS/Google Chrome'),
            '/Applications/Yandex.app/Contents/MacOS/Yandex',
            '/Applications/Microsoft Edge.app/Contents/MacOS/Microsoft Edge',
        ]
    else:
        cands = [shutil.which(n) for n in ('google-chrome', 'google-chrome-stable', 'chromium',
                                          'chromium-browser', 'yandex-browser', 'microsoft-edge')]
    for c in cands:
        if c and os.path.exists(c):
            return c
    return None


def tabs(port):
    try:
        with urllib.request.urlopen(f'http://127.0.0.1:{port}/json/list', timeout=3) as r:
            return [t for t in json.load(r) if t.get('type') == 'page']
    except Exception:
        return None


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--browser', help='путь к chrome.exe / browser.exe / msedge.exe')
    ap.add_argument('--port', type=int, default=None)
    ap.add_argument('--check', action='store_true', help='только проверить')
    a = ap.parse_args()
    port = a.port or port_from_config()

    t = tabs(port)
    if t is not None:
        disk = [x for x in t if 'disk.yandex.' in x['url'] and '/client/' in x['url']]
        print(f'Браузер с портом {port} уже открыт.')
        if disk:
            print(f'Вкладка Диска есть: {disk[0]["url"][:100]}')
            print('Дальше: python rescue.py ping')
        else:
            print(f'Вкладки Диска нет — откройте в этом окне {DISK_URL} и войдите в аккаунт.')
        return
    if a.check:
        sys.exit(f'Браузер с портом {port} не открыт. Запустите: python start_chrome.py')

    exe = a.browser or find_browser()
    if not exe:
        sys.exit('Не нашёл Chrome, Яндекс Браузер или Edge. Укажите путь: '
                 'python start_chrome.py --browser "путь к браузеру"')
    os.makedirs(PROFILE, exist_ok=True)
    args = [exe, f'--remote-debugging-port={port}', f'--user-data-dir={PROFILE}',
            '--no-first-run', '--no-default-browser-check', DISK_URL]
    kw = {}
    if os.name == 'nt':
        kw['creationflags'] = 0x00000008 | 0x00000200      # DETACHED_PROCESS | NEW_PROCESS_GROUP
    else:
        kw['start_new_session'] = True
    subprocess.Popen(args, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                     stderr=subprocess.DEVNULL, **kw)
    for _ in range(20):
        time.sleep(1)
        if tabs(port) is not None:
            break
    else:
        sys.exit(f'Браузер открылся, но порт {port} не отвечает. Закройте это окно и попробуйте '
                 f'другой порт: python rescue.py setup --port 9333, затем python start_chrome.py')
    print(f'Открыл {os.path.basename(exe)} с отдельным профилем ({PROFILE}).')
    print('1) В открывшемся окне войдите в свой аккаунт Яндекса (логин, пароль, код — как обычно).')
    print('2) Откройте Диск — должна быть видна страница с вашими папками.')
    print('3) Окно не закрывайте до конца работы. Дальше: python rescue.py ping')


if __name__ == '__main__':
    main()
