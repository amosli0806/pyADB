# src/gui/dialogs/monkey_dialog.py
import os
import re
import subprocess
import sys
import tempfile
import threading
import time
from PySide6.QtWidgets import (QDialog, QVBoxLayout, QHBoxLayout, QFormLayout,
                               QLineEdit, QSpinBox, QPushButton, QLabel,
                               QMessageBox, QGroupBox, QCheckBox, QComboBox,
                               QScrollArea, QWidget, QSizePolicy, QLayout,
                               QListWidget, QListWidgetItem)
from PySide6.QtCore import Qt
from PySide6.QtGui import QIcon
import qtawesome as qta
from src.gui.toast import Toast
from src.core.adb_client import AdbClient


class MonkeyDialog(QDialog):
    def __init__(self, adb_manager, device_serial, parent=None):
        super().__init__(parent)
        self.adb_manager = adb_manager
        self.device_serial = device_serial
        self.adb = AdbClient(serial=device_serial)
        self.setWindowTitle("Monkey测试")
        self.resize(500, 630)
        self.process = None          # 当前对话框启动的 Monkey 进程
        self.monkey_pid = None       # 检测到的 Monkey 进程 PID
        self.monitor_thread = None   # 用于监控 Monkey 进程的线程
        self.setup_ui()
        self.check_running_monkey()   # 启动时检查是否有 Monkey 在运行

        # 白名单文件模式下、设备侧白名单文件的路径（结束后要清理）
        self._whitelist_remote = None

    def setup_ui(self):
        # 主布局
        main_layout = QVBoxLayout(self)

        # 基本参数表单
        form = QFormLayout()
        self.event_count = QSpinBox()
        self.event_count.setRange(1, 100000000)
        self.event_count.setValue(50000000)
        form.addRow("事件数量 :", self.event_count)

        self.seed = QSpinBox()
        self.seed.setRange(0, 999)
        self.seed.setValue(733)
        form.addRow("随机种子 :", self.seed)

        # 包名输入（支持多包名，逗号分隔）
        pkg_layout = QHBoxLayout()
        self.pkg = QLineEdit()
        self.pkg.setPlaceholderText("可选，留空则测试所有应用。多包名用英文逗号分隔")
        self.pkg.setText("com.baidu.naviauto")
        # ---- 选择包名按钮：蓝色，始终启用 ----
        self.select_pkg_btn = QPushButton(" 选择")
        self.select_pkg_btn.setIcon(qta.icon('fa6s.list', color='#1F1D1A'))
        self.select_pkg_btn.setFixedHeight(32)
        self.select_pkg_btn.clicked.connect(self.choose_packages)

        pkg_layout.addWidget(self.pkg)
        pkg_layout.addWidget(self.select_pkg_btn)
        form.addRow("  包  名   :", pkg_layout)

        # ---- 启动方式：部分受限车机 ROM 对 -p 参数不生效（点开始后设备没动作），
        #      白名单文件方式（--pkg-whitelist-file）在这类系统上通常可用
        self.start_mode = QComboBox()
        self.start_mode.addItem("-p 参数（默认）", "param")
        self.start_mode.addItem("白名单文件（兼容受限系统）", "whitelist")
        form.addRow("  启动方式 :", self.start_mode)

        main_layout.addLayout(form)

        # 高级选项分组框（默认折叠）
        self.advanced_group = QGroupBox("高级选项")
        self.advanced_group.setCheckable(True)
        self.advanced_group.setProperty("checkable", True)   # QSS 用它给标题让出勾选框位置
        self.advanced_group.setChecked(True)
        self.advanced_group.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        main_layout.addWidget(self.advanced_group)

        # 高级选项布局（使用滚动区域）
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)   # 只保留纵向滚动
        scroll.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        scroll_widget = QWidget()
        # 横向用 Ignored：让内部表单缩到视口宽度，而不是按 sizeHint 撑出视口再被裁掉
        scroll_widget.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Expanding)
        scroll_widget.setMinimumHeight(400)
        scroll_layout = QVBoxLayout(scroll_widget)
        scroll_layout.setContentsMargins(5, 5, 5, 5)

        # 事件间隔
        throttle_layout = QFormLayout()
        self.throttle = QSpinBox()
        self.throttle.setRange(0, 10000)
        self.throttle.setValue(800)
        self.throttle.setSuffix(" 毫秒")
        throttle_layout.addRow(" 事 件 间 隔  :", self.throttle)
        scroll_layout.addLayout(throttle_layout)

        # 事件百分比
        pct_layout = QFormLayout()
        self.pct_touch = QSpinBox()
        self.pct_touch.setRange(0, 100)
        self.pct_touch.setValue(95)
        pct_layout.addRow(" 触摸事件%  :", self.pct_touch)

        self.pct_motion = QSpinBox()
        self.pct_motion.setRange(0, 100)
        self.pct_motion.setValue(5)
        pct_layout.addRow(" 滑动事件%  :", self.pct_motion)

        self.pct_trackball = QSpinBox()
        self.pct_trackball.setRange(0, 100)
        self.pct_trackball.setValue(0)
        pct_layout.addRow("轨迹球事件% :", self.pct_trackball)

        self.pct_nav = QSpinBox()
        self.pct_nav.setRange(0, 100)
        self.pct_nav.setValue(0)
        pct_layout.addRow(" 导航事件%  :", self.pct_nav)

        self.pct_majornav = QSpinBox()
        self.pct_majornav.setRange(0, 100)
        self.pct_majornav.setValue(0)
        pct_layout.addRow(" 主要导航%  :", self.pct_majornav)

        self.pct_appswitch = QSpinBox()
        self.pct_appswitch.setRange(0, 100)
        self.pct_appswitch.setValue(0)
        pct_layout.addRow(" 应用切换%  :", self.pct_appswitch)

        self.pct_flip = QSpinBox()
        self.pct_flip.setRange(0, 100)
        self.pct_flip.setValue(0)
        pct_layout.addRow(" 翻转事件%  :", self.pct_flip)

        self.pct_anyevent = QSpinBox()
        self.pct_anyevent.setRange(0, 100)
        self.pct_anyevent.setValue(0)
        pct_layout.addRow(" 其他事件%  :", self.pct_anyevent)

        self.pct_syskeys = QSpinBox()
        self.pct_syskeys.setRange(0, 100)
        self.pct_syskeys.setValue(0)
        pct_layout.addRow(" 系统按键%  :", self.pct_syskeys)

        scroll_layout.addLayout(pct_layout)

        # 忽略异常选项
        ignore_layout = QHBoxLayout()
        self.ignore_crashes = QCheckBox("忽略崩溃")
        self.ignore_crashes.setChecked(True)
        self.ignore_timeouts = QCheckBox("忽略超时")
        self.ignore_timeouts.setChecked(True)
        self.ignore_security = QCheckBox("忽略安全异常")
        self.ignore_security.setChecked(True)
        self.ignore_native = QCheckBox("忽略原生崩溃")
        self.ignore_native.setChecked(True)
        self.monitor_native = QCheckBox("监控原生崩溃")
        self.monitor_native.setChecked(True)
        ignore_layout.addWidget(self.ignore_crashes)
        ignore_layout.addWidget(self.ignore_timeouts)
        ignore_layout.addWidget(self.ignore_security)
        ignore_layout.addWidget(self.ignore_native)
        ignore_layout.addWidget(self.monitor_native)
        scroll_layout.addLayout(ignore_layout)

        # 详细程度
        verbosity_layout = QFormLayout()
        self.verbosity = QComboBox()
        self.verbosity.addItems(["0 (默认)", "1 (-v)", "2 (-v -v)", "3 (-v -v -v)"])
        self.verbosity.setCurrentIndex(3)
        verbosity_layout.addRow("详细程度:", self.verbosity)
        scroll_layout.addLayout(verbosity_layout)

        # 提示标签
        tip_label = QLabel("提示：各事件百分比总和不必为100，剩余比例将由系统自动分配。")
        tip_label.setWordWrap(True)
        tip_label.setStyleSheet("color: gray; font-size: 9pt;")
        scroll_layout.addWidget(tip_label)

        scroll.setWidget(scroll_widget)
        advanced_layout = QVBoxLayout(self.advanced_group)
        advanced_layout.setContentsMargins(0, 25, 0, 0)
        advanced_layout.addWidget(scroll)

        # 按钮区域
        btn_layout = QHBoxLayout()

        # ---- 开始按钮：绿色 ----
        self.start_btn = QPushButton("开始")
        self.start_btn.setFixedHeight(32)
        self.start_btn.setObjectName("primaryBtn")
        self.start_btn.clicked.connect(self.start_monkey)

        # ---- 停止按钮：红色 ----
        self.stop_btn = QPushButton("停止")
        self.stop_btn.setFixedHeight(32)
        self.stop_btn.setObjectName("stopBtn")
        self.stop_btn.clicked.connect(self.stop_monkey)
        self.stop_btn.setEnabled(True)

        btn_layout.addWidget(self.start_btn)
        btn_layout.addWidget(self.stop_btn)
        main_layout.addLayout(btn_layout)

        self.status = QLabel("就绪")
        main_layout.addWidget(self.status)

        main_layout.setStretchFactor(self.advanced_group, 1)
        self.layout().setSizeConstraint(QLayout.SetMinimumSize)

    # ---------- 辅助方法：更新按钮文本和图标 ----------
    def _set_start_button_state(self, enabled, override_text=None):
        """
        设置开始按钮的状态。
        enabled: True 启用（显示图标+文字），False 禁用（清除图标，只显示文字）。
        override_text: 可选，强制指定文字内容（不覆盖图标逻辑）。
        """
        if enabled:
            # 启用：显示图标和默认文字（若 override_text 有值则覆盖）
            self.start_btn.setIcon(qta.icon('fa6s.play', color='white'))
            text = " 开始" if override_text is None else override_text
            self.start_btn.setText(text)
        else:
            # 禁用：清除图标，文字为纯文本（若 override_text 有值则覆盖）
            self.start_btn.setIcon(QIcon())
            text = "开始" if override_text is None else override_text
            self.start_btn.setText(text)
        self.start_btn.setEnabled(enabled)

    def _set_stop_button_state(self, enabled, override_text=None):
        """
        设置停止按钮的状态。
        enabled: True 启用（显示图标+文字），False 禁用（清除图标，只显示文字）。
        override_text: 可选，强制指定文字内容。
        """
        if enabled:
            self.stop_btn.setIcon(qta.icon('fa6s.stop', color='white'))
            text = " 停止" if override_text is None else override_text
            self.stop_btn.setText(text)
        else:
            self.stop_btn.setIcon(QIcon())
            text = "停止" if override_text is None else override_text
            self.stop_btn.setText(text)
        self.stop_btn.setEnabled(enabled)

    # 保留旧方法以兼容（若其他地方调用）
    def _set_start_button_icon(self, enabled, override_text=None):
        self._set_start_button_state(enabled, override_text)

    def _set_stop_button_icon(self, enabled, override_text=None):
        self._set_stop_button_state(enabled, override_text)

    def _run_adb_cmd(self, args, timeout=10):
        """执行 adb 子命令，返回 stdout。args 不含 adb 路径与 -s 设备号。"""
        result = self.adb.run(args, timeout=timeout)
        if not result.ok:
            print(f"[adb] {' '.join(result.command)}: {result.error}")
            return ""
        return result.stdout

    # ---------------- 白名单文件（兼容受限 ROM） ----------------
    def _push_whitelist_file(self, pkgs) -> bool:
        """
        把包名写成本地文件推到设备，返回是否成功。

        为什么需要这条路径：部分车机 ROM 上 monkey 的 -p 参数会被忽略 ——
        点「开始」后设备毫无反应也不报错，换 --pkg-whitelist-file 通常就正常了。
        """
        local = os.path.join(tempfile.gettempdir(), "bugcatcher_monkey_whitelist.txt")
        remote = "/data/local/tmp/bugcatcher_monkey_whitelist.txt"
        try:
            with open(local, "w", encoding="utf-8", newline="\n") as f:
                f.write("\n".join(pkgs) + "\n")

            res = self.adb.run(["push", local, remote], timeout=20)
            out = (res.stdout or "") + (res.stderr or "")
            if not res.ok or "error" in out.lower():
                QMessageBox.warning(self, "白名单文件推送失败",
                                    f"无法把白名单文件推到设备：\n{out.strip()[:200]}\n\n可改用「-p 参数」方式启动。")
                return False

            self._whitelist_remote = remote
            return True
        except Exception as e:
            QMessageBox.warning(self, "白名单文件推送失败", f"{e}\n\n可改用「-p 参数」方式启动。")
            return False

    def _remove_whitelist_file(self):
        """删除设备侧白名单文件（尽力而为，失败忽略）"""
        remote = getattr(self, "_whitelist_remote", None)
        self._whitelist_remote = None
        if not remote:
            return
        try:
            self._run_adb_cmd(["shell", "rm", "-f", remote], timeout=5)
        except Exception:
            pass

    def choose_packages(self):
        """弹出应用选择对话框，让用户选择多个应用，支持搜索"""
        result3 = self._run_adb_cmd(["shell", "pm", "list", "packages", "-3"])
        third_party = [line.split(':')[1].strip() for line in result3.splitlines() if line.startswith("package:")]

        results = self._run_adb_cmd(["shell", "pm", "list", "packages", "-s"])
        system = [line.split(':')[1].strip() for line in results.splitlines() if line.startswith("package:")]

        apps = system + third_party
        if not apps:
            Toast.notify(self, "无法获取应用列表，请检查设备连接")
            return

        dialog = QDialog(self)
        dialog.setWindowTitle("选择应用")
        dialog.resize(500, 450)
        dialog.setModal(True)

        layout = QVBoxLayout(dialog)

        search_layout = QHBoxLayout()
        search_edit = QLineEdit()
        search_edit.setPlaceholderText("🔍 搜索包名...")
        search_layout.addWidget(search_edit)

        clear_search_btn = QPushButton("清空")
        clear_search_btn.setFixedWidth(50)
        clear_search_btn.setFixedHeight(30)
        clear_search_btn.setObjectName("linkBtn")

        def clear_search():
            search_edit.clear()

        clear_search_btn.clicked.connect(clear_search)
        search_layout.addWidget(clear_search_btn)

        layout.addLayout(search_layout)

        list_widget = QListWidget()
        list_widget.setSelectionMode(QListWidget.MultiSelection)
        # 选中态由全局 QSS 的 QListWidget::item:selected 统一控制

        for app in sorted(apps):
            item = QListWidgetItem(app)
            item.setData(Qt.UserRole, app)
            list_widget.addItem(item)

        layout.addWidget(list_widget)

        def apply_filter(keyword: str):
            keyword_lower = keyword.lower().strip()
            for i in range(list_widget.count()):
                item = list_widget.item(i)
                pkg = item.data(Qt.UserRole)
                if pkg is None:
                    continue
                if keyword_lower:
                    item.setHidden(keyword_lower not in pkg.lower())
                else:
                    item.setHidden(False)

        search_edit.textChanged.connect(apply_filter)

        btn_layout = QHBoxLayout()
        btn_layout.addStretch()

        ok_btn = QPushButton("确定")
        ok_btn.setFixedHeight(32)
        ok_btn.setObjectName("primaryBtn")

        btn_layout.addWidget(ok_btn)
        btn_layout.addStretch()
        layout.addLayout(btn_layout)

        def on_ok():
            selected = [item.text() for item in list_widget.selectedItems()]
            if selected:
                self.pkg.setText(','.join(selected))
            dialog.accept()

        ok_btn.clicked.connect(on_ok)

        dialog.exec()

    def check_running_monkey(self):
        """检测设备上是否有 Monkey 进程在运行"""
        try:
            cmd = ["shell", "pgrep", "monkey"]
            result = self._run_adb_cmd(cmd, timeout=5)
            if result.strip():
                pid = result.strip().split('\n')[0]
                self.monkey_pid = pid
                self.monkey_running = True
                self._set_start_button_state(False)  # 禁用开始
                self._set_stop_button_state(True)    # 启用停止
                self.status.setText(f"Monkey 正在运行中 (PID: {pid})")
                self.start_monitor_thread()
                return
        except Exception:
            pass
        self.monkey_running = False
        self.monkey_pid = None
        self._set_start_button_state(True)   # 启用开始
        self._set_stop_button_state(False)   # 禁用停止
        self.status.setText("就绪")

    def start_monitor_thread(self):
        """启动线程监控 Monkey 进程是否已结束"""
        if self.monitor_thread and self.monitor_thread.is_alive():
            return  # 已有监控线程

        def monitor():
            while self.monkey_running:
                # 如果 PID 未知，尝试获取
                if not self.monkey_pid:
                    try:
                        cmd = ["shell", "pgrep", "monkey"]
                        result = self._run_adb_cmd(cmd, timeout=3)
                        if result.strip():
                            self.monkey_pid = result.strip().split('\n')[0]
                    except:
                        pass
                    time.sleep(0.5)
                    continue

                try:
                    cmd = ["shell", "ps", "-p", str(self.monkey_pid)]
                    result = self._run_adb_cmd(cmd, timeout=5)
                    if not re.search(rf'\b{self.monkey_pid}\b', result):
                        # 进程已退出
                        self.monkey_running = False
                        self.monkey_pid = None
                        self._set_start_button_state(True)
                        self._set_stop_button_state(False)
                        self.status.setText("Monkey 已开启")
                        break
                except Exception:
                    pass
                threading.Event().wait(2)

        self.monitor_thread = threading.Thread(target=monitor, daemon=True)
        self.monitor_thread.start()

    def start_monkey(self):
        if self.monkey_running:
            Toast.notify(self, "Monkey 已在运行，请先停止后再启动。")
            return

        # 验证输入
        event_count = self.event_count.value()
        seed = self.seed.value()
        pkg_str = self.pkg.text().strip()

        pkgs = []
        if pkg_str:
            pkgs = [p.strip() for p in pkg_str.split(',') if p.strip()]

        for p in pkgs:
            if not re.match(r'^[a-zA-Z0-9_.]+$', p):
                Toast.notify(self, f"包名 {p} 格式不正确")
                return

        if pkgs:
            for p in pkgs:
                check_cmd = ["shell", "pm", "list", "packages", p]
                result = self._run_adb_cmd(check_cmd, timeout=5)
                if not result.strip():
                    Toast.notify(self, f"包名 {p} 不存在于设备上，请检查包名是否正确。")
                    return

        # ---- 白名单文件模式：先把包名推到设备（受限 ROM 上 -p 常常静默失效） ----
        mode = self.start_mode.currentData() if hasattr(self, "start_mode") else "param"
        self._whitelist_remote = None
        if mode == "whitelist" and pkgs:
            if not self._push_whitelist_file(pkgs):
                return

        # 构建命令（Monkey 是长任务，下面用 Popen 启动，这里只取完整 argv）
        cmd = self.adb.command(["shell", "monkey"])
        if mode == "whitelist" and self._whitelist_remote:
            cmd.extend(["--pkg-whitelist-file", self._whitelist_remote])
        else:
            for p in pkgs:
                cmd.extend(["-p", p])
        cmd.extend(["-s", str(seed)])

        if self.advanced_group.isChecked():
            throttle = self.throttle.value()
            if throttle > 0:
                cmd.extend(["--throttle", str(throttle)])

            pcts = [
                ("--pct-touch", self.pct_touch.value()),
                ("--pct-motion", self.pct_motion.value()),
                ("--pct-trackball", self.pct_trackball.value()),
                ("--pct-nav", self.pct_nav.value()),
                ("--pct-majornav", self.pct_majornav.value()),
                ("--pct-appswitch", self.pct_appswitch.value()),
                ("--pct-flip", self.pct_flip.value()),
                ("--pct-anyevent", self.pct_anyevent.value()),
                ("--pct-syskeys", self.pct_syskeys.value()),
            ]
            for flag, val in pcts:
                if val > 0:
                    cmd.extend([flag, str(val)])

            if self.ignore_crashes.isChecked():
                cmd.append("--ignore-crashes")
            if self.ignore_timeouts.isChecked():
                cmd.append("--ignore-timeouts")
            if self.ignore_security.isChecked():
                cmd.append("--ignore-security-exceptions")
            if self.ignore_native.isChecked():
                cmd.append("--ignore-native-crashes")
            if self.monitor_native.isChecked():
                cmd.append("--monitor-native-crashes")

            verbosity = self.verbosity.currentIndex()
            for _ in range(verbosity):
                cmd.append("-v")
        else:
            cmd.append("-v")

        cmd.append(str(event_count))

        # ---- 立即设置状态为运行中，以便停止按钮立即感知 ----
        self.monkey_running = True
        self._set_start_button_state(False, "启动中...")  # 禁用开始，文字自定义，图标清除
        self._set_stop_button_state(True)                # 启用停止
        self.status.setText("正在启动 Monkey...")

        def run():
            try:
                if sys.platform == 'win32':
                    self.process = subprocess.Popen(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                                                    creationflags=subprocess.CREATE_NO_WINDOW)
                else:
                    self.process = subprocess.Popen(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

                # 等待进程启动并获取 PID
                time.sleep(1)
                # 尝试获取设备上的 Monkey PID
                pid_cmd = ["shell", "pgrep", "monkey"]
                for _ in range(5):
                    result = self._run_adb_cmd(pid_cmd, timeout=3)
                    if result.strip():
                        self.monkey_pid = result.strip().split('\n')[0]
                        break
                    time.sleep(0.5)

                self.status.setText("Monkey 正在运行中...")
                self.start_monitor_thread()

                self.process.wait()
            except Exception as e:
                self.status.setText(f"启动失败: {e}")
                self.monkey_running = False
                self.monkey_pid = None
                self._set_start_button_state(True)
                self._set_stop_button_state(False)
            finally:
                self.process = None
                self._remove_whitelist_file()

        threading.Thread(target=run, daemon=True).start()

    def stop_monkey(self):
        """停止当前运行的 Monkey 进程"""
        if not self.monkey_running:
            Toast.notify(self, "当前没有正在运行的 Monkey")
            return

        # 优先使用已知的 PID
        if self.monkey_pid:
            try:
                kill_cmd = ["shell", "kill", "-9", str(self.monkey_pid)]
                self._run_adb_cmd(kill_cmd, timeout=5)
                self.status.setText("已发送停止信号")
                self.monkey_running = False
                self.monkey_pid = None
                self._set_start_button_state(True)
                self._set_stop_button_state(False)
            except Exception as e:
                self.status.setText(f"停止失败: {e}")
        else:
            try:
                kill_cmd = ["shell", "pkill", "monkey"]
                self._run_adb_cmd(kill_cmd, timeout=5)
                self.status.setText("已发送停止信号")
                self.monkey_running = False
                self.monkey_pid = None
                self._set_start_button_state(True)
                self._set_stop_button_state(False)
            except Exception as e:
                self.status.setText(f"停止失败: {e}")

        # 停止后顺手清掉设备侧白名单文件
        self._remove_whitelist_file()

    def closeEvent(self, event):
        """关闭窗口时停掉 Monkey，避免设备侧进程与本地 adb 子进程残留"""
        if getattr(self, 'monkey_running', False):
            reply = QMessageBox.question(
                self, "确认",
                "Monkey 正在运行，关闭窗口将停止 Monkey。是否继续？",
                QMessageBox.Yes | QMessageBox.No
            )
            if reply != QMessageBox.Yes:
                event.ignore()
                return
            try:
                self.stop_monkey()
            except Exception:
                pass

        self.monkey_running = False

        # 终止本地 adb 客户端进程（设备侧已 kill，客户端可能仍挂着）
        proc = getattr(self, 'process', None)
        if proc is not None and proc.poll() is None:
            try:
                proc.terminate()
                proc.wait(timeout=2)
            except Exception:
                try:
                    proc.kill()
                except Exception:
                    pass
        self.process = None

        event.accept()