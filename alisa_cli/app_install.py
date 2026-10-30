"""Filesystem transaction shared by the Linux and Windows application installers.

Only the allowlisted application is replaced. State, browser profiles and all
previous/unknown files stay outside the candidate and are never deleted here.
This module deliberately uses only the Python standard library.
"""
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import shutil
import tempfile
import time

FORMATS = {
    'linux-x86_64': ('package-manifest.json', 'alisa-voice'),
    'windows-amd64': ('windows-package.json', 'alisa-voice-windows-native'),
}


def linked(path):
    path = Path(path)
    return path.is_symlink() or (hasattr(path, 'is_junction') and path.is_junction())


def relative_name(name):
    if not isinstance(name, str) or not name or '\\' in name or ':' in name or '\x00' in name:
        raise RuntimeError('Некорректный путь пакета.')
    path = PurePosixPath(name)
    if not path.parts or path.is_absolute() or '..' in path.parts or str(path) != name:
        raise RuntimeError('Некорректный путь пакета.')
    return path


def file_hash(path):
    digest = hashlib.sha256()
    with Path(path).open('rb') as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b''):
            digest.update(chunk)
    return digest.hexdigest()


def validate(root, target, version=None, strict=False):
    root = Path(root)
    name, package = FORMATS[target]
    if linked(root) or linked(root / name):
        raise RuntimeError('Пакет не должен содержать ссылки.')
    value = json.loads((root / name).read_text(encoding='utf-8'))
    if not isinstance(value, dict) or value.get('package') != package or not isinstance(value.get('files'), dict) or not value['files']:
        raise RuntimeError('Неизвестный пакет программы.')
    if version is not None and (value.get('version') != version or value.get('platform') != target):
        raise RuntimeError('Версия или платформа пакета не совпадает с выбранным обновлением.')
    for entry, digest in value['files'].items():
        relative = relative_name(entry)
        path = root / entry
        if any(linked(root.joinpath(*relative.parts[:i])) for i in range(1, len(relative.parts) + 1)):
            raise RuntimeError('Пакет не должен содержать ссылки.')
        if not path.is_file() or not isinstance(digest, str) or file_hash(path) != digest:
            raise RuntimeError('Повреждён файл пакета: ' + entry)
    if strict:
        actual = {p.relative_to(root).as_posix() for p in root.rglob('*') if p.is_file()}
        if actual != set(value['files']) | {name}:
            raise RuntimeError('Состав пакета не совпал с manifest.')
    return value


def owned(path, target):
    name, package = FORMATS[target]
    try:
        value = json.loads((Path(path) / name).read_text(encoding='utf-8'))
        return not linked(path) and isinstance(value, dict) and value.get('package') == package
    except (OSError, ValueError):
        return False


def private_json(path, value):
    path = Path(path)
    fd, temporary = tempfile.mkstemp(prefix='.' + path.name, dir=path.parent)
    try:
        if hasattr(os, 'fchmod'):
            os.fchmod(fd, 0o600)
        with os.fdopen(fd, 'w', encoding='utf-8') as stream:
            json.dump(value, stream, ensure_ascii=False, indent=2)
            stream.write('\n')
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def replace(source, destination, target, record_path=None):
    source, destination = Path(source), Path(destination)
    manifest = validate(source, target)
    destination.parent.mkdir(parents=True, exist_ok=True)
    if linked(destination) or linked(destination.parent):
        raise RuntimeError('Папка установки не должна быть ссылкой.')
    if destination.exists() and not owned(destination, target):
        raise RuntimeError('Папка установки занята другими файлами. Они сохранены.')
    required = sum((source / name).stat().st_size for name in manifest['files']) + 16 * 1024 * 1024
    if shutil.disk_usage(destination.parent).free < required:
        raise RuntimeError('Не хватает места для установки новой программы. Прежняя версия сохранена.')
    backup = destination.with_name(destination.name + '.previous')
    if backup.exists() or linked(backup):
        backup = destination.with_name(destination.name + '.previous-' + str(time.time_ns()))
    record = {'destination': str(destination), 'backup': None, 'platform': target,
              'version': manifest.get('version')}
    with tempfile.TemporaryDirectory(prefix='.alisa-install-', dir=destination.parent) as temporary:
        staged = Path(temporary) / 'app'
        staged.mkdir(mode=0o700)
        for name in manifest['files']:
            output = staged / name
            output.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source / name, output)
        name = FORMATS[target][0]
        shutil.copy2(source / name, staged / name)
        validate(staged, target, strict=True)
        if destination.exists():
            destination.rename(backup)
            record['backup'] = str(backup)
        try:
            staged.rename(destination)
        except BaseException:
            if record['backup']:
                backup.rename(destination)
            raise
    if record_path:
        try:
            private_json(record_path, record)
        except BaseException:
            if record['backup']:
                rollback(record)
            raise
    return record


def rollback(record):
    destination = Path(record['destination'])
    backup = Path(record['backup']) if record.get('backup') else None
    if not backup or not owned(backup, record['platform']):
        raise RuntimeError('Не найдена проверенная прежняя версия программы.')
    if not destination.exists():
        backup.rename(destination)
        return {'restored': str(destination), 'failed_preserved': None}
    if not owned(destination, record['platform']):
        raise RuntimeError('Папка программы изменилась; автоматический откат остановлен.')
    failed = destination.with_name(destination.name + '.failed-' + str(time.time_ns()))
    destination.rename(failed)
    try:
        backup.rename(destination)
    except BaseException:
        failed.rename(destination)
        raise
    return {'restored': str(destination), 'failed_preserved': str(failed)}
