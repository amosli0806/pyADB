# src/gui/command_list.py
"""
自定义命令列表控件模块
提供两个类：
- CommandListItemWidget: 代表列表中的一个命令项，包含复选框（含命令名称）和执行/停止按钮。
- CommandListWidget: 继承自 QListWidget，用于显示所有命令（预设和自定义），并支持选择、过滤、执行/停止等操作。
"""

import time
from PySide6.QtWidgets import (
    QListWidget, QListWidgetItem, QCheckBox, QPushButton,
    QHBoxLayout, QWidget, QApplication
)
from PySide6.QtCore import Signal, QTimer
from PySide6.QtGui import QIcon
import qtawesome as qta

from src.models.command import Command
from src.core.command_manager import CommandManager
from src.utils.settings import Settings


class CommandListItemWidget(QWidget):
    """
    自定义命令列表项控件。
    每个项显示一个命令，包含一个复选框（含命令名称，点击文字可切换）和一个按钮（执行/停止）。
    点击按钮会根据当前状态发射 execute_clicked 或 stop_clicked 信号。
    """
    # 定义三个信号
    execute_clicked = Signal(Command)   # 执行信号，参数为对应的 Command 对象
    stop_clicked = Signal(Command)      # 停止信号，参数为对应的 Command 对象
    state_changed = Signal(int, bool)   # 复选框状态变化信号，参数为 command_id, 是否选中

    def __init__(self, command: Command, show_stop_button: bool, checked: bool = False):
        """
        初始化命令列表项。
        :param command: 该列表项对应的命令对象
        :param show_stop_button: 是否显示停止按钮（如果命令支持停止，则为 True）
        :param checked: 复选框初始是否选中
        """
        super().__init__()
        self.command = command          # 保存命令对象
        self.state = 'idle'             # 状态：'idle', 'starting', 'running', 'stopping'

        # 延迟切换相关（启动中显示0.3秒，停止中显示2秒）
        self._starting_delay = 0.3      # 启动中延迟秒数
        self._stopping_delay = 2.0      # 停止中延迟秒数
        self._state_timestamp = 0.0     # 进入中间状态（starting/stopping）的时间戳
        self._pending_state = None      # 待应用的最终状态（running/idle）
        self._delay_timer = QTimer()
        self._delay_timer.setSingleShot(True)
        self._delay_timer.timeout.connect(self._apply_pending_state)

        # 创建水平布局作为该 widget 的主布局
        layout = QHBoxLayout(self)
        layout.setContentsMargins(2, 2, 2, 2)

        # 复选框，包含命令名称，点击文字即可切换勾选
        self.checkbox = QCheckBox(command.name)
        self.checkbox.setChecked(checked)
        self.checkbox.toggled.connect(self._on_checkbox_toggled)
        layout.addWidget(self.checkbox)

        layout.addStretch()  # 弹性空间，让按钮靠右

        # 如果支持停止按钮，则创建按钮并连接点击事件
        if show_stop_button:
            self.exec_btn = QPushButton("执行")
            self.exec_btn.setObjectName("commandExecuteButton")
            self.exec_btn.setFixedWidth(60)
            self.exec_btn.setIcon(qta.icon('fa6s.play', color='white'))
            self.exec_btn.clicked.connect(self.on_click)
            layout.addWidget(self.exec_btn)
        else:
            self.exec_btn = None

    def _on_checkbox_toggled(self, checked: bool):
        """复选框状态变化时发射信号"""
        self.state_changed.emit(self.command.id, checked)

    def on_click(self):
        """按钮点击事件的槽函数，根据当前状态执行相应操作"""
        if self.state == 'idle':
            # 空闲 → 启动中
            self.set_state('starting')
            self.execute_clicked.emit(self.command)
        elif self.state == 'running':
            # 运行中 → 停止中
            self.set_state('stopping')
            self.stop_clicked.emit(self.command)
        # 其他状态（starting, stopping）按钮禁用，不会触发点击

    def set_state(self, state: str):
        """
        设置命令状态，并根据需要延迟切换。
        :param state: 'idle', 'starting', 'running', 'stopping'
        """
        # 如果是中间状态（starting/stopping），立即应用，并记录时间
        if state in ('starting', 'stopping'):
            self._apply_state(state)
            self._state_timestamp = time.time()
            # 取消任何待处理的延迟切换
            self._delay_timer.stop()
            self._pending_state = None
            # 强制刷新UI（确保立即显示）
            QApplication.processEvents()
        else:
            # state 为 'idle' 或 'running'
            if self.state in ('starting', 'stopping'):
                # 确定当前中间状态对应的延迟时长
                delay = self._starting_delay if self.state == 'starting' else self._stopping_delay
                elapsed = time.time() - self._state_timestamp
                if elapsed < delay:
                    # 未满延迟时间，设置待处理状态并在剩余时间后切换
                    self._pending_state = state
                    remaining_ms = int((delay - elapsed) * 1000) + 10
                    self._delay_timer.start(remaining_ms)
                    return
            # 否则立即应用
            self._apply_state(state)
            self._delay_timer.stop()
            self._pending_state = None

    def _apply_pending_state(self):
        """定时器超时后，应用待处理的状态"""
        if self._pending_state:
            self._apply_state(self._pending_state)
            self._pending_state = None

    def _apply_state(self, state: str):
        self.state = state
        if self.exec_btn:
            if state == 'idle':
                self.exec_btn.setText("执行")
                self.exec_btn.setIcon(qta.icon('fa6s.play', color='white'))
                self.exec_btn.setStyleSheet(
                    """
                    QPushButton {
                        background-color: #27ae60;
                        color: white;
                        border: none;
                        border-radius: 6px;
                    }
                    QPushButton:hover {
                        background-color: #2ecc71;
                    }
                    QPushButton:pressed {
                        background-color: #1e8449;
                    }
                """
                )
                self.exec_btn.setEnabled(True)
            elif state == 'starting':
                self.exec_btn.setText("启动中…")
                self.exec_btn.setIcon(QIcon())  # 清除图标
                self.exec_btn.setStyleSheet(
                    """
                    QPushButton {
                        background-color: #f39c12;
                        color: white;
                        border: none;
                        border-radius: 6px;
                    }
                """
                )
                self.exec_btn.setEnabled(False)
            elif state == 'running':
                self.exec_btn.setText("停止")
                self.exec_btn.setIcon(qta.icon('fa6s.stop', color='white'))
                self.exec_btn.setStyleSheet(
                    """
                    QPushButton {
                        background-color: #e74c3c;
                        color: white;
                        border: none;
                        border-radius: 6px;
                    }
                    QPushButton:hover {
                        background-color: #f05a4a;
                    }
                    QPushButton:pressed {
                        background-color: #c0392b;
                    }
                """
                )
                self.exec_btn.setEnabled(True)
            elif state == 'stopping':
                self.exec_btn.setText("停止中…")
                self.exec_btn.setIcon(QIcon())  # 清除图标
                self.exec_btn.setStyleSheet(
                    """
                    QPushButton {
                        background-color: #f39c12;
                        color: white;
                        border: none;
                        border-radius: 6px;
                    }
                """
                )
                self.exec_btn.setEnabled(False)

    def is_checked(self):
        """返回复选框是否被选中"""
        return self.checkbox.isChecked()


