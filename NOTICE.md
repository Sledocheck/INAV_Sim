# Сторонние компоненты

## INAV SITL

`sitl/inav_SITL.exe` и `sitl/cygwin1.dll` — сборочная версия INAV SITL
8.0.1-RC1 (коммит 171d00bb), взятая из INAV Configurator 8.0.1.
INAV распространяется по GNU General Public License v3.
Исходники: https://github.com/iNavFlight/inav (тег 8.0.1-RC1 / ветка master).
Лицензия: https://www.gnu.org/licenses/gpl-3.0.html

## Шрифт OSD

`assets/font_inav.png` — пиксельный шрифт OSD из проекта INAV
(Configurator, ресурсы `fonts`). Распространяется в составе INAV по GPLv3.

## JSBSim

`jsbsim` устанавливается из PyPI (`requirements.txt`).
JSBSim — LGPL-2.1: https://github.com/JSBSim-Team/jsbsim

## PySide6 / Qt

PySide6 — LGPLv3, Qt — LGPLv3/GPLv3:
https://www.qt.io/qt-licensing

## EEPROM

`sitl/eeprom.bin` — конфигурация полётника, снятая с борта FLYWING и
изменённая `tools/make_eeprom.py`. Не содержит кода.
