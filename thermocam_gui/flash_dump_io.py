"""Persist read-only 0xDD calibration payloads without losing raw values."""
import hashlib
import json
import re
import shutil
import sys
from datetime import datetime
from pathlib import Path

import numpy as np

from .protocol import (BADPT_PAYLOAD_SIZE, GAIN_PAYLOAD_SIZE,
                       IMG_PAYLOAD_SIZE, PX_H, PX_W)


_SAFE_LABEL_RE = re.compile(r'[^0-9A-Za-z._-]+')


def flash_dump_root():
    """Return the app-local calibration backup directory."""
    if getattr(sys, 'frozen', False):
        app_dir = Path(sys.executable).resolve().parent
    else:
        app_dir = Path(__file__).resolve().parent.parent
    return app_dir / 'cali_data_backup'


def _array_sha256(arr):
    return hashlib.sha256(arr.tobytes(order='C')).hexdigest()


def _safe_label(label):
    value = _SAFE_LABEL_RE.sub('_', label.strip()).strip('._-')
    return value or 'unlabeled'


def save_flash_dump(img_bg, gain, badpts, *, device_label, port,
                    trigger_baud, read_baud, output_root=None, now=None):
    """Validate and atomically save one complete 0xDD calibration dump."""
    if not device_label.strip():
        raise ValueError('设备标签不能为空')

    img_bg = np.ascontiguousarray(img_bg, dtype='<u2')
    gain = np.ascontiguousarray(gain, dtype='<f4')
    badpts = np.asarray(badpts, dtype=np.uint8)
    if badpts.size == 0:
        badpts = np.empty((0, 2), dtype=np.uint8)
    else:
        badpts = np.ascontiguousarray(badpts.reshape(-1, 2))

    expected_shape = (PX_H, PX_W)
    if img_bg.shape != expected_shape:
        raise ValueError(f'img_bg 形状异常：{img_bg.shape}，预期 {expected_shape}')
    if gain.shape != expected_shape:
        raise ValueError(f'gain 形状异常：{gain.shape}，预期 {expected_shape}')
    if not np.isfinite(gain).all():
        raise ValueError('gain 包含 NaN 或 Inf')
    if badpts.shape[0] > 5:
        raise ValueError(f'坏点数量异常：{badpts.shape[0]}，上限 5')
    if badpts.size and ((badpts[:, 0] >= PX_H).any()
                        or (badpts[:, 1] >= PX_W).any()):
        raise ValueError('坏点坐标超出 160×120 范围')

    root = Path(output_root) if output_root is not None else flash_dump_root()
    root.mkdir(parents=True, exist_ok=True)
    captured_at = now or datetime.now().astimezone()
    stamp = captured_at.strftime('%Y%m%d_%H%M%S_%f')
    folder_name = f'flash_{stamp}_{_safe_label(device_label)}'
    output_dir = root / folder_name
    staging_dir = root / f'.{folder_name}.tmp'
    staging_dir.mkdir(exist_ok=False)

    archive_path = staging_dir / 'calibration_data.npz'
    try:
        with archive_path.open('wb') as stream:
            np.savez_compressed(stream, img_bg=img_bg, gain=gain,
                                badpts=badpts)
        archive_sha256 = hashlib.sha256(archive_path.read_bytes()).hexdigest()

        manifest = {
            'schema': 'tn160.flash-calibration-dump.v1',
            'captured_at': captured_at.isoformat(timespec='milliseconds'),
            'device_label': device_label.strip(),
            'serial': {
                'port': port,
                'trigger_baud': int(trigger_baud),
                'read_baud': int(read_baud),
            },
            'protocol': {
                'command': '0xDD',
                'payload_bytes': (IMG_PAYLOAD_SIZE + GAIN_PAYLOAD_SIZE
                                  + BADPT_PAYLOAD_SIZE),
            },
            'archive': {
                'file': archive_path.name,
                'sha256': archive_sha256,
            },
            'arrays': {
                'img_bg': {
                    'shape': list(img_bg.shape),
                    'dtype': str(img_bg.dtype),
                    'sha256': _array_sha256(img_bg),
                },
                'gain': {
                    'shape': list(gain.shape),
                    'dtype': str(gain.dtype),
                    'sha256': _array_sha256(gain),
                },
                'badpts': {
                    'shape': list(badpts.shape),
                    'dtype': str(badpts.dtype),
                    'sha256': _array_sha256(badpts),
                    'coordinates_yx': badpts.tolist(),
                },
            },
            'statistics': {
                'img_bg_mean': float(img_bg.mean()),
                'img_bg_min': int(img_bg.min()),
                'img_bg_max': int(img_bg.max()),
                'gain_mean': float(gain.mean()),
                'gain_std': float(gain.std()),
                'gain_p2': float(np.percentile(gain, 2)),
                'gain_p98': float(np.percentile(gain, 98)),
                'badpt_count': int(badpts.shape[0]),
            },
        }
        manifest_path = staging_dir / 'manifest.json'
        manifest_path.write_text(
            json.dumps(manifest, ensure_ascii=False, indent=2) + '\n',
            encoding='utf-8')
        staging_dir.replace(output_dir)
    except Exception:
        shutil.rmtree(staging_dir, ignore_errors=True)
        raise

    return output_dir, archive_sha256
