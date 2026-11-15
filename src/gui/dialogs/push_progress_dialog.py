# src/gui/dialogs/push_progress_dialog.py
"""
推送进度对话框模块
显示 adb push 的实时进度，并允许用户取消操作。
"""

import subprocess
import sys
import threading
import os
from PySide6.QtWidgets import (QDialog, QVBoxLayout, QProgressBar,
                               QPushButton, QLabel, QMessageBox)
from PySide6.QtCore import QThread, Signal


class PushWorker(QThread):
    progress_updated = Signal(int)
    output_line = Signal(str)
    finished = Signal(bool, str)

    def __init__(self, adb_path, device_serial, local_path, remote_path, parent=None):
        super().__init__(parent)
        self.adb_path = adb_path
        self.device_serial = device_serial
        self.local_path = local_path
        self.remote_path = remote_path
        self._process = None
        self._is_running = True
        self._cancelled = False

    def run(self):
        try:
            if sys.platform == 'win32':
                startupinfo = subprocess.STARTUPINFO()
                startupinfo.dwFlags |= subprocess.STARTF_USESHOWWINDOW
                startupinfo.wShowWindow = subprocess.SW_HIDE
                creationflags = subprocess.CREATE_NO_WINDOW
            else:
                startupinfo = None
                creationflags = 0

            is_directory = self.remote_path.endswith('/') or os.path.isdir(self.local_path)
            if is_directory:
                mkdir_cmd = [self.adb_path, "-s", self.device_serial, "shell", "mkdir", "-p", self.remote_path]
                mkdir_result = subprocess.run(
                    mkdir_cmd, capture_output=True, text=True, timeout=5,
                    startupinfo=startupinfo, creationflags=creationflags
                )
                if mkdir_result.returncode != 0:
                    self.finished.emit(False, f"无法创建远程目录 {self.remote_path}\n{mkdir_result.stderr}")
                    return

            cmd = [self.adb_path, "-s", self.device_serial, "push", self.local_path, self.remote_path]
            self._process = subprocess.Popen(
                cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                text=True, encoding='utf-8', errors='replace', bufsize=1,
                universal_newlines=True, startupinfo=startupinfo, creationflags=creationflags
            )

            def read_stdout():
                for line in iter(self._process.stdout.readline, ''):
                    if not self._is_running:
                        break
                    line = line.rstrip('\n')
                    if line:
                        self.output_line.emit(line)

            def read_stderr():
                for line in iter(self._process.stderr.readline, ''):
                    if not self._is_running:
                        break
                    line = line.rstrip('\n')
                    if line:
                        self.output_line.emit(f"[STDERR] {line}")

            t_out = threading.Thread(target=read_stdout, daemon=True)
            t_err = threading.Thread(target=read_stderr, daemon=True)
            t_out.start()
            t_err.start()

            returncode = self._process.wait(timeout=600)
            t_out.join(timeout=1)
            t_err.join(timeout=1)

            if self._cancelled:
                self.finished.emit(False, "推送已取消")
            elif returncode == 0:
                self.progress_updated.emit(100)
                self.finished.emit(True, "推送成功")
            else:
                self.finished.emit(False, f"推送失败，返回码：{returncode}")

        except subprocess.TimeoutExpired:
            if self._cancelled:
                self.finished.emit(False, "推送已取消")
            else:
                if self._process:
                    self._process.kill()
                self.finished.emit(False, "推送超时")
        except Exception as e:
            self.finished.emit(False, f"推送异常: {str(e)}")
        finally:
            self._is_running = False

    def stop(self):
        self._is_running = False
        self._cancelled = True
        if self._process and self._process.poll() is None:
            try:
                self._process.terminate()
                self._process.wait(timeout=2)
            except:
                self._process.kill()


class PushProgressDialog(QDialog):
    log_message = Signal(str)

    def __init__(self, adb_path, device_serial, local_path, remote_path, parent=None):
        super().__init__(parent)
        self.setWindowTitle("正在推送文件")
        self.setModal(True)
        self.resize(400, 150)

        self.worker = PushWorker(adb_path, device_serial, local_path, remote_path, self)
        self.worker.progress_updated.connect(self.update_progress)
        self.worker.output_line.connect(self.on_output_line)
        self.worker.finished.connect(self.on_finished)

        layout = QVBoxLayout(self)

        self.label = QLabel(f"正在推送: {local_path} → {remote_path}")
        self.label.setWordWrap(True)
        layout.addWidget(self.label)

        self.progress = QProgressBar()
        self.progress.setRange(0, 100)
        self.progress.setValue(0)
        layout.addWidget(self.progress)

        self.cancel_btn = QPushButton("取消")
        self.cancel_btn.clicked.connect(self.on_cancel)
        layout.addWidget(self.cancel_btn)

        self._success = False

    def start(self):
        self.worker.start()

    def update_progress(self, percent):
        self.progress.setValue(percent)

    def on_output_line(self, line):
        if "1 file pushed" in line or "files pushed" in line:
            formatted = f"<span style='color:#2F6B45;'>✅ [推送] {line}</span>"
        elif "error" in line.lower() or "failed" in line.lower():
            formatted = f"<span style='color:#A6402F;'>❌ [推送] {line}</span>"
        else:
            formatted = f"<span style='color:#1F1D1A;'>ℹ️ [推送] {line}</span>"
        if hasattr(self.parent(), 'append_log'):
            self.parent().append_log(formatted)

    def on_finished(self, success, message):
        self._success = success
        if success:
            self.label.setText("推送成功！")
        else:
            self.label.setText(message)
            if message != "推送已取消":
                self.log_message.emit(f"推送失败详情：{message}")

        self.cancel_btn.clicked.disconnect()
        self.cancel_btn.clicked.connect(self.accept)
        self.cancel_btn.setText("关闭")

    def on_cancel(self):
        if self.worker.isRunning():
            reply = QMessageBox.question(self, "取消推送", "确定要取消推送吗？",
                                         QMessageBox.Yes | QMessageBox.No)
            if reply == QMessageBox.Yes:
                self.worker.stop()
                self.cancel_btn.setEnabled(False)
                self.label.setText("正在取消推送...")
        else:
            self.accept()

    def closeEvent(self, event):
        if self.worker.isRunning():
            self.on_cancel()
            event.ignore()
        else:
            event.accept()