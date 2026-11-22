# src/core/adb_manager.py
"""
ADB 管理器模块
负责与 Android 调试桥（ADB）交互，管理设备连接、执行命令（如录屏、logcat 等），
并支持多任务并发和停止功能。
"""

import subprocess
import threading
import os
import sys
import shlex
from collections import deque
from datetime import datetime
import re
from PySide6.QtCore import QObject, Signal, QThreadPool, QRunnable
import time

from src.core.adb_client import AdbClient
from src.utils.helpers import get_timestamp, resource_path
from src.utils.constants import MAX_CONCURRENT_TASKS, MAX_OUTPUT_BUFFER_LINES

from src.utils.logger import get_logger

logger = get_logger(__name__)


class CommandSignals(QObject):
    """命令执行过程中的信号类"""
    started = Signal(int, str)
    output = Signal(str)
    error = Signal(str)
    finished = Signal(int, str, bool)
    stopped = Signal(int, str)  # 新增

class AdbCommandRunnable(QRunnable):
    """可运行的任务类，用于在线程池中执行具体的 ADB 命令"""

    # 消息级别常量
    LEVEL_INFO = 0
    LEVEL_SUCCESS = 1
    LEVEL_WARNING = 2
    LEVEL_ERROR = 3
    LEVEL_RESULT = 4

    def __init__(self, command, device_serial, output_dir, signals):
        super().__init__()
        self.command = command
        self.device_serial = device_serial
        self.safe_device_serial = re.sub(r'[\\/*?:"<>|]', '_', device_serial)
        self.output_dir = output_dir
        self.signals = signals
        self.process = None
        self._is_running = True
        self._stopped = False
        self.output_file = None
        self.is_logcat = ("logcat" in self.command.name.lower()) or ("logcat" in self.command.command_text.lower())
        self.is_screenrecord = ("录屏" in self.command.name) or ("screenrecord" in self.command.command_text)
        # 新增：判断是否为 Monkey 命令（屏蔽其大量输出）
        self.is_monkey = ("monkey" in self.command.name.lower()) or ("monkey" in self.command.command_text.lower())
        self._device_video_path = None
        self._local_video_path = None
        self._video_pulled = False
        self._manually_stopped = False
        self._finished_emitted = False
        self._is_multi_command = len([c.strip() for c in command.command_text.split(';') if c.strip()]) > 1
        # 只保留最近若干行输出用于错误判定：logcat 等长跑命令的输出量无上限，
        # 用有界队列避免内存随运行时间无限增长
        self._output_lines = deque(maxlen=MAX_OUTPUT_BUFFER_LINES)

        if command.save_output:
            self._prepare_output_file()

    def _format_message(self, level, text):
        """格式化带级别的消息"""
        icons = {
            self.LEVEL_INFO: "ℹ️",
            self.LEVEL_SUCCESS: "✅",
            self.LEVEL_WARNING: "⚠️",
            self.LEVEL_ERROR: "❌",
            self.LEVEL_RESULT: "🏁"
        }
        colors = {
            self.LEVEL_INFO: "#1F1D1A",
            self.LEVEL_SUCCESS: "#2F6B45",
            self.LEVEL_WARNING: "#B0761F",
            self.LEVEL_ERROR: "#A6402F",
            self.LEVEL_RESULT: "#3E6E8E"
        }
        icon = icons.get(level, "")
        color = colors.get(level, "#1F1D1A")
        return f"<span style='color:{color};'>{icon} {text}</span>"

    def _emit_finished(self, success):
        """安全发射 finished 信号，确保只发射一次"""
        if not self._finished_emitted:
            self._finished_emitted = True
            self.signals.finished.emit(self.command.id, self.command.name, success)

    def _prepare_output_file(self):
        try:
            device_dir = os.path.join(self.output_dir, self.safe_device_serial)
            os.makedirs(device_dir, exist_ok=True)
            safe_name = re.sub(r'[\\/*?:"<>|]', "", self.command.name)
            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            filename = f"{safe_name}_{timestamp}.log"
            filepath = os.path.join(device_dir, filename)
            self.output_file = open(filepath, 'a', encoding='utf-8', errors='replace')
            self.output_file.write(f"===== 命令启动于 {get_timestamp()} =====\n")
            self.output_file.flush()
        except Exception as e:
            self.signals.error.emit(f"[{self.command.name}] 无法创建输出文件: {str(e)}")

    def _get_screenrecord_pid(self):
        """在设备上找 screenrecord 进程 PID。

        按 ROM 差异依次试 ps -e / ps -A / ps / pgrep / pidof。
        原先这些命令是 `shell=True` 交给本机 cmd 执行，`| grep` 在 Windows 上
        根本没有 grep 可用，前三条永远失败；现在统一走设备侧 sh。
        """
        probes = [
            ("ps -e | grep screenrecord", False),
            ("ps -A | grep screenrecord", False),
            ("ps | grep screenrecord", False),
            ("pgrep screenrecord", True),
            ("pidof screenrecord", True),
        ]
        adb = AdbClient(serial=self.device_serial)
        for cmd, pid_only in probes:
            try:
                result = adb.shell(cmd, timeout=5)
                if not (result.ok and result.stdout.strip()):
                    continue
                output = result.stdout.strip()
                if pid_only:
                    pid = output.split()[0] if output else None
                    if pid and pid.isdigit():
                        return pid
                for line in output.splitlines():
                    parts = line.split()
                    if len(parts) >= 2:
                        if parts[0].isdigit():
                            return parts[0]
                        if parts[1].isdigit():
                            return parts[1]
            except Exception as e:
                self.signals.error.emit(f"[{self.command.name}] 获取 PID 异常: {e}")
                continue
        return None

    def _stop_screenrecord_gracefully(self):
        pid = self._get_screenrecord_pid()
        if not pid:
            self.signals.error.emit(f"[{self.command.name}] 无法获取录屏进程 PID，将强制终止")
            return False
        adb = AdbClient(serial=self.device_serial)
        try:
            adb.shell(f"kill -2 {pid}", timeout=3)
            for _ in range(20):
                time.sleep(0.5)
                check_result = adb.shell(f"ps -e | grep {pid}", timeout=3)
                if not (check_result.ok and check_result.stdout):
                    # 成功优雅退出，不再输出冗余日志
                    return True
            self.signals.error.emit(f"[{self.command.name}] 等待录屏进程退出超时，将强制终止")
            return False
        except Exception as e:
            self.signals.error.emit(f"[{self.command.name}] 优雅停止异常: {e}")
            return False

    def _kill_process_tree(self, proc, timeout=2):
        """
        结束命令对应的整棵子进程树。

        Windows 下这些命令是用 shell=True 启动的，Popen 拿到的其实是 cmd.exe，
        直接 terminate() 只会杀掉 cmd.exe，真正的 adb.exe 仍然活着并占着管道，
        导致读取线程一直阻塞（每次关闭要白等 5 秒），退出后 adb.exe 还会变成孤儿进程。
        因此这里按进程树整体结束。
        """
        if proc is None:
            return
        try:
            if proc.poll() is not None:
                return
        except Exception:
            return

        if sys.platform == 'win32':
            try:
                subprocess.run(
                    ['taskkill', '/F', '/T', '/PID', str(proc.pid)],
                    capture_output=True, timeout=5,
                    creationflags=subprocess.CREATE_NO_WINDOW
                )
            except Exception as exc:
                logger.debug("忽略异常（_kill_process_tree）: %s", exc, exc_info=True)

        try:
            proc.terminate()
        except Exception as exc:
            logger.debug("忽略异常（_kill_process_tree）: %s", exc, exc_info=True)
        try:
            proc.wait(timeout=timeout)
        except Exception:
            try:
                proc.kill()
            except Exception as exc:
                logger.debug("忽略异常（_kill_process_tree）: %s", exc, exc_info=True)
            try:
                proc.wait(timeout=1)
            except Exception as exc:
                logger.debug("忽略异常（_kill_process_tree）: %s", exc, exc_info=True)

    def stop(self):
        if self._stopped:
            return
        self._is_running = False
        self._stopped = True
        self._manually_stopped = True
        # 立即发射停止信号，让 UI 更新
        self.signals.stopped.emit ( self.command.id , self.command.name )

        if self.output_file:
            try:
                self.output_file.close()
            except Exception as exc:
                logger.debug("忽略异常（stop）: %s", exc, exc_info=True)
            self.output_file = None

        self._kill_process_tree(self.process)

        if "logcat" in self.command.name.lower():
            threading.Thread(target=self._clean_remote_process, daemon=True).start()

        if self.is_screenrecord and self._device_video_path and not self._video_pulled:
            self._stop_screenrecord_gracefully()
            self._kill_process_tree(self.process)
            threading.Thread(target=self._pull_video, args=(self._device_video_path, self._local_video_path),
                             daemon=True).start()
        else:
            self._emit_finished(True)

    def _clean_remote_process(self):
        try:
            adb = AdbClient(serial=self.device_serial)
            result = adb.shell("ps -e -o pid,cmd", timeout=5)
            if not result.ok:
                self.signals.error.emit(f"[清理] 获取进程列表失败: {result.stderr}")
                return

            pids_to_kill = []
            for line in result.stdout.splitlines():
                if "PID" in line:
                    continue
                if "logcat" in line.lower() or "hilogcat" in line.lower():
                    parts = line.strip().split(maxsplit=1)
                    if parts and parts[0].isdigit():
                        pids_to_kill.append(parts[0])

            if not pids_to_kill:
                return

            killed_count = 0
            for pid in pids_to_kill:
                kill_result = adb.shell(f"kill -9 {pid}", timeout=2)
                if kill_result.ok:
                    killed_count += 1
                else:
                    err_msg = kill_result.stderr.strip()
                    if "Operation not permitted" not in err_msg and "Permission denied" not in err_msg:
                        if err_msg:
                            self.signals.error.emit(f"[清理] 终止进程 {pid} 失败: {err_msg}")

            # if killed_count > 0:
            #     self.signals.output.emit(self._format_message(self.LEVEL_INFO, f"[清理] 已终止 {killed_count} 个 logcat 残留进程"))
        except Exception as e:
            self.signals.error.emit(f"[清理] 异常: {str(e)}")

    def run(self):
        success = True
        try:
            self.signals.started.emit(self.command.id, self.command.name)
            loop_count = self.command.loop_count
            interval = self.command.interval
            count = 0
            while self._is_running:
                if loop_count is not None and count >= loop_count:
                    break
                timestamp = datetime.now().strftime("%Y%m%d%H%M%S")
                if self.is_screenrecord:
                    success = self._run_screenrecord(timestamp)
                else:
                    success = self._run_normal_commands(timestamp)
                count += 1
                continue_loop = False
                if loop_count is not None:
                    if count < loop_count:
                        continue_loop = True
                else:
                    if interval is not None and interval > 0:
                        continue_loop = True
                    else:
                        continue_loop = False
                if not continue_loop:
                    break
                if interval and interval > 0:
                    wait_end = time.time() + interval
                    while time.time() < wait_end and self._is_running:
                        time.sleep(0.1)
        except Exception as e:
            if not self._stopped:
                self.signals.error.emit(self._format_message(self.LEVEL_ERROR, f"[{self.command.name}] 执行异常: {str(e)}"))
                self._emit_finished(False)
        finally:
            if self.output_file:
                self.output_file.close()
                self.output_file = None

        if not self._stopped and not self._manually_stopped:
            self._emit_finished(success)

    def _run_screenrecord(self, timestamp):
        # ---- 检测是否已有录屏进程在运行（与停止逻辑共用一套探测，避免两份实现漂移） ----
        existing_pid = self._get_screenrecord_pid()
        if existing_pid:
            self.signals.error.emit(
                self._format_message(
                    self.LEVEL_ERROR,
                    f"[{self.command.name}] 设备上已有录屏进程 (PID: {existing_pid}) 在运行，请先停止后再启动"
                )
            )
            self._emit_finished(False)
            return False
        # ------------------------------------------

        try:
            self._device_video_path = f"/sdcard/video_{timestamp}.mp4"
            local_dir = os.path.join(self.output_dir, self.safe_device_serial)
            os.makedirs(local_dir, exist_ok=True)
            self._local_video_path = os.path.join(local_dir, f"video_{timestamp}.mp4")
            self._local_video_path = os.path.normpath(self._local_video_path)
            full_cmd = AdbClient(serial=self.device_serial).command(
                ["shell", f"screenrecord --time-limit 180 {self._device_video_path}"])

            if sys.platform == 'win32':
                self.process = subprocess.Popen(
                    full_cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                    text=True, bufsize=1, universal_newlines=True, encoding='utf-8',
                    errors='backslashreplace',
                    creationflags=subprocess.CREATE_NO_WINDOW
                )
            else:
                self.process = subprocess.Popen(
                    full_cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                    text=True, bufsize=1, universal_newlines=True, encoding='utf-8',
                    errors='backslashreplace',
                    preexec_fn=os.setsid
                )

            import threading
            def read_stream(stream, is_stderr):
                for line in iter(stream.readline, ''):
                    if not self._is_running:
                        break
                    line = line.rstrip('\n')
                    if is_stderr:
                        self.signals.error.emit(
                            self._format_message(self.LEVEL_ERROR, f"[{self.command.name}] {line}")
                        )
                    else:
                        self.signals.output.emit(
                            self._format_message(self.LEVEL_INFO, f"[{self.command.name}] {line}")
                        )

            t_stdout = threading.Thread(target=read_stream, args=(self.process.stdout, False))
            t_stderr = threading.Thread(target=read_stream, args=(self.process.stderr, True))
            t_stdout.daemon = t_stderr.daemon = True
            t_stdout.start()
            t_stderr.start()

            self.process.wait()
            t_stdout.join()
            t_stderr.join()

            if self.process.returncode != 0:
                if self._stopped:
                    self.signals.output.emit(
                        self._format_message(
                            self.LEVEL_INFO,
                            f"[{self.command.name}] 录屏已手动停止"
                        )
                    )
                else:
                    if self.process.returncode == 127:
                        self.signals.error.emit(
                            self._format_message(
                                self.LEVEL_ERROR,
                                f"[{self.command.name}] 设备不支持 screenrecord 命令"
                            )
                        )
                    else:
                        self.signals.error.emit(
                            self._format_message(
                                self.LEVEL_ERROR,
                                f"[{self.command.name}] 录屏命令异常退出 (错误码 {self.process.returncode})"
                            )
                        )
                    return False

            if not self._video_pulled:
                self._pull_video(self._device_video_path, self._local_video_path)

            if not self._stopped:
                success = (self.process.returncode == 0)
            else:
                if self._video_pulled and self._local_video_path:
                    normalized_path = os.path.normpath(self._local_video_path)
                    if os.path.exists(normalized_path):
                        file_size = os.path.getsize(normalized_path)
                        success = (file_size > 1024)
                        if not success:
                            self.signals.error.emit(
                                self._format_message(
                                    self.LEVEL_ERROR, f"[{self.command.name}] 视频文件过小 ({file_size} 字节)"
                                )
                            )
                    else:
                        self.signals.error.emit(
                            self._format_message(
                                self.LEVEL_ERROR, f"[{self.command.name}] 视频文件不存在: {normalized_path}"
                            )
                        )
                        success = False
                else:
                    self.signals.error.emit(
                        self._format_message(self.LEVEL_ERROR, f"[{self.command.name}] 视频未拉取或路径无效")
                    )
                    success = False
            return success

        except Exception as e:
            self.signals.error.emit(
                self._format_message(self.LEVEL_ERROR, f"[{self.command.name}] 录屏异常: {str(e)}")
            )
            return False

    def _pull_video(self, device_path, local_path):
        if self._video_pulled:
            return
        self._video_pulled = True
        success = False
        try:
            adb = AdbClient(serial=self.device_serial)
            result = adb.shell(f"ls {device_path}", timeout=5)
            if not result.ok:
                self.signals.error.emit(self._format_message(self.LEVEL_ERROR, f"[{self.command.name}] 设备上未找到视频文件 {device_path}"))
                success = False
                return

            pull_result = adb.run(["pull", device_path, local_path], timeout=30)
            if pull_result.ok:
                if os.path.exists(local_path):
                    file_size = os.path.getsize(local_path)
                    if file_size < 1024:
                        self.signals.error.emit(self._format_message(self.LEVEL_ERROR, f"[{self.command.name}] 拉取的文件过小 ({file_size} 字节)，可能损坏，已删除"))
                        os.remove(local_path)
                        success = False
                    else:
                        self.signals.output.emit(self._format_message(self.LEVEL_SUCCESS, f"[{self.command.name}] 视频已保存至: {local_path} ({file_size} 字节)"))
                        adb.shell(f"rm -f {device_path}", timeout=5)
                        success = True
                else:
                    self.signals.error.emit(self._format_message(self.LEVEL_ERROR, f"[{self.command.name}] 拉取失败，本地文件未生成"))
                    success = False
            else:
                self.signals.error.emit(self._format_message(self.LEVEL_ERROR, f"[{self.command.name}] 拉取视频失败: {pull_result.stderr}"))
                success = False
        except Exception as e:
            self.signals.error.emit(self._format_message(self.LEVEL_ERROR, f"[{self.command.name}] 拉取视频异常: {str(e)}"))
            success = False
        finally:
            if self._stopped:
                self._emit_finished(success)

    def _check_output_for_errors ( self , output_text ) :
        # logcat 命令的输出不视为错误
        if self.is_logcat:
            return False
        # am start 的无害警告：Activity 已在前台
        if "intent has been delivered to currently running top-most instance" in output_text:
            return False
        if "its current task has been brought to the front" in output_text:
            return False
        error_keywords = [
            "Error", "error", "unable to", "not found", "inaccessible",
            "failed", "Failed", "No such file", "cannot", "Can't",
            "unable to resolve", "Activity not started"
        ]
        for kw in error_keywords:
            if kw in output_text:
                return True
        return False

    def _get_friendly_error(self, output):
        output_lower = output.lower()
        if "permission denied" in output_lower:
            return "权限不足，请尝试 adb root 或检查文件权限"
        elif "no such file" in output_lower or "not found" in output_lower:
            return "文件或目录不存在"
        elif "device offline" in output_lower:  # 新增
            return "设备离线或未连接，请检查 USB 连接或重新插拔设备"
        elif "inaccessible or not found" in output_lower:
            return "设备不支持该命令"
        elif "unable to resolve intent" in output_lower or "activity not started" in output_lower:
            return "无法启动Activity，请检查包名/Activity名称是否正确"
        elif "operation not permitted" in output_lower:
            return "操作不允许，可能需要 root 权限"
        elif "unknown command" in output_lower:
            # 尝试提取未知命令部分
            import re
            match = re.search(r"unknown command ['\"]?(\S+)['\"]?", output, re.IGNORECASE)
            bad_cmd = match.group(1) if match else "未知"
            return f"无效的 ADB 命令：'{bad_cmd}'，请检查命令内容是否正确"
        else:
            lines = [l.strip() for l in output.splitlines() if l.strip() and not l.startswith('[')]
            return lines[0][:150] if lines else "未知错误"

    def _run_normal_commands ( self , timestamp ) :
        commands = [ c.strip ( ) for c in self.command.command_text.split ( ';' ) if c.strip ( ) ]
        overall_success = True
        first_error_reason = None
        sub_results = [ ]

        for raw_cmd in commands :
            if not self._is_running :
                break

            # ---- 新增：判断当前子命令是否为 pull/push ----
            is_push_pull = 'push' in raw_cmd.lower() or 'pull' in raw_cmd.lower()

            cmd = raw_cmd.replace ( "{output_dir}" , self.output_dir ) \
                .replace ( "{device_serial}" , self.device_serial ) \
                .replace ( "{safe_device_serial}" , self.safe_device_serial ) \
                .replace ( "{timestamp}" , timestamp )

            if 'pull' in cmd :
                try :
                    args = shlex.split ( cmd , posix = False )
                    if 'pull' in args :
                        pull_index = args.index ( 'pull' )
                        if len ( args ) > pull_index + 2 :
                            local_path = args [ -1 ]
                            raw_local = local_path.strip ( '"' ).strip ( "'" )
                            norm_local = os.path.normpath ( raw_local )
                            parent_dir = os.path.dirname ( norm_local )
                            if parent_dir :
                                os.makedirs ( parent_dir , exist_ok = True )
                            cmd = ' '.join ( args )
                except Exception as e :
                    self.signals.error.emit (
                        self._format_message ( self.LEVEL_ERROR , f"[{self.command.name}] 准备 pull 命令失败: {e}" )
                        )
                    overall_success = False
                    continue

            full_cmd = f"adb -s {self.device_serial} {cmd}"

            if sys.platform == 'win32' :
                self.process = subprocess.Popen (
                    full_cmd , shell = True , stdout = subprocess.PIPE , stderr = subprocess.PIPE ,
                    text = True , bufsize = 1 , universal_newlines = True ,
                    encoding = 'utf-8' , errors = 'backslashreplace' ,
                    creationflags = subprocess.CREATE_NO_WINDOW
                    )
            else :
                self.process = subprocess.Popen (
                    full_cmd , shell = True , stdout = subprocess.PIPE , stderr = subprocess.PIPE ,
                    text = True , bufsize = 1 , universal_newlines = True ,
                    encoding = 'utf-8' , errors = 'backslashreplace' ,
                    preexec_fn = os.setsid
                    )

            self._output_lines.clear()

            import threading
            is_logcat = self.is_logcat

            def read_stream ( stream , is_stderr ) :
                for line in iter(stream.readline, ''):
                    if not self._is_running:
                        break
                    line = line.rstrip('\n')
                    if line:
                        self._output_lines.append(line)
                        if is_stderr:
                            # 判断是否为 am start 的无害警告
                            is_harmless_warning = (
                                    "its current task has been brought to the front" in line or
                                    "intent has been delivered to currently running top-most instance" in line
                            )
                            if not is_logcat and not self.is_monkey:
                                if is_harmless_warning:
                                    # 降级为普通信息
                                    self.signals.output.emit(
                                        self._format_message(self.LEVEL_INFO, f"[{self.command.name}] {line}"))
                                    # 额外友好提示（可选）
                                    if "intent has been delivered" in line and self.command.name == "原生设置页面":
                                        self.signals.output.emit(self._format_message(
                                            self.LEVEL_INFO,
                                            f"[{self.command.name}] 提示：页面已处于前台，无需重复启动"
                                        ))
                                else:
                                    # ---- 修改点：对 push/pull 特殊处理 ----
                                    if is_push_pull:
                                        # 检查是否包含真正的错误关键词
                                        error_keywords = ["error", "failed", "permission denied",
                                                          "not found", "cannot", "unable", "denied"]
                                        if any(kw in line.lower() for kw in error_keywords):
                                            self.signals.error.emit(
                                                self._format_message(self.LEVEL_ERROR, f"[{self.command.name}] {line}"))
                                        else:
                                            # 作为普通信息输出（不再显示红色 ❌）
                                            self.signals.output.emit(
                                                self._format_message(self.LEVEL_INFO, f"[{self.command.name}] {line}"))
                                    else:
                                        # 对于多命令组合，不逐行输出 stderr，避免重复刷屏
                                        if not self._is_multi_command:
                                            self.signals.error.emit(
                                                self._format_message(self.LEVEL_ERROR, f"[{self.command.name}] {line}"))
                                        # 如果是多命令，则静默收集，后续只会输出统一的失败原因
                            if self.output_file:
                                self.output_file.write(f"[ERROR] {line}\n")
                                self.output_file.flush()
                        else :
                            # 对于 stdout，如果不是 logcat 且不是 monkey 命令，才输出信息
                            if not is_logcat and not self.is_monkey :
                                # 多命令组合时不显示成功的子步骤输出，减少刷屏
                                if not self._is_multi_command :
                                    self.signals.output.emit (
                                        self._format_message ( self.LEVEL_INFO , f"[{self.command.name}] {line}" )
                                        )
                            if self.output_file :
                                self.output_file.write ( f"{line}\n" )
                                self.output_file.flush ( )

            t_stdout = threading.Thread ( target = read_stream , args = (self.process.stdout , False) )
            t_stderr = threading.Thread ( target = read_stream , args = (self.process.stderr , True) )
            t_stdout.daemon = t_stderr.daemon = True
            t_stdout.start ( )
            t_stderr.start ( )

            try :
                if self.is_logcat or is_push_pull or self.command.no_timeout :
                    timeout = None
                else :
                    timeout = 30

                returncode = self.process.wait ( timeout = timeout )
                t_stdout.join ( timeout = 5 )
                t_stderr.join ( timeout = 5 )

                full_output = "\n".join ( self._output_lines )
                is_really_success = (returncode == 0)
                if is_really_success and self._check_output_for_errors ( full_output ) :
                    is_really_success = False

                sub_results.append ( (cmd.split ( ) [ 0 ] [ :20 ] , is_really_success) )

                if not is_really_success :
                    if not self._stopped:
                        if self._is_multi_command:
                            # 多命令时只记录第一个失败的原因，不立即输出
                            if first_error_reason is None:
                                first_error_reason = self._get_friendly_error(full_output)
                        else:
                            friendly_error = self._get_friendly_error(full_output)
                            self.signals.error.emit(self._format_message(self.LEVEL_ERROR,
                                                                         f"[{self.command.name}] 失败原因：{friendly_error}"))
                    overall_success = False
                else :
                    # 单条命令执行成功时，根据输出内容生成友好提示
                    if not self._is_multi_command and not self.is_monkey:
                        if 'pull' in cmd.lower():
                            import re
                            # 匹配 "X files pulled" 或 "X file pulled"
                            match = re.search(r'(\d+)\s+files? pulled', full_output, re.IGNORECASE)
                            if match:
                                pulled_count = int(match.group(1))
                                if pulled_count == 0:
                                    # 提取远程路径用于提示
                                    path_match = re.search(r'pull[:\s]+([^\s]+)', cmd, re.IGNORECASE)
                                    remote_path = path_match.group(1) if path_match else "指定路径"
                                    msg = f"拉取完成，但未获取到文件（目录可能为空或无匹配项）：{remote_path}"
                                    self.signals.output.emit(
                                        self._format_message(self.LEVEL_WARNING, f"[{self.command.name}] {msg}"))
                        # 其他情况：不再额外发送“命令执行成功”，因为关键输出已经在 stdout 中显示了
                        # （例如 pull 成功会显示 "1 file pulled"，截图命令会显示 "1 file pushed" 等）
                        # 但如果 stdout 没有任何输出，则补充一条提示
                        elif not full_output.strip ( ) :
                            self.signals.output.emit (
                                self._format_message (
                                    self.LEVEL_SUCCESS , f"[{self.command.name}] 命令执行成功（无输出）"
                                    )
                                )

            except subprocess.TimeoutExpired :
                if not self._stopped :
                    self.signals.error.emit (
                        self._format_message ( self.LEVEL_ERROR , f"[{self.command.name}] 命令执行超时（30秒），强制终止" )
                        )
                self._kill_process_tree(self.process, timeout=5)
                overall_success = False
            except Exception as e :
                if not self._stopped :
                    self.signals.error.emit (
                        self._format_message ( self.LEVEL_ERROR , f"[{self.command.name}] 命令执行异常: {str ( e )}" )
                        )
                overall_success = False
            finally :
                self._kill_process_tree(self.process)

        if self._is_multi_command and sub_results and not self._stopped and not self.is_monkey :  # Monkey 不显示汇总
            if self.command.name not in ["截图", "权限获取", "root权限获取"]:
                success_count = sum(1 for _, ok in sub_results if ok)
                total = len(sub_results)
                if success_count == total:
                    level = self.LEVEL_SUCCESS
                    summary = f"全部 {total} 项任务执行成功"
                else:
                    level = self.LEVEL_WARNING if success_count > 0 else self.LEVEL_ERROR
                    summary = f"{total} 项任务：{success_count} 项成功，{total - success_count} 项失败"
                self.signals.output.emit(self._format_message(level, f"[{self.command.name}] {summary}"))

        if self._is_multi_command and not overall_success and first_error_reason:
            self.signals.error.emit(
                self._format_message(self.LEVEL_ERROR, f"[{self.command.name}] 失败原因：{first_error_reason}"))

        return overall_success


