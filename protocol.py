"""Shared serial-protocol parsing for monitor frames and calibration dump."""
import struct
import numpy as np

FRAME_SIZE = 19223
PX_W, PX_H = 160, 120
INT16_MAX = 0x7FFF
TEMP_MIN_X10 = -1000
TEMP_MAX_X10 = 3200

IMG_PAYLOAD_SIZE = PX_W * PX_H * 2
GAIN_PAYLOAD_SIZE = PX_W * PX_H * 4
MAX_BADPT = 5
BADPT_PAYLOAD_SIZE = MAX_BADPT * 2


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
    if temp_pair == (INT16_MAX, INT16_MAX):
        return True

    if not (TEMP_MIN_X10 <= t_lo_x10 <= TEMP_MAX_X10):
        return False
    if not (TEMP_MIN_X10 <= t_hi_x10 <= TEMP_MAX_X10):
        return False
    if t_hi_x10 < t_lo_x10:
        return False

    return True


def sync_and_read_frame(ser):
    buf = bytearray()
    while True:
        if len(buf) < FRAME_SIZE:
            chunk = ser.read(max(FRAME_SIZE - len(buf), 1024))
            if not chunk:
                raise TimeoutError("monitor frame sync timeout")
            buf.extend(chunk)

        start = buf.find(b'\xff')
        if start < 0:
            if len(buf) > FRAME_SIZE:
                del buf[:-FRAME_SIZE]
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
    return {
        'pixels': pixels, 'vtemp': vtemp,
        't_lo_x10': t_lo_x10, 't_hi_x10': t_hi_x10,
        'anchor': anchor, 'smooth_low': smooth_low,
        'smooth_high': smooth_high, 'mean_diff': mean_diff,
        'ntc_ref': ntc_ref, 'baseline': 0, 'ntc': ntc,
    }


def read_exact(ser, size):
    buf = bytearray()
    while len(buf) < size:
        chunk = ser.read(size - len(buf))
        if not chunk:
            raise TimeoutError(f"read timeout: need {size}, got {len(buf)}")
        buf.extend(chunk)
    return bytes(buf)


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
