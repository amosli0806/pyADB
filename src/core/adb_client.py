# src/core/adb_client.py
"""
统一的 ADB 调用出口。

为什么要有这一层：
GUI 层原本散着 50+ 处 subprocess.run，每处都重复抄一遍
「Windows 隐藏控制台 / 其他平台直接跑」的分支（helpers.get_subprocess_kwargs 其实
早就封装好了，只是没人用），超时值、解码方式、错误提示也各写各的。

收敛到这里之后：
- 平台差异只有一处；
- 超时/找不到 adb/解码失败都能返回可读的错误，而不是抛异常让上层自己吞；
- 以后要换实现（比如走 uiautomator2、换成异步）只改这里。

用法：
    adb = AdbClient(serial="emulator-5554")
    r = adb.shell("getprop ro.product.model")
    if r.ok:
        print(r.stdout)
"""

import subprocess
from dataclasses import dataclass, field

from src.utils.helpers import get_adb_path, get_subprocess_kwargs


def _decode(raw: bytes) -> str:
    """adb 输出按 UTF-8 解，失败再退回 GBK（部分 Windows 中文提示是 GBK）。"""
    if not raw:
        return ""
    for enc in ("utf-8", "gbk"):
        try:
            return raw.decode(enc)
        except UnicodeDecodeError:
            continue
    return raw.decode("utf-8", errors="replace")


@dataclass
class AdbResult:
    """一次 adb 调用的结果。调用方只关心 ok / text 时不必自己判断 returncode。"""

    ok: bool
    stdout: str = ""
    stderr: str = ""
    returncode: int = 0
    timed_out: bool = False
    command: list = field(default_factory=list)

    @property
    def text(self) -> str:
        """stdout 有内容就取它，否则取 stderr —— 大部分场景只想要“有输出”。"""
        return self.stdout if self.stdout.strip() else self.stderr

    @property
    def error(self) -> str:
        if self.timed_out:
            return self.stderr or "命令超时"
        return self.stderr.strip()


class AdbClient:
    """一个设备（或全局）的 adb 调用入口。

    :param serial: 目标设备序列号；为 None 时命令不带 -s（单设备或全局操作）
    :param adb_path: 显式指定 adb 路径，默认走 helpers.get_adb_path()
    :param default_timeout: 未单独指定 timeout 时的默认值（秒）
    """

    def __init__(self, serial=None, adb_path=None, default_timeout=15):
        self.serial = serial
        self.adb_path = adb_path or get_adb_path()
        self.default_timeout = default_timeout

    # ---------------- 内部 ----------------
    def _build(self, args, serial=None):
        target = self.serial if serial is None else serial
        cmd = [self.adb_path]
        if target:
            cmd += ["-s", target]
        return cmd + [str(a) for a in args]

    # ---------------- 对外 ----------------
    def run(self, args, serial=None, timeout=None, stdin=None):
        """跑一条 adb 子命令，例如 run(["devices"]) 或 run(["shell", "ls", "/sdcard"])。"""
        limit = self.default_timeout if timeout is None else timeout
        cmd = self._build(args, serial)
        try:
            proc = subprocess.run(
                cmd,
                capture_output=True,
                timeout=limit,
                input=stdin.encode("utf-8") if isinstance(stdin, str) else stdin,
                **get_subprocess_kwargs(),
            )
        except subprocess.TimeoutExpired:
            return AdbResult(False, stderr=f"命令超时（{limit}s）", returncode=-1,
                             timed_out=True, command=cmd)
        except FileNotFoundError:
            return AdbResult(False, stderr="找不到 adb：请确认 tools 目录内有 adb.exe，或系统 PATH 已配置",
                             returncode=-1, command=cmd)
        except OSError as exc:
            return AdbResult(False, stderr=f"调用 adb 失败：{exc}", returncode=-1, command=cmd)

        return AdbResult(
            ok=proc.returncode == 0,
            stdout=_decode(proc.stdout).strip(),
            stderr=_decode(proc.stderr).strip(),
            returncode=proc.returncode,
            command=cmd,
        )

    def shell(self, command, serial=None, timeout=None):
        """在设备上执行一条 shell 命令。多语句用 ; 或 && 自行拼接。"""
        return self.run(["shell", command], serial=serial, timeout=timeout)

    def command(self, args, serial=None):
        """返回完整 argv（含 adb 路径与 -s），供需要自己 Popen 的长任务场景使用。"""
        return self._build(args, serial)

    def devices(self, timeout=10):
        """返回 [(serial, state), ...]，state 通常是 device / offline / unauthorized。"""
        result = self.run(["devices"], timeout=timeout)
        devices = []
        for line in result.stdout.splitlines()[1:]:      # 第一行是 "List of devices attached"
            parts = line.split()
            if len(parts) >= 2:
                devices.append((parts[0], parts[1]))
        return devices

    def with_serial(self, serial):
        """返回绑定了新设备的新实例，避免共享状态被改写。"""
        return AdbClient(serial=serial, adb_path=self.adb_path, default_timeout=self.default_timeout)
