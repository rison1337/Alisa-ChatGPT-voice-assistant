#!/usr/bin/env python3
"""Continue in the same terminal, outside the application being replaced."""
import ctypes
import json
import os
from pathlib import Path
import subprocess
import sys
import time

PAYLOAD = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PAYLOAD))
from alisa_cli import app_install


def python_for(root, target):
    return str(Path(root) / 'windows/runtime/python.exe') if target == 'windows-amd64' else sys.executable


def execute(root, target, arguments, phase, timeout=300):
    argv = [python_for(root, target), '-B', '-X', 'utf8', str(Path(root) / 'alisa.py'), *arguments]
    result = subprocess.run(argv, stdin=subprocess.DEVNULL, capture_output=True, timeout=timeout)
    if result.returncode:
        # Child output can include private account/runtime fields. Preserve
        # only the operation name and result; never echo argv or raw output.
        raise RuntimeError('Не выполнен этап: ' + phase)
    return result.stdout


def wait_previous_process(pid):
    if pid == os.getpid() or os.name != 'nt':
        return
    from ctypes import wintypes as w
    kernel = ctypes.WinDLL('kernel32', use_last_error=True)
    kernel.OpenProcess.argtypes = [w.DWORD, w.BOOL, w.DWORD]
    kernel.OpenProcess.restype = w.HANDLE
    kernel.WaitForSingleObject.argtypes = [w.HANDLE, w.DWORD]
    kernel.CloseHandle.argtypes = [w.HANDLE]
    handle = kernel.OpenProcess(0x100000, False, pid)
    if handle:
        try:
            if kernel.WaitForSingleObject(handle, 30000) != 0:
                raise RuntimeError('Предыдущее окно программы ещё работает; обновление не началось.')
        finally:
            kernel.CloseHandle(handle)


def install_application(payload, target, record):
    command = ([python_for(payload, target), '-B', '-X', 'utf8', str(payload / 'windows/install.py'), '--no-run']
               if target == 'windows-amd64' else
               [sys.executable, '-B', str(payload / 'scripts/release.py'), '--install'])
    result = subprocess.run([*command, '--record', str(record)], stdin=subprocess.DEVNULL, capture_output=True, timeout=300)
    if result.returncode:
        raise RuntimeError('Не удалось заменить программу на компьютере. Прежняя версия сохранена.')
    return json.loads(record.read_text(encoding='utf-8'))


