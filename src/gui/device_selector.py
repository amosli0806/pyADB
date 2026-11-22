# src/gui/device_selector.py
"""
设备选择器模块
提供一个自定义的 QWidget 控件，用于显示当前连接的 Android 设备列表，
并允许用户选择其中一个设备。包含一个下拉框和一个刷新按钮，
可以定时刷新设备列表，并在设备变化时发射信号。
"""

import re
import threading

from PySide6.QtWidgets import QWidget, QHBoxLayout, QComboBox, QPushButton, QLabel
from PySide6.QtCore import Signal, Slot, QTimer, Qt
from PySide6.QtGui import QColor

from src.core.adb_client import AdbClient
from src.core.adb_manager import AdbManager
import qtawesome as qta


class DeviceSelector(QWidget):
    """
    设备选择器类，继承自 QWidget。
    它包含一个下拉框和一个刷新按钮，能够显示已连接的 Android 设备，
    并在用户选择不同设备时发射 device_changed 信号。
    """

    device_changed = Signal(str)
    device_info_ready = Signal(str, str, str)   # (serial, Android 版本, wm size 原始输出)

    def __init__(self, adb_manager: AdbManager, parent=None):
        super().__init__(parent)
        self.adb_manager = adb_manager
        self.current_serial = None

        self.setup_ui()
        self.adb_manager.device_list_updated.connect(self.update_combo)
        self.device_info_ready.connect(self._on_device_info)

    def setup_ui(self):
        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(8)

        self.combo = QComboBox()
        self.combo.setMinimumWidth(230)
        self.combo.currentIndexChanged.connect(self.on_combo_changed)
        layout.addWidget(self.combo)

        # 版本 / 分辨率 chips：空着就隐藏，有设备时后台查出来再显示
        self.android_chip = QLabel()
        self.android_chip.setObjectName("deviceChip")
        self.android_chip.hide()
        layout.addWidget(self.android_chip)

        self.res_chip = QLabel()
        self.res_chip.setObjectName("deviceChip")
        self.res_chip.hide()
        layout.addWidget(self.res_chip)

        # 刷新按钮：纯图标按钮，悬停显底
        self.refresh_btn = QPushButton()
        self.refresh_btn.setObjectName("iconBtn")
        self.refresh_btn.setIcon(qta.icon('fa6s.rotate', color='#1F1D1A'))
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

        # 没设备时给一条占位文案：空下拉框看起来像界面坏了，用户不知道该干什么
        if not devices:
            self.combo.addItem(
                qta.icon('fa6s.plug-circle-xmark', color='#9C9488'),
                "未检测到设备",
                None,
            )
            self.combo.setItemData(0, QColor('#9C9488'), Qt.ForegroundRole)
            self.combo.setCurrentIndex(0)
            # 占位文案调灰：给控件单独加一条 color（palette 会被全局样式表覆盖，改不动）
            self.combo.setStyleSheet("QComboBox { color: #9C9488; }")
            self.android_chip.hide()
            self.res_chip.hide()
            self.combo.blockSignals(False)
            self.current_serial = None
            if old_serial is not None:
                self.device_changed.emit("")   # 空串=当前无设备，主窗口据此打一条日志
            return

        for d in devices:
            self.combo.addItem(f"{d['serial']} [{d['status']}]", d['serial'])

        self.combo.setStyleSheet("")      # 有设备时恢复常规文字色

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
        self._query_device_info(new_serial)   # 查到版本/分辨率后回填 chips

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

    # ---------------- 设备信息（Android 版本 / 分辨率） ----------------
    def _query_device_info(self, serial):
        if not serial:
            self.android_chip.hide()
            self.res_chip.hide()
            return

        def worker():
            version = self._shell(serial, "getprop ro.build.version.release")
            size = self._shell(serial, "wm size")
            self.device_info_ready.emit(serial, version.strip(), size.strip())

        threading.Thread(target=worker, daemon=True).start()

    def _shell(self, serial, cmd):
        """只读查询，失败就返回空串（拿不到信息不影响主流程）"""
        result = AdbClient(serial=serial).run(["shell"] + cmd.split(), timeout=6)
        return (result.stdout or "") if result.ok else ""

    @Slot(str, str, str)
    def _on_device_info(self, serial, version, size_output):
        # 查询期间用户可能已经切走，迟到的结果直接丢掉
        if serial != self.current_serial:
            return

        if version:
            self.android_chip.setText(f"Android {version}")
            self.android_chip.show()
        else:
            self.android_chip.hide()

        # wm size 可能同时给出 Physical 和 Override 两种，取最后一条（实际生效的）
        found = re.findall(r"size:\s*(\d+)x(\d+)", size_output or "")
        if found:
            w, h = found[-1]
            self.res_chip.setText(f"{w}×{h}")
            self.res_chip.show()
        else:
            self.res_chip.hide()
