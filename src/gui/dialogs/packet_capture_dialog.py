# src/gui/dialogs/packet_capture_dialog.py
import subprocess
import os
import sys
from datetime import datetime
from PySide6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QPushButton,
    QLabel, QLineEdit, QProgressBar, QMessageBox, QComboBox,
    QTextEdit, QDialogButtonBox
)
from PySide6.QtCore import Signal, QTimer, QThread
from PySide6.QtGui import QIcon
import qtawesome as qta
from src.core.adb_client import AdbClient
from src.utils.helpers import resource_path
from src.gui.toast import Toast


class TcpdumpDeployThread(QThread):
    finished = Signal(bool, str)

    def __init__(self, device_serial):
        super().__init__()
        self.device_serial = device_serial

    def run(self):
        adb = AdbClient(serial=self.device_serial)
        # 1. 检查 root 权限
        result = adb.shell("id", timeout=5)
        if not result.ok:
            self.finished.emit(False, f"检测 root 失败: {result.error}")
            return
        if "uid=0" not in result.stdout:
            self.finished.emit(False, "设备未 root，无法使用 tcpdump 抓包。请 root 设备后重试。")
            return

        check = adb.shell("ls /data/local/tmp/tcpdump", timeout=5)
        if check.ok:
            self.finished.emit(True, "tcpdump 已就绪")
            return

        tcpdump_local = resource_path(os.path.join("tools", "tcpdump"))
        if not os.path.exists(tcpdump_local):
            self.finished.emit(False, "未找到 tcpdump 工具，请将 tcpdump 放入 tools 目录")
            return

        push = adb.run(["push", tcpdump_local, "/data/local/tmp/tcpdump"], timeout=30)
        if not push.ok:
            self.finished.emit(False, f"部署失败: {push.error}")
            return
        chmod = adb.shell("chmod +x /data/local/tmp/tcpdump", timeout=5)
        if not chmod.ok:
            self.finished.emit(False, f"部署失败: 无法赋予执行权限（{chmod.error}）")
            return
        self.finished.emit(True, "tcpdump 部署成功")


class CaptureWorker(QThread):
    started = Signal(str)
    finished = Signal(bool, str)
    error = Signal(str)
    size_updated = Signal(int)

    def __init__(self, device_serial, output_dir, filter_expr=""):
        super().__init__()
        self.device_serial = device_serial
        self.output_dir = output_dir
        self.filter_expr = filter_expr
        self._is_running = True
        self.process = None
        self.capture_file = None
        self.file_handle = None

    def run(self):
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        safe_serial = self.device_serial.replace(':', '_')
        device_dir = os.path.join(self.output_dir, safe_serial)
        try:
            os.makedirs(device_dir, exist_ok=True)
        except Exception as e:
            self.error.emit(f"无法创建目录: {e}")
            self.finished.emit(False, "创建目录失败")
            return

        filename = f"capture_{timestamp}.pcap"
        self.capture_file = os.path.join(device_dir, filename)
        try:
            self.file_handle = open(self.capture_file, 'wb')
        except Exception as e:
            self.error.emit(f"无法创建文件: {e}")
            self.finished.emit(False, "创建文件失败")
            return

        adb = AdbClient(serial=self.device_serial)
        args = ["shell", "/data/local/tmp/tcpdump", "-i", "any", "-s", "0", "-w", "-", "-U"]
        if self.filter_expr.strip():
            args.extend(["-f", self.filter_expr])
        cmd = adb.command(args)

        try:
            if sys.platform == 'win32':
                self.process = subprocess.Popen(
                    cmd, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                    creationflags=subprocess.CREATE_NO_WINDOW
                )
            else:
                self.process = subprocess.Popen(
                    cmd, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                    preexec_fn=os.setsid
                )
        except Exception as e:
            self.error.emit(f"启动 tcpdump 失败: {e}")
            self.file_handle.close()
            self.finished.emit(False, "启动失败")
            return

        self.started.emit(self.capture_file)

        try:
            while self._is_running:
                data = self.process.stdout.read(8192)
                if not data:
                    break
                self.file_handle.write(data)
                self.file_handle.flush()
                size = self.file_handle.tell()
                if size % (1024 * 1024) < 8192:
                    self.size_updated.emit(size)
        except Exception as e:
            self.error.emit(f"读取数据异常: {e}")
        finally:
            self._cleanup()

    def _cleanup(self):
        if self.process:
            try:
                self.process.terminate()
                self.process.wait(timeout=3)
            except:
                self.process.kill()
            self.process = None
        if self.file_handle:
            self.file_handle.close()
            self.file_handle = None
        if self.capture_file and os.path.exists(self.capture_file):
            self.size_updated.emit(os.path.getsize(self.capture_file))
        self.finished.emit(True, self.capture_file)

    def stop(self):
        self._is_running = False
        if self.process and self.process.poll() is None:
            try:
                self.process.terminate()
            except:
                pass


