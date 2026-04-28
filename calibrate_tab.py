"""Run a full on-device calibration (0xCC) and visualize results.

Mirrors the behavior of docs/bin/cali.py:
  - sends 0xCC at trigger baud
  - reads 5 payloads (img_l, img_h, img_bg, gain, badpts) + finish flag
  - verifies img_l == img_bg (flash-write sanity)
  - renders img_l / img_h / img_dt / gain / bad points
  - optionally auto-saves PNGs to cali_data_backup/cali_<ts>/
"""
import os
import time
from datetime import datetime
import numpy as np
from PySide6.QtCore import Slot
from PySide6.QtWidgets import (QWidget, QVBoxLayout, QHBoxLayout, QPushButton,
                               QLabel, QLineEdit, QComboBox, QPlainTextEdit,
                               QCheckBox)
from matplotlib.backends.backend_qtagg import FigureCanvasQTAgg as FigureCanvas
from matplotlib.backends.backend_agg import FigureCanvasAgg
from matplotlib.figure import Figure

from workers import CalibRunWorker
from port_utils import list_serial_ports, probe_active_port
from ui_style import (style_figure, style_card, style_summary_card,
                      empty_placeholder, status_badge, kv_block,
                      FIG_BG, TITLE_FG)


CARD_TITLES = [
    ("img_l",      "冷快门"),
    ("img_h",      "热快门"),
    ("img_dt",     "热−冷 对比"),
    ("gain",       "逐像素增益"),
    ("坏点",       "缺陷掩膜"),
    ("汇总",       "统计 · flash 校验"),
]


