"""Shared serial-protocol parsing for monitor frames and calibration dump."""
import math
import struct
import zlib
import numpy as np

FRAME_SIZE = 19255
PX_W, PX_H = 160, 120
RAW_ROW_WORDS = PX_W + 2
RAW_FRAME_SIZE = 2 + PX_H * RAW_ROW_WORDS * 2 + 2
INT16_MAX = 0x7FFF
TEMP_MIN_X10 = -1000
TEMP_MAX_X10 = 3200
U14_MAX = 0x3FFF
NTC_ADC_MAX = 4095

# Board NTC: 10K pull-up to 3.3 V, 10K NTC to GND (β=3435, R0=10K @ 25°C).
# V_adc = 3.3 · R_ntc / (R_ntc + 10K) → R_ntc = 10K · ADC / (4095 − ADC).
NTC_PULLUP_R   = 10000.0
NTC_R0         = 10000.0
NTC_BETA       = 3435.0
NTC_T0_K       = 298.15
ADC_FULL_SCALE = 4095


def ntc_adu_to_celsius(adc):
    """Convert raw NTC ADC (12-bit) to °C. Returns nan if reading is at rail."""
    if adc <= 0 or adc >= ADC_FULL_SCALE:
        return float('nan')
    r_ntc = NTC_PULLUP_R * adc / (ADC_FULL_SCALE - adc)
    inv_t = 1.0 / NTC_T0_K + math.log(r_ntc / NTC_R0) / NTC_BETA
    return 1.0 / inv_t - 273.15

IMG_PAYLOAD_SIZE = PX_W * PX_H * 2
GAIN_PAYLOAD_SIZE = PX_W * PX_H * 4
MAX_BADPT = 5
BADPT_PAYLOAD_SIZE = MAX_BADPT * 2
STATIC_CAL_COMMAND = 0xED
STATIC_CAL_MAGIC = b'TNCS'
STATIC_CAL_VERSION = 1
STATIC_CAL_HEADER_FORMAT = '<4sHHHHIIffI'
STATIC_CAL_HEADER_SIZE = struct.calcsize(STATIC_CAL_HEADER_FORMAT)
FFC_MAP_COMMAND = 0xEE
FFC_MAP_MAGIC = b'TNFM'
FFC_MAP_VERSION = 1
FFC_MAP_HEADER_FORMAT = '<4sHHHHIIiHHHBBI'
FFC_MAP_HEADER_SIZE = struct.calcsize(FFC_MAP_HEADER_FORMAT)
FFC_MAP_PAYLOAD_SIZE = PX_W * PX_H * 2
THERMAL_V2_WRITE_COMMAND = 0xEF
THERMAL_V2_MAGIC = b'TNV2'
THERMAL_V2_VERSION = 2
THERMAL_V2_MAP_COUNT = 4
THERMAL_V2_MAP_SIZE = PX_W * PX_H * 2
THERMAL_V2_MAP_OFFSETS = (0x02E000, 0x038000, 0x042000, 0x04C000)
THERMAL_V2_HEADER_FORMAT = '<4sHHIIHHBBHI4H4h4I4II12sI'
THERMAL_V2_HEADER_SIZE = struct.calcsize(THERMAL_V2_HEADER_FORMAT)
THERMAL_V2_REPLY_MAGIC = b'TNVW'
THERMAL_V2_REPLY_FORMAT = '<4sHBBII'
THERMAL_V2_REPLY_SIZE = struct.calcsize(THERMAL_V2_REPLY_FORMAT)
THERMAL_V2_READY_HEADER = 0x7F
THERMAL_V2_READY_MAP0 = 0x10
THERMAL_V2_WRITE_OK = 0x00


