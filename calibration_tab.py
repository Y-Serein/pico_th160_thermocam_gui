"""Calibration-data viewer tab: trigger flash dump, show img_bg / gain / badpts."""
import time
import numpy as np
from PySide6.QtCore import Slot
from PySide6.QtWidgets import (QWidget, QVBoxLayout, QHBoxLayout, QPushButton,
                               QLabel, QLineEdit, QComboBox, QPlainTextEdit)
from matplotlib.backends.backend_qtagg import FigureCanvasQTAgg as FigureCanvas
from matplotlib.figure import Figure

from workers import CalibWorker
from port_utils import list_serial_ports, probe_active_port
from ui_style import (style_figure, style_card, style_summary_card,
                      empty_placeholder, kv_block)


class CalibrationTab(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.worker = None

        root = QVBoxLayout(self)

        ctrl = QHBoxLayout()
        self.port_cb = QComboBox()
        self.port_cb.setEditable(True)
        self.port_cb.setMinimumWidth(180)
        self.refresh_btn = QPushButton("Scan")
        self.baud_def_edit = QLineEdit("2000000")
        self.baud_def_edit.setMaximumWidth(100)
        self.baud_edit = QLineEdit("5000000")
        self.baud_edit.setMaximumWidth(100)
        self.run_btn = QPushButton("Trigger Dump")

        self.refresh_btn.clicked.connect(self._refresh_ports)
        self.run_btn.clicked.connect(self._run)

        ctrl.addWidget(QLabel("Port:")); ctrl.addWidget(self.port_cb)
        ctrl.addWidget(self.refresh_btn)
        ctrl.addWidget(QLabel("  Trigger baud:")); ctrl.addWidget(self.baud_def_edit)
        ctrl.addWidget(QLabel("  Dump baud:")); ctrl.addWidget(self.baud_edit)
        ctrl.addWidget(self.run_btn); ctrl.addStretch(1)
        root.addLayout(ctrl)

        self.log = QPlainTextEdit()
        self.log.setReadOnly(True)
        self.log.setMaximumHeight(140)
        self.log.setStyleSheet("background:#111; color:#ddd; font-family:monospace;")
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
            self._log(f"probe: active stream on {active}")
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
        style_card(ax1, "img_bg", "FFC background")
        empty_placeholder(ax1, msg='·  no dump yet  ·')

        ax2 = self.fig.add_subplot(gs[0, 1])
        style_card(ax2, "gain", "per-pixel correction")
        empty_placeholder(ax2, msg='·  no dump yet  ·')

        ax3 = self.fig.add_subplot(gs[0, 2])
        style_summary_card(ax3, "summary")
        ax3.text(0.04, 0.55,
                 "Click  Trigger Dump  to read the\n"
                 "stored calibration back from flash.\n"
                 "This is read-only — safe to run\n"
                 "anytime.",
                 transform=ax3.transAxes, va='center', ha='left',
                 color='#8a90ab', fontsize=9, family='monospace')

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
            self._log("select a port first")
            return
        try:
            baud_def = int(self.baud_def_edit.text().strip())
            baud = int(self.baud_edit.text().strip())
        except ValueError:
            self._log("invalid baud")
            return
        self.run_btn.setEnabled(False)
        self.log.clear()
        self.worker = CalibWorker(port, baud_def, baud, self)
        self.worker.progress.connect(self._log)
        self.worker.success.connect(self._on_success)
        self.worker.error.connect(self._on_error)
        self.worker.start()

    @Slot(object, object, list)
    def _on_success(self, img_bg, gain, badpts):
        self._log("-" * 40)
        self._log(f"  img_bg mean : {img_bg.mean():.2f}   min/max: {img_bg.min()}/{img_bg.max()}")
        self._log(f"  gain   mean : {gain.mean():.4f}   std: {gain.std():.4f}")
        self._log(f"  gain  p2/p98: {np.percentile(gain, 2):.3f} / {np.percentile(gain, 98):.3f}")
        self._log(f"  bad points  : {len(badpts)}   {badpts}")

        vmin, vmax = np.percentile(gain, [2, 98])
        if vmin >= vmax:
            vmin, vmax = float(gain.min()), float(gain.max())

        self.fig.clear()
        style_figure(self.fig)
        gs = self.fig.add_gridspec(1, 3, width_ratios=[4, 4, 3], wspace=0.22)

        ax1 = self.fig.add_subplot(gs[0, 0])
        style_card(ax1, "img_bg", "FFC background")
        im1 = ax1.imshow(img_bg, cmap='gray')
        cb1 = self.fig.colorbar(im1, ax=ax1, fraction=0.046, pad=0.03)
        cb1.ax.tick_params(colors='#8c93af', labelsize=7)
        for sp in cb1.ax.spines.values():
            sp.set_edgecolor('#2e3246')
        if badpts:
            ys = [p[0] for p in badpts]; xs = [p[1] for p in badpts]
            ax1.scatter(xs, ys, c='#ff6b6b', s=45, marker='x', linewidths=1.6)

        ax2 = self.fig.add_subplot(gs[0, 1])
        style_card(ax2, "gain", f"p2~p98 normalized")
        im2 = ax2.imshow(gain, cmap='jet', vmin=vmin, vmax=vmax)
        cb2 = self.fig.colorbar(im2, ax=ax2, fraction=0.046, pad=0.03)
        cb2.ax.tick_params(colors='#8c93af', labelsize=7)
        for sp in cb2.ax.spines.values():
            sp.set_edgecolor('#2e3246')

        ax3 = self.fig.add_subplot(gs[0, 2])
        style_summary_card(ax3, "summary")
        kv_block(ax3, [
            ('img_bg  mean', f"{img_bg.mean():.1f}"),
            ('img_bg  min',  f"{img_bg.min()}"),
            ('img_bg  max',  f"{img_bg.max()}"),
            ('gain    μ',    f"{gain.mean():.4f}"),
            ('gain    σ',    f"{gain.std():.4f}"),
            ('gain    p2',   f"{vmin:.4f}"),
            ('gain    p98',  f"{vmax:.4f}"),
            ('bad points',   f"{len(badpts)}"),
        ], x=0.05, y_top=0.80, line_h=0.085)

        self.fig.subplots_adjust(left=0.04, right=0.98,
                                 top=0.88, bottom=0.08, wspace=0.24)
        self.canvas.draw_idle()

        self.run_btn.setEnabled(True)

    def _on_error(self, msg):
        self._log(f"[error] {msg}")
        self.run_btn.setEnabled(True)
