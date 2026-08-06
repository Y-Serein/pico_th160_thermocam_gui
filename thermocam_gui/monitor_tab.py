"""Runtime monitor tab: live thermal image + thermal/FFC diagnostics."""
import csv
import sys
import time
from collections import deque
from pathlib import Path

import numpy as np
from PySide6.QtCore import Qt, Slot
from PySide6.QtWidgets import (QWidget, QVBoxLayout, QHBoxLayout, QPushButton,
                               QLabel, QLineEdit, QComboBox, QFileDialog,
                               QFrame)
from matplotlib.backends.backend_qtagg import FigureCanvasQTAgg as FigureCanvas
from matplotlib.figure import Figure
import matplotlib.gridspec as gridspec

from .protocol import PX_W, PX_H, INT16_MAX, ntc_adu_to_celsius
from .workers import MonitorWorker
from .port_utils import list_serial_ports, probe_active_port
from .ui_style import (FIG_BG, CARD_BG, CARD_EDGE, TITLE_FG, AXIS_FG,
                       set_button_kind, set_status_tone)

TREND_MAX = 1800       # rolling display window (~3 min @ 10 fps)
REDRAW_EVERY = 8       # throttle trend redraw

NTC_LINE_COLOR = '#48C7D9'
CENTER_TEMP_COLOR = '#65D6A3'
CENTER_ADU_COLOR = '#F2B45F'
VTEMP_RAW_COLOR = '#EF7A8A'
VTEMP_SMOOTH_COLOR = '#B89AF4'

FFC_EVENT_COLORS = {
    0: '#AAB4BE',  # accepted
    1: '#D7A85B',  # VTEMP/NTC mismatch
    2: '#D6815B',  # dG/dV limit
    3: '#D66D73',  # predicted temperature jump
    4: '#9B87C8',  # no frame
}


def export_log_dir():
    """Return the app-local logs directory, creating it when needed."""
    if getattr(sys, 'frozen', False):
        app_dir = Path(sys.executable).resolve().parent
    else:
        app_dir = Path(__file__).resolve().parent.parent
    log_dir = app_dir / 'logs'
    log_dir.mkdir(parents=True, exist_ok=True)
    return log_dir


def prepare_export_path(path):
    """Create a user-selected export path's parent directory if absent."""
    output_path = Path(path).expanduser()
    output_path.parent.mkdir(parents=True, exist_ok=True)
    return output_path


def nuc_status_text(decision, count, dg, dv, dn):
    labels = {
        0: "ACC",
        1: "REJ_VN",
        2: "REJ_LIM",
        3: "REJ_JMP",
        4: "NOFRM",
    }
    if decision == 255:
        return "NUC --"
    label = labels.get(decision, f"EV{decision}")
    return f"NUC {label}#{count} dg {dg:+d} dv {dv:+d} dn {dn:+d}"


def nuc_decision_label(decision):
    return {
        0: 'ACC',
        1: 'REJ_VN',
        2: 'REJ_LIM',
        3: 'REJ_JMP',
        4: 'NOFRM',
    }.get(decision, f'EV{decision}')


def comp_status_text(frame):
    boot_delta = frame.get('boot_delta', 0xFFFF)
    boot_delta_s = "—" if boot_delta == 0xFFFF else f"{boot_delta:d}"
    return (
        f"bootN {frame.get('boot_ntc', 0)}/{boot_delta_s}   "
        f"comp W/N/T {frame.get('warmup_comp', 0):+d}/"
        f"{frame.get('normal_comp', 0):+d}/"
        f"{frame.get('total_comp', 0):+d}   "
        f"eff {frame.get('eff_anchor', frame.get('anchor', 0))}   "
        f"sV {frame.get('smooth_vtemp', frame.get('vtemp', 0))}"
    )


