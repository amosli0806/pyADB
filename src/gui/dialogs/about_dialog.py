# src/gui/dialogs/about_dialog.py
"""关于对话框。

参考虫师的排版：左边大图标、右边标题与版本/作者信息，一条分隔线，底部一行版权 + 按钮，
固定尺寸、信息一屏读完。之前是一整页滚动的富文本（核心特性 + 更新日志），
信息量太大反而不像「关于」，更像帮助文档。
"""

import os

from PySide6.QtCore import Qt
from PySide6.QtGui import QIcon
from PySide6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QLabel, QPushButton, QFrame,
    QApplication, QWidget
)

from src.utils.helpers import resource_path
from src.utils.version import APP_VERSION, BUILD_DATE
from src.gui.toast import Toast

GITHUB_URL = "https://github.com/amosli0806/pyADB"
CONTACT = "1573553463@qq.com"

_DIALOG_QSS = """
#AboutDialog {
    background-color: #FFFFFF;
}
#AboutDialog QLabel {
    background: transparent;
}
#AboutDialog QLabel#AboutTitle {
    color: #1F1D1A;
    font-size: 22px;
    font-weight: bold;
}
#AboutDialog QLabel#AboutSubtitle {
    color: #6E675D;
    font-size: 12px;
}
#AboutDialog QLabel#AboutBody {
    color: #4A443C;
    font-size: 12px;
}
#AboutDialog QLabel#AboutLink {
    color: #3E6E8E;
    font-size: 12px;
}
#AboutDialog QFrame#AboutDivider {
    background-color: #E3DED5;
    max-height: 1px;
    border: none;
}
"""


class AboutDialog(QDialog):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("AboutDialog")
        self.setWindowTitle("关于")
        self.setModal(True)
        self.setFixedSize(520, 300)
        self.setStyleSheet(_DIALOG_QSS)
        self._build_ui()

    def _build_ui(self):
        root = QVBoxLayout(self)
        root.setContentsMargins(28, 24, 28, 18)
        root.setSpacing(14)

        # ---------- 顶部：图标 + 信息 ----------
        top = QHBoxLayout()
        top.setSpacing(18)

        self.icon_label = QLabel()
        self.icon_label.setFixedSize(64, 64)
        self.icon_label.setAlignment(Qt.AlignCenter)
        icon = self.parent().windowIcon() if self.parent() else QIcon()
        if icon.isNull():
            icon = QApplication.windowIcon()
        if icon.isNull():
            icon = QIcon(os.path.join(resource_path("resources/icons"), "app_icon.ico"))
        if not icon.isNull():
            self.icon_label.setPixmap(icon.pixmap(64, 64))
        top.addWidget(self.icon_label, alignment=Qt.AlignTop)

        info = QVBoxLayout()
        info.setSpacing(5)

        title = QLabel("捕虫师")
        title.setObjectName("AboutTitle")
        info.addWidget(title)

        sub = QLabel(f"V{APP_VERSION}  ·  Build {BUILD_DATE}")
        sub.setObjectName("AboutSubtitle")
        info.addWidget(sub)

        info.addSpacing(8)

        author = QLabel("作者：李金钊")
        author.setObjectName("AboutBody")
        info.addWidget(author)

        stack = QLabel("技术栈：Python 3.13 · PySide6 · QtAwesome")
        stack.setObjectName("AboutBody")
        info.addWidget(stack)

        # 邮箱不做成链接：它只是「反馈渠道」的信息，不是要点开的入口
        # （做成 mailto 会显出蓝色下划线，和下面的项目地址混成一片）
        mail = QLabel(f"反馈：{CONTACT}")
        mail.setObjectName("AboutBody")
        mail.setTextFormat(Qt.PlainText)
        mail.setTextInteractionFlags(Qt.TextSelectableByMouse)   # 能选中复制就够了
        info.addWidget(mail)

        repo = QLabel(f"项目：<a href='{GITHUB_URL}' style='color:#3E6E8E;'>{GITHUB_URL}</a>")
        repo.setObjectName("AboutLink")
        repo.setOpenExternalLinks(True)
        repo.setTextFormat(Qt.RichText)
        info.addWidget(repo)

        info.addStretch()
        top.addLayout(info, 1)
        root.addLayout(top)

        # ---------- 分隔线 ----------
        divider = QFrame()
        divider.setObjectName("AboutDivider")
        divider.setFrameShape(QFrame.HLine)
        divider.setFixedHeight(1)
        root.addWidget(divider)

        # ---------- 版权 ----------
        copyright_label = QLabel("© 2026 李金钊  ·  捕虫师 —— 让 Android 调试更简单")
        copyright_label.setObjectName("AboutSubtitle")
        copyright_label.setAlignment(Qt.AlignCenter)
        root.addWidget(copyright_label)

        # ---------- 底部按钮 ----------
        btn_row = QHBoxLayout()
        btn_row.addStretch()

        copy_btn = QPushButton(" 复制信息")
        copy_btn.setMinimumWidth(100)
        copy_btn.setFixedHeight(32)
        copy_btn.clicked.connect(self._copy_info)
        btn_row.addWidget(copy_btn)

        close_btn = QPushButton(" 关闭")
        close_btn.setObjectName("primaryBtn")
        close_btn.setMinimumWidth(96)
        close_btn.setFixedHeight(32)
        close_btn.setDefault(True)
        close_btn.clicked.connect(self.accept)
        btn_row.addWidget(close_btn)

        root.addLayout(btn_row)

    def _copy_info(self):
        text = (
            f"捕虫师 V{APP_VERSION} (Build {BUILD_DATE})\n"
            f"作者：李金钊\n"
            f"反馈：{CONTACT}\n"
            f"项目：{GITHUB_URL}\n"
        )
        QApplication.clipboard().setText(text)
        Toast.notify(self, "已复制版本信息", duration=1800)
