"""QThread workers so serial I/O never blocks the Qt event loop."""
import time
import numpy as np
import serial
from PySide6.QtCore import QThread, Signal

from .protocol import (sync_and_read_frame, parse_monitor_frame,
                       sync_and_read_raw_frame, parse_raw_frame,
                       read_payload_or_error, read_runtime_ffc_map,
                       read_static_calibration,
                       read_thermal_v2_write_reply,
                       parse_badpts, FFC_MAP_COMMAND, STATIC_CAL_COMMAND,
                       THERMAL_V2_WRITE_COMMAND,
                       THERMAL_V2_READY_HEADER, THERMAL_V2_READY_MAP0,
                       THERMAL_V2_WRITE_OK,
                       IMG_PAYLOAD_SIZE, GAIN_PAYLOAD_SIZE, BADPT_PAYLOAD_SIZE,
                       PX_W, PX_H)
from .thermal_capture_io import build_thermal_v2_calibration


def _set_serial_buffer(ser):
    try:
        ser.set_buffer_size(rx_size=512 * 1024)
    except Exception:
        pass


def _telemetry_only(frame):
    return {key: value for key, value in frame.items() if key != 'pixels'}


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
        rx_buf = bytearray()
        try:
            while not self._stop:
                raw = sync_and_read_frame(ser, rx_buf)
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


class RawCaptureWorker(QThread):
    """Capture existing 0xAA RAW_U16 frames and restore monitor mode."""
    progress = Signal(str)
    success = Signal(object)
    error = Signal(str)

    RAW_BAUD = 2000000

    def __init__(self, port, control_baud, frame_count, parent=None):
        super().__init__(parent)
        self.port = port
        self.control_baud = int(control_baud)
        self.frame_count = int(frame_count)

    def _send_command(self, command, baud):
        with serial.Serial(self.port, baud, timeout=1.0) as ser:
            ser.reset_input_buffer()
            ser.write(bytes([command]))
            ser.flush()
        time.sleep(0.35)

    def _read_monitor_snapshot(self):
        self._send_command(0x11, self.RAW_BAUD)
        with serial.Serial(self.port, self.RAW_BAUD, timeout=2.0) as ser:
            _set_serial_buffer(ser)
            ser.reset_input_buffer()
            raw = sync_and_read_frame(ser, bytearray())
        return _telemetry_only(parse_monitor_frame(raw))

    def run(self):
        restored = False
        try:
            if self.control_baud != self.RAW_BAUD:
                raise ValueError('当前采集仅支持设备处于 2000000 波特率')
            self.progress.emit('读取切换前NTC/VTEMP/FFC状态…')
            pre = self._read_monitor_snapshot()

            self.progress.emit('发送0xAA，切换到现有RAW_U16模式…')
            self._send_command(0xAA, self.RAW_BAUD)
            frames = []
            row_meta = []
            frame_vtemp = []
            started = time.monotonic()
            with serial.Serial(self.port, self.RAW_BAUD, timeout=3.0) as ser:
                _set_serial_buffer(ser)
                ser.reset_input_buffer()
                rx_buf = bytearray()
                for index in range(self.frame_count):
                    if self.isInterruptionRequested():
                        raise RuntimeError('原始帧采集已取消')
                    decoded = parse_raw_frame(
                        sync_and_read_raw_frame(ser, rx_buf))
                    frames.append(decoded['pixels'])
                    row_meta.append(decoded['row_meta'])
                    frame_vtemp.append(decoded['frame_vtemp'])
                    if (index + 1) % 8 == 0 or index + 1 == self.frame_count:
                        self.progress.emit(
                            f'已采集 {index + 1}/{self.frame_count} 帧')
            duration_s = time.monotonic() - started

            self.progress.emit('恢复0x11监控模式并读取结束状态…')
            post = self._read_monitor_snapshot()
            restored = True
            self.success.emit({
                'frames': np.stack(frames),
                'row_meta': np.stack(row_meta),
                'frame_vtemp': np.asarray(frame_vtemp, dtype=np.uint16),
                'pre': pre,
                'post': post,
                'duration_s': duration_s,
            })
        except Exception as exc:
            self.error.emit(str(exc))
        finally:
            if not restored:
                try:
                    self._send_command(0x11, self.RAW_BAUD)
                    self.progress.emit('异常退出后已尝试恢复0x11监控模式')
                except Exception as restore_exc:
                    self.progress.emit(f'恢复监控模式失败：{restore_exc}')


