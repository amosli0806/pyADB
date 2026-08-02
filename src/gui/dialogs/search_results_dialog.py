# src/gui/dialogs/search_results_dialog.py
"""
搜索结果对话框模块
当用户在搜索框中输入关键词并搜索时，会弹出此对话框，
以单个列表的形式展示所有匹配的命令（预设、自定义、ADB 命令库）。
用户可以直接点击“执行”按钮执行某个命令，或者对于 ADB 命令库中的命令，可以点击“复制”将其复制到剪贴板。
"""

from PySide6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QLabel,
    QListWidget, QListWidgetItem, QPushButton,
    QWidget, QSizePolicy
)
from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QFont, QIcon
import qtawesome as qta


class SearchResultsDialog(QDialog):
    """
    搜索结果对话框类。
    显示所有类别的搜索结果（预设、自定义、ADB 命令库）在同一个列表中，
    并提供执行或复制命令的交互。
    """

    # 定义两个信号，用于与父窗口（通常是主窗口）通信
    execute_command = Signal(object)   # object 实际上是指 Command 对象
    copy_command = Signal(str)

    def __init__(self, search_results, parent=None):
        super().__init__(parent)
        self.search_results = search_results
        self.setWindowTitle("搜索结果")
        self.resize(600, 450)
        self.setup_ui()
        self.populate_results()

    def setup_ui(self):
        layout = QVBoxLayout(self)

        # 搜索关键词提示标签
        self.keyword_label = QLabel()
        self.keyword_label.setStyleSheet("font-weight: bold; color: #2c3e50;")
        layout.addWidget(self.keyword_label)

        # 单个列表控件
        self.list_widget = QListWidget()
        self.list_widget.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)  # 禁用水平滚动条
        layout.addWidget(self.list_widget)

        # 底部已无按钮

    def set_keyword(self, keyword):
        self.keyword_label.setText(f"搜索关键词：{keyword}")

    def populate_results(self):
        """将所有结果按顺序添加到列表中"""
        # 预置命令
        for cmd in self.search_results.get("preset", []):
            item = QListWidgetItem()
            widget = self.create_item_widget(cmd, "预设")
            item.setSizeHint(widget.sizeHint())
            self.list_widget.addItem(item)
            self.list_widget.setItemWidget(item, widget)

        # 自定义命令
        for cmd in self.search_results.get("custom", []):
            item = QListWidgetItem()
            widget = self.create_item_widget(cmd, "自定义")
            item.setSizeHint(widget.sizeHint())
            self.list_widget.addItem(item)
            self.list_widget.setItemWidget(item, widget)

        # ADB 命令库
        for cmd in self.search_results.get("adb_library", []):
            item = QListWidgetItem()
            widget = self.create_adb_item_widget(cmd)
            item.setSizeHint(widget.sizeHint())
            self.list_widget.addItem(item)
            self.list_widget.setItemWidget(item, widget)

    def create_item_widget(self, cmd, cmd_type):
        """创建预设或自定义命令的条目控件"""
        widget = QWidget()
        layout = QVBoxLayout(widget)
        layout.setContentsMargins(5, 5, 5, 5)

        # 标题行：类型标签 + 命令名称
        name_label = QLabel(f"【{cmd_type}】{cmd.name}")
        name_font = QFont()
        name_font.setBold(True)
        name_label.setFont(name_font)
        layout.addWidget(name_label)

        # 描述
        if cmd.description:
            desc_label = QLabel(cmd.description)
            desc_label.setStyleSheet("color: #7f8c8d; font-size: 10pt;")
            layout.addWidget(desc_label)

        # 命令文本（自动换行）
        cmd_label = QLabel(f"命令：{cmd.command_text}")
        cmd_label.setStyleSheet("color: #2980b9; font-family: monospace;")
        cmd_label.setWordWrap(True)
        cmd_label.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Minimum)
        layout.addWidget(cmd_label)

        # 执行按钮
        btn_layout = QHBoxLayout()
        execute_btn = QPushButton(" 执行")
        execute_btn.setIcon(qta.icon('fa6s.play', color='white'))
        execute_btn.setFixedHeight(30)
        execute_btn.setStyleSheet("""
            QPushButton {
                background-color: #27ae60;
                color: white;
                border: none;
                border-radius: 6px;
                min-width: 80px;
            }
            QPushButton:hover {
                background-color: #2ecc71;
            }
            QPushButton:pressed {
                background-color: #1e8449;
            }
        """)
        execute_btn.clicked.connect(lambda: self.execute_command.emit(cmd))
        btn_layout.addStretch()
        btn_layout.addWidget(execute_btn)
        layout.addLayout(btn_layout)

        return widget

    def create_adb_item_widget(self, cmd):
        """创建 ADB 命令库的条目控件"""
        widget = QWidget()
        layout = QVBoxLayout(widget)
        layout.setContentsMargins(5, 5, 5, 5)

        # 头部：命令（自动换行） + 分类标签
        header_layout = QHBoxLayout()
        cmd_label = QLabel(cmd["command"])
        cmd_font = QFont()
        cmd_font.setFamily("Courier New")
        cmd_font.setBold(True)
        cmd_label.setFont(cmd_font)
        cmd_label.setStyleSheet("color: #27ae60;")
        cmd_label.setWordWrap(True)
        cmd_label.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Minimum)

        category_label = QLabel(f"[{cmd['category']}]")
        category_label.setStyleSheet("color: #7f8c8d; font-size: 9pt;")
        category_label.setAlignment(Qt.AlignRight)
        category_label.setSizePolicy(QSizePolicy.Minimum, QSizePolicy.Preferred)

        header_layout.addWidget(cmd_label, 1)
        header_layout.addWidget(category_label, 0)
        layout.addLayout(header_layout)

        # 描述（自动换行）
        desc_label = QLabel(cmd["description"])
        desc_label.setStyleSheet("color: #34495e; font-size: 10pt;")
        desc_label.setWordWrap(True)
        layout.addWidget(desc_label)

        # 复制按钮
        btn_layout = QHBoxLayout()
        copy_btn = QPushButton(" 复制")
        copy_btn.setIcon(qta.icon('fa6s.copy', color='white'))
        copy_btn.setFixedHeight(30)
        copy_btn.setStyleSheet("""
            QPushButton {
                background-color: #3498db;
                color: white;
                border: none;
                border-radius: 6px;
                min-width: 80px;
            }
            QPushButton:hover {
                background-color: #5dade2;
            }
            QPushButton:pressed {
                background-color: #2980b9;
            }
        """)
        copy_btn.clicked.connect(lambda: self.copy_command.emit(cmd["command"]))
        btn_layout.addStretch()
        btn_layout.addWidget(copy_btn)
        layout.addLayout(btn_layout)

        return widget