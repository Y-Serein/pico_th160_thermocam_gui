#!/usr/bin/env python3
"""TN160 工具箱 — 一体化 GUI (校准 dump + 运行记录)."""
import sys

import matplotlib
matplotlib.rcParams['font.sans-serif'] = [
    'Microsoft YaHei', 'SimHei', 'Noto Sans CJK SC',
    'PingFang SC', 'WenQuanYi Zen Hei', 'DejaVu Sans']
matplotlib.rcParams['axes.unicode_minus'] = False

from PySide6.QtCore import Qt
from PySide6.QtGui import QPalette, QColor
from PySide6.QtWidgets import QApplication, QMainWindow, QTabWidget, QStatusBar

from monitor_tab import MonitorTab
from calibrate_tab import CalibrateTab
from calibration_tab import CalibrationTab
from flash_tab import FlashTab


def apply_dark_palette(app: QApplication):
    app.setStyle("Fusion")
    p = QPalette()
    p.setColor(QPalette.Window,         QColor("#1a1a1a"))
    p.setColor(QPalette.WindowText,     QColor("#e0e0e0"))
    p.setColor(QPalette.Base,           QColor("#111111"))
    p.setColor(QPalette.AlternateBase,  QColor("#202020"))
    p.setColor(QPalette.Text,           QColor("#e0e0e0"))
    p.setColor(QPalette.Button,         QColor("#262626"))
    p.setColor(QPalette.ButtonText,     QColor("#e0e0e0"))
    p.setColor(QPalette.Highlight,      QColor("#2b6cb0"))
    p.setColor(QPalette.HighlightedText, QColor("#ffffff"))
    p.setColor(QPalette.ToolTipBase,    QColor("#333333"))
    p.setColor(QPalette.ToolTipText,    QColor("#e0e0e0"))
    app.setPalette(p)


class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("TN160 工具箱（产测版）")
        self.resize(1180, 880)

        self.tabs = QTabWidget()
        self.flash_tab = FlashTab()
        self.monitor_tab = MonitorTab()
        self.calibrate_tab = CalibrateTab()
        self.calib_tab = CalibrationTab()
        self.tabs.addTab(self.flash_tab, "固件烧录")
        self.tabs.addTab(self.monitor_tab, "实时监控")
        self.tabs.addTab(self.calibrate_tab, "标定")
        self.tabs.addTab(self.calib_tab, "标定数据")
        self.setCentralWidget(self.tabs)

        sb = QStatusBar()
        sb.showMessage("请在上方选择 TN160 串口，设置波特率（默认 2M），然后点击「开始」。")
        self.setStatusBar(sb)

    def closeEvent(self, e):
        self.monitor_tab.shutdown()
        self.flash_tab.shutdown()
        super().closeEvent(e)


def main():
    app = QApplication(sys.argv)
    apply_dark_palette(app)
    win = MainWindow()
    win.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
