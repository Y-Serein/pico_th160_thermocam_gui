"""固件烧录 Tab：把 .uf2 复制到 RP2350 BOOTSEL 盘符，支持串行批量烧录。

行为对齐 docs/bin/th160flash-main（Rust 版）：
  - 启动时记录所有 ≥ 512 MB 的盘符，永不作为烧录目标（避免误写系统盘 / U 盘）
  - 监听过程中每 500 ms 轮询 A:..Z:，检测到新出现的小容量盘 → 复制 flash.uf2
  - 写入失败重试 5 次，每次间隔 500 ms
  - 烧录成功后等待用户拔出设备，可继续插入下一台
"""
import os
import shutil
import sys
import time
from html import escape
from pathlib import Path

from PySide6.QtCore import QThread, Signal, Slot
from PySide6.QtWidgets import (QWidget, QVBoxLayout, QHBoxLayout, QPushButton,
                               QLabel, QPlainTextEdit, QFileDialog)


LARGE_DRIVE_THRESHOLD = 512 * 1024 * 1024  # 字节；该阈值及以上的盘符不会被烧录
POLL_INTERVAL_MS = 500
COPY_RETRIES = 5
COPY_RETRY_DELAY_MS = 500


def _list_drive_letters():
    """枚举当前存在的 Windows 盘符（A..Z）。非 Windows 平台返回空列表。"""
    if sys.platform != 'win32':
        return []
    present = []
    for i in range(26):
        letter = chr(ord('A') + i)
        if os.path.exists(f'{letter}:\\'):
            present.append(letter)
    return present


def _drive_capacity(letter):
    """返回盘符总容量（字节）；读不到时返回 None。"""
    try:
        return shutil.disk_usage(f'{letter}:\\').total
    except OSError:
        return None


def _scan_large_drives():
    """启动时快照：≥ 512 MB 的盘符全部加入永久跳过名单。"""
    skip = []
    for letter in _list_drive_letters():
        cap = _drive_capacity(letter)
        if cap is not None and cap >= LARGE_DRIVE_THRESHOLD:
            skip.append(letter)
    return skip


