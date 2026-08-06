"""Persist synchronized RAW_U16 thermal-state captures for offline models."""
import hashlib
import json
import re
import shutil
import sys
from datetime import datetime
from pathlib import Path

import numpy as np

from .protocol import (PX_H, PX_W, RAW_FRAME_SIZE,
                       build_thermal_v2_header)


_SAFE_LABEL_RE = re.compile(r'[^0-9A-Za-z._-]+')


def thermal_capture_root():
    if getattr(sys, 'frozen', False):
        app_dir = Path(sys.executable).resolve().parent
    else:
        app_dir = Path(__file__).resolve().parent.parent
    return app_dir / 'thermal_state_capture'


def ffc_diagnostic_root():
    if getattr(sys, 'frozen', False):
        app_dir = Path(sys.executable).resolve().parent
    else:
        app_dir = Path(__file__).resolve().parent.parent
    return app_dir / 'ffc_runtime_diagnostic'


def thermal_v2_root():
    if getattr(sys, 'frozen', False):
        app_dir = Path(sys.executable).resolve().parent
    else:
        app_dir = Path(__file__).resolve().parent.parent
    return app_dir / 'thermal_v2_calibration'


def _safe_label(value):
    cleaned = _SAFE_LABEL_RE.sub('_', value.strip()).strip('._-')
    return cleaned or 'unlabeled'


def _array_sha256(arr):
    return hashlib.sha256(arr.tobytes(order='C')).hexdigest()


def _json_telemetry(frame):
    return {
        key: value.item() if isinstance(value, np.generic) else value
        for key, value in frame.items() if key != 'pixels'
    }


def build_thermal_v2_calibration(states, static_calibration):
    """Derive absolute open-path residual maps from four matched FFC/RAW states."""
    if len(states) != 4:
        raise ValueError('Flash V2必须正好包含T0～T3四个状态')
    gain = np.asarray(static_calibration['gain'], dtype=np.float64)
    if gain.shape != (PX_H, PX_W) or not np.isfinite(gain).all():
        raise ValueError('factory gain无效')
    valid = (gain > 0.05) & (gain < 20.0)
    if int(valid.sum()) < PX_W * PX_H - 64:
        raise ValueError('factory gain有效像元不足')

    responses = []
    raw_means = []
    ntc_nodes = []
    anchors = []
    for index, stage in enumerate(states):
        frames = np.asarray(stage['frames'], dtype=np.float64)
        ffc_map = np.asarray(stage['map_packet']['map'], dtype=np.float64)
        if frames.ndim != 3 or frames.shape[1:] != (PX_H, PX_W):
            raise ValueError(f'T{index} RAW形状异常：{frames.shape}')
        if ffc_map.shape != (PX_H, PX_W):
            raise ValueError(f'T{index} FFC map形状异常：{ffc_map.shape}')
        raw_mean = frames.mean(axis=0)
        response = gain * (raw_mean - ffc_map) + float(ffc_map.mean())
        raw_means.append(raw_mean)
        responses.append(response)
        ntc_nodes.append(int(stage['map_packet']['ntc']))
        anchors.append(int(stage['map_packet']['temp_anchor']))

    if any(ntc_nodes[i] <= ntc_nodes[i + 1] for i in range(3)):
        raise ValueError(f'NTC节点未随升温严格下降：{ntc_nodes}')

    base = responses[0]
    base_anchor = anchors[0]
    residual_maps = []
    global_anchor = []
    state_metrics = []
    for index, response in enumerate(responses):
        if index == 0:
            residual = np.zeros((PX_H, PX_W), dtype='<i2')
            global_before = 0.0
            global_after = 0.0
            spatial_before = 0.0
            spatial_after = 0.0
        else:
            delta = response - base
            global_before = float(np.median(delta[valid]))
            spatial = delta - global_before
            residual_f = np.zeros_like(spatial)
            residual_f[valid] = spatial[valid] / gain[valid]
            residual = np.rint(residual_f).astype(np.int32)
            if int(np.max(np.abs(residual))) > 2048:
                raise ValueError(
                    f'T{index}逐像素残差超过±2048 ADU，拒绝写入')
            residual = np.ascontiguousarray(residual, dtype='<i2')
            corrected_delta = delta - gain * residual.astype(np.float64)
            global_after = float(np.median(corrected_delta[valid]))
            spatial_before = float(np.percentile(
                np.abs(spatial[valid]), 98))
            spatial_after = float(np.percentile(
                np.abs(corrected_delta[valid] - global_after), 98))
        anchor_delta = anchors[index] - base_anchor
        comp = int(round(global_after - anchor_delta))
        if abs(comp) > 600:
            raise ValueError(
                f'T{index} global anchor {comp} ADU超过±600，拒绝写入')
        residual_maps.append(residual)
        global_anchor.append(comp)
        state_metrics.append({
            'state': f'T{index}',
            'ntc_adu': ntc_nodes[index],
            'temp_anchor_adu': anchors[index],
            'response_global_delta_adu': global_before,
            'post_spatial_global_delta_adu': global_after,
            'global_anchor_comp_adu': comp,
            'spatial_residual_p98_before_adu': spatial_before,
            'spatial_residual_p98_after_adu': spatial_after,
            'raw_mean_adu': float(raw_means[index].mean()),
        })

    header, payloads, crc = build_thermal_v2_header(
        ntc_nodes, global_anchor, residual_maps)
    return {
        'header': header,
        'map_payloads': payloads,
        'residual_maps': np.stack(residual_maps),
        'ntc_adu': ntc_nodes,
        'global_anchor_adu': global_anchor,
        'state_metrics': state_metrics,
        'crc': crc,
    }