class MonitorTab(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.worker = None

        self._trend_t = deque(maxlen=TREND_MAX)
        self._trend_center_temp = deque(maxlen=TREND_MAX)
        self._trend_roi_temp = deque(maxlen=TREND_MAX)
        self._trend_ntc = deque(maxlen=TREND_MAX)
        self._trend_center_adu = deque(maxlen=TREND_MAX)
        self._trend_roi_adu = deque(maxlen=TREND_MAX)
        self._trend_vtemp = deque(maxlen=TREND_MAX)
        self._trend_smooth_vtemp = deque(maxlen=TREND_MAX)
        self._hist_t = []
        self._hist_center_temp = []
        self._hist_roi_temp = []
        self._hist_ntc = []
        self._hist_center_adu = []
        self._hist_roi_adu = []
        self._hist_vtemp = []
        self._hist_smooth_vtemp = []
        self._hist_rows = []
        self._ffc_events = []
        self._ffc_event_artists = []
        self._last_ffc_signature = None
        self._redraw_i = 0
        self._cx, self._cy = PX_W // 2, PX_H // 2
        self._vtemp_ref = None
        self._ntc_ref = None

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
        self.baud_edit = QLineEdit("2000000")
        self.baud_edit.setMaximumWidth(100)
        self.start_btn = QPushButton("2.开始采集")
        self.stop_btn = QPushButton("3.停止")
        self.save_btn = QPushButton("导出 PNG")
        self.csv_btn = QPushButton("导出 CSV")
        self.stop_btn.setEnabled(False)
        set_button_kind(self.refresh_btn, 'step')
        set_button_kind(self.start_btn, 'primary')
        set_button_kind(self.stop_btn, 'danger')
        set_button_kind(self.save_btn, 'quiet')
        set_button_kind(self.csv_btn, 'quiet')

        self.refresh_btn.clicked.connect(self._refresh_ports)
        self.start_btn.clicked.connect(self._start)
        self.stop_btn.clicked.connect(self._stop)
        self.save_btn.clicked.connect(self._save_png)
        self.csv_btn.clicked.connect(self._save_csv)

        port_label = QLabel("串口")
        port_label.setObjectName('fieldLabel')
        baud_label = QLabel("波特率")
        baud_label.setObjectName('fieldLabel')
        ctrl.addWidget(port_label); ctrl.addWidget(self.port_cb)
        ctrl.addWidget(self.refresh_btn)
        ctrl.addSpacing(8)
        ctrl.addWidget(baud_label); ctrl.addWidget(self.baud_edit)
        ctrl.addWidget(self.start_btn); ctrl.addWidget(self.stop_btn)
        ctrl.addStretch(1); ctrl.addWidget(self.save_btn); ctrl.addWidget(self.csv_btn)
        root.addWidget(command_bar)

        self.status_lbl = QLabel("空闲")
        self.status_lbl.setObjectName('statusBadge')
        set_status_tone(self.status_lbl, 'idle')
        status_row = QHBoxLayout()
        status_row.addWidget(self.status_lbl)
        status_row.addStretch(1)
        root.addLayout(status_row)

        telemetry = QFrame()
        telemetry.setObjectName('telemetryStrip')
        telemetry_layout = QHBoxLayout(telemetry)
        telemetry_layout.setContentsMargins(10, 6, 10, 6)
        self.diag_lbl = QLabel("")
        self.diag_lbl.setObjectName('telemetryText')
        self.diag_lbl.setText("等待数据 · 中心5×5 / ADU / VTEMP / NTC / FFC")
        telemetry_layout.addWidget(self.diag_lbl)
        root.addWidget(telemetry)

        self.fig = Figure(figsize=(10, 8.2), facecolor=FIG_BG)
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
            set_status_tone(self.status_lbl, 'success')
        elif current and current in ports:
            self.port_cb.setCurrentText(current)
        else:
            self.port_cb.setCurrentIndex(0)

    def _setup_plot(self):
        gs = gridspec.GridSpec(3, 2, height_ratios=[3, 1.15, 1.15], width_ratios=[20, 1],
                               hspace=0.25, wspace=0.05, figure=self.fig)
        self.ax   = self.fig.add_subplot(gs[0, 0])
        self.cax  = self.fig.add_subplot(gs[0, 1])
        self.tax  = self.fig.add_subplot(gs[1, :])
        self.tax2 = self.tax.twinx()
        self.aax  = self.fig.add_subplot(gs[2, :], sharex=self.tax)
        self.aax2 = self.aax.twinx()

        for ax in (self.ax, self.cax, self.tax, self.tax2, self.aax, self.aax2):
            ax.set_facecolor(CARD_BG)

        blank = np.zeros((PX_H, PX_W), dtype=np.float32)
        self.im = self.ax.imshow(blank, cmap='inferno', vmin=0, vmax=50,
                                 interpolation='nearest', aspect='equal')
        self.cbar = self.fig.colorbar(self.im, cax=self.cax)
        self.cbar.set_label('°C', color=AXIS_FG, fontsize=9)
        self.cbar.ax.tick_params(colors=AXIS_FG, labelsize=8)
        self.ax.set_xticks([]); self.ax.set_yticks([])
        self.title = self.ax.set_title('TN160 — 空闲', color=TITLE_FG, fontsize=11, pad=6)

        self._ch = self.ax.axhline(self._cy, color='cyan', lw=0.6, alpha=0.8)
        self._cv = self.ax.axvline(self._cx, color='cyan', lw=0.6, alpha=0.8)
        self._cross_txt = self.ax.text(
            self._cx + 3, self._cy - 4, '',
            color='cyan', fontsize=8,
            bbox=dict(boxstyle='round,pad=0.2', fc='black', alpha=0.65))
        kw = dict(fontsize=7.5, bbox=dict(boxstyle='round,pad=0.25', fc='#000', alpha=0.6))
        self._t_min = self.ax.text(3, PX_H - 5,  '', color='#66aaff', **kw)
        self._t_max = self.ax.text(3, PX_H - 13, '', color='#ffaa44', **kw)

        self.tax.tick_params(colors=NTC_LINE_COLOR, labelsize=7)
        self.tax2.tick_params(colors=CENTER_TEMP_COLOR, labelsize=7)
        self.aax.tick_params(colors=CENTER_ADU_COLOR, labelsize=7)
        self.aax2.tick_params(colors=VTEMP_SMOOTH_COLOR, labelsize=7)
        self.tax.set_ylabel('NTC (°C)', color=NTC_LINE_COLOR, fontsize=8)
        self.tax2.set_ylabel('Center (°C)', color=CENTER_TEMP_COLOR, fontsize=8)
        self.aax.set_ylabel('Center (ADU)', color=CENTER_ADU_COLOR, fontsize=8)
        self.aax2.set_ylabel('VTEMP (ADU)', color=VTEMP_SMOOTH_COLOR, fontsize=8)
        self.aax.ticklabel_format(axis='y', style='plain', useOffset=False)
        self.aax2.ticklabel_format(axis='y', style='plain', useOffset=False)
        self.aax.set_xlabel('t (s)', color=AXIS_FG, fontsize=8)
        self.tax.tick_params(axis='x', colors=AXIS_FG, labelbottom=False)
        self.aax.tick_params(axis='x', colors=AXIS_FG)
        self.tax.grid(True, color=CARD_EDGE, lw=0.4, alpha=0.55)
        self.aax.grid(True, color=CARD_EDGE, lw=0.4, alpha=0.55)
        for sp in (list(self.tax.spines.values()) + list(self.tax2.spines.values())
                   + list(self.aax.spines.values()) + list(self.aax2.spines.values())):
            sp.set_edgecolor(CARD_EDGE)
        self._ln_ntc, = self.tax.plot([], [], color=NTC_LINE_COLOR, lw=1.0,
                                      alpha=0.95, label='NTC')
        self._ln_center_temp, = self.tax2.plot(
            [], [], color=CENTER_TEMP_COLOR, lw=1.35, label='Center point')
        self._ln_roi_temp, = self.tax2.plot(
            [], [], color=CENTER_TEMP_COLOR, lw=0.95, ls='--', alpha=0.7,
            label='Center 5x5 avg')
        self._ln_center_adu, = self.aax.plot(
            [], [], color=CENTER_ADU_COLOR, lw=1.25, label='Point ADU')
        self._ln_roi_adu, = self.aax.plot(
            [], [], color=CENTER_ADU_COLOR, lw=0.9, ls='--', alpha=0.65,
            label='5x5 avg ADU')
        self._ln_vtemp, = self.aax2.plot(
            [], [], color=VTEMP_RAW_COLOR, lw=0.7, alpha=0.5,
            label='VTEMP raw')
        self._ln_smooth_vtemp, = self.aax2.plot(
            [], [], color=VTEMP_SMOOTH_COLOR, lw=1.1, label='VTEMP smooth')

        legend_kw = dict(fontsize=6.5, frameon=True, framealpha=0.82,
                         facecolor='#171c21', edgecolor=CARD_EDGE,
                         labelcolor=AXIS_FG, borderpad=0.35,
                         handlelength=2.2, columnspacing=1.0)
        self.tax.legend(handles=[self._ln_center_temp, self._ln_roi_temp,
                                 self._ln_ntc],
                        loc='upper left', ncol=3, **legend_kw)
        self.aax.legend(handles=[self._ln_center_adu, self._ln_roi_adu,
                                 self._ln_vtemp, self._ln_smooth_vtemp],
                        loc='upper left', ncol=4, **legend_kw)

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
            set_status_tone(self.status_lbl, 'warning')
            return
        try:
            baud = int(self.baud_edit.text().strip())
        except ValueError:
            self.status_lbl.setText("波特率无效")
            set_status_tone(self.status_lbl, 'warning')
            return
        self._trend_t.clear(); self._trend_center_temp.clear(); self._trend_roi_temp.clear()
        self._trend_ntc.clear(); self._trend_center_adu.clear(); self._trend_roi_adu.clear()
        self._trend_vtemp.clear(); self._trend_smooth_vtemp.clear()
        self._hist_t.clear(); self._hist_center_temp.clear(); self._hist_roi_temp.clear()
        self._hist_ntc.clear(); self._hist_center_adu.clear(); self._hist_roi_adu.clear()
        self._hist_vtemp.clear(); self._hist_smooth_vtemp.clear()
        self._hist_rows.clear(); self._ffc_events.clear()
        for artist in self._ffc_event_artists:
            artist.remove()
        self._ffc_event_artists.clear()
        self._last_ffc_signature = None
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
        self.status_lbl.setText(f"运行中：{port} @ {baud}")
        set_status_tone(self.status_lbl, 'active')

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
        nuc_s = nuc_status_text(frame.get('nuc_decision', 255),
                                frame.get('nuc_count', 0),
                                frame.get('nuc_dg', 0),
                                frame.get('nuc_dv', 0),
                                frame.get('nuc_dn', 0))
        comp_s = comp_status_text(frame)

        if self._vtemp_ref is None and vtemp > 0:
            self._vtemp_ref = vtemp
        ntc_c = ntc_adu_to_celsius(ntc)
        ntc_ref_c = ntc_adu_to_celsius(ntc_ref) if ntc_ref else float('nan')
        if self._ntc_ref is None and not np.isnan(ntc_c):
            self._ntc_ref = ntc_ref_c if not np.isnan(ntc_ref_c) else ntc_c

        center_raw_x10 = frame.get('center_raw_x10', INT16_MAX)
        center_roi_x10 = frame.get('center_roi_x10', INT16_MAX)
        center_raw_adu = frame.get('center_raw_adu', 0)
        center_roi_adu = frame.get('center_roi_adu', 0)
        center_raw_c = (center_raw_x10 / 10.0
                        if center_raw_x10 != INT16_MAX else np.nan)
        center_roi_c = (center_roi_x10 / 10.0
                        if center_roi_x10 != INT16_MAX else np.nan)
        ntc_s = ntc_c if not np.isnan(ntc_c) else np.nan
        center_adu_s = float(center_raw_adu) if center_raw_adu > 0 else np.nan
        roi_adu_s = float(center_roi_adu) if center_roi_adu > 0 else np.nan
        smooth_vtemp = frame.get('smooth_vtemp', vtemp)
        self._trend_t.append(t_s)
        self._trend_center_temp.append(center_raw_c)
        self._trend_roi_temp.append(center_roi_c)
        self._trend_ntc.append(ntc_s)
        self._trend_center_adu.append(center_adu_s)
        self._trend_roi_adu.append(roi_adu_s)
        self._trend_vtemp.append(float(vtemp))
        self._trend_smooth_vtemp.append(float(smooth_vtemp))
        self._hist_t.append(t_s)
        self._hist_center_temp.append(center_raw_c)
        self._hist_roi_temp.append(center_roi_c)
        self._hist_ntc.append(ntc_s)
        self._hist_center_adu.append(center_adu_s)
        self._hist_roi_adu.append(roi_adu_s)
        self._hist_vtemp.append(float(vtemp))
        self._hist_smooth_vtemp.append(float(smooth_vtemp))

        event_label = self._track_ffc_event(frame, t_s)
        self._hist_rows.append({
            't_s': f'{t_s:.3f}',
            'center_roi_c': '' if np.isnan(center_roi_c) else f'{center_roi_c:.1f}',
            'center_raw_c': '' if np.isnan(center_raw_c) else f'{center_raw_c:.1f}',
            'center_roi_adu': center_roi_adu,
            'center_raw_adu': center_raw_adu,
            'vtemp_raw_adu': vtemp,
            'vtemp_smooth_adu': smooth_vtemp,
            'vtemp_delta_adu': '' if self._vtemp_ref is None else vtemp - self._vtemp_ref,
            'ntc_adu': ntc,
            'ntc_c': '' if np.isnan(ntc_c) else f'{ntc_c:.3f}',
            'ntc_ref_adu': ntc_ref,
            'ntc_ref_c': '' if np.isnan(ntc_ref_c) else f'{ntc_ref_c:.3f}',
            't_lo_c': '' if t_lo_x10 == INT16_MAX else f'{t_lo_x10 / 10.0:.1f}',
            't_hi_c': '' if t_hi_x10 == INT16_MAX else f'{t_hi_x10 / 10.0:.1f}',
            'anchor': anchor,
            'eff_anchor': frame.get('eff_anchor', anchor),
            'mean_diff': f'{md:.6f}',
            'warmup_comp': frame.get('warmup_comp', 0),
            'normal_comp': frame.get('normal_comp', 0),
            'total_comp': frame.get('total_comp', 0),
            'ffc_event': event_label,
            'ffc_decision': frame.get('nuc_decision', 255),
            'ffc_count': frame.get('nuc_count', 0),
            'ffc_dg': frame.get('nuc_dg', 0),
            'ffc_dv': frame.get('nuc_dv', 0),
            'ffc_dn': frame.get('nuc_dn', 0),
        })

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
                                f'中心点 {center_raw_c:.1f}°C   '
                                f'VTEMP {vtemp}   {fps:.1f}fps')
        else:
            self.im.set_data(pixels.astype(np.float32))
            self.im.set_clim(0, 255)
            self._cross_txt.set_text('')
            self._t_min.set_text(''); self._t_max.set_text('')
            self.title.set_text(f'TN160 — 未标定   VTEMP {vtemp}   {fps:.1f}fps')

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
            f"中心点 {center_raw_c:.1f}°C/{center_raw_adu}ADU   "
            f"5×5 {center_roi_c:.1f}°C/{center_roi_adu}ADU   "
            f"VTEMP {vtemp}/{smooth_vtemp}  ΔVTEMP {dv_str}   "
            f"anchor {anchor}   smooth {slow}~{shigh} (Δ{shigh - slow})   "
            f"mean_diff {md:.1f}   NTC {ntc_c_str}  ΔNTC {dntc_str}   "
            f"{nuc_s}   {comp_s}")

        self._redraw_i = (self._redraw_i + 1) % REDRAW_EVERY
        if self._redraw_i == 0 and len(self._trend_t) >= 2:
            ts  = np.fromiter(self._trend_t,   dtype=np.float32)
            yct = np.fromiter(self._trend_center_temp, dtype=np.float32)
            yrt = np.fromiter(self._trend_roi_temp, dtype=np.float32)
            ynt = np.fromiter(self._trend_ntc, dtype=np.float32)
            yca = np.fromiter(self._trend_center_adu, dtype=np.float32)
            yra = np.fromiter(self._trend_roi_adu, dtype=np.float32)
            yvr = np.fromiter(self._trend_vtemp, dtype=np.float32)
            yvs = np.fromiter(self._trend_smooth_vtemp, dtype=np.float32)
            self._ln_center_temp.set_data(ts, yct)
            self._ln_roi_temp.set_data(ts, yrt)
            self._ln_ntc.set_data(ts, ynt)
            self._ln_center_adu.set_data(ts, yca)
            self._ln_roi_adu.set_data(ts, yra)
            self._ln_vtemp.set_data(ts, yvr)
            self._ln_smooth_vtemp.set_data(ts, yvs)
            t_end = ts[-1]; t_st = max(ts[0], t_end - 180.0)
            self.tax.set_xlim(t_st, max(t_end, t_st + 1.0))
            center_t = np.concatenate((yct[np.isfinite(yct)],
                                       yrt[np.isfinite(yrt)]))
            if center_t.size:
                lo, hi = float(center_t.min()), float(center_t.max())
                pad = max(0.2, (hi - lo) * 0.1)
                self.tax2.set_ylim(lo - pad, hi + pad)
            nt_f = ynt[np.isfinite(ynt)]
            if nt_f.size:
                lo, hi = float(nt_f.min()), float(nt_f.max())
                pad = max(0.2, (hi - lo) * 0.1)
                self.tax.set_ylim(lo - pad, hi + pad)
            center_a = np.concatenate((yca[np.isfinite(yca)],
                                       yra[np.isfinite(yra)]))
            if center_a.size:
                lo, hi = float(center_a.min()), float(center_a.max())
                pad = max(5.0, (hi - lo) * 0.1)
                self.aax.set_ylim(lo - pad, hi + pad)
            vt = np.concatenate((yvr[np.isfinite(yvr)], yvs[np.isfinite(yvs)]))
            if vt.size:
                lo, hi = float(vt.min()), float(vt.max())
                pad = max(10.0, (hi - lo) * 0.1)
                self.aax2.set_ylim(lo - pad, hi + pad)

        self.canvas.draw_idle()

    def _track_ffc_event(self, frame, t_s):
        decision = frame.get('nuc_decision', 255)
        signature = (
            decision,
            frame.get('nuc_count', 0),
            frame.get('nuc_dg', 0),
            frame.get('nuc_dv', 0),
            frame.get('nuc_dn', 0),
        )
        if self._last_ffc_signature is None:
            self._last_ffc_signature = signature
            return ''
        if signature == self._last_ffc_signature:
            return ''
        self._last_ffc_signature = signature
        if decision == 255:
            return ''

        label = nuc_decision_label(decision)
        event = {'t_s': t_s, 'decision': decision, 'label': label}
        self._ffc_events.append(event)
        color = FFC_EVENT_COLORS.get(decision, '#dddddd')
        line = self.tax.axvline(t_s, color=color, lw=0.8, ls=':', alpha=0.75)
        text = self.tax.text(t_s, 0.97, label, color=color, fontsize=5.5,
                             rotation=90, va='top', ha='right',
                             transform=self.tax.get_xaxis_transform())
        self._ffc_event_artists.extend((line, text))
        return label

    def _on_error(self, msg):
        self.status_lbl.setText(f"错误：{msg}")
        set_status_tone(self.status_lbl, 'error')
        self._had_error = True

    def _on_stopped(self):
        self.start_btn.setEnabled(True)
        self.stop_btn.setEnabled(False)
        if not getattr(self, '_had_error', False):
            self.status_lbl.setText("已停止")
            set_status_tone(self.status_lbl, 'idle')
        self.worker = None

    def _save_png(self):
        if len(self._hist_t) < 2:
            self.status_lbl.setText("样本不足，无法保存")
            return
        default_name = f"trend_{time.strftime('%Y%m%d_%H%M%S')}.png"
        try:
            default_path = export_log_dir() / default_name
        except OSError as exc:
            self._on_error(f"无法创建导出目录：{exc}")
            return
        path, _ = QFileDialog.getSaveFileName(
            self, "保存完整历史趋势图（PNG）", str(default_path), "PNG (*.png)")
        if not path:
            return
        try:
            output_path = prepare_export_path(path)
        except OSError as exc:
            self._on_error(f"无法创建导出目录：{exc}")
            return
        import matplotlib.pyplot as plt
        ts  = np.asarray(self._hist_t,   dtype=np.float32)
        yct = np.asarray(self._hist_center_temp, dtype=np.float32)
        yrt = np.asarray(self._hist_roi_temp, dtype=np.float32)
        ynt = np.asarray(self._hist_ntc, dtype=np.float32)
        yca = np.asarray(self._hist_center_adu, dtype=np.float32)
        yra = np.asarray(self._hist_roi_adu, dtype=np.float32)
        yvr = np.asarray(self._hist_vtemp, dtype=np.float32)
        yvs = np.asarray(self._hist_smooth_vtemp, dtype=np.float32)
        fig, (ax1, ax3) = plt.subplots(2, 1, figsize=(11, 7), sharex=True,
                                      facecolor=FIG_BG)
        ax2 = ax1.twinx()
        ax4 = ax3.twinx()
        for axis in (ax1, ax2, ax3, ax4):
            axis.set_facecolor(CARD_BG)
        ln_ntc, = ax1.plot(ts, ynt, color=NTC_LINE_COLOR, lw=1.0, label='NTC')
        ln_center, = ax2.plot(ts, yct, color=CENTER_TEMP_COLOR, lw=1.35,
                              label='Center point')
        ln_roi, = ax2.plot(ts, yrt, color=CENTER_TEMP_COLOR, lw=0.95,
                           ls='--', alpha=0.7, label='Center 5x5 avg')
        ln_center_adu, = ax3.plot(ts, yca, color=CENTER_ADU_COLOR, lw=1.25,
                                  label='Point ADU')
        ln_roi_adu, = ax3.plot(ts, yra, color=CENTER_ADU_COLOR, lw=0.9,
                               ls='--', alpha=0.65, label='5x5 avg ADU')
        ln_vtemp, = ax4.plot(ts, yvr, color=VTEMP_RAW_COLOR, lw=0.7,
                             alpha=0.5, label='VTEMP raw')
        ln_smooth_vtemp, = ax4.plot(ts, yvs, color=VTEMP_SMOOTH_COLOR,
                                    lw=1.1, label='VTEMP smooth')
        ax3.set_xlabel('t (s)', color=AXIS_FG)
        ax1.set_ylabel('NTC (°C)', color=NTC_LINE_COLOR)
        ax2.set_ylabel('Center (°C)', color=CENTER_TEMP_COLOR)
        ax3.set_ylabel('Center (ADU)', color=CENTER_ADU_COLOR)
        ax4.set_ylabel('VTEMP (ADU)', color=VTEMP_SMOOTH_COLOR)
        ax3.ticklabel_format(axis='y', style='plain', useOffset=False)
        ax4.ticklabel_format(axis='y', style='plain', useOffset=False)
        ax1.tick_params(colors=NTC_LINE_COLOR)
        ax2.tick_params(colors=CENTER_TEMP_COLOR)
        ax3.tick_params(colors=CENTER_ADU_COLOR)
        ax4.tick_params(colors=VTEMP_SMOOTH_COLOR)
        ax3.tick_params(axis='x', colors=AXIS_FG)
        ax1.grid(True, color=CARD_EDGE, lw=0.4, alpha=0.55)
        ax3.grid(True, color=CARD_EDGE, lw=0.4, alpha=0.55)

        for event in self._ffc_events:
            decision = event['decision']
            color = FFC_EVENT_COLORS.get(decision, '#dddddd')
            ax1.axvline(event['t_s'], color=color, lw=0.8, ls=':', alpha=0.75)
            ax1.text(event['t_s'], 0.98, event['label'], color=color,
                     fontsize=6, rotation=90, va='top', ha='right',
                     transform=ax1.get_xaxis_transform())

        legend_kw = dict(fontsize=7.5, frameon=True, framealpha=0.86,
                         facecolor='#171c21', edgecolor=CARD_EDGE,
                         labelcolor=AXIS_FG, borderpad=0.4,
                         handlelength=2.4, columnspacing=1.2)
        ax1.legend(handles=[ln_center, ln_roi, ln_ntc], loc='upper left',
                   ncol=3, **legend_kw)
        ax3.legend(handles=[ln_center_adu, ln_roi_adu, ln_vtemp,
                            ln_smooth_vtemp], loc='upper left', ncol=4,
                   **legend_kw)
        fig.tight_layout()
        try:
            fig.savefig(output_path, dpi=140, facecolor=fig.get_facecolor())
            self.status_lbl.setText(f"已保存：{output_path}")
        except OSError as exc:
            self._on_error(f"PNG 保存失败：{exc}")
        finally:
            plt.close(fig)

    def _save_csv(self):
        if len(self._hist_rows) < 2:
            self.status_lbl.setText("样本不足，无法保存")
            return
        default_name = f"trend_{time.strftime('%Y%m%d_%H%M%S')}.csv"
        try:
            default_path = export_log_dir() / default_name
        except OSError as exc:
            self._on_error(f"无法创建导出目录：{exc}")
            return
        path, _ = QFileDialog.getSaveFileName(
            self, "保存完整诊断数据（CSV）", str(default_path), "CSV (*.csv)")
        if not path:
            return
        try:
            output_path = prepare_export_path(path)
        except OSError as exc:
            self._on_error(f"无法创建导出目录：{exc}")
            return
        fieldnames = list(self._hist_rows[0].keys())
        try:
            with output_path.open('w', newline='', encoding='utf-8-sig') as f:
                writer = csv.DictWriter(f, fieldnames=fieldnames)
                writer.writeheader()
                writer.writerows(self._hist_rows)
            self.status_lbl.setText(f"已保存：{output_path}")
        except OSError as exc:
            self._on_error(f"CSV 保存失败：{exc}")