def _telemetry_plausible(raw):
    if len(raw) != FRAME_SIZE or raw[0] != 0xFF:
        return False

    base = 1 + PX_W * PX_H
    raw_vtemp = struct.unpack_from('>H', raw, base)[0]
    if raw_vtemp & 0xC000:
        return False

    t_lo_x10 = struct.unpack_from('>h', raw, base + 2)[0]
    t_hi_x10 = struct.unpack_from('>h', raw, base + 4)[0]
    temp_pair = (t_lo_x10, t_hi_x10)
    if temp_pair != (INT16_MAX, INT16_MAX):
        if not (TEMP_MIN_X10 <= t_lo_x10 <= TEMP_MAX_X10):
            return False
        if not (TEMP_MIN_X10 <= t_hi_x10 <= TEMP_MAX_X10):
            return False
        if t_hi_x10 < t_lo_x10:
            return False

    smooth_low = struct.unpack_from('>H', raw, base + 10)[0]
    smooth_high = struct.unpack_from('>H', raw, base + 12)[0]
    ntc_ref = struct.unpack_from('>H', raw, base + 18)[0]
    ntc = struct.unpack_from('>H', raw, base + 20)[0]
    nuc_decision = raw[base + 22]
    smooth_vtemp = struct.unpack_from('>H', raw, base + 44)[0]
    center_raw_x10 = struct.unpack_from('>h', raw, base + 46)[0]
    center_roi_x10 = struct.unpack_from('>h', raw, base + 48)[0]
    center_raw_adu = struct.unpack_from('>H', raw, base + 50)[0]
    center_roi_adu = struct.unpack_from('>H', raw, base + 52)[0]

    if smooth_low > U14_MAX or smooth_high > U14_MAX:
        return False
    if smooth_high < smooth_low:
        return False
    if smooth_vtemp > U14_MAX:
        return False
    if center_raw_adu > U14_MAX or center_roi_adu > U14_MAX:
        return False
    if ntc_ref not in (0, 0xFFFF) and ntc_ref > NTC_ADC_MAX:
        return False
    if ntc not in (0, 0xFFFF) and ntc > NTC_ADC_MAX:
        return False
    if nuc_decision not in (0, 1, 2, 3, 4, 255):
        return False
    for center_x10 in (center_raw_x10, center_roi_x10):
        if center_x10 != INT16_MAX and not (
                TEMP_MIN_X10 <= center_x10 <= TEMP_MAX_X10):
            return False

    return True


def sync_and_read_frame(ser, buf=None):
    """Read one validated frame while retaining surplus bytes for the next."""
    if buf is None:
        buf = bytearray()
    while True:
        if len(buf) < FRAME_SIZE:
            chunk = ser.read(FRAME_SIZE - len(buf))
            if not chunk:
                raise TimeoutError("monitor frame sync timeout")
            buf.extend(chunk)

        start = buf.find(b'\xff')
        if start < 0:
            buf.clear()
            continue

        if len(buf) - start < FRAME_SIZE:
            if start > 0:
                del buf[:start]
            continue

        raw = bytes(buf[start:start + FRAME_SIZE])
        if _telemetry_plausible(raw):
            del buf[:start + FRAME_SIZE]
            return raw

        del buf[:start + 1]


def parse_monitor_frame(raw):
    if not _telemetry_plausible(raw):
        raise ValueError("invalid monitor frame telemetry")

    base = 1 + PX_W * PX_H
    pixels = np.frombuffer(raw[1:base], dtype=np.uint8).reshape(PX_H, PX_W).copy()
    vtemp       = struct.unpack_from('>H', raw, base)[0] & 0x3FFF
    t_lo_x10    = struct.unpack_from('>h', raw, base + 2)[0]
    t_hi_x10    = struct.unpack_from('>h', raw, base + 4)[0]
    anchor      = struct.unpack_from('>i', raw, base + 6)[0]
    smooth_low  = struct.unpack_from('>H', raw, base + 10)[0]
    smooth_high = struct.unpack_from('>H', raw, base + 12)[0]
    mean_diff   = struct.unpack_from('>f', raw, base + 14)[0]
    ntc_ref     = struct.unpack_from('>H', raw, base + 18)[0]
    ntc         = struct.unpack_from('>H', raw, base + 20)[0]
    nuc_decision = raw[base + 22]
    nuc_count    = raw[base + 23]
    nuc_dg       = struct.unpack_from('>h', raw, base + 24)[0]
    nuc_dv       = struct.unpack_from('>h', raw, base + 26)[0]
    nuc_dn       = struct.unpack_from('>h', raw, base + 28)[0]
    boot_ntc     = struct.unpack_from('>H', raw, base + 30)[0]
    boot_delta   = struct.unpack_from('>H', raw, base + 32)[0]
    warmup_comp  = struct.unpack_from('>h', raw, base + 34)[0]
    normal_comp  = struct.unpack_from('>h', raw, base + 36)[0]
    total_comp   = struct.unpack_from('>h', raw, base + 38)[0]
    eff_anchor   = struct.unpack_from('>i', raw, base + 40)[0]
    smooth_vtemp = struct.unpack_from('>H', raw, base + 44)[0]
    center_raw_x10 = struct.unpack_from('>h', raw, base + 46)[0]
    center_roi_x10 = struct.unpack_from('>h', raw, base + 48)[0]
    center_raw_adu = struct.unpack_from('>H', raw, base + 50)[0]
    center_roi_adu = struct.unpack_from('>H', raw, base + 52)[0]
    return {
        'pixels': pixels, 'vtemp': vtemp,
        't_lo_x10': t_lo_x10, 't_hi_x10': t_hi_x10,
        'anchor': anchor, 'smooth_low': smooth_low,
        'smooth_high': smooth_high, 'mean_diff': mean_diff,
        'ntc_ref': ntc_ref, 'ntc': ntc,
        'nuc_decision': nuc_decision, 'nuc_count': nuc_count,
        'nuc_dg': nuc_dg, 'nuc_dv': nuc_dv, 'nuc_dn': nuc_dn,
        'boot_ntc': boot_ntc, 'boot_delta': boot_delta,
        'warmup_comp': warmup_comp, 'normal_comp': normal_comp,
        'total_comp': total_comp, 'eff_anchor': eff_anchor,
        'smooth_vtemp': smooth_vtemp,
        'center_raw_x10': center_raw_x10,
        'center_roi_x10': center_roi_x10,
        'center_raw_adu': center_raw_adu,
        'center_roi_adu': center_roi_adu,
    }


