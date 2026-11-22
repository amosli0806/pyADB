# src/gui/dialogs/crash_log_dialog.py
import threading
from PySide6.QtWidgets import (QDialog, QVBoxLayout, QTextEdit,
                               QPushButton, QHBoxLayout, QApplication,
                               QFileDialog, QMessageBox, QSizePolicy)
from PySide6.QtCore import Signal, Slot, Qt
from PySide6.QtGui import QIcon
import qtawesome as qta
from src.gui.toast import Toast
from src.core.adb_client import AdbClient


class CrashLogDialog(QDialog):
    log_ready = Signal(str)

    def __init__(self, adb_manager, device_serial, parent=None):
        super().__init__(parent)
        self.adb_manager = adb_manager
        self.device_serial = device_serial
        self.adb = AdbClient(serial=device_serial)

        # 允许窗口最大化，并设置最小尺寸
        self.setWindowFlags(self.windowFlags() | Qt.WindowMaximizeButtonHint)
        self.setMinimumSize(600, 500)

        self.setWindowTitle("崩溃日志")
        self.setup_ui()
        self.log_ready.connect(self.set_log_text)
        self.load_logs()

    def setup_ui(self):
        layout = QVBoxLayout(self)
        self.text_edit = QTextEdit()
        self.text_edit.setReadOnly(True)
        self.text_edit.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        layout.addWidget(self.text_edit)

        btn_layout = QHBoxLayout()

        # ---- 复制按钮：蓝色 ----
        self.copy_btn = QPushButton("复制")
        self.copy_btn.setFixedHeight(32)
        self.copy_btn.clicked.connect(self.copy_log)
        self.copy_btn.setEnabled(False)

        # ---- 保存到文件按钮：绿色 ----
        self.save_btn = QPushButton("保存到文件")
        self.save_btn.setFixedHeight(32)
        self.save_btn.setObjectName("primaryBtn")
        self.save_btn.clicked.connect(self.save_log)
        self.save_btn.setEnabled(False)

        btn_layout.addWidget(self.copy_btn)
        btn_layout.addWidget(self.save_btn)
        layout.addLayout(btn_layout)

    def load_logs(self):
        def worker():
            try:
                result = self.adb.run(["logcat", "-b", "crash", "-d"], timeout=30)
                if not result.ok:
                    self.log_ready.emit(f"获取崩溃日志失败: {result.error}")
                    return
                log = result.stdout if result.stdout else "没有崩溃日志"
                self.log_ready.emit(log)
            except Exception as e:
                self.log_ready.emit(f"获取崩溃日志失败: {str(e)}")

        threading.Thread(target=worker, daemon=True).start()

    @Slot(str)
    def set_log_text(self, text):
        self.text_edit.setText(text)
        # 判断是否有有效日志
        has_log = not (text == "没有崩溃日志" or text.startswith("获取崩溃日志失败"))

        if has_log:
            self.copy_btn.setEnabled(True)
            self.copy_btn.setText(" 复制")
            self.copy_btn.setIcon(qta.icon('fa6s.copy', color='#1F1D1A'))
            self.save_btn.setEnabled(True)
            self.save_btn.setText(" 保存到文件")
            self.save_btn.setIcon(qta.icon('fa6s.floppy-disk', color='white'))
        else:
            self.copy_btn.setEnabled(False)
            self.copy_btn.setText("复制")
            self.copy_btn.setIcon(QIcon())  # 清除图标
            self.save_btn.setEnabled(False)
            self.save_btn.setText("保存到文件")
            self.save_btn.setIcon(QIcon())

    def copy_log(self):
        clipboard = QApplication.clipboard()
        clipboard.setText(self.text_edit.toPlainText())
        Toast.notify(self, "崩溃日志已复制到剪贴板")

    def save_log(self):
        file_path, _ = QFileDialog.getSaveFileName(self, "保存崩溃日志", "crash_log.txt", "文本文件 (*.txt)")
        if file_path:
            try:
                with open(file_path, 'w', encoding='utf-8') as f:
                    f.write(self.text_edit.toPlainText())
                Toast.notify(self, f"日志已保存至 {file_path}")
            except Exception as e:
                QMessageBox.critical(self, "保存失败", str(e))