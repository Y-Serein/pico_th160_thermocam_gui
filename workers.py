"""QThread workers so serial I/O never blocks the Qt event loop."""
import time
import numpy as np
import serial
from PySide6.QtCore import QThread, Signal

from protocol import (sync_and_read_frame, parse_monitor_frame,
                      read_payload_or_error, parse_badpts,
                      IMG_PAYLOAD_SIZE, GAIN_PAYLOAD_SIZE, BADPT_PAYLOAD_SIZE,
                      PX_W, PX_H)


class MonitorWorker(QThread):
    frame_ready = Signal(dict, float, float)
    error = Signal(str)
    stopped = Signal()

    def __init__(self, port, baud, parent=None):
        super().__init__(parent)
        self.port = port
        self.baud = baud
        self._stop = False

    def stop(self):
        self._stop = True

    def run(self):
        try:
            ser = serial.Serial(self.port, self.baud, timeout=2.0)
            try:
                ser.set_buffer_size(rx_size=512 * 1024)
            except Exception:
                pass
        except Exception as e:
            self.error.emit(f"open port failed: {e}")
            self.stopped.emit()
            return

        t_start = time.time()
        n = 0
        try:
            while not self._stop:
                raw = sync_and_read_frame(ser)
                n += 1
                now = time.time()
                fps = n / max(now - t_start, 1e-6)
                frame = parse_monitor_frame(raw)
                self.frame_ready.emit(frame, fps, now - t_start)
        except Exception as e:
            if not self._stop:
                self.error.emit(str(e))
        finally:
            try:
                ser.close()
            except Exception:
                pass
            self.stopped.emit()


class CalibWorker(QThread):
    progress = Signal(str)
    success = Signal(object, object, list)
    error = Signal(str)

    def __init__(self, port, baud_def, baud, parent=None):
        super().__init__(parent)
        self.port = port
        self.baud_def = baud_def
        self.baud = baud

    def run(self):
        try:
            self.progress.emit(f"触发 Dump 模式 @ {self.baud_def}…")
            with serial.Serial(self.port, self.baud_def, timeout=1.0) as ser:
                ser.write(bytes([0xDD]))
            time.sleep(0.5)

            self.progress.emit(f"切换到 {self.baud} 接收 payload…")
            with serial.Serial(self.port, self.baud, timeout=2.0) as ser:
                try:
                    ser.set_buffer_size(rx_size=512 * 1024)
                except Exception:
                    pass

                self.progress.emit("  ↓ img_bg (38400 B)")
                r = read_payload_or_error(ser, IMG_PAYLOAD_SIZE)
                if r['type'] != 'data':
                    raise RuntimeError(f"img_bg 中断: {r}")
                img_bg = np.frombuffer(r['payload'], dtype='<u2').reshape(PX_H, PX_W).copy()

                self.progress.emit("  ↓ gain    (76800 B)")
                r = read_payload_or_error(ser, GAIN_PAYLOAD_SIZE)
                if r['type'] != 'data':
                    raise RuntimeError(f"gain 中断: {r}")
                gain = np.frombuffer(r['payload'], dtype='<f4').reshape(PX_H, PX_W).copy()

                self.progress.emit("  ↓ badpts  (10 B)")
                r = read_payload_or_error(ser, BADPT_PAYLOAD_SIZE)
                if r['type'] != 'data':
                    raise RuntimeError(f"badpts 中断: {r}")
                badpts = parse_badpts(r['payload'])

                self.progress.emit("  ↓ finish flag")
                r = read_payload_or_error(ser, 0)
                if r['type'] != 'finish':
                    raise RuntimeError(f"结束标志异常: {r}")

            self.progress.emit("Dump 完成")
            self.success.emit(img_bg, gain, badpts)
        except Exception as e:
            self.error.emit(str(e))


def _parse_u16_be(buf, h, w):
    u8 = np.frombuffer(buf, dtype=np.uint8)
    return (((u8[0::2].astype(np.uint16) << 8) | u8[1::2])
            .reshape(h, w).copy())


class CalibRunWorker(QThread):
    """Trigger a full calibration on the device (0xCC) and read results."""
    progress = Signal(str)
    # img_l, img_h, img_bg, gain, badpts, bg_ok
    success = Signal(object, object, object, object, list, bool)
    error = Signal(str)

    def __init__(self, port, baud_def, baud, parent=None):
        super().__init__(parent)
        self.port = port
        self.baud_def = baud_def
        self.baud = baud

    def run(self):
        try:
            self.progress.emit(f"send 0xCC @ {self.baud_def}…")
            with serial.Serial(self.port, self.baud_def, timeout=1.0) as ser:
                ser.write(bytes([0xCC]))
            time.sleep(0.5)

            self.progress.emit(f"switch to {self.baud} for payload…")
            with serial.Serial(self.port, self.baud, timeout=120.0) as ser:
                try:
                    ser.set_buffer_size(rx_size=512 * 1024)
                except Exception:
                    pass

                self.progress.emit("  ↓ img_l (cold shutter, up to ~100s)")
                r = read_payload_or_error(ser, IMG_PAYLOAD_SIZE)
                if r['type'] != 'data':
                    raise RuntimeError(f"img_l: {r}")
                img_l = _parse_u16_be(r['payload'], PX_H, PX_W)

                ser.timeout = 5.0
                self.progress.emit("  ↓ img_h (38400 B)")
                r = read_payload_or_error(ser, IMG_PAYLOAD_SIZE)
                if r['type'] != 'data':
                    raise RuntimeError(f"img_h: {r}")
                img_h = _parse_u16_be(r['payload'], PX_H, PX_W)

                self.progress.emit("  ↓ img_bg (38400 B)")
                r = read_payload_or_error(ser, IMG_PAYLOAD_SIZE)
                if r['type'] != 'data':
                    raise RuntimeError(f"img_bg: {r}")
                img_bg = np.frombuffer(r['payload'], dtype='<u2').reshape(PX_H, PX_W).copy()

                self.progress.emit("  ↓ gain   (76800 B)")
                r = read_payload_or_error(ser, GAIN_PAYLOAD_SIZE)
                if r['type'] != 'data':
                    raise RuntimeError(f"gain: {r}")
                gain = np.frombuffer(r['payload'], dtype='<f4').reshape(PX_H, PX_W).copy()

                self.progress.emit("  ↓ badpts (10 B)")
                r = read_payload_or_error(ser, BADPT_PAYLOAD_SIZE)
                if r['type'] != 'data':
                    raise RuntimeError(f"badpts: {r}")
                badpts = parse_badpts(r['payload'])

                self.progress.emit("  ↓ finish flag")
                r = read_payload_or_error(ser, 0)
                if r['type'] != 'finish':
                    raise RuntimeError(f"finish: {r}")

            bg_ok = bool(np.array_equal(img_l, img_bg))
            self.progress.emit("bg check: PASS" if bg_ok
                               else "bg check: FAIL (img_l != img_bg, flash write mismatch)")
            self.progress.emit("Calibration done")
            self.success.emit(img_l, img_h, img_bg, gain, badpts, bg_ok)
        except Exception as e:
            self.error.emit(str(e))
