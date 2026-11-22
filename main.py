# main.py
# 这是程序的入口文件，负责初始化 Qt 应用程序、加载样式表并显示主窗口。

import os
import sys
# sys 模块提供了对 Python 解释器相关功能的访问，比如通过 sys.argv 获取命令行参数，
# 以及使用 sys.exit() 退出程序。

import time

from PySide6.QtCore import QTimer
from PySide6.QtWidgets import QApplication
# QApplication 是 Qt 应用程序的核心类，管理应用程序的控制流和主要设置。
# 每个 Qt 应用程序有且仅有一个 QApplication 实例。

# 注意：MainWindow 不在顶部导入。
# src.gui.main_window 会连带加载十几个对话框模块，冷启动实测要 1.3s 以上，
# 写在顶部等于在“窗口出现之前”就先卡住一秒多。改为在启动封面显示之后再导入（见 main()）。

from src.utils.helpers import resource_path
# 从工具模块导入 resource_path 函数。这个函数的作用是获取资源文件的绝对路径，
# 在开发环境和打包成可执行文件后都能正确找到文件（例如样式表、图标等）。

from src.utils.logger import setup_logging, install_excepthook
# 日志：越早初始化越好，这样启动阶段的异常也能落盘

logger = None   # 在 main() 内初始化，避免导入期就建文件


def main():
    """
    应用程序的主函数，负责初始化 Qt 应用、加载样式表、创建并显示主窗口，
    最后启动事件循环。
    """
    global logger
    setup_logging()
    install_excepthook()
    from src.utils.logger import get_logger
    logger = get_logger(__name__)

    from src.utils.version import APP_VERSION
    logger.info("===== pyADB %s 启动，Python %s =====", APP_VERSION, sys.version.split()[0])

    # 创建 QApplication 实例，所有 Qt 应用必须首先创建它。
    # sys.argv 允许 Qt 处理命令行参数（例如一些 Qt 特定的参数）。
    app = QApplication(sys.argv)
    app.setStyle ( "Fusion" )  # 关键：先设置样式

    # 把内置的 tools 目录前置到 PATH：
    # 打包后 adb.exe / scrcpy.exe 在 _internal/tools 下，并不在系统 PATH 里，
    # 而代码里有大量裸 "adb" 调用。不前置的话，用户机器没装 adb 就会全部失败。
    tools_dir = resource_path("tools")
    if os.path.isdir(tools_dir):
        os.environ["PATH"] = tools_dir + os.pathsep + os.environ.get("PATH", "")

    # ---- 启动封面：先亮起来再干活，避免点击后几百毫秒毫无反馈 ----
    # 注意顺序：主窗口的导入、样式表解析都被推到封面显示之后。
    # 冷启动实测：import main_window 约 1.2s、QSS 解析约 0.2s，
    # 这些若发生在封面之前，就是用户盯着桌面发呆的那段时间。
    from src.gui.splash import SplashWindow
    splash = SplashWindow()
    splash.show()
    splash.fade_in()
    # 冒烟几轮：让窗口管理器真的把封面合成上屏、出第一帧。
    # 一次 processEvents 不够，Windows 上首帧要经过 expose/paint 才可见。
    for _ in range(3):
        app.processEvents()
    started = time.monotonic()

    holder = {}

    def build_main_window():
        """真正重的一步（样式表 + 导入 + 构造合计约 2.5s）放到事件循环里做。

        以前是同步跟在 splash.show() 后面直接跑的，阻塞期间封面既不重绘、
        淡入动画也推进不了（透明度停在 0），于是用户看到的是“快要加载完才弹出”。
        """
        splash.set_stage("正在加载界面…", 0.35)
        app.processEvents()

        # 样式表在这里解析：封面用的是自己的内联样式，不需要它。
        qss_path = resource_path("resources/styles.qss")
        # 样式表里用 {ASSETS} 占位图标目录：QSS 的 url() 是相对工作目录解析的，
        # 打包后工作目录不固定，这里统一替换成绝对路径，保证勾选图标等资源能加载
        assets_dir = resource_path("resources/icons").replace("\\", "/")
        try:
            with open(qss_path, "r", encoding='utf-8') as f:
                qss = f.read().replace("{ASSETS}", assets_dir)
            app.setStyleSheet(qss)
        except Exception as e:
            # 样式表坏了不该阻止程序启动，但要留下现场
            logger.error("加载样式表失败: %s", e, exc_info=True)

        from src.gui.main_window import MainWindow
        window = MainWindow()
        holder["window"] = window          # 挂住引用，避免主窗口被回收

        splash.set_stage("正在准备主界面…", 0.85)
        app.processEvents()
        # 显示主窗口。默认情况下，窗口创建后是隐藏的，需要调用 show() 使其可见。
        window.show()

        splash.set_stage("就绪", 1.0)
        logger.info("主界面就绪，耗时 %.0f ms", (time.monotonic() - started) * 1000)
        # 至少停留 650ms：太快一闪而过反而像闪屏 bug
        remain = max(0, 650 - int((time.monotonic() - started) * 1000))
        QTimer.singleShot(remain + 140, splash.finish)

    # 80ms 延时入队：先把 show()/fade_in() 的绘制与动画交给事件循环跑一轮，
    # 等封面确实上屏出帧了再开始阻塞式构造主窗口（0 延时在部分机器上会抢在首帧前）。
    QTimer.singleShot(80, build_main_window)

    # 启动 Qt 事件循环，并等待应用程序退出。
    # app.exec() 会进入事件循环（处理用户输入、重绘等），直到调用 quit() 或关闭窗口。
    # sys.exit() 确保程序以正确的退出码退出，并将 exec() 的返回值传递给操作系统。
    exit_code = app.exec()
    logger.info("===== 退出，code=%s =====", exit_code)
    sys.exit(exit_code)

# 当此脚本被直接运行时（而不是作为模块被导入），执行 main() 函数。
if __name__ == "__main__":
    main()