def _raw_frame_plausible(raw):
    if len(raw) != RAW_FRAME_SIZE or raw[:2] != b'\xff\xff':
        return False
    words = np.frombuffer(raw, dtype='>u2', offset=2)
    expected_words = PX_H * RAW_ROW_WORDS + 1
    return words.size == expected_words and not (words > U14_MAX).any()


def sync_and_read_raw_frame(ser, buf=None):
    """Read one existing RAW_U16 frame while retaining surplus serial data."""
    if buf is None:
        buf = bytearray()
    while True:
        if len(buf) < RAW_FRAME_SIZE:
            chunk = ser.read(RAW_FRAME_SIZE - len(buf))
            if not chunk:
                raise TimeoutError("raw frame sync timeout")
            buf.extend(chunk)

        start = buf.find(b'\xff\xff')
        if start < 0:
            if buf and buf[-1] == 0xFF:
                del buf[:-1]
            else:
                buf.clear()
            continue

        if len(buf) - start < RAW_FRAME_SIZE:
            if start > 0:
                del buf[:start]
            continue

        raw = bytes(buf[start:start + RAW_FRAME_SIZE])
        if _raw_frame_plausible(raw):
            del buf[:start + RAW_FRAME_SIZE]
            return raw

        del buf[:start + 2]


def parse_raw_frame(raw):
    """Decode the firmware's 0xAA/0xBB RAW_U16 payload without correction."""
    if not _raw_frame_plausible(raw):
        raise ValueError("invalid RAW_U16 frame")

    words = np.frombuffer(raw, dtype='>u2', offset=2)
    rows = words[:PX_H * RAW_ROW_WORDS].reshape(PX_H, RAW_ROW_WORDS)
    return {
        'pixels': rows[:, :PX_W].astype(np.uint16, copy=True),
        'row_meta': rows[:, PX_W:].astype(np.uint16, copy=True),
        'frame_vtemp': int(words[-1]),
    }


def read_exact(ser, size):
    buf = bytearray()
    while len(buf) < size:
        chunk = ser.read(size - len(buf))
        if not chunk:
            raise TimeoutError(f"read timeout: need {size}, got {len(buf)}")
        buf.extend(chunk)
    return bytes(buf)


def _sync_magic(ser, magic, max_scan=2 * 1024 * 1024):
    buf = bytearray()
    scanned = 0
    while True:
        start = buf.find(magic)
        if start >= 0:
            if start:
                del buf[:start]
            return buf
        if len(buf) > len(magic) - 1:
            drop = len(buf) - (len(magic) - 1)
            scanned += drop
            del buf[:drop]
        if scanned > max_scan:
            raise TimeoutError(f'{magic!r} sync exceeded {max_scan} bytes')
        chunk = ser.read(4096)
        if not chunk:
            raise TimeoutError(f'{magic!r} sync timeout')
        buf.extend(chunk)


