# INAV Sim

Отдельный FPV-симулятор для INAV: **JSBSim 6-DoF + INAV SITL + джойстик**.
OSD и телеметрия приходят из настоящей прошивки INAV по MSP, а не рисуются
приложением. Папка самодостаточна: не использует файлы проекта SkyLock и
может быть скопирована/отправлена другому человеку целиком.

## Быстрый старт

1. Поставьте **Python 3.10–3.12** (python.org, галочка «Add to PATH»).
2. Запустите `setup.bat` — создаст `.venv` и поставит PySide6 + JSBSim.
3. Подключите пульт/джойстик (EdgeTX в режиме USB Joystick тоже подходит).
4. Запустите `run.bat`.

При первом запуске проверяется GitHub Releases. Если вышла новая версия,
появится окно с предложением обновиться. Обновление скачивается и
применяется автоматически, `config.json`, `logs/` и `userdata/` не трогаются.

## Что внутри

```
INAV_Sim/
├── main.py                 точка входа, проверка обновлений
├── sim/
│   ├── app.py              физика + пульт + кадр
│   ├── sitl.py             мост INAV SITL (X-Plane UDP, MSP, OSD)
│   ├── osd.py              приём MSP DisplayPort и пиксельный шрифт INAV
│   ├── render.py           лёгкий 3D-вид (небо, земля, полоса, дом, деревья)
│   ├── joystick.py         HID/winmm-пульт
│   ├── presets.py          пресеты борта (масса, крыло, CLmax, тяга)
│   ├── fdm/                JSBSim 6-DoF
│   └── updater.py          GitHub Releases
├── sitl/                   inav_SITL.exe + cygwin1.dll + eeprom.bin
├── assets/font_inav.png    шрифт OSD из INAV
└── tools/make_eeprom.py    пересборка EEPROM (OSD/UART3/раскладка)
```

## Управление

| Действие | Пульт | Клавиатура |
|---|---|---|
| Крен | ось Roll | ← → |
| Тангаж | ось Pitch | ↑ ↓ |
| Рыскание | ось Yaw | A D |
| Газ | ось Throttle | W S |
| ARM | кнопка из настроек / авто | Space |
| AH / ACRO | кнопка из настроек | M |
| Старт | — | F5 |
| Пауза | — | P |
| Сброс | — | R |
| Перезапуск INAV | — | F9 |
| Вид снаружи | — | F2 |

Оси, инверсии, мёртвая зона, кнопки ARM/режима и устройство выбираются в
**Файл → Настройки → Пульт** (там же живые полоски осей).

## INAV SITL

`sim/sitl.py` запускает `sitl/inav_SITL.exe` с параметрами:

```
inav_SITL.exe --sim=xp --simip=127.0.0.1 --simport=49100
              --path=sitl/eeprom.bin --chanmap=M01-01,S01-02,S02-03
```

Схема:

```
джойстик -> MSP_SET_RAW_RC -> INAV SITL (UART2, TCP 5761)
физика   -> X-Plane RREF/DREF -> INAV SITL (UDP 49100)
INAV     -> MSP_MOTOR / MSP_SERVO -> рули/тяга JSBSim
INAV     -> MSP_DISPLAYPORT -> OSD (UART3, TCP 5762)
```

Почему порт 49100, а не 49000: 49000 занимает ПК-симулятор SkyLock. Если
оба приложения запущены одновременно, порты не конфликтуют. Порт можно
поменять в настройках.

### EEPROM

`sitl/eeprom.bin` — дамп борта FLYWING с правками:

- включены `OSD`, `GPS`, `VBAT`, `CURRENT_METER`;
- UART3 отдан под **MSP DisplayPort** (OSD);
- `gps_provider = FAKE` (иначе SITL снимает `FEATURE_GPS`);
- напряжение батареи — от fake-сенсора SITL (16.8 В);
- раскладка OSD 30×16: режим, время, высота, курс, скорость, дом, батарея,
  соты, газ, mAh, ток, спутники, вариометр, перекрестие, AHI, сообщения.

Пересобрать: `python tools/make_eeprom.py` (берёт `sitl/base_flywing.bin`).
Раскладку можно менять в `tools/make_eeprom.py` или через INAV Configurator.

## Обновления и релизы

Репозиторий: `https://github.com/Sledocheck/INAV_Sim`.

Чтобы выпустить обновление:

1. Поднимите `version.py` (например, `0.1.1`).
2. Соберите zip: `python tools/make_release.py`.
3. Создайте релиз с тегом `v0.1.1` и приложите zip:

```bat
gh release create v0.1.1 dist\INAV_Sim-v0.1.1.zip -t "v0.1.1" -n "Что нового"
```

Приложение при запуске запрашивает `releases/latest`, сравнивает версии и
предлагает обновиться. Если релиза нет или нет интернета — просто
продолжает работу.

## Если что-то не работает

- **«JSBSim не установлен»** — выполните `setup.bat`.
- **«UDP 49100: ...»** — порт занят. Закройте другой симулятор или смените
  X-Plane-порт в настройках.
- **Долго «Калибровка»** — INAV калибрует гироскоп на старте (до ~12 с).
  Не двигайте борт и не жмите ARM, пока не появится «связь».
- **Нет OSD** — проверьте, что в `sitl/eeprom.bin` включён OSD и UART3 =
  MSP DisplayPort (`tools/make_eeprom.py`), и что порт 5762 свободен.
- **Пульт не найден** — проверьте `Файл → Настройки → Пульт`; как fallback
  работают клавиши.
- **Самолёт валится** — включён режим AH? Нажмите `M`. Если борт не
  взведён, газ не действует.
- **Не запускается SITL** — посмотрите `sitl/sitl.log`; проверьте, что
  `inav_SITL.exe` и `cygwin1.dll` лежат рядом.

## Лицензии

Код приложения — MIT. INAV SITL и шрифт OSD — GPLv3 (INA V Project),
JSBSim — LGPL-2.1, PySide6 — LGPLv3. Подробности в `NOTICE.md`.
