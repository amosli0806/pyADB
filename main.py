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

from src.gui.main_window import MainWindow
# 从自定义的 gui 包中导入主窗口类 MainWindow，它是应用程序的主界面。

from src.utils.helpers import resource_path
# 从工具模块导入 resource_path 函数。这个函数的作用是获取资源文件的绝对路径，
# 在开发环境和打包成可执行文件后都能正确找到文件（例如样式表、图标等）。

def main():
    """
    应用程序的主函数，负责初始化 Qt 应用、加载样式表、创建并显示主窗口，
    最后启动事件循环。
    """
    # 创建 QApplication 实例，所有 Qt 应用必须首先创建它。
    # sys.argv 允许 Qt 处理命令行参数（例如一些 Qt 特定的参数）。
    app = QApplication(sys.argv)
    # 使用 resource_path 函数获取样式表文件的完整路径。
    # "resources/styles.qss" 是相对于项目根目录的路径，resource_path 会将其转换为绝对路径。
    app.setStyle ( "Fusion" )  # 关键：先设置样式

    # 把内置的 tools 目录前置到 PATH：
    # 打包后 adb.exe / scrcpy.exe 在 _internal/tools 下，并不在系统 PATH 里，
    # 而代码里有大量裸 "adb" 调用。不前置的话，用户机器没装 adb 就会全部失败。
    tools_dir = resource_path("tools")
    if os.path.isdir(tools_dir):
        os.environ["PATH"] = tools_dir + os.pathsep + os.environ.get("PATH", "")

    qss_path = resource_path("resources/styles.qss")
    # 样式表里用 {ASSETS} 占位图标目录：QSS 的 url() 是相对工作目录解析的，
    # 打包后工作目录不固定，这里统一替换成绝对路径，保证勾选图标等资源能加载
    assets_dir = resource_path("resources/icons").replace("\\", "/")
    try:
        with open(qss_path, "r", encoding='utf-8') as f:
            qss = f.read().replace("{ASSETS}", assets_dir)
        app.setStyleSheet(qss)

    except Exception as e:
        # 如果加载失败，打印错误信息。在实际项目中可能会改用日志记录。
        print(f"加载样式表失败: {e}")  # 可改为日志或忽略

    # ---- 启动封面：先亮起来再干活，避免点击后几百毫秒毫无反馈 ----
    from src.gui.splash import SplashWindow
    splash = SplashWindow()
    splash.show()
    splash.fade_in()
    app.processEvents()
    started = time.monotonic()

    splash.set_stage("正在加载界面…", 0.35)
    app.processEvents()

    # 创建主窗口实例。
    window = MainWindow()

    splash.set_stage("正在准备主界面…", 0.85)
    app.processEvents()
    # 显示主窗口。默认情况下，窗口创建后是隐藏的，需要调用 show() 使其可见。
    window.show()

    splash.set_stage("就绪", 1.0)
    # 至少停留 650ms：太快一闪而过反而像闪屏 bug
    remain = max(0, 650 - int((time.monotonic() - started) * 1000))
    QTimer.singleShot(remain + 140, splash.finish)

    # 启动 Qt 事件循环，并等待应用程序退出。
    # app.exec() 会进入事件循环（处理用户输入、重绘等），直到调用 quit() 或关闭窗口。
    # sys.exit() 确保程序以正确的退出码退出，并将 exec() 的返回值传递给操作系统。
    sys.exit(app.exec())

# 当此脚本被直接运行时（而不是作为模块被导入），执行 main() 函数。
if __name__ == "__main__":
    main()