class CalibrateTab(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.worker = None
        self._last = None

        root = QVBoxLayout(self)

        ctrl = QHBoxLayout()
        self.port_cb = QComboBox()
        self.port_cb.setEditable(True)
        self.port_cb.setMinimumWidth(180)
        self.refresh_btn = QPushButton("1.扫描")
        self.baud_def_edit = QLineEdit("2000000")
        self.baud_def_edit.setMaximumWidth(100)
        self.baud_edit = QLineEdit("5000000")
        self.baud_edit.setMaximumWidth(100)
        self.save_cb = QCheckBox("自动保存 PNG")
        self.save_cb.setChecked(True)
        self.run_btn = QPushButton("2.运行标定")

        self.refresh_btn.clicked.connect(self._refresh_ports)
        self.run_btn.clicked.connect(self._run)

        ctrl.addWidget(QLabel("串口：")); ctrl.addWidget(self.port_cb)
        ctrl.addWidget(self.refresh_btn)
        ctrl.addWidget(QLabel("  触发波特率：")); ctrl.addWidget(self.baud_def_edit)
        ctrl.addWidget(QLabel("  数据波特率：")); ctrl.addWidget(self.baud_edit)
        ctrl.addWidget(self.save_cb)
        ctrl.addWidget(self.run_btn); ctrl.addStretch(1)
        root.addLayout(ctrl)

        self.log = QPlainTextEdit()
        self.log.setReadOnly(True)
        self.log.setMaximumHeight(160)
        self.log.setStyleSheet("background:#111; color:#ddd; font-family:monospace;")
        root.addWidget(self.log)

        self.fig = Figure(figsize=(12, 6.5))
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
        for i, (name, subtitle) in enumerate(CARD_TITLES):
            ax = self.fig.add_subplot(2, 3, i + 1)
            if name == "汇总":
                style_summary_card(ax, title=name)
                ax.text(0.03, 0.55,
                        "点击  2.运行标定  开始。\n"
                        "采集 img_l / img_h / img_bg，\n"
                        "计算 gain 与坏点，然后将结果\n"
                        "写入设备 flash。",
                        transform=ax.transAxes, va='center', ha='left',
                        color='#8a90ab', fontsize=9, family='monospace')
            else:
                style_card(ax, name, subtitle)
                empty_placeholder(ax, msg='·  暂无数据  ·')
        self.fig.subplots_adjust(left=0.04, right=0.98, top=0.93,
                                 bottom=0.07, hspace=0.38, wspace=0.22)
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
        try:
            baud_def = int(self.baud_def_edit.text().strip())
            baud = int(self.baud_edit.text().strip())
        except ValueError:
            self._log("波特率无效")
            return
        self.run_btn.setEnabled(False)
        self.log.clear()
        self._log("标定已开始。此操作会覆盖 flash 中的标定数据。")
        self.worker = CalibRunWorker(port, baud_def, baud, self)
        self.worker.progress.connect(self._log)
        self.worker.success.connect(self._on_success)
        self.worker.error.connect(self._on_error)
        self.worker.start()

    @Slot(object, object, object, object, list, bool)
    def _on_success(self, img_l, img_h, img_bg, gain, badpts, bg_ok):
        img_dt = img_h.astype(np.float32) - img_l.astype(np.float32)
        filt_bp = [(y, x) for (y, x) in badpts if not (y == 255 and x == 255)]

        gv1, gv99 = np.percentile(gain, [1, 99])
        if gv1 >= gv99:
            gv1, gv99 = float(gain.min()), float(gain.max())

        self._log("-" * 40)
        self._log(f"  img_l   均值/最大 : {img_l.mean():.1f} / {img_l.max()}")
        self._log(f"  img_h   均值/最大 : {img_h.mean():.1f} / {img_h.max()}")
        self._log(f"  img_dt  均值      : {img_dt.mean():.1f}")
        self._log(f"  gain    最小/最大 : {gain.min():.4f} / {gain.max():.4f}")
        self._log(f"  gain    p1 / p99  : {gv1:.4f} / {gv99:.4f}")
        self._log(f"  坏点              : {len(filt_bp)}  {filt_bp}")

        self._last = dict(img_l=img_l, img_h=img_h, img_bg=img_bg, gain=gain,
                          badpts=filt_bp, img_dt=img_dt, bg_ok=bg_ok,
                          gv1=gv1, gv99=gv99)

        self._render(self.fig, interactive=True)
        self.canvas.draw_idle()

        if self.save_cb.isChecked():
            self._save_pngs()

        self.run_btn.setEnabled(True)

    def _render(self, fig, interactive):
        fig.clear()
        r = self._last
        style_figure(fig) if interactive else fig.patch.set_facecolor('white')

        if interactive:
            ax1 = fig.add_subplot(2, 3, 1); style_card(ax1, "img_l", "冷快门")
            ax2 = fig.add_subplot(2, 3, 2); style_card(ax2, "img_h", "热快门")
            ax3 = fig.add_subplot(2, 3, 3); style_card(ax3, "img_dt", "热−冷 对比")
            ax4 = fig.add_subplot(2, 3, 4); style_card(ax4, "gain", "逐像素")
            ax5 = fig.add_subplot(2, 3, 5); style_card(ax5, "坏点", "缺陷掩膜")
            ax6 = fig.add_subplot(2, 3, 6); style_summary_card(ax6, "汇总")
        else:
            ax1 = fig.add_subplot(2, 3, 1); ax1.set_title("img_l")
            ax2 = fig.add_subplot(2, 3, 2); ax2.set_title("img_h")
            ax3 = fig.add_subplot(2, 3, 3); ax3.set_title("img_dt")
            ax4 = fig.add_subplot(2, 3, 4); ax4.set_title("gain (99% norm)")
            ax5 = fig.add_subplot(2, 3, 5); ax5.set_title(f"bad points ({len(r['badpts'])})")
            ax6 = fig.add_subplot(2, 3, 6); ax6.set_title("summary"); ax6.axis('off')

        cb_kw = dict(fraction=0.046, pad=0.03)

        im1 = ax1.imshow(r['img_l'], cmap='gray')
        cb = fig.colorbar(im1, ax=ax1, **cb_kw)
        self._style_cbar(cb, interactive)

        im2 = ax2.imshow(r['img_h'], cmap='gray')
        cb = fig.colorbar(im2, ax=ax2, **cb_kw); self._style_cbar(cb, interactive)

        im3 = ax3.imshow(r['img_dt'], cmap='gray')
        cb = fig.colorbar(im3, ax=ax3, **cb_kw); self._style_cbar(cb, interactive)

        im4 = ax4.imshow(r['gain'], cmap='jet', vmin=r['gv1'], vmax=r['gv99'])
        cb = fig.colorbar(im4, ax=ax4, **cb_kw); self._style_cbar(cb, interactive)

        ax5.imshow(r['img_dt'], cmap='gray')
        if r['badpts']:
            ys = [y for y, _ in r['badpts']]
            xs = [x for _, x in r['badpts']]
            ax5.scatter(xs, ys, c='#ff6b6b', s=45, marker='x', linewidths=1.6)

        # summary panel
        if interactive:
            status_badge(ax6, r['bg_ok'], "flash 写入校验",
                         x=0.04, y=0.90)
            kv_block(ax6, [
                ('img_l  均值', f"{r['img_l'].mean():.1f}"),
                ('img_l  最大', f"{r['img_l'].max()}"),
                ('img_h  均值', f"{r['img_h'].mean():.1f}"),
                ('img_h  最大', f"{r['img_h'].max()}"),
                ('img_dt 均值', f"{r['img_dt'].mean():.1f}"),
                ('gain   μ',    f"{r['gain'].mean():.4f}"),
                ('gain   σ',    f"{r['gain'].std():.4f}"),
                ('gain   p1',   f"{r['gv1']:.4f}"),
                ('gain   p99',  f"{r['gv99']:.4f}"),
                ('坏点',        f"{len(r['badpts'])}"),
            ], x=0.04, y_top=0.76, line_h=0.068)
        else:
            tag = "PASS" if r['bg_ok'] else "FAIL"
            text = (
                f"flash check: {tag}\n\n"
                f"img_l  mean {r['img_l'].mean():.1f}  max {r['img_l'].max()}\n"
                f"img_h  mean {r['img_h'].mean():.1f}  max {r['img_h'].max()}\n"
                f"img_dt mean {r['img_dt'].mean():.1f}\n\n"
                f"gain μ {r['gain'].mean():.4f}  σ {r['gain'].std():.4f}\n"
                f"gain p1 {r['gv1']:.4f}  p99 {r['gv99']:.4f}\n\n"
                f"bad points {len(r['badpts'])}"
            )
            ax6.text(0.02, 0.92, text, transform=ax6.transAxes,
                     va='top', family='monospace', fontsize=9)

        fig.subplots_adjust(left=0.04, right=0.98, top=0.93, bottom=0.07,
                            hspace=0.38, wspace=0.22)

    def _style_cbar(self, cbar, interactive):
        if not interactive:
            return
        cbar.ax.tick_params(colors='#8c93af', labelsize=7)
        for sp in cbar.ax.spines.values():
            sp.set_edgecolor('#2e3246')

    def _save_pngs(self):
        r = self._last
        if r is None:
            return
        folder = datetime.now().strftime("cali_%Y%m%d_%H%M%S")
        out_dir = os.path.join("cali_data_backup", folder)
        os.makedirs(out_dir, exist_ok=True)

        # Reuse the same renderer for the saved figure.
        fig = Figure(figsize=(15, 8), facecolor='white')
        FigureCanvasAgg(fig)
        self._render(fig, interactive=False)
        fig.savefig(os.path.join(out_dir, "calibration.png"), dpi=140)

        # Also save the cali.py-style split pair for compatibility.
        fig1 = Figure(figsize=(15, 5), facecolor='white')
        FigureCanvasAgg(fig1)
        for i, (arr, name) in enumerate([(r['img_l'], 'img_l'),
                                         (r['img_h'], 'img_h'),
                                         (r['img_dt'], 'img_dt')]):
            ax = fig1.add_subplot(1, 3, i + 1)
            im = ax.imshow(arr, cmap='gray')
            ax.set_title(name)
            fig1.colorbar(im, ax=ax)
        fig1.tight_layout()
        fig1.savefig(os.path.join(out_dir, "images_comparison.png"))

        fig2 = Figure(figsize=(12, 5), facecolor='white')
        FigureCanvasAgg(fig2)
        ax = fig2.add_subplot(1, 2, 1)
        im = ax.imshow(r['gain'], cmap='jet', vmin=r['gv1'], vmax=r['gv99'])
        ax.set_title('gain_map (99% norm)')
        fig2.colorbar(im, ax=ax)
        ax = fig2.add_subplot(1, 2, 2)
        ax.imshow(r['img_dt'], cmap='gray')
        if r['badpts']:
            ys = [y for y, _ in r['badpts']]
            xs = [x for _, x in r['badpts']]
            ax.scatter(xs, ys, c='r', s=30)
        ax.set_title('bad points on img_dt')
        fig2.tight_layout()
        fig2.savefig(os.path.join(out_dir, "gain_and_badpts.png"))

        self._log(f"PNG 已保存 → {out_dir}")

    def _on_error(self, msg):
        self._log(f"[错误] {msg}")
        self.run_btn.setEnabled(True)
