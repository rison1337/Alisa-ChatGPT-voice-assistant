"""Wait outside app so Windows can replace it and return the worker exit code.

Uses a private immutable copy of embedded Python, with no app/vendor imports.
"""
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile

HANDOFF = 'ALISA_WINDOWS_HANDOFF'
SCHEDULED = 75


def run(arguments, directory, environment):
    child = subprocess.Popen(arguments, cwd=directory, env=environment)
    while True:
        try:
            return child.wait()
        except KeyboardInterrupt:
            # The shared console also delivers Ctrl+C to the child. Let the
            # worker finish rollback rather than kill it while replacing app.
            continue


def main():
    base = Path(__file__).resolve().parents[2]
    app = base / 'app'
    os.chdir(base)
    folder = Path(tempfile.mkdtemp(prefix='.launcher-run-', dir=base))
    request = folder / 'handoff.json'
    environment = {**os.environ, HANDOFF: str(request)}
    arguments = sys.argv[1:]
    update_error = 0
    try:
        while True:
            code = run([str(app / 'windows/runtime/python.exe'), '-B', '-X', 'utf8',
                        str(app / 'alisa.py'), *arguments], base, environment)
            if code != SCHEDULED:
                return update_error or code
            if request.stat().st_size > 1024 * 1024:
                raise ValueError('Oversized handoff')
            job = json.loads(request.read_text(encoding='utf-8'))
            executable, argv, directory = job['executable'], job['arguments'], job['directory']
            if (job.get('schema') != 1 or not isinstance(argv, list) or not argv
                    or any(not isinstance(value, str) or '\0' in value for value in argv)
                    or argv[0] != executable or not Path(executable).is_absolute()
                    or not Path(directory).is_absolute()
                    or Path(executable).resolve().is_relative_to(app.resolve())):
                raise ValueError('Invalid update handoff')
            request.unlink()
            worker_environment = environment.copy()
            worker_environment.pop(HANDOFF)
            code = run(argv, directory, worker_environment)
            update_error = update_error or code
            if not job.get('relaunch'):
                return update_error
            # Reopen only after the worker has exited and released its DLLs.
            # A successful menu exit must not erase a failed update's result.
            arguments = []
    finally:
        try:
            request.unlink(missing_ok=True)
            folder.rmdir()
        except OSError:
            pass  # Never remove unknown files recursively.


if __name__ == '__main__':
    try:
        raise SystemExit(main())
    except (OSError, ValueError, KeyError, TypeError):
        print('Не удалось продолжить запуск или обновление Alisa Voice.', file=sys.stderr)
        raise SystemExit(1)
