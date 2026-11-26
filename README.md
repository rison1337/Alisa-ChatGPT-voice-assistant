https://github.com/user-attachments/assets/222b4d58-75bb-4ee6-bbb1-db9dd40330e0

# Alisa Voice — Яндекс Станция Стрит (Mango)

Форк [rison1337/Alisa-ChatGPT-voice-assistant](https://github.com/rison1337/Alisa-ChatGPT-voice-assistant)
(v0.3.11, ветка `street-mango`). Оригинал поддерживает Лайт 1/2, Мини 2 и Мини 3, Станцию 2 и
экспериментальную Миди — там VQE работает в `maind` на CPU. **Стрит устроена иначе**, и это
единственное, что добавляет этот форк.

> **Документация оригинала (установка, CLI, обновления, VPN, подсветка, фразы) не дублируется здесь.**
> Всё общее — в [README апстрима](https://github.com/rison1337/Alisa-ChatGPT-voice-assistant#readme)
> и его `docs/`. Здесь — только отличия Стрит и специфика этой платформы.

---

## Станция Стрит (Mango): что это за устройство

Розничное имя — **Яндекс Станция Стрит**, кодовое — **Mango**, device-tree
`Yandex Station Mango DVT`, совместимость `amlogic,a1`. Самая младшая платформа семейства и
единственная 32-битная.

### Проверено на нашем экземпляре (root по SSH)

| Параметр | Значение |
|---|---|
| Платформа | `amlogic,a1`, device-tree `Yandex Station Mango DVT` |
| Разрядность | `armv7l`, 32-битные ядро и userspace (ARMhf, EABI5, hard-float) |
| Прошивка | `0.376.1.20.4861173338.20261116.100` (в `stack.sh` закреплена точным сравнением) |
| Корень | `/` — `ubi0:rootfs`, UBIFS, смонтирован **ro** (`assert=read-only`) |
| Данные | `/bkupgrade` — `ubi1:bkupgrade`, UBIFS, **rw**; сюда ставится порт |
| Инициализация | Buildroot-style `rcS`, исполняются только `/etc/init.d/S??*` с ro-раздела |
| Голосовой стек | `maind` + `quasar`; OTA-применение через `apply_packed_ota`/`updatesd` |
| VQE | вынесен в **HiFi4 DSP** (`/dev/hifi4dsp0`), а не в CPU |
| Звук | ALSA `speakers` → `plug`/`dmix` → `hw:0,1`; `default` как fallback |
| Mute | `gpio_keypad` (`/dev/input/event0`, `0660 root:input`), состояние — `SW_MUTE_DEVICE` через `EVIOCGSW` |
| Сон | `maind` → `yandex-standby` → запись `mem` в `/sys/power/state` |

### Паспортные данные (карточка сообщества, на нашем экземпляре не перепроверялись)

Источник: [hackalice.me → Станция Стрит (Mango)](https://hackalice.me/devices/station-street/).

| Что | Значение |
|---|---|
| SoC | Amlogic A1 (`amlogic,a1`) |
| CPU | 2 × Cortex-A35 (`0xd04`), 128…1200 МГц |
| ОЗУ | ~226 МБ (`MemTotal 226116 kB`) |
| Ядро | Linux 5.15.137 armv7l, userspace Buildroot 2023.11.1 |
| Флеш | NAND + UBI: `mtd5 system 80 MB` → `ubi0:rootfs` (ro), `mtd6 bkupgrade 97 MB` → `ubi1` (rw) |
| Усилитель | ACM8625 (`snd_soc_acm8625`), ALSA card0 `AML-AUGESOUND`: `TDM-B-acm8625`, `PDM` (микрофоны), `LOOPBACK-A` (AEC) |
| Питание | встроенный аккумулятор; топливомер CW2217, зарядник charge2366, зарядка по USB-C |
| Индикация | логотип — 3 зоны RGBW (`mango:*:logo0..2`), заряд — 4 красных сегмента |
| Датчики | акселерометр SC7A20E (`1-0x19`) |
| Радио | Unisoc (Wi-Fi/BT), поддержан BT-пульт |
| Кнопки | `adc_keypad` (громкость, пауза, BT, Алиса), `gpio_keypad` (микрофон `KEY_MUTE`, питание) |

Ограничение по памяти (~226 МБ) — практическое: на Стрит нельзя рассчитывать на 64-битные пакеты
и тяжёлые демоны, всё собирается под ARMhf.

---

## Чем этот форк отличается от оригинала

| Область | Оригинал | Здесь (Стрит) |
|---|---|---|
| Перехват VQE | `YandexVqeC_GetOutputChannelData` в `maind` | `YandexVqeC_DspClient_Process` в DSP-клиенте |
| Выбор канала | по фиксированному индексу | по метке `(kind, index)`, `vqe_0` = `(6, 0)` |
| Mute | иная модель GPIO | `gpio_keypad` + `SW_MUTE_DEVICE`, **fail-closed** |
| Вывод звука | маршрут модели | штатный `speakers` → `dmix` → `hw:0,1` |
| Микшер | — | `holder` открывает PCM первым, чтобы не ловить `EACCES` на dmix-семафоре |
| Автозапуск | штатные механизмы модели | `S99alisa-port` в `/etc/init.d` + `standby-inhibit.sh` |
| Голос и reasoning | по модели | `voice` (9 голосов) и `reasoning` (`instant`/`high`) с откатом |
| Сеть | VPN-сценарии апстрима | исходящий SSH-relay для `chatgpt.com` |

### Ключевое: почему обычный tap не работает

На Лайт 2 `vqe_tap.c` подменяет геттер и получает готовый канал. На Стрит обработка живёт в
HiFi4 DSP, геттер не вызывается — библиотека загружается, но счётчик `hits=0`. Поэтому
`engines/native/station/vqe_tap_mango_dsp.c` перехватывает вызов DSP-клиента
(`libyandex_evil.so`) и выбирает обработанный канал по метке, а не по позиции в массиве из 16
дорожек. Всё остальное остаётся штатным: буферы, mute, жизненный цикл DSP не меняются, tap лишь
отправляет необязательные неблокирующие датаграммы `AF_UNIX`.

### Почему нужен внешний relay

`chatgpt.com` может отказать запросам напрямую с колонки. Клиент открывает **исходящее** SSH-
соединение к небольшому серверу, и HTTPS идёт через него. Сервер не расшифровывает TLS, не имеет
доступа к колонке и не принимает входящих сессий: канал односторонний «колонка → сервер → сайт».
Без relay стек не стартует, и колонка остаётся обычной Алисой.

---

## Требования

1. **Root и SSH уже получены.** Порт трогает системные компоненты: `LD_PRELOAD` для `maind`,
   чтение `/dev/input/event0`, запись в `/etc/init.d`. Получение root в проект не входит и здесь не
   описано. Для группы `input` используйте `setpriv --groups 29,104` только для процесса клиента —
   не правьте `/etc/group`.
2. **Прошивка закреплена.** Требуется ровно `0.376.1.20.4861173338.20261116.100`. На другой сборке
   `stack.sh` не перехватывает управление и возвращает штатную Алису. Поддержки «любой Стрит на
   любой прошивке» нет.
3. **Репозиторий сам по себе не устанавливается.** Нужны собранные под ARMhf `client.armhf`,
   `vqe_tap_mango_dsp.so`, `holder`, `dispatch`, `relay`, а также `native_agent.so`, `loopback.so`
   из установки апстрима, `state/` с живой сессией и `MANIFEST` с контрольными суммами.
   `install.sh` — обновлятор поверх рабочей установки, не первичный установщик.

## Сборка

```sh
# 1. DSP-tap
aarch64-linux-gnu-gcc -marm -mfloat-abi=hard -shared -fPIC -O2 \
  -o vqe_tap_mango_dsp.so engines/native/station/vqe_tap_mango_dsp.c -ldl

# 2. holder (нужны заголовки libasound под ARMhf)
aarch64-linux-gnu-gcc -marm -mfloat-abi=hard -O2 -o holder deploy/street/holder.c -lasound

# 3. dispatch (Go, linux/arm)
GOOS=linux GOARCH=arm GOARM=7 go build -o dispatch deploy/street/dispatch.go

# 4. client — тулчейном апстрима, пропатченный в этой ветке
```

Хеши проверяйте **на самой колонке**: побайтовой воспроизводимости кросс-сборок нет.

## Установка и проверка

Полная процедура — [STREET.md](STREET.md): подготовка каталога на `/bkupgrade`, `MANIFEST`,
`S99alisa-port`, `verify-post-power.sh`. Кратко:

1. Собрать компоненты, разложить в каталог на записываемом разделе, сгенерировать `MANIFEST`.
2. Установить `S99alisa-port` в `/etc/init.d` при временно `rw` корне.
3. Перезагрузить и проверить счётчики `Process`, `layout_ok`, `eligible_blocks`,
   `vqe_0_datagrams`, `mute`, `phase`.

**Доказательство — только счётчики.** Загруженная `.so` в `maps`, `MANGO-NATIVE/1`, HTTP 200 или
открытый WebRTC-канал сами по себе голосовой обмен не подтверждают: датаграммы должны расти при
открытом микрофоне и прекращаться при закрытом.

## Ограничения

- Без доступного relay колонка возвращается к штатной Алисе.
- Сервер не сообщает, какой `reasoning` применил (`effort_reported=unknown`): `high` подтверждается
  принятием запроса и задержкой, а не полем статуса.
- Обновление прошивки перезаписывает `/etc/init.d` — автозапуск нужно ставить заново.
- `stack.sh` fail-open: при любой ошибке (relay, holder, client, manifest, версия) возвращается
  штатный лаунчер.

## Ссылки

- Оригинальный проект и общая документация: [rison1337/Alisa-ChatGPT-voice-assistant](https://github.com/rison1337/Alisa-ChatGPT-voice-assistant)
- Подробности платформы: [STREET.md](STREET.md)
- PR с этим портом: [pull/30](https://github.com/rison1337/Alisa-ChatGPT-voice-assistant/pull/30)
- Карточка устройства: [hackalice.me/devices/station-street](https://hackalice.me/devices/station-street/)
- Лицензии компонентов: [THIRD-PARTY.md](THIRD-PARTY.md)

## Благодарности и лицензия

Вся исходная работа — автору апстрима; этот форк добавляет только поддержку Стрит/Mango.
В оригинальном репозитории нет файла лицензии, поэтому ветку следует считать личной производной
для совместимости, а бинарники — не распространять коммерчески.