def apply(job_path):
    job_path = Path(job_path).resolve()
    job = json.loads(job_path.read_text(encoding='utf-8'))
    root, payload = Path(job['current_root']), Path(job['payload'])
    target, version, modes = job['platform'], job['version'], job['modes']
    if job.get('schema') != 1 or payload.resolve() != PAYLOAD.resolve() or (root / '.git').exists():
        raise RuntimeError('Неизвестное задание обновления.')
    app_install.validate(payload, target, version, strict=True)
    if not app_install.owned(root, target):
        raise RuntimeError('Установленная программа не распознана; её файлы не изменены.')
    wait_previous_process(int(job['parent_pid']))
    os.chdir(job_path.parent)
    receipt = Path(job['state_directory']) / 'update-status.json'
    result = {'phase': 'starting', 'version': version, 'job': str(job_path.parent), 'modes': modes}
    record = None
    stopped, attempted, started = [], [], []
    try:
        for item in modes:
            mode = item['mode']
            if mode not in ('native', 'bridge'):
                raise RuntimeError('Неизвестный режим обновления.')
            state = json.loads(execute(root, target, [mode, 'status'], 'проверка состояния ' + mode))
            if state.get('voice_active') or state.get('phase') in ('active', 'connecting', 'opening', 'starting', 'stopping'):
                raise RuntimeError('Сначала закройте голосовой чат. Программа не заменена.')
            # Recheck readiness immediately before mutation; the download may
            # have taken time and voice commands can arrive in that interval.
            item['running'] = bool(state.get('agent_connected')) if mode == 'native' else state.get('phase') == 'running'
        for item in modes:
            if item['running']:
                print('Останавливаю компоненты: ' + item['mode'], flush=True)
                stopped.append(item)
                execute(root, target, [item['mode'], 'stop'], 'остановка ' + item['mode'])
        app_install.private_json(receipt, result)
        print('Устанавливаю новую версию программы…', flush=True)
        record = install_application(payload, target, job_path.parent / 'application-record.json')
        if Path(record['destination']).resolve() != root.resolve():
            raise RuntimeError('Установщик вернул другую папку программы.')
        observed = execute(root, target, ['--version'], 'проверка новой программы').decode().strip()
        if observed != version:
            raise RuntimeError('Установленная версия не прошла проверку.')
        for item in modes:
            attempted.append(item)
            print('Обновляю компоненты: ' + item['mode'], flush=True)
            execute(root, target, [item['mode'], 'install', '--yes', '--no-start'], 'обновление ' + item['mode'], timeout=900)
        for item in modes:
            if item['running']:
                started.append(item)
                execute(root, target, [item['mode'], 'start'], 'запуск ' + item['mode'])
        result.update(phase='complete', backup=record.get('backup'), message='Программа и установленные компоненты обновлены. Вход, чаты и настройки сохранены.')
        print(result['message'], flush=True)
    except BaseException as error:
        if isinstance(error, (KeyboardInterrupt, SystemExit)):
            message = 'Обновление прервано.'
        elif isinstance(error, RuntimeError):
            message = str(error)
        else:
            message = 'Ошибка обновления программы.'
        failures = []
        for item in reversed(started):
            try:
                execute(root, target, [item['mode'], 'stop'], 'остановка новой версии')
            except Exception:
                failures.append('остановка ' + item['mode'])
        if record is None:
            try:
                record = json.loads((job_path.parent / 'application-record.json').read_text(encoding='utf-8'))
            except (OSError, ValueError):
                pass
        if record and record.get('backup'):
            try:
                app_install.rollback(record)
            except Exception:
                failures.append('программа на компьютере')
        if not failures:
            for item in reversed(attempted):
                try:
                    execute(root, target, [item['mode'], 'install', '--yes', '--no-start'], 'возврат компонентов', timeout=900)
                except Exception:
                    failures.append('компоненты ' + item['mode'])
        for item in stopped:
            try:
                execute(root, target, [item['mode'], 'start'], 'возврат режима')
            except Exception:
                failures.append('запуск ' + item['mode'])
        result.update(phase='recovery_needed' if failures else 'rolled_back', message=message, rollback_failures=failures)
        print(message, flush=True)
        print('Нужна проверка: ' + ', '.join(failures) if failures else 'Прежняя программа и работающие режимы восстановлены.', flush=True)
    result['finished_at'] = time.time()
    try:
        if result['phase'] in ('complete','rolled_back'):
            manifest=app_install.validate(payload,target)
            manifest_name=app_install.FORMATS[target][0]
            cleanup={str((payload/name).relative_to(job_path.parent)).replace('\\','/'):digest
                     for name,digest in manifest['files'].items()}
            for path in [payload/manifest_name,job_path,job_path.parent/'application-record.json']:
                if path.is_file():cleanup[path.relative_to(job_path.parent).as_posix()]=app_install.file_hash(path)
            if job.get('downloaded_archive'):
                name=app_install.relative_name(job['downloaded_archive'])
                if len(name.parts)==1:cleanup[str(name)]=job['archive_sha256']
            app_install.private_json(job_path.parent/'cleanup.json',cleanup)
            result['cleanup_sha256']=app_install.file_hash(job_path.parent/'cleanup.json')
    except (OSError,ValueError,RuntimeError):
        pass  # Cache cleanup cannot invalidate a completed installation.
    app_install.private_json(job_path.parent/'result.json',result)
    app_install.private_json(receipt, result)
    if job.get('relaunch'):
        executable = python_for(root, target)
        if os.name == 'nt':
            relaunched = subprocess.call([executable, '-B', '-X', 'utf8', str(root / 'alisa.py')])
            return (0 if result['phase'] == 'complete' else 1) or relaunched
        os.execv(executable, [executable, '-B', '-X', 'utf8', str(root / 'alisa.py')])
    return 0 if result['phase'] == 'complete' else 1


if __name__ == '__main__':
    try:
        raise SystemExit(apply(sys.argv[1]))
    except (OSError, ValueError, KeyError, RuntimeError, subprocess.SubprocessError):
        print('Обновление остановлено. Проверенные файлы и прежняя программа сохранены.', file=sys.stderr)
        raise SystemExit(1)
