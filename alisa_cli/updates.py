"""Opt-in release installation, with a startup offer and no background apply."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import platform
import re
import shutil
import sys
import tarfile
import tempfile
import time
import urllib.error
import urllib.parse
import urllib.request
import zipfile

from . import __version__, app_install, ui
from .state import HOME, ROOT, load, write_private

REPOSITORY = 'rison1337/Alisa-ChatGPT-voice-assistant'
API = 'https://api.github.com/repos/' + REPOSITORY + '/releases/latest'
ASSETS = {'linux-x86_64': ('alisa-voice-linux.tar.gz', 'alisa-voice-linux'),
          'windows-amd64': ('alisa-voice-windows.zip', 'alisa-voice-windows')}
MAX_ARCHIVE = 240 * 1024 * 1024
MAX_EXPANDED = 600 * 1024 * 1024
CACHE_SECONDS = 6 * 3600


class UpdateScheduled(Exception):
    """Unwind every terminal UI before replacing the current process."""
    def __init__(self, executable, arguments, directory, relaunch=False):
        super().__init__('update scheduled')
        self.executable, self.arguments, self.directory = executable, arguments, directory
        self.relaunch = relaunch


def target_platform():
    machine = platform.machine().lower()
    if sys.platform == 'win32' and machine in ('amd64', 'x86_64'):
        return 'windows-amd64'
    if sys.platform == 'linux' and machine in ('x86_64', 'amd64'):
        return 'linux-x86_64'
    raise RuntimeError('Обновление поддерживает Windows x64 и Linux x86_64.')


def version_key(value):
    if not isinstance(value, str):
        raise RuntimeError('Неизвестный формат версии релиза.')
    match = re.fullmatch(r'v?(\d+)\.(\d+)\.(\d+)(?:[.-](dev|rc|beta|alpha)[.-]?(\d*))?', value or '')
    if not match:
        raise RuntimeError('Неизвестный формат версии релиза.')
    return (*map(int, match.group(1, 2, 3)), {None:4,'dev':0,'alpha':1,'beta':2,'rc':3}[match.group(4)], int(match.group(5) or 0))


def preferences():
    try:
        value = json.loads((HOME / 'updates.json').read_text(encoding='utf-8'))
        return {'ask_on_start': value.get('ask_on_start') is not False}
    except (OSError, ValueError, AttributeError):
        return {'ask_on_start': True}


def set_startup(enabled):
    write_private(HOME / 'updates.json', {'version': 1, 'ask_on_start': bool(enabled)})


def request(url, limit, timeout):
    req = urllib.request.Request(url, headers={'User-Agent': 'Alisa-Voice-Updater', 'Accept': 'application/vnd.github+json'})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as response:
            data = response.read(limit + 1)
    except (OSError, urllib.error.URLError) as error:
        raise RuntimeError('Не удалось связаться с GitHub. Программа продолжит работать с текущей версией.') from error
    if len(data) > limit:
        raise RuntimeError('Ответ сервера обновлений превышает допустимый размер.')
    return data


def release_url(url, name):
    if not isinstance(url, str):
        raise RuntimeError('GitHub вернул неизвестный адрес пакета обновления.')
    parsed = urllib.parse.urlsplit(url)
    prefix = '/' + REPOSITORY + '/releases/download/'
    if (parsed.scheme != 'https' or parsed.netloc != 'github.com' or parsed.query or parsed.fragment
            or not parsed.path.startswith(prefix) or parsed.path.rsplit('/', 1)[-1] != name):
        raise RuntimeError('GitHub вернул неизвестный адрес пакета обновления.')
    return url


def check(force=False):
    target = target_platform()
    cache = HOME / 'updates' / 'release-cache.json'
    if not force:
        try:
            saved = json.loads(cache.read_text(encoding='utf-8'))
            if saved['target'] == target and 0 <= time.time() - saved['checked_at'] < CACHE_SECONDS:
                value = saved['release']
                # Cached content is validated below exactly like a fresh reply.
            else:
                value = None
        except (OSError, ValueError, KeyError, TypeError):
            value = None
    else:
        value = None
    if value is None:
        try:
            value = json.loads(request(API, 1024 * 1024, 3))
        except (ValueError, UnicodeError) as error:
            raise RuntimeError('Не удалось прочитать сведения о новой версии.') from error
        write_private(cache, {'target': target, 'checked_at': time.time(), 'release': value})
    if not isinstance(value, dict) or value.get('draft') or value.get('prerelease'):
        raise RuntimeError('Сервер не вернул стабильный релиз.')
    tag = value.get('tag_name', '')
    newer = version_key(tag) > version_key(__version__)
    name = ASSETS[target][0]
    items = value.get('assets', [])
    if not isinstance(items, list):
        raise RuntimeError('Не удалось прочитать список пакетов релиза.')
    assets = {item['name']: item for item in items if isinstance(item, dict) and isinstance(item.get('name'), str)}
    urls = {}
    for filename in (name, name + '.sha256'):
        if filename in assets:
            urls[filename] = release_url(assets[filename].get('browser_download_url', ''), filename)
    available = newer and len(urls) == 2
    return {'current_version': __version__, 'latest_version': tag.removeprefix('v'), 'available': available,
            'platform': target, 'asset': name, 'urls': urls, 'ask_on_start': preferences()['ask_on_start'],
            'message': ('Доступна новая версия.' if available else 'Для этой системы пакет новой версии ещё не опубликован.'
                        if newer else 'Установлена актуальная версия.')}


def download(url, destination):
    req = urllib.request.Request(url, headers={'User-Agent': 'Alisa-Voice-Updater'})
    start = time.monotonic()
    total = 0
    try:
        with urllib.request.urlopen(req, timeout=20) as response, destination.open('wb') as output:
            while True:
                chunk = response.read(1024 * 1024)
                if not chunk:
                    break
                total += len(chunk)
                if total > MAX_ARCHIVE or time.monotonic() - start > 180:
                    raise RuntimeError('Загрузка пакета превысила допустимый размер или время.')
                output.write(chunk)
    except (OSError, urllib.error.URLError) as error:
        raise RuntimeError('Не удалось загрузить обновление. Прежняя программа сохранена.') from error


def checksum(text):
    fields = text.strip().split()
    if not fields or not re.fullmatch('[0-9a-fA-F]{64}', fields[0]):
        raise RuntimeError('Некорректная контрольная сумма релиза.')
    return fields[0].lower()


def unpack(archive, destination, target):
    """Reject links, duplicate/escaping paths and oversized archives before writing."""
    prefix = ASSETS[target][1]
    files = []
    seen = set()
    expanded = 0
    with (zipfile.ZipFile(archive) if target == 'windows-amd64' else tarfile.open(archive, 'r:gz')) as bundle:
        members = bundle.infolist() if target == 'windows-amd64' else bundle.getmembers()
        if len(members) > 12000:
            raise RuntimeError('Слишком много файлов в архиве обновления.')
        for item in members:
            name = item.filename.rstrip('/') if target == 'windows-amd64' else item.name.rstrip('/')
            path = app_install.relative_name(name)
            if path.parts[0] != prefix or name in seen:
                raise RuntimeError('Недопустимый или повторный путь в архиве обновления.')
            seen.add(name)
            if target == 'windows-amd64':
                mode = item.external_attr >> 16
                directory = item.is_dir()
                if item.flag_bits & 1 or mode & 0o170000 not in (0, 0o040000, 0o100000):
                    raise RuntimeError('Архив обновления содержит ссылку или специальный файл.')
                size = item.file_size
            else:
                mode, directory, size = item.mode, item.isdir(), item.size
                if not (item.isdir() or item.isfile()):
                    raise RuntimeError('Архив обновления содержит ссылку или специальный файл.')
            expanded += size
            if expanded > MAX_EXPANDED:
                raise RuntimeError('Распакованный пакет превышает допустимый размер.')
            files.append((item, name, directory, mode))
        if shutil.disk_usage(destination).free < expanded + 16 * 1024 * 1024:
            raise RuntimeError('Недостаточно места для распаковки обновления.')
        for item, name, directory, mode in files:
            path = destination / name
            if directory:
                path.mkdir(parents=True, exist_ok=True, mode=0o700)
                continue
            path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
            with (bundle.open(item) if target == 'windows-amd64' else bundle.extractfile(item)) as stream, path.open('xb') as output:
                shutil.copyfileobj(stream, output)
            if target != 'windows-amd64':
                path.chmod(0o700 if mode & 0o111 else 0o600)
    return destination / prefix


def installed_root(target):
    destination = (Path(os.environ.get('LOCALAPPDATA', Path.home() / 'AppData/Local')) / 'AlisaVoice/app'
                   if target == 'windows-amd64' else Path.home() / '.local/share/alisa-voice')
    if (ROOT / '.git').exists() or ROOT.resolve() != destination.resolve() or not app_install.owned(ROOT, target):
        raise RuntimeError('Запущена копия исходников. Установите готовый пакет, чтобы обновлять программу через CLI; исходники не изменены.')
    return destination


def mode_plan():
    from . import runtime
    plans = []
    for mode in ('native', 'bridge'):
        if mode == 'bridge' and sys.platform == 'win32':
            continue
        config = load(mode)
        if not config.get('installed'):
            continue
        state = runtime.status(config)
        if state.get('voice_active') or state.get('phase') in ('active', 'connecting', 'opening', 'starting', 'stopping'):
            raise RuntimeError('Сначала закройте голосовой чат и дождитесь готовности Алисы. Обновление не началось.')
        plans.append({'mode': mode, 'running': bool(state.get('agent_connected')) if mode == 'native' else state.get('phase') == 'running'})
    return plans


def prepare(value, archive=None):
    target = value['platform']
    current = installed_root(target)
    if target == 'windows-amd64' and os.name == 'nt' and not os.environ.get('ALISA_WINDOWS_HANDOFF'):
        raise RuntimeError('Для обновления в Windows запустите установленный alisa.cmd. Данные и программа не изменены.')
    folder = HOME / 'updates'
    folder.mkdir(parents=True, exist_ok=True, mode=0o700)
    job = Path(tempfile.mkdtemp(prefix='install-', dir=folder))
    if os.name == 'nt':
        from .windows import private_directory
        private_directory(job)
    try:
        if archive is None:
            archive = job / value['asset']
            download(value['urls'][value['asset']], archive)
            expected = checksum(request(value['urls'][value['asset'] + '.sha256'], 4096, 10).decode('ascii'))
        else:
            archive = Path(archive).resolve()
            expected = checksum(Path(str(archive) + '.sha256').read_text(encoding='ascii'))
        if archive.stat().st_size > MAX_ARCHIVE:
            raise RuntimeError('Архив обновления превышает допустимый размер.')
        if app_install.file_hash(archive) != expected:
            raise RuntimeError('SHA-256 обновления не совпал. Установка не началась.')
        try:
            payload = unpack(archive, job, target)
        except (zipfile.BadZipFile, tarfile.TarError, EOFError) as error:
            raise RuntimeError('Не удалось распаковать обновление. Прежняя программа сохранена.') from error
        app_install.validate(payload, target, value['latest_version'], strict=True)
        plans = mode_plan()
        # Run the worker outside the installed directory. On Windows this also
        # releases the old embedded python.exe before its folder is renamed.
        executable = str(payload / 'windows/runtime/python.exe') if target == 'windows-amd64' else sys.executable
        worker = payload / 'scripts/update_worker.py'
        if not worker.is_file():
            raise RuntimeError('Пакет не содержит программу обновления.')
        supervised = target == 'windows-amd64' and bool(os.environ.get('ALISA_WINDOWS_HANDOFF'))
        relaunch = ui.interactive()
        specification = {'schema': 1, 'current_root': str(current), 'payload': str(payload), 'platform': target,
                         'version': value['latest_version'], 'parent_pid': os.getpid(), 'modes': plans,
                         'state_directory': str(HOME), 'relaunch': relaunch and not supervised,
                         'downloaded_archive': archive.name if archive.parent == job else None,
                         'archive_sha256': expected}
        write_private(job / 'job.json', specification)
        return UpdateScheduled(executable, [executable, '-B', '-X', 'utf8', str(worker), str(job / 'job.json')], str(job), relaunch=relaunch and supervised)
    except BaseException:
        shutil.rmtree(job)
        raise


def offer(value, startup=False, archive=None):
    if not value['available']:
        if not startup:
            ui.note(value['message'])
        return False
    choices = [('Обновить', 'apply'), ('Не сейчас', 'later'), ('Больше не спрашивать', 'never')]
    answer = ui.select('Доступна Alisa Voice ' + value['latest_version'] + ' (сейчас ' + value['current_version'] + ')', choices, default='later')
    if answer == 'never':
        set_startup(False)
        ui.note('Предложения при запуске отключены. Проверить обновления можно в меню обслуживания.')
    elif answer == 'apply':
        with ui.activity('Загружаю и проверяю обновление'):
            scheduled = prepare(value, archive=archive)
        ui.note('Обновляю программу и установленные компоненты. Настройки, вход и чаты сохраняются.')
        raise scheduled
    return False


def startup_offer():
    if not preferences()['ask_on_start'] or (ROOT / '.git').exists():
        return
    try:
        value = check()
    except (RuntimeError, OSError, ValueError, KeyError, TypeError):
        return  # Network availability must never prevent using the installed CLI.
    try:
        offer(value, startup=True)
    except ui.Back:
        return
    except (OSError, ValueError, RuntimeError) as error:
        ui.note(str(error), 'yellow')


def show_result():
    path = HOME / 'update-status.json'
    try:
        value = json.loads(path.read_text(encoding='utf-8'))
        if not isinstance(value, dict):
            return
        if value.get('notice_seen') or value.get('phase') == 'starting':
            return
        ui.note(value.get('message', 'Обновление завершено.'), 'green' if value.get('phase') == 'complete' else 'yellow')
        if value.get('rollback_failures'):
            ui.note('Требуется проверка компонентов: ' + ', '.join(value['rollback_failures']), 'yellow')
        value['notice_seen'] = True
        write_private(path, value)
    except (OSError, ValueError, TypeError):
        return


def clean_downloads():
    """Remove only unchanged generated payload files after the worker has exited.

    Backups of the installed application and unknown/modified files are kept.
    A locked Windows DLL is retried on a later CLI launch.
    """
    folder = HOME / 'updates'
    if not folder.is_dir() or app_install.linked(folder):
        return
    for job in sorted(folder.glob('install-*'))[-5:]:
        if not job.is_dir() or app_install.linked(job):
            continue
        try:
            result = json.loads((job / 'result.json').read_text(encoding='utf-8'))
            plan = job / 'cleanup.json'
            if not isinstance(result, dict) or result.get('phase') not in ('complete', 'rolled_back') or app_install.linked(plan):
                continue
            if app_install.file_hash(plan) != result.get('cleanup_sha256'):
                continue
            files = json.loads(plan.read_text(encoding='utf-8'))
            if not isinstance(files, dict):
                continue
            for name, digest in files.items():
                relative = app_install.relative_name(name)
                path = job / name
                if any(app_install.linked(job.joinpath(*relative.parts[:i])) for i in range(1, len(relative.parts) + 1)):
                    continue
                if path.is_file() and app_install.file_hash(path) == digest:
                    try:path.unlink()
                    except OSError:pass
            # Keep the plan while an unchanged owned DLL is still open.
            if any((job / name).is_file() and not app_install.linked(job / name)
                   and app_install.file_hash(job / name) == digest for name, digest in files.items()):
                continue
            plan.unlink()
            (job / 'result.json').unlink()
            for directory in sorted((p for p in job.rglob('*') if p.is_dir()), key=lambda p: len(p.parts), reverse=True):
                try:directory.rmdir()
                except OSError:pass
            try:job.rmdir()
            except OSError:pass
        except (OSError, ValueError, KeyError, TypeError, RuntimeError):
            continue


def command(arguments):
    parser = argparse.ArgumentParser(prog='alisa update', description='Проверить обновление программы без потери настроек.')
    parser.add_argument('operation', nargs='?', choices=['check', 'ask-on', 'ask-off'])
    parser.add_argument('--yes', action='store_true')
    parser.add_argument('--archive', type=Path, help='проверенный локальный архив с соседним .sha256')
    parser.add_argument('--version', dest='archive_version', help='версия локального архива')
    args = parser.parse_args(arguments)
    if args.operation in ('ask-on', 'ask-off'):
        set_startup(args.operation == 'ask-on')
        ui.note('Предлагать обновления при запуске: ' + ('да' if preferences()['ask_on_start'] else 'нет'))
        return 0
    if args.archive:
        if not args.archive_version:
            raise ValueError('Для локального архива укажите --version.')
        version_key(args.archive_version)
        if version_key(args.archive_version) <= version_key(__version__):
            raise RuntimeError('Для установки выберите более новую версию.')
        value = {'platform': target_platform(), 'asset': args.archive.name, 'latest_version': args.archive_version,
                 'current_version': __version__, 'available': True}
    else:
        value = check(force=True)
    if args.operation == 'check':
        ui.show({key: val for key, val in value.items() if key != 'urls'})
    elif args.yes and value['available']:
        raise prepare(value, archive=args.archive)
    else:
        offer(value, archive=args.archive)
    return 0
