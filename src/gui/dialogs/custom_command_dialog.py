# src/gui/dialogs/custom_command_dialog.py
"""
自定义命令对话框模块
提供一个对话框窗口，用于创建新的自定义命令或编辑已有的命令。
用户可以输入命令名称、ADB命令内容、描述，并可设置定时执行、循环执行等选项。
"""

from PySide6.QtWidgets import (
    QLabel, QDialog, QVBoxLayout, QFormLayout,
    QLineEdit, QTextEdit, QCheckBox, QSpinBox,
    QPushButton, QHBoxLayout
)
from src.models.command import Command


class CustomCommandDialog(QDialog):
    """
    自定义命令对话框类。
    当用户点击“新增指令”或双击现有指令时弹出此对话框，用于编辑命令属性。
    """

    def __init__(self, parent=None, command: Command = None):
        super().__init__(parent)
        self.command = command

        self.setWindowTitle("新增指令" if not command else "编辑指令")
        self.setModal(True)
        self.resize(530, 520)
        self.setup_ui()
        if command:
            self.load_command()

    def setup_ui(self):
        layout = QVBoxLayout(self)

        # ---- 注意事项标签（无线联调警告样式） ----
        hint_label = QLabel(
            "💡 注意事项：\n"
            "1. pull 命令示例：pull 本地文件夹路径 {output_dir}/{safe_device_serial}/\n"
            "2. 请勿在命令中包含设备选择参数（如 -s），程序会自动处理"
        )
        hint_label.setWordWrap(True)
        hint_label.setStyleSheet(
            "background-color: #FBEFEC; color: #8C3A2B; padding: 8px; border-radius: 4px; font-weight: bold;"
        )
        layout.addWidget(hint_label)

        form = QFormLayout()

        # ---- 功能名称 ----
        self.name_edit = QLineEdit()
        self.name_edit.setMaxLength(18)
        self.name_edit.setPlaceholderText("最多18个汉字")
        form.addRow("功能名称:", self.name_edit)

        # ---- ADB命令内容 ----
        self.command_edit = QTextEdit()
        self.command_edit.setPlaceholderText("输入ADB命令，多条用;分隔")
        form.addRow("命令内容:", self.command_edit)

        # ---- 功能描述 ----
        self.desc_edit = QLineEdit()
        form.addRow("功能描述:", self.desc_edit)

        # ---- 定时执行复选框 ----
        self.timer_check = QCheckBox("定时执行")
        form.addRow("", self.timer_check)

        # ---- 执行间隔数字框 ----
        self.interval_spin = QSpinBox()
        self.interval_spin.setSuffix(" 秒")
        self.interval_spin.setMaximum(10800)
        self.interval_spin.setValue(3600)
        self.interval_spin.setEnabled(False)
        self.timer_check.toggled.connect(self.interval_spin.setEnabled)
        form.addRow("执行间隔:", self.interval_spin)

        # ---- 循环执行复选框 ----
        self.loop_check = QCheckBox("循环执行")
        form.addRow("", self.loop_check)

        # ---- 循环次数数字框 ----
        self.loop_count_spin = QSpinBox()
        self.loop_count_spin.setMinimum(1)
        self.loop_count_spin.setMaximum(500000)
        self.loop_count_spin.setValue(20)
        self.loop_count_spin.setEnabled(False)
        self.loop_check.toggled.connect(self.loop_count_spin.setEnabled)
        form.addRow("循环次数:", self.loop_count_spin)

        # ---- 显示执行/停止按钮复选框 ----
        self.stop_btn_check = QCheckBox("显示执行/停止按钮")
        self.stop_btn_check.setChecked(True)
        form.addRow("", self.stop_btn_check)

        # ---- 保存输出到文件复选框 ----
        self.save_output_check = QCheckBox("保存输出到文件")
        form.addRow("", self.save_output_check)

        # ---- 无超时执行复选框 ----
        self.no_timeout_check = QCheckBox("无超时执行（适用于长时间运行的命令）")
        self.no_timeout_check.setToolTip("勾选后该命令不受30秒超时限制")
        form.addRow("", self.no_timeout_check)

        # ---- 预置命令提示标签 ----
        self.preset_hint = QLabel("（预置命令不可修改循环/定时设置）")
        self.preset_hint.setStyleSheet("color: gray; font-size: 9pt;")
        self.preset_hint.setVisible(False)
        form.addRow("", self.preset_hint)

        layout.addLayout(form)

        # ---- 按钮区域：居中展示 ----
        btn_layout = QHBoxLayout()
        btn_layout.addStretch()

        # ---- 确定按钮：绿色，宽度100px（样式表控制） ----
        self.ok_btn = QPushButton("确定")
        self.ok_btn.setFixedHeight(32)
        self.ok_btn.setObjectName("primaryBtn")
        self.ok_btn.clicked.connect(self.accept)
        btn_layout.addWidget(self.ok_btn)

        btn_layout.addSpacing(10)

        # ---- 取消按钮：灰色，宽度100px（样式表控制） ----
        self.cancel_btn = QPushButton("取消")
        self.cancel_btn.setFixedHeight(32)
        self.cancel_btn.clicked.connect(self.reject)
        btn_layout.addWidget(self.cancel_btn)

        btn_layout.addStretch()
        layout.addLayout(btn_layout)

    def load_command(self):
        self.name_edit.setText(self.command.name)
        self.command_edit.setPlainText(self.command.command_text)
        self.desc_edit.setText(self.command.description)
        if self.command.interval:
            self.timer_check.setChecked(True)
            self.interval_spin.setValue(self.command.interval)
        if self.command.loop_count:
            self.loop_check.setChecked(True)
            self.loop_count_spin.setValue(self.command.loop_count)
        self.stop_btn_check.setChecked(self.command.show_stop_button)
        self.save_output_check.setChecked(self.command.save_output)
        self.no_timeout_check.setChecked(self.command.no_timeout)
        if self.command.is_preset:
            self._apply_preset_restrictions()

    def _apply_preset_restrictions(self):
        self.timer_check.setEnabled(False)
        self.interval_spin.setEnabled(False)
        self.loop_check.setEnabled(False)
        self.loop_count_spin.setEnabled(False)
        self.preset_hint.setVisible(True)

    def get_command(self):
        name = self.name_edit.text().strip()
        cmd_text = self.command_edit.toPlainText().strip()
        desc = self.desc_edit.text().strip()

        interval = self.interval_spin.value() if self.timer_check.isChecked() else None
        loop_count = self.loop_count_spin.value() if self.loop_check.isChecked() else None

        show_stop = self.stop_btn_check.isChecked()
        save_output = self.save_output_check.isChecked()
        no_timeout = self.no_timeout_check.isChecked()

        cmd_id = self.command.id if self.command else None
        is_preset = self.command.is_preset if self.command else False

        if is_preset:
            interval = None
            loop_count = None

        return Command(
            id=cmd_id,
            name=name,
            command_text=cmd_text,
            description=desc,
            interval=interval,
            loop_count=loop_count,
            show_stop_button=show_stop,
            is_preset=is_preset,
            save_output=save_output,
            no_timeout=no_timeout
        )