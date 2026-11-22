# src/utils/logger.py
"""
日志模块
提供统一的日志初始化与获取入口：
- setup_logging()：控制台 + 滚动文件双输出，程序启动时调用一次
- get_logger(name)：各模块用 ``logger = get_logger(__name__)`` 获取
- install_excepthook()：把未捕获异常（含子线程）写进日志

日志文件位置：开发环境为项目根目录 logs/，打包后为 exe 同级 logs/。
以往代码里大量 ``except Exception: pass`` 会吞掉错误，排查线上问题时
既没有堆栈也没有上下文；改为记录 debug 级别后，默认 INFO 下依然安静，
需要时把级别调成 DEBUG 或用日志查看器即可还原现场。
"""

import logging
import os
import sys
from logging.handlers import RotatingFileHandler

_LOGGER_NAME = "pyadb"
_CONFIGURED = False

# 单个日志文件 2MB，保留 3 个历史文件，够定位最近几次运行
_MAX_BYTES = 2 * 1024 * 1024
_BACKUP_COUNT = 3

_LOG_FORMAT = "%(asctime)s [%(levelname)-7s] %(name)s: %(message)s"
_DATE_FORMAT = "%Y-%m-%d %H:%M:%S"


def get_log_dir():
    """返回日志目录（不保证已存在）"""
    if getattr(sys, "frozen", False):
        base = os.path.dirname(sys.executable)
    else:
        # 当前文件为 src/utils/logger.py，向上三级到达项目根目录
        base = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    return os.path.join(base, "logs")


def setup_logging(level=logging.INFO, log_dir=None):
    """
    初始化根日志器，重复调用不会叠加 handler。
    :param level: 控制台输出级别，文件始终记录 DEBUG 及以上，方便事后回溯
    :param log_dir: 自定义日志目录，默认 get_log_dir()
    :return: 日志文件绝对路径；创建失败时返回 None（不影响程序运行）
    """
    global _CONFIGURED
    root = logging.getLogger(_LOGGER_NAME)
    if _CONFIGURED:
        return getattr(root, "_log_file", None)

    root.setLevel(logging.DEBUG)
    root.propagate = False

    formatter = logging.Formatter(_LOG_FORMAT, datefmt=_DATE_FORMAT)

    # Windows 控制台默认 GBK，中文日志会变成乱码；管道/重定向时更明显
    try:
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, OSError):
        pass

    console = logging.StreamHandler(sys.stderr)
    console.setLevel(level)
    console.setFormatter(formatter)
    root.addHandler(console)

    log_file = None
    try:
        target_dir = log_dir or get_log_dir()
        os.makedirs(target_dir, exist_ok=True)
        log_file = os.path.join(target_dir, "pyadb.log")
        file_handler = RotatingFileHandler(
            log_file, maxBytes=_MAX_BYTES, backupCount=_BACKUP_COUNT, encoding="utf-8"
        )
        file_handler.setLevel(logging.DEBUG)
        file_handler.setFormatter(formatter)
        root.addHandler(file_handler)
    except OSError as exc:
        # 目录不可写（例如装到了 Program Files）时降级为仅控制台，不能让日志把程序拖死
        root.warning("日志文件不可用，仅输出到控制台: %s", exc)

    root._log_file = log_file  # 记录路径，供“关于/帮助”页展示
    _CONFIGURED = True
    root.debug("日志初始化完成，文件: %s", log_file)
    return log_file


def get_log_dir_or_none():
    """返回日志目录；目录不存在时返回 None（供 UI 判断能否打开）"""
    path = get_log_dir()
    return path if os.path.isdir(path) else None


def get_log_file():
    """返回当前进程正在写的日志文件路径；不可用时返回 None

    目录不可写时 setup_logging 会降级为仅控制台，此时 _log_file 为 None，
    帮助中心据此把「打开日志目录」按钮置灰，而不是让用户点了没反应。
    """
    root = logging.getLogger(_LOGGER_NAME)
    path = getattr(root, "_log_file", None)
    return path if path and os.path.isfile(path) else None


def get_logger(name=None):
    """获取带应用前缀的 logger，name 传 __name__ 即可"""
    if not _CONFIGURED:
        # 兜底：模块被单独导入（如测试脚本）时也能打日志
        setup_logging()
    suffix = name or "app"
    if suffix.startswith("src."):
        suffix = suffix[4:]
    return logging.getLogger(f"{_LOGGER_NAME}.{suffix}")


def install_excepthook():
    """
    接管 sys.excepthook 与 threading.excepthook，把未捕获异常写入日志。
    仅记录，不改变原有的进程退出行为。
    """
    logger = get_logger("excepthook")
    previous = sys.excepthook

    def _hook(exc_type, exc_value, exc_tb):
        if issubclass(exc_type, KeyboardInterrupt):
            previous(exc_type, exc_value, exc_tb)
            return
        logger.error("未捕获异常", exc_info=(exc_type, exc_value, exc_tb))
        previous(exc_type, exc_value, exc_tb)

    sys.excepthook = _hook

    def _thread_hook(args):
        if issubclass(args.exc_type, SystemExit):
            return
        logger.error(
            "子线程未捕获异常: %s",
            getattr(args.thread, "name", "unknown"),
            exc_info=(args.exc_type, args.exc_value, args.exc_traceback),
        )

    import threading

    threading.excepthook = _thread_hook
