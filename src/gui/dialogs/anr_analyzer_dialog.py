# src/gui/dialogs/anr_analyzer_dialog.py
import os
import re
import subprocess
import sys
import threading
import zipfile
import tempfile
from PySide6.QtWidgets import (QDialog, QVBoxLayout, QHBoxLayout,
                               QTabWidget, QWidget, QPushButton,
                               QTextEdit, QFileDialog, QMessageBox,
                               QProgressBar, QLabel)
from PySide6.QtCore import Signal
from PySide6.QtGui import QIcon
import qtawesome as qta


class AnrAnalyzerDialog(QDialog):
    # 定义信号用于跨线程更新 UI
    device_status_signal = Signal(str)
    device_list_signal = Signal(str)
    result_signal = Signal(str)

    def __init__(self, adb_manager, device_serial, parent=None):
        super().__init__(parent)
        self.adb_manager = adb_manager
        self.device_serial = device_serial
        self.setWindowTitle("ANR 日志分析")
        self.resize(700, 600)
        self.setup_ui()
        self.connect_signals()
        self.refresh_anr_list()

    def setup_ui(self):
        layout = QVBoxLayout(self)

        # 选项卡
        self.tab_widget = QTabWidget()
        self.tab_device = QWidget()
        self.tab_local = QWidget()
        self.tab_widget.addTab(self.tab_device, "从设备拉取")
        self.tab_widget.addTab(self.tab_local, "从本地导入")
        layout.addWidget(self.tab_widget)

        # ----- 设备拉取选项卡 -----
        device_layout = QVBoxLayout(self.tab_device)

        self.device_status_label = QLabel("设备连接状态: ")
        device_layout.addWidget(self.device_status_label)

        self.device_list_text = QTextEdit()
        self.device_list_text.setReadOnly(True)
        self.device_list_text.setPlaceholderText("ANR 文件列表将显示在这里")
        device_layout.addWidget(self.device_list_text)

        # ---- 刷新列表按钮：蓝色 ----
        self.refresh_btn = QPushButton("刷新列表")
        self.refresh_btn.setFixedHeight(32)
        self.refresh_btn.clicked.connect(self.refresh_anr_list)
        device_layout.addWidget(self.refresh_btn)

        # ---- 拉取并分析按钮：绿色（始终可点击） ----
        self.pull_btn = QPushButton("拉取并分析")
        self.pull_btn.setFixedHeight(32)
        self.pull_btn.setObjectName("primaryBtn")
        self.pull_btn.setEnabled(True)  # 始终启用
        self.pull_btn.clicked.connect(self.pull_and_analyze)
        device_layout.addWidget(self.pull_btn)

        # ----- 本地导入选项卡 -----
        local_layout = QVBoxLayout(self.tab_local)

        self.local_file_edit = QTextEdit()
        self.local_file_edit.setReadOnly(True)
        self.local_file_edit.setPlaceholderText("选择的文件路径将显示在这里")
        local_layout.addWidget(self.local_file_edit)

        # ---- 选择 ANR 文件按钮：蓝色 ----
        self.select_file_btn = QPushButton("选择 ANR 文件或 bugreport.zip")
        self.select_file_btn.setFixedHeight(32)
        self.select_file_btn.clicked.connect(self.select_local_file)
        local_layout.addWidget(self.select_file_btn)

        # ---- 分析本地文件按钮：绿色 ----
        self.analyze_local_btn = QPushButton("分析本地文件")
        self.analyze_local_btn.setFixedHeight(32)
        self.analyze_local_btn.setObjectName("primaryBtn")
        self.analyze_local_btn.clicked.connect(self.analyze_local_file)
        local_layout.addWidget(self.analyze_local_btn)

        # 分析结果显示区域
        self.result_text = QTextEdit()
        self.result_text.setReadOnly(True)
        self.result_text.setPlaceholderText("解析后的 ANR 信息将显示在这里")
        layout.addWidget(self.result_text)

        # 底部按钮
        btn_layout = QHBoxLayout()
        btn_layout.addStretch()

        # ---- 保存报告按钮：蓝色 ----
        self.save_btn = QPushButton("保存报告")
        self.save_btn.setFixedHeight(32)
        self.save_btn.setEnabled(False)
        self.save_btn.clicked.connect(self.save_report)
        btn_layout.addWidget(self.save_btn)
        layout.addLayout(btn_layout)

        # ---- 进度条（隐藏） ----
        self.progress = QProgressBar()
        self.progress.setVisible(False)
        layout.addWidget(self.progress)

        # 初始化所有按钮的图标和文本状态
        self._update_button_texts()

    # ---------- 辅助方法：更新按钮图标 ----------
    def _set_button_text(self, button, enabled, icon_text, plain_text):
        """
        根据启用状态设置按钮文本和图标。
        启用时显示图标+文字（icon_text），禁用时只显示文字（plain_text）并清除图标。
        """
        if enabled:
            button.setText(icon_text)
            # 根据按钮类型设置对应图标
            if button == self.refresh_btn:
                button.setIcon(qta.icon('fa6s.rotate', color='#1F1D1A'))
            elif button == self.pull_btn:
                button.setIcon(qta.icon('fa6s.download', color='white'))
            elif button == self.select_file_btn:
                button.setIcon(qta.icon('fa6s.folder-open', color='#1F1D1A'))
            elif button == self.analyze_local_btn:
                button.setIcon(qta.icon('fa6s.magnifying-glass', color='white'))
            elif button == self.save_btn:
                button.setIcon(qta.icon('fa6s.floppy-disk', color='#1F1D1A'))
        else:
            button.setText(plain_text)
            button.setIcon(QIcon())  # 清除图标

    def _update_button_texts(self):
        """根据当前启用状态更新所有需要图标的按钮"""
        # 刷新列表按钮始终启用
        self._set_button_text(self.refresh_btn, True, " 刷新列表", "刷新列表")
        # 拉取并分析按钮始终启用
        self._set_button_text(self.pull_btn, True, " 拉取并分析", "拉取并分析")
        # 选择 ANR 文件按钮始终启用
        self._set_button_text(self.select_file_btn, True, " 选择 ANR 文件或 bugreport.zip", "选择 ANR 文件或 bugreport.zip")
        # 分析本地文件按钮根据启用状态
        self._set_button_text(self.analyze_local_btn, self.analyze_local_btn.isEnabled(), " 分析本地文件", "分析本地文件")
        # 保存报告按钮根据启用状态
        self._set_button_text(self.save_btn, self.save_btn.isEnabled(), " 保存报告", "保存报告")

    def connect_signals(self):
        self.device_status_signal.connect(self.device_status_label.setText)
        self.device_list_signal.connect(self.device_list_text.setPlainText)
        self.result_signal.connect(self.on_result_ready)

    def on_result_ready(self, text):
        """结果就绪时启用保存按钮"""
        self.result_text.setPlainText(text)
        has_content = bool(text.strip()) and "未检测到 ANR" not in text
        self.save_btn.setEnabled(has_content)
        self._update_button_texts()

    def refresh_anr_list(self):
        """刷新设备上的 ANR 文件列表（不改变拉取按钮状态）"""
        def worker():
            try:
                # 检查设备状态
                state_cmd = ["adb", "-s", self.device_serial, "get-state"]
                if sys.platform == 'win32':
                    result = subprocess.run(state_cmd, capture_output=True, text=True, timeout=3,
                                            creationflags=subprocess.CREATE_NO_WINDOW)
                else:
                    result = subprocess.run(state_cmd, capture_output=True, text=True, timeout=3)
                if result.stdout.strip() != "device":
                    self.device_status_signal.emit("设备未连接或未授权")
                    self.device_list_signal.emit("设备未连接或未授权")
                    return
                self.device_status_signal.emit("设备已连接")

                # 列出 /data/anr/ 目录
                ls_cmd = ["adb", "-s", self.device_serial, "shell", "ls", "-l", "/data/anr/"]
                if sys.platform == 'win32':
                    ls_result = subprocess.run(ls_cmd, capture_output=True, text=True, timeout=5,
                                               creationflags=subprocess.CREATE_NO_WINDOW)
                else:
                    ls_result = subprocess.run(ls_cmd, capture_output=True, text=True, timeout=5)

                if ls_result.returncode != 0:
                    self.device_list_signal.emit("无法访问 /data/anr/，可能需要 root 权限")
                else:
                    files = ls_result.stdout.strip()
                    if not files:
                        self.device_list_signal.emit("未找到 ANR 文件")
                    else:
                        self.device_list_signal.emit(files)
            except Exception as e:
                self.device_list_signal.emit(f"获取列表失败: {str(e)}")

        threading.Thread(target=worker, daemon=True).start()

    def pull_and_analyze(self):
        """拉取最新 ANR 文件并分析（拉取按钮始终保持可点击）"""
        def worker():
            try:
                # 获取最新文件
                ls_cmd = ["adb", "-s", self.device_serial, "shell", "ls", "-t", "/data/anr/"]
                if sys.platform == 'win32':
                    ls_result = subprocess.run(ls_cmd, capture_output=True, text=True, timeout=5,
                                               creationflags=subprocess.CREATE_NO_WINDOW)
                else:
                    ls_result = subprocess.run(ls_cmd, capture_output=True, text=True, timeout=5)
                if ls_result.returncode != 0:
                    self.result_signal.emit("无法获取 ANR 文件列表，可能需要 root 权限")
                    return
                files = ls_result.stdout.splitlines()
                if not files:
                    self.result_signal.emit("未找到 ANR 文件")
                    return
                latest = files[0].strip()
                remote_path = f"/data/anr/{latest}"
                # 拉取到临时文件（mkstemp 保证文件名唯一，避免 mktemp 的竞态）
                fd, local_path = tempfile.mkstemp(suffix=".txt")
                os.close(fd)
                try:
                    pull_cmd = ["adb", "-s", self.device_serial, "pull", remote_path, local_path]
                    if sys.platform == 'win32':
                        subprocess.run(pull_cmd, capture_output=True, timeout=30,
                                       creationflags=subprocess.CREATE_NO_WINDOW, check=True)
                    else:
                        subprocess.run(pull_cmd, capture_output=True, timeout=30, check=True)
                    # 分析
                    content = self._read_file(local_path)
                finally:
                    # 无论成功失败都清理临时文件，避免残留
                    try:
                        os.remove(local_path)
                    except Exception:
                        pass
                parsed = self._parse_anr(content)
                self.result_signal.emit(parsed)
            except subprocess.CalledProcessError as e:
                self.result_signal.emit(f"拉取失败: 权限不足，请尝试执行 adb root 后重试\n错误详情: {str(e)}")
            except Exception as e:
                self.result_signal.emit(f"拉取或分析失败: {str(e)}")

        threading.Thread(target=worker, daemon=True).start()

    def select_local_file(self):
        file_path, _ = QFileDialog.getOpenFileName(
            self, "选择 ANR 文件或 bugreport.zip", "",
            "ANR 文件 (*.txt *.trace);;Bugreport 压缩包 (*.zip);;所有文件 (*.*)")
        if file_path:
            self.local_file_edit.setPlainText(file_path)
            self.analyze_local_btn.setEnabled(True)
            self._update_button_texts()

    def analyze_local_file(self):
        """分析本地 ANR 文件"""
        file_path = self.local_file_edit.toPlainText().strip()
        if not file_path:
            QMessageBox.warning(self, "提示", "       请先选择文件      ")
            return

        def worker():
            try:
                if file_path.endswith('.zip'):
                    content = self._extract_anr_from_zip(file_path)
                else:
                    content = self._read_file(file_path)
                if content:
                    parsed = self._parse_anr(content)
                    self.result_signal.emit(parsed)
                else:
                    self.result_signal.emit("无法解析文件内容")
            except Exception as e:
                self.result_signal.emit(f"分析失败: {str(e)}")

        threading.Thread(target=worker, daemon=True).start()

    def _extract_anr_from_zip(self, zip_path):
        """从 bugreport.zip 中提取 ANR 文件内容"""
        try:
            with zipfile.ZipFile(zip_path, 'r') as zf:
                anr_files = [f for f in zf.namelist() if 'anr' in f.lower() and f.endswith(('.txt', '.trace'))]
                if not anr_files:
                    return "未在压缩包中找到 ANR 文件"
                with zf.open(anr_files[0]) as f:
                    return f.read().decode('utf-8', errors='replace')
        except Exception as e:
            return f"解压失败: {str(e)}"

    def _read_file(self, path):
        try:
            with open(path, 'r', encoding='utf-8', errors='replace') as f:
                return f.read()
        except:
            return ""

    def _parse_anr(self, content):
        """解析 ANR 日志内容，返回格式化的文本"""
        lines = content.splitlines()
        result = []
        subject = re.search(r'Subject:\s*(.*)', content)
        if subject:
            result.append(f"【ANR 主题】\n{subject.group(1)}\n")
        pid_match = re.search(r'PID:\s*(\d+)', content)
        if pid_match:
            result.append(f"PID: {pid_match.group(1)}")
        proc_match = re.search(r'Process:\s*(.*)', content)
        if proc_match:
            result.append(f"进程: {proc_match.group(1)}")
        cpu_section = re.search(r'CPU usage from.*?\n(.*?)(?=\n\n|\Z)', content, re.DOTALL)
        if cpu_section:
            result.append("\n【CPU 使用情况】")
            result.append(cpu_section.group(1).strip())
        main_thread = re.search(r'\"main\" prio=.*?\n(.*?)(?=\n\n|\Z)', content, re.DOTALL)
        if main_thread:
            result.append("\n【主线程堆栈】")
            result.append(main_thread.group(1).strip())
        else:
            stack_start = content.find("DALVIK THREADS")
            if stack_start != -1:
                stack_content = content[stack_start:]
                main_stack = re.search(r'\"main\" .*?\n(.*?)(?=\n\n|\Z)', stack_content, re.DOTALL)
                if main_stack:
                    result.append("\n【主线程堆栈】")
                    result.append(main_stack.group(1).strip())
        if len(result) <= 2:
            result = ["【ANR 分析结果】", "未检测到 ANR 相关信息，可能当前日志不包含 ANR 记录。"]

        return "\n".join(result)

    def save_report(self):
        text = self.result_text.toPlainText()
        if not text:
            QMessageBox.warning(self, "提示", "没有可保存的内容")
            return
        file_path, _ = QFileDialog.getSaveFileName(self, "保存报告", "anr_report.txt", "文本文件 (*.txt)")
        if file_path:
            try:
                with open(file_path, 'w', encoding='utf-8') as f:
                    f.write(text)
                QMessageBox.information(self, "成功", f"报告已保存至 {file_path}")
            except Exception as e:
                QMessageBox.critical(self, "错误", f"保存失败: {e}")