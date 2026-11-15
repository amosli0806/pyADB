# src/gui/dialogs/update_dialog.py
"""更新相关对话框：发现新版本 / 更新已就绪。

界面沿用主窗口的暖白纸风格（#primaryBtn 实心主按钮 + 次级描边按钮），
更新说明按 Markdown 做一次极简渲染 —— 只认标题、列表、粗体、行内代码和链接，
够读 Release Notes 就行，不引第三方 Markdown 库。
"""

import html
import re

from PySide6.QtCore import Qt
from PySide6.QtGui import QDesktopServices
from PySide6.QtCore import QUrl
from PySide6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QLabel, QPushButton, QTextBrowser
)


def _md_to_html(text: str) -> str:
    """极简 Markdown -> HTML：标题 / 无序列表 / 粗体 / 行内代码 / 链接。"""
    text = (text or "").replace("\r\n", "\n").strip()
    if not text:
        return "<p style='color:#6E675D;'>该版本没有提供更新说明。</p>"

    out = []
    in_list = False

    def close_list():
        nonlocal in_list
        if in_list:
            out.append("</ul>")
            in_list = False

    for raw in text.split("\n"):
        line = raw.rstrip()
        stripped = line.strip()

        if not stripped:
            close_list()
            continue

        if re.match(r"^#{1,6}\s+", stripped):          # 标题
            close_list()
            content = re.sub(r"^#{1,6}\s+", "", stripped)
            out.append(f"<p style='font-size:13px;font-weight:bold;color:#1F1D1A;"
                       f"margin:10px 0 4px 0;'>{_inline(content)}</p>")
            continue

        if re.match(r"^[-*+]\s+", stripped):           # 无序列表
            if not in_list:
                out.append("<ul style='margin:2px 0 6px 18px;'>")
                in_list = True
            content = re.sub(r"^[-*+]\s+", "", stripped)
            out.append(f"<li style='margin:2px 0;'>{_inline(content)}</li>")
            continue

        close_list()
        out.append(f"<p style='margin:3px 0;'>{_inline(stripped)}</p>")

    close_list()
    return "".join(out)


def _inline(text: str) -> str:
    """行内标记：先转义，再处理 **粗体**、`代码`、[文字](链接)"""
    s = html.escape(text)
    s = re.sub(r"\*\*(.+?)\*\*", r"<b>\1</b>", s)
    s = re.sub(r"`(.+?)`", r"<code style='background:#F1EDE6;padding:1px 4px;"
                             r"border-radius:3px;'>\1</code>", s)
    s = re.sub(r"\[(.+?)\]\((https?://[^\s)]+)\)",
               r"<a href='\2' style='color:#3E6E8E;'>\1</a>", s)
    return s


class UpdateDialog(QDialog):
    """发现新版本：展示版本 / 日期 / 包大小 / 更新说明，让用户决定是否更新。"""

    ACTION_UPDATE = "update"
    ACTION_LATER = "later"

    def __init__(self, info, parent=None):
        super().__init__(parent)
        self.info = info
        self.action = self.ACTION_LATER
        self.setWindowTitle("发现新版本")
        self.setMinimumWidth(520)
        self._build_ui()

    def _build_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(20, 18, 20, 16)
        layout.setSpacing(10)

        title = QLabel(f"发现新版本  V{self.info.version}")
        title.setStyleSheet("font-size:17px; font-weight:bold; color:#1F1D1A;")
        layout.addWidget(title)

        meta_parts = []
        if self.info.date_text:
            meta_parts.append(f"发布于 {self.info.date_text}")
        if self.info.size_text:
            meta_parts.append(self.info.size_text)
        meta_parts.append("下载后自动校验完整性")
        meta = QLabel(" · ".join(meta_parts))
        meta.setStyleSheet("color:#6E675D; font-size:12px;")
        layout.addWidget(meta)

        label = QLabel("发 布 说 明")
        label.setObjectName("sectionLabel")
        layout.addWidget(label)

        notes = QTextBrowser()
        notes.setOpenExternalLinks(True)
        notes.setReadOnly(True)
        notes.setMinimumHeight(200)
        notes.setStyleSheet(
            "QTextBrowser { background:#FFFFFF; border:1px solid #E3DED5; border-radius:8px;"
            " padding:10px 12px; font-size:12px; color:#1F1D1A; }"
        )
        notes.setHtml(_md_to_html(self.info.release_notes))
        layout.addWidget(notes, 1)

        btn_row = QHBoxLayout()
        btn_row.setSpacing(8)

        if self.info.html_url:
            view_btn = QPushButton("打开发布页")
            view_btn.setObjectName("linkBtn")
            view_btn.setCursor(Qt.PointingHandCursor)
            view_btn.clicked.connect(lambda: QDesktopServices.openUrl(QUrl(self.info.html_url)))
            btn_row.addWidget(view_btn)

        btn_row.addStretch()

        later_btn = QPushButton("稍后")
        later_btn.setMinimumWidth(90)
        later_btn.clicked.connect(self.reject)
        btn_row.addWidget(later_btn)

        update_btn = QPushButton(" 立即更新")
        update_btn.setObjectName("primaryBtn")
        update_btn.setMinimumWidth(120)
        update_btn.setDefault(True)
        update_btn.clicked.connect(self._on_update)
        btn_row.addWidget(update_btn)

        layout.addLayout(btn_row)

    def _on_update(self):
        self.action = self.ACTION_UPDATE
        self.accept()


class UpdateReadyDialog(QDialog):
    """更新包已下载并校验完成，提示重启完成安装。"""

    def __init__(self, info, file_path, parent=None):
        super().__init__(parent)
        self.info = info
        self.file_path = file_path
        self.install_now = False
        self.setWindowTitle("更新已就绪")
        self.setMinimumWidth(440)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(20, 18, 20, 16)
        layout.setSpacing(10)

        title = QLabel(f"V{info.version} 已下载完成")
        title.setStyleSheet("font-size:17px; font-weight:bold; color:#1F1D1A;")
        layout.addWidget(title)

        desc = QLabel("点击「立即重启更新」后程序会自动关闭、替换文件并重新启动。\n"
                      "你的指令配置与输出目录不会受影响。")
        desc.setStyleSheet("color:#6E675D; font-size:12px;")
        desc.setWordWrap(True)
        layout.addWidget(desc)

        btn_row = QHBoxLayout()
        btn_row.addStretch()

        later_btn = QPushButton("稍后")
        later_btn.setMinimumWidth(90)
        later_btn.clicked.connect(self.reject)
        btn_row.addWidget(later_btn)

        restart_btn = QPushButton(" 立即重启更新")
        restart_btn.setObjectName("primaryBtn")
        restart_btn.setMinimumWidth(140)
        restart_btn.setDefault(True)
        restart_btn.clicked.connect(self._on_restart)
        btn_row.addWidget(restart_btn)

        layout.addLayout(btn_row)

    def _on_restart(self):
        self.install_now = True
        self.accept()