def read_static_calibration(ser):
    """Read the non-rebooting 0xED factory-gain packet."""
    buf = _sync_magic(ser, STATIC_CAL_MAGIC)
    while len(buf) < STATIC_CAL_HEADER_SIZE:
        buf.extend(read_exact(ser, STATIC_CAL_HEADER_SIZE - len(buf)))
    header = bytes(buf[:STATIC_CAL_HEADER_SIZE])
    fields = struct.unpack(STATIC_CAL_HEADER_FORMAT, header)
    (magic, version, header_size, width, height, gain_size, gain_crc32,
     mean_diff, t_ref_low, header_crc32) = fields
    if (magic != STATIC_CAL_MAGIC or version != STATIC_CAL_VERSION
            or header_size != STATIC_CAL_HEADER_SIZE
            or width != PX_W or height != PX_H
            or gain_size != GAIN_PAYLOAD_SIZE):
        raise ValueError('静态标定头不兼容')
    if zlib.crc32(header[:-4]) & 0xFFFFFFFF != header_crc32:
        raise ValueError('静态标定头CRC错误')
    payload = bytes(buf[STATIC_CAL_HEADER_SIZE:])
    if len(payload) < gain_size:
        payload += read_exact(ser, gain_size - len(payload))
    payload = payload[:gain_size]
    if zlib.crc32(payload) & 0xFFFFFFFF != gain_crc32:
        raise ValueError('factory gain CRC错误')
    gain = np.frombuffer(payload, dtype='<f4').reshape(PX_H, PX_W).copy()
    if not np.isfinite(gain).all() or (gain <= 0).any():
        raise ValueError('factory gain包含无效值')
    return {
        'gain': gain,
        'gain_crc32': gain_crc32,
        'mean_diff': float(mean_diff),
        't_ref_low': float(t_ref_low),
        'header_crc32': header_crc32,
    }


def build_thermal_v2_header(ntc_adu, global_anchor_adu, residual_maps):
    """Pack the exact 96-byte firmware V2 header and map payloads."""
    ntc = tuple(int(v) for v in ntc_adu)
    anchors = tuple(int(v) for v in global_anchor_adu)
    if len(ntc) != THERMAL_V2_MAP_COUNT:
        raise ValueError('V2必须有4个NTC节点')
    if len(anchors) != THERMAL_V2_MAP_COUNT:
        raise ValueError('V2必须有4个global anchor节点')
    if any(not 8 < value < 4088 for value in ntc):
        raise ValueError('V2 NTC节点越界')
    if any(ntc[i] <= ntc[i + 1]
           for i in range(THERMAL_V2_MAP_COUNT - 1)):
        raise ValueError('V2 NTC节点必须随升温严格下降')
    if any(abs(value) > 600 for value in anchors):
        raise ValueError('V2 global anchor超过±600 ADU')

    map_payloads = []
    map_crc32 = []
    payload_crc32 = 0
    for index, value in enumerate(residual_maps):
        arr = np.ascontiguousarray(value, dtype='<i2')
        if arr.shape != (PX_H, PX_W):
            raise ValueError(f'V2 map {index}形状异常：{arr.shape}')
        if np.max(np.abs(arr.astype(np.int32))) > 2048:
            raise ValueError(f'V2 map {index}超过±2048 ADU')
        if index == 0 and np.any(arr):
            raise ValueError('V2 T0 map必须全零')
        payload = arr.tobytes(order='C')
        crc = zlib.crc32(payload) & 0xFFFFFFFF
        payload_crc32 = zlib.crc32(payload, payload_crc32) & 0xFFFFFFFF
        map_payloads.append(payload)
        map_crc32.append(crc)

    values = (
        THERMAL_V2_MAGIC, THERMAL_V2_VERSION, THERMAL_V2_HEADER_SIZE,
        1, THERMAL_V2_MAP_COUNT * THERMAL_V2_MAP_SIZE,
        PX_W, PX_H, THERMAL_V2_MAP_COUNT, 1, 0, THERMAL_V2_MAP_SIZE,
        *ntc, *anchors, *THERMAL_V2_MAP_OFFSETS, *map_crc32,
        payload_crc32, b'\x00' * 12, 0)
    header = bytearray(struct.pack(THERMAL_V2_HEADER_FORMAT, *values))
    header_crc32 = zlib.crc32(header[:-4]) & 0xFFFFFFFF
    struct.pack_into('<I', header, len(header) - 4, header_crc32)
    return bytes(header), map_payloads, {
        'header_crc32': header_crc32,
        'payload_crc32': payload_crc32,
        'map_crc32': map_crc32,
    }


def read_thermal_v2_write_reply(ser):
    buf = _sync_magic(ser, THERMAL_V2_REPLY_MAGIC, max_scan=256 * 1024)
    while len(buf) < THERMAL_V2_REPLY_SIZE:
        buf.extend(read_exact(ser, THERMAL_V2_REPLY_SIZE - len(buf)))
    fields = struct.unpack(
        THERMAL_V2_REPLY_FORMAT, bytes(buf[:THERMAL_V2_REPLY_SIZE]))
    magic, version, status, detail, payload_crc32, header_crc32 = fields
    if magic != THERMAL_V2_REPLY_MAGIC or version != THERMAL_V2_VERSION:
        raise ValueError('V2写入回复版本不兼容')
    return {
        'status': status,
        'detail': detail,
        'payload_crc32': payload_crc32,
        'header_crc32': header_crc32,
    }


