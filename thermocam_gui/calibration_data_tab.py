"""Calibration-data viewer tab: trigger flash dump, show img_bg / gain / badpts."""
import time
import numpy as np
from PySide6.QtCore import Slot
from PySide6.QtWidgets import (QWidget, QVBoxLayout, QHBoxLayout, QPushButton,
                               QLabel, QLineEdit, QComboBox, QPlainTextEdit,
                               QFrame)
from matplotlib.backends.backend_qtagg import FigureCanvasQTAgg as FigureCanvas
from matplotlib.figure import Figure

from .flash_dump_io import save_flash_dump
from .workers import CalibWorker
from .port_utils import list_serial_ports, probe_active_port
from .ui_style import (style_figure, style_card, style_summary_card,
                       empty_placeholder, kv_block, set_button_kind,
                       SUBTITLE_FG, AXIS_FG, CARD_EDGE)


class CalibrationTab(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.worker = None
        self._active_dump_context = None

        root = QVBoxLayout(self)
        root.setContentsMargins(18, 16, 18, 16)
        root.setSpacing(10)

        command_bar = QFrame()
        command_bar.setObjectName('commandBar')
        ctrl = QHBoxLayout(command_bar)
        ctrl.setContentsMargins(12, 8, 12, 8)
        ctrl.setSpacing(8)
        self.port_cb = QComboBox()
        self.port_cb.setEditable(True)
        self.port_cb.setMinimumWidth(180)
        self.refresh_btn = QPushButton("1.扫描串口")
        self.device_label_edit = QLineEdit()
        self.device_label_edit.setPlaceholderText("例如 v1")
        self.device_label_edit.setMaximumWidth(100)
        self.baud_def_edit = QLineEdit("2000000")
        self.baud_def_edit.setMaximumWidth(100)
        self.baud_edit = QLineEdit("5000000")
        self.baud_edit.setMaximumWidth(100)
        self.run_btn = QPushButton("2.读取并保存")
        set_button_kind(self.refresh_btn, 'step')
        set_button_kind(self.run_btn, 'primary')

        self.refresh_btn.clicked.connect(self._refresh_ports)
        self.run_btn.clicked.connect(self._run)

        port_label = QLabel("串口")
        port_label.setObjectName('fieldLabel')
        device_label = QLabel("设备标签")
        device_label.setObjectName('fieldLabel')
        trigger_label = QLabel("触发波特率")
        trigger_label.setObjectName('fieldLabel')
        read_label = QLabel("读取波特率")
        read_label.setObjectName('fieldLabel')
        ctrl.addWidget(port_label); ctrl.addWidget(self.port_cb)
        ctrl.addWidget(self.refresh_btn)
        ctrl.addSpacing(8)
        ctrl.addWidget(device_label); ctrl.addWidget(self.device_label_edit)
        ctrl.addSpacing(8)
        ctrl.addWidget(trigger_label); ctrl.addWidget(self.baud_def_edit)
        ctrl.addWidget(read_label); ctrl.addWidget(self.baud_edit)
        ctrl.addWidget(self.run_btn); ctrl.addStretch(1)
        root.addWidget(command_bar)

        self.log = QPlainTextEdit()
        self.log.setReadOnly(True)
        self.log.setMaximumHeight(124)
        self.log.setPlaceholderText("读取进度和标定数据摘要将在这里显示")
        root.addWidget(self.log)

        self.fig = Figure(figsize=(10, 5))
        style_figure(self.fig)
        self.canvas = FigureCanvas(self.fig)
        root.addWidget(self.canvas, 1)
        self._draw_empty()

        self._refresh_ports()

    def _refresh_ports(self):
        current = self.port_cb.currentText()
        self.port_cb.clear()
        ports = list_serial_ports()
        self.port_cb.addItems(ports)
        if not ports:
            return
        active = probe_active_port(ports)
        if active:
            self.port_cb.setCurrentText(active)
            self._log(f"探测：{active} 上检测到数据流")
        elif current and current in ports:
            self.port_cb.setCurrentText(current)
        else:
            self.port_cb.setCurrentIndex(0)

    def _draw_empty(self):
        self.fig.clear()
        style_figure(self.fig)
        gs = self.fig.add_gridspec(1, 3, width_ratios=[4, 4, 3],
                                   wspace=0.22)
        ax1 = self.fig.add_subplot(gs[0, 0])
        style_card(ax1, "img_bg", "FFC 背景")
        empty_placeholder(ax1, msg='·  暂无数据  ·')

        ax2 = self.fig.add_subplot(gs[0, 1])
        style_card(ax2, "gain", "逐像素校正")
        empty_placeholder(ax2, msg='·  暂无数据  ·')

        ax3 = self.fig.add_subplot(gs[0, 2])
        style_summary_card(ax3, "汇总")
        ax3.text(0.04, 0.55,
                 "填写设备标签后，点击“2.读取并保存”\n"
                 "从 Flash 只读导出原始标定数组。\n"
                 "结果保存到 cali_data_backup。",
                 transform=ax3.transAxes, va='center', ha='left',
                 color=SUBTITLE_FG, fontsize=9)

        self.fig.subplots_adjust(left=0.04, right=0.98,
                                 top=0.88, bottom=0.08,
                                 wspace=0.24)
        self.canvas.draw_idle()

    def _log(self, msg):
        ts = time.strftime('%H:%M:%S')
        self.log.appendPlainText(f"[{ts}] {msg}")

    def _run(self):
        if self.worker is not None and self.worker.isRunning():
            return
        port = self.port_cb.currentText().strip()
        if not port:
            self._log("请先选择串口")
            return
        device_label = self.device_label_edit.text().strip()
        if not device_label:
            self._log("请填写设备标签，例如 v1、v2 或 v3")
            return
        try:
            baud_def = int(self.baud_def_edit.text().strip())
            baud = int(self.baud_edit.text().strip())
        except ValueError:
            self._log("波特率无效")
            return
        self.run_btn.setEnabled(False)
        self.log.clear()
        self._active_dump_context = {
            'device_label': device_label,
            'port': port,
            'trigger_baud': baud_def,
            'read_baud': baud,
        }
        self.worker = CalibWorker(port, baud_def, baud, self)
        self.worker.progress.connect(self._log)
        self.worker.success.connect(self._on_success)
        self.worker.error.connect(self._on_error)
        self.worker.start()

    @Slot(object, object, list)
    def _on_success(self, img_bg, gain, badpts):
        context = self._active_dump_context
        self._active_dump_context = None
        if context is None:
            self._log("[保存失败] 缺少本次读取上下文，请重新读取")
        else:
            try:
                output_dir, digest = save_flash_dump(
                    img_bg, gain, badpts, **context)
                self._log(f"原始数据已保存：{output_dir}")
                self._log(f"NPZ SHA256：{digest}")
            except Exception as exc:
                self._log(f"[保存失败] {exc}")

        self._log("-" * 40)
        self._log(f"  img_bg 均值 : {img_bg.mean():.2f}   最小/最大: {img_bg.min()}/{img_bg.max()}")
        self._log(f"  gain   均值 : {gain.mean():.4f}   方差: {gain.std():.4f}")
        self._log(f"  gain  p2/p98: {np.percentile(gain, 2):.3f} / {np.percentile(gain, 98):.3f}")
        self._log(f"  坏点        : {len(badpts)}   {badpts}")

        vmin, vmax = np.percentile(gain, [2, 98])
        if vmin >= vmax:
            vmin, vmax = float(gain.min()), float(gain.max())

        self.fig.clear()
        style_figure(self.fig)
        gs = self.fig.add_gridspec(1, 3, width_ratios=[4, 4, 3], wspace=0.22)

        ax1 = self.fig.add_subplot(gs[0, 0])
        style_card(ax1, "img_bg", "FFC 背景")
        im1 = ax1.imshow(img_bg, cmap='gray')
        cb1 = self.fig.colorbar(im1, ax=ax1, fraction=0.046, pad=0.03)
        cb1.ax.tick_params(colors=AXIS_FG, labelsize=7)
        for sp in cb1.ax.spines.values():
            sp.set_edgecolor(CARD_EDGE)
        if badpts:
            ys = [p[0] for p in badpts]; xs = [p[1] for p in badpts]
            ax1.scatter(xs, ys, c='#ff6b6b', s=45, marker='x', linewidths=1.6)

        ax2 = self.fig.add_subplot(gs[0, 1])
        style_card(ax2, "gain", f"p2~p98 归一化")
        im2 = ax2.imshow(gain, cmap='viridis', vmin=vmin, vmax=vmax)
        cb2 = self.fig.colorbar(im2, ax=ax2, fraction=0.046, pad=0.03)
        cb2.ax.tick_params(colors=AXIS_FG, labelsize=7)
        for sp in cb2.ax.spines.values():
            sp.set_edgecolor(CARD_EDGE)

        ax3 = self.fig.add_subplot(gs[0, 2])
        style_summary_card(ax3, "汇总")
        kv_block(ax3, [
            ('img_bg  均值', f"{img_bg.mean():.1f}"),
            ('img_bg  最小', f"{img_bg.min()}"),
            ('img_bg  最大', f"{img_bg.max()}"),
            ('gain    μ',    f"{gain.mean():.4f}"),
            ('gain    σ',    f"{gain.std():.4f}"),
            ('gain    p2',   f"{vmin:.4f}"),
            ('gain    p98',  f"{vmax:.4f}"),
            ('坏点',         f"{len(badpts)}"),
        ], x=0.05, y_top=0.80, line_h=0.085)

        self.fig.subplots_adjust(left=0.04, right=0.98,
                                 top=0.88, bottom=0.08, wspace=0.24)
        self.canvas.draw_idle()

        self.run_btn.setEnabled(True)

    def _on_error(self, msg):
        self._active_dump_context = None
        self._log(f"[错误] {msg}")
        self.run_btn.setEnabled(True)
