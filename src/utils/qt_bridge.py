# src/utils/qt_bridge.py
"""
跨线程 UI 调用跳板

Qt 规定控件只能在 GUI 线程操作，但本项目里仍有大量 ``threading.Thread``
在后台跑 adb 命令，跑完直接改 label 文字、切按钮状态、刷新列表。这类调用
在 PySide6 里不会立刻报错，表现为随机崩溃或界面花屏，很难复现。

用法::

    from src.utils.qt_bridge import ui_call

    # 后台线程里
    ui_call(self, self._update_ui_state)          # 整个方法甩回 GUI 线程
    ui_call(self, self.status.setText, "完成")

入口方法整段转投最省事：方法内部所有控件操作自然都在 GUI 线程执行。
"""

from PySide6.QtCore import QObject, Qt, QThread, Signal


class UiBridge(QObject):
    """把任意 callable 排队回对象所属线程执行"""

    _invoke = Signal(object, tuple)

    def __init__(self, host=None):
        # 不带 parent 构造：带 parent 的 QObject 不允许 moveToThread，
        # 而 bridge 有可能是在后台线程里被惰性创建的。
        super().__init__()
        host_thread = host.thread() if host is not None else QThread.currentThread()
        if QThread.currentThread() is not host_thread:
            # 首次创建就发生在后台线程：把对象迁到 GUI 线程，
            # 否则排队信号会投给一个没有事件循环的线程，永远不执行。
            self.moveToThread(host_thread)
        self._invoke.connect(self._execute, Qt.QueuedConnection)

    def _execute(self, func, args):
        func(*args)

    def call(self, func, *args):
        """已在 GUI 线程则同步执行，否则排队（调用方立即返回）"""
        if QThread.currentThread() is self.thread():
            func(*args)
            return
        try:
            self._invoke.emit(func, args)
        except RuntimeError:
            # 控件已销毁（关闭对话框后后台线程才收尾），此时无事可做
            pass


def ui_call(widget, func, *args):
    """
    按需为 widget 挂一个 UiBridge 并调用 func。
    widget 必须属于 GUI 线程（通常就是对话框或主窗口自身）。
    """
    bridge = getattr(widget, "_ui_bridge", None)
    if bridge is None:
        bridge = UiBridge(widget)
        widget._ui_bridge = bridge
    bridge.call(func, *args)