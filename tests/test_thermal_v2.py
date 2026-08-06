import struct
import sys
import unittest
import zlib
from pathlib import Path

import numpy as np


APP_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(APP_ROOT))

from thermocam_gui.protocol import (  # noqa: E402
    PX_H,
    PX_W,
    THERMAL_V2_HEADER_FORMAT,
    THERMAL_V2_HEADER_SIZE,
    THERMAL_V2_MAP_OFFSETS,
    THERMAL_V2_MAP_SIZE,
    THERMAL_V2_REPLY_FORMAT,
    build_thermal_v2_header,
)
from thermocam_gui.thermal_capture_io import (  # noqa: E402
    build_thermal_v2_calibration,
)

try:
    from thermocam_gui.workers import ThermalV2CalibrationWorker  # noqa: E402
except ModuleNotFoundError as exc:
    if exc.name != 'PySide6':
        raise
    ThermalV2CalibrationWorker = None


class ThermalV2HeaderTests(unittest.TestCase):
    def test_exact_header_layout_and_crc(self):
        maps = [np.zeros((PX_H, PX_W), dtype=np.int16) for _ in range(4)]
        maps[1].fill(1)
        maps[2].fill(-2)
        maps[3].fill(3)
        header, payloads, crc = build_thermal_v2_header(
            [2000, 1900, 1800, 1700], [0, 10, 20, 30], maps)

        self.assertEqual(struct.calcsize(THERMAL_V2_HEADER_FORMAT), 96)
        self.assertEqual(THERMAL_V2_HEADER_SIZE, 96)
        self.assertEqual(len(header), 96)
        self.assertTrue(all(len(payload) == THERMAL_V2_MAP_SIZE
                            for payload in payloads))
        fields = struct.unpack(THERMAL_V2_HEADER_FORMAT, header)
        self.assertEqual(fields[0], b'TNV2')
        self.assertEqual(fields[1:3], (2, 96))
        self.assertEqual(fields[3:5], (1, 4 * THERMAL_V2_MAP_SIZE))
        self.assertEqual(fields[5:11], (PX_W, PX_H, 4, 1, 0,
                                        THERMAL_V2_MAP_SIZE))
        self.assertEqual(fields[11:15], (2000, 1900, 1800, 1700))
        self.assertEqual(fields[15:19], (0, 10, 20, 30))
        self.assertEqual(fields[19:23], THERMAL_V2_MAP_OFFSETS)
        self.assertEqual(fields[23:27], tuple(crc['map_crc32']))
        self.assertEqual(fields[27], crc['payload_crc32'])
        self.assertEqual(fields[-1], crc['header_crc32'])
        self.assertEqual(zlib.crc32(header[:-4]) & 0xFFFFFFFF,
                         crc['header_crc32'])

    def test_semantic_limits_are_rejected(self):
        maps = [np.zeros((PX_H, PX_W), dtype=np.int16) for _ in range(4)]
        with self.assertRaisesRegex(ValueError, '严格下降'):
            build_thermal_v2_header(
                [2000, 1900, 1900, 1700], [0, 0, 0, 0], maps)
        with self.assertRaisesRegex(ValueError, 'global anchor'):
            build_thermal_v2_header(
                [2000, 1900, 1800, 1700], [0, 0, 0, 601], maps)
        maps[0][0, 0] = 1
        with self.assertRaisesRegex(ValueError, 'T0 map'):
            build_thermal_v2_header(
                [2000, 1900, 1800, 1700], [0, 0, 0, 0], maps)
        maps[0][0, 0] = 0
        maps[3][0, 0] = 2049
        with self.assertRaisesRegex(ValueError, '2048'):
            build_thermal_v2_header(
                [2000, 1900, 1800, 1700], [0, 0, 0, 0], maps)


