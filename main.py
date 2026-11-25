from __future__ import annotations

import sys

from PySide6.QtCore import QTimer, Qt
from PySide6.QtGui import QColor, QPalette
from PySide6.QtWidgets import QApplication

from sim import config
from sim.ui import MainWindow
from sim.updater import maybe_check_updates


def main() -> int:
    QApplication.setHighDpiScaleFactorRoundingPolicy(
        Qt.HighDpiScaleFactorRoundingPolicy.PassThrough)
    app = QApplication(sys.argv)
    app.setApplicationName("INAV Sim")
    app.setOrganizationName("Sledocheck")
    app.setStyle("Fusion")
    palette = QPalette()
    palette.setColor(QPalette.Window, QColor(28, 32, 38))
    palette.setColor(QPalette.WindowText, QColor(228, 232, 238))
    palette.setColor(QPalette.Base, QColor(20, 24, 30))
    palette.setColor(QPalette.AlternateBase, QColor(34, 40, 48))
    palette.setColor(QPalette.Text, QColor(228, 232, 238))
    palette.setColor(QPalette.Button, QColor(40, 46, 54))
    palette.setColor(QPalette.ButtonText, QColor(228, 232, 238))
    palette.setColor(QPalette.Highlight, QColor(64, 148, 200))
    palette.setColor(QPalette.HighlightedText, QColor(255, 255, 255))
    app.setPalette(palette)

    cfg = config.load()
    win = MainWindow(cfg)
    win.show()
    if cfg.get("update_check", True):
        QTimer.singleShot(1500, lambda: maybe_check_updates(win, cfg, False))
    return app.exec()


if __name__ == "__main__":
    sys.exit(main())