def read_runtime_ffc_map(ser):
    """Find and validate one runtime ram_calibg packet in the UART stream."""
    buf = bytearray()
    scanned = 0
    while True:
        start = buf.find(FFC_MAP_MAGIC)
        if start < 0:
            if len(buf) > len(FFC_MAP_MAGIC) - 1:
                scanned += len(buf) - (len(FFC_MAP_MAGIC) - 1)
                del buf[:-(len(FFC_MAP_MAGIC) - 1)]
            if scanned > 2 * 1024 * 1024:
                raise TimeoutError('FFC map sync exceeded 2 MiB')
            chunk = ser.read(4096)
            if not chunk:
                raise TimeoutError('FFC map sync timeout')
            buf.extend(chunk)
            continue

        if start:
            scanned += start
            del buf[:start]
        while len(buf) < FFC_MAP_HEADER_SIZE:
            chunk = ser.read(FFC_MAP_HEADER_SIZE - len(buf))
            if not chunk:
                raise TimeoutError('FFC map header timeout')
            buf.extend(chunk)

        fields = struct.unpack_from(FFC_MAP_HEADER_FORMAT, buf)
        (magic, version, header_size, width, height, payload_size,
         payload_crc32, temp_anchor, ntc, vtemp, ffc_count,
         last_decision, flags, uptime_ms) = fields
        valid_header = (
            magic == FFC_MAP_MAGIC
            and version == FFC_MAP_VERSION
            and header_size == FFC_MAP_HEADER_SIZE
            and width == PX_W and height == PX_H
            and payload_size == FFC_MAP_PAYLOAD_SIZE)
        if not valid_header:
            del buf[0]
            continue

        packet_size = header_size + payload_size
        while len(buf) < packet_size:
            chunk = ser.read(min(4096, packet_size - len(buf)))
            if not chunk:
                raise TimeoutError(
                    f'FFC map payload timeout: need {packet_size}, got {len(buf)}')
            buf.extend(chunk)
        payload = bytes(buf[header_size:packet_size])
        actual_crc32 = zlib.crc32(payload) & 0xFFFFFFFF
        if actual_crc32 != payload_crc32:
            raise ValueError(
                f'FFC map CRC mismatch: packet={payload_crc32:08x}, '
                f'actual={actual_crc32:08x}')
        return {
            'map': np.frombuffer(payload, dtype='<u2')
                     .reshape(PX_H, PX_W).copy(),
            'version': version,
            'payload_crc32': payload_crc32,
            'temp_anchor': temp_anchor,
            'ntc': ntc,
            'vtemp': vtemp,
            'ffc_count': ffc_count,
            'last_decision': last_decision,
            'flags': flags,
            'runtime_ffc_valid': bool(flags & 0x01),
            'ram_map_active': bool(flags & 0x02),
            'thermal_comp_model': (flags >> 4) & 0x0F,
            'uptime_ms': uptime_ms,
        }


def read_payload_or_error(ser, expected_size):
    """Consume stream until FF FF sync, then parse typed packet."""
    while True:
        b1 = ser.read(1)
        if not b1:
            raise TimeoutError("payload sync timeout (no FF FF within port timeout)")
        if b1 == b'\xff':
            b2 = ser.read(1)
            if b2 == b'\xff':
                break
    t = ser.read(1)
    if not t:
        raise TimeoutError("payload type byte timeout")
    tb = t[0]
    if tb == 0xEE:
        err_code = ser.read(1)[0]
        str_len = ser.read(1)[0]
        msg = read_exact(ser, str_len).decode('ascii', errors='ignore') if str_len else ''
        kind = 'finish' if err_code == 0 else 'error'
        return {'type': kind, 'code': err_code, 'msg': msg}
    if tb == 0xDD:
        payload = read_exact(ser, expected_size) if expected_size > 0 else b''
        return {'type': 'data', 'payload': payload}
    raise RuntimeError(f"unknown packet type 0x{tb:02X}")


def parse_badpts(buf):
    pts = []
    for i in range(0, len(buf), 2):
        y, x = buf[i], buf[i + 1]
        if y != 255 and x != 255:
            pts.append((y, x))
    return pts


def fpa_to_celsius(vtemp):
    """VTEMP ADU -> approximate FPA °C (formula constants are NOT calibrated
    for T-NV160; only relative ΔVTEMP is meaningful)."""
    if vtemp == 0:
        return float('nan')
    return 25.0 + (vtemp - 8192) / 70.0
