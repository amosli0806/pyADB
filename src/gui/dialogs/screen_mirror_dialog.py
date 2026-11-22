# src/gui/dialogs/screen_mirror_dialog.py
"""
投屏交互对话框模块
提供 scrcpy 投屏的参数配置、启动/停止控制、状态监控、录屏控制等功能。
非模态设计，不阻塞主界面操作。
"""

import os
import sys
import subprocess
import threading
import time
from datetime import datetime
from PySide6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QLabel,
    QComboBox, QSpinBox, QCheckBox, QPushButton,
    QGroupBox, QMessageBox, QWidget
)
from PySide6.QtCore import Qt, QTimer, Signal
from PySide6.QtGui import QIcon
import qtawesome as qta
from src.core.adb_client import AdbClient
from src.utils.helpers import resource_path, get_timestamp

from src.utils.logger import get_logger
from src.utils.qt_bridge import ui_call

logger = get_logger(__name__)


class ScreenMirrorDialog(QDialog):
    """投屏设置与交互对话框（非模态）"""

    mirror_stopped = Signal(str)

    def __init__(self, device_serial: str, device_model: str, output_dir: str, parent=None):
        super().__init__(parent)
        self.device_serial = device_serial
        self.device_model = device_model or device_serial
        self.adb = AdbClient(serial=device_serial)
        self.output_dir = output_dir
        self.safe_serial = device_serial.replace(':', '_')

        # 进程管理
        self.scrcpy_process = None
        self.record_process = None
        self.scrcpy_pid = None   # 用于跟踪已有进程

        # 状态
        self.is_mirroring = False
        self.is_recording = False
        self.is_starting = False
        self.is_record_starting = False
        self.is_record_stopping = False
        self.record_completed = False

        # 录屏临时文件
        self.record_temp_path = "/sdcard/temp_screenrecord.mp4"

        self.setWindowTitle("投屏")
        self.setModal(False)
        self.setFixedSize(480, 350)
        self.setup_ui()
        self._update_ui_state()
        self.check_timer = QTimer()
        self.check_timer.timeout.connect(self._check_process_status)
        self.check_timer.setInterval(1000)

        # ---- 检测已有投屏进程 ----
        self._detect_existing_scrcpy()

    # ---------- 检测已有投屏进程 ----------
    def _detect_existing_scrcpy(self):
        """检测设备上是否已有 scrcpy 投屏进程"""
        try:
            result = self.adb.shell(f"pgrep -f 'scrcpy.*-s {self.device_serial}'", timeout=5)
            if result.ok and result.stdout.strip():
                pid_str = result.stdout.strip().split('\n')[0]
                if pid_str.isdigit():
                    self.scrcpy_pid = int(pid_str)
                    self.is_mirroring = True
                    self.check_timer.start()
                    self._update_ui_state()
                    return
        except Exception as exc:
            logger.debug("忽略异常（_detect_existing_scrcpy）: %s", exc, exc_info=True)

    # ---------- UI 布局 ----------
    def setup_ui(self):
        layout = QVBoxLayout(self)
        layout.setSpacing(10)

        # ---- 投屏参数 ----
        param_group = QGroupBox("投屏参数")
        param_layout = QVBoxLayout(param_group)
        param_layout.setContentsMargins(9, 20, 9, 9)

        row1 = QHBoxLayout()
        row1.addWidget(QLabel("分辨率:"))
        self.resolution_combo = QComboBox()
        self.resolution_combo.addItems(["原始", "1920x1080", "1280x720", "854x480"])
        self.resolution_combo.setCurrentIndex(0)
        row1.addWidget(self.resolution_combo)
        row1.addSpacing(20)
        row1.addWidget(QLabel("码率:"))
        self.bitrate_spin = QSpinBox()
        self.bitrate_spin.setRange(1, 50)
        self.bitrate_spin.setValue(8)
        self.bitrate_spin.setSuffix(" Mbps")
        row1.addWidget(self.bitrate_spin)
        row1.addStretch()
        param_layout.addLayout(row1)

        row2 = QHBoxLayout()
        row2.addWidget(QLabel("帧   率:"))
        self.fps_spin = QSpinBox()
        self.fps_spin.setRange(10, 120)
        self.fps_spin.setValue(60)
        self.fps_spin.setSuffix(" fps")
        row2.addWidget(self.fps_spin)
        row2.addStretch()
        param_layout.addLayout(row2)

        layout.addWidget(param_group)

        # ---- 投屏选项 ----
        option_group = QGroupBox("投屏选项")
        option_layout = QVBoxLayout(option_group)
        option_layout.setContentsMargins(9, 20, 9, 9)

        self.stay_awake_cb = QCheckBox("保持屏幕唤醒")
        self.stay_awake_cb.setChecked(True)
        option_layout.addWidget(self.stay_awake_cb)

        self.show_touches_cb = QCheckBox("显示触摸操作")
        self.show_touches_cb.setChecked(True)
        option_layout.addWidget(self.show_touches_cb)

        self.turn_screen_off_cb = QCheckBox("投屏时关闭设备屏幕")
        self.turn_screen_off_cb.setChecked(False)
        option_layout.addWidget(self.turn_screen_off_cb)

        layout.addWidget(option_group)

        # ---- 录制状态 ----
        record_status_layout = QHBoxLayout()
        record_status_layout.addWidget(QLabel("📹 录制状态:"))
        self.record_status_label = QLabel("未录制")
        self.record_status_label.setStyleSheet("color: #9C9488;")
        record_status_layout.addWidget(self.record_status_label)
        record_status_layout.addStretch()
        layout.addLayout(record_status_layout)

        # ---- 控制按钮 ----
        btn_layout = QHBoxLayout()
        btn_layout.addStretch()

        # ---- 投屏按钮：始终显示“开始投屏”，始终可点击 ----
        self.start_btn = QPushButton(" 开始投屏")
        self.start_btn.setIcon(qta.icon('fa6s.play', color='white'))
        self.start_btn.setFixedHeight(32)
        self.start_btn.setObjectName("primaryBtn")
        self.start_btn.clicked.connect(self.toggle_mirror)

        btn_layout.addWidget(self.start_btn)
        btn_layout.addSpacing(15)

        # 录制控制按钮
        self.record_btn = QPushButton(" 开始录制")
        self.record_btn.setObjectName("recordBtn")
        self.record_btn.setIcon(qta.icon('fa6s.video', color='#1F1D1A'))
        self.record_btn.setFixedHeight(32)
        self.record_btn.setFixedWidth(180)
        self.record_btn.clicked.connect(self.toggle_record)
        self.record_btn.setEnabled(False)

        btn_layout.addWidget(self.record_btn)
        btn_layout.addStretch()
        layout.addLayout(btn_layout)

        # ---- 状态提示 ----
        self.tip_label = QLabel("💡 点击「开始投屏」启动 scrcpy")
        self.tip_label.setStyleSheet("color: #6E675D; font-size: 11px;")
        layout.addWidget(self.tip_label)

    # ---------- 辅助方法 ----------
    def _set_start_button_state(self):
        """投屏按钮始终为“开始投屏”，始终可点击"""
        self.start_btn.setText(" 开始投屏")
        self.start_btn.setObjectName("primaryBtn")
        self.start_btn.setEnabled(True)

    def _set_record_button_state(self, state: str):
        """
        录制按钮三态：idle 描边 / recording 实心红 / busy 灰。

        只切换 objectName 与可用性，外观交给 QSS，不再每次状态变化重写一遍色值。
        """
        if state == 'idle':
            self.record_btn.setText(" 开始录制")
            self.record_btn.setIcon(qta.icon('fa6s.video', color='#1F1D1A'))
            self.record_btn.setObjectName("recordBtn")
            self.record_btn.setEnabled(True)
        elif state == 'recording':
            self.record_btn.setText(" 停止录制")
            self.record_btn.setIcon(qta.icon('fa6s.stop', color='#FFFFFF'))
            self.record_btn.setObjectName("stopBtn")
            self.record_btn.setEnabled(True)
        else:
            # starting / stopping / disabled：过渡态与未就绪态统一为灰色禁用
            self.record_btn.setText("开始录制")
            self.record_btn.setIcon(QIcon())
            self.record_btn.setObjectName("recordBtn")
            self.record_btn.setEnabled(False)

        # objectName 变了要重新走一遍样式
        self.record_btn.style().unpolish(self.record_btn)
        self.record_btn.style().polish(self.record_btn)
        self.record_btn.update()

    def _update_ui_state(self):
        """更新所有 UI 控件的状态"""
        # 投屏按钮始终可点击，文本不变
        self._set_start_button_state()

        # 录制按钮状态
        if self.is_recording:
            self._set_record_button_state('recording')
        elif self.is_record_starting:
            self._set_record_button_state('starting')
        elif self.is_record_stopping:
            self._set_record_button_state('stopping')
        elif self.is_mirroring and not self.is_starting:
            # 投屏中且非启动中，录制按钮可用
            self._set_record_button_state('idle')
        else:
            # 投屏未就绪或启动中，录制按钮不可用（纯文本）
            self._set_record_button_state('disabled')

        # 录制状态标签
        if self.is_record_starting:
            self.record_status_label.setText("启动录制中…")
            self.record_status_label.setStyleSheet("color: #B0761F;")
        elif self.is_recording:
            self.record_status_label.setText("● 录制中")
            self.record_status_label.setStyleSheet("color: #A6402F; font-weight: bold;")
        elif self.is_record_stopping:
            self.record_status_label.setText("停止录制中…")
            self.record_status_label.setStyleSheet("color: #B0761F;")
        else:
            if self.record_completed:
                self.record_status_label.setText("录制完成")
                self.record_status_label.setStyleSheet("color: #2F6B45;")
            else:
                self.record_status_label.setText("未录制")
                self.record_status_label.setStyleSheet("color: #9C9488;")

        # 投屏参数禁用
        param_enabled = not (self.is_mirroring or self.is_starting)
        self.resolution_combo.setEnabled(param_enabled)
        self.bitrate_spin.setEnabled(param_enabled)
        self.fps_spin.setEnabled(param_enabled)
        self.stay_awake_cb.setEnabled(param_enabled)
        self.show_touches_cb.setEnabled(param_enabled)
        self.turn_screen_off_cb.setEnabled(param_enabled)

    # ---------- 投屏控制 ----------
    def toggle_mirror(self):
        """点击开始投屏：主动检测进程状态并重置"""
        # 优先使用进程对象判断进程是否运行
        process_running = False
        if self.scrcpy_process and self.scrcpy_process.poll() is None:
            process_running = True
        elif self.scrcpy_pid and self._is_pid_running(self.scrcpy_pid):
            process_running = True

        if not process_running:
            # 进程未运行，重置状态
            self.is_mirroring = False
            self.is_starting = False
            self.scrcpy_pid = None
            self.scrcpy_process = None
            self.check_timer.stop()
            self._update_ui_state()
            self.update()
        else:
            # 进程运行中
            if self.is_mirroring or self.is_starting:
                self.tip_label.setText("投屏已启动，请勿重复点击")
                return

        self.start_mirror()

    def _is_pid_running(self, pid):
        """检查指定 PID 是否还在运行"""
        try:
            result = self.adb.shell(f"ps -p {pid}", timeout=3)
            if result.ok and str(pid) in result.stdout:
                return True
            # 兜底：部分精简 ROM 的 ps 不支持 -p，退回全量列表 + grep
            result2 = self.adb.shell(f"ps | grep {pid}", timeout=3)
            if result2.ok and str(pid) in result2.stdout:
                return True
        except Exception as exc:
            logger.debug("忽略异常（_is_pid_running）: %s", exc, exc_info=True)
        return False

    def start_mirror(self):
        if self.is_mirroring or self.is_starting:
            return

        if self.scrcpy_process and self.scrcpy_process.poll() is None:
            try:
                self.scrcpy_process.terminate()
                self.scrcpy_process.wait(timeout=2)
            except:
                try:
                    self.scrcpy_process.kill()
                except Exception as exc:
                    logger.debug("忽略异常（start_mirror）: %s", exc, exc_info=True)
            self.scrcpy_process = None
        self.scrcpy_pid = None

        self.is_starting = True
        self._update_ui_state()
        self.tip_label.setText("⏳ 正在启动投屏...")

        def run():
            try:
                scrcpy_name = 'scrcpy.exe' if sys.platform == 'win32' else 'scrcpy'
                scrcpy_path = resource_path(os.path.join('tools', scrcpy_name))

                if not os.path.exists(scrcpy_path):
                    scrcpy_path = "scrcpy"
                    try:
                        subprocess.run([scrcpy_path, "--version"], capture_output=True, timeout=5, check=True)
                    except:
                        ui_call(self, self._on_mirror_missing_scrcpy)
                        return

                cmd = [scrcpy_path, "-s", self.device_serial]

                resolution = self.resolution_combo.currentText()
                if resolution != "原始":
                    size = int(resolution.split('x')[1])
                    cmd.extend(["--max-size", str(size)])

                bitrate = self.bitrate_spin.value()
                cmd.extend(["--video-bit-rate", f"{bitrate}M"])

                fps = self.fps_spin.value()
                cmd.extend(["--max-fps", str(fps)])

                if self.stay_awake_cb.isChecked():
                    cmd.append("--stay-awake")
                if self.show_touches_cb.isChecked():
                    cmd.append("--show-touches")
                if self.turn_screen_off_cb.isChecked():
                    cmd.append("--turn-screen-off")

                cmd.extend(["--window-title", f"投屏 - {self.device_model}"])

                if sys.platform == 'win32':
                    self.scrcpy_process = subprocess.Popen(
                        cmd,
                        stdout=subprocess.PIPE,
                        stderr=subprocess.PIPE,
                        creationflags=subprocess.CREATE_NO_WINDOW,
                        text=True,
                        encoding='utf-8',
                        errors='replace'
                    )
                else:
                    self.scrcpy_process = subprocess.Popen(
                        cmd,
                        stdout=subprocess.PIPE,
                        stderr=subprocess.PIPE,
                        text=True,
                        encoding='utf-8',
                        errors='replace'
                    )

                self.scrcpy_pid = self.scrcpy_process.pid

                time.sleep(0.5)
                if self.scrcpy_process.poll() is not None:
                    returncode = self.scrcpy_process.returncode
                    ui_call(self, self._on_mirror_start_failed,
                            f"❌ scrcpy 启动失败，返回码: {returncode}", True)
                    return

                ui_call(self, self._on_mirror_started)

            except Exception as e:
                ui_call(self, self._on_mirror_start_failed, f"❌ 启动失败: {str(e)[:80]}", False)

        threading.Thread(target=run, daemon=True).start()

    def _on_mirror_missing_scrcpy(self):
        """找不到 scrcpy 可执行文件（GUI 线程）"""
        self.tip_label.setText("❌ 未找到 scrcpy，请将 scrcpy 放到 tools 目录或添加到 PATH")
        self.is_starting = False
        self._update_ui_state()

    def _on_mirror_start_failed(self, message, clear_pid=True):
        """投屏启动失败（GUI 线程）"""
        self.tip_label.setText(message)
        self.is_starting = False
        if clear_pid:
            self.scrcpy_process = None
            self.scrcpy_pid = None
        self._update_ui_state()
        self.update()

    def _on_mirror_started(self):
        """投屏启动成功：置状态、启轮询定时器（QTimer 必须在其所属线程启动）"""
        self.is_mirroring = True
        self.is_starting = False
        self.check_timer.start()
        self.tip_label.setText("✅ 投屏已启动")
        self._update_ui_state()
        self.update()

    # ---------- 录制控制 ----------
    def toggle_record(self):
        if self.is_recording:
            self.stop_record()
        else:
            self.start_record()

    def start_record(self):
        if not self.is_mirroring or self.is_recording or self.is_record_starting:
            return

        self.is_record_starting = True
        self.record_completed = False
        self._update_ui_state()
        self.tip_label.setText("🎬 准备开始录制...")

        def run():
            try:
                self.adb.shell(f"rm -f {self.record_temp_path}", timeout=5)

                record_cmd = self.adb.command(
                    ["shell", f"screenrecord --time-limit 180 --size 1920x1080 {self.record_temp_path}"])
                self.record_process = subprocess.Popen(
                    record_cmd,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                    creationflags=subprocess.CREATE_NO_WINDOW if sys.platform == 'win32' else 0
                )

                self.is_recording = True
                self.is_record_starting = False

                def monitor_record():
                    self.record_process.wait()
                    if self.is_recording:
                        ui_call(self, self._finish_recording)

                threading.Thread(target=monitor_record, daemon=True).start()
                ui_call(self, self._on_record_started)

            except Exception as e:
                ui_call(self, self._on_record_start_failed, str(e)[:80])

        threading.Thread(target=run, daemon=True).start()

    def _on_record_started(self):
        """录制进程已拉起（GUI 线程）"""
        self.tip_label.setText("⏺ 录制中...")
        self._update_ui_state()
        self.update()

    def _on_record_start_failed(self, message):
        """录制启动失败（GUI 线程）"""
        self.is_record_starting = False
        self.tip_label.setText(f"❌ 录制启动失败: {message}")
        self._update_ui_state()
        self.update()

    def stop_record(self):
        if not self.is_recording or self.is_record_stopping:
            return

        self.is_record_stopping = True
        self._update_ui_state()
        self.tip_label.setText("⏹ 正在停止录制...")

        def kill_record():
            try:
                if self.record_process and self.record_process.poll() is None:
                    self.record_process.terminate()
                    self.record_process.wait(timeout=5)
            except Exception:
                try:
                    self.record_process.kill()
                except Exception as exc:
                    logger.debug("忽略异常（stop_record）: %s", exc, exc_info=True)

            self.is_recording = False
            self.is_record_stopping = False
            self._finish_recording()

        threading.Thread(target=kill_record, daemon=True).start()

    def _finish_recording(self):
        try:
            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            device_dir = os.path.join(self.output_dir, self.safe_serial)
            os.makedirs(device_dir, exist_ok=True)
            local_path = os.path.join(device_dir, f"screenrecord_{timestamp}.mp4")

            pull_result = self.adb.run(["pull", self.record_temp_path, local_path], timeout=30)
            self.adb.shell(f"rm -f {self.record_temp_path}", timeout=5)

            if pull_result.ok and os.path.exists(local_path):
                file_size = os.path.getsize(local_path)
                if file_size > 1024:
                    self.record_completed = True
                    self.tip_label.setText(f"✅ 录制完成，已保存至: {os.path.basename(local_path)}")
                else:
                    os.remove(local_path)
                    self.tip_label.setText("⚠️ 录制文件过小，可能录制失败")
                    self.record_completed = False
            else:
                self.tip_label.setText("❌ 拉取录制文件失败")
                self.record_completed = False

        except Exception as e:
            self.tip_label.setText(f"❌ 录制完成处理失败: {str(e)[:80]}")
            self.record_completed = False

        self.is_recording = False
        self.is_record_starting = False
        self.is_record_stopping = False
        self.record_process = None
        self._update_ui_state()
        self.update()

    # ---------- 进程监控 ----------
    def _check_process_status(self):
        """定时检查投屏进程是否还在运行"""
        if self.is_mirroring:
            exists = False
            if self.scrcpy_process and self.scrcpy_process.poll() is None:
                exists = True
            elif self.scrcpy_pid:
                if self._is_pid_running(self.scrcpy_pid):
                    exists = True

            if not exists:
                self.is_mirroring = False
                self.is_starting = False
                self.check_timer.stop()
                self.scrcpy_pid = None
                self.scrcpy_process = None
                self.tip_label.setText("💡 scrcpy 窗口已关闭，点击「开始投屏」可再次启动")
                self._update_ui_state()
                self.update()

    # ---------- 关闭事件 ----------
    def _terminate_scrcpy(self):
        """终止 scrcpy 子进程并停掉状态定时器，避免关闭对话框后残留进程"""
        if self.check_timer.isActive():
            self.check_timer.stop()

        proc = getattr(self, 'scrcpy_process', None)
        if proc is not None and proc.poll() is None:
            try:
                proc.terminate()
                proc.wait(timeout=2)
            except Exception:
                try:
                    proc.kill()
                except Exception as exc:
                    logger.debug("忽略异常（_terminate_scrcpy）: %s", exc, exc_info=True)

        self.scrcpy_process = None
        self.scrcpy_pid = None
        self.is_mirroring = False
        self.is_starting = False

    def _wait_record_finished(self, timeout=3.0):
        """等待录制停止与文件拉取完成（异步线程），避免关闭时丢文件或残留线程"""
        deadline = time.time() + timeout
        while (self.is_recording or self.is_record_stopping) and time.time() < deadline:
            time.sleep(0.1)

    def force_close(self):
        """退出程序时调用：不弹任何确认框，直接停下所有后台活动并关闭"""
        try:
            if self.is_recording and not self.is_record_stopping:
                self.stop_record()
        except Exception as exc:
            logger.debug("忽略异常（force_close）: %s", exc, exc_info=True)
        self._wait_record_finished()
        self._terminate_scrcpy()
        self.close()

    def closeEvent(self, event):
        if self.is_recording or self.is_record_starting or self.is_record_stopping:
            reply = QMessageBox.question(
                self, "确认",
                "录制正在进行中，确定关闭将停止录制并保存文件。是否继续？",
                QMessageBox.Yes | QMessageBox.No
            )
            if reply == QMessageBox.Yes:
                if self.is_recording:
                    self.stop_record()
                self._wait_record_finished()
                self._terminate_scrcpy()
                event.accept()
            else:
                event.ignore()
        else:
            self._terminate_scrcpy()
            event.accept()