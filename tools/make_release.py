"""Собрать zip для GitHub Release.

Исключает .git, .venv, config.json, logs, userdata, __pycache__, dist.
Запуск:  python tools/make_release.py
"""
from __future__ import annotations

import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
VERSION = "0.0.0"
for line in (ROOT / "version.py").read_text(encoding="utf-8").splitlines():
    if line.startswith("__version__"):
        VERSION = line.split("=", 1)[1].strip().strip('"\'')
        break

EXCLUDE_DIRS = {".git", ".venv", "__pycache__", "logs", "userdata", "dist",
                "build", ".idea", ".vscode"}
EXCLUDE_FILES = {"config.json", "sitl.log", "eeprom_cli.bin"}
OUT = ROOT / "dist" / ("INAV_Sim-v%s.zip" % VERSION)
OUT.parent.mkdir(parents=True, exist_ok=True)

with zipfile.ZipFile(OUT, "w", zipfile.ZIP_DEFLATED, compresslevel=9) as zf:
    for path in sorted(ROOT.rglob("*")):
        if not path.is_file():
            continue
        rel = path.relative_to(ROOT)
        if any(part in EXCLUDE_DIRS for part in rel.parts):
            continue
        if path.name in EXCLUDE_FILES or path.suffix == ".zip":
            continue
        zf.write(path, rel.as_posix())

print("wrote", OUT, "%.1f MB" % (OUT.stat().st_size / 1e6))
