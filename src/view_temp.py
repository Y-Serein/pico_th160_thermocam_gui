#!/usr/bin/env python3
"""
TN160 CALI_U8 frame viewer with per-pixel temperature display.

Frame format (19221 bytes):
  [0]            = 0xFF header
  [1..19200]     = 160x120 grayscale pixels (temperature-linear within
                   [t_lo, t_hi]; firmware LUT converts ADU-linear -> temp-linear
                   via T^4 interpolation)
  [19201..19202] = VTEMP raw (uint16 big-endian, 14-bit)
                   T(FPA) = 25 + (VTEMP - 8192) / 70  [°C]
  [19203..19204] = frame min temp x10 (int16 big-endian, 0x7FFF = uncalibrated)
  [19205..19206] = frame max temp x10 (int16 big-endian)
  [19207..19210] = ram_temp_anchor       (int32 big-endian, corrected-ADU anchor)
  [19211..19212] = g_smooth_low          (uint16 BE, 1% percentile smoothed)
  [19213..19214] = g_smooth_high         (uint16 BE, 99% percentile smoothed)
  [19215..19218] = ram_mean_diff         (float32 BE, IEEE 754)
  [19219..19220] = baseline_fpa_vtemp    (uint16 BE, first-FFC VTEMP; 0 before)

Usage:
  python view_temp.py COM5 5000000 [--log diag.csv]
  python view_temp.py /dev/ttyUSB0 5000000
"""

import argparse
import csv
import serial
import struct
import sys
import time
from collections import deque

import numpy as np
import matplotlib.pyplot as plt
import matplotlib.gridspec as gridspec

TREND_MAX_SAMPLES = 1800   # ~3 min @ 10 fps; rolling window
TREND_REDRAW_EVERY = 8     # update trend panel every N frames to save CPU

FRAME_SIZE  = 19221
PX_W, PX_H  = 160, 120
INT16_MAX   = 0x7FFF


# ---------------------------------------------------------------------------
# Serial / frame parsing
# ---------------------------------------------------------------------------

def sync_and_read(ser: serial.Serial) -> bytes:
    """Scan for 0xFF header then read the rest of the frame."""
    while True:
        b = ser.read(1)
        if b == b'\xff':
            rest = ser.read(FRAME_SIZE - 1)
            if len(rest) == FRAME_SIZE - 1:
                return b + rest


def parse_frame(raw: bytes):
    """Return dict with pixels and all tail diagnostics."""
    base = 1 + PX_W * PX_H
    pixels = np.frombuffer(raw[1:base], dtype=np.uint8).reshape(PX_H, PX_W).copy()
    vtemp    = struct.unpack_from('>H', raw, base)[0] & 0x3FFF
    t_lo_x10 = struct.unpack_from('>h', raw, base + 2)[0]
    t_hi_x10 = struct.unpack_from('>h', raw, base + 4)[0]
    anchor      = struct.unpack_from('>i', raw, base + 6)[0]
    smooth_low  = struct.unpack_from('>H', raw, base + 10)[0]
    smooth_high = struct.unpack_from('>H', raw, base + 12)[0]
    mean_diff   = struct.unpack_from('>f', raw, base + 14)[0]
    baseline    = struct.unpack_from('>H', raw, base + 18)[0]
    return {
        'pixels':      pixels,
        'vtemp':       vtemp,
        't_lo_x10':    t_lo_x10,
        't_hi_x10':    t_hi_x10,
        'anchor':      anchor,
        'smooth_low':  smooth_low,
        'smooth_high': smooth_high,
        'mean_diff':   mean_diff,
        'baseline':    baseline,
    }


# ---------------------------------------------------------------------------
# Temperature conversion
# ---------------------------------------------------------------------------

def fpa_to_celsius(vtemp: int) -> float:
    """VTEMP ADC → FPA junction temperature.  H1617B1OD §2.3.5.
    NOTE: formula constants (8192 ref, 70 LSB/°C) do not match this sensor unit;
    absolute value is meaningless.  Only ADU delta is used for FFC triggering."""
    if vtemp == 0:
        return float('nan')
    return 25.0 + (vtemp - 8192) / 70.0


