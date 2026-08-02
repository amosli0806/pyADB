# src/core/async_command.py
"""
异步命令执行器，每个命令独立线程，通过信号返回输出
这个模块定义了一个 CommandRunner 类，继承自 QThread，用于在单独的线程中执行一个 adb 命令，
并实时将命令的输出通过信号发送给主线程（或其他接收者）。
"""

from PySide6.QtCore import QThread, Signal  # QThread: Qt 的线程类，Signal: 用于线程间通信的信号
import subprocess  # 用于执行外部命令（如 adb）


class CommandRunner(QThread):
    """在子线程中运行一个 adb 命令，逐行输出结果"""

    # 定义信号，这些信号可以在不同线程之间安全地传递数据
    output_ready = Signal(str)      # 每当命令输出一行时发射，参数是这一行的文本（不含换行符）
    finished = Signal()              # 命令正常结束时发射（无参数）
    error_occurred = Signal(str)     # 发生错误时发射，参数是错误信息字符串

    def __init__(self, adb_path: str, serial: str, command: str, cmd_name: str):
        """
        初始化命令执行器
        :param adb_path: adb 可执行文件的完整路径
        :param serial: 目标设备的序列号
        :param command: 要执行的 adb 命令（例如 "shell ls /sdcard"），注意不包含 adb 本身
        :param cmd_name: 命令的名称，用于日志标识（便于识别是哪个命令的输出）
        """
        super().__init__()  # 调用父类 QThread 的初始化方法
        self.adb_path = adb_path      # 保存 adb 路径
        self.serial = serial          # 保存设备序列号
        self.command = command        # 保存要执行的命令
        self.cmd_name = cmd_name      # 保存命令名称（用于日志或调试）
        self._process = None          # 用于存储 subprocess.Popen 对象，即子进程
        self._is_running = False      # 内部标志，表示命令是否正在运行，用于控制停止

    def run(self):
        """
        线程执行体，当调用 start() 启动线程时，此方法会在新线程中自动执行。
        它负责构造完整的 adb 命令、启动子进程、逐行读取输出并发射信号。
        """
        # 构造完整的命令行参数列表
        # 例如：["adb", "-s", "设备号", "shell", "ls", "/sdcard"]
        cmd = [self.adb_path, "-s", self.serial] + self.command.split()

        try:
            # 启动子进程
            # Popen 不会阻塞，立即返回一个进程对象
            # stdout=subprocess.PIPE 表示捕获标准输出
            # stderr=subprocess.STDOUT 表示将标准错误合并到标准输出（这样只需读取一个流）
            # universal_newlines=True 表示以文本模式处理输出（自动解码字节为字符串）
            # bufsize=1 表示使用行缓冲，这样每次读取一行时会更及时
            self._process = subprocess.Popen(
                cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                universal_newlines=True,
                bufsize=1
            )
            self._is_running = True   # 设置运行标志为 True

            # 逐行读取进程的标准输出
            # iter(stream.readline, '') 会不断调用 readline() 直到返回空字符串（表示流结束）
            for line in iter(self._process.stdout.readline, ''):
                if not self._is_running:  # 如果外部调用了 stop()，标志变为 False，立即退出循环
                    break
                if line:  # 如果读取到非空行
                    # 去除行尾的换行符，然后发射 output_ready 信号
                    self.output_ready.emit(line.rstrip('\n'))

            # 关闭输出流，释放资源
            self._process.stdout.close()

            # 等待进程结束，并获取返回码
            returncode = self._process.wait()

            # 如果返回码不为 0，表示命令执行失败（例如 adb 命令本身出错）
            if returncode != 0:
                self.error_occurred.emit(f"命令返回码 {returncode}")

        except Exception as e:
            # 如果在执行过程中发生异常（例如 adb 未找到），捕获并发射错误信号
            self.error_occurred.emit(str(e))
        finally:
            # 无论成功还是失败，最终将运行标志设为 False，并发射 finished 信号
            self._is_running = False
            self.finished.emit()

    def stop(self):
        """
        停止命令的执行
        通过设置内部标志并终止子进程来实现
        """
        self._is_running = False  # 先将标志置为 False，让 run() 中的循环退出
        if self._process:
            # 终止子进程（发送 SIGTERM 信号）
            # 如果希望强制结束，可以使用 .kill()，但 terminate() 更温和
            self._process.terminate()