"""Автообновление с GitHub Releases.

При запуске приложение (если включено в настройках) спрашивает последний
релиз и, если он новее, показывает окно с предложением обновиться.
Обновление скачивает zip-ассет, распаковывает во временную папку и
запускает маленький .bat, который ждёт выхода программы, копирует файлы
и снова её запускает. Настройки (config.json), logs/ и userdata/ не
трогаются.
"""
from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import tempfile
import urllib.error
import urllib.request
import zipfile
from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QApplication, QMessageBox, QProgressDialog

from .config import app_dir

API = "https://api.github.com/repos/%s/releases/latest"
USER_AGENT = "INAV-Sim-Updater"


def version_tuple(text: str) -> tuple:
    nums = re.findall(r"\d+", str(text or ""))
    return tuple(int(n) for n in nums[:4]) if nums else (0,)


def current_version() -> str:
    try:
        from version import __version__  # noqa: PLC0415
        return str(__version__)
    except Exception:  # noqa: BLE001
        return "0.0.0"


def check_latest(repo: str, timeout: float = 3.5) -> dict | None:
    url = API % repo
    req = urllib.request.Request(url, headers={
        "User-Agent": USER_AGENT,
        "Accept": "application/vnd.github+json",
    })
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            data = json.loads(resp.read().decode("utf-8", "replace"))
    except (urllib.error.URLError, urllib.error.HTTPError, OSError, ValueError):
        return None
    tag = str(data.get("tag_name") or data.get("name") or "")
    assets = data.get("assets") or []
    zip_url = ""
    for a in assets:
        name = str(a.get("name") or "")
        if name.lower().endswith(".zip"):
            zip_url = str(a.get("browser_download_url") or "")
            if name.lower().startswith("inav"):
                break
    if not tag or not zip_url:
        return None
    return {
        "tag": tag,
        "version": version_tuple(tag),
        "name": str(data.get("name") or tag),
        "body": str(data.get("body") or ""),
        "zip_url": zip_url,
        "page": str(data.get("html_url") or ""),
    }


def _download(url: str, dest: Path, progress=None) -> bool:
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    try:
        with urllib.request.urlopen(req, timeout=20) as resp, \
                open(dest, "wb") as fh:
            total = int(resp.headers.get("Content-Length") or 0)
            done = 0
            while True:
                chunk = resp.read(65536)
                if not chunk:
                    break
                fh.write(chunk)
                done += len(chunk)
                if progress is not None:
                    progress(done, total)
        return True
    except (urllib.error.URLError, urllib.error.HTTPError, OSError):
        return False


def _find_root(folder: Path) -> Path:
    if (folder / "main.py").is_file():
        return folder
    for sub in folder.iterdir():
        if sub.is_dir() and (sub / "main.py").is_file():
            return sub
    return folder


def _write_bat(staging: Path, app: Path, pid: int) -> Path:
    bat = staging.parent / "apply_update.bat"
    text = f"""@echo off
setlocal
title INAV Sim update
:wait
tasklist /FI "PID eq {pid}" 2>nul | find "{pid}" >nul
if not errorlevel 1 (
  timeout /t 1 /nobreak >nul
  goto wait
)
robocopy "{staging}" "{app}" /E /R:2 /W:1 /XD .git .venv userdata logs __pycache__ /XF config.json sitl.log >nul
start "" "{app}\\run.bat"
del "%~f0"
"""
    bat.write_text(text, encoding="utf-8")
    return bat


def apply_update(zip_path: Path) -> bool:
    app = app_dir()
    work = Path(tempfile.mkdtemp(prefix="inav_sim_update_"))
    try:
        with zipfile.ZipFile(zip_path) as zf:
            zf.extractall(work)
    except (OSError, zipfile.BadZipFile):
        return False
    staging = _find_root(work)
    if not (staging / "main.py").is_file():
        return False
    bat = _write_bat(staging, app, os.getpid())
    try:
        subprocess.Popen(["cmd", "/c", str(bat)],
                         creationflags=getattr(subprocess, "CREATE_NEW_CONSOLE", 0),
                         close_fds=True)
    except OSError:
        return False
    QApplication.quit()
    return True


def ask_and_update(parent, cfg: dict, info: dict) -> None:
    cur = current_version()
    body = info.get("body") or ""
    if len(body) > 1200:
        body = body[:1200] + "…"
    text = ("Доступно обновление INAV Sim.\n\n"
            "Установлено: %s\nНовое: %s\n\n%s\n\nОбновить сейчас?"
            % (cur, info["tag"], body))
    box = QMessageBox(parent)
    box.setWindowTitle("Обновление INAV Sim")
    box.setIcon(QMessageBox.Question)
    box.setText(text)
    b_yes = box.addButton("Обновить", QMessageBox.AcceptRole)
    b_later = box.addButton("Позже", QMessageBox.RejectRole)
    b_skip = box.addButton("Пропустить версию", QMessageBox.DestructiveRole)
    box.setDefaultButton(b_yes)
    box.exec()
    if box.clickedButton() is b_skip:
        cfg["skipped_version"] = info["tag"]
        from .config import save  # noqa: PLC0415
        save(cfg)
        return
    if box.clickedButton() is not b_later:
        return
    dest = Path(tempfile.gettempdir()) / ("inav_sim_%s.zip" % info["tag"].lstrip("v"))
    dlg = QProgressDialog("Скачивание обновления…", "Отмена", 0, 100, parent)
    dlg.setWindowTitle("Обновление INAV Sim")
    dlg.setWindowModality(Qt.WindowModal)
    dlg.setMinimumDuration(0)
    state = {"cancel": False}

    def progress(done: int, total: int) -> None:
        if total > 0:
            dlg.setValue(int(done * 100 / total))
        QApplication.processEvents()
        if dlg.wasCanceled():
            state["cancel"] = True
            raise OSError("cancelled")

    ok = False
    try:
        ok = _download(info["zip_url"], dest, progress)
    except OSError:
        ok = False
    dlg.close()
    if state["cancel"] or not ok:
        QMessageBox.warning(parent, "Обновление",
                            "Не удалось скачать обновление.")
        return
    if not apply_update(dest):
        QMessageBox.warning(parent, "Обновление",
                            "Не удалось распаковать обновление.")
        return
    QMessageBox.information(parent, "Обновление",
                            "Обновление установлено. Программа перезапустится.")


def maybe_check_updates(parent, cfg: dict, interactive: bool = False) -> None:
    repo = str(cfg.get("github_repo", "Sledocheck/INAV_Sim"))
    info = check_latest(repo)
    if info is None:
        if interactive:
            QMessageBox.information(parent, "Обновление",
                                    "Не удалось проверить обновления.\n"
                                    "Проверьте интернет и имя репозитория.")
        return
    cur = version_tuple(current_version())
    if info["version"] <= cur:
        if interactive:
            QMessageBox.information(parent, "Обновление",
                                    "У вас последняя версия: %s"
                                    % current_version())
        return
    if not interactive and str(cfg.get("skipped_version", "")) == info["tag"]:
        return
    ask_and_update(parent, cfg, info)


__all__ = ["check_latest", "maybe_check_updates", "current_version",
           "version_tuple", "apply_update"]