def save_thermal_capture(result, *, device_label, startup_path, state_label,
                         scene_label, scene_temp_c, port, control_baud,
                         frame_count, output_root=None, now=None):
    if not device_label.strip():
        raise ValueError('设备标签不能为空')
    if not startup_path.strip():
        raise ValueError('启动路径不能为空')
    if not state_label.strip():
        raise ValueError('热状态不能为空')
    if not scene_label.strip():
        raise ValueError('场景标签不能为空')

    frames = np.ascontiguousarray(result['frames'], dtype='<u2')
    row_meta = np.ascontiguousarray(result['row_meta'], dtype='<u2')
    frame_vtemp = np.ascontiguousarray(result['frame_vtemp'], dtype='<u2')
    expected_frames = (int(frame_count), PX_H, PX_W)
    expected_meta = (int(frame_count), PX_H, 2)
    if frames.shape != expected_frames:
        raise ValueError(f'原始帧形状异常：{frames.shape}，预期 {expected_frames}')
    if row_meta.shape != expected_meta:
        raise ValueError(f'行元数据形状异常：{row_meta.shape}，预期 {expected_meta}')
    if frame_vtemp.shape != (int(frame_count),):
        raise ValueError(f'VTEMP形状异常：{frame_vtemp.shape}')

    raw_mean = frames.mean(axis=0, dtype=np.float64).astype('<f4')
    raw_std = frames.std(axis=0, dtype=np.float64).astype('<f4')
    root = Path(output_root) if output_root is not None else thermal_capture_root()
    root.mkdir(parents=True, exist_ok=True)
    captured_at = now or datetime.now().astimezone()
    stamp = captured_at.strftime('%Y%m%d_%H%M%S_%f')
    parts = (_safe_label(device_label), _safe_label(startup_path),
             _safe_label(state_label), _safe_label(scene_label))
    folder_name = (
        f'thermal_{stamp}_{parts[0]}_{parts[1]}_{parts[2]}_{parts[3]}')
    output_dir = root / folder_name
    staging_dir = root / f'.{folder_name}.tmp'
    staging_dir.mkdir(exist_ok=False)

    archive_path = staging_dir / 'raw_capture.npz'
    try:
        with archive_path.open('wb') as stream:
            np.savez_compressed(stream, raw_frames=frames,
                                raw_mean=raw_mean, raw_std=raw_std,
                                row_meta=row_meta,
                                frame_vtemp=frame_vtemp)
        archive_sha256 = hashlib.sha256(archive_path.read_bytes()).hexdigest()
        pre = _json_telemetry(result['pre'])
        post = _json_telemetry(result['post'])
        manifest = {
            'schema': 'tn160.thermal-state-raw-capture.v1',
            'captured_at': captured_at.isoformat(timespec='milliseconds'),
            'device_label': device_label.strip(),
            'startup_path': startup_path.strip(),
            'thermal_state': state_label.strip(),
            'scene': {
                'label': scene_label.strip(),
                'temperature_c': scene_temp_c,
            },
            'serial': {
                'port': port,
                'control_baud': int(control_baud),
                'raw_command': '0xAA',
                'raw_baud': 2000000,
                'monitor_restore_command': '0x11',
            },
            'capture': {
                'frame_count': int(frame_count),
                'raw_frame_bytes': RAW_FRAME_SIZE,
                'duration_s': float(result['duration_s']),
                'pre_telemetry': pre,
                'post_telemetry': post,
            },
            'archive': {
                'file': archive_path.name,
                'sha256': archive_sha256,
            },
            'arrays': {
                'raw_frames': {
                    'shape': list(frames.shape),
                    'dtype': str(frames.dtype),
                    'sha256': _array_sha256(frames),
                },
                'raw_mean': {
                    'shape': list(raw_mean.shape),
                    'dtype': str(raw_mean.dtype),
                    'sha256': _array_sha256(raw_mean),
                },
                'raw_std': {
                    'shape': list(raw_std.shape),
                    'dtype': str(raw_std.dtype),
                    'sha256': _array_sha256(raw_std),
                },
                'row_meta': {
                    'shape': list(row_meta.shape),
                    'dtype': str(row_meta.dtype),
                    'sha256': _array_sha256(row_meta),
                },
                'frame_vtemp': {
                    'shape': list(frame_vtemp.shape),
                    'dtype': str(frame_vtemp.dtype),
                    'sha256': _array_sha256(frame_vtemp),
                },
            },
            'statistics': {
                'raw_mean_adu': float(raw_mean.mean()),
                'raw_spatial_std_adu': float(raw_mean.std()),
                'temporal_std_mean_adu': float(raw_std.mean()),
                'temporal_std_p98_adu': float(np.percentile(raw_std, 98)),
                'frame_vtemp_min_adu': int(frame_vtemp.min()),
                'frame_vtemp_max_adu': int(frame_vtemp.max()),
                'pre_post_ntc_delta_adu': int(post.get('ntc', 0))
                                              - int(pre.get('ntc', 0)),
            },
        }
        (staging_dir / 'manifest.json').write_text(
            json.dumps(manifest, ensure_ascii=False, indent=2) + '\n',
            encoding='utf-8')
        staging_dir.replace(output_dir)
    except Exception:
        shutil.rmtree(staging_dir, ignore_errors=True)
        raise

    return output_dir, archive_sha256


