"""Capture synchronized T0–T3 RAW_U16 frame stacks without firmware changes."""
import time

import numpy as np
from PySide6.QtCore import Slot
from PySide6.QtWidgets import (QComboBox, QFrame, QHBoxLayout, QLabel,
                               QLineEdit, QPlainTextEdit, QPushButton,
                               QSpinBox, QVBoxLayout, QWidget)
from matplotlib.backends.backend_qtagg import FigureCanvasQTAgg as FigureCanvas
from matplotlib.figure import Figure

from .port_utils import list_serial_ports, probe_active_port
from .thermal_capture_io import (save_thermal_capture,
                                 save_ffc_runtime_diagnostic,
                                 save_thermal_v2_calibration)
from .ui_style import (AXIS_FG, CARD_EDGE, SUBTITLE_FG, empty_placeholder,
                       kv_block, set_button_kind, style_card, style_figure,
                       style_summary_card)
from .workers import (RawCaptureWorker, FfcRuntimeDiagnosticWorker,
                      ThermalV2CalibrationWorker)


class ThermalCaptureTab(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.worker = None
        self._capture_context = None
        self._diag_context = None
        self._v2_context = None

        root = QVBoxLayout(self)
        root.setContentsMargins(18, 16, 18, 16)
        root.setSpacing(10)

        command_bar = QFrame()
        command_bar.setObjectName('commandBar')
        ctrl = QHBoxLayout(command_bar)
        ctrl.setContentsMargins(12, 8, 12, 8)
        ctrl.setSpacing(8)
        self.port_cb = QComboBox(); self.port_cb.setEditable(True)
        self.port_cb.setMinimumWidth(150)
        self.refresh_btn = QPushButton('1.扫描串口')
        self.device_edit = QLineEdit('v3'); self.device_edit.setPlaceholderText('v3')
        self.device_edit.setMaximumWidth(80)
        self.path_cb = QComboBox()
        self.path_cb.addItem('冷启动', 'cold')
        self.path_cb.addItem('热机 RST', 'hot_rst')
        self.state_cb = QComboBox(); self.state_cb.addItems(['T0', 'T1', 'T2', 'T3'])
        self.scene_edit = QLineEdit('room_fixed'); self.scene_edit.setPlaceholderText('room / bb50')
        self.scene_edit.setMaximumWidth(110)
        self.temp_edit = QLineEdit('28'); self.temp_edit.setPlaceholderText('可空')
        self.temp_edit.setMaximumWidth(70)
        self.frame_spin = QSpinBox(); self.frame_spin.setRange(8, 64)
        self.frame_spin.setValue(32); self.frame_spin.setSuffix(' 帧')
        self.capture_btn = QPushButton('2.采集并保存')
        self.v2_btn = QPushButton('6分钟自动标定并写入Flash V2')
        self.diag_btn = QPushButton('一次性FFC诊断（约10分钟）')
        set_button_kind(self.refresh_btn, 'step')
        set_button_kind(self.capture_btn, 'primary')
        set_button_kind(self.v2_btn, 'primary')
        set_button_kind(self.diag_btn, 'primary')
        self.refresh_btn.clicked.connect(self._refresh_ports)
        self.capture_btn.clicked.connect(self._capture)
        self.v2_btn.clicked.connect(self._run_v2_calibration)
        self.diag_btn.clicked.connect(self._run_ffc_diagnostic)
        fields = (
            ('串口', self.port_cb), ('', self.refresh_btn),
            ('设备', self.device_edit), ('路径', self.path_cb),
            ('状态', self.state_cb),
            ('场景', self.scene_edit), ('目标°C', self.temp_edit),
            ('平均', self.frame_spin), ('', self.capture_btn),
        )
        for label, widget in fields:
            if label:
                lab = QLabel(label); lab.setObjectName('fieldLabel')
                ctrl.addWidget(lab)
            ctrl.addWidget(widget)
        ctrl.addStretch(1)
        root.addWidget(command_bar)
        root.addWidget(self.v2_btn)
        root.addWidget(self.diag_btn)

        hint = QLabel(
            '生产操作只需：完全冷却后上电，立即点击“6分钟自动标定并写入Flash V2”，'
            '让镜头正对固定高发射率平场板/平场罩并保持不动。无需连续占用精密黑体；'
            '目标温度可留空。10分钟FFC诊断和手动采集仅保留为工程功能。')
        hint.setWordWrap(True); hint.setStyleSheet(f'color: {SUBTITLE_FG};')
        root.addWidget(hint)

        self.log = QPlainTextEdit(); self.log.setReadOnly(True)
        self.log.setMaximumHeight(116)
        root.addWidget(self.log)

        self.fig = Figure(figsize=(10, 5)); style_figure(self.fig)
        self.canvas = FigureCanvas(self.fig)
        root.addWidget(self.canvas, 1)
        self._draw_empty()
        self._refresh_ports()

    def _refresh_ports(self):
        current = self.port_cb.currentText()
        self.port_cb.clear()
        ports = list_serial_ports(); self.port_cb.addItems(ports)
        if not ports:
            return
        active = probe_active_port(ports)
        if active:
            self.port_cb.setCurrentText(active)
        elif current in ports:
            self.port_cb.setCurrentText(current)
        else:
            self.port_cb.setCurrentIndex(0)

    def _draw_empty(self):
        self.fig.clear(); style_figure(self.fig)
        gs = self.fig.add_gridspec(1, 3, width_ratios=[4, 4, 3], wspace=0.22)
        for idx, title in enumerate(('RAW均值', '帧间标准差')):
            ax = self.fig.add_subplot(gs[0, idx]); style_card(ax, title, '160×120')
            empty_placeholder(ax, msg='·  暂无采集  ·')
        ax = self.fig.add_subplot(gs[0, 2]); style_summary_card(ax, '汇总')
        ax.text(0.04, 0.55, '每次保存完整帧栈、均值、标准差、\n行元数据及前后热状态。',
                transform=ax.transAxes, va='center', color=SUBTITLE_FG, fontsize=9)
        self.fig.subplots_adjust(left=0.04, right=0.98, top=0.88, bottom=0.08)
        self.canvas.draw_idle()

    def _log(self, message):
        self.log.appendPlainText(f'[{time.strftime("%H:%M:%S")}] {message}')

    def _capture(self):
        if self.worker is not None and self.worker.isRunning():
            return
        port = self.port_cb.currentText().strip()
        device = self.device_edit.text().strip()
        scene = self.scene_edit.text().strip()
        if not port or not device or not scene:
            self._log('请填写串口、设备标签和场景标签')
            return
        temp_text = self.temp_edit.text().strip()
        try:
            scene_temp = None if not temp_text else float(temp_text)
        except ValueError:
            self._log('目标温度必须是数字或留空')
            return
        self._capture_context = {
            'device_label': device,
            'startup_path': self.path_cb.currentData(),
            'state_label': self.state_cb.currentText(),
            'scene_label': scene,
            'scene_temp_c': scene_temp,
            'port': port,
            'control_baud': 2000000,
            'frame_count': self.frame_spin.value(),
        }
        self.capture_btn.setEnabled(False)
        self.v2_btn.setEnabled(False)
        self.diag_btn.setEnabled(False)
        self.log.clear()
        self.worker = RawCaptureWorker(
            port, 2000000, self.frame_spin.value(), self)
        self.worker.progress.connect(self._log)
        self.worker.success.connect(self._on_success)
        self.worker.error.connect(self._on_error)
        self.worker.finished.connect(self._worker_finished)
        self.worker.start()

    def _run_v2_calibration(self):
        if self.worker is not None and self.worker.isRunning():
            return
        port = self.port_cb.currentText().strip()
        device = self.device_edit.text().strip()
        scene = self.scene_edit.text().strip()
        if not port or not device or not scene:
            self._log('Flash V2标定需要串口、设备标签和场景标签')
            return
        temp_text = self.temp_edit.text().strip()
        try:
            scene_temp = None if not temp_text else float(temp_text)
        except ValueError:
            self._log('目标温度必须是数字或留空')
            return
        self._v2_context = {
            'device_label': device,
            'scene_label': scene,
            'scene_temp_c': scene_temp,
            'port': port,
        }
        self.capture_btn.setEnabled(False)
        self.v2_btn.setEnabled(False)
        self.diag_btn.setEnabled(False)
        self.log.clear()
        self.worker = ThermalV2CalibrationWorker(
            port, self.frame_spin.value(), self)
        self.worker.progress.connect(self._log)
        self.worker.success.connect(self._on_v2_success)
        self.worker.error.connect(self._on_error)
        self.worker.finished.connect(self._worker_finished)
        self.worker.start()

    def _run_ffc_diagnostic(self):
        if self.worker is not None and self.worker.isRunning():
            return
        port = self.port_cb.currentText().strip()
        device = self.device_edit.text().strip()
        scene = self.scene_edit.text().strip()
        temp_text = self.temp_edit.text().strip()
        if not port or not device or not scene or not temp_text:
            self._log('FFC诊断需要串口、设备、场景和目标温度')
            return
        try:
            target_temp = float(temp_text)
        except ValueError:
            self._log('目标温度必须是数字')
            return
        self._diag_context = {
            'device_label': device,
            'scene_label': scene,
            'target_temp_c': target_temp,
            'port': port,
        }
        self.capture_btn.setEnabled(False)
        self.v2_btn.setEnabled(False)
        self.diag_btn.setEnabled(False)
        self.log.clear()
        self.worker = FfcRuntimeDiagnosticWorker(
            port, self.frame_spin.value(), self)
        self.worker.progress.connect(self._log)
        self.worker.success.connect(self._on_diag_success)
        self.worker.error.connect(self._on_error)
        self.worker.finished.connect(self._worker_finished)
        self.worker.start()

    def _worker_finished(self):
        self.capture_btn.setEnabled(True)
        self.v2_btn.setEnabled(True)
        self.diag_btn.setEnabled(True)

    @Slot(object)
    def _on_success(self, result):
        context = self._capture_context; self._capture_context = None
        if context is None:
            self._log('保存失败：缺少本次采集上下文')
            return
        try:
            output_dir, digest = save_thermal_capture(result, **context)
        except Exception as exc:
            self._log(f'保存失败：{exc}')
            return
        self._log(f'原始采集已保存：{output_dir}')
        self._log(f'NPZ SHA256：{digest}')
        self._draw_result(result, context)

    @Slot(object)
    def _on_diag_success(self, result):
        context = self._diag_context
        self._diag_context = None
        if context is None:
            self._log('保存失败：缺少本次FFC诊断上下文')
            return
        try:
            output_dir, analysis, digest = save_ffc_runtime_diagnostic(
                result, **context)
        except Exception as exc:
            self._log(f'FFC诊断保存失败：{exc}')
            return
        self._log(f'FFC诊断已保存：{output_dir}')
        self._log(f'空间残差P98：{analysis["corrected_spatial_residual_abs_p98_adu"]:.2f} ADU')
        self._log(f'判定：{analysis["decision_cn"]}')
        self._log(f'NPZ SHA256：{digest}')
        self._draw_diag_result(result, context, analysis)

    @Slot(object)
    def _on_v2_success(self, result):
        context = self._v2_context
        self._v2_context = None
        if context is None:
            self._log('保存失败：缺少本次Flash V2标定上下文')
            return
        try:
            output_dir, analysis, digest = save_thermal_v2_calibration(
                result, **context)
        except Exception as exc:
            self._log(f'Flash V2结果保存失败：{exc}')
            return

        for metric in result['package']['state_metrics']:
            self._log(
                f"{metric['state']}：NTC {metric['ntc_adu']}，"
                f"空间P98 {metric['spatial_residual_p98_before_adu']:.2f}→"
                f"{metric['spatial_residual_p98_after_adu']:.2f} ADU，"
                f"global {metric['global_anchor_comp_adu']:+d} ADU")
        crc = result['package']['crc']
        self._log(
            f"CRC：payload={crc['payload_crc32']:08X}，"
            f"header={crc['header_crc32']:08X}")
        if analysis['flash_write_ok'] and analysis['within_360s']:
            self._log(
                f"Flash写入、CRC回读通过；总耗时 "
                f"{analysis['elapsed_to_flash_s']:.1f} 秒（≤360秒）")
        else:
            reason = result['flash_write'].get('error', '超过360秒')
            self._log(
                f"Flash V2未通过：{reason}；V1标定仍保留，不能按成功件放行")
        self._log(f'完整标定数据已保存：{output_dir}')
        self._log(f'NPZ SHA256：{digest}')
        self._draw_v2_result(result, context, analysis)

    def _draw_v2_result(self, result, context, analysis):
        residual = np.asarray(
            result['package']['residual_maps'], dtype=np.float64)
        metrics = result['package']['state_metrics']
        self.fig.clear(); style_figure(self.fig)
        gs = self.fig.add_gridspec(1, 3, width_ratios=[4, 4, 3], wspace=0.22)
        ax1 = self.fig.add_subplot(gs[0, 0]); style_card(
            ax1, 'T0基准残差', '固定为0')
        im1 = ax1.imshow(residual[0], cmap='coolwarm', vmin=-1, vmax=1)
        cb1 = self.fig.colorbar(im1, ax=ax1, fraction=0.046, pad=0.03)
        cb1.ax.tick_params(colors=AXIS_FG, labelsize=7)
        ax2 = self.fig.add_subplot(gs[0, 1]); style_card(
            ax2, 'T3开放光路残差', '写入Flash的raw-offset ADU')
        vmax = max(float(np.percentile(np.abs(residual[3]), 98)), 1.0)
        im2 = ax2.imshow(
            residual[3], cmap='coolwarm', vmin=-vmax, vmax=vmax)
        cb2 = self.fig.colorbar(im2, ax=ax2, fraction=0.046, pad=0.03)
        cb2.ax.tick_params(colors=AXIS_FG, labelsize=7)
        ax3 = self.fig.add_subplot(gs[0, 2]); style_summary_card(
            ax3, 'Flash V2结果')
        kv_block(ax3, [
            ('设备/场景', f"{context['device_label']} / {context['scene_label']}"),
            ('实际节点秒', ' / '.join(
                f"{stage['map_packet']['uptime_ms'] / 1000:.0f}"
                for stage in result['states'])),
            ('NTC节点', ' / '.join(
                str(value) for value in result['package']['ntc_adu'])),
            ('最大P98',
             f"{analysis['max_spatial_p98_before_adu']:.1f}→"
             f"{analysis['max_spatial_p98_after_adu']:.1f} ADU"),
            ('Global节点', ' / '.join(
                f'{value:+d}'
                for value in result['package']['global_anchor_adu'])),
            ('Flash回读', '通过' if analysis['flash_write_ok'] else '失败'),
            ('总耗时', f"{analysis['elapsed_to_flash_s']:.1f} s"),
        ], x=0.05, y_top=0.80, line_h=0.085)
        self.fig.subplots_adjust(left=0.04, right=0.98, top=0.88, bottom=0.08)
        self.canvas.draw_idle()

    def _draw_diag_result(self, result, context, analysis):
        early = result['early']
        stable = result['stable']
        early_mean = np.asarray(early['frames'], dtype=np.float64).mean(axis=0)
        stable_mean = np.asarray(stable['frames'], dtype=np.float64).mean(axis=0)
        early_map = np.asarray(early['map_packet']['map'], dtype=np.float64)
        stable_map = np.asarray(stable['map_packet']['map'], dtype=np.float64)
        early_corrected = early_mean - early_map + early_map.mean()
        stable_corrected = stable_mean - stable_map + stable_map.mean()
        delta = stable_corrected - early_corrected
        residual = delta - np.median(delta)

        self.fig.clear(); style_figure(self.fig)
        gs = self.fig.add_gridspec(1, 3, width_ratios=[4, 4, 3], wspace=0.22)
        ax1 = self.fig.add_subplot(gs[0, 0]); style_card(ax1, '升温早期校正图', 'RAW − FFC map')
        im1 = ax1.imshow(early_corrected, cmap='gray')
        cb1 = self.fig.colorbar(im1, ax=ax1, fraction=0.046, pad=0.03)
        cb1.ax.tick_params(colors=AXIS_FG, labelsize=7)
        ax2 = self.fig.add_subplot(gs[0, 1]); style_card(ax2, '稳定期空间残差', '已去全局偏移')
        vmax = max(float(np.percentile(np.abs(residual), 98)), 0.01)
        im2 = ax2.imshow(residual, cmap='coolwarm', vmin=-vmax, vmax=vmax)
        cb2 = self.fig.colorbar(im2, ax=ax2, fraction=0.046, pad=0.03)
        cb2.ax.tick_params(colors=AXIS_FG, labelsize=7)
        ax3 = self.fig.add_subplot(gs[0, 2]); style_summary_card(ax3, '自动判定')
        kv_block(ax3, [
            ('设备/目标', f"{context['device_label']} / {context['target_temp_c']:g}°C"),
            ('早期FFC', str(early['map_packet']['ffc_count'])),
            ('稳定FFC', str(stable['map_packet']['ffc_count'])),
            ('全局变化', f"{analysis['corrected_global_delta_adu']:+.2f} ADU"),
            ('空间P98', f"{analysis['corrected_spatial_residual_abs_p98_adu']:.2f} ADU"),
            ('结论', analysis['decision_cn']),
        ], x=0.05, y_top=0.80, line_h=0.10)
        self.fig.subplots_adjust(left=0.04, right=0.98, top=0.88, bottom=0.08)
        self.canvas.draw_idle()

    def _draw_result(self, result, context):
        frames = np.asarray(result['frames'], dtype=np.float32)
        mean = frames.mean(axis=0); std = frames.std(axis=0)
        pre, post = result['pre'], result['post']
        self.fig.clear(); style_figure(self.fig)
        gs = self.fig.add_gridspec(1, 3, width_ratios=[4, 4, 3], wspace=0.22)
        ax1 = self.fig.add_subplot(gs[0, 0]); style_card(ax1, 'RAW均值', context['state_label'])
        im1 = ax1.imshow(mean, cmap='gray')
        cb1 = self.fig.colorbar(im1, ax=ax1, fraction=0.046, pad=0.03)
        cb1.ax.tick_params(colors=AXIS_FG, labelsize=7)
        for spine in cb1.ax.spines.values(): spine.set_edgecolor(CARD_EDGE)
        ax2 = self.fig.add_subplot(gs[0, 1])
        style_card(ax2, '帧间标准差', f'{frames.shape[0]}帧质量')
        vmax = max(float(np.percentile(std, 98)), 0.01)
        im2 = ax2.imshow(std, cmap='magma', vmin=0, vmax=vmax)
        cb2 = self.fig.colorbar(im2, ax=ax2, fraction=0.046, pad=0.03)
        cb2.ax.tick_params(colors=AXIS_FG, labelsize=7)
        for spine in cb2.ax.spines.values(): spine.set_edgecolor(CARD_EDGE)
        ax3 = self.fig.add_subplot(gs[0, 2]); style_summary_card(ax3, '汇总')
        kv_block(ax3, [
            ('设备/状态', f"{context['device_label']} / {context['state_label']}"),
            ('启动路径', context['startup_path']),
            ('场景', context['scene_label']),
            ('RAW均值', f'{mean.mean():.1f} ADU'),
            ('时域σ均值', f'{std.mean():.2f} ADU'),
            ('NTC 前→后', f"{pre.get('ntc', 0)} → {post.get('ntc', 0)}"),
            ('VTEMP 前→后', f"{pre.get('vtemp', 0)} → {post.get('vtemp', 0)}"),
            ('耗时', f"{result['duration_s']:.1f} s"),
        ], x=0.05, y_top=0.80, line_h=0.085)
        self.fig.subplots_adjust(left=0.04, right=0.98, top=0.88, bottom=0.08)
        self.canvas.draw_idle()

    def _on_error(self, message):
        self._capture_context = None
        self._diag_context = None
        self._v2_context = None
        self._log(f'采集失败：{message}')

    def shutdown(self):
        if self.worker and self.worker.isRunning():
            self.worker.requestInterruption()
            self.worker.wait()
