# src/gui/dialogs/shortcut_settings_dialog.py
from PySide6.QtWidgets import (
    QDialog, QVBoxLayout, QFormLayout, QPushButton,
    QHBoxLayout, QMessageBox, QKeySequenceEdit, QScrollArea, QWidget
)
from PySide6.QtCore import Signal
from PySide6.QtGui import QIcon
import qtawesome as qta
from src.utils.settings import Settings


class ShortcutSettingsDialog(QDialog):
    shortcuts_changed = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("快捷键设置")
        self.setModal(True)
        self.resize(500, 600)
        self.setup_ui()
        self.load_shortcuts()

    def setup_ui(self):
        layout = QVBoxLayout(self)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        container = QWidget()
        form = QFormLayout(container)

        # 所有可配置的动作，与 settings.DEFAULT_SHORTCUTS 保持一致
        self.key_edits = {}
        actions = [
            ("refresh_devices", "  刷  新  "),
            ("execute_selected", "执行选中"),
            ("clear_log", "清空日志"),
            ("search_commands", "搜索指令"),
            ("add_command", "新增指令"),
            ("edit_command", "编辑指令"),
            ("delete_command", "删除指令"),
            ("wireless", "无线联调"),
            ("scrcpy", "设备投屏"),
            ("install_app", "安装应用"),
            ("push_file", "推送文件"),
            ("app_manager", "应用管理"),
            ("device_info", "硬件信息"),
            ("hprof_dump", "堆 转 储 "),
            ("monkey", "Monkey"),
            ("crash_log", "崩溃日志"),
            ("anr_analyzer", "   ANR   "),
            ("md5_query", "   MD5  "),
            ("weak_network", "弱网模拟"),
            ("export_commands", "导出指令"),
            ("import_commands", "导入指令"),
            ("help_center", "帮助中心"),
            ("packet_capture", "网络抓包"),
            ("about", "  关  于  "),
        ]
        for action_name, label in actions:
            edit = QKeySequenceEdit()
            self.key_edits[action_name] = edit
            form.addRow(label + ":", edit)

        scroll.setWidget(container)
        layout.addWidget(scroll)

        btn_layout = QHBoxLayout()
        btn_layout.addStretch()

        # ---- 保存按钮：绿色，带软盘图标 ----
        self.save_btn = QPushButton(" 保存")
        self.save_btn.setIcon(qta.icon('fa6s.floppy-disk', color='white'))
        self.save_btn.setFixedHeight(32)
        self.save_btn.setObjectName("primaryBtn")
        self.save_btn.clicked.connect(self.save)
        btn_layout.addWidget(self.save_btn)

        btn_layout.addSpacing(10)

        # ---- 恢复默认按钮：橙色，带撤销图标 ----
        self.reset_btn = QPushButton(" 恢复默认")
        self.reset_btn.setIcon(qta.icon('fa6s.arrow-rotate-left', color='#B0761F'))
        self.reset_btn.setFixedHeight(32)
        self.reset_btn.setObjectName("warnBtn")
        self.reset_btn.clicked.connect(self.reset_defaults)
        btn_layout.addWidget(self.reset_btn)

        btn_layout.addStretch()
        layout.addLayout(btn_layout)

    def load_shortcuts(self):
        shortcuts = Settings.get_shortcuts()
        for action_name, edit in self.key_edits.items():
            ks = shortcuts.get(action_name, "")
            if ks:
                edit.setKeySequence(ks)

    def save(self):
        for action_name, edit in self.key_edits.items():
            ks = edit.keySequence().toString()
            if ks:
                Settings.set_shortcut(action_name, ks)
        self.shortcuts_changed.emit()
        QMessageBox.information(self, "成功", "快捷键已保存")
        self.accept()

    def reset_defaults(self):
        reply = QMessageBox.question(self, "恢复默认", "确定要恢复所有快捷键为默认值吗？")
        if reply == QMessageBox.Yes:
            Settings.reset_shortcuts()
            self.load_shortcuts()