def _packet_metadata(packet):
    return {
        key: (value.item() if isinstance(value, np.generic) else value)
        for key, value in packet.items() if key != 'map'
    }


def save_ffc_runtime_diagnostic(result, *, device_label, scene_label,
                                target_temp_c, port, output_root=None,
                                now=None):
    if not device_label.strip():
        raise ValueError('设备标签不能为空')
    if not scene_label.strip():
        raise ValueError('场景标签不能为空')

    arrays = {}
    stages = {}
    for name in ('early', 'stable'):
        stage = result[name]
        ffc_map = np.ascontiguousarray(stage['map_packet']['map'], dtype='<u2')
        frames = np.ascontiguousarray(stage['frames'], dtype='<u2')
        row_meta = np.ascontiguousarray(stage['row_meta'], dtype='<u2')
        frame_vtemp = np.ascontiguousarray(stage['frame_vtemp'], dtype='<u2')
        if ffc_map.shape != (PX_H, PX_W):
            raise ValueError(f'{name} FFC map形状异常：{ffc_map.shape}')
        if frames.ndim != 3 or frames.shape[1:] != (PX_H, PX_W):
            raise ValueError(f'{name} RAW形状异常：{frames.shape}')
        if row_meta.shape != (frames.shape[0], PX_H, 2):
            raise ValueError(f'{name} 行元数据形状异常：{row_meta.shape}')
        if frame_vtemp.shape != (frames.shape[0],):
            raise ValueError(f'{name} VTEMP形状异常：{frame_vtemp.shape}')

        raw_mean = frames.mean(axis=0, dtype=np.float64).astype('<f4')
        corrected = (raw_mean.astype(np.float64) - ffc_map
                     + float(ffc_map.mean())).astype('<f4')
        arrays[f'{name}_ffc_map'] = ffc_map
        arrays[f'{name}_raw_frames'] = frames
        arrays[f'{name}_raw_mean'] = raw_mean
        arrays[f'{name}_corrected_mean'] = corrected
        arrays[f'{name}_row_meta'] = row_meta
        arrays[f'{name}_frame_vtemp'] = frame_vtemp
        stages[name] = {
            'map_packet': _packet_metadata(stage['map_packet']),
            'pre_telemetry': _json_telemetry(stage['pre']),
            'post_telemetry': _json_telemetry(stage['post']),
            'raw_duration_s': float(stage['duration_s']),
        }

    delta = (arrays['stable_corrected_mean'].astype(np.float64)
             - arrays['early_corrected_mean'].astype(np.float64))
    global_delta = float(np.median(delta))
    spatial_residual = np.ascontiguousarray(delta - global_delta, dtype='<f4')
    arrays['corrected_spatial_residual'] = spatial_residual
    spatial_p98 = float(np.percentile(np.abs(spatial_residual), 98))
    if spatial_p98 <= 10.0:
        decision = 'global_state_model'
        decision_cn = '运行期FFC已压住空间项：采用每机全局热状态模型'
    elif spatial_p98 > 17.0:
        decision = 'four_state_pixel_nuc_v2'
        decision_cn = '空间项仍显著：采用自动四热状态逐像素NUC V2'
    else:
        decision = 'inconclusive'
        decision_cn = '空间残差落在灰区：本轮不能可靠二选一'

    root = Path(output_root) if output_root is not None else ffc_diagnostic_root()
    root.mkdir(parents=True, exist_ok=True)
    captured_at = now or datetime.now().astimezone()
    stamp = captured_at.strftime('%Y%m%d_%H%M%S_%f')
    target_label = 'unknown' if target_temp_c is None else f'{target_temp_c:g}C'
    folder_name = (f'ffc_diag_{stamp}_{_safe_label(device_label)}_'
                   f'{_safe_label(target_label)}')
    output_dir = root / folder_name
    staging_dir = root / f'.{folder_name}.tmp'
    staging_dir.mkdir(exist_ok=False)
    archive_path = staging_dir / 'ffc_runtime_pairs.npz'
    try:
        with archive_path.open('wb') as stream:
            np.savez_compressed(stream, **arrays)
        archive_sha256 = hashlib.sha256(archive_path.read_bytes()).hexdigest()
        manifest = {
            'schema': 'tn160.ffc-runtime-diagnostic.v1',
            'captured_at': captured_at.isoformat(timespec='milliseconds'),
            'device_label': device_label.strip(),
            'scene': {
                'label': scene_label.strip(),
                'target_temperature_c': target_temp_c,
            },
            'serial': {
                'port': port,
                'baud': 2000000,
                'ffc_map_command': '0xEE',
                'raw_command': '0xAA',
                'monitor_restore_command': '0x11',
            },
            'capture': {
                'frame_count_per_stage': int(result['frame_count']),
                'stable_uptime_target_ms': int(
                    result['stable_uptime_target_ms']),
                'stages': stages,
            },
            'archive': {
                'file': archive_path.name,
                'sha256': archive_sha256,
            },
            'arrays': {
                key: {
                    'shape': list(value.shape),
                    'dtype': str(value.dtype),
                    'sha256': _array_sha256(value),
                } for key, value in arrays.items()
            },
            'analysis': {
                'method': ('per-stage raw_mean - runtime_ffc_map + map_mean; '
                           'then stable - early and remove median'),
                'corrected_global_delta_adu': global_delta,
                'corrected_spatial_residual_abs_p98_adu': spatial_p98,
                'threshold_global_model_max_adu': 10.0,
                'threshold_pixel_nuc_min_adu_exclusive': 17.0,
                'decision': decision,
                'decision_cn': decision_cn,
            },
        }
        (staging_dir / 'manifest.json').write_text(
            json.dumps(manifest, ensure_ascii=False, indent=2) + '\n',
            encoding='utf-8')
        staging_dir.replace(output_dir)
    except Exception:
        shutil.rmtree(staging_dir, ignore_errors=True)
        raise
    return output_dir, manifest['analysis'], archive_sha256


