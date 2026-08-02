# src/utils/helpers.py
"""
工具函数模块，提供各种辅助功能：
- 获取资源文件路径（支持开发环境和打包后的可执行文件）
- 获取创建子进程时的额外参数（如隐藏控制台窗口）
- 获取 adb 可执行文件的路径
- 生成时间戳
- 运行 shell 命令并返回结果
"""

import datetime      # 用于处理日期和时间
import subprocess    # 用于执行外部命令
import os            # 用于文件和路径操作
import sys           # 用于判断系统类型和打包环境


def resource_path(relative_path):
    """
    获取资源文件的绝对路径，兼容开发环境和 PyInstaller 打包后的环境。
    :param relative_path: 相对于项目根目录（或打包后的根目录）的路径
    :return: 资源的绝对路径
    """
    if getattr(sys, 'frozen', False):
        # 打包环境：资源文件在 sys._MEIPASS 目录下
        base = sys._MEIPASS
    else:
        # 开发环境：以 main.py 所在目录为基准
        # 获取 main.py 的绝对路径（sys.argv[0] 是主脚本路径）
        main_file = sys.argv[0]
        if not os.path.isabs(main_file):
            main_file = os.path.abspath(main_file)
        base = os.path.dirname(main_file)
    return os.path.join(base, relative_path)


def get_subprocess_kwargs():
    """
    返回用于 subprocess.Popen 或 subprocess.run 的额外关键字参数。
    主要用于 Windows 平台，添加 creationflags=subprocess.CREATE_NO_WINDOW 来隐藏控制台窗口，
    避免在执行 adb 命令时弹出黑框。
    :return: 包含额外参数的字典，在其他平台上返回空字典
    """
    if sys.platform == 'win32':   # 如果是 Windows 系统
        return {'creationflags': subprocess.CREATE_NO_WINDOW}
    return {}                      # 其他系统无需额外参数


def get_adb_path():
    """
    返回 adb 可执行文件的路径，优先级：
    1. 打包后的 tools/adb.exe（如果存在）
    2. 当前可执行文件目录下的 adb.exe
    3. 系统的 PATH 环境变量中的 adb
    :return: adb 路径字符串
    """
    # 1. 尝试从打包后的 tools 目录查找 adb.exe
    if sys.platform == 'darwin' :
        adb_name = 'adb'  # macOS 上无扩展名
    else :
        adb_name = 'adb.exe'
    adb_packed = resource_path ( os.path.join ( 'tools' , adb_name ) )
    if os.path.exists(adb_packed):
        return adb_packed

    # 2. 尝试从当前可执行文件所在目录查找（对于打包后的 exe，或者开发环境直接运行）
    if getattr(sys, 'frozen', False):
        # 打包环境：可执行文件所在目录
        base = os.path.dirname(sys.executable)
    else:
        # 开发环境：当前文件（helpers.py）所在目录
        base = os.path.dirname(os.path.abspath(__file__))
    adb_local = os.path.join(base, "adb.exe")
    if os.path.exists(adb_local):
        return adb_local

    # 3. 最后依赖系统 PATH 环境变量
    return "adb"


def get_timestamp():
    """
    生成当前时间的格式化字符串，格式为 "时:分:秒.毫秒"（精确到毫秒，截取前3位）。
    例如：14:23:05.123
    :return: 时间戳字符串
    """
    # datetime.datetime.now() 获取当前日期时间
    # strftime 格式化：%H 小时（24小时制），%M 分钟，%S 秒，%f 微秒（6位）
    # 取前3位作为毫秒，所以用 [:-3] 去掉最后3位微秒，保留3位毫秒
    return datetime.datetime.now().strftime("%H:%M:%S.%f")[:-3]


def run_command(cmd, timeout=None):
    """
    运行一个 shell 命令，并返回其标准输出、标准错误和返回码。
    支持超时控制，超时后返回空输出、错误信息 "Timeout" 和返回码 -1。
    :param cmd: 要执行的命令字符串
    :param timeout: 超时时间（秒），如果为 None 则不限制
    :return: 元组 (stdout, stderr, returncode)
    """
    try:
        # 根据操作系统决定是否传递隐藏窗口标志
        if sys.platform == 'win32':
            # Windows 下使用 creationflags 隐藏控制台窗口
            result = subprocess.run(
                cmd,
                shell=True,                 # 通过 shell 执行，允许使用管道、重定向等
                capture_output=True,        # 捕获 stdout 和 stderr
                text=True,                   # 以文本模式返回（自动解码）
                timeout=timeout,              # 设置超时
                creationflags=subprocess.CREATE_NO_WINDOW  # 隐藏窗口
            )
        else:
            # 非 Windows 系统不需要额外标志
            result = subprocess.run(
                cmd,
                shell=True,
                capture_output=True,
                text=True,
                timeout=timeout
            )
        # 返回 stdout, stderr, 返回码
        return result.stdout, result.stderr, result.returncode
    except subprocess.TimeoutExpired:
        # 如果命令执行超时，捕获异常并返回特定信息
        return "", "Timeout", -1