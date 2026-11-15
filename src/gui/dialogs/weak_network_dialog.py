# src/gui/dialogs/weak_network_dialog.py
import subprocess
import sys
import threading
import re
from PySide6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QFormLayout,
    QSpinBox, QDoubleSpinBox, QPushButton, QLabel,
    QMessageBox, QComboBox, QDialogButtonBox, QTextEdit, QSizePolicy
)
from PySide6.QtCore import Signal, Slot
from PySide6.QtGui import QIcon
import qtawesome as qta
from src.gui.toast import Toast


class WeakNetworkDialog(QDialog):
    show_info_signal = Signal(str, str)
    show_warning_signal = Signal(str, str)
    show_critical_signal = Signal(str, str)
    residual_found = Signal(list)      # 启动检测到残留规则时发射（网卡名列表）
    # 能力探测结果：(是否可提权, su 路径, tc 路径, 内核是否支持 netem)
    capability_ready = Signal(bool, str, str, bool)

    # 启动时检查残留规则涉及的网卡（与下拉框默认项保持一致）
    RESIDUAL_CHECK_IFACES = ("wlan0", "eth0", "rmnet0")

    def __init__(self, adb_manager, device_serial, parent=None):
        super().__init__(parent)
        self.adb_manager = adb_manager
        self.device_serial = device_serial
        self.tc_path = None
        self._processing = False
        # ---- 设备能力（打开时后台探测，参考虫师的做法） ----
        # 为什么分这么细：只判「能不能加 netem」会在没 root 的设备上也报「内核没有 netem」，
        # 原因说错，用户会跑去折腾内核。这里把 root / tc / netem 三者分开，各给一句人话。
        self.has_privilege = False      # 已经能执行特权命令（root，或 su -c 可用）
        self.su_path = None             # 找到的 su 路径（没授权时也有值）
        self.netem_ok = None            # None=还没探测过
        self._probing = False
        # 是否已有弱网规则生效，以及生效在哪块网卡上
        # 关闭对话框/退出程序时需要据此自动恢复网络，避免设备残留规则导致断网
        self._applied = False
        self._applied_iface = None
        self._closing = False       # 是否正在关闭（用于丢弃后台检测的迟到回调）
        self.setWindowTitle("弱网模拟")
        self.resize(470, 440)   # 增加能力横幅后需要更高的空间
        self.setup_ui()
        self.connect_signals()
        # 打开即后台检查上次是否留下未清理的规则（不阻塞界面）
        self._start_residual_check()
        # 同时探测设备能力：root / tc / netem（不阻塞界面）
        self._probe_capability()

    def connect_signals(self):
        self.show_info_signal.connect(self._show_info)
        self.show_warning_signal.connect(self._show_warning)
        self.show_critical_signal.connect(self._show_critical)
        self.residual_found.connect(self._on_residual_found)
        self.capability_ready.connect(self._on_capability_ready)

    @Slot(str, str)
    def _show_info(self, title, text):
        Toast.notify(self, text)

    @Slot(str, str)
    def _show_warning(self, title, text):
        Toast.notify(self, text)

    @Slot(str, str)
    def _show_critical(self, title, text):
        QMessageBox.critical(self, title, text)

    def setup_ui(self):
        layout = QVBoxLayout(self)

        # ---- 设备能力横幅：探测完自动更新，说不清的地方直接给下一步该做什么 ----
        banner_row = QHBoxLayout()
        banner_row.setSpacing(8)

        self.capability_label = QLabel("正在检测设备能力…")
        self.capability_label.setObjectName("capabilityBanner")
        self.capability_label.setProperty("level", "probing")
        self.capability_label.setWordWrap(True)
        self.capability_label.setMinimumHeight(48)
        self.capability_label.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Minimum)
        banner_row.addWidget(self.capability_label, 1)

        self.reprobe_btn = QPushButton(" 重新检测")
        self.reprobe_btn.setIcon(qta.icon('fa6s.rotate', color='#1F1D1A'))
        self.reprobe_btn.setFixedHeight(30)
        self.reprobe_btn.clicked.connect(self._probe_capability)
        banner_row.addWidget(self.reprobe_btn)
        layout.addLayout(banner_row)

        tip = QLabel("仅对指定网卡生效，断开 WiFi 后规则可能自动清除。wlan0 是 WiFi，eth0 是移动数据（TSU）。")
        tip.setWordWrap(True)
        tip.setObjectName("hintLabel")
        layout.addWidget(tip)

        form = QFormLayout()

        # 网卡
        self.iface_combo = QComboBox()
        self.iface_combo.addItems(["wlan0", "eth0", "rmnet0"])
        self.iface_combo.setEditable(True)
        self.iface_combo.setToolTip("选择或输入网络接口名称，通常 wlan0 为 WiFi、eth0 为 TSU")
        form.addRow("  网  卡  :", self.iface_combo)

        # 场景模板
        self.scene_combo = QComboBox()
        self.scene_combo.addItem("自定义", None)
        self.scene_combo.addItem("2G (GPRS/EDGE)", {"delay": 300, "loss": 5, "bandwidth": 50})
        self.scene_combo.addItem("3G (HSPA)", {"delay": 100, "loss": 2, "bandwidth": 500})
        self.scene_combo.addItem("4G (LTE)", {"delay": 50, "loss": 1, "bandwidth": 5000})
        self.scene_combo.addItem("高延迟 (200ms)", {"delay": 200, "loss": 0, "bandwidth": 0})
        self.scene_combo.addItem("高丢包 (20%)", {"delay": 0, "loss": 20, "bandwidth": 0})
        self.scene_combo.addItem("弱信号 (高延迟+高丢包)", {"delay": 300, "loss": 15, "bandwidth": 200})
        self.scene_combo.setToolTip("选择典型网络场景，自动填充下方参数，您也可以手动微调")
        self.scene_combo.currentIndexChanged.connect(self.on_scene_selected)

        scene_layout = QHBoxLayout()
        scene_layout.addWidget(self.scene_combo)

        # ---- 场景说明按钮：蓝色，始终启用 ----
        self.help_btn = QPushButton("场景说明")
        self.help_btn.setIcon(qta.icon('fa6s.book', color='#1F1D1A'))
        self.help_btn.setText(" 场景说明")
        self.help_btn.setFixedWidth(100)
        self.help_btn.setFixedHeight(32)
        self.help_btn.clicked.connect(self.show_scene_help)
        scene_layout.addWidget(self.help_btn)

        form.addRow("场景模板:", scene_layout)

        # 延迟
        self.delay_spin = QSpinBox()
        self.delay_spin.setRange(0, 2000)
        self.delay_spin.setSuffix(" ms")
        self.delay_spin.setValue(2000)
        self.delay_spin.setToolTip("网络响应延迟，单位毫秒。")
        form.addRow("  延  迟  :", self.delay_spin)

        # 丢包率
        self.loss_spin = QDoubleSpinBox()
        self.loss_spin.setRange(0, 100)
        self.loss_spin.setSuffix(" %")
        self.loss_spin.setSingleStep(0.5)
        self.loss_spin.setValue(20)
        form.addRow(" 丢包率  :", self.loss_spin)

        # 带宽限制
        self.bandwidth_spin = QSpinBox()
        self.bandwidth_spin.setRange(0, 100000)
        self.bandwidth_spin.setSuffix(" kbit")
        self.bandwidth_spin.setSpecialValueText("不限")
        self.bandwidth_spin.setValue(1)
        form.addRow("带宽限制:", self.bandwidth_spin)

        layout.addLayout(form)

        # 按钮行
        btn_layout = QHBoxLayout()
        btn_layout.addStretch()

        # ---- 应用按钮：绿色 ----
        self.apply_btn = QPushButton(" 应用")
        self.apply_btn.setIcon(qta.icon('fa6s.play', color='white'))
        self.apply_btn.setFixedHeight(32)
        self.apply_btn.setObjectName("primaryBtn")
        self.apply_btn.clicked.connect(self.apply_limit)

        btn_layout.addWidget(self.apply_btn)
        btn_layout.addSpacing(10)

        # ---- 清除按钮：橙色 ----
        self.clear_btn = QPushButton(" 清除")
        self.clear_btn.setIcon(qta.icon('fa6s.eraser', color='#B0761F'))
        self.clear_btn.setFixedHeight(32)
        self.clear_btn.setObjectName("warnBtn")
        self.clear_btn.clicked.connect(self.clear_limit)

        btn_layout.addWidget(self.clear_btn)
        btn_layout.addStretch()
        layout.addLayout(btn_layout)

        self.status_label = QLabel("就绪")
        layout.addWidget(self.status_label)

        # 当用户手动修改参数时，自动切回“自定义”场景
        self.delay_spin.valueChanged.connect(self.reset_scene_to_custom)
        self.loss_spin.valueChanged.connect(self.reset_scene_to_custom)
        self.bandwidth_spin.valueChanged.connect(self.reset_scene_to_custom)

    def reset_scene_to_custom(self):
        """当用户手动调整任一参数时，将场景下拉框重置为“自定义”并提示"""
        if self.scene_combo.currentIndex() != 0:
            self.scene_combo.blockSignals(True)
            self.scene_combo.setCurrentIndex(0)
            self.scene_combo.blockSignals(False)
            self.status_label.setText("已切换为“自定义”模式（参数手动调整）")

    def on_scene_selected(self, index):
        """场景模板选中后自动填充参数，并更新状态"""
        data = self.scene_combo.currentData()
        if data is None:
            return

        self.delay_spin.blockSignals(True)
        self.loss_spin.blockSignals(True)
        self.bandwidth_spin.blockSignals(True)

        self.delay_spin.setValue(data.get("delay", 0))
        self.loss_spin.setValue(data.get("loss", 0))
        self.bandwidth_spin.setValue(data.get("bandwidth", 0))

        self.delay_spin.blockSignals(False)
        self.loss_spin.blockSignals(False)
        self.bandwidth_spin.blockSignals(False)

        scene_text = self.scene_combo.currentText()
        self.status_label.setText(f"已加载场景: {scene_text}")

    def show_scene_help(self):
        """显示详细的场景参考说明（仅右上角关闭按钮）"""
        dialog = QDialog(self)
        dialog.setWindowTitle("场景说明")
        dialog.resize(500, 410)

        layout = QVBoxLayout(dialog)

        text_edit = QTextEdit()
        text_edit.setReadOnly(True)
        text_edit.setHtml("""
        <h3>📊 弱网场景参考表</h3>
        <table border="1" cellpadding="6" style="border-collapse: collapse; width:100%;">
        <tr style="background:#eee;">
            <th>场景</th><th>延迟 (ms)</th><th>丢包率 (%)</th><th>带宽 (kbit)</th><th>典型效果</th>
        </tr>
        <tr><td>2G (GPRS/EDGE)</td><td>300~500</td><td>5~10</td><td>50~100</td><td>仅文本，图片加载缓慢</td></tr>
        <tr><td>3G (HSPA)</td><td>100~200</td><td>2~5</td><td>500~2000</td><td>网页可打开，视频缓冲频繁</td></tr>
        <tr><td>4G (LTE)</td><td>30~70</td><td>0.5~1</td><td>5000~30000</td><td>流畅视频，大部分应用正常</td></tr>
        <tr><td>高延迟</td><td>200~500</td><td>0</td><td>不限</td><td>模拟跨国链路，响应迟钝</td></tr>
        <tr><td>高丢包</td><td>0</td><td>15~30</td><td>不限</td><td>模拟信号差，数据丢失严重</td></tr>
        <tr><td>弱信号</td><td>200~400</td><td>10~20</td><td>100~500</td><td>综合模拟信号不稳定</td></tr>
        </table>
        <br>
        <b>💡 提示：</b>以上数值为典型参考，实际效果可能因设备和网络环境而异。
        """)
        layout.addWidget(text_edit)

        try:
            dialog.exec()
        finally:
            dialog.deleteLater()      # 以 self 为父对象，不销毁会累积

    # ---------- 核心功能方法 ----------
    def _run_adb_shell(self, cmd, timeout=10):
        full_cmd = ["adb", "-s", self.device_serial, "shell"] + cmd.split()
        try:
            if sys.platform == 'win32':
                result = subprocess.run(
                    full_cmd, capture_output=True, text=True, timeout=timeout,
                    creationflags=subprocess.CREATE_NO_WINDOW, encoding='utf-8'
                )
            else:
                result = subprocess.run(full_cmd, capture_output=True, text=True, timeout=timeout, encoding='utf-8')
            return result.stdout.strip(), result.stderr.strip(), result.returncode
        except subprocess.TimeoutExpired:
            return "", "Timeout", -1
        except Exception as e:
            return "", str(e), -1

    def _get_tc_path(self):
        if self.tc_path:
            return self.tc_path
        candidates = ["tc", "/system/bin/tc", "/system/xbin/tc"]
        for cand in candidates:
            stdout, _, rc = self._run_adb_shell(f"command -v {cand} 2>/dev/null")
            if rc == 0 and stdout:
                self.tc_path = stdout.strip()
                return self.tc_path
            stdout, _, rc = self._run_adb_shell(f"{cand} -help 2>/dev/null && echo OK")
            if rc == 0 and "OK" in stdout:
                self.tc_path = cand
                return self.tc_path
        return None

    def _run_tc_cmd(self, cmd_args):
        tc = self._get_tc_path()
        if not tc:
            return "", "tc 命令未找到，请确认设备已 root 且支持 netem", -1
        # 没有直接 root 时借用 su 提权（部分设备只有 su，adb root 拿不到）
        if getattr(self, "_use_su", False) and self.su_path:
            return self._run_adb_shell_str(f"{self.su_path} -c '{tc} {cmd_args}'")
        return self._run_adb_shell(f"{tc} {cmd_args}")

    def _run_adb_shell_str(self, cmd: str, timeout=10):
        """整条命令作为单个参数交给设备侧 sh，保留 su -c 里的引号结构"""
        full_cmd = ["adb", "-s", self.device_serial, "shell", cmd]
        try:
            result = subprocess.run(
                full_cmd, capture_output=True, text=True, timeout=timeout,
                creationflags=subprocess.CREATE_NO_WINDOW if sys.platform == 'win32' else 0,
                encoding='utf-8'
            )
            return result.stdout.strip(), result.stderr.strip(), result.returncode
        except subprocess.TimeoutExpired:
            return "", "Timeout", -1
        except Exception as e:
            return "", str(e), -1

    # ---------------- 设备能力探测（root / tc / netem） ----------------
    def _find_su(self):
        for cand in ("su", "/system/bin/su", "/system/xbin/su", "/sbin/su"):
            out, _, rc = self._run_adb_shell(f"command -v {cand} 2>/dev/null")
            if rc == 0 and out.strip():
                return out.strip()
        return None

    def _detect_privilege(self):
        """返回 (能否提权, su 路径)。只看不试 adb root —— 那会重启 adbd 让设备断连。"""
        out, _, _ = self._run_adb_shell("id")
        if "uid=0" in out:
            return True, None

        su = self._find_su()
        if not su:
            return False, None
        out2, _, _ = self._run_adb_shell_str(f"{su} -c id")
        return ("uid=0" in out2), su

    def _probe_netem(self):
        """
        往网卡上挂一条最小 netem 规则再删掉，判断内核有没有 sch_netem。

        优先用 lo（不影响业务流量）；lo 上已有规则时换当前选中的网卡试。
        """
        candidates = ["lo"]
        iface = self.iface_combo.currentText().strip()
        if iface and iface != "lo":
            candidates.append(iface)

        for dev in candidates:
            out, err, rc = self._run_tc_cmd(f"qdisc add dev {dev} root netem delay 1ms")
            text = f"{out} {err}".lower()
            if rc == 0:
                self._run_tc_cmd(f"qdisc del dev {dev} root")
                return True
            if "file exists" in text or "exclusivity" in text:
                continue          # 网卡已有规则，换一块再试
            if "no such file" in text or "not supported" in text:
                return False      # 明确的「内核不认识 netem」
        return False

    def _probe_capability(self):
        if self._probing:
            return
        self._probing = True
        self._set_banner("probing", "正在检测设备能力…")
        self.reprobe_btn.setEnabled(False)

        def worker():
            try:
                has_priv, su = self._detect_privilege()
                self.su_path = su
                self._use_su = bool(su)
                tc = self._get_tc_path() if has_priv else None
                netem = self._probe_netem() if tc else False
            except Exception:
                has_priv, su, tc, netem = False, None, None, False
            self.capability_ready.emit(bool(has_priv), su or "", tc or "", bool(netem))

        threading.Thread(target=worker, daemon=True).start()

    @Slot(bool, str, str, bool)
    def _on_capability_ready(self, has_priv, su_path, tc_path, netem_ok):
        self._probing = False
        self.reprobe_btn.setEnabled(True)
        self.has_privilege = has_priv
        self.netem_ok = netem_ok
        if tc_path:
            self.tc_path = tc_path

        if not has_priv:
            if su_path:
                level = "error"
                text = "⚠ 设备未授权 root：检测到 su，但当前 shell 没有 root 权限 —— 请在手机上同意授权后点「重新检测」"
            else:
                level = "error"
                text = "⚠ 设备未 root：弱网模拟需要 root（调试版可先 adb root，正式版机型通常没有可行路径）"
        elif not tc_path:
            level, text = "error", "⚠ 设备上没有 tc 命令，无法做弱网模拟"
        elif not netem_ok:
            level, text = "warn", "⚠ 延迟 / 丢包不可用（内核没有 netem 模块）· 仍可限制带宽"
        else:
            level, text = "ok", "✅ 本设备支持 netem：延迟 / 丢包 / 带宽限制均可使用"

        self._set_banner(level, text)

        # 内核不支持 netem 时把这两项置灰并清零：填了也不会生效，不如不让填
        for spin in (self.delay_spin, self.loss_spin):
            spin.setEnabled(bool(netem_ok))
        if not netem_ok:
            self.delay_spin.setValue(0)
            self.loss_spin.setValue(0)

        self.apply_btn.setEnabled(bool(has_priv and tc_path))

    def _set_banner(self, level: str, text: str):
        self.capability_label.setText(text)
        self.capability_label.setProperty("level", level)
        self.capability_label.style().unpolish(self.capability_label)
        self.capability_label.style().polish(self.capability_label)

    def apply_limit(self):
        if self._processing:
            return
        self._processing = True

        iface = self.iface_combo.currentText().strip()
        if not iface:
            self.show_warning_signal.emit("提示", "请输入网卡名称")
            self._processing = False
            return

        if not self.has_privilege:
            self.show_critical_signal.emit(
                "权限不足",
                "设备未 root 或没有可用的 su，无法应用弱网规则。\n"
                "如果这台机器能 root，请先 adb root 或在手机上授权 su，再点「重新检测」。"
            )
            self._processing = False
            return

        self._run_tc_cmd(f"qdisc del dev {iface} root 2>/dev/null")

        # 内核不支持 netem 时忽略延迟/丢包，只下发带宽规则（与界面上的置灰保持一致）
        delay = self.delay_spin.value() if self.netem_ok else 0
        loss = self.loss_spin.value() if self.netem_ok else 0
        bandwidth = self.bandwidth_spin.value()

        commands = []
        if bandwidth > 0:
            commands.append(f"qdisc add dev {iface} root handle 1: htb default 11")
            commands.append(f"class add dev {iface} parent 1: classid 1:1 htb rate {bandwidth}kbit")
            commands.append(f"class add dev {iface} parent 1:1 classid 1:11 htb rate {bandwidth}kbit")
            netem_cmd = f"qdisc add dev {iface} parent 1:11 handle 10: netem"
            if delay > 0:
                netem_cmd += f" delay {delay}ms"
            if loss > 0:
                netem_cmd += f" loss {loss}%"
            commands.append(netem_cmd)
        else:
            netem_cmd = f"qdisc add dev {iface} root netem"
            if delay > 0:
                netem_cmd += f" delay {delay}ms"
            if loss > 0:
                netem_cmd += f" loss {loss}%"
            commands.append(netem_cmd)

        # 只禁用应用按钮，清除按钮保持可用（但被 _processing 阻止）
        self.apply_btn.setEnabled(False)
        self.apply_btn.setText("应用")
        self.apply_btn.setIcon(QIcon())
        self.status_label.setText("正在应用...")

        error_emitted = False

        def worker():
            nonlocal error_emitted
            success = True
            error_detail = ""

            for cmd in commands:
                stdout, stderr, rc = self._run_tc_cmd(cmd)
                if rc != 0:
                    success = False
                    error_detail = stderr or stdout or "未知错误"
                    # 应用中途失败时回滚：否则会留下半套规则，设备侧网络处于异常状态
                    self._run_tc_cmd(f"qdisc del dev {iface} root 2>/dev/null")
                    self.status_label.setText("应用失败")
                    # 恢复应用按钮（带图标）
                    self.apply_btn.setEnabled(True)
                    self.apply_btn.setText(" 应用")
                    self.apply_btn.setIcon(qta.icon('fa6s.play', color='white'))
                    self._processing = False

                    if not error_emitted:
                        error_emitted = True
                        if "No such file or directory" in error_detail:
                            self.show_critical_signal.emit(
                                "应用失败",
                                f"网卡 '{iface}' 不存在或内核不支持 netem。\n"
                                f"请尝试其他网卡名称（如 eth0, rmnet0）或确认内核已编译 netem 支持。"
                            )
                        elif "Operation not permitted" in error_detail or "Permission denied" in error_detail:
                            self.show_critical_signal.emit(
                                "权限不足",
                                "请确保设备已 root 并执行 'adb root' 后再试"
                            )
                        else:
                            self.show_critical_signal.emit(
                                "应用失败",
                                f"应用失败: {error_detail}"
                            )
                    return

            if success:
                self._applied = True
                self._applied_iface = iface
                self.status_label.setText(f"规则已生效（{iface}），关闭窗口时将自动恢复")
                self.show_info_signal.emit(
                    "成功",
                    f"弱网规则已在 {iface} 上生效\n关闭窗口时会自动恢复网络"
                )

            # 恢复应用按钮（带图标）
            self.apply_btn.setEnabled(True)
            self.apply_btn.setText(" 应用")
            self.apply_btn.setIcon(qta.icon('fa6s.play', color='white'))
            self._processing = False

        threading.Thread(target=worker, daemon=True).start()

    def clear_limit(self):
        if self._processing:
            return
        self._processing = True

        iface = self.iface_combo.currentText().strip()
        if not iface:
            self._processing = False
            return

        # 只禁用清除按钮，应用按钮保持可用（但被 _processing 阻止）
        self.clear_btn.setEnabled(False)
        self.clear_btn.setText("清除")
        self.clear_btn.setIcon(QIcon())
        self.status_label.setText("正在清除...")

        def worker():
            stdout, stderr, rc = self._run_tc_cmd(f"qdisc del dev {iface} root 2>/dev/null")
            # 恢复清除按钮（带图标）
            self.clear_btn.setEnabled(True)
            self.clear_btn.setText(" 清除")
            self.clear_btn.setIcon(qta.icon('fa6s.eraser', color='#B0761F'))
            self._processing = False

            if rc == 0:
                self._applied = False
                self._applied_iface = None
                self.status_label.setText("规则已清除")
                self.show_info_signal.emit("成功", "网络限制已清除")
                return

            # 处理各种错误情况
            err_msg = stderr or stdout

            # 情况1：没有规则需要清除（最常见）
            if err_msg and "No such file or directory" in err_msg:
                self._applied = False
                self._applied_iface = None
                self.status_label.setText("无规则需清除")
                self.show_info_signal.emit("提示", "当前网卡无任何限制规则")
                return

            # 情况2：权限不足
            if err_msg and ("Permission denied" in err_msg or "Operation not permitted" in err_msg):
                self.status_label.setText("清除失败")
                self.show_critical_signal.emit(
                    "清除失败",
                    "权限不足，请确保设备已 root 并执行 'adb root' 后再试"
                )
                return

            # 情况3：tc 命令不存在或网卡名称错误
            if err_msg and ("not found" in err_msg.lower() or "no such" in err_msg.lower()):
                self.status_label.setText("清除失败")
                self.show_critical_signal.emit(
                    "清除失败",
                    f"请检查网卡名称 '{iface}' 是否正确，或设备是否已 root 且内核支持 netem。\n\n错误信息：{err_msg}"
                )
                return

            # 情况4：其他未预料到的错误
            if err_msg and err_msg.strip():
                self.status_label.setText("清除失败")
                self.show_critical_signal.emit("清除失败", err_msg)
            else:
                # 完全没有错误信息
                self.status_label.setText("清除失败")
                self.show_critical_signal.emit(
                    "清除失败",
                    "清除规则失败，请检查设备是否已 root 或网卡名称是否正确"
                )

        threading.Thread(target=worker, daemon=True).start()

    # ---------- 自动恢复 ----------
    def _notify_log(self, text):
        """把恢复结果同步到主窗口日志（父窗口是 MainWindow 时）"""
        parent = self.parent()
        if parent is not None and hasattr(parent, 'append_log'):
            try:
                parent.append_log(text)
            except Exception:
                pass

    def _restore_rules_sync(self):
        """
        同步清除已下发的 tc 规则。

        这里刻意用同步方式而不是后台线程：关闭窗口/退出程序前必须确保规则已清掉，
        否则对话框已销毁而线程还在跑，容易出现"规则没清掉、程序已退出"的断网残留。
        """
        iface = self._applied_iface or self.iface_combo.currentText().strip()
        if not iface:
            self._applied = False
            self._applied_iface = None
            return True, "未记录网卡，跳过恢复"

        stdout, stderr, rc = self._run_tc_cmd(f"qdisc del dev {iface} root 2>/dev/null")
        err = (stderr or stdout or "").strip()

        # rc != 0 且提示 no such file 说明本来就没有规则，同样视为恢复完成
        if rc == 0 or not err or "No such file or directory" in err:
            self._applied = False
            self._applied_iface = None
            return True, iface

        return False, f"{iface}：{err}"

    # ---------- 残留规则检测 ----------
    def _start_residual_check(self):
        """
        打开对话框时后台检查设备上是否残留上次未清理的弱网规则。

        覆盖"程序被强杀 / 崩溃 / 直接拔线"这类没走到 closeEvent 的情况：
        下次打开时第一时间发现并提示清除，避免设备一直处于限速丢包状态。
        """
        def worker():
            try:
                # 非 root 既读不准也清不掉，直接跳过，避免误报
                stdout, _, _ = self._run_adb_shell("id")
                if "uid=0" not in stdout:
                    return

                found = []
                for iface in self.RESIDUAL_CHECK_IFACES:
                    out, err, rc = self._run_tc_cmd(f"qdisc show dev {iface}")
                    if rc != 0 or not out:
                        continue
                    low = out.lower()
                    # netem 一定是弱网规则；htb handle 1: 是本工具带限速时下发的特征
                    if "netem" in low or "handle 1: htb" in low:
                        found.append(iface)
                if found:
                    self.residual_found.emit(found)
            except Exception:
                pass

        threading.Thread(target=worker, daemon=True).start()

    @Slot(list)
    def _on_residual_found(self, ifaces):
        """主线程处理：提示用户是否清除残留规则"""
        # 检测在后台线程中进行，若用户已关闭窗口则不再弹窗打扰
        if self._closing:
            return

        names = "、".join(ifaces)
        reply = QMessageBox.question(
            self, "检测到残留弱网规则",
            f"设备上存在上次未清理的弱网规则（网卡：{names}）。\n"
            f"这会让设备表现为变慢、丢包甚至断网。\n\n是否立即清除？",
            QMessageBox.Yes | QMessageBox.No,
            QMessageBox.Yes
        )
        if reply == QMessageBox.Yes:
            self._clear_residual(ifaces)
        else:
            self.status_label.setText(f"⚠ 检测到残留规则（{names}），未清除")
            self._notify_log(
                f"<span style='color:#B0761F;'>⚠ 检测到残留弱网规则（{names}），按用户选择保留</span>"
            )

    def _clear_residual(self, ifaces):
        """清除启动时检测到的残留规则（后台执行，结果写日志）"""
        self.status_label.setText("正在清除残留规则...")

        def worker():
            ok_list, fail_list = [], []
            for iface in ifaces:
                out, err, rc = self._run_tc_cmd(f"qdisc del dev {iface} root 2>/dev/null")
                msg = (err or out or "").strip()
                if rc == 0 or not msg or "No such file or directory" in msg:
                    ok_list.append(iface)
                else:
                    fail_list.append(f"{iface}：{msg}")

            if ok_list:
                self._notify_log(
                    f"<span style='color:#2F6B45;'>✅ 已清除残留弱网规则：{'、'.join(ok_list)}</span>"
                )
            if fail_list:
                self.status_label.setText("残留规则清除失败")
                self._notify_log(
                    f"<span style='color:#A6402F;'>❌ 残留弱网规则清除失败：{'；'.join(fail_list)}</span>"
                )
                self.show_critical_signal.emit(
                    "清除失败",
                    "以下网卡的残留规则未能清除，请确认设备已 root：\n" + "\n".join(fail_list)
                )
            else:
                self.status_label.setText("残留规则已清除")

        threading.Thread(target=worker, daemon=True).start()

    def closeEvent(self, event):
        """关闭窗口时自动恢复网络，避免设备残留弱网规则（表现为断网 / 无限卡顿）"""
        if self._processing:
            Toast.notify(self, "操作正在进行中，请稍候再关闭。")
            event.ignore()
            return

        if self._applied:
            ok, detail = self._restore_rules_sync()
            if ok:
                self._notify_log(
                    f"<span style='color:#2F6B45;'>✅ 已自动恢复网卡 {detail} 的网络限制</span>"
                )
            else:
                self._notify_log(
                    f"<span style='color:#A6402F;'>❌ 弱网规则自动恢复失败（{detail}）</span>"
                )
                iface = self._applied_iface or self.iface_combo.currentText().strip()
                QMessageBox.warning(
                    self, "恢复失败",
                    f"弱网规则未能自动恢复，设备可能仍处于限速 / 丢包状态。\n\n"
                    f"请重新打开本窗口点击「清除」，或手动执行：\n"
                    f"adb shell tc qdisc del dev {iface} root\n\n"
                    f"错误信息：{detail}"
                )

        self._closing = True
        event.accept()