class FfcRuntimeDiagnosticWorker(QThread):
    """Capture early/stable runtime FFC maps with matching RAW frame stacks."""
    progress = Signal(str)
    success = Signal(object)
    error = Signal(str)

    BAUD = 2000000
    EARLY_FFC_COUNT = 3
    STABLE_UPTIME_MS = 600000

    def __init__(self, port, frame_count=32, parent=None):
        super().__init__(parent)
        self.port = port
        self.frame_count = int(frame_count)

    def _open(self):
        ser = serial.Serial(self.port, self.BAUD, timeout=3.0)
        _set_serial_buffer(ser)
        ser.reset_input_buffer()
        return ser

    def _read_monitor(self, ser):
        raw = sync_and_read_frame(ser, bytearray())
        return _telemetry_only(parse_monitor_frame(raw))

    def _set_monitor_mode(self, ser):
        ser.write(b'\x11')
        ser.flush()
        time.sleep(0.25)
        ser.reset_input_buffer()

    def _wait_for_ffc_count(self, ser, *, minimum=None, after=None,
                            timeout_s=75.0):
        started = time.monotonic()
        last_report = -1
        while time.monotonic() - started < timeout_s:
            if self.isInterruptionRequested():
                raise RuntimeError('运行期FFC诊断已取消')
            frame = self._read_monitor(ser)
            count = int(frame['nuc_count'])
            if ((minimum is not None and count >= minimum)
                    or (after is not None and count > after)):
                return frame
            elapsed = int(time.monotonic() - started)
            if elapsed // 10 != last_report:
                last_report = elapsed // 10
                self.progress.emit(f'等待有效FFC，当前计数 {count}…')
        raise TimeoutError('等待有效FFC超时')

    def _capture_pair(self, ser, pre, packet=None):
        if packet is None:
            ser.reset_input_buffer()
            ser.write(bytes([FFC_MAP_COMMAND]))
            ser.flush()
            packet = read_runtime_ffc_map(ser)
        if not packet['runtime_ffc_valid'] or not packet['ram_map_active']:
            raise RuntimeError('设备返回的不是有效运行期FFC map')
        if int(packet['ffc_count']) != int(pre['nuc_count']):
            raise RuntimeError(
                'FFC计数在map导出前发生变化，未形成同步配对')

        frames = []
        row_meta = []
        frame_vtemp = []
        started = time.monotonic()
        try:
            ser.reset_input_buffer()
            ser.write(b'\xAA')
            ser.flush()
            time.sleep(0.25)
            ser.reset_input_buffer()
            rx_buf = bytearray()
            for index in range(self.frame_count):
                if self.isInterruptionRequested():
                    raise RuntimeError('运行期FFC诊断已取消')
                decoded = parse_raw_frame(
                    sync_and_read_raw_frame(ser, rx_buf))
                frames.append(decoded['pixels'])
                row_meta.append(decoded['row_meta'])
                frame_vtemp.append(decoded['frame_vtemp'])
                if (index + 1) % 8 == 0 or index + 1 == self.frame_count:
                    self.progress.emit(
                        f'同步RAW {index + 1}/{self.frame_count} 帧')
        finally:
            self._set_monitor_mode(ser)

        post = self._read_monitor(ser)
        if int(post['nuc_count']) != int(packet['ffc_count']):
            raise RuntimeError('RAW采集期间发生FFC，本组配对作废')
        return {
            'map_packet': packet,
            'frames': np.stack(frames),
            'row_meta': np.stack(row_meta),
            'frame_vtemp': np.asarray(frame_vtemp, dtype=np.uint16),
            'pre': pre,
            'post': post,
            'duration_s': time.monotonic() - started,
        }

    def _wait_until_stable(self, early_uptime_ms):
        remaining = max(0.0,
                        (self.STABLE_UPTIME_MS - early_uptime_ms) / 1000.0)
        next_report = remaining
        while remaining > 0:
            if self.isInterruptionRequested():
                raise RuntimeError('运行期FFC诊断已取消')
            if remaining <= next_report:
                self.progress.emit(
                    f'保持场景不动，距稳定采集约 {int(remaining + 0.5)} 秒')
                next_report -= 30.0
            step = min(1.0, remaining)
            self.msleep(max(1, int(step * 1000)))
            remaining -= step

    def run(self):
        try:
            self.progress.emit('连接设备并等待冷启动第3次有效FFC…')
            with self._open() as ser:
                self._set_monitor_mode(ser)
                first = self._read_monitor(ser)
                first_count = int(first['nuc_count'])
                if first_count > 4:
                    raise RuntimeError(
                        f'设备已运行过久（FFC计数 {first_count}）；'
                        '需要冷启动后立即开始')
                early_pre = (first if first_count >= self.EARLY_FFC_COUNT
                             else self._wait_for_ffc_count(
                                 ser, minimum=self.EARLY_FFC_COUNT,
                                 timeout_s=45.0))
                self.progress.emit(
                    f'采集升温早期配对（FFC {early_pre["nuc_count"]}）…')
                early = self._capture_pair(ser, early_pre)

            early_uptime_ms = int(early['map_packet']['uptime_ms'])
            if early_uptime_ms > 120000:
                raise RuntimeError(
                    f'早期配对时设备已运行 {early_uptime_ms / 1000:.1f} 秒，'
                    '不属于冷启动早期')
            self._wait_until_stable(early_uptime_ms)

            self.progress.emit('等待稳定阶段下一次有效FFC…')
            with self._open() as ser:
                self._set_monitor_mode(ser)
                stable_base = self._read_monitor(ser)
                if int(stable_base['nuc_count']) <= int(
                        early['map_packet']['ffc_count']):
                    raise RuntimeError('等待期间设备疑似重启，诊断作废')
                stable_pre = self._wait_for_ffc_count(
                    ser, after=int(stable_base['nuc_count']), timeout_s=75.0)
                self.progress.emit(
                    f'采集稳定配对（FFC {stable_pre["nuc_count"]}）…')
                stable = self._capture_pair(ser, stable_pre)

            if int(stable['map_packet']['uptime_ms']) < self.STABLE_UPTIME_MS:
                raise RuntimeError('稳定配对早于设备运行10分钟，诊断作废')
            self.success.emit({
                'early': early,
                'stable': stable,
                'frame_count': self.frame_count,
                'stable_uptime_target_ms': self.STABLE_UPTIME_MS,
            })
        except Exception as exc:
            self.error.emit(str(exc))