class CommandListWidget(QListWidget):
    """
    命令列表控件，继承自 QListWidget。
    用于显示所有命令（包括预设和自定义），每个命令项是 CommandListItemWidget。
    提供刷新列表、获取选中命令、设置命令状态、过滤等功能。
    """
    # 定义两个信号，当用户点击某个命令项的执行或停止按钮时发射
    execute_signal = Signal(Command)   # 执行信号
    stop_signal = Signal(Command)      # 停止信号

    def __init__(self, command_manager: CommandManager, parent=None):
        """
        初始化命令列表控件。
        :param command_manager: CommandManager 实例，用于获取命令列表
        :param parent: 父窗口（通常是主窗口）
        """
        super().__init__(parent)
        self.command_manager = command_manager
        self.commands = []                        # 当前显示的命令列表（Command 对象）
        self.states = {}                          # 记录每个命令的状态，key: command.id, value: state string
        self.refresh()

    def refresh(self):
        """刷新列表：从命令管理器重新获取所有命令，并重建列表项，同时恢复勾选状态"""
        self.clear()
        self.commands = self.command_manager.get_all_commands()
        # 加载保存的勾选状态
        checked_ids = set(Settings.load_checked_commands())

        for cmd in self.commands:
            item = QListWidgetItem(self)
            checked = cmd.id in checked_ids
            widget = CommandListItemWidget(cmd, cmd.show_stop_button, checked)
            widget.execute_clicked.connect(self.on_item_execute)
            widget.stop_clicked.connect(self.on_item_stop)
            widget.state_changed.connect(self._on_item_state_changed)
            item.setSizeHint(widget.sizeHint())
            self.addItem(item)
            self.setItemWidget(item, widget)
            # 如果该命令ID在状态字典中有记录，则恢复状态
            if cmd.id in self.states:
                widget.set_state(self.states[cmd.id])
            else:
                widget.set_state('idle')

    def _on_item_state_changed(self, cmd_id, checked):
        """当某个命令项的复选框状态变化时，保存所有勾选状态"""
        self._save_checked_state()

    def _save_checked_state(self):
        """收集所有勾选的命令 ID 并保存到设置中"""
        checked_ids = []
        for i in range(self.count()):
            item = self.item(i)
            widget = self.itemWidget(item)
            if widget and widget.is_checked():
                checked_ids.append(widget.command.id)
        Settings.save_checked_commands(checked_ids)

    def on_item_execute(self, command):
        """内部槽函数，当某个列表项的 execute_clicked 信号触发时调用。"""
        self.execute_signal.emit(command)

    def on_item_stop(self, command):
        """内部槽函数，当某个列表项的 stop_clicked 信号触发时调用。"""
        self.stop_signal.emit(command)

    def get_selected_commands(self):
        """获取所有被选中的命令（即复选框被勾选的项）。"""
        selected = []
        for i in range(self.count()):
            item = self.item(i)
            widget = self.itemWidget(item)
            if widget and widget.is_checked():
                selected.append(widget.command)
        return selected

    def current_selected_command(self):
        """获取当前选中的列表项（通过鼠标点击高亮的项）对应的命令对象。"""
        current = self.currentItem()
        if current:
            widget = self.itemWidget(current)
            if widget:
                return widget.command
        return None

    def set_command_state(self, command_id, state):
        """
        设置指定命令的状态，并更新 UI。
        :param command_id: 命令 ID
        :param state: 'idle', 'starting', 'running', 'stopping'
        """
        self.states[command_id] = state
        # 遍历列表找到对应的 widget 并更新
        for i in range(self.count()):
            item = self.item(i)
            widget = self.itemWidget(item)
            if widget and widget.command.id == command_id:
                widget.set_state(state)
                break

    def get_running_ids(self):
        """
        返回当前正在运行（running 或 starting）的命令 ID 集合，用于外部判断。
        注意：stopping 状态不算运行中。
        """
        return {cmd_id for cmd_id, st in self.states.items() if st in ('running', 'starting')}

    def set_filter(self, show_preset=True, show_custom=True, keyword=None):
        """
        设置列表的过滤条件，隐藏不符合条件的项。
        :param show_preset: 是否显示预设命令
        :param show_custom: 是否显示自定义命令
        :param keyword: 搜索关键词，在命令名称和描述中匹配（不区分大小写）
        """
        for i in range(self.count()):
            item = self.item(i)
            widget = self.itemWidget(item)
            if widget:
                cmd = widget.command
                type_ok = (show_preset and cmd.is_preset) or (show_custom and not cmd.is_preset)
                keyword_ok = True
                if keyword:
                    keyword_lower = keyword.lower()
                    name_match = keyword_lower in cmd.name.lower()
                    desc_match = keyword_lower in cmd.description.lower() if cmd.description else False
                    keyword_ok = name_match or desc_match
                item.setHidden(not (type_ok and keyword_ok))

    def set_filter_by_ids(self, ids):
        """根据命令ID列表显示/隐藏列表项。如果 ids 为空，则显示所有命令。"""
        if not ids:
            for i in range(self.count()):
                item = self.item(i)
                item.setHidden(False)
            return
        for i in range(self.count()):
            item = self.item(i)
            widget = self.itemWidget(item)
            if widget:
                cmd = widget.command
                item.setHidden(cmd.id not in ids)