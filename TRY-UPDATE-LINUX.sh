#!/bin/sh
set -eu
task_directory=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
task_archive="$task_directory/archives/alisa-voice-linux.tar.gz"
[ -f "$task_archive" ] || task_archive="$task_directory/alisa-voice-linux.tar.gz"
task_cli="$HOME/.local/bin/alisa"
if [ ! -f "$task_archive" ] || [ ! -f "$task_archive.sha256" ]; then
    echo 'Рядом нужен alisa-voice-linux.tar.gz и соседний .sha256.' >&2
    exit 1
fi
if [ ! -x "$task_cli" ]; then
    echo 'Сначала установите приложение через bootstrap-linux.sh.' >&2
    exit 1
fi
if ! "$task_cli" --help | grep -q update; then
    echo 'В старой Linux 0.2.0 ещё нет команды обновления.' >&2
    echo 'Остановите режим и один раз установите новый CLI поверх старого:' >&2
    printf 'sh bootstrap-linux.sh --archive "%s"\n' "$task_archive" >&2
    echo 'Настройки и вход сохранятся. Затем обновления доступны из CLI.' >&2
    exit 2
fi
exec "$task_cli" update --archive "$task_archive" --version 0.3.0.dev2