class ThermalV2CalibrationWorker(FfcRuntimeDiagnosticWorker):
    """Capture four matched thermal states and program per-device Flash V2."""

    TARGET_UPTIME_MS = (6000, 60000, 120000, 300000)
    LATEST_UPTIME_MS = (30000, 90000, 150000, 320000)
    MAX_ELAPSED_S = 360.0

    def __init__(self, port, frame_count=32, parent=None):
        super().__init__(port, frame_count, parent)

    def _check_deadline(self, deadline):
        if time.monotonic() >= deadline:
            raise TimeoutError('自动标定已达到360秒硬上限，停止且不延长')

    def _read_state_packet(self, ser, *, state_index, after_count, deadline):
        target_ms = self.TARGET_UPTIME_MS[state_index]
        latest_ms = self.LATEST_UPTIME_MS[state_index]
        minimum_count = 3 if state_index == 0 else 0
        inspected_count = int(after_count)
        last_report_s = -1

        while True:
            self._check_deadline(deadline)
            if self.isInterruptionRequested():
                raise RuntimeError('Flash V2自动标定已取消')
            frame = self._read_monitor(ser)
            count = int(frame['nuc_count'])
            elapsed_s = int(self.MAX_ELAPSED_S
                            - max(0.0, deadline - time.monotonic()))
            if elapsed_s // 15 != last_report_s:
                last_report_s = elapsed_s // 15
                self.progress.emit(
                    f'T{state_index} 等待目标FFC，当前计数 {count}，'
                    f'流程已用 {elapsed_s} 秒…')
            if count <= inspected_count or count < minimum_count:
                continue

            ser.reset_input_buffer()
            ser.write(bytes([FFC_MAP_COMMAND]))
            ser.flush()
            packet = read_runtime_ffc_map(ser)
            if (not packet['runtime_ffc_valid']
                    or not packet['ram_map_active']):
                raise RuntimeError('设备返回的不是有效运行期FFC map')
            if int(packet['ffc_count']) != count:
                raise RuntimeError('FFC计数在map导出前发生变化，配对作废')
            inspected_count = count
            uptime_ms = int(packet['uptime_ms'])
            self.progress.emit(
                f'T{state_index} 检查FFC {count}：设备运行 '
                f'{uptime_ms / 1000:.1f} 秒，目标≥{target_ms / 1000:.0f}秒')
            if uptime_ms < target_ms:
                continue
            if uptime_ms > latest_ms:
                raise TimeoutError(
                    f'T{state_index} 首个可用FFC已到 {uptime_ms / 1000:.1f} 秒，'
                    f'超过本状态 {latest_ms / 1000:.0f} 秒上限')
            return frame, packet

    def _read_static_calibration(self, ser):
        ser.reset_input_buffer()
        ser.write(bytes([STATIC_CAL_COMMAND]))
        ser.flush()
        result = read_static_calibration(ser)
        self._set_monitor_mode(ser)
        return result

    @staticmethod
    def _reply_summary(reply):
        return {
            'status': int(reply['status']),
            'detail': int(reply['detail']),
            'payload_crc32': int(reply['payload_crc32']),
            'header_crc32': int(reply['header_crc32']),
        }

    def _write_v2(self, ser, package):
        replies = []

        def receive(expected_status, label):
            reply = read_thermal_v2_write_reply(ser)
            summary = self._reply_summary(reply)
            replies.append(summary)
            if int(reply['status']) != expected_status:
                raise RuntimeError(
                    f'{label}失败：status=0x{reply["status"]:02X}, '
                    f'detail={reply["detail"]}')
            return reply

        try:
            ser.reset_input_buffer()
            ser.write(bytes([THERMAL_V2_WRITE_COMMAND]))
            ser.flush()
            self.progress.emit('Flash V2：等待头部握手…')
            receive(THERMAL_V2_READY_HEADER, 'V2头部握手')
            if ser.write(package['header']) != len(package['header']):
                raise RuntimeError('V2头部发送不完整')
            ser.flush()

            for index, payload in enumerate(package['map_payloads']):
                self.progress.emit(f'Flash V2：等待并写入map {index}/3…')
                reply = receive(
                    THERMAL_V2_READY_MAP0 + index,
                    f'V2 map {index}握手')
                if (int(reply['payload_crc32'])
                        != int(package['crc']['payload_crc32'])
                        or int(reply['header_crc32'])
                        != int(package['crc']['header_crc32'])):
                    raise RuntimeError(f'V2 map {index}握手CRC标识不一致')
                if ser.write(payload) != len(payload):
                    raise RuntimeError(f'V2 map {index}发送不完整')
                ser.flush()

            final = receive(THERMAL_V2_WRITE_OK, 'V2最终回读')
            if (int(final['payload_crc32'])
                    != int(package['crc']['payload_crc32'])
                    or int(final['header_crc32'])
                    != int(package['crc']['header_crc32'])):
                raise RuntimeError('V2最终回读CRC不一致')
            return {'ok': True, 'replies': replies}
        except Exception as exc:
            return {'ok': False, 'error': str(exc), 'replies': replies}

    def run(self):
        started = time.monotonic()
        deadline = started + self.MAX_ELAPSED_S
        states = []
        static_calibration = None
        package = None
        try:
            self.progress.emit(
                '连接设备；固定高发射率平场保持不动，开始6分钟Flash V2自动标定…')
            with self._open() as ser:
                self._set_monitor_mode(ser)
                first = self._read_monitor(ser)
                first_count = int(first['nuc_count'])
                if first_count > 3:
                    raise RuntimeError(
                        f'设备已运行过久（FFC计数 {first_count}）；'
                        '请完全断电冷却后，上电立即开始')

                after_count = -1
                for state_index in range(4):
                    pre, packet = self._read_state_packet(
                        ser, state_index=state_index,
                        after_count=after_count, deadline=deadline)
                    self.progress.emit(
                        f'采集T{state_index}配对（FFC {packet["ffc_count"]}，'
                        f'{packet["uptime_ms"] / 1000:.1f}秒）…')
                    state = self._capture_pair(ser, pre, packet=packet)
                    states.append(state)
                    after_count = int(packet['ffc_count'])
                    self._check_deadline(deadline)

                    if state_index == 0:
                        self.progress.emit('读取每机factory gain与V1温度元数据…')
                        static_calibration = self._read_static_calibration(ser)

                self.progress.emit('计算四温区绝对开放光路残差与CRC…')
                package = build_thermal_v2_calibration(
                    states, static_calibration)
                self._check_deadline(deadline)
                flash_write = self._write_v2(ser, package)
                time.sleep(0.2)
                try:
                    self._set_monitor_mode(ser)
                except Exception:
                    pass

            elapsed_s = time.monotonic() - started
            if elapsed_s > self.MAX_ELAPSED_S and flash_write['ok']:
                flash_write = {
                    **flash_write,
                    'ok': False,
                    'error': '固件回读完成但总流程超过360秒硬上限',
                }
            self.success.emit({
                'states': states,
                'static_calibration': static_calibration,
                'package': package,
                'flash_write': flash_write,
                'elapsed_to_flash_s': elapsed_s,
                'target_uptime_ms': self.TARGET_UPTIME_MS,
                'frame_count': self.frame_count,
            })
        except Exception as exc:
            self.error.emit(str(exc))


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