def pixels_to_temp(pixels: np.ndarray, t_lo: float, t_hi: float) -> np.ndarray:
    """
    Convert 8-bit temperature-linear pixels to scene temperature (°C).

    Firmware pipeline (post T^4 fix):
        linear_val = (raw14 - low) / range * 254          (ADU-linear 0-254)
        pixel_u8   = LUT[linear_val]                      (T^4 remap so output
                                                           is linear in °C
                                                           within [t_lo, t_hi])

    Inverse (simple linear map, since firmware LUT already did the T^4 work):
        T = t_lo + (pixel_u8 / 254) * (t_hi - t_lo)
    """
    if t_hi <= t_lo:
        return np.full_like(pixels, t_lo, dtype=np.float32)
    norm = (pixels.astype(np.float32) / 254.0).clip(0.0, 1.0)
    return (t_lo + (t_hi - t_lo) * norm).astype(np.float32)


# ---------------------------------------------------------------------------
# GUI
# ---------------------------------------------------------------------------

class ThermalViewer:
    CMAP = 'inferno'

    def __init__(self):
        # Three rows: image / trend / status bar
        self.fig = plt.figure(figsize=(9, 8.6), facecolor='#111')
        gs = gridspec.GridSpec(3, 2,
                               height_ratios=[14, 4, 1],
                               width_ratios=[20, 1],
                               hspace=0.18, wspace=0.05)
        self.ax   = self.fig.add_subplot(gs[0, 0])
        self.cax  = self.fig.add_subplot(gs[0, 1])
        self.tax  = self.fig.add_subplot(gs[1, :])   # trend panel
        self.tax2 = self.tax.twinx()                  # right-axis for scene °C
        self.sax  = self.fig.add_subplot(gs[2, :])   # status bar

        for ax in (self.ax, self.cax, self.tax, self.tax2, self.sax):
            ax.set_facecolor('#1c1c1c')
        self.sax.set_xticks([])
        self.sax.set_yticks([])
        for sp in self.sax.spines.values():
            sp.set_edgecolor('#333')

        # ---- trend panel ----
        # display buffer: rolling 3-min window for live update (cheap redraw)
        self._trend_t    = deque(maxlen=TREND_MAX_SAMPLES)
        self._trend_fpa  = deque(maxlen=TREND_MAX_SAMPLES)
        self._trend_mid  = deque(maxlen=TREND_MAX_SAMPLES)
        # full-history buffer: unbounded, used by save_trend_png() so the
        # saved figure covers t=0 to end-of-session regardless of window size
        self._hist_t   = []
        self._hist_fpa = []
        self._hist_mid = []
        self._trend_redraw_i = 0

        self.tax .tick_params(colors='#ff8888', labelsize=7, pad=2)
        self.tax2.tick_params(colors='#88cc88', labelsize=7, pad=2)
        for sp in self.tax.spines.values():
            sp.set_edgecolor('#333')
        for sp in self.tax2.spines.values():
            sp.set_edgecolor('#333')
        self.tax .set_ylabel('FPA °C',  color='#ff8888', fontsize=8)
        self.tax2.set_ylabel('场景 °C', color='#88cc88', fontsize=8)
        self.tax .grid(True, color='#333', lw=0.3, alpha=0.5)
        self.tax .set_xlim(0, 60)
        self._trend_line_fpa, = self.tax .plot([], [], color='#ff8888', lw=1.0,
                                                label='FPA')
        self._trend_line_mid, = self.tax2.plot([], [], color='#88cc88', lw=1.0,
                                                label='场景中温')

        blank = np.zeros((PX_H, PX_W), dtype=np.float32)
        self.im = self.ax.imshow(blank, cmap=self.CMAP, vmin=0, vmax=50,
                                 interpolation='nearest', aspect='auto')
        self.cbar = self.fig.colorbar(self.im, cax=self.cax)
        self._style_colorbar()

        self.ax.set_xticks([])
        self.ax.set_yticks([])
        self.title = self.ax.set_title('TN160 — waiting for frame…',
                                       color='white', fontsize=11, pad=8)

        # crosshair (click to move)
        self._cx, self._cy = PX_W // 2, PX_H // 2
        self._ch = self.ax.axhline(self._cy, color='cyan', lw=0.6, alpha=0.8)
        self._cv = self.ax.axvline(self._cx, color='cyan', lw=0.6, alpha=0.8)
        self._ct = self.ax.text(
            self._cx + 3, self._cy - 4, '',
            color='cyan', fontsize=8,
            bbox=dict(boxstyle='round,pad=0.2', fc='black', alpha=0.65))

        # min / max overlays inside image
        kw = dict(fontsize=7.5, bbox=dict(boxstyle='round,pad=0.25',
                                          fc='#000', alpha=0.6))
        self._t_min = self.ax.text(3, PX_H - 5,  '', color='#66aaff', **kw)
        self._t_max = self.ax.text(3, PX_H - 13, '', color='#ffaa44', **kw)

        # status bar fields — two rows via newline in text
        skw = dict(fontsize=8.0, va='center', transform=self.sax.transAxes,
                   bbox=dict(boxstyle='round,pad=0.3', fc='#111', alpha=0.0))
        self._s_tlo   = self.sax.text(0.01, 0.75, '', color='#66aaff', **skw)
        self._s_thi   = self.sax.text(0.13, 0.75, '', color='#ffaa44', **skw)
        self._s_cross = self.sax.text(0.26, 0.75, '', color='cyan',    **skw)
        self._s_vtemp = self.sax.text(0.48, 0.75, '', color='#aaa',    **skw)
        self._s_fpa   = self.sax.text(0.64, 0.75, '', color='#888',    **skw)
        self._s_fps   = self.sax.text(0.88, 0.75, '', color='#777',    **skw)

        # diagnostic row (anchor / smooth / mean_diff / vtemp delta)
        self._s_anch  = self.sax.text(0.01, 0.25, '', color='#cc88ff', **skw)
        self._s_smth  = self.sax.text(0.22, 0.25, '', color='#88cc88', **skw)
        self._s_md    = self.sax.text(0.50, 0.25, '', color='#cccc88', **skw)
        self._s_dv    = self.sax.text(0.70, 0.25, '', color='#ff8888', **skw)

        self.fig.canvas.mpl_connect('button_press_event', self._on_click)
        plt.tight_layout(pad=0.4)

    def _style_colorbar(self):
        self.cbar.set_label('°C', color='white', fontsize=9)
        self.cbar.ax.yaxis.set_tick_params(color='white')
        plt.setp(self.cbar.ax.yaxis.get_ticklabels(), color='white', fontsize=8)

    def _on_click(self, event):
        if event.inaxes is not self.ax:
            return
        x = max(0, min(PX_W - 1, int(round(event.xdata))))
        y = max(0, min(PX_H - 1, int(round(event.ydata))))
        self._cx, self._cy = x, y
        self._ch.set_ydata([y])
        self._cv.set_xdata([x])
        self._ct.set_position((x + 3, y - 4))

    def update(self, frame: dict, fps: float, vtemp_ref: int | None,
               t_s: float):
        pixels   = frame['pixels']
        vtemp    = frame['vtemp']
        t_lo_x10 = frame['t_lo_x10']
        t_hi_x10 = frame['t_hi_x10']
        anchor   = frame['anchor']
        slow     = frame['smooth_low']
        shigh    = frame['smooth_high']
        md       = frame['mean_diff']
        baseline = frame['baseline']

        fpa = fpa_to_celsius(vtemp)
        fpa_ok = not np.isnan(fpa)

        scene_mid = None
        if t_lo_x10 != INT16_MAX and t_hi_x10 != INT16_MAX:
            scene_mid = (t_lo_x10 + t_hi_x10) / 20.0  # /10 for °C, /2 for mean

        fpa_sample = fpa if fpa_ok else np.nan
        mid_sample = scene_mid if scene_mid is not None else np.nan
        self._trend_t  .append(t_s)
        self._trend_fpa.append(fpa_sample)
        self._trend_mid.append(mid_sample)
        self._hist_t  .append(t_s)
        self._hist_fpa.append(fpa_sample)
        self._hist_mid.append(mid_sample)

        calibrated = (t_lo_x10 != INT16_MAX and t_hi_x10 != INT16_MAX)

        if calibrated:
            t_lo = t_lo_x10 / 10.0
            t_hi = t_hi_x10 / 10.0
            temp_img = pixels_to_temp(pixels, t_lo, t_hi)

            self.im.set_data(temp_img)
            self.im.set_clim(t_lo, t_hi)

            ct = temp_img[self._cy, self._cx]
            self._ct.set_text(f'{ct:.1f}°C')

            min_idx = np.argmin(temp_img)
            max_idx = np.argmax(temp_img)
            my, mx = np.unravel_index(min_idx, temp_img.shape)
            hy, hx = np.unravel_index(max_idx, temp_img.shape)
            self._t_min.set_text(f'▼ {temp_img[my, mx]:.1f}°C  ({mx},{my})')
            self._t_max.set_text(f'▲ {temp_img[hy, hx]:.1f}°C  ({hx},{hy})')

            self.title.set_text(f'TN160  {t_lo:.1f}°C ~ {t_hi:.1f}°C')

            # status bar
            self._s_tlo  .set_text(f'T_lo  {t_lo:+.1f}°C')
            self._s_thi  .set_text(f'T_hi  {t_hi:+.1f}°C')
            self._s_cross.set_text(f'准星  {ct:.1f}°C  ({self._cx},{self._cy})')
        else:
            self.im.set_data(pixels.astype(np.float32))
            self.im.set_clim(0, 255)
            self._ct.set_text('')
            self._t_min.set_text('')
            self._t_max.set_text('')
            self.title.set_text('TN160 — 未标定')
            self._s_tlo  .set_text('T_lo  N/A')
            self._s_thi  .set_text('T_hi  N/A')
            self._s_cross.set_text('')

        self._s_vtemp.set_text(f'VTEMP  {vtemp} ADU')
        self._s_fpa  .set_text(f'FPA  {fpa:.1f}°C*' if fpa_ok else 'FPA  N/A')  # * = formula uncalibrated
        self._s_fps  .set_text(f'{fps:.1f} fps')

        # diagnostic row
        self._s_anch.set_text(f'anchor {anchor}')
        self._s_smth.set_text(f'smooth {slow}~{shigh} (Δ{shigh - slow})')
        self._s_md  .set_text(f'mean_diff {md:.1f}')
        # Prefer firmware-reported baseline (first-FFC VTEMP); fall back to
        # session-first reading if FFC has not fired yet.
        ref = baseline if baseline != 0 else vtemp_ref
        if ref is None:
            self._s_dv.set_text('ΔVTEMP  —')
        else:
            tag = 'fw' if baseline != 0 else 'ses'
            self._s_dv.set_text(f'ΔVTEMP  {vtemp - ref:+d} ({tag})')

        # Trend redraw (throttled)
        self._trend_redraw_i = (self._trend_redraw_i + 1) % TREND_REDRAW_EVERY
        if self._trend_redraw_i == 0 and len(self._trend_t) >= 2:
            ts  = np.fromiter(self._trend_t,   dtype=np.float32)
            yfp = np.fromiter(self._trend_fpa, dtype=np.float32)
            ymd = np.fromiter(self._trend_mid, dtype=np.float32)
            self._trend_line_fpa.set_data(ts, yfp)
            self._trend_line_mid.set_data(ts, ymd)
            # Keep a sliding window: show last 3 min OR full history if shorter
            t_end = ts[-1]
            t_start = max(ts[0], t_end - 180.0)
            self.tax.set_xlim(t_start, max(t_end, t_start + 1.0))
            finite_fp = yfp[np.isfinite(yfp)]
            finite_md = ymd[np.isfinite(ymd)]
            if finite_fp.size:
                lo, hi = float(finite_fp.min()), float(finite_fp.max())
                pad = max(0.5, (hi - lo) * 0.1)
                self.tax.set_ylim(lo - pad, hi + pad)
            if finite_md.size:
                lo, hi = float(finite_md.min()), float(finite_md.max())
                pad = max(0.5, (hi - lo) * 0.1)
                self.tax2.set_ylim(lo - pad, hi + pad)

        self.fig.canvas.draw_idle()
        self.fig.canvas.flush_events()

    def alive(self) -> bool:
        return plt.fignum_exists(self.fig.number)

    def save_trend_png(self, path: str) -> bool:
        """Render a standalone trend figure covering the full session (t=0..end)."""
        if len(self._hist_t) < 2:
            return False
        ts  = np.asarray(self._hist_t,   dtype=np.float32)
        yfp = np.asarray(self._hist_fpa, dtype=np.float32)
        ymd = np.asarray(self._hist_mid, dtype=np.float32)

        fig, ax1 = plt.subplots(figsize=(10, 4), facecolor='#111')
        ax1.set_facecolor('#1c1c1c')
        ax2 = ax1.twinx()
        ax1.plot(ts, yfp, color='#ff8888', lw=1.0, label='FPA °C')
        ax2.plot(ts, ymd, color='#88cc88', lw=1.0, label='scene mid °C')
        ax1.set_xlabel('t (s)', color='white', fontsize=9)
        ax1.set_ylabel('FPA °C',   color='#ff8888', fontsize=9)
        ax2.set_ylabel('scene °C', color='#88cc88', fontsize=9)
        ax1.tick_params(colors='#ff8888', labelsize=8)
        ax2.tick_params(colors='#88cc88', labelsize=8)
        ax1.grid(True, color='#333', lw=0.3, alpha=0.5)
        for sp in ax1.spines.values():
            sp.set_edgecolor('#444')
        fig.tight_layout()
        try:
            fig.savefig(path, dpi=140, facecolor=fig.get_facecolor())
        finally:
            plt.close(fig)
        return True