class AdbManager(QObject):
    device_list_updated = Signal(list)
    command_output = Signal(str)
    command_started = Signal(int, str)
    command_finished = Signal(int, str, bool)
    command_stopped = Signal ( int , str )
    def __init__(self):
        super().__init__()
        self.current_device = None
        self.devices = []
        self._cached_devices_str = ""
        self.threadpool = QThreadPool()
        self.threadpool.setMaxThreadCount(MAX_CONCURRENT_TASKS)
        self.running_commands = {}

    def refresh_devices(self):
        def get_devices():
            try:
                return [{'serial': s, 'status': st} for s, st in AdbClient().devices(timeout=5)]
            except Exception:
                return []

        def worker():
            devices = get_devices()
            current_str = str(sorted([(d['serial'], d['status']) for d in devices]))
            if current_str != self._cached_devices_str:
                self._cached_devices_str = current_str
                self.devices = devices
                self.device_list_updated.emit(devices)

        threading.Thread(target=worker, daemon=True).start()

    def set_current_device(self, serial):
        self.current_device = serial

    def execute_command(self, command_obj, device_serial, output_dir):
        signals = CommandSignals()
        signals.started.connect(self.command_started)
        signals.output.connect(self.command_output)
        signals.finished.connect(self.on_command_finished)
        signals.error.connect(lambda msg: self.command_output.emit(f"<span style='color:#A6402F;'>{msg}</span>"))
        signals.stopped.connect ( self.on_command_stopped )  # 新增连接
        runnable = AdbCommandRunnable(command_obj, device_serial, output_dir, signals)
        self.running_commands[command_obj.id] = runnable
        self.threadpool.start(runnable)

    def on_command_finished(self, cmd_id, cmd_name, success):
        self.running_commands.pop(cmd_id, None)
        self.command_finished.emit(cmd_id, cmd_name, success)

    def stop_command(self, command_id):
        runnable = self.running_commands.get(command_id)
        if runnable:
            runnable.stop()

    def stop_all(self, timeout_ms=5000):
        """
        停止所有正在运行的命令，并等待线程池收尾。

        退出程序时必须调用：QThreadPool 在解释器结束时会等待所有运行中的任务，
        若 logcat 这类无限命令还在跑，进程将永远退不出去（任务管理器里残留）；
        同时子进程 adb.exe 也会变成孤儿进程。

        :param timeout_ms: 等待线程池结束的最长时间（毫秒）
        :return: True 表示所有任务都已结束
        """
        # 先让每个任务自行终止（内部会 terminate/kill 对应的 adb 子进程）
        for runnable in list(self.running_commands.values()):
            try:
                runnable.stop()
            except Exception as exc:
                logger.debug("忽略异常（stop_all）: %s", exc, exc_info=True)

        # 再等待线程池真正空闲，避免退出时线程仍在操作已销毁的对象
        try:
            done = self.threadpool.waitForDone(timeout_ms)
        except Exception:
            done = False

        self.running_commands.clear()
        return done

    def start_scrcpy(self, device_serial):
        def run():
            scrcpy_name = 'scrcpy.exe' if sys.platform == 'win32' else 'scrcpy'
            scrcpy_path = resource_path(os.path.join('tools', scrcpy_name))
            if not os.path.exists(scrcpy_path):
                scrcpy_path = "scrcpy"
            if sys.platform == 'win32':
                subprocess.Popen(
                    [scrcpy_path, "-s", device_serial],
                    stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                    creationflags=subprocess.CREATE_NO_WINDOW
                )
            else:
                subprocess.Popen(
                    [scrcpy_path, "-s", device_serial],
                    stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL
                )

        threading.Thread(target=run, daemon=True).start()

    # 新增槽函数，转发信号
    def on_command_stopped(self, cmd_id, cmd_name):
        self.command_stopped.emit(cmd_id, cmd_name)