# src/gui/dialogs/app_manager_dialog.py
import subprocess
import sys
import threading
from PySide6.QtCore import Qt, Signal, Slot
from PySide6.QtWidgets import (QDialog, QVBoxLayout, QHBoxLayout,
                               QListWidget, QListWidgetItem, QPushButton,
                               QLabel, QMessageBox, QProgressBar, QLineEdit)
from PySide6.QtGui import QIcon
import qtawesome as qta
from src.gui.toast import Toast


class AppManagerDialog(QDialog):
    operation_result = Signal(bool, str)   # 操作结果信号
    enable_buttons = Signal()              # 恢复按钮信号
    apps_loaded = Signal(list)             # 应用列表加载完成信号，传递应用列表

    def __init__(self, adb_manager, device_serial, parent=None):
        super().__init__(parent)
        self.adb_manager = adb_manager
        self.device_serial = device_serial
        self.all_apps = []                 # 存储完整应用列表，用于搜索过滤
        self.setWindowTitle("应用管理")
        self.resize(600, 500)
        self.setup_ui()
        self.operation_result.connect(self.on_operation_result)
        self.enable_buttons.connect(self._enable_buttons)
        self.apps_loaded.connect(self._populate_apps)

        # 初始状态：无选中，按钮禁用且无图标
        self._update_button_state()

        # 清除搜索框的焦点，打开对话框时不自动聚焦
        self.search_edit.clearFocus()

        self.load_apps()

    def setup_ui(self):
        layout = QVBoxLayout(self)

        # 状态标签
        self.status_label = QLabel("正在加载应用列表...")
        layout.addWidget(self.status_label)

        # ---- 搜索框 ----
        search_layout = QHBoxLayout()
        self.search_edit = QLineEdit()
        self.search_edit.setPlaceholderText("🔍 搜索包名...")
        self.search_edit.setFocusPolicy(Qt.ClickFocus)  # 只有点击时才获得焦点
        self.search_edit.textChanged.connect(self.on_search_text_changed)
        search_layout.addWidget(self.search_edit)

        # 清空搜索按钮
        self.clear_search_btn = QPushButton("清空")
        self.clear_search_btn.setFixedWidth(50)
        self.clear_search_btn.setFixedHeight(30)
        self.clear_search_btn.setObjectName("linkBtn")
        self.clear_search_btn.clicked.connect(self.clear_search)
        search_layout.addWidget(self.clear_search_btn)

        layout.addLayout(search_layout)

        # 应用列表
        self.list_widget = QListWidget()
        layout.addWidget(self.list_widget)

        # 按钮行
        btn_layout = QHBoxLayout()

        # ---- 卸载按钮：红色 ----
        self.uninstall_btn = QPushButton("卸载")
        self.uninstall_btn.setFixedHeight(32)
        self.uninstall_btn.setObjectName("stopBtn")
        self.uninstall_btn.clicked.connect(self.on_uninstall)
        self.uninstall_btn.setEnabled(False)
        btn_layout.addWidget(self.uninstall_btn)

        # ---- 清除数据按钮：橙色 ----
        self.clear_btn = QPushButton("清除数据")
        self.clear_btn.setFixedHeight(32)
        self.clear_btn.setObjectName("warnBtn")
        self.clear_btn.clicked.connect(self.on_clear_data)
        self.clear_btn.setEnabled(False)
        btn_layout.addWidget(self.clear_btn)

        # ---- 强制停止按钮：蓝色 ----
        self.force_stop_btn = QPushButton("强制停止")
        self.force_stop_btn.setFixedHeight(32)
        self.force_stop_btn.clicked.connect(self.on_force_stop)
        self.force_stop_btn.setEnabled(False)
        btn_layout.addWidget(self.force_stop_btn)

        layout.addLayout(btn_layout)

        # 进度条已移除

        # 信号连接
        self.list_widget.itemSelectionChanged.connect(self.on_selection_changed)

    def _update_button_state(self):
        """根据选中状态更新按钮的启用/禁用和文本/图标"""
        selected = self.list_widget.selectedItems()
        enabled = bool(selected)

        # 更新卸载按钮
        if enabled:
            self.uninstall_btn.setEnabled(True)
            self.uninstall_btn.setText(" 卸载")
            self.uninstall_btn.setIcon(qta.icon('fa6s.trash-can', color='white'))
        else:
            self.uninstall_btn.setEnabled(False)
            self.uninstall_btn.setText("卸载")
            self.uninstall_btn.setIcon(QIcon())  # 清除图标

        # 更新清除数据按钮
        if enabled:
            self.clear_btn.setEnabled(True)
            self.clear_btn.setText(" 清除数据")
            self.clear_btn.setIcon(qta.icon('fa6s.eraser', color='#B0761F'))
        else:
            self.clear_btn.setEnabled(False)
            self.clear_btn.setText("清除数据")
            self.clear_btn.setIcon(QIcon())

        # 更新强制停止按钮
        if enabled:
            self.force_stop_btn.setEnabled(True)
            self.force_stop_btn.setText(" 强制停止")
            self.force_stop_btn.setIcon(qta.icon('fa6s.stop', color='#1F1D1A'))
        else:
            self.force_stop_btn.setEnabled(False)
            self.force_stop_btn.setText("强制停止")
            self.force_stop_btn.setIcon(QIcon())

    def clear_search(self):
        """清空搜索框（不自动获取焦点，避免光标闪烁）"""
        self.search_edit.clear()

    def on_search_text_changed(self, text):
        """搜索框内容变化时实时过滤"""
        self._apply_filter(text)

    def _apply_filter(self, keyword: str):
        """根据关键词过滤应用列表"""
        keyword_lower = keyword.lower().strip()

        # 遍历列表，根据包名匹配显示/隐藏
        for i in range(self.list_widget.count()):
            item = self.list_widget.item(i)
            # 获取包名（存储在 UserRole 中）
            pkg = item.data(Qt.UserRole)
            if pkg is None:
                continue

            if keyword_lower:
                # 包名包含关键词则显示，否则隐藏
                match = keyword_lower in pkg.lower()
                item.setHidden(not match)
            else:
                # 关键词为空，全部显示
                item.setHidden(False)

        # 更新状态标签，显示当前可见数量
        visible_count = 0
        for i in range(self.list_widget.count()):
            if not self.list_widget.item(i).isHidden():
                visible_count += 1

        total_count = self.list_widget.count()
        if keyword_lower and visible_count < total_count:
            self.status_label.setText(f"共 {total_count} 个应用（显示 {visible_count} 个）")
        else:
            self.status_label.setText(f"共 {total_count} 个应用")

    def load_apps(self):
        """后台加载应用列表，完成后通过信号发送"""
        def worker():
            # 获取第三方应用
            cmd3 = ["adb", "-s", self.device_serial, "shell", "pm", "list", "packages", "-3"]
            result3 = self._run_adb_cmd(cmd3)
            third_party = [line.split(':')[1].strip() for line in result3.splitlines() if line.startswith("package:")]

            # 获取系统应用
            cmds = ["adb", "-s", self.device_serial, "shell", "pm", "list", "packages", "-s"]
            results = self._run_adb_cmd(cmds)
            system = [line.split(':')[1].strip() for line in results.splitlines() if line.startswith("package:")]

            all_apps = [(pkg, "系统") for pkg in system] + [(pkg, "第三方") for pkg in third_party]
            all_apps.sort(key=lambda x: x[0])

            # 通过信号传递数据到主线程
            self.apps_loaded.emit(all_apps)

        threading.Thread(target=worker, daemon=True).start()

    @Slot(list)
    def _populate_apps(self, all_apps):
        """主线程中更新列表UI"""
        self.all_apps = all_apps
        self.list_widget.clear()

        for pkg, type_ in all_apps:
            item = QListWidgetItem(f"{pkg}  [{type_}]")
            item.setData(Qt.UserRole, pkg)   # 存储包名，用于搜索过滤
            self.list_widget.addItem(item)

        # 应用过滤（保留当前搜索关键词）
        keyword = self.search_edit.text()
        self._apply_filter(keyword)

        # 更新状态（_apply_filter 已更新状态标签，但如果没有搜索词，显示总数）
        if not keyword:
            self.status_label.setText(f"共 {len(all_apps)} 个应用")

        # 清空选中状态，更新按钮
        self.list_widget.clearSelection()
        self._update_button_state()

    def _run_adb_cmd(self, cmd, timeout=10):
        try:
            if sys.platform == 'win32':
                result = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout,
                                        creationflags=subprocess.CREATE_NO_WINDOW, encoding='utf-8')
            else:
                result = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout, encoding='utf-8')
            return result.stdout
        except Exception:
            return ""

    def on_selection_changed(self):
        """列表选中项变化时更新按钮状态"""
        self._update_button_state()

    def get_selected_pkg(self):
        selected = self.list_widget.selectedItems()
        if selected:
            return selected[0].data(Qt.UserRole)
        return None

    def on_uninstall(self):
        pkg = self.get_selected_pkg()
        if not pkg:
            return
        reply = QMessageBox.question(self, "确认卸载", f"确定要卸载 {pkg} 吗？")
        if reply != QMessageBox.Yes:
            return
        self._run_operation("uninstall", pkg)

    def on_clear_data(self):
        pkg = self.get_selected_pkg()
        if not pkg:
            return
        reply = QMessageBox.question(
            self,
            "确认清除数据",
            f"确定要清除 {pkg} 的数据吗？\n\n⚠️ 此操作将清除该应用的所有用户数据，不可恢复！",
            QMessageBox.Yes | QMessageBox.No
        )
        if reply != QMessageBox.Yes:
            return
        self._run_operation("clear", pkg)

    def on_force_stop(self):
        pkg = self.get_selected_pkg()
        if not pkg:
            return
        reply = QMessageBox.question(
            self,
            "确认强制停止",
            f"确定要强制停止 {pkg} 吗？",
            QMessageBox.Yes | QMessageBox.No
        )
        if reply != QMessageBox.Yes:
            return
        self._run_operation("stop", pkg)

    def _run_operation(self, op, pkg):
        # 操作期间禁用按钮并清除图标（纯文本）
        self.uninstall_btn.setEnabled(False)
        self.uninstall_btn.setText("卸载")
        self.uninstall_btn.setIcon(QIcon())
        self.clear_btn.setEnabled(False)
        self.clear_btn.setText("清除数据")
        self.clear_btn.setIcon(QIcon())
        self.force_stop_btn.setEnabled(False)
        self.force_stop_btn.setText("强制停止")
        self.force_stop_btn.setIcon(QIcon())

        def worker():
            if op == "uninstall":
                cmd = ["adb", "-s", self.device_serial, "uninstall", pkg]
                msg = f"卸载 {pkg}"
            elif op == "clear":
                cmd = ["adb", "-s", self.device_serial, "shell", "pm", "clear", pkg]
                msg = f"清除数据 {pkg}"
            else:  # stop
                cmd = ["adb", "-s", self.device_serial, "shell", "am", "force-stop", pkg]
                msg = f"强制停止 {pkg}"

            try:
                if sys.platform == 'win32':
                    result = subprocess.run(cmd, capture_output=True, text=True, timeout=30,
                                            creationflags=subprocess.CREATE_NO_WINDOW, encoding='utf-8')
                else:
                    result = subprocess.run(cmd, capture_output=True, text=True, timeout=30, encoding='utf-8')
                if result.returncode == 0:
                    self.operation_result.emit(True, f"{msg} 成功")
                else:
                    err_msg = result.stderr or result.stdout or "未知错误"
                    self.operation_result.emit(False, f"{msg} 失败: {err_msg}")
            except Exception as e:
                self.operation_result.emit(False, f"执行出错: {str(e)}")
            finally:
                self.enable_buttons.emit()

        threading.Thread(target=worker, daemon=True).start()

    @Slot()
    def _enable_buttons(self):
        """主线程中恢复按钮状态并刷新列表"""
        # 刷新列表
        self.load_apps()

    @Slot(bool, str)
    def on_operation_result(self, success, message):
        if success:
            Toast.notify(self, message)
        else:
            Toast.notify(self, message)