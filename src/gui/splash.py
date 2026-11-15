# src/gui/splash.py
"""启动封面。

为什么自己画而不是用 QSplashScreen：QSplashScreen 只能贴一张位图，
做不了「淡入 + 进度 + 分阶段文案 + 淡出」，而且它默认带系统边框感。
这里是一个无边框透明窗口，圆角卡片和白底都由 paintEvent 画。

用法（main.py）：
    splash = SplashWindow(); splash.show()
    splash.set_stage("正在加载界面…", 0.4)
    ...
    splash.finish()        # 淡出后自动销毁
"""

import os

from PySide6.QtCore import Qt, QTimer, QPropertyAnimation, QEasingCurve, QRectF
from PySide6.QtGui import QPainter, QColor, QPixmap, QPainterPath, QIcon
from PySide6.QtWidgets import QWidget, QLabel, QProgressBar, QVBoxLayout, QHBoxLayout

from src.utils.helpers import resource_path
from src.utils.version import APP_VERSION

CARD_W, CARD_H = 460, 240
RADIUS = 14

BG = QColor("#FBFAF7")
BORDER = QColor("#E3DED5")
INK = QColor("#1F1D1A")
MUTED = QColor("#9C9488")
ACCENT = QColor("#2F6B45")


class SplashWindow(QWidget):
    def __init__(self):
        super().__init__()
        # 无边框 + 置顶 + 不进任务栏：启动瞬间不该抢焦点，也不该在任务栏多一个按钮
        self.setWindowFlags(Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint | Qt.Tool)
        self.setAttribute(Qt.WA_TranslucentBackground, True)
        self.resize(CARD_W, CARD_H)
        self.setWindowOpacity(0.0)

        self._closing = False
        self._build_ui()
        self._center()

    # ---------------- UI ----------------
    def _build_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(34, 30, 34, 22)
        layout.setSpacing(0)

        row = QHBoxLayout()
        row.setSpacing(16)

        icon_label = QLabel()
        icon_path = os.path.join(resource_path("resources/icons"), "app_icon.ico")
        if os.path.exists(icon_path):
            pix = QIcon(icon_path).pixmap(64, 64)
            icon_label.setPixmap(pix)
        icon_label.setFixedSize(64, 64)
        row.addWidget(icon_label, 0, Qt.AlignTop)

        text_col = QVBoxLayout()
        text_col.setSpacing(6)

        name = QLabel("捕 虫 师")
        name.setStyleSheet(f"color:{INK.name()}; font-size:24px; font-weight:700; letter-spacing:2px;")
        text_col.addWidget(name)

        self.sub = QLabel(f"Android 调试工具箱  ·  V{APP_VERSION}")
        self.sub.setStyleSheet(f"color:{MUTED.name()}; font-size:11px; letter-spacing:.4px;")
        text_col.addWidget(self.sub)
        text_col.addStretch()

        row.addLayout(text_col, 1)
        layout.addLayout(row)
        layout.addStretch()          # 中间留白：文案和进度条压到底部，视觉重心才不会飘在上半截

        self.stage = QLabel("正在启动…")
        self.stage.setStyleSheet(f"color:{MUTED.name()}; font-size:11px;")
        layout.addWidget(self.stage)
        layout.addSpacing(8)

        self.bar = QProgressBar()
        self.bar.setObjectName("splashBar")
        self.bar.setRange(0, 1000)
        self.bar.setValue(30)
        self.bar.setTextVisible(False)
        self.bar.setFixedHeight(4)
        layout.addWidget(self.bar)

        self.setStyleSheet("""
            QProgressBar#splashBar {
                background-color: #EFEDE7;
                border: none;
                border-radius: 2px;
            }
            QProgressBar#splashBar::chunk {
                background-color: #2F6B45;
                border-radius: 2px;
            }
        """)

    def _center(self):
        screen = self.screen() or (self.parentWidget().screen() if self.parentWidget() else None)
        if screen is None:
            return
        geo = screen.availableGeometry()
        self.move(geo.center().x() - CARD_W // 2, geo.center().y() - CARD_H // 2)

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing, True)
        rect = QRectF(0.5, 0.5, self.width() - 1, self.height() - 1)
        path = QPainterPath()
        path.addRoundedRect(rect, RADIUS, RADIUS)
        painter.fillPath(path, BG)
        painter.setPen(BORDER)
        painter.drawPath(path)

    # ---------------- 对外 ----------------
    def set_stage(self, text: str, progress: float = None):
        """progress 取值 0~1；不给就只换文案"""
        if self._closing:
            return
        self.stage.setText(text)
        if progress is not None:
            self.bar.setValue(int(max(0.0, min(1.0, progress)) * 1000))

    def fade_in(self):
        self._anim = QPropertyAnimation(self, b"windowOpacity", self)
        self._anim.setDuration(220)
        self._anim.setStartValue(0.0)
        self._anim.setEndValue(1.0)
        self._anim.setEasingCurve(QEasingCurve.OutCubic)
        self._anim.start()

    def finish(self):
        """淡出后关闭。重复调用安全。"""
        if self._closing:
            return
        self._closing = True
        self.set_stage("就绪", 1.0)
        anim = QPropertyAnimation(self, b"windowOpacity", self)
        anim.setDuration(200)
        anim.setStartValue(self.windowOpacity())
        anim.setEndValue(0.0)
        anim.setEasingCurve(QEasingCurve.InCubic)
        anim.finished.connect(self.close)
        anim.start()
        self._fade_out = anim
