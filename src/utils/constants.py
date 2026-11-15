# src/utils/constants.py
"""
常量模块
定义整个应用程序中使用的常量，如配置文件路径、预设命令列表、默认输出目录等。
"""

import os      # 提供与操作系统交互的功能，如文件路径操作
import sys     # 提供对 Python 解释器相关功能的访问，如判断程序是否被打包成可执行文件


def get_config_path():
    """
    获取配置文件 config.json 的完整路径。
    该函数根据程序运行环境（开发环境 vs 打包后的 exe）动态确定路径：
    - 如果程序是打包后的可执行文件（如用 PyInstaller 打包），配置文件应放在 exe 所在目录。
    - 如果是开发环境（直接运行 Python 脚本），配置文件放在项目根目录（与 main.py 同级）。
    """
    # 判断程序是否被打包成可执行文件（frozen）
    # 当使用 PyInstaller 等工具打包后，sys.frozen 属性会被设置为 True
    if getattr(sys, 'frozen', False):
        # 打包环境：配置文件放在可执行文件所在的目录
        base = os.path.dirname(sys.executable)      # sys.executable 是当前可执行文件的完整路径
    else:
        # 开发环境：配置文件放在项目根目录
        # 当前文件是 constants.py，位于 src/utils/constants.py
        # 需要向上两级目录到达项目根目录（与 main.py 同级）
        # os.path.abspath(__file__) 获取当前文件的绝对路径
        # os.path.dirname 两次，得到项目根目录
        base = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    # 将 base 路径与文件名 "config.json" 拼接，得到完整路径
    return os.path.join(base, 'config.json')


# 配置文件路径，调用 get_config_path 函数获得
CONFIG_FILE = get_config_path()


# 预设命令列表
# 这些是程序内置的常用命令，不可修改定时/循环设置，且不允许删除。
# 每个命令是一个字典，包含 id、name、command_text、description 等属性。
PRESET_COMMANDS = [
    {
        "id": 1,                            # 命令的唯一标识符，预设命令 ID 固定为 1-4
        "name": "权限获取",                  # 命令名称
        "command_text": "root; remount",    # ADB 命令内容，多条命令用分号分隔
        "description": "获取root权限并remount",  # 命令描述
        "show_stop_button": True,            # 是否显示“停止”按钮（该命令可能持续运行）
        "is_preset": True                     # 标记为预设命令
    },
    {
        "id": 2,
        "name": "logcat日志",
        # 先清除旧日志，然后开始抓取新日志并显示时间
        "command_text": "shell logcat -c; logcat -v threadtime",
        "description": "清除旧日志并开始抓取新日志（threadtime 格式，PID/TID 不带括号）",
        "show_stop_button": True,
        "is_preset": True,
        "save_output": True,   # 新增：是否将命令输出保存到文件（例如日志保存到本地）
        "no_timeout": True   # 新增
    },
    {
        "id": 3,
        "name": "录屏",
        # 使用 screenrecord 命令录制 3 分钟视频，文件名包含时间戳
        "command_text": "shell screenrecord --time-limit 180 /sdcard/video_{timestamp}.mp4",
        "description": "录制3分钟视频",
        "show_stop_button": True,
        "is_preset": True
    },
    {
        "id": 4,
        "name": "截图",
        "command_text": "shell screencap /sdcard/screenshot_{timestamp}.png; pull /sdcard/screenshot_{timestamp}.png \"{output_dir}/{safe_device_serial}/screenshot_{timestamp}.png\"; shell rm /sdcard/screenshot_{timestamp}.png",
        "description": "截图并自动pull到本地（保存为 screenshot_时间戳.png）",
        "show_stop_button": True,
        "is_preset": True
    },
    {
        "id" : 5 ,
        "name" : "地图引擎日志" ,
        "command_text" : "pull /sdcard/BaiduMapAuto/naviautoenginelog/ \"{output_dir}/{safe_device_serial}/\"" ,
        "description" : "本田23M24M25M26M引擎日志" ,
        "show_stop_button" : True ,
        "is_preset" : True,
        "no_timeout": True   # 新增
        } ,
{
        "id" : 6 ,
        "name" : "本田车机日志27M（3DAA）" ,
        "command_text" : "pull /mnt/vendor/log/Logcat \"{output_dir}/{safe_device_serial}/\"" ,
        "description" : "本田27M车机日志" ,
        "show_stop_button" : True ,
        "is_preset" : True,
        "no_timeout": True   # 新增
        } ,
    {
        "id" : 7 ,
        "name" : "本田车机日志26/27.5M（3A0W/30AW）" ,
        "command_text" : "pull /data/persistlogs/hsaelog \"{output_dir}/{safe_device_serial}/\"" ,
        "description" : "本田26M车机日志" ,
        "show_stop_button" : True ,
        "is_preset" : True,
        "no_timeout": True   # 新增
        } ,
    {
        "id" : 8 ,
        "name" : "本田车机日志25M（33WA）" ,
        "command_text" : "pull /data/hsaelog \"{output_dir}/{safe_device_serial}/\"" ,
        "description" : "本田25M车机日志" ,
        "show_stop_button" : True ,
        "is_preset" : True,
        "no_timeout": True   # 新增
        } ,
    {
        "id" : 9 ,
        "name" : "本田车机日志23/24M（30EA/31YA）" ,
        "command_text" : "pull /data/misc/logd \"{output_dir}/{safe_device_serial}/\"" ,
        "description" : "本田23M/24M车机日志" ,
        "show_stop_button" : True ,
        "is_preset" : True,
        "no_timeout": True   # 新增
        } ,
    {
        "id" : 10 ,
        "name" : "本田TSU日志" ,
        "command_text" : "pull /storage/emulated/0/cxlog \"{output_dir}/{safe_device_serial}/\"" ,
        "description" : "本田TSU日志" ,
        "show_stop_button" : True ,
        "is_preset" : True,
        "no_timeout": True   # 新增
        } ,
    {
        "id" : 11 ,
        "name" : "本田ANR/Crash/墓碑日志" ,
        "command_text" : "pull /data/anr \"{output_dir}/{safe_device_serial}/\"; pull /storage/emulated/0/Android/data/com.baidu.naviauto/files/xcrash \"{output_dir}/{safe_device_serial}/\"; pull /data/tombstones \"{output_dir}/{safe_device_serial}/\";" ,
        "description" : "本田ANR/Crash/墓碑日志" ,
        "show_stop_button" : True ,
        "is_preset" : True,
        "no_timeout": True   # 新增
        } ,
    {
        "id" : 12 ,
        "name" : "原生设置页面" ,
        
        "command_text" : "shell am start com.android.car.settings" ,
        "description" : "原生设置页面" ,
        "show_stop_button" : True ,
        "is_preset" : True
        } ,


]

# 默认输出目录：用户桌面上的 ADB_Output 文件夹
# os.path.expanduser("~") 获取当前用户的家目录（如 C:\Users\用户名 或 /home/用户名）
# 然后拼接桌面路径和文件夹名
DEFAULT_OUTPUT_DIR = os.path.expanduser("~/Desktop")

# 最大并发任务数：同时执行的 ADB 命令数量上限
# 用于限制线程池中的线程数量，避免过多进程导致系统资源耗尽
MAX_CONCURRENT_TASKS = 10

# 单条命令在内存中保留的最大输出行数（仅用于执行结束后的错误判定）
# logcat 这类持续运行的命令输出量无上限，必须限制缓冲，否则内存会随运行时间无限增长
MAX_OUTPUT_BUFFER_LINES = 2000