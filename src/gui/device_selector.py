# src/gui/device_selector.py
"""
设备选择器模块
提供一个自定义的 QWidget 控件，用于显示当前连接的 Android 设备列表，
并允许用户选择其中一个设备。包含一个下拉框和一个刷新按钮，
可以定时刷新设备列表，并在设备变化时发射信号。
"""

from PySide6.QtWidgets import QWidget, QHBoxLayout, QComboBox, QPushButton
from PySide6.QtCore import Signal, Slot, QTimer, Qt

from src.core.adb_manager import AdbManager
import qtawesome as qta


class DeviceSelector(QWidget):
    """
    设备选择器类，继承自 QWidget。
    它包含一个下拉框和一个刷新按钮，能够显示已连接的 Android 设备，
    并在用户选择不同设备时发射 device_changed 信号。
    """

    device_changed = Signal(str)

    def __init__(self, adb_manager: AdbManager, parent=None):
        super().__init__(parent)
        self.adb_manager = adb_manager
        self.current_serial = None

        self.setup_ui()
        self.adb_manager.device_list_updated.connect(self.update_combo)

    def setup_ui(self):
        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(8)

        self.combo = QComboBox()
        self.combo.setMinimumWidth(230)
        self.combo.currentIndexChanged.connect(self.on_combo_changed)
        layout.addWidget(self.combo)

        # 刷新按钮：纯图标按钮，悬停显底
        self.refresh_btn = QPushButton()
        self.refresh_btn.setObjectName("iconBtn")
        self.refresh_btn.setIcon(qta.icon('fa6s.rotate', color='#1F1D1A'))
        self.refresh_btn.setToolTip("刷新设备列表")
        self.refresh_btn.setCursor(Qt.PointingHandCursor)
        self.refresh_btn.clicked.connect(self.refresh_devices)
        layout.addWidget(self.refresh_btn)

    def start_refresh(self):
        self.refresh_devices()
        self.timer = QTimer()
        self.timer.timeout.connect(self.refresh_devices)
        self.timer.start(3000)

    @Slot()
    def refresh_devices(self):
        self.adb_manager.refresh_devices()

    @Slot(list)
    def update_combo(self, devices):
        old_serial = self.current_serial

        self.combo.blockSignals(True)
        self.combo.clear()

        for d in devices:
            self.combo.addItem(f"{d['serial']} [{d['status']}]", d['serial'])

        new_serial = None
        if devices:
            if old_serial is not None:
                index = self.combo.findData(old_serial)
                if index >= 0:
                    self.combo.setCurrentIndex(index)
                    new_serial = old_serial
                else:
                    self.combo.setCurrentIndex(0)
                    new_serial = self.combo.itemData(0)
            else:
                self.combo.setCurrentIndex(0)
                new_serial = self.combo.itemData(0)

        self.combo.blockSignals(False)

        self.current_serial = new_serial

        if new_serial != old_serial:
            self.device_changed.emit(new_serial)

    @Slot(int)
    def on_combo_changed(self, index):
        if index >= 0:
            serial = self.combo.itemData(index)
            if serial != self.current_serial:
                self.current_serial = serial
                self.device_changed.emit(serial)

    def current_device(self):
        return self.current_serial