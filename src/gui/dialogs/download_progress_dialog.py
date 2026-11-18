# src/gui/dialogs/download_progress_dialog.py
"""更新下载进度框。

替代原来的 QProgressDialog：那个只有一行文字 + 一条系统进度条，
看不出速度、剩余时间，也没法说明这次下的是增量还是全量。
"""

import time
from collections import deque

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QLabel, QProgressBar, QPushButton, QFrame
)

MUTED = "#9C9488"
INK = "#1F1D1A"


def _human_size(num: float) -> str:
    if num <= 0:
        return "0 B"
    mb = num / 1024 / 1024
    if mb >= 1:
        return f"{mb:.1f} MB"
    kb = num / 1024
    return f"{kb:.0f} KB"


def _human_speed(bps: float) -> str:
    if bps <= 0:
        return ""
    if bps >= 1024 * 1024:
        return f"{bps / 1024 / 1024:.1f} MB/s"
    return f"{bps / 1024:.0f} KB/s"


def _human_eta(seconds: float) -> str:
    if seconds <= 0 or seconds > 3600 * 5:
        return ""
    m, s = divmod(int(seconds), 60)
    return f"剩余 {m}:{s:02d}" if m else f"剩余 {s} 秒"


class DownloadProgressDialog(QDialog):
    """下载中显示速度 / 剩余时间；取消按钮由用户显式点击才发 canceled。"""

    canceled = Signal()

    def __init__(self, version: str, total_hint: int = 0, is_patch: bool = False, parent=None):
        super().__init__(parent)
        self.setWindowTitle("更新下载")
        self.setModal(True)
        self.setFixedWidth(460)
        self.setWindowFlag(Qt.WindowContextHelpButtonHint, False)

        self._total = total_hint or 0
        self._received = 0
        self._samples = deque(maxlen=24)       # (时间戳, 已下载字节)
        self._done = False

        self._build_ui(version, is_patch)

    def _build_ui(self, version: str, is_patch: bool):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(24, 22, 24, 18)
        layout.setSpacing(0)

        head = QHBoxLayout()
        head.setSpacing(12)

        badge = QLabel("↓")
        badge.setFixedSize(34, 34)
        badge.setAlignment(Qt.AlignCenter)
        badge.setStyleSheet(
            "background-color:#EDF4EF; color:#2F6B45; border-radius:9px;"
            "font-size:16px; font-weight:700;"
        )
        head.addWidget(badge, 0, Qt.AlignTop)

        titles = QVBoxLayout()
        titles.setSpacing(3)
        title = QLabel("正在下载更新")
        title.setStyleSheet(f"color:{INK}; font-size:15px; font-weight:700; letter-spacing:.3px;")
        titles.addWidget(title)

        mode = "增量包" if is_patch else "完整安装包"
        self.sub = QLabel(f"捕虫师 V{version} · {mode}")
        self.sub.setStyleSheet(f"color:{MUTED}; font-size:11px;")
        titles.addWidget(self.sub)
        head.addLayout(titles, 1)

        close_btn = QPushButton("✕")
        close_btn.setObjectName("dlgCloseBtn")
        close_btn.setFixedSize(26, 26)
        close_btn.setCursor(Qt.PointingHandCursor)
        close_btn.clicked.connect(self._on_cancel)
        head.addWidget(close_btn, 0, Qt.AlignTop)

        layout.addLayout(head)
        layout.addSpacing(20)

        self.bar = QProgressBar()
        self.bar.setObjectName("dlProgress")
        self.bar.setRange(0, 1000)
        self.bar.setValue(0)
        self.bar.setTextVisible(False)
        self.bar.setFixedHeight(8)
        layout.addWidget(self.bar)
        layout.addSpacing(9)

        meta = QHBoxLayout()
        meta.setSpacing(8)
        self.size_label = QLabel("准备中…")
        self.size_label.setStyleSheet(f"color:{MUTED}; font-size:11px;")
        meta.addWidget(self.size_label)
        meta.addStretch()
        self.speed_label = QLabel("")
        self.speed_label.setStyleSheet(f"color:{MUTED}; font-size:11px;")
        meta.addWidget(self.speed_label)
        layout.addLayout(meta)

        layout.addSpacing(18)
        line = QFrame()
        line.setFixedHeight(1)
        line.setStyleSheet("background-color:#F0ECE4; border:none;")
        layout.addWidget(line)
        layout.addSpacing(14)

        foot = QHBoxLayout()
        foot.addStretch()
        self.cancel_btn = QPushButton("取 消")
        self.cancel_btn.setObjectName("ghostBtn")
        self.cancel_btn.setFixedSize(88, 30)
        self.cancel_btn.setCursor(Qt.PointingHandCursor)
        self.cancel_btn.clicked.connect(self._on_cancel)
        foot.addWidget(self.cancel_btn)
        layout.addLayout(foot)

        self.setStyleSheet("""
            QDialog { background-color: #FFFFFF; }
            /* 全局 QSS 里 QDialog 是米色，标签带自己的样式表时会渲成米色块，
               这里必须显式声明透明，否则标题后面会留一条底色 */
            QLabel { background: transparent; }
            QProgressBar#dlProgress {
                background-color: #EFEDE7;
                border: none;
                border-radius: 4px;
            }
            QProgressBar#dlProgress::chunk {
                background-color: #2F6B45;
                border-radius: 4px;
            }
            QPushButton#ghostBtn {
                background-color: #FFFFFF;
                border: 1px solid #E3DED5;
                border-radius: 7px;
                color: #1F1D1A;
                font-size: 12px;
            }
            QPushButton#ghostBtn:hover { background-color: #F7F4EE; }
            QPushButton#ghostBtn:disabled { color: #B3ACA0; border-color: #EDE8E0; }
            QPushButton#dlgCloseBtn {
                background: transparent;
                border: none;
                color: #9C9488;
                font-size: 14px;
            }
            QPushButton#dlgCloseBtn:hover { color: #1F1D1A; }
        """)

    # ---------------- 数据 ----------------
    def set_progress(self, received: int, total: int):
        if self._done:
            return
        now = time.monotonic()
        self._received = received
        if total > 0:
            self._total = total

        self._samples.append((now, received))
        speed = 0.0
        if len(self._samples) >= 2:
            t0, b0 = self._samples[0]
            dt = now - t0
            if dt > 0.4:
                speed = max(0.0, (received - b0) / dt)

        if self._total > 0:
            ratio = min(1.0, received / self._total)
            self.bar.setValue(int(ratio * 1000))
            self.size_label.setText(f"{_human_size(received)} / {_human_size(self._total)}")
        else:
            self.bar.setValue(min(1000, int(received / 1024 / 50)))   # 未知总量时缓慢爬
            self.size_label.setText(_human_size(received))

        parts = []
        sp = _human_speed(speed)
        if sp:
            parts.append(sp)
        if speed > 0 and self._total > received:
            eta = _human_eta((self._total - received) / speed)
            if eta:
                parts.append(eta)
        self.speed_label.setText(" · ".join(parts))

    def set_stage(self, text: str):
        """下载完成后的收尾阶段文案（校验 / 准备安装）"""
        self.size_label.setText(text)
        self.speed_label.setText("")
        self.bar.setValue(1000)
        self._done = True
        self.cancel_btn.setEnabled(False)
        self.cancel_btn.setText("请稍候")

    # ---------------- 交互 ----------------
    def _on_cancel(self):
        if self._done:
            return
        self._done = True
        self.canceled.emit()
        self.reject()

    def closeEvent(self, event):
        # 用户点窗口的 ✕ 也算取消；已结束的状态下不做任何事
        if not self._done:
            self._done = True
            self.canceled.emit()
        super().closeEvent(event)

    def keyPressEvent(self, event):
        # 屏蔽 Esc：下载中误触会白下几十 MB，取消要点「取消」或 ✕
        if event.key() == Qt.Key_Escape:
            return
        super().keyPressEvent(event)
