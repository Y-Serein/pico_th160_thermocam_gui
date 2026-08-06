#!/usr/bin/env python3
"""TN160 Thermal Workbench — production and thermal diagnostics."""
import sys

import matplotlib
matplotlib.rcParams['font.sans-serif'] = [
    'Microsoft YaHei', 'SimHei', 'Noto Sans CJK SC',
    'PingFang SC', 'WenQuanYi Zen Hei', 'DejaVu Sans']
matplotlib.rcParams['axes.unicode_minus'] = False

from PySide6.QtCore import Qt
from PySide6.QtGui import QPalette, QColor, QFont, QFontDatabase
from PySide6.QtWidgets import (QApplication, QMainWindow, QStatusBar, QWidget,
                               QHBoxLayout, QVBoxLayout, QFrame, QLabel,
                               QListWidget, QListWidgetItem, QStackedWidget)

from .monitor_tab import MonitorTab
from .device_calibration_tab import CalibrateTab
from .calibration_data_tab import CalibrationTab
from .thermal_capture_tab import ThermalCaptureTab
from .flash_tab import FlashTab
from .ui_style import (APP_STYLESHEET, APP_BG, SIDEBAR_BG, FIG_BG, TITLE_FG,
                       BODY_FG, ACCENT_FG)


def apply_dark_palette(app: QApplication):
    app.setStyle("Fusion")
    families = set(QFontDatabase.families())
    ui_family = next((name for name in (
        'Microsoft YaHei UI', 'Microsoft YaHei', 'Noto Sans CJK SC',
        'Source Han Sans SC', 'Segoe UI') if name in families), 'Segoe UI')
    app.setFont(QFont(ui_family, 9))
    p = QPalette()
    p.setColor(QPalette.Window,         QColor(APP_BG))
    p.setColor(QPalette.WindowText,     QColor(TITLE_FG))
    p.setColor(QPalette.Base,           QColor(FIG_BG))
    p.setColor(QPalette.AlternateBase,  QColor(SIDEBAR_BG))
    p.setColor(QPalette.Text,           QColor(BODY_FG))
    p.setColor(QPalette.Button,         QColor("#20262d"))
    p.setColor(QPalette.ButtonText,     QColor(BODY_FG))
    p.setColor(QPalette.Highlight,      QColor(ACCENT_FG))
    p.setColor(QPalette.HighlightedText, QColor("#ffffff"))
    p.setColor(QPalette.ToolTipBase,    QColor("#20262d"))
    p.setColor(QPalette.ToolTipText,    QColor(TITLE_FG))
    app.setPalette(p)
    app.setStyleSheet(APP_STYLESHEET)


class MainWindow(QMainWindow):
    PAGES = (
        ('固件烧录', '批量写入 UF2，并保护系统盘与大容量设备'),
        ('实时诊断', '观察中心测温、原始量、热状态与运行期 FFC'),
        ('设备标定', '执行 0°C / 50°C 两点标定并写入设备 Flash'),
        ('标定数据', '只读检查背景、增益和坏点数据'),
        ('热状态采集', '采集T0～T3原始帧，分离热状态下的增益与零点漂移'),
    )

    def __init__(self):
        super().__init__()
        self.setWindowTitle("TN160 Thermal Workbench")
        self.setMinimumSize(1080, 720)
        self.resize(1360, 900)

        self.flash_tab = FlashTab()
        self.monitor_tab = MonitorTab()
        self.calibrate_tab = CalibrateTab()
        self.calib_tab = CalibrationTab()
        self.thermal_capture_tab = ThermalCaptureTab()

        shell = QWidget()
        shell.setObjectName('appShell')
        shell_layout = QHBoxLayout(shell)
        shell_layout.setContentsMargins(0, 0, 0, 0)
        shell_layout.setSpacing(0)

        sidebar = QFrame()
        sidebar.setObjectName('sidebar')
        sidebar.setFixedWidth(216)
        side_layout = QVBoxLayout(sidebar)
        side_layout.setContentsMargins(18, 22, 16, 18)
        side_layout.setSpacing(4)

        mark = QLabel('THERMAL INSTRUMENT')
        mark.setObjectName('brandMark')
        title = QLabel('TN160')
        title.setObjectName('brandTitle')
        sub = QLabel('Production & diagnostics')
        sub.setObjectName('brandSub')
        side_layout.addWidget(mark)
        side_layout.addWidget(title)
        side_layout.addWidget(sub)
        side_layout.addSpacing(24)

        self.nav = QListWidget()
        self.nav.setObjectName('primaryNav')
        self.nav.setFocusPolicy(Qt.StrongFocus)
        self.nav.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        for name, _ in self.PAGES:
            self.nav.addItem(QListWidgetItem(name))
        side_layout.addWidget(self.nav, 1)

        meta = QLabel('RP2350  /  T-NV160\n160 × 120  ·  UART / UVC')
        meta.setObjectName('sidebarMeta')
        side_layout.addWidget(meta)
        shell_layout.addWidget(sidebar)

        content = QFrame()
        content.setObjectName('contentShell')
        content_layout = QVBoxLayout(content)
        content_layout.setContentsMargins(0, 0, 0, 0)
        content_layout.setSpacing(0)

        header = QFrame()
        header.setObjectName('pageHeader')
        header_layout = QHBoxLayout(header)
        header_layout.setContentsMargins(22, 13, 22, 13)
        header_layout.setSpacing(12)
        title_col = QVBoxLayout()
        title_col.setSpacing(2)
        self.page_title = QLabel()
        self.page_title.setObjectName('pageTitle')
        self.page_subtitle = QLabel()
        self.page_subtitle.setObjectName('pageSubtitle')
        title_col.addWidget(self.page_title)
        title_col.addWidget(self.page_subtitle)
        header_layout.addLayout(title_col, 1)
        profile = QLabel('0–50°C CAL  ·  DIAGNOSTIC BUILD')
        profile.setObjectName('profileChip')
        header_layout.addWidget(profile)
        content_layout.addWidget(header)

        self.stack = QStackedWidget()
        self.tabs = self.stack  # compatibility for existing local test helpers
        self.stack.addWidget(self.flash_tab)
        self.stack.addWidget(self.monitor_tab)
        self.stack.addWidget(self.calibrate_tab)
        self.stack.addWidget(self.calib_tab)
        self.stack.addWidget(self.thermal_capture_tab)
        content_layout.addWidget(self.stack, 1)
        shell_layout.addWidget(content, 1)
        self.setCentralWidget(shell)

        self.nav.currentRowChanged.connect(self._select_page)
        self.nav.setCurrentRow(0)

        sb = QStatusBar()
        sb.showMessage("TN160 · Full-Speed Bulk UVC · 160×120 YUY2 @ 10 fps")
        self.setStatusBar(sb)

    def _select_page(self, index):
        if not 0 <= index < len(self.PAGES):
            return
        self.stack.setCurrentIndex(index)
        self.page_title.setText(self.PAGES[index][0])
        self.page_subtitle.setText(self.PAGES[index][1])

    def closeEvent(self, e):
        self.monitor_tab.shutdown()
        self.flash_tab.shutdown()
        self.thermal_capture_tab.shutdown()
        super().closeEvent(e)


def main():
    app = QApplication(sys.argv)
    apply_dark_palette(app)
    win = MainWindow()
    win.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