def save_thermal_v2_calibration(result, *, device_label, scene_label,
                                scene_temp_c, port, output_root=None,
                                now=None):
    if not device_label.strip():
        raise ValueError('设备标签不能为空')
    if not scene_label.strip():
        raise ValueError('场景标签不能为空')
    states = result['states']
    package = result['package']
    arrays = {
        'factory_gain': np.ascontiguousarray(
            result['static_calibration']['gain'], dtype='<f4'),
        'v2_residual_maps': np.ascontiguousarray(
            package['residual_maps'], dtype='<i2'),
    }
    stages = []
    for index, stage in enumerate(states):
        prefix = f't{index}'
        arrays[f'{prefix}_ffc_map'] = np.ascontiguousarray(
            stage['map_packet']['map'], dtype='<u2')
        arrays[f'{prefix}_raw_frames'] = np.ascontiguousarray(
            stage['frames'], dtype='<u2')
        arrays[f'{prefix}_row_meta'] = np.ascontiguousarray(
            stage['row_meta'], dtype='<u2')
        arrays[f'{prefix}_frame_vtemp'] = np.ascontiguousarray(
            stage['frame_vtemp'], dtype='<u2')
        stages.append({
            'state': f'T{index}',
            'map_packet': _packet_metadata(stage['map_packet']),
            'pre_telemetry': _json_telemetry(stage['pre']),
            'post_telemetry': _json_telemetry(stage['post']),
            'raw_duration_s': float(stage['duration_s']),
        })

    root = Path(output_root) if output_root is not None else thermal_v2_root()
    root.mkdir(parents=True, exist_ok=True)
    captured_at = now or datetime.now().astimezone()
    stamp = captured_at.strftime('%Y%m%d_%H%M%S_%f')
    folder_name = (f'thermal_v2_{stamp}_{_safe_label(device_label)}_'
                   f'{_safe_label(scene_label)}')
    output_dir = root / folder_name
    staging_dir = root / f'.{folder_name}.tmp'
    staging_dir.mkdir(exist_ok=False)
    archive_path = staging_dir / 'thermal_v2_capture.npz'
    header_path = staging_dir / 'thermal_v2_header.bin'
    try:
        with archive_path.open('wb') as stream:
            np.savez_compressed(stream, **arrays)
        header_path.write_bytes(package['header'])
        archive_sha256 = hashlib.sha256(archive_path.read_bytes()).hexdigest()
        header_sha256 = hashlib.sha256(package['header']).hexdigest()
        metrics = package['state_metrics']
        analysis = {
            'max_spatial_p98_before_adu': max(
                float(item['spatial_residual_p98_before_adu'])
                for item in metrics),
            'max_spatial_p98_after_adu': max(
                float(item['spatial_residual_p98_after_adu'])
                for item in metrics),
            'flash_write_ok': bool(result['flash_write']['ok']),
            'elapsed_to_flash_s': float(result['elapsed_to_flash_s']),
            'within_360s': float(result['elapsed_to_flash_s']) <= 360.0,
        }
        manifest = {
            'schema': 'tn160.thermal-v2-calibration.v1',
            'captured_at': captured_at.isoformat(timespec='milliseconds'),
            'device_label': device_label.strip(),
            'scene': {
                'label': scene_label.strip(),
                'temperature_c': scene_temp_c,
                'requirement': 'fixed high-emissivity flat field',
            },
            'serial': {
                'port': port,
                'baud': 2000000,
                'static_cal_command': '0xED',
                'ffc_map_command': '0xEE',
                'raw_command': '0xAA',
                'v2_write_command': '0xEF',
            },
            'capture': {
                'target_uptime_ms': list(result['target_uptime_ms']),
                'frame_count_per_state': int(result['frame_count']),
                'stages': stages,
            },
            'v2': {
                'model': 'absolute_open_path_residual',
                'ntc_adu': list(package['ntc_adu']),
                'global_anchor_adu': list(package['global_anchor_adu']),
                'state_metrics': metrics,
                'crc': package['crc'],
                'header_file': header_path.name,
                'header_sha256': header_sha256,
                'flash_write': result['flash_write'],
            },
            'archive': {
                'file': archive_path.name,
                'sha256': archive_sha256,
            },
            'arrays': {
                key: {
                    'shape': list(value.shape),
                    'dtype': str(value.dtype),
                    'sha256': _array_sha256(value),
                } for key, value in arrays.items()
            },
            'analysis': analysis,
        }
        (staging_dir / 'manifest.json').write_text(
            json.dumps(manifest, ensure_ascii=False, indent=2) + '\n',
            encoding='utf-8')
        staging_dir.replace(output_dir)
    except Exception:
        shutil.rmtree(staging_dir, ignore_errors=True)
        raise
    return output_dir, analysis, archive_sha256