class PacketCaptureDialog(QDialog):
    def __init__(self, adb_manager, device_serial, output_dir, parent=None):
        super().__init__(parent)
        self.adb_manager = adb_manager
        self.device_serial = device_serial
        self.output_dir = output_dir
        self.capture_worker = None
        self.setWindowTitle("网络抓包")
        self.resize(550, 240)
        self.setup_ui()
        self._update_button_states(deploying=True)
        self.deploy_tcpdump()

    def setup_ui(self):
        layout = QVBoxLayout(self)

        # ---- 提示标签（无线联调警告样式） ----
        info = QLabel(
            "注意事项：抓包需要设备已 root，将实时保存为 pcap 文件，可用 Wireshark 分析。"
        )
        info.setWordWrap(True)
        info.setStyleSheet(
            "background-color: #FBEFEC; color: #8C3A2B; padding: 8px; border-radius: 4px; font-weight: bold;"
        )
        layout.addWidget(info)

        # ---- 场景模板行：下拉框左对齐，帮助按钮右对齐 ----
        scene_layout = QHBoxLayout()
        scene_layout.addWidget(QLabel(" 场景模板  :"))
        self.scene_combo = QComboBox()
        self.scene_combo.addItem("自定义", "")
        self.scene_combo.addItem("HTTP/HTTPS 流量", "tcp port 80 or tcp port 443")
        self.scene_combo.addItem("DNS 查询", "udp port 53")
        self.scene_combo.addItem("ICMP (ping)", "icmp")
        self.scene_combo.addItem("WiFi 全流量（排除ARP）", "ip and not arp")
        self.scene_combo.addItem("指定主机 (需修改IP)", "host 192.168.1.100")
        self.scene_combo.addItem("排除某IP", "not host 8.8.8.8")
        self.scene_combo.setToolTip("选择常见抓包场景，自动填充过滤表达式，您也可以手动修改")
        self.scene_combo.currentIndexChanged.connect(self.on_scene_selected)
        scene_layout.addWidget(self.scene_combo)

        # 添加弹性空间，将帮助按钮推到右侧
        scene_layout.addStretch()

        # ---- 过滤语法帮助按钮：蓝色，始终启用 ----
        self.help_btn = QPushButton("过滤语法")
        self.help_btn.setIcon(qta.icon('fa6s.book', color='#1F1D1A'))
        self.help_btn.setText(" 过滤语法")
        self.help_btn.setFixedWidth(100)
        self.help_btn.setFixedHeight(32)
        self.help_btn.clicked.connect(self.show_filter_help)
        scene_layout.addWidget(self.help_btn)

        layout.addLayout(scene_layout)

        # ---- 过滤表达式 ----
        filter_layout = QHBoxLayout()
        filter_layout.addWidget(QLabel("过滤表达式:"))
        self.filter_edit = QLineEdit()
        self.filter_edit.setPlaceholderText("例如: host 8.8.8.8 or port 80")
        filter_layout.addWidget(self.filter_edit)
        layout.addLayout(filter_layout)

        # ---- 状态和大小 ----
        self.status_label = QLabel("正在准备 tcpdump...")
        layout.addWidget(self.status_label)

        self.size_label = QLabel("已捕获: 0 KB")
        layout.addWidget(self.size_label)

        # ---- 进度条（隐藏） ----
        self.progress = QProgressBar()
        self.progress.setVisible(False)
        layout.addWidget(self.progress)

        # ---- 按钮 ----
        btn_layout = QHBoxLayout()
        btn_layout.addStretch()

        # ---- 开始按钮：绿色 ----
        self.start_btn = QPushButton("开始")
        self.start_btn.setFixedHeight(32)
        self.start_btn.setObjectName("primaryBtn")
        self.start_btn.clicked.connect(self.start_capture)

        # ---- 停止按钮：红色 ----
        self.stop_btn = QPushButton("停止")
        self.stop_btn.setFixedHeight(32)
        self.stop_btn.setObjectName("stopBtn")
        self.stop_btn.clicked.connect(self.stop_capture)
        self.stop_btn.setEnabled(False)

        # ---- 打开文件位置按钮：蓝色 ----
        self.open_btn = QPushButton("打开文件位置")
        self.open_btn.setFixedHeight(32)
        self.open_btn.clicked.connect(self.open_file_location)
        self.open_btn.setEnabled(False)

        btn_layout.addWidget(self.start_btn)
        btn_layout.addSpacing(10)
        btn_layout.addWidget(self.stop_btn)
        btn_layout.addSpacing(10)
        btn_layout.addWidget(self.open_btn)
        btn_layout.addStretch()
        layout.addLayout(btn_layout)

        # ---- 定时器更新文件大小 ----
        self.size_timer = QTimer()
        self.size_timer.timeout.connect(self.update_file_size)

        # 初始化按钮状态（部署中）
        self._update_button_states(deploying=True)

    def _update_button_states(self, deploying=False, capturing=False, stopped=False):
        """
        更新三个主要按钮的状态、文本和图标。
        :param deploying: 正在部署 tcpdump（开始和停止禁用，打开禁用）
        :param capturing: 抓包进行中（开始禁用，停止启用，打开禁用）
        :param stopped: 抓包已停止（开始启用，停止禁用，打开启用）
        """
        if deploying:
            # 部署中：开始禁用（纯文本），停止禁用（纯文本），打开禁用（纯文本）
            self.start_btn.setEnabled(False)
            self.start_btn.setText("开始")
            self.start_btn.setIcon(QIcon())
            self.stop_btn.setEnabled(False)
            self.stop_btn.setText("停止")
            self.stop_btn.setIcon(QIcon())
            self.open_btn.setEnabled(False)
            self.open_btn.setText("打开文件位置")
            self.open_btn.setIcon(QIcon())
        elif capturing:
            # 抓包中：开始禁用（纯文本），停止启用（带图标），打开禁用（纯文本）
            self.start_btn.setEnabled(False)
            self.start_btn.setText("开始")
            self.start_btn.setIcon(QIcon())
            self.stop_btn.setEnabled(True)
            self.stop_btn.setText(" 停止")
            self.stop_btn.setIcon(qta.icon('fa6s.stop', color='white'))
            self.open_btn.setEnabled(False)
            self.open_btn.setText("打开文件位置")
            self.open_btn.setIcon(QIcon())
        elif stopped:
            # 已停止：开始启用（带图标），停止禁用（纯文本），打开启用（带图标）
            self.start_btn.setEnabled(True)
            self.start_btn.setText(" 开始")
            self.start_btn.setIcon(qta.icon('fa6s.play', color='white'))
            self.stop_btn.setEnabled(False)
            self.stop_btn.setText("停止")
            self.stop_btn.setIcon(QIcon())
            self.open_btn.setEnabled(True)
            self.open_btn.setText(" 打开文件位置")
            self.open_btn.setIcon(qta.icon('fa6s.folder-open', color='#1F1D1A'))
        else:
            # 初始就绪（部署完成）：开始启用（带图标），停止禁用（纯文本），打开禁用（纯文本）
            self.start_btn.setEnabled(True)
            self.start_btn.setText(" 开始")
            self.start_btn.setIcon(qta.icon('fa6s.play', color='white'))
            self.stop_btn.setEnabled(False)
            self.stop_btn.setText("停止")
            self.stop_btn.setIcon(QIcon())
            self.open_btn.setEnabled(False)
            self.open_btn.setText("打开文件位置")
            self.open_btn.setIcon(QIcon())

    # ---------- 原有方法（修改部分） ----------
    def on_scene_selected(self, index):
        expr = self.scene_combo.currentData()
        if expr is not None:
            self.filter_edit.setText(expr)
            self.status_label.setText(f"已加载场景: {self.scene_combo.currentText()}")
        else:
            self.filter_edit.clear()
            self.status_label.setText("就绪")

    def show_filter_help(self):
        """显示过滤语法帮助对话框（无底部关闭按钮）"""
        help_text = """
        <h3>📖 tcpdump 过滤语法速查</h3>
        <p><b>基本结构：</b> [协议] [方向] [类型] [值]</p>
        <table border="1" cellpadding="5" style="border-collapse: collapse;">
        <tr><th>示例</th><th>说明</th></tr>
        <tr><td><code>host 192.168.1.100</code></td><td>指定主机 IP</td></tr>
        <tr><td><code>port 80</code></td><td>指定端口</td></tr>
        <tr><td><code>tcp</code></td><td>仅 TCP 包</td></tr>
        <tr><td><code>udp</code></td><td>仅 UDP 包</td></tr>
        <tr><td><code>icmp</code></td><td>仅 ICMP (ping)</td></tr>
        <tr><td><code>src host 10.0.0.1</code></td><td>源地址</td></tr>
        <tr><td><code>dst port 53</code></td><td>目的端口</td></tr>
        <tr><td><code>not arp</code></td><td>排除 ARP 包</td></tr>
        <tr><td><code>host 1.2.3.4 and tcp</code></td><td>组合条件（与）</td></tr>
        <tr><td><code>host 1.2.3.4 or host 5.6.7.8</code></td><td>组合条件（或）</td></tr>
        <tr><td><code>(host 1.2.3.4) and not (port 22)</code></td><td>复杂组合</td></tr>
        </table>
        <br>
        <b>💡 提示：</b>留空表示抓取所有流量，可能会产生较大文件。
        """
        dialog = QDialog(self)
        dialog.setWindowTitle("过滤语法帮助")
        dialog.resize(400, 460)
        layout = QVBoxLayout(dialog)
        text_edit = QTextEdit()
        text_edit.setReadOnly(True)
        text_edit.setHtml(help_text)
        layout.addWidget(text_edit)
        dialog.exec()

    def deploy_tcpdump(self):
        self.status_label.setText("正在检查/部署 tcpdump...")
        self.deploy_thread = TcpdumpDeployThread(self.device_serial)
        self.deploy_thread.finished.connect(self.on_deploy_finished)
        self.deploy_thread.start()

    def on_deploy_finished(self, success, message):
        if success:
            self.status_label.setText("就绪")
            self._update_button_states(deploying=False)  # 就绪状态
        else:
            self.status_label.setText(f" 部署失败  : {message}")
            self._update_button_states(deploying=True)  # 保持禁用
            Toast.notify(self, message)

    def start_capture(self):
        if self.capture_worker and self.capture_worker.isRunning():
            return

        filter_expr = self.filter_edit.text().strip()
        self.capture_worker = CaptureWorker(self.device_serial, self.output_dir, filter_expr)
        self.capture_worker.started.connect(self.on_capture_started)
        self.capture_worker.error.connect(self.on_capture_error)
        self.capture_worker.finished.connect(self.on_capture_finished)
        self.capture_worker.size_updated.connect(self.on_size_updated)

        self._update_button_states(capturing=True)  # 抓包中
        self.status_label.setText("正在启动抓包...")
        self.size_label.setText("已捕获: 0 KB")
        self.capture_worker.start()

    def on_capture_started(self, file_path):
        self._update_button_states(capturing=True)
        self.status_label.setText("抓包中...")
        self.size_timer.start(1000)

    def on_capture_error(self, error_msg):
        QMessageBox.critical(self, "抓包错误", error_msg)
        self._update_button_states(stopped=False)  # 回到就绪但无文件可打开
        self.status_label.setText("就绪")

    def on_capture_finished(self, success, result):
        self.size_timer.stop()
        if success:
            self._update_button_states(stopped=True)  # 已停止，可打开文件
            self.status_label.setText("抓包已停止")
            Toast.notify(self, f"抓包文件已保存至:\n{result}")
        else:
            self._update_button_states(stopped=False)  # 失败，不启用打开
            self.status_label.setText("抓包失败")
            QMessageBox.critical(self, "失败", f"抓包失败: {result}")

    def on_size_updated(self, size_bytes):
        kb = size_bytes / 1024
        mb = kb / 1024
        if mb >= 1:
            self.size_label.setText(f"已捕获: {mb:.2f} MB")
        else:
            self.size_label.setText(f"已捕获: {kb:.2f} KB")

    def update_file_size(self):
        if self.capture_worker and self.capture_worker.capture_file:
            try:
                size = os.path.getsize(self.capture_worker.capture_file)
                self.on_size_updated(size)
            except:
                pass

    def stop_capture(self):
        if self.capture_worker and self.capture_worker.isRunning():
            self.capture_worker.stop()
            self.status_label.setText("正在停止抓包...")
            self.stop_btn.setEnabled(False)
            self.stop_btn.setText("停止")
            self.stop_btn.setIcon(QIcon())

    def open_file_location(self):
        if self.capture_worker and self.capture_worker.capture_file:
            file_path = self.capture_worker.capture_file
            if os.path.exists(file_path):
                if sys.platform == 'win32':
                    os.startfile(os.path.dirname(file_path))
                elif sys.platform == 'darwin':
                    subprocess.run(["open", os.path.dirname(file_path)])
                else:
                    subprocess.run(["xdg-open", os.path.dirname(file_path)])

    def closeEvent(self, event):
        if self.capture_worker and self.capture_worker.isRunning():
            reply = QMessageBox.question(self, "确认", "抓包进行中，确定关闭窗口将停止抓包。是否继续？")
            if reply == QMessageBox.Yes:
                self.capture_worker.stop()
                self.capture_worker.wait(3000)
                event.accept()
            else:
                event.ignore()
        else:
            event.accept()