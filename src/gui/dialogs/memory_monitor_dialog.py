# src/gui/dialogs/memory_monitor_dialog.py
"""
内存监控对话框模块
提供启动/停止后台内存监控、拉取数据文件、生成分析报告等功能。
采用 setsid 启动独立守护进程，避免被杀。
"""
import os
import subprocess
import sys
import threading
import time
from datetime import datetime
from PySide6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QPushButton,
    QLabel, QProgressBar, QMessageBox, QTextEdit, QInputDialog
)
from PySide6.QtCore import Signal, Slot

from src.utils.helpers import resource_path, get_adb_path
# 直接导入底层解析和生成函数
from src.utils.memory_analyzer import parse_memory_data, _generate_chart, _export_to_excel


class MemoryMonitorDialog(QDialog):
    log_signal = Signal(str)

    REMOTE_WORK_DIR = "/data/local/tmp/dumpmeminfo"
    REMOTE_SCRIPT_PATH = f"{REMOTE_WORK_DIR}/dump.sh"
    REMOTE_OUTPUT_PATH = f"{REMOTE_WORK_DIR}/output.txt"

    def __init__(self, adb_manager, device_serial, output_dir, parent=None):
        super().__init__(parent)
        self.adb_manager = adb_manager
        self.device_serial = device_serial
        self.output_dir = output_dir
        self.local_data_file = None
        self.monitoring = False

        self.setWindowTitle("内存监控")
        self.resize(550, 450)
        self.setup_ui()
        self.log_signal.connect(self.append_log)

    def setup_ui(self):
        layout = QVBoxLayout(self)

        info = QLabel(
            "功能说明：\n"
            "1. 启动监控后，设备后台每30秒记录一次 com.baidu.naviauto 的内存信息。\n"
            "2. 停止监控后，可拉取数据文件到本地。\n"
            "3. 生成报告将解析数据并生成 HTML 图表和 Excel 文件。\n"
            "⚠️ 需要 root 权限，脚本存放于 /data/local/tmp/"
        )
        info.setWordWrap(True)
        info.setStyleSheet("color: #2c3e50; margin-bottom: 10px;")
        layout.addWidget(info)

        self.log_text = QTextEdit()
        self.log_text.setReadOnly(True)
        layout.addWidget(self.log_text)

        self.progress = QProgressBar()
        self.progress.setVisible(False)
        layout.addWidget(self.progress)

        btn_layout = QHBoxLayout()
        self.start_btn = QPushButton("启动监控")
        self.pull_btn = QPushButton("拉取数据")
        self.report_btn = QPushButton("生成报告")

        self.start_btn.clicked.connect(self.start_monitor)
        self.pull_btn.clicked.connect(self.pull_data)
        self.report_btn.clicked.connect(self.generate_report)

        btn_layout.addWidget(self.start_btn)
        btn_layout.addWidget(self.pull_btn)
        btn_layout.addWidget(self.report_btn)
        layout.addLayout(btn_layout)

    @Slot(str)
    def append_log(self, text):
        self.log_text.append(text)

    def _run_adb(self, cmd, timeout=15):
        adb = get_adb_path()
        full_cmd = [adb, "-s", self.device_serial] + cmd
        try:
            creationflags = subprocess.CREATE_NO_WINDOW if sys.platform == 'win32' else 0
            proc = subprocess.run(
                full_cmd, capture_output=True, text=True,
                timeout=timeout, creationflags=creationflags, encoding='utf-8', errors='replace'
            )
            return proc.returncode, proc.stdout, proc.stderr
        except subprocess.TimeoutExpired:
            return -1, "", "Timeout"
        except Exception as e:
            return -1, "", str(e)

    def _check_root(self):
        rc, out, _ = self._run_adb(["shell", "id"], timeout=5)
        return rc == 0 and "uid=0" in out

    def ensure_environment(self):
        if not self._check_root():
            self.log_signal.emit("❌ 设备未 root 或 ADB 无 root 权限。")
            return False

        self.log_signal.emit("正在准备设备环境...")

        # 1. 确保本地脚本存在（如果不存在则自动创建）
        local_script = resource_path(os.path.join("resources", "scripts", "dump.sh"))
        if not os.path.exists(local_script):
            self.log_signal.emit(f"⚠️ 本地脚本不存在: {local_script}，正在自动创建...")
            os.makedirs(os.path.dirname(local_script), exist_ok=True)
            default_script = '''#!/system/bin/sh
    # 内存监控脚本，每30秒记录一次 com.baidu.naviauto 的内存信息
    PACKAGE="com.baidu.naviauto"
    OUTPUT_DIR="/data/local/tmp/dumpmeminfo"
    mkdir -p $OUTPUT_DIR
    cd $OUTPUT_DIR
    echo "=== 监控启动于 $(date) ===" >> dump.log
    i=0
    while true; do
        i=$((i+1))
        date "+%Y-%m-%d %H:%M:%S" >> output.txt
        dumpsys meminfo $PACKAGE >> output.txt 2>> dump.log
        sleep 30
    done
    '''
            with open(local_script, 'w', encoding='utf-8', newline='\n') as f:
                f.write(default_script)
            # 设置可执行权限（Windows 下忽略）
            try:
                os.chmod(local_script, 0o755)
            except:
                pass
            self.log_signal.emit("✅ 已自动创建默认脚本")

        # 2. 在设备上创建工作目录
        rc, _, err = self._run_adb(["shell", "mkdir", "-p", self.REMOTE_WORK_DIR])
        if rc != 0:
            self.log_signal.emit(f"❌ 创建工作目录失败: {err}")
            return False

        # 3. 推送脚本
        self.log_signal.emit("推送 dump.sh ...")
        rc, _, err = self._run_adb(["push", local_script, self.REMOTE_SCRIPT_PATH])
        if rc != 0:
            self.log_signal.emit(f"❌ 推送脚本失败: {err}")
            # 尝试用 adb root 再试一次
            self.log_signal.emit("尝试执行 adb root 后重新推送...")
            self._run_adb(["root"])
            import time
            time.sleep(2)
            rc, _, err = self._run_adb(["push", local_script, self.REMOTE_SCRIPT_PATH])
            if rc != 0:
                self.log_signal.emit(f"❌ 重新推送仍然失败: {err}")
                return False

        # 4. 赋予执行权限
        self._run_adb(["shell", "chmod", "755", self.REMOTE_SCRIPT_PATH])

        # 5. 验证脚本是否存在且可执行
        rc, out, _ = self._run_adb(["shell", f"test -f {self.REMOTE_SCRIPT_PATH} && echo OK"])
        if "OK" not in out:
            self.log_signal.emit(f"❌ 脚本验证失败，设备上未找到 {self.REMOTE_SCRIPT_PATH}")
            return False

        self.log_signal.emit("✅ 环境准备完成。")
        return True

    def _get_monitor_pids(self):
        """返回 dump.sh 相关进程的 PID（精确匹配脚本路径）"""
        pids = []
        # 使用 pgrep -f 精确匹配命令行中包含 dump.sh 的进程
        rc, out, _ = self._run_adb(["shell", "pgrep -f 'dump.sh'"], timeout=5)
        if rc == 0 and out.strip():
            for pid in out.splitlines():
                if pid.strip().isdigit():
                    pids.append(pid.strip())
        # 如果 pgrep 不可用，回退到 ps + grep 但排除 tcpdump 等
        if not pids:
            rc, out, _ = self._run_adb(["shell", "ps -A | grep -E 'dump\\.sh' | grep -v grep"], timeout=5)
            if rc == 0 and out.strip():
                for line in out.splitlines():
                    parts = line.split()
                    if len(parts) >= 2 and parts[1].isdigit():
                        pids.append(parts[1])
        return pids

    def _check_monitor_running(self):
        return len(self._get_monitor_pids()) > 0

    def start_monitor(self):
        def worker():
            # 先确保环境就绪（推送脚本等）
            if not self.ensure_environment():
                self.log_signal.emit("❌ 环境准备失败，无法启动监控")
                return

            # 检查是否已有监控进程
            if self._check_monitor_running():
                self.log_signal.emit("⚠️ 监控已在运行中")
                return

            self.log_signal.emit("正在启动监控...")

            # 构建完整的 shell 命令：cd 到工作目录，使用 nohup 启动脚本，重定向所有输出
            # 关键点：整个命令必须作为单个字符串传递给 adb shell，并且使用 '&' 后台运行
            full_command = f"cd {self.REMOTE_WORK_DIR} && nohup sh dump.sh > output.txt 2>&1 < /dev/null &"
            # 使用 adb shell 执行该命令
            rc, out, err = self._run_adb(["shell", full_command], timeout=10)
            if rc != 0:
                self.log_signal.emit(f"⚠️ 启动命令返回非零: {rc}, err: {err}")

            # 等待进程启动
            time.sleep(3)

            if self._check_monitor_running():
                pids = self._get_monitor_pids()
                self.log_signal.emit(f"✅ 监控已启动 (PID: {', '.join(pids)})")
                # 启动后验证输出文件是否生成
                threading.Thread(target=self._verify_output_file, daemon=True).start()
            else:
                self.log_signal.emit("❌ 启动监控失败，未检测到进程")
                # 尝试读取可能的错误输出
                self._read_error_log(f"{self.REMOTE_WORK_DIR}/error.log")
                # 可选：直接手动测试后台启动是否可行
                self.log_signal.emit("提示：请手动执行以下命令测试后台启动：")
                self.log_signal.emit(f"adb shell 'cd {self.REMOTE_WORK_DIR} && nohup sh dump.sh > output.txt 2>&1 &'")

        threading.Thread(target=worker, daemon=True).start()

    def _read_error_log(self, log_file):
        """读取启动时的错误日志"""
        rc, out, _ = self._run_adb(["shell", f"cat {log_file} 2>/dev/null"], timeout=5)
        if rc == 0 and out.strip():
            self.log_signal.emit(f"📄 启动错误日志:\n{out.strip()}")
        else:
            self.log_signal.emit("📄 无错误日志输出。")

    def _test_script_directly(self):
        """在前台短暂执行脚本以捕获错误输出（不阻塞）"""
        def run_test():
            self.log_signal.emit("🔍 正在测试脚本直接执行（超时10秒）...")
            test_cmd = f"cd {self.REMOTE_WORK_DIR} && timeout 10 sh dump.sh 2>&1"
            rc, out, _ = self._run_adb(["shell", test_cmd], timeout=15)
            if out.strip():
                self.log_signal.emit(f"📄 脚本测试输出:\n{out.strip()}")
            else:
                self.log_signal.emit("📄 脚本测试无输出（可能正常，但未产生数据）。")
            # 同时检查 dumpsys 是否能正常运行
            rc2, out2, _ = self._run_adb(["shell", "dumpsys meminfo --package com.baidu.naviauto 2>&1 | head -5"],
                                         timeout=10)
            if rc2 == 0 and out2.strip():
                self.log_signal.emit(f"✅ dumpsys 测试正常:\n{out2.strip()[:200]}")
            else:
                self.log_signal.emit("❌ dumpsys 命令可能失败，请检查包名是否存在及权限。")
        threading.Thread(target=run_test, daemon=True).start()

    def _verify_output_file(self):
        """等待10秒后检查输出文件是否被创建/更新"""
        time.sleep(10)
        rc, out, _ = self._run_adb(["shell", f"ls -l {self.REMOTE_OUTPUT_PATH} 2>/dev/null || echo 'NOT_FOUND'"], timeout=5)
        if "NOT_FOUND" in out:
            self.log_signal.emit("⚠️ 输出文件尚未生成，请检查脚本是否正确执行。")
        else:
            self.log_signal.emit(f"✅ 输出文件已创建: {out.strip()}")

    def _diagnose(self):
        """保留原有的诊断信息，并整合测试"""
        self.log_signal.emit("🔍 正在收集诊断信息...")
        # 检查脚本内容（前5行）
        rc, out, _ = self._run_adb(["shell", f"head -5 {self.REMOTE_SCRIPT_PATH}"])
        if rc == 0:
            self.log_signal.emit(f"脚本头部:\n{out.strip()}")
        # 检查工作目录内容
        rc, out, _ = self._run_adb(["shell", f"ls -la {self.REMOTE_WORK_DIR}/"])
        if rc == 0:
            self.log_signal.emit(f"工作目录内容:\n{out.strip()}")
        self._test_script_directly()

    def stop_monitor(self):
        def worker():
            pids = self._get_monitor_pids()
            if not pids:
                self.log_signal.emit("⚠️ 没有运行中的监控任务。")
                return

            self.log_signal.emit(f"正在终止进程: {', '.join(pids)}")
            # 直接使用 pkill -f 更可靠
            rc, _, _ = self._run_adb(["shell", "pkill -f 'sh dump.sh'"], timeout=10)
            if rc == 0:
                time.sleep(1)
                remaining = self._get_monitor_pids()
                if remaining:
                    # 强制杀掉残留
                    for pid in remaining:
                        self._run_adb(["shell", f"kill -9 {pid}"])
                    time.sleep(1)
                    if self._check_monitor_running():
                        self.log_signal.emit(f"❌ 仍有进程残留: {self._get_monitor_pids()}")
                    else:
                        self.log_signal.emit("✅ 监控已停止（强制终止）。")
                else:
                    self.log_signal.emit("✅ 监控已停止。")
            else:
                self.log_signal.emit("⚠️ pkill 失败，尝试 killall...")
                rc, _, _ = self._run_adb(["shell", "killall dump.sh"], timeout=10)
                if rc == 0:
                    self.log_signal.emit("✅ 监控已停止（killall）。")
                else:
                    self.log_signal.emit("❌ 无法停止监控进程，请手动执行 adb shell pkill -f dump.sh")

        threading.Thread(target=worker, daemon=True).start()

    def pull_data(self):
        safe_serial = self.device_serial.replace(':', '_')
        device_dir = os.path.join(self.output_dir, safe_serial)
        os.makedirs(device_dir, exist_ok=True)
        local_file = os.path.join(device_dir, "memory_output.txt")

        def worker():
            self.log_signal.emit("正在拉取 output.txt ...")
            self.progress.setVisible(True)
            self.progress.setRange(0, 0)

            rc, out, _ = self._run_adb(["shell", f"test -f {self.REMOTE_OUTPUT_PATH} && echo exists"])
            if "exists" not in out:
                self.log_signal.emit(f"⚠️ 设备上未找到 {self.REMOTE_OUTPUT_PATH}")
                self.progress.setVisible(False)
                return

            rc, _, err = self._run_adb(["pull", self.REMOTE_OUTPUT_PATH, local_file], timeout=30)
            self.progress.setVisible(False)
            if rc == 0:
                self.local_data_file = local_file
                self.log_signal.emit(f"✅ 数据已保存至: {local_file}")
            else:
                self.log_signal.emit(f"❌ 拉取失败: {err}")

        threading.Thread(target=worker, daemon=True).start()

    def generate_report(self):
        if not self.local_data_file or not os.path.exists(self.local_data_file):
            QMessageBox.warning(self, "警告", "请先拉取数据文件")
            return

        car_model, ok = QInputDialog.getText(self, "车型信息", "请输入车型型号:", text="3BPA_monkey")
        if not ok:
            return

        def worker():
            self.log_signal.emit("正在解析数据并生成报告...")
            self.progress.setVisible(True)
            self.progress.setRange(0, 0)
            try:
                data = parse_memory_data(self.local_data_file)
                if not data.get('timestamp'):
                    self.log_signal.emit("❌ 未解析到有效数据。")
                    return
                safe_serial = self.device_serial.replace(':', '_')
                report_dir = os.path.join(self.output_dir, f"{safe_serial}_memory_reports")
                os.makedirs(report_dir, exist_ok=True)

                # 修复文件名中的连续下划线
                start_time = data['timestamp'][0].replace(':', '-')
                end_time = data['timestamp'][-1].replace(':', '-')
                if not start_time:
                    start_time = datetime.now().strftime("%Y%m%d_%H%M%S")
                    end_time = start_time
                base_name = f"{car_model}_{start_time}_{end_time}"
                chart_path = os.path.join(report_dir, f"{base_name}.html")
                excel_path = os.path.join(report_dir, f"{base_name}.xlsx")

                _generate_chart(data, chart_path, car_model)
                _export_to_excel(data, excel_path)

                self.log_signal.emit(f"✅ 报告生成完毕!\n图表: {chart_path}\nExcel: {excel_path}")
                import webbrowser
                webbrowser.open(chart_path)
            except Exception as e:
                self.log_signal.emit(f"❌ 生成报告出错: {e}")
            finally:
                self.progress.setVisible(False)

        threading.Thread(target=worker, daemon=True).start()