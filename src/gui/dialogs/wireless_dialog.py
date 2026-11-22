# src/gui/dialogs/wireless_dialog.py
"""
无线连接对话框模块
提供一个对话框，用于通过 ADB 无线连接 Android 设备。
用户可以获取当前有线连接设备的 IP 地址，然后输入或自动填入 IP 进行无线连接，
或者断开已有的无线连接。
"""

from PySide6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QLabel,
    QLineEdit, QPushButton, QMessageBox
)
from PySide6.QtCore import Qt, Slot
from PySide6.QtGui import QIcon
import qtawesome as qta

from src.core.adb_client import AdbClient
from src.core.adb_manager import AdbManager
from src.core.wireless_manager import WirelessManager
from src.gui.toast import Toast


class WirelessDialog(QDialog):
    """
    无线连接对话框类。
    允许用户通过有线连接获取设备的 IP 地址，然后通过无线 ADB 连接到该设备。
    同时支持断开当前无线连接。
    """

    def __init__(self, adb_manager: AdbManager, current_device: str, parent=None):
        super().__init__(parent)
        self.adb_manager = adb_manager
        self.wireless_manager = WirelessManager(adb_manager)
        self.current_device = current_device
        self.adb = AdbClient(serial=current_device)
        self.setWindowTitle("无线联调")
        self.setModal(True)
        self.resize(480, 180)
        self.setup_ui()
        self.update_state()
        self._update_version_warning()
        # 清除 IP 输入框的焦点，打开时不自动聚焦
        self.ip_edit.clearFocus()

    def setup_ui(self):
        layout = QVBoxLayout(self)

        # ---- 警告标签（Android 12 不支持） ----
        self.version_warning_label = QLabel(
            "注意事项：本项目不支持 Android 12 系统，无线联调功能在 Android 12 设备上无法使用。"
        )
        self.version_warning_label.setStyleSheet(
            "background-color: #FBEFEC; color: #8C3A2B; padding: 8px; border-radius: 4px; font-weight: bold;"
        )
        self.version_warning_label.setWordWrap(True)
        self.version_warning_label.hide()
        layout.addWidget(self.version_warning_label)

        # ---- IP输入行 ----
        ip_layout = QHBoxLayout()
        ip_layout.addWidget(QLabel(" 设备 IP :"))
        self.ip_edit = QLineEdit()
        self.ip_edit.setPlaceholderText("例如 192.168.1.100")
        self.ip_edit.setFocusPolicy(Qt.ClickFocus)  # 只有点击时才获得焦点
        ip_layout.addWidget(self.ip_edit)

        # ---- 获取按钮：蓝色，带 WiFi 图标 ----
        self.get_ip_btn = QPushButton(" 获取")
        self.get_ip_btn.setIcon(qta.icon('fa6s.wifi', color='#1F1D1A'))
        self.get_ip_btn.setFixedHeight(32)
        self.get_ip_btn.clicked.connect(self.on_get_ip)
        ip_layout.addWidget(self.get_ip_btn)

        layout.addLayout(ip_layout)

        # ---- 按钮行 ----
        btn_layout = QHBoxLayout()
        btn_layout.addStretch()

        # ---- 连接按钮：绿色，带链接图标 ----
        self.connect_btn = QPushButton(" 连接")
        self.connect_btn.setIcon(qta.icon('fa6s.link', color='white'))
        self.connect_btn.setFixedHeight(32)
        self.connect_btn.setObjectName("primaryBtn")
        self.connect_btn.clicked.connect(self.on_connect)

        # ---- 断开按钮：红色，带禁止图标 ----
        self.disconnect_btn = QPushButton(" 断开")
        self.disconnect_btn.setIcon(qta.icon('fa6s.ban', color='white'))
        self.disconnect_btn.setFixedHeight(32)
        self.disconnect_btn.setObjectName("stopBtn")
        self.disconnect_btn.clicked.connect(self.on_disconnect)

        btn_layout.addWidget(self.connect_btn)
        btn_layout.addSpacing(10)
        btn_layout.addWidget(self.disconnect_btn)
        btn_layout.addStretch()
        layout.addLayout(btn_layout)

        # ---- 状态标签 ----
        self.status_label = QLabel()
        layout.addWidget(self.status_label)

    def _get_device_version(self) -> str:
        """获取当前选中设备的 Android 版本号，若失败返回空字符串"""
        if not self.current_device:
            return ""
        result = self.adb.shell("getprop ro.build.version.release", timeout=5)
        return result.stdout.strip() if result.ok else ""

    def _update_version_warning(self):
        """检查当前设备版本，若为 Android 12 则显示警告并禁用相关按钮"""
        version = self._get_device_version()
        is_android_12 = version.startswith("12")
        if is_android_12:
            self.version_warning_label.show()
            self._set_button_text(self.get_ip_btn, False, "获取")
            self.get_ip_btn.setEnabled(False)
            self._set_button_text(self.connect_btn, False, "连接")
            self.connect_btn.setEnabled(False)
            self.status_label.setText("")
        else:
            self.version_warning_label.hide()
            self.get_ip_btn.setEnabled(True)
            self.connect_btn.setEnabled(True)
            self.status_label.setText("")

    def _set_button_text(self, button, enabled: bool, text: str):
        """
        根据启用状态设置按钮的文本和图标。
        启用时显示带空格的文字并设置图标，禁用时仅显示纯文本并移除图标。
        """
        if enabled:
            # 启用：显示带空格的文字 + 图标
            if button == self.get_ip_btn:
                button.setText(" 获取")
                button.setIcon(qta.icon('fa6s.wifi', color='#1F1D1A'))
            elif button == self.connect_btn:
                button.setText(" 连接")
                button.setIcon(qta.icon('fa6s.link', color='white'))
            elif button == self.disconnect_btn:
                button.setText(" 断开")
                button.setIcon(qta.icon('fa6s.ban', color='white'))
        else:
            # 禁用：只显示纯文字，移除图标
            button.setText(text)
            button.setIcon(QIcon())  # 清除图标

    def update_state(self):
        """
        更新界面控件的启用状态和显示内容，根据当前是否有无线设备连接。
        同时会触发版本检测。
        """
        self._update_version_warning()

        wireless_connected = self.wireless_manager.has_wireless_connected()

        if wireless_connected:
            ip = self.wireless_manager.get_current_wireless_ip()
            self.ip_edit.setText(ip)
            self.ip_edit.setEnabled(False)
            self._set_button_text(self.get_ip_btn, False, "获取")
            self.get_ip_btn.setEnabled(False)
            self._set_button_text(self.connect_btn, False, "连接")
            self.connect_btn.setEnabled(False)
            self._set_button_text(self.disconnect_btn, True, "断开")
            self.disconnect_btn.setEnabled(True)
            self.status_label.setText("已连接无线设备")
        else:
            self.ip_edit.setEnabled(True)
            version = self._get_device_version()
            is_android_12 = version.startswith("12")

            if not is_android_12:
                self._set_button_text(self.get_ip_btn, True, "获取")
                self.get_ip_btn.setEnabled(True)
                self._set_button_text(self.connect_btn, True, "连接")
                self.connect_btn.setEnabled(bool(self.ip_edit.text().strip()))
                self._set_button_text(self.disconnect_btn, False, "断开")
                self.disconnect_btn.setEnabled(False)
                self.status_label.setText("未连接无线设备")
            else:
                self._set_button_text(self.get_ip_btn, False, "获取")
                self.get_ip_btn.setEnabled(False)
                self._set_button_text(self.connect_btn, False, "连接")
                self.connect_btn.setEnabled(False)
                self._set_button_text(self.disconnect_btn, False, "断开")
                self.disconnect_btn.setEnabled(False)

    @Slot()
    def on_get_ip(self):
        """获取设备 IP（能进入此页面说明设备已存在，无需重复判断）"""
        ip = self.wireless_manager.get_device_ip(self.current_device)
        if ip:
            self.ip_edit.setText(ip)
            self.connect_btn.setEnabled(True)
            self.status_label.setText("已获取IP，可点击连接")
        else:
            QMessageBox.critical(self, "错误", "无法获取设备IP，请检查设备连接")

    @Slot()
    def on_connect(self):
        ip = self.ip_edit.text().strip()
        if not ip:
            Toast.notify(self, "请输入IP地址")
            return

        success, msg = self.wireless_manager.connect(ip, serial=self.current_device)
        if success:
            Toast.notify(self, msg)
            self.update_state()
        else:
            QMessageBox.critical(self, "连接失败", msg)

    @Slot()
    def on_disconnect(self):
        success, msg = self.wireless_manager.disconnect()
        if success:
            Toast.notify(self, msg)
            self.update_state()
        else:
            QMessageBox.critical(self, "失败", msg)