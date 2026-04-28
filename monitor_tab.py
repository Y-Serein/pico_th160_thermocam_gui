"""Runtime monitor tab: live thermal image + FPA/scene trend."""
import time
from collections import deque

import numpy as np
from PySide6.QtCore import Qt, Slot
from PySide6.QtWidgets import (QWidget, QVBoxLayout, QHBoxLayout, QPushButton,
                               QLabel, QLineEdit, QComboBox, QFileDialog)
from matplotlib.backends.backend_qtagg import FigureCanvasQTAgg as FigureCanvas
from matplotlib.figure import Figure
import matplotlib.gridspec as gridspec

from protocol import PX_W, PX_H, INT16_MAX, fpa_to_celsius, ntc_adu_to_celsius
from workers import MonitorWorker
from port_utils import list_serial_ports, probe_active_port

TREND_MAX = 1800       # rolling display window (~3 min @ 10 fps)
REDRAW_EVERY = 8       # throttle trend redraw


class MonitorTab(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.worker = None

        self._trend_t   = deque(maxlen=TREND_MAX)
        self._trend_fpa = deque(maxlen=TREND_MAX)
        self._trend_mid = deque(maxlen=TREND_MAX)
        self._trend_ntc = deque(maxlen=TREND_MAX)
        self._hist_t   = []
        self._hist_fpa = []
        self._hist_mid = []
        self._hist_ntc = []
        self._redraw_i = 0
        self._cx, self._cy = PX_W // 2, PX_H // 2
        self._vtemp_ref = None
        self._ntc_ref = None

        root = QVBoxLayout(self)

        ctrl = QHBoxLayout()
        self.port_cb = QComboBox()
        self.port_cb.setEditable(True)
        self.port_cb.setMinimumWidth(180)
        self.refresh_btn = QPushButton("1.扫描")
        self.baud_edit = QLineEdit("2000000")
        self.baud_edit.setMaximumWidth(100)
        self.start_btn = QPushButton("2.开始")
        self.stop_btn = QPushButton("3.停止")
        self.save_btn = QPushButton("4.保存趋势图")
        self.stop_btn.setEnabled(False)

        self.refresh_btn.clicked.connect(self._refresh_ports)
        self.start_btn.clicked.connect(self._start)
        self.stop_btn.clicked.connect(self._stop)
        self.save_btn.clicked.connect(self._save_png)

        ctrl.addWidget(QLabel("串口：")); ctrl.addWidget(self.port_cb)
        ctrl.addWidget(self.refresh_btn)
        ctrl.addWidget(QLabel("  波特率：")); ctrl.addWidget(self.baud_edit)
        ctrl.addWidget(self.start_btn); ctrl.addWidget(self.stop_btn)
        ctrl.addStretch(1); ctrl.addWidget(self.save_btn)
        root.addLayout(ctrl)

        self.status_lbl = QLabel("空闲")
        self.status_lbl.setStyleSheet("color:#888; padding:4px 2px;")
        root.addWidget(self.status_lbl)

        self.diag_lbl = QLabel("")
        self.diag_lbl.setStyleSheet("color:#aaa; padding:0px 2px; font-family:monospace;")
        root.addWidget(self.diag_lbl)

        self.fig = Figure(figsize=(10, 7.5), facecolor='#111')
        self.canvas = FigureCanvas(self.fig)
        root.addWidget(self.canvas, 1)
        self._setup_plot()
        self.canvas.mpl_connect('button_press_event', self._on_click)

        self._refresh_ports()

    def _refresh_ports(self):
        current = self.port_cb.currentText()
        self.port_cb.clear()
        ports = list_serial_ports()
        self.port_cb.addItems(ports)
        if not ports:
            return
        # don't probe while a worker holds a port — it would conflict
        if self.worker is not None and self.worker.isRunning():
            if current in ports:
                self.port_cb.setCurrentText(current)
            else:
                self.port_cb.setCurrentIndex(0)
            return
        active = probe_active_port(ports)
        if active:
            self.port_cb.setCurrentText(active)
            self.status_lbl.setText(f"探测：{active} 上检测到数据流")
        elif current and current in ports:
            self.port_cb.setCurrentText(current)
        else:
            self.port_cb.setCurrentIndex(0)

    def _setup_plot(self):
        gs = gridspec.GridSpec(2, 2, height_ratios=[3, 1], width_ratios=[20, 1],
                               hspace=0.25, wspace=0.05, figure=self.fig)
        self.ax   = self.fig.add_subplot(gs[0, 0])
        self.cax  = self.fig.add_subplot(gs[0, 1])
        self.tax  = self.fig.add_subplot(gs[1, :])
        self.tax2 = self.tax.twinx()
        self.tax3 = self.tax.twinx()
        self.tax3.spines['right'].set_position(('axes', 1.08))

        for ax in (self.ax, self.cax, self.tax, self.tax2, self.tax3):
            ax.set_facecolor('#1c1c1c')

        blank = np.zeros((PX_H, PX_W), dtype=np.float32)
        self.im = self.ax.imshow(blank, cmap='inferno', vmin=0, vmax=50,
                                 interpolation='nearest', aspect='equal')
        self.cbar = self.fig.colorbar(self.im, cax=self.cax)
        self.cbar.set_label('°C', color='white', fontsize=9)
        self.cbar.ax.tick_params(colors='white', labelsize=8)
        self.ax.set_xticks([]); self.ax.set_yticks([])
        self.title = self.ax.set_title('TN160 — 空闲', color='white', fontsize=11, pad=6)

        self._ch = self.ax.axhline(self._cy, color='cyan', lw=0.6, alpha=0.8)
        self._cv = self.ax.axvline(self._cx, color='cyan', lw=0.6, alpha=0.8)
        self._cross_txt = self.ax.text(
            self._cx + 3, self._cy - 4, '',
            color='cyan', fontsize=8,
            bbox=dict(boxstyle='round,pad=0.2', fc='black', alpha=0.65))
        kw = dict(fontsize=7.5, bbox=dict(boxstyle='round,pad=0.25', fc='#000', alpha=0.6))
        self._t_min = self.ax.text(3, PX_H - 5,  '', color='#66aaff', **kw)
        self._t_max = self.ax.text(3, PX_H - 13, '', color='#ffaa44', **kw)

        self.tax.tick_params(colors='#ff8888', labelsize=7)
        self.tax2.tick_params(colors='#88cc88', labelsize=7)
        self.tax3.tick_params(colors='#66ccff', labelsize=7)
        self.tax.set_ylabel('FPA °C', color='#ff8888', fontsize=8)
        self.tax2.set_ylabel('scene mid °C', color='#88cc88', fontsize=8)
        self.tax3.set_ylabel('NTC °C', color='#66ccff', fontsize=8)
        self.tax.set_xlabel('t (s)', color='white', fontsize=8)
        self.tax.tick_params(axis='x', colors='white')
        self.tax.grid(True, color='#333', lw=0.3, alpha=0.5)
        for sp in (list(self.tax.spines.values()) + list(self.tax2.spines.values())
                   + list(self.tax3.spines.values())):
            sp.set_edgecolor('#333')
        self._ln_fpa, = self.tax.plot([], [], color='#ff8888', lw=1.0)
        self._ln_mid, = self.tax2.plot([], [], color='#88cc88', lw=1.0)
        self._ln_ntc, = self.tax3.plot([], [], color='#66ccff', lw=0.9, alpha=0.9)

        self.canvas.draw_idle()

    def _on_click(self, event):
        if event.inaxes is not self.ax or event.xdata is None:
            return
        x = max(0, min(PX_W - 1, int(round(event.xdata))))
        y = max(0, min(PX_H - 1, int(round(event.ydata))))
        self._cx, self._cy = x, y
        self._ch.set_ydata([y])
        self._cv.set_xdata([x])
        self._cross_txt.set_position((x + 3, y - 4))
        self.canvas.draw_idle()

    def _start(self):
        port = self.port_cb.currentText().strip()
        if not port:
            self.status_lbl.setText("请先选择串口")
            return
        try:
            baud = int(self.baud_edit.text().strip())
        except ValueError:
            self.status_lbl.setText("波特率无效")
            return
        self._trend_t.clear(); self._trend_fpa.clear(); self._trend_mid.clear(); self._trend_ntc.clear()
        self._hist_t.clear(); self._hist_fpa.clear(); self._hist_mid.clear(); self._hist_ntc.clear()
        self._vtemp_ref = None
        self._ntc_ref = None

        self.worker = MonitorWorker(port, baud, self)
        self.worker.frame_ready.connect(self._on_frame)
        self.worker.error.connect(self._on_error)
        self.worker.stopped.connect(self._on_stopped)
        self.worker.start()

        self.start_btn.setEnabled(False)
        self.stop_btn.setEnabled(True)
        self._had_error = False
        self.status_lbl.setStyleSheet("color:#888; padding:4px 2px;")
        self.status_lbl.setText(f"运行中：{port} @ {baud}")

    def _stop(self):
        if self.worker:
            self.worker.stop()

    def shutdown(self):
        self._stop()
        if self.worker:
            self.worker.wait(2000)

    @Slot(dict, float, float)
    def _on_frame(self, frame, fps, t_s):
        pixels   = frame['pixels']
        vtemp    = frame['vtemp']
        t_lo_x10 = frame['t_lo_x10']
        t_hi_x10 = frame['t_hi_x10']
        anchor   = frame['anchor']
        slow     = frame['smooth_low']
        shigh    = frame['smooth_high']
        md       = frame['mean_diff']
        ntc_ref  = frame.get('ntc_ref', 0)
        ntc      = frame.get('ntc', 0)

        fpa = fpa_to_celsius(vtemp)
        fpa_ok = not np.isnan(fpa)
        if self._vtemp_ref is None and vtemp > 0:
            self._vtemp_ref = vtemp
        ntc_c = ntc_adu_to_celsius(ntc)
        ntc_ref_c = ntc_adu_to_celsius(ntc_ref) if ntc_ref else float('nan')
        if self._ntc_ref is None and not np.isnan(ntc_c):
            self._ntc_ref = ntc_ref_c if not np.isnan(ntc_ref_c) else ntc_c

        scene_mid = None
        if t_lo_x10 != INT16_MAX and t_hi_x10 != INT16_MAX:
            scene_mid = (t_lo_x10 + t_hi_x10) / 20.0

        fpa_s = fpa if fpa_ok else np.nan
        mid_s = scene_mid if scene_mid is not None else np.nan
        ntc_s = ntc_c if not np.isnan(ntc_c) else np.nan
        self._trend_t.append(t_s); self._trend_fpa.append(fpa_s); self._trend_mid.append(mid_s); self._trend_ntc.append(ntc_s)
        self._hist_t.append(t_s);  self._hist_fpa.append(fpa_s);  self._hist_mid.append(mid_s);  self._hist_ntc.append(ntc_s)

        calibrated = (t_lo_x10 != INT16_MAX and t_hi_x10 != INT16_MAX)
        if calibrated:
            t_lo = t_lo_x10 / 10.0
            t_hi = t_hi_x10 / 10.0
            if t_hi > t_lo:
                norm = pixels.astype(np.float32) / 254.0
                temp_img = t_lo + (t_hi - t_lo) * np.clip(norm, 0.0, 1.0)
            else:
                temp_img = np.full_like(pixels, t_lo, dtype=np.float32)
            self.im.set_data(temp_img)
            self.im.set_clim(t_lo, t_hi)
            ct = temp_img[self._cy, self._cx]
            self._cross_txt.set_text(f'{ct:.1f}°C')
            mi = np.unravel_index(np.argmin(temp_img), temp_img.shape)
            ma = np.unravel_index(np.argmax(temp_img), temp_img.shape)
            self._t_min.set_text(f'▼ {temp_img[mi]:.1f}°C  ({mi[1]},{mi[0]})')
            self._t_max.set_text(f'▲ {temp_img[ma]:.1f}°C  ({ma[1]},{ma[0]})')
            self.title.set_text(f'TN160  {t_lo:.1f}~{t_hi:.1f}°C   '
                                f'FPA {fpa:.1f}°C*   {fps:.1f}fps')
        else:
            self.im.set_data(pixels.astype(np.float32))
            self.im.set_clim(0, 255)
            self._cross_txt.set_text('')
            self._t_min.set_text(''); self._t_max.set_text('')
            self.title.set_text(f'TN160 — 未标定   FPA {fpa:.1f}°C*   {fps:.1f}fps')

        ref = self._vtemp_ref
        dv_str = "—" if ref is None else f"{vtemp - ref:+d} (ses)"
        if not np.isnan(ntc_ref_c):
            ntc_c_str = f"{ntc_c:.1f}°C" if not np.isnan(ntc_c) else "—"
            dntc_str = (f"{ntc_c - ntc_ref_c:+.1f}°C (fw)"
                        if not np.isnan(ntc_c) else "—")
        elif self._ntc_ref is not None and not np.isnan(ntc_c):
            ntc_c_str = f"{ntc_c:.1f}°C"
            dntc_str = f"{ntc_c - self._ntc_ref:+.1f}°C (ses)"
        else:
            ntc_c_str = "—"
            dntc_str = "—"
        self.diag_lbl.setText(
            f"VTEMP {vtemp}  ΔVTEMP {dv_str}   "
            f"anchor {anchor}   smooth {slow}~{shigh} (Δ{shigh - slow})   "
            f"mean_diff {md:.1f}   NTC {ntc_c_str}  ΔNTC {dntc_str}")

        self._redraw_i = (self._redraw_i + 1) % REDRAW_EVERY
        if self._redraw_i == 0 and len(self._trend_t) >= 2:
            ts  = np.fromiter(self._trend_t,   dtype=np.float32)
            yfp = np.fromiter(self._trend_fpa, dtype=np.float32)
            ymd = np.fromiter(self._trend_mid, dtype=np.float32)
            ynt = np.fromiter(self._trend_ntc, dtype=np.float32)
            self._ln_fpa.set_data(ts, yfp)
            self._ln_mid.set_data(ts, ymd)
            self._ln_ntc.set_data(ts, ynt)
            t_end = ts[-1]; t_st = max(ts[0], t_end - 180.0)
            self.tax.set_xlim(t_st, max(t_end, t_st + 1.0))
            fp = yfp[np.isfinite(yfp)]
            if fp.size:
                lo, hi = float(fp.min()), float(fp.max())
                pad = max(0.5, (hi - lo) * 0.1)
                self.tax.set_ylim(lo - pad, hi + pad)
            md_f = ymd[np.isfinite(ymd)]
            if md_f.size:
                lo, hi = float(md_f.min()), float(md_f.max())
                pad = max(0.5, (hi - lo) * 0.1)
                self.tax2.set_ylim(lo - pad, hi + pad)
            nt_f = ynt[np.isfinite(ynt)]
            if nt_f.size:
                lo, hi = float(nt_f.min()), float(nt_f.max())
                pad = max(0.5, (hi - lo) * 0.1)
                self.tax3.set_ylim(lo - pad, hi + pad)

        self.canvas.draw_idle()

    def _on_error(self, msg):
        self.status_lbl.setText(f"错误：{msg}")
        self.status_lbl.setStyleSheet("color:#ff6666; padding:4px 2px; font-weight:bold;")
        self._had_error = True

    def _on_stopped(self):
        self.start_btn.setEnabled(True)
        self.stop_btn.setEnabled(False)
        if not getattr(self, '_had_error', False):
            self.status_lbl.setText("已停止")
            self.status_lbl.setStyleSheet("color:#888; padding:4px 2px;")
        self.worker = None

    def _save_png(self):
        if len(self._hist_t) < 2:
            self.status_lbl.setText("样本不足，无法保存")
            return
        default_name = f"trend_{time.strftime('%Y%m%d_%H%M%S')}.png"
        path, _ = QFileDialog.getSaveFileName(
            self, "保存完整历史趋势图（PNG）", default_name, "PNG (*.png)")
        if not path:
            return
        import matplotlib.pyplot as plt
        ts  = np.asarray(self._hist_t,   dtype=np.float32)
        yfp = np.asarray(self._hist_fpa, dtype=np.float32)
        ymd = np.asarray(self._hist_mid, dtype=np.float32)
        ynt = np.asarray(self._hist_ntc, dtype=np.float32)
        fig, ax1 = plt.subplots(figsize=(10, 4), facecolor='#111')
        ax1.set_facecolor('#1c1c1c')
        ax2 = ax1.twinx()
        ax3 = ax1.twinx()
        ax3.spines['right'].set_position(('axes', 1.08))
        ax1.plot(ts, yfp, color='#ff8888', lw=1.0, label='FPA °C')
        ax2.plot(ts, ymd, color='#88cc88', lw=1.0, label='scene mid °C')
        ax3.plot(ts, ynt, color='#66ccff', lw=0.9, label='NTC °C')
        ax1.set_xlabel('t (s)', color='white')
        ax1.set_ylabel('FPA °C',   color='#ff8888')
        ax2.set_ylabel('scene °C', color='#88cc88')
        ax3.set_ylabel('NTC °C',   color='#66ccff')
        ax1.tick_params(colors='#ff8888')
        ax2.tick_params(colors='#88cc88')
        ax3.tick_params(colors='#66ccff')
        ax1.grid(True, color='#333', lw=0.3, alpha=0.5)
        fig.tight_layout()
        try:
            fig.savefig(path, dpi=140, facecolor=fig.get_facecolor())
            self.status_lbl.setText(f"已保存：{path}")
        finally:
            plt.close(fig)
