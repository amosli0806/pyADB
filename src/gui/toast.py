# src/gui/toast.py
"""轻量提示条（toast）。

原来「未检测到可用设备」这类提醒都弹 QMessageBox —— 要点一下 OK 才能继续，
而它描述的往往只是一次操作被忽略，不值得打断。改成底部浮出的短提示：
从下往上滑入 + 淡入，2.4 秒后原路滑出，不拦截任何操作。

用法：
    Toast.notify(self, "未检测到可用设备")
"""

from PySide6.QtCore import (
    Qt, QTimer, QPoint, QPropertyAnimation, QEasingCurve, QParallelAnimationGroup
)
from PySide6.QtWidgets import QLabel, QGraphicsOpacityEffect, QWidget, QDialog

from src.utils.logger import get_logger

logger = get_logger(__name__)


class Toast(QLabel):
    """浮在父窗口底部的短提示，同一时刻只存在一个。"""

    _current = None

    # 所有 toast 统一宽度：忽宽忽窄会让同级消息看起来像不同层级的组件
    FIXED_WIDTH = 360
    SLIDE_OFFSET = 10          # 滑入时从下往上挪的距离

    def __init__(self, parent: QWidget, text: str, duration: int = 2400):
        super().__init__(text, parent)
        self.setObjectName("toast")
        self.setAlignment(Qt.AlignCenter)
        self.setWordWrap(True)                          # 文字长了换行，而不是把条子撑宽
        self.setAttribute(Qt.WA_TransparentForMouseEvents, True)  # 不挡点击

        self._effect = QGraphicsOpacityEffect(self)
        self._effect.setOpacity(0.0)
        self.setGraphicsEffect(self._effect)

        self._duration = duration
        self._target = QPoint(0, 0)

    # ---------------- 对外接口 ----------------
    @classmethod
    def notify(cls, widget: QWidget, text: str, level: str = "info", duration: int = 2400):
        """
        轻提示的统一入口：把 toast 挂到**顶层窗口**上。

        为什么不挂在 self 上：对话框里点「保存成功」之后对话框马上就关了，
        toast 作为子控件会跟着一起销毁，用户根本看不到。挂到 window() 就不会。
        """
        if widget is None:
            return None
        host = widget.window() if hasattr(widget, "window") else widget
        # 对话框里的提示挂到它的父窗口：弹窗类往往点完就关，挂自己会跟着销毁
        if isinstance(widget, QDialog) and widget.parent() is not None:
            host = widget.parent().window()
        toast = cls.show_message(host, (text or "").strip(), duration)
        if toast is not None:
            toast.setProperty("level", level)
            toast.style().unpolish(toast)
            toast.style().polish(toast)
        return toast

    @classmethod
    def show_message(cls, parent: QWidget, text: str, duration: int = 2400):
        """在父窗口底部居中弹出一条提示（旧的会被顶掉）"""
        if parent is None:
            return None

        old = Toast._current
        if old is not None:
            try:
                old._dismiss_now()
            except RuntimeError as exc:
                logger.debug("忽略异常（show_message）: %s", exc, exc_info=True)

        toast = cls(parent, text, duration)
        Toast._current = toast
        toast._pop_up()
        return toast

    # ---------------- 内部 ----------------
    def _pop_up(self):
        self.show()
        self.raise_()
        self._apply_geometry(start_offset=self.SLIDE_OFFSET)

        # 滑入 + 淡入同时进行
        anim = QParallelAnimationGroup(self)

        slide = QPropertyAnimation(self, b"pos", self)
        slide.setDuration(180)
        slide.setStartValue(self._target + QPoint(0, self.SLIDE_OFFSET))
        slide.setEndValue(self._target)
        slide.setEasingCurve(QEasingCurve.OutCubic)

        fade = QPropertyAnimation(self._effect, b"opacity", self)
        fade.setDuration(180)
        fade.setStartValue(0.0)
        fade.setEndValue(1.0)

        anim.addAnimation(slide)
        anim.addAnimation(fade)
        anim.start()
        self._anim_in = anim

        QTimer.singleShot(self._duration, self._fade_out)

    def _fade_out(self):
        # 父窗口可能在计时器到点前就关了，对象已被销毁，这里安静跳过
        try:
            if not self.isVisible():
                return
        except RuntimeError:
            return

        # 原路滑出 + 淡出
        anim = QParallelAnimationGroup(self)

        slide = QPropertyAnimation(self, b"pos", self)
        slide.setDuration(160)
        slide.setStartValue(self._target)
        slide.setEndValue(self._target + QPoint(0, self.SLIDE_OFFSET))
        slide.setEasingCurve(QEasingCurve.InCubic)

        fade = QPropertyAnimation(self._effect, b"opacity", self)
        fade.setDuration(160)
        fade.setStartValue(self._effect.opacity())
        fade.setEndValue(0.0)

        anim.addAnimation(slide)
        anim.addAnimation(fade)
        anim.finished.connect(self._dismiss_now)
        anim.start()
        self._anim_out = anim

    def _dismiss_now(self):
        if Toast._current is self:
            Toast._current = None
        self.hide()
        self.deleteLater()

    def _apply_geometry(self, start_offset: int = 0):
        parent = self.parentWidget()
        if parent is None:
            return

        self.setFixedWidth(min(self.FIXED_WIDTH, max(parent.width() - 80, 200)))
        self.adjustSize()                 # 按固定宽度重算换行后的高度

        x = (parent.width() - self.width()) // 2
        y = parent.height() - self.height() - 36
        self._target = QPoint(x, y)
        self.move(self._target + QPoint(0, start_offset))
