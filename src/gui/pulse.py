# src/gui/pulse.py
"""呼吸灯效果：让「正在运行」的控件缓慢明暗呼吸。

为什么用透明度而不是改颜色：这些控件的外观由 QSS 的 [state=...] 规则决定，
如果每帧 setStyleSheet 改色，一是开销大，二是会把 QSS 里的 hover/pressed 规则冲掉。
用 QGraphicsOpacityEffect 只影响绘制结果，不动样式，收放都干净。
"""

from PySide6.QtCore import (
    QAbstractAnimation, QEasingCurve, QPropertyAnimation, QSequentialAnimationGroup
)
from PySide6.QtWidgets import QGraphicsOpacityEffect


class Pulse:
    """给一个控件加呼吸灯，可随时 start / stop。"""

    def __init__(self, widget, low: float = 0.55, high: float = 1.0, duration: int = 1100):
        self.widget = widget
        self.low = low
        self.high = high

        self._effect = QGraphicsOpacityEffect(widget)
        self._effect.setOpacity(high)
        widget.setGraphicsEffect(self._effect)

        fade_out = QPropertyAnimation(self._effect, b"opacity")
        fade_out.setDuration(duration // 2)
        fade_out.setStartValue(high)
        fade_out.setEndValue(low)
        fade_out.setEasingCurve(QEasingCurve.InOutSine)

        fade_in = QPropertyAnimation(self._effect, b"opacity")
        fade_in.setDuration(duration // 2)
        fade_in.setStartValue(low)
        fade_in.setEndValue(high)
        fade_in.setEasingCurve(QEasingCurve.InOutSine)

        self._group = QSequentialAnimationGroup(widget)
        self._group.addAnimation(fade_out)
        self._group.addAnimation(fade_in)
        self._group.setLoopCount(-1)

    def start(self):
        if self._group.state() != QAbstractAnimation.State.Running:
            self._effect.setOpacity(self.high)
            self._group.start()

    def stop(self):
        self._group.stop()
        self._effect.setOpacity(1.0)