class FlashWorker(QThread):
    log = Signal(str, str)        # (message, level: info/success/warning/error)
    status = Signal(str, str)     # (message, color hex)
    success = Signal()
    finished_clean = Signal()

    def __init__(self, firmware_path, skip_drives, parent=None):
        super().__init__(parent)
        self.firmware_path = firmware_path
        self.skip_drives = set(skip_drives)
        self._stop = False

    def request_stop(self):
        self._stop = True

    def run(self):
        try:
            uf2_data = Path(self.firmware_path).read_bytes()
        except OSError as e:
            self.log.emit(f'读取固件失败：{e}', 'error')
            self.finished_clean.emit()
            return

        # 把开始监听时已经存在的盘符记为「已见过」，避免误烧已挂载的设备
        seen = set()
        for letter in _list_drive_letters():
            if letter not in self.skip_drives:
                seen.add(letter)

        self.status.emit('搜索中：等待 BOOT 模式设备插入…', '#ffd166')

        while not self._stop:
            current = set(l for l in _list_drive_letters() if l not in self.skip_drives)
            arrived = current - seen
            removed = seen - current

            for letter in sorted(removed):
                self.log.emit(f'盘符已移除：{letter}:', 'info')
            for letter in sorted(arrived):
                self.log.emit(f'检测到新盘符：{letter}:', 'info')
                cap = _drive_capacity(letter)
                if cap is None:
                    self.log.emit(f'无法读取 {letter}: 容量，跳过', 'warning')
                    continue
                if cap >= LARGE_DRIVE_THRESHOLD:
                    self.log.emit(
                        f'{letter}: 容量 {cap // (1024 * 1024)} MB ≥ 512 MB，跳过',
                        'warning')
                    continue
                self.log.emit('=' * 40, 'info')
                self.log.emit(f'目标设备：{letter}:  ({cap // 1024} KB)', 'info')
                self.status.emit(f'烧录中：{letter}: …', '#f4a300')
                if self._flash(letter, uf2_data):
                    self.log.emit('烧录成功，请拔出设备插入下一台', 'success')
                    self.success.emit()
                    self.status.emit('搜索中：等待下一台设备…', '#ffd166')
                else:
                    self.status.emit(f'失败：{letter}: 烧录出错', '#ff5050')

            seen = current
            # 分片 sleep，stop 信号能在 50 ms 内响应
            for _ in range(POLL_INTERVAL_MS // 50):
                if self._stop:
                    break
                self.msleep(50)

        self.status.emit('已停止', '#888')
        self.finished_clean.emit()

    def _flash(self, drive_letter, uf2_data):
        dest = Path(f'{drive_letter}:\\') / 'flash.uf2'
        for attempt in range(1, COPY_RETRIES + 1):
            if attempt > 1:
                self.log.emit(f'重试 {attempt}/{COPY_RETRIES}…', 'info')
            try:
                with open(dest, 'wb') as f:
                    f.write(uf2_data)
            except OSError as e:
                self.log.emit(f'尝试 {attempt} 写入失败：{e}', 'warning')
                if attempt < COPY_RETRIES:
                    self.msleep(COPY_RETRY_DELAY_MS)
                continue

            # 写入成功后短暂等待 → UF2 触发的设备复位通常会在此间发生
            self.msleep(100)
            try:
                size = os.path.getsize(dest)
            except (FileNotFoundError, PermissionError, OSError) as e:
                # UF2 写入完成后设备会立即复位、盘符消失，read-back 必然失败
                # 这是 RP2350 BOOTSEL 的预期行为，按 AGENTS.md 视为烧录成功
                self.log.emit(f'写入完成，设备已复位（{type(e).__name__}）→ 视为成功', 'info')
                return True
            if size == len(uf2_data):
                self.log.emit(f'写入校验通过（{size} 字节）', 'info')
                return True
            self.log.emit(f'尝试 {attempt} 大小校验失败：{size} vs {len(uf2_data)}', 'warning')
            if attempt < COPY_RETRIES:
                self.msleep(COPY_RETRY_DELAY_MS)
        self.log.emit(f'{COPY_RETRIES} 次尝试均失败', 'error')
        return False


class FlashTab(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.firmware_path = None
        self.skip_drives = []
        self.worker = None
        self.success_count = 0

        root = QVBoxLayout(self)

        ctrl = QHBoxLayout()
        self.select_btn = QPushButton('1.选择固件')
        self.start_btn = QPushButton('2.开始烧录')
        self.stop_btn = QPushButton('3.停止')
        self.clear_btn = QPushButton('清空日志')
        self.start_btn.setEnabled(False)
        self.stop_btn.setEnabled(False)
        self.select_btn.clicked.connect(self._select)
        self.start_btn.clicked.connect(self._start)
        self.stop_btn.clicked.connect(self._stop)
        self.clear_btn.clicked.connect(self._clear_log)
        ctrl.addWidget(self.select_btn)
        ctrl.addWidget(self.start_btn)
        ctrl.addWidget(self.stop_btn)
        ctrl.addStretch(1)
        ctrl.addWidget(self.clear_btn)
        root.addLayout(ctrl)

        self.path_lbl = QLabel('未选择固件')
        self.path_lbl.setStyleSheet('color:#aaa; padding:2px;')
        root.addWidget(self.path_lbl)

        info_row = QHBoxLayout()
        self.status_lbl = QLabel('空闲')
        self.status_lbl.setStyleSheet('color:#888; padding:4px; font-weight:bold;')
        self.count_lbl = QLabel('成功烧录：0')
        self.count_lbl.setStyleSheet(
            'color:#50c878; padding:4px; font-weight:bold; font-size:14px;')
        info_row.addWidget(self.status_lbl, 1)
        info_row.addWidget(self.count_lbl)
        root.addLayout(info_row)

        if sys.platform != 'win32':
            hint = QLabel('注意：固件烧录功能仅支持 Windows（依赖盘符挂载机制）。')
            hint.setStyleSheet('color:#ff8866; padding:4px;')
            root.addWidget(hint)
            self.select_btn.setEnabled(False)

        self.log_view = QPlainTextEdit()
        self.log_view.setReadOnly(True)
        self.log_view.setStyleSheet(
            'background:#111; color:#ddd; font-family:monospace;')
        root.addWidget(self.log_view, 1)

        if sys.platform == 'win32':
            self.skip_drives = _scan_large_drives()
            if self.skip_drives:
                self._log(
                    f'启动时已记录大容量盘符（不会作为目标）：'
                    f'{", ".join(self.skip_drives)}',
                    'info')

        self._log(
            '使用流程：① 选择 .uf2 固件 → ② 点击「开始烧录」 → '
            '③ 按住 RP2350 的 BOOT 键插入 USB → 自动写入并校验',
            'info')
        self._log(
            '烧录成功后拔出设备即可继续插入下一台进行批量烧录，点击「停止」结束。',
            'info')

    def _select(self):
        path, _ = QFileDialog.getOpenFileName(
            self, '选择 UF2 固件', '', 'UF2 固件 (*.uf2)')
        if not path:
            return
        self.firmware_path = path
        self.path_lbl.setText(f'已选择：{os.path.basename(path)}')
        self.path_lbl.setStyleSheet('color:#9ec5ff; padding:2px; font-weight:bold;')
        self.start_btn.setEnabled(True)
        self._log(f'固件已选择：{path}', 'info')

    def _start(self):
        if not self.firmware_path:
            return
        self.worker = FlashWorker(self.firmware_path, self.skip_drives, self)
        self.worker.log.connect(self._log)
        self.worker.status.connect(self._set_status)
        self.worker.success.connect(self._inc_count)
        self.worker.finished_clean.connect(self._on_finished)
        self.worker.start()
        self.select_btn.setEnabled(False)
        self.start_btn.setEnabled(False)
        self.stop_btn.setEnabled(True)
        self._log('--- 开始监听 ---', 'info')

    def _stop(self):
        if self.worker:
            self.worker.request_stop()
            self.stop_btn.setEnabled(False)
            self._log('--- 停止监听 ---', 'info')

    def _on_finished(self):
        self.worker = None
        self.select_btn.setEnabled(sys.platform == 'win32')
        self.start_btn.setEnabled(self.firmware_path is not None)
        self.stop_btn.setEnabled(False)

    def _clear_log(self):
        self.log_view.clear()

    @Slot(str, str)
    def _log(self, msg, level='info'):
        ts = time.strftime('%H:%M:%S')
        prefix = {
            'info':    'INFO  ',
            'success': '成功  ',
            'warning': '警告  ',
            'error':   '错误  ',
        }.get(level, 'INFO  ')
        color = {
            'info':    '#dddddd',
            'success': '#50ff78',
            'warning': '#ffc850',
            'error':   '#ff6464',
        }.get(level, '#dddddd')
        self.log_view.appendHtml(
            f'<span style="color:#666">[{ts}]</span> '
            f'<span style="color:{color}; font-weight:bold;">{prefix}</span> '
            f'<span style="color:{color};">{escape(msg)}</span>')

    @Slot(str, str)
    def _set_status(self, msg, color):
        self.status_lbl.setText(msg)
        self.status_lbl.setStyleSheet(
            f'color:{color}; padding:4px; font-weight:bold;')

    @Slot()
    def _inc_count(self):
        self.success_count += 1
        self.count_lbl.setText(f'成功烧录：{self.success_count}')

    def shutdown(self):
        if self.worker:
            self.worker.request_stop()
            self.worker.wait(2000)
