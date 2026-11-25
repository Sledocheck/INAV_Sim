"""Собрать sitl/eeprom.bin из базового дампа FLYWING.

Что делает:
  * включает FEATURE_OSD, GPS, VBAT, CURRENT_METER;
  * отдаёт UART3 (TCP 5762) под MSP DisplayPort (OSD);
  * задаёт понятную раскладку OSD для экрана 30x16;
  * пересчитывает CRC16/XMODEM.

Запуск:  python tools/make_eeprom.py
"""
from __future__ import annotations

import struct
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "sitl" / "base_flywing.bin"
DST = ROOT / "sitl" / "eeprom.bin"

FEATURE_VBAT = 1 << 1
FEATURE_GPS = 1 << 7
FEATURE_CURRENT_METER = 1 << 11
FEATURE_OSD = 1 << 29
FUNCTION_MSP_OSD = 1 << 25
BAUD_115200 = 8
OSD_VISIBLE_FLAG = 0x2000

# OSD_POS(x, y) = x | (y << 6), плюс флаг видимости.
def pos(x: int, y: int) -> int:
    return (x & 0x3F) | ((y & 0x3F) << 6) | OSD_VISIBLE_FLAG


# Индексы osd_items_e
OSD = {
    "rssi": 0, "batt": 1, "crosshairs": 2, "horizon": 3, "ontime": 5,
    "flymode": 7, "craft": 8, "throttle": 9, "current": 11, "mah": 12,
    "gps_speed": 13, "sats": 14, "altitude": 15, "home_dir": 22,
    "home_dist": 23, "heading": 24, "vario": 25, "air_speed": 27,
    "messages": 30, "cell_voltage": 32, "batt_percent": 38,
}

LAYOUT = {
    "flymode": pos(1, 0),
    "ontime": pos(23, 0),
    "altitude": pos(1, 1),
    "heading": pos(24, 1),
    "gps_speed": pos(1, 2),
    "home_dist": pos(24, 2),
    "batt": pos(1, 3),
    "sats": pos(23, 3),
    "throttle": pos(1, 4),
    "mah": pos(10, 4),
    "current": pos(18, 4),
    "batt_percent": pos(1, 5),
    "cell_voltage": pos(24, 5),
    "vario": pos(24, 8),
    "crosshairs": pos(15, 8),
    "horizon": pos(15, 8),
    "messages": pos(1, 15),
}

LAYOUT_COUNT = 4
ITEM_COUNT = 167


def crc16(crc: int, b: int) -> int:
    crc ^= b << 8
    for _ in range(8):
        crc = ((crc << 1) ^ 0x1021) & 0xFFFF if (crc & 0x8000) \
            else (crc << 1) & 0xFFFF
    return crc


def crc16_buf(data: bytes) -> int:
    crc = 0
    for b in data:
        crc = crc16(crc, b)
    return crc


def main() -> None:
    data = bytearray(SRC.read_bytes())
    off = 1
    records = {}
    while True:
        size, pgn, _ver, flags = struct.unpack_from("<HHBB", data, off)
        if size == 0:
            end = off
            break
        records.setdefault((pgn, flags), []).append((off, size))
        off += size

    # features
    feat_off = records[(19, 0)][0][0]
    feat = struct.unpack_from("<I", data, feat_off + 6)[0]
    feat |= FEATURE_OSD | FEATURE_GPS | FEATURE_VBAT | FEATURE_CURRENT_METER
    struct.pack_into("<I", data, feat_off + 6, feat)

    # UART3 -> MSP_OSD
    ser_off = records[(13, 0)][0][0]
    base = ser_off + 6
    o = base + 2 * 12
    mask, ident, msp_b, gps_b, periph_b, tele_b = struct.unpack_from(
        "<IIBBBB", data, o)
    mask |= FUNCTION_MSP_OSD
    struct.pack_into("<IIBBBB", data, o, mask, ident, BAUD_115200, gps_b,
                     BAUD_115200, tele_b)

    # GPS: SITL кормит координаты через xplane.c (gpsFakeSet), а не через
    # serial. Без provider=FAKE gpsInit не находит порт и снимает FEATURE_GPS.
    gps_off = records[(30, 0)][0][0] + 6
    struct.pack_into("<i", data, gps_off, 2)   # GPS_PROVIDER_FAKE

    # battery meters: в SITL напряжение приходит от fake-сенсора
    # (xplane.c: fakeBattSensorSetVbat(16.8V)), поэтому тип = FAKE.
    batt_off = records[(45, 0)][0][0] + 6
    # batteryMetersConfig_t: voltage.type по смещению 4,
    # current.type — по смещению 12.
    struct.pack_into("<i", data, batt_off + 4, 3)   # VOLTAGE_SENSOR_FAKE
    struct.pack_into("<i", data, batt_off + 12, 3)  # CURRENT_SENSOR_FAKE

    # OSD: не мигать высотой. В симуляторе старт в воздухе, и «ниже
    # домашней высоты на 5 м» — норма, а не авария.
    osd_off = records[(1004, 0)][0][0] + 6
    struct.pack_into("<H", data, osd_off + 4, 0)   # alt_alarm
    struct.pack_into("<H", data, osd_off + 8, 0)   # neg_alt_alarm

    # OSD layout 0
    lay_off = records[(1025, 0)][0][0] + 6
    data[lay_off:lay_off + LAYOUT_COUNT * ITEM_COUNT * 2] = \
        b"\x00" * (LAYOUT_COUNT * ITEM_COUNT * 2)
    for name, value in LAYOUT.items():
        idx = OSD[name]
        struct.pack_into("<H", data, lay_off + idx * 2, value)

    # CRC
    crc = crc16_buf(bytes(data[0:end + 2]))
    struct.pack_into("<H", data, end + 2, crc)
    DST.write_bytes(data)
    print("features: 0x%08X" % feat)
    print("UART3: mask=0x%08X, peripheral baud idx %d" % (mask, BAUD_115200))
    print("OSD layout items: %d" % len(LAYOUT))
    print("wrote %s (crc 0x%04X)" % (DST, crc))


if __name__ == "__main__":
    main()
