# src/gui/dialogs/device_info_dialog.py
import subprocess
import sys
import threading
import re
from PySide6.QtWidgets import (QDialog, QVBoxLayout,
                               QPushButton, QTextEdit, QApplication, QMessageBox)
from PySide6.QtCore import Qt, Signal, Slot
import qtawesome as qta


class DeviceInfoDialog(QDialog):
    info_ready = Signal(str)  # 信号，用于将信息传回主线程

    def __init__(self, adb_manager, device_serial, parent=None):
        super().__init__(parent)
        self.adb_manager = adb_manager
        self.device_serial = device_serial
        self.setWindowTitle("设备信息")
        self.resize(500, 400)
        self.setup_ui()
        self.info_ready.connect(self.set_info_text)

        # 先显示"信息获取中..."
        self.info_text.setText("⏳ 信息获取中...")
        self.info_text.setAlignment(Qt.AlignCenter)

        self.load_info()

    def setup_ui(self):
        layout = QVBoxLayout(self)
        self.info_text = QTextEdit()
        self.info_text.setReadOnly(True)
        layout.addWidget(self.info_text)

        # 复制按钮：蓝色，带复制图标
        self.copy_btn = QPushButton(" 复制")
        self.copy_btn.setIcon(qta.icon('fa6s.copy', color='white'))
        self.copy_btn.setFixedHeight(32)
        self.copy_btn.setStyleSheet("""
            QPushButton {
                background-color: #3498db;
                color: white;
                border: none;
                border-radius: 6px;
            }
            QPushButton:hover {
                background-color: #5dade2;
            }
            QPushButton:pressed {
                background-color: #2980b9;
            }
        """)
        self.copy_btn.clicked.connect(self.copy_to_clipboard)
        layout.addWidget(self.copy_btn)

    def load_info(self):
        def worker():
            info = {}
            errors = []

            # 获取分辨率、密度
            try:
                wm_size = self._run_adb("shell wm size")
                match = re.search(r'(\d+x\d+)', wm_size)
                info['分辨率'] = match.group(1) if match else "未知"
            except Exception as e:
                errors.append(f"分辨率获取失败: {e}")
                info['分辨率'] = "获取失败"

            try:
                wm_density = self._run_adb("shell wm density")
                match = re.search(r'(\d+)', wm_density)
                info['屏幕密度'] = match.group(1) if match else "未知"
            except Exception as e:
                errors.append(f"屏幕密度获取失败: {e}")
                info['屏幕密度'] = "获取失败"

            # 芯片型号、CPU型号、核心数、总内存
            info['芯片型号'] = self._get_prop("ro.board.platform") or "未知"
            info['CPU型号'] = self._get_prop("ro.product.board") or "未知"

            try:
                cpuinfo = self._run_adb("shell cat /proc/cpuinfo")
                cores = re.findall(r'processor\s*:', cpuinfo)
                info['CPU核心数'] = str(len(cores)) if cores else "未知"
            except Exception as e:
                errors.append(f"CPU核心数获取失败: {e}")
                info['CPU核心数'] = "获取失败"

            try:
                mem_total = self._run_adb("shell cat /proc/meminfo")
                mem_match = re.search(r'MemTotal:\s+(\d+) kB', mem_total)
                if mem_match:
                    total_mb = round(int(mem_match.group(1)) / 1024)
                    info['总内存(MB)'] = str(total_mb)
                else:
                    info['总内存(MB)'] = "未知"
            except Exception as e:
                errors.append(f"总内存获取失败: {e}")
                info['总内存(MB)'] = "获取失败"

            # 安卓版本
            info['安卓版本'] = self._get_prop("ro.build.version.release") or "未知"

            # 分区可用空间
            try:
                df = self._run_adb("shell df /data /system")
                lines = df.splitlines()
                for line in lines:
                    if '/data' in line:
                        parts = line.split()
                        if len(parts) >= 4:
                            info['数据分区可用'] = parts[3]
                        else:
                            info['数据分区可用'] = "未知"
                    elif '/system' in line:
                        parts = line.split()
                        if len(parts) >= 4:
                            info['系统分区可用'] = parts[3]
                        else:
                            info['系统分区可用'] = "未知"
            except Exception as e:
                errors.append(f"分区空间获取失败: {e}")
                info['数据分区可用'] = "获取失败"
                info['系统分区可用'] = "获取失败"

            # 组装显示文本
            text = ""
            for key, value in info.items():
                text += f"{key}: {value}\n"
            if errors:
                text += "\n[错误信息]\n" + "\n".join(errors)

            self.info_ready.emit(text)

        threading.Thread(target=worker, daemon=True).start()

    def _run_adb(self, cmd):
        try:
            full_cmd = ["adb", "-s", self.device_serial] + cmd.split()
            if sys.platform == 'win32':
                result = subprocess.run(full_cmd, capture_output=True, text=True, timeout=5,
                                        creationflags=subprocess.CREATE_NO_WINDOW, encoding='utf-8')
            else:
                result = subprocess.run(full_cmd, capture_output=True, text=True, timeout=5, encoding='utf-8')
            return result.stdout.strip()
        except Exception:
            return ""

    def _get_prop(self, prop):
        return self._run_adb(f"shell getprop {prop}")

    @Slot(str)
    def set_info_text(self, text):
        # 取消居中，恢复左对齐显示设备信息
        self.info_text.setAlignment(Qt.AlignLeft)
        self.info_text.setText(text)

    def copy_to_clipboard(self):
        clipboard = QApplication.clipboard()
        clipboard.setText(self.info_text.toPlainText())
        QMessageBox.information(self, "复制成功", "设备信息已复制到剪贴板")