# src/core/scrcpy_manager.py
"""
Scrcpy 管理器模块
负责启动 scrcpy 投屏工具，以独立子进程运行，不阻塞主程序。
"""

import subprocess
import threading


class ScrcpyManager:
    """
    Scrcpy 管理器类，提供静态方法启动 scrcpy。
    无需实例化，直接通过类名调用。
    """

    @staticmethod
    def start(device_serial):
        """
        启动 scrcpy 投屏，连接到指定设备。
        :param device_serial: 目标 Android 设备的序列号
        """

        def run():
            # 构造命令：添加 --forward-all-clicks 参数解决安卓14点击无反应问题
            cmd = ["scrcpy", "-s", device_serial, "--forward-all-clicks"]
            subprocess.Popen(
                cmd,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL
            )

        threading.Thread(target=run, daemon=True).start()