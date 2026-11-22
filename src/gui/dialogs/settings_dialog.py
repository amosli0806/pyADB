# src/gui/dialogs/settings_dialog.py
"""设置面板。

布局参考虫师：左侧导航树 + 右侧内容页 + 底部保存/取消。
目前两页 —— 输出目录、快捷键；以后加设置项只需再加一个 item + 一个 _build_xxx_page。

为什么把输出目录放进来：原来它占着顶部工具栏一整行，只为了显示一个路径，
现在路径只在设置里改，工具栏让位给设备/菜单这些高频操作。
"""

import os

from PySide6.QtCore import Qt
from PySide6.QtGui import QKeySequence
from PySide6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QLabel, QPushButton, QLineEdit,
    QFileDialog, QTreeWidget, QTreeWidgetItem, QStackedWidget,
    QFormLayout, QKeySequenceEdit, QScrollArea, QWidget, QMessageBox
)
import qtawesome as qta

from src.utils.settings import Settings
from src.gui.toast import Toast

# 可配置快捷键的动作（与 Settings.DEFAULT_SHORTCUTS 的 key 对齐）
SHORTCUT_ACTIONS = [
    ("refresh_devices", "刷新设备"),
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
    ("hprof_dump", "堆转储"),
    ("monkey", "Monkey"),
    ("crash_log", "崩溃日志"),
    ("anr_analyzer", "ANR 分析"),
    ("md5_query", "MD5 查询"),
    ("weak_network", "弱网模拟"),
    ("packet_capture", "网络抓包"),
    ("export_commands", "导出指令"),
    ("import_commands", "导入指令"),
    ("help_center", "帮助中心"),
    ("about", "关于"),
]


