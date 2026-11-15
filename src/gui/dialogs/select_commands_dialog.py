from PySide6.QtWidgets import (QDialog, QVBoxLayout, QHBoxLayout,
                               QListWidget, QListWidgetItem, QCheckBox,
                               QPushButton, QLabel)
from PySide6.QtCore import Qt


class SelectCommandsDialog(QDialog):
    def __init__(self, command_manager, parent=None):
        super().__init__(parent)
        self.command_manager = command_manager
        self.setWindowTitle("自定义显示命令")
        self.setModal(True)
        self.resize(400, 500)
        self.setup_ui()
        self.load_commands()

    def setup_ui(self):
        layout = QVBoxLayout(self)

        # 提示标签
        tip = QLabel("勾选您希望在左侧列表中显示的命令（取消勾选则隐藏）")
        tip.setWordWrap(True)
        layout.addWidget(tip)

        # 列表控件
        self.list_widget = QListWidget()
        # 增加项的内边距和间距
        self.list_widget.setSpacing(2)  # 项之间垂直间距
        layout.addWidget(self.list_widget)

        # 按钮区域：两个按钮居中
        btn_layout = QHBoxLayout()
        btn_layout.setAlignment(Qt.AlignCenter)

        # ---- 全选/全不选按钮：蓝色 ----
        self.toggle_all_btn = QPushButton("全选")
        self.toggle_all_btn.setFixedHeight(32)
        self.toggle_all_btn.clicked.connect(self.toggle_all)
        btn_layout.addWidget(self.toggle_all_btn)

        # ---- 确定按钮：绿色 ----
        self.ok_btn = QPushButton("确定")
        self.ok_btn.setFixedHeight(32)
        self.ok_btn.setObjectName("primaryBtn")
        self.ok_btn.clicked.connect(self.accept)
        btn_layout.addWidget(self.ok_btn)

        layout.addLayout(btn_layout)

    def load_commands(self):
        """加载所有命令到列表中，每个命令是一个复选框"""
        self.checkboxes = []
        for cmd in self.command_manager.get_all_commands():
            item = QListWidgetItem(self.list_widget)
            label_text = f"{cmd.name}  {'[预设]' if cmd.is_preset else '[自定义]'}"
            cb = QCheckBox(label_text)
            cb.setChecked(True)
            self.list_widget.addItem(item)
            self.list_widget.setItemWidget(item, cb)
            self.checkboxes.append((cmd.id, cb))

        self._update_toggle_button_text()

    def _update_toggle_button_text(self):
        if not self.checkboxes:
            return
        all_checked = all(cb.isChecked() for _, cb in self.checkboxes)
        self.toggle_all_btn.setText("全不选" if all_checked else "全选")

    def toggle_all(self):
        if not self.checkboxes:
            return
        all_checked = all(cb.isChecked() for _, cb in self.checkboxes)
        new_state = not all_checked
        for _, cb in self.checkboxes:
            cb.setChecked(new_state)
        self._update_toggle_button_text()

    def get_selected_ids(self):
        return [cmd_id for cmd_id, cb in self.checkboxes if cb.isChecked()]