# ---------------------------------------------------------------------------
# Main loop
# ---------------------------------------------------------------------------

def main():
    ap = argparse.ArgumentParser(add_help=False)
    ap.add_argument('port')
    ap.add_argument('baud', type=int)
    ap.add_argument('--log', metavar='FILE', default=None,
                    help='append per-frame diagnostic CSV to FILE')
    ap.add_argument('--log-every', type=int, default=30,
                    help='write one CSV row every N frames (default 30)')
    ap.add_argument('--trend-out', metavar='DIR', default='.',
                    help='directory to auto-save the trend PNG on exit '
                         '(default: current dir; pass "" to disable)')
    ap.add_argument('-h', '--help', action='store_true')
    args = ap.parse_args()

    if args.help:
        print(__doc__)
        sys.exit(0)

    ser = serial.Serial(port=args.port, baudrate=args.baud,
                        bytesize=serial.EIGHTBITS,
                        parity=serial.PARITY_NONE,
                        stopbits=serial.STOPBITS_ONE,
                        timeout=2.0)
    try:
        ser.set_buffer_size(rx_size=512 * 1024)
    except Exception:
        pass

    print(f'Opened {args.port} @ {args.baud}  (close window or Ctrl-C to stop)')

    logf = None
    logw = None
    if args.log:
        logf = open(args.log, 'a', newline='')
        logw = csv.writer(logf)
        if logf.tell() == 0:
            logw.writerow(['t_s', 'vtemp', 'baseline', 'anchor',
                           'smooth_low', 'smooth_high', 'mean_diff',
                           't_lo_x10', 't_hi_x10'])
        print(f'Logging to {args.log} (every {args.log_every} frames)')

    viewer = ThermalViewer()
    plt.ion()
    plt.show(block=False)

    frame_count = 0
    t_start = time.time()
    vtemp_ref = None

    try:
        while viewer.alive():
            raw = sync_and_read(ser)
            frame = parse_frame(raw)
            frame_count += 1
            now = time.time()
            fps = frame_count / (now - t_start)

            if vtemp_ref is None and frame['vtemp'] > 0:
                vtemp_ref = frame['vtemp']

            viewer.update(frame, fps, vtemp_ref, now - t_start)

            if logw is not None and (frame_count % args.log_every) == 0:
                logw.writerow([f'{now - t_start:.2f}',
                               frame['vtemp'],
                               frame['baseline'],
                               frame['anchor'],
                               frame['smooth_low'],
                               frame['smooth_high'],
                               f"{frame['mean_diff']:.3f}",
                               frame['t_lo_x10'],
                               frame['t_hi_x10']])
                logf.flush()

    except KeyboardInterrupt:
        pass
    finally:
        elapsed = time.time() - t_start
        print(f'\n{frame_count} frames in {elapsed:.1f}s  '
              f'({frame_count/elapsed:.1f} fps avg)')
        ser.close()
        if logf is not None:
            logf.close()
        if args.trend_out:
            import os
            os.makedirs(args.trend_out, exist_ok=True)
            stamp = time.strftime('%Y%m%d_%H%M%S')
            path = os.path.join(args.trend_out, f'trend_{stamp}.png')
            if viewer.save_trend_png(path):
                print(f'Trend saved to {path}')
            else:
                print('Trend not saved (insufficient samples)')
        plt.close('all')


if __name__ == '__main__':
    main()