class SettingsDialog(QDialog):
    """设置：左导航 + 右内容。点「保存」才写回 Settings。"""

    def __init__(self, current_output_dir: str, parent=None):
        super().__init__(parent)
        self.setWindowTitle("设置")
        self.resize(720, 560)
        self.output_dir = current_output_dir
        self.key_edits = {}
        self._build_ui()
        self._load_shortcuts()

    # ---------------- 界面 ----------------
    def _build_ui(self):
        root = QVBoxLayout(self)
        root.setContentsMargins(16, 14, 16, 14)
        root.setSpacing(12)

        body = QHBoxLayout()
        body.setSpacing(14)

        # 左侧导航
        self.nav = QTreeWidget()
        self.nav.setObjectName("SettingsNav")
        self.nav.setHeaderHidden(True)
        self.nav.setFixedWidth(150)
        self.nav.setRootIsDecorated(False)
        self.nav.setIndentation(4)
        self.nav.currentItemChanged.connect(self._on_nav_changed)

        # 右侧内容
        self.stack = QStackedWidget()

        pages = [
            ("输出目录", self._build_output_page()),
            ("快捷键", self._build_shortcuts_page()),
        ]
        for idx, (title, page) in enumerate(pages):
            item = QTreeWidgetItem([title])
            item.setData(0, Qt.UserRole, idx)
            item.setIcon(0, qta.icon(
                'fa6s.folder-open' if idx == 0 else 'fa6s.keyboard', color='#6E675D'))
            self.nav.addTopLevelItem(item)
            self.stack.addWidget(page)

        self.nav.setCurrentItem(self.nav.topLevelItem(0))

        body.addWidget(self.nav)
        body.addWidget(self.stack, 1)
        root.addLayout(body, 1)

        # 底部按钮
        btn_row = QHBoxLayout()
        btn_row.addStretch()
        cancel_btn = QPushButton("取消")
        cancel_btn.setMinimumWidth(88)
        cancel_btn.setFixedHeight(32)
        cancel_btn.clicked.connect(self.reject)
        btn_row.addWidget(cancel_btn)

        save_btn = QPushButton(" 保存")
        save_btn.setObjectName("primaryBtn")
        save_btn.setIcon(qta.icon('fa6s.floppy-disk', color='#FFFFFF'))
        save_btn.setMinimumWidth(100)
        save_btn.setFixedHeight(32)
        save_btn.setDefault(True)
        save_btn.clicked.connect(self.accept)
        btn_row.addWidget(save_btn)
        root.addLayout(btn_row)

    def _on_nav_changed(self, current, previous):
        if current is None:
            return
        idx = current.data(0, Qt.UserRole)
        if idx is not None:
            self.stack.setCurrentIndex(int(idx))

    # ---------------- 输出目录页 ----------------
    def _build_output_page(self):
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(2, 2, 2, 2)
        layout.setSpacing(8)

        title = QLabel("输出目录")
        title.setStyleSheet("font-size:15px; font-weight:bold; color:#1F1D1A;")
        layout.addWidget(title)

        desc = QLabel("截图、日志、抓包等所有命令的产物都会保存到这里。\n"
                      "命令里出现 {output_dir} 时会被替换成这个路径。")
        desc.setWordWrap(True)
        desc.setStyleSheet("color:#6E675D; font-size:12px;")
        layout.addWidget(desc)

        row = QHBoxLayout()
        row.setSpacing(8)
        self.output_edit = QLineEdit(self.output_dir)
        self.output_edit.setMinimumHeight(32)
        self.output_edit.setPlaceholderText("请选择输出目录")
        row.addWidget(self.output_edit, 1)

        browse_btn = QPushButton(" 浏览")
        browse_btn.setIcon(qta.icon('fa6s.folder-open', color='#1F1D1A'))
        browse_btn.setFixedHeight(32)
        browse_btn.setMinimumWidth(90)
        browse_btn.clicked.connect(self._on_browse_output)
        row.addWidget(browse_btn)
        layout.addLayout(row)

        layout.addStretch()
        return page

    def _on_browse_output(self):
        start = self.output_edit.text().strip() or self.output_dir
        path = QFileDialog.getExistingDirectory(self, "选择输出目录", start)
        if path:
            self.output_edit.setText(path)

    # ---------------- 快捷键页 ----------------
    def _build_shortcuts_page(self):
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(2, 2, 2, 2)
        layout.setSpacing(8)

        header = QHBoxLayout()
        title = QLabel("快捷键")
        title.setStyleSheet("font-size:15px; font-weight:bold; color:#1F1D1A;")
        header.addWidget(title)
        header.addStretch()

        reset_btn = QPushButton(" 恢复默认")
        reset_btn.setObjectName("warnBtn")
        reset_btn.setIcon(qta.icon('fa6s.arrow-rotate-left', color='#B0761F'))
        reset_btn.setFixedHeight(30)
        reset_btn.clicked.connect(self._on_reset_shortcuts)
        header.addWidget(reset_btn)
        layout.addLayout(header)

        desc = QLabel("点输入框后直接按组合键即可录制；留空表示不使用快捷键。")
        desc.setStyleSheet("color:#6E675D; font-size:12px;")
        layout.addWidget(desc)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QScrollArea.NoFrame)
        container = QWidget()
        form = QFormLayout(container)
        form.setContentsMargins(0, 4, 8, 4)
        form.setHorizontalSpacing(16)
        form.setVerticalSpacing(6)

        for action_name, label in SHORTCUT_ACTIONS:
            edit = QKeySequenceEdit()
            # 高度要留够：QKeySequenceEdit 内部包着一个 QLineEdit，
            # 全局 QSS 把它撑到 32px（18 min-height + 上下 6 padding + 2 边框），
            # 这里若只给 30px，内部输入框会溢出父控件被裁掉，表现为下边框消失。
            edit.setFixedHeight(34)
            edit.setMaximumWidth(260)
            self.key_edits[action_name] = edit
            form.addRow(f"{label}", edit)

        scroll.setWidget(container)
        layout.addWidget(scroll, 1)
        return page

    def _load_shortcuts(self):
        shortcuts = Settings.get_shortcuts()
        for action_name, edit in self.key_edits.items():
            edit.setKeySequence(QKeySequence(shortcuts.get(action_name, "")))

    def _on_reset_shortcuts(self):
        if QMessageBox.question(self, "恢复默认", "确定要把所有快捷键恢复成默认值吗？") != QMessageBox.Yes:
            return
        Settings.reset_shortcuts()
        self._load_shortcuts()

    # ---------------- 保存 ----------------
    def accept(self):
        path = self.output_edit.text().strip()
        if not path:
            Toast.notify(self, "输出目录不能为空")
            return
        # 统一成当前平台的分隔符，避免出现 "C:\Users\xxx/Desktop" 这种混写
        self.output_dir = os.path.normpath(path)

        for action_name, edit in self.key_edits.items():
            ks = edit.keySequence().toString()
            if ks:
                Settings.set_shortcut(action_name, ks)

        super().accept()
