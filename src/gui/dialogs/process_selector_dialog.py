# src/gui/dialogs/process_selector_dialog.py
import threading
import re
from PySide6.QtWidgets import (QDialog, QVBoxLayout, QHBoxLayout,
                               QListWidget, QListWidgetItem, QPushButton,
                               QLabel, QLineEdit)
from PySide6.QtCore import Qt
from src.core.adb_client import AdbClient


class ProcessSelectorDialog(QDialog):
    def __init__(self, adb_manager, device_serial, parent=None):
        super().__init__(parent)
        self.device_serial = device_serial
        self.adb = AdbClient(serial=device_serial)
        self.all_processes = []
        self.setWindowTitle("堆转储")
        self.resize(500, 550)
        self.setup_ui()
        # 清除搜索框焦点，打开时不自动聚焦
        self.search_edit.clearFocus()
        self.load_processes()

    def setup_ui(self):
        layout = QVBoxLayout(self)

        self.label = QLabel("正在加载应用列表...")
        layout.addWidget(self.label)

        # ---- 搜索框 ----
        search_layout = QHBoxLayout()
        self.search_edit = QLineEdit()
        self.search_edit.setPlaceholderText("🔍 搜索包名...")
        self.search_edit.setFocusPolicy(Qt.ClickFocus)  # 只有点击时才获得焦点
        self.search_edit.textChanged.connect(self.on_search_text_changed)
        search_layout.addWidget(self.search_edit)

        # 清空搜索按钮（保留边框、悬停、按压样式，文字改为"清空"）
        self.clear_search_btn = QPushButton("清空")
        self.clear_search_btn.setFixedWidth(50)
        self.clear_search_btn.setFixedHeight(30)
        self.clear_search_btn.setObjectName("linkBtn")
        self.clear_search_btn.clicked.connect(self.clear_search)
        search_layout.addWidget(self.clear_search_btn)

        layout.addLayout(search_layout)

        self.list = QListWidget()
        layout.addWidget(self.list)

        self.btn = QPushButton("确定")
        self.btn.setFixedHeight(32)
        self.btn.setObjectName("primaryBtn")
        self.btn.clicked.connect(self.accept)
        self.btn.setEnabled(False)
        layout.addWidget(self.btn)

        self.list.itemSelectionChanged.connect(self.on_selection_changed)

    def clear_search(self):
        """清空搜索框（不自动获取焦点）"""
        self.search_edit.clear()

    def on_search_text_changed(self, text):
        self._apply_filter(text)

    def _apply_filter(self, keyword: str):
        keyword_lower = keyword.lower().strip()

        for i in range(self.list.count()):
            item = self.list.item(i)
            pkg = item.data(Qt.UserRole)
            if pkg is None:
                continue

            if keyword_lower:
                item.setHidden(keyword_lower not in pkg.lower())
            else:
                item.setHidden(False)

        # 更新状态标签
        visible_count = 0
        for i in range(self.list.count()):
            if not self.list.item(i).isHidden():
                visible_count += 1

        total_count = self.list.count()
        if keyword_lower and visible_count < total_count:
            self.label.setText(f"共 {total_count} 个应用（显示 {visible_count} 个）")
        else:
            self.label.setText(f"共 {total_count} 个应用")

    def load_processes(self):
        def worker():
            try:
                # 只获取已安装的应用包名（不要求进程正在运行）
                # 同时获取系统应用和第三方应用，与应用管理保持一致
                result = self.adb.run(["shell", "pm", "list", "packages"], timeout=15)

                packages = []
                for line in result.stdout.splitlines():
                    if line.startswith("package:"):
                        pkg = line[8:].strip()
                        if pkg:
                            packages.append(pkg)

                packages.sort()

                # 更新UI（在主线程）
                # 注意：对话框可能已被销毁（用户直接关窗），此时访问任何属性都会抛 RuntimeError
                try:
                    if not self.isVisible():
                        return
                except RuntimeError:
                    return

                self.list.clear()
                for pkg in packages:
                    item = QListWidgetItem(pkg)
                    item.setData(Qt.UserRole, pkg)
                    self.list.addItem(item)

                self.label.setText(f"共 {len(packages)} 个应用")

            except RuntimeError:
                # 对话框已销毁，线程安静退出，避免控制台刷堆栈
                return
            except Exception as e:
                try:
                    self.label.setText(f"加载失败: {e}")
                except RuntimeError:
                    return

        threading.Thread(target=worker, daemon=True).start()

    def on_selection_changed(self):
        self.btn.setEnabled(bool(self.list.selectedItems()))

    def get_selected_pid(self):
        selected = self.list.selectedItems()
        if selected:
            # 用户选中的是包名，但我们需要 PID
            # 为了兼容性，获取选中项的文本（包名）
            pkg = selected[0].data(Qt.UserRole)
            if pkg:
                # 尝试从设备获取该应用的 PID（如果有正在运行的进程）
                try:
                    result = self.adb.run(["shell", "pgrep", "-f", pkg], timeout=5)
                    if result.ok and result.stdout.strip():
                        pid = result.stdout.strip().split('\n')[0]
                        return pid
                except Exception:
                    pass
                # 如果没有正在运行的进程，返回包名（让调用方处理）
                return pkg
        return None