class ThermalV2CalibrationTests(unittest.TestCase):
    def _synthetic_states(self):
        yy, xx = np.indices((PX_H, PX_W))
        gain = 0.8 + 0.4 * xx / (PX_W - 1)
        base_response = 7000.0 + 0.1 * xx + 0.05 * yy
        ffc_map = 6000.0 + 0.02 * xx - 0.03 * yy
        spatial_unit = np.rint(
            20.0 * np.sin(xx / 11.0) + 12.0 * np.cos(yy / 9.0))
        global_delta = (0.0, 18.0, 39.0, 63.0)
        anchor = (1000, 1004, 1009, 1015)
        ntc = (2000, 1900, 1800, 1700)
        states = []
        for index in range(4):
            residual = index * spatial_unit
            response = (base_response + global_delta[index]
                        + gain * residual)
            raw = ffc_map + (response - ffc_map.mean()) / gain
            states.append({
                'frames': np.repeat(raw[None, :, :], 8, axis=0),
                'map_packet': {
                    'map': ffc_map,
                    'ntc': ntc[index],
                    'temp_anchor': anchor[index],
                },
            })
        return states, {'gain': gain}

    def test_absolute_residual_closes_synthetic_spatial_error(self):
        states, static_calibration = self._synthetic_states()
        package = build_thermal_v2_calibration(states, static_calibration)

        self.assertFalse(package['residual_maps'][0].any())
        before = [item['spatial_residual_p98_before_adu']
                  for item in package['state_metrics'][1:]]
        after = [item['spatial_residual_p98_after_adu']
                 for item in package['state_metrics'][1:]]
        self.assertGreater(min(before), 10.0)
        self.assertLess(max(after), 0.7)
        # The synthetic spatial wave is not median-zero after gain scaling;
        # its median therefore belongs in the global term by definition.
        self.assertEqual(package['global_anchor_adu'], [0, 17, 37, 58])

    def test_non_monotonic_ntc_is_rejected(self):
        states, static_calibration = self._synthetic_states()
        states[2]['map_packet']['ntc'] = 1950
        with self.assertRaisesRegex(ValueError, '严格下降'):
            build_thermal_v2_calibration(states, static_calibration)


class _FakeV2Serial:
    def __init__(self, package, fail_first_map=False):
        self.package = package
        self.fail_first_map = fail_first_map
        self.rx = bytearray()
        self.writes = []
        self.map_count = 0

    def _reply(self, status, detail):
        self.rx.extend(struct.pack(
            THERMAL_V2_REPLY_FORMAT,
            b'TNVW', 2, status, detail,
            self.package['crc']['payload_crc32'],
            self.package['crc']['header_crc32']))

    def reset_input_buffer(self):
        pass

    def flush(self):
        pass

    def write(self, data):
        payload = bytes(data)
        self.writes.append(payload)
        if payload == b'\xEF':
            self._reply(0x7F, 0)
        elif len(payload) == 96:
            self._reply(0x82 if self.fail_first_map else 0x10, 0)
        elif len(payload) == THERMAL_V2_MAP_SIZE:
            self.map_count += 1
            if self.map_count < 4:
                self._reply(0x10 + self.map_count, self.map_count)
            else:
                self._reply(0x00, 4)
        return len(payload)

    def read(self, size):
        if not self.rx:
            return b''
        count = min(int(size), len(self.rx))
        result = bytes(self.rx[:count])
        del self.rx[:count]
        return result


@unittest.skipUnless(ThermalV2CalibrationWorker is not None,
                     'PySide6 is only installed in the Windows build venv')
class ThermalV2WorkerHandshakeTests(unittest.TestCase):
    def setUp(self):
        maps = [np.zeros((PX_H, PX_W), dtype=np.int16) for _ in range(4)]
        header, payloads, crc = build_thermal_v2_header(
            [2000, 1900, 1800, 1700], [0, 10, 20, 30], maps)
        self.package = {
            'header': header,
            'map_payloads': payloads,
            'crc': crc,
        }
        self.worker = ThermalV2CalibrationWorker('COM_TEST')

    def test_full_handshake_order_and_crc_echo(self):
        serial_port = _FakeV2Serial(self.package)
        result = self.worker._write_v2(serial_port, self.package)

        self.assertTrue(result['ok'])
        self.assertEqual(len(result['replies']), 6)
        self.assertEqual(
            [reply['status'] for reply in result['replies']],
            [0x7F, 0x10, 0x11, 0x12, 0x13, 0x00])
        self.assertEqual(serial_port.writes,
                         [b'\xEF', self.package['header'],
                          *self.package['map_payloads']])

    def test_error_reply_stops_before_map_payload(self):
        serial_port = _FakeV2Serial(self.package, fail_first_map=True)
        result = self.worker._write_v2(serial_port, self.package)

        self.assertFalse(result['ok'])
        self.assertIn('status=0x82', result['error'])
        self.assertEqual(serial_port.writes,
                         [b'\xEF', self.package['header']])


if __name__ == '__main__':
    unittest.main()
