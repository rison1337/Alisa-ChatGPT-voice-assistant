# Разработка и сборка

Готовый релиз устанавливается без компилятора. Собственный код разделён на три части:

- `alisa_cli/` — установщик и управление режимами; `station/` — его вспомогательные скрипты.
- `engines/native/` — C++-клиент WebRTC/NetEq и C-модули для колонки.
- `engines/bridge/` — браузерный мост Linux, расширение Chromium и C-модули.

Старый мост из корня репозитория перенесён в `engines/bridge/`. Обычный запуск и обновление выполняются через `alisa`, а не через прежний `bridge.py` в корне. Аккаунты и настройки находятся отдельно от исходников; их нельзя добавлять в Git или релиз.

## Проверка CLI и моста

```bash
python3 -m unittest discover -s tests -v
python3 engines/bridge/scripts/build.py --target host
(cd engines/bridge && python3 -m unittest discover -s tests -v)
```

Для тестов C-модулей нужны clang, lld, системная libopus и ALSA utilities. Используются фикстуры и звуковое устройство null; аккаунт ChatGPT и колонка не нужны. Проверки DOM требуют Chromium. Host-модули генерируются в `engines/bridge/build` и в Git не входят.

ARM-модули моста собираются командой `python3 engines/bridge/scripts/build.py --target arm`. Она также скачивает закреплённый пакет Opus Debian, проверяет SHA-256 и извлекает библиотеку с её лицензией.

## Исходники зависимостей

Рядом с бинарным пакетом в [релизе v0.2.0](https://github.com/rison1337/Alisa-ChatGPT-voice-assistant/releases/tag/v0.2.0) опубликован архив `alisa-voice-sources.tar.gz`. Он содержит исходники нативных зависимостей с submodules, sing-box/Xray с `vendor/`, лицензии и метаданные. Точные коммиты и SHA-256 загрузок закреплены в [native-dependencies.json](native-dependencies.json); список исходников NetEq — в [neteq-sources.json](neteq-sources.json).

Из корня клонированного проекта:

```bash
curl -fLO https://github.com/rison1337/Alisa-ChatGPT-voice-assistant/releases/download/v0.2.0/alisa-voice-sources.tar.gz
curl -fLO https://github.com/rison1337/Alisa-ChatGPT-voice-assistant/releases/download/v0.2.0/alisa-voice-sources.tar.gz.sha256
sha256sum -c alisa-voice-sources.tar.gz.sha256
tar -xzf alisa-voice-sources.tar.gz --strip-components=1
```

Архив добавляет `engines/native/research/upstream` и `third_party`, не заменяя README и программу. В `research/upstream` находятся только публичные сторонние исходники: это историческое имя, используемое скриптами сборки. Локальные исследования устройства в архив не входят.

## Нативный клиент ARMhf

Нужны CMake 3.21+, Ninja, make, Perl, clang/lld, llvm-ar/llvm-ranlib и Zig 0.14.1. Сборка выполняется на Linux x86_64 для ARMhf/glibc 2.28; ядро колонки aarch64, но её userspace — 32-битный.

Установите закреплённый Zig в папку проекта:

```bash
curl -fLo zig.tar.xz https://ziglang.org/download/0.14.1/zig-x86_64-linux-0.14.1.tar.xz
echo '24aeeec8af16c381934a6cd7d95c807a8cb2cf7df9fa40d359aa884195c4716c  zig.tar.xz' | sha256sum -c -
mkdir -p engines/native/toolchains
tar -xJf zig.tar.xz -C engines/native/toolchains
```

Затем соберите зависимости, клиент и модули:

```bash
python3 engines/native/scripts/build_native.py --jobs 4
cmake -S engines/native -B engines/native/build/client -G Ninja \
  -DCMAKE_TOOLCHAIN_FILE="$PWD/engines/native/build/arm/toolchain.cmake" \
  -DCMAKE_BUILD_TYPE=Release -DALISA_USE_NETEQ=ON
cmake --build engines/native/build/client --target alisa-standalone --parallel 4
python3 engines/native/scripts/build_hooks.py
python3 scripts/build_mark.py
python3 scripts/package.py
```

`package.py` обновляет `assets/native/manifest.json`. Изменённые модули требуют проверки на совместимой колонке; успешная компиляция не подтверждает ABI другой прошивки. Точное побайтовое совпадение сборок в разных окружениях не заявляется.

## VPN

Установщик использует неизменённые официальные бинарники. Повторная загрузка с проверкой SHA-256: `python3 scripts/fetch_vpn.py` и `python3 scripts/fetch_xray.py`. Исходники соответствующих версий и Go-зависимости находятся в `third_party/vpn/` после распаковки исходного архива. Параметры upstream-сборки сохранены в `.goreleaser.yaml` sing-box и `.github/workflows/release.yml` Xray.

Для самостоятельной ARMv7-сборки с установленным Go требуемой upstream-версии:

```bash
(cd third_party/vpn/sing-box && CGO_ENABLED=0 GOOS=linux GOARCH=arm GOARM=7 \
  go build -mod=vendor -trimpath \
  -tags with_gvisor,with_quic,with_dhcp,with_wireguard,with_utls,with_acme,with_clash_api,with_tailscale \
  -ldflags '-s -w -buildid= -X github.com/sagernet/sing-box/constant.Version=1.12.0' ./cmd/sing-box)
(cd third_party/vpn/xray && CGO_ENABLED=0 GOOS=linux GOARCH=arm GOARM=7 \
  go build -mod=vendor -trimpath -buildvcs=false -gcflags='all=-l=4' \
  -ldflags '-s -w -buildid= -X github.com/xtls/xray-core/core.build=1b1647cd9' -o xray ./main)
```

## Установочный пакет

```bash
python3 scripts/release.py
sh bootstrap-linux.sh --archive "$PWD/dist/alisa-voice-linux.tar.gz" --no-run
```

Первая команда создаёт архив и SHA-256 в `dist/`; вторая устанавливает его текущему пользователю. Сборщик использует явный список файлов, не включает аккаунты, VPN-подписки, ключи, журналы и каталоги исследований. Bootstrap до установки проверяет SHA-256, пути, типы файлов и manifest.

## Версии и пакеты обновлений

Версия находится в `alisa_cli/__init__.py`. Оба сборщика записывают её и платформу в manifest; `alisa --version` должен возвращать то же значение. Перед стабильным релизом нужно установить стабильную версию, собрать оба пакета и создать совпадающий тег `vX.Y.Z`. Локальная `0.3.0.dev2` не является опубликованным релизом.

```bash
python3 scripts/release.py
python3 scripts/fetch_windows.py
python3 scripts/bundle_windows.py --output build/AlisaVoice-Windows-new --archive
```

Windows-каталог вывода должен быть пустым. `fetch_windows.py` загружает только закреплённые зависимости с SHA-256. Linux-архив не включает Windows VPN-ядра; Windows-архив содержит embedded Python, Paramiko и свои валидаторы VPN.

Для updater нужны следующие пары файлов в одном стабильном GitHub Release:

| Платформа | Архив | Контрольная сумма |
|---|---|---|
| Linux x86_64 | `alisa-voice-linux.tar.gz` | `alisa-voice-linux.tar.gz.sha256` |
| Windows x64 | `alisa-voice-windows.zip` | `alisa-voice-windows.zip.sha256` |

Если пара для платформы отсутствует, CLI не предлагает её установку. API возвращает только последний стабильный релиз; draft/prerelease не предлагаются. [Поведение обновлений](updates.md).

Для локального теста установленного приложения доступно:

```bash
alisa update --archive /path/to/alisa-voice-linux.tar.gz --version X.Y.Z --yes
```

В Windows используйте соответствующий ZIP и `alisa.cmd`. Нужен соседний `.sha256`; версия должна быть новее запущенной и совпадать с manifest и `--version` самого клиента. Git-копия этим путём не заменяется. Проверки выполняются до запуска программы из архива.

`scripts/update_worker.py` продолжает работу вне заменяемой папки. Windows ожидает завершения предыдущего процесса; Linux использует `exec`. После замены приложения обновляются компоненты установленных режимов. Ошибка вызывает возврат прежнего приложения и попытку восстановления компонентов; неполный откат отражается как `recovery_needed`. Временный пакет остаётся до успешной проверки результата и освобождения его DLL. Предыдущие папки приложения не удаляются автоматически.

Тесты `test_updates.py` проверяют реальную файловую транзакцию, подменённый релиз, отказ/отключение уведомлений и worker с имитацией колонки. `test_terminal_ui.py` проверяет выход из полноэкранного меню до замены процесса. Эти проверки дополняют прогон в настоящей Windows, а не заменяют его.
