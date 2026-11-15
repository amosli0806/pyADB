# src/gui/dialogs/select_commands_dialog.py
"""展示指令：勾选左侧列表里要显示的命令。

原来是个二级子菜单（显示全部 / 仅预设 / 仅自定义 / 自定义显示），层级深且语义重叠，
现在合成一个对话框：所有命令一屏列出，勾选即显示，和虫师的「展示指令」一致。
"""

from PySide6.QtCore import Qt, QSize
from PySide6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QListWidget, QListWidgetItem,
    QCheckBox, QLabel, QPushButton, QWidget
)
import qtawesome as qta

from src.core.command_manager import CommandManager


class _CommandRow(QWidget):
    """一行：复选框（命令名） + 右侧标签（预设 / 自定义）。点行内空白也能勾选。"""

    def __init__(self, name: str, is_preset: bool, parent=None):
        super().__init__(parent)
        self.setObjectName("displayRow")

        layout = QHBoxLayout(self)
        layout.setContentsMargins(8, 4, 10, 4)
        layout.setSpacing(8)

        self.checkbox = QCheckBox(name)
        self.checkbox.setChecked(True)
        layout.addWidget(self.checkbox)
        layout.addStretch()

        tag = QLabel("预设" if is_preset else "自定义")
        tag.setObjectName("presetTag" if is_preset else "customTag")
        layout.addWidget(tag)

    def is_checked(self) -> bool:
        return self.checkbox.isChecked()

    def mouseReleaseEvent(self, event):
        if event.button() == Qt.LeftButton:
            self.checkbox.toggle()
        super().mouseReleaseEvent(event)


class SelectCommandsDialog(QDialog):
    def __init__(self, command_manager: CommandManager, parent=None):
        super().__init__(parent)
        self.command_manager = command_manager
        self.checkboxes = []
        self.setWindowTitle("展示指令")
        self.resize(420, 520)
        self.setup_ui()
        self.load_commands()

    def setup_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 16, 16, 14)
        layout.setSpacing(10)

        # 标题只用窗口标题栏，正文不再重复一遍「展示指令」
        tip = QLabel("勾选的命令会显示在左侧列表，取消勾选则隐藏。")
        tip.setStyleSheet("color:#6E675D; font-size:12px;")
        layout.addWidget(tip)

        self.list_widget = QListWidget()
        self.list_widget.setObjectName("displayList")
        self.list_widget.setSpacing(0)
        layout.addWidget(self.list_widget, 1)

        btn_layout = QHBoxLayout()
        btn_layout.setSpacing(8)

        # 全选 / 全不选：次级描边
        self.toggle_all_btn = QPushButton("全不选")
        self.toggle_all_btn.setFixedHeight(32)
        self.toggle_all_btn.clicked.connect(self.toggle_all)
        btn_layout.addWidget(self.toggle_all_btn)
        btn_layout.addStretch()

        cancel_btn = QPushButton("取消")
        cancel_btn.setFixedHeight(32)
        cancel_btn.setMinimumWidth(80)
        cancel_btn.clicked.connect(self.reject)
        btn_layout.addWidget(cancel_btn)

        self.ok_btn = QPushButton(" 确定")
        self.ok_btn.setFixedHeight(32)
        self.ok_btn.setMinimumWidth(96)
        self.ok_btn.setObjectName("primaryBtn")
        self.ok_btn.setIcon(qta.icon('fa6s.check', color='#FFFFFF'))
        self.ok_btn.setDefault(True)
        self.ok_btn.clicked.connect(self.accept)
        btn_layout.addWidget(self.ok_btn)

        layout.addLayout(btn_layout)

    def load_commands(self):
        """每个命令一行；按上次保存的显示列表回显（没保存过 = 全选）"""
        from src.utils.settings import Settings
        saved_ids = None
        if Settings.load_display_filter_mode() != 'all':
            saved_ids = Settings.load_display_command_ids() or None

        self.checkboxes = []
        for cmd in self.command_manager.get_all_commands():
            row = _CommandRow(cmd.name, cmd.is_preset)
            if saved_ids is not None:
                row.checkbox.setChecked(cmd.id in saved_ids)
            row.checkbox.toggled.connect(self._update_toggle_button_text)
            item = QListWidgetItem(self.list_widget)
            # 固定行高：用 sizeHint 会贴着内容算，复选框（15px 指示器 + 内边距）容易被裁掉一半
            item.setSizeHint(QSize(0, 36))
            self.list_widget.addItem(item)
            self.list_widget.setItemWidget(item, row)
            self.checkboxes.append((cmd.id, row))

        self._update_toggle_button_text()

    def _update_toggle_button_text(self):
        if not self.checkboxes:
            return
        all_checked = all(row.is_checked() for _, row in self.checkboxes)
        self.toggle_all_btn.setText("全不选" if all_checked else "全选")

    def toggle_all(self):
        if not self.checkboxes:
            return
        all_checked = all(row.is_checked() for _, row in self.checkboxes)
        new_state = not all_checked
        for _, row in self.checkboxes:
            row.checkbox.setChecked(new_state)
        self._update_toggle_button_text()

    def get_selected_ids(self):
        return [cmd_id for cmd_id, row in self.checkboxes if row.is_checked()]
