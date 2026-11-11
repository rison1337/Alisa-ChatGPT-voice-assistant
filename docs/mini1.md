# Мини 1 YNDX-0004: экспериментальная source-only адаптация

Отчёт: [issue #4](https://github.com/rison1337/Alisa-ChatGPT-voice-assistant/issues/4).
Это Draft-перенос исходников, а не готовый установочный релиз. Унаследованные
бинарные assets не заменены экспериментальными файлами. Установка Mini 1
блокируется, пока пакет не пересобран с адаптером громкости. Получение root
не входит в проект; нужны работающие root и SSH.

## Аппаратные результаты исходной ветки

Источник проверенной адаптации сохранён веткой `mini1-tested-2026-11-01`,
commit `c07da9de35f173ac29a01b0c08e82045d1a18172`, до переноса с
`dbe3727598f37ff1baf48e19c3cf50971901512d` на актуальный main.
Состав и идентификаторы проверенных артефактов: [snapshot](mini1-tested-baseline.md).

Один YNDX-0004, Quasar `1.355.4.3.4297407902.20260623`, ядро aarch64,
userspace ARMhf/glibc 2.23. Device tree `amlogic, axg` недостаточен сам по себе:
проверяется также `deviceType=yandexmini`. Неизвестные хеши остаются unverified.

В исходной установке подтверждены микрофон и ответ ChatGPT, два последовательных
open/close-разговора с жестовой громкостью, холодное открытие и громкость после
перезагрузки. Короткий verify: 699 микрофонных кадров, 71 audible block, 0 ALSA
underrun. Проверена только одна прошивка/сеть. Длительная стабильность, сетевые
отказы, истечение сессии, Bluetooth и другие модели не проверены на этом стенде.

После переноса на main аппаратный прогон клиента **ещё не выполнен**. Не
переносить результаты исходной установки на новый бинарник без отдельного теста.

## Сборка

Зависимости и Zig 0.14.1: [development.md](development.md). После их подготовки:

```sh
python3 engines/native/scripts/build_native.py --jobs 4
cmake -S engines/native -B engines/native/build/client -G Ninja \
  -DCMAKE_TOOLCHAIN_FILE="$PWD/engines/native/build/arm/toolchain.cmake" \
  -DCMAKE_BUILD_TYPE=Release -DALISA_USE_NETEQ=ON
cmake --build engines/native/build/client --target alisa-standalone --parallel 4
python3 engines/native/scripts/build_hooks.py
python3 scripts/build_mark.py
sh engines/native/scripts/build_mini1_volume.sh
python3 scripts/package.py
python3 integrations/mini1/build.py
```

Последняя команда требует Go и создаёт только локальные артефакты в
`build/mini1-integrations`; на колонку она ничего не устанавливает. Архивы не
добавлять в PR. Homebrew manifest получает SHA нового ELF при упаковке.

## Изолированные обходы Mini 1

`run.sh` включает их только при совпадении Mini 1 identity:

- `/bin/true` как донор агента: на устройстве отсутствует aplay;
- `ALISA_DISABLE_NATIVE_FOCUS=1`: старый AudioFilePlayer/focus handshake не работал;
- `ALISA_DISABLE_STUN=1`: STUN gathering зависал на проверенной сети;
- ALSA `plughw:0,0`, Quasar library path и mute shim;
- `ALISA_MINI1=1`: LED helper подтверждает команды без записи в чужой backend;
- `ALISA_VOLUME_FILE=/data/quasar/last_vendor_volume`: громкость прямого PCM.

Адаптер `volume-libs/libasound.so.2` передаёт PCM-вызовы настоящей библиотеке
по абсолютному пути и масштабирует S16 stereo. Он не предназначен для произвольных
ALSA-клиентов. Ключ линковки `-Wl,-z,nodelete` обязателен: иначе второй разговор
может разрешить системную libasound по тому же soname и обойти адаптер. Беззвучный
`mini1-volume-probe` трижды открывает null PCM и печатает выбранную библиотеку.
Первоначальные результаты loader probe подтверждены на колонке; новая сборка
после переноса требует отдельной проверки. STUN/focus обходы и линейная шкала
громкости пока не являются универсальной реализацией Mini 1.

## Опциональные команды и автозапуск Luna/Homebrew

Исходный агент Alisa Voice не получал события речи Mini 1. Полная проверенная
схема поэтому содержит дополнительные компоненты, отдельно от ядра клиента:

1. [YAM](https://github.com/nyakokitsu/yam), официальный пакет `yam/luna` 0.2.12
   для yandexmini; Linux runtime [Luna](https://github.com/nyakokitsu/luna).
2. [Homebrew](https://github.com/hertebeznat-cell/alice-homebrew) 6.0.1, уже имевший
   запуск при загрузке в исходном эксперименте. Этот патч не ставит Homebrew и
   не редактирует системные boot scripts.
3. `local-luna-boot`: native Homebrew-плагин, запускающий одну Luna на
   `127.0.0.1:8100`, data-dir `/data/yam/var/luna/data`. Он создаёт временный YAM
   lock; требует явного согласия на `system.full-access`. При ошибке Luna
   Homebrew выключает плагин; автоматического бесконечного рестарта нет.
4. Два `.yap`: fixed-phrase open/close, права commands/exec без network. В Luna
   требуется `allow_plugin_binaries`. Не включать это право для непроверенных
   пакетов. Обработчики переподключаются после разрыва Quasar IPC; open ждёт
   `agent_connected=true` до 30 секунд. Close закрывает разговор, не весь runtime.

Для установки собранного `luna-boot.aliceplugin` используйте импорт локального
пакета Homebrew и явное подтверждение root-доступа. Два `.yap` импортируются в
Luna, включаются и добавляются как service. Не запускать несколько Luna и не
включать одновременно конкурирующий запускатель нативного клиента.
Доступ к loopback-панели для настройки при подключённом ADB:

```sh
adb forward tcp:18100 tcp:8100
```

После настройки проверить `enabled`, `service_enabled`, `running` обоих сервисов,
затем реальную фразу и ответ; повторить open/close и перезагрузку. Статус
`running` сам по себе не доказывает распознавание речи. Журналы listener могут
содержать ошибки переподключения при смене профиля. Не публиковать account state,
browser profile, полные Quasar configs, SSH-ключи или аудио домашних разговоров.

Отключение автозапуска: выключить `local-luna-boot` в Homebrew; остановить runtime
штатным `run.sh stop`/CLI для восстановления stock Quasar. Это разные действия:
отключение wrapper не гарантирует остановку уже запущенного GPT runtime.
