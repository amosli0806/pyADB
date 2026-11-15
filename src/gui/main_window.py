# src/gui/main_window.py
import hashlib
import os
import re
import sys
import subprocess
import threading
from PySide6.QtWidgets import (
    QMainWindow, QWidget, QVBoxLayout, QHBoxLayout,
    QToolBar, QPushButton, QLineEdit,
    QSplitter, QTextEdit, QMenu, QMessageBox, QFileDialog,
    QDialog, QScrollArea, QGridLayout, QLabel, QProgressDialog
)
from PySide6.QtCore import Qt, Slot, QTimer
from PySide6.QtGui import QAction, QIcon, QShortcut, QKeySequence, QTextCursor
from src.models.command import Command
from src.gui.device_selector import DeviceSelector
from src.gui.command_list import CommandListWidget
from src.gui.dialogs.wireless_dialog import WirelessDialog
from src.gui.dialogs.custom_command_dialog import CustomCommandDialog
from src.core.adb_manager import AdbManager
from src.core.command_manager import CommandManager
from src.utils.helpers import get_timestamp, get_adb_path
from src.utils.settings import Settings
from src.gui.dialogs.search_results_dialog import SearchResultsDialog
from PySide6.QtWidgets import QApplication
from src.gui.dialogs.select_commands_dialog import SelectCommandsDialog
from PySide6.QtWidgets import QSystemTrayIcon
import qtawesome as qta

# 新增导入更新模块
from src.core.updater import Updater
from src.utils.version import APP_VERSION


class FlowLayout(QHBoxLayout):
    """流式布局，子控件超出宽度时自动换行"""
    def __init__(self, parent=None, margin=0, spacing=-1):
        super().__init__(parent)
        self.setContentsMargins(margin, margin, margin, margin)
        self.setSpacing(spacing)
        self._line_height = 0

    def addWidget(self, widget):
        super().addWidget(widget)
        widget.show()

    def setGeometry(self, rect):
        super().setGeometry(rect)
        self._do_layout(rect)

    def _do_layout(self, rect):
        if self.count() == 0:
            return
        x = rect.x()
        y = rect.y()
        line_height = 0
        spacing = self.spacing()
        for i in range(self.count()):
            item = self.itemAt(i)
            widget = item.widget()
            if not widget:
                continue
            size_hint = widget.sizeHint()
            if x + size_hint.width() > rect.right() and line_height > 0:
                x = rect.x()
                y += line_height + spacing
                line_height = 0
            widget.setGeometry(x, y, size_hint.width(), size_hint.height())
            x += size_hint.width() + spacing
            line_height = max(line_height, size_hint.height())
        self._line_height = line_height


class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("捕虫师")
        self.resize(1000, 600)

        self.adb_manager = AdbManager()
        self.command_manager = CommandManager()

        self.adb_manager.device_list_updated.connect(self.on_device_list_updated)
        self.adb_manager.command_output.connect(self.append_log)
        self.adb_manager.command_started.connect(self.on_command_started)
        self.adb_manager.command_finished.connect(self.on_command_finished)
        self.adb_manager.command_stopped.connect(self.on_command_stopped)
        self.current_output_dir = Settings.get_output_dir()

        self.setup_ui()
        self.init_tray()
        self.last_devices = set()

        # 应用保存的显示过滤
        self._apply_display_filter()

        self.command_list.execute_signal.connect(self.on_execute_single_command)
        self.command_list.stop_signal.connect(self.on_stop_command)

        self._executing_selected = False
        self._selected_executing_ids = set()
        self.apply_shortcuts()
        self.device_selector.start_refresh()
        self._mirror_dialog = None

        # 当前版本号（硬编码，发布时修改）
        # 当前版本号统一取自 src/utils/version.py（发版时只改那一处）
        self.current_version = APP_VERSION
        self.updater = None

    def _apply_display_filter(self):
        """根据配置重新应用显示过滤模式"""
        saved_ids = Settings.load_display_command_ids()
        if saved_ids:
            self.command_list.set_filter_by_ids(saved_ids)
        else:
            mode = Settings.load_display_filter_mode()
            if mode == 'all':
                self.command_list.set_filter(True, True)
            elif mode == 'preset':
                self.command_list.set_filter(True, False)
            elif mode == 'custom':
                self.command_list.set_filter(False, True)
            else:
                self.command_list.set_filter(True, True)

    def init_tray(self):
        # 先置空：托盘不可用（部分 Linux 环境）时保持 None，
        # 避免 closeEvent / 设备断开提示访问未定义属性而抛 AttributeError
        self.tray = None
        if QSystemTrayIcon.isSystemTrayAvailable():
            self.tray = QSystemTrayIcon(self)
            icon_path = os.path.join(os.path.dirname(__file__), '../../resources/icons/app_icon.ico')
            if os.path.exists(icon_path):
                self.tray.setIcon(QIcon(icon_path))
            else:
                self.tray.setIcon(self.windowIcon())
            self.tray.setToolTip("捕虫师")
            self.tray.show()

    def _set_execute_selected_button_state(self, enabled, text):
        """只切换可用状态与文案，外观交给 QSS（#primaryBtn 的 :disabled 规则）"""
        self.execute_selected_btn.setEnabled(enabled)
        self.execute_selected_btn.setText(text)
        self.execute_selected_btn.update()

    def setup_ui(self):
        central = QWidget()
        self.setCentralWidget(central)
        main_layout = QVBoxLayout(central)
        main_layout.setContentsMargins(12, 12, 12, 12)
        main_layout.setSpacing(10)

        # ---------- 工具栏 ----------
        toolbar = QToolBar()
        toolbar.setMovable(False)      # 禁止拖动工具栏
        toolbar.setFloatable(False)    # 禁止拖出成浮动窗口
        toolbar.setContextMenuPolicy(Qt.PreventContextMenu)
        self.addToolBar(toolbar)

        icon_path = os.path.join(os.path.dirname(__file__), '../../resources/icons')
        self.setWindowIcon(QIcon(os.path.join(icon_path, 'app_icon.ico')))

        # ---- 设备选择器（内部包含刷新按钮，单独在 device_selector.py 中修改） ----
        self.device_selector = DeviceSelector(self.adb_manager)
        self.device_selector.device_changed.connect(self.on_device_changed)
        toolbar.addWidget(self.device_selector)

        # ---- 执行选中：全窗口唯一的实心主按钮 ----
        self.execute_selected_btn = QPushButton(" 执行选中")
        self.execute_selected_btn.setObjectName("primaryBtn")
        self.execute_selected_btn.clicked.connect(self.on_execute_selected)
        toolbar.addWidget(self.execute_selected_btn)

        toolbar.addSeparator()

        self.path_edit = QLineEdit()
        self.path_edit.setObjectName("outputPathEdit")
        self.path_edit.setText(self.current_output_dir)
        self.path_edit.setPlaceholderText("输出目录")
        self.path_edit.setReadOnly(True)
        self.path_edit.setFocusPolicy(Qt.ClickFocus)
        toolbar.addWidget(self.path_edit)

        # ---- 浏览按钮：次级描边按钮 ----
        self.browse_btn = QPushButton(" 浏览")
        self.browse_btn.setIcon(qta.icon('fa6s.folder-open', color='#1F1D1A'))
        self.browse_btn.clicked.connect(self.on_browse_output_dir)
        toolbar.addWidget(self.browse_btn)

        toolbar.addSeparator()

        # ---- 菜单按钮：次级描边按钮 ----
        self.menu_btn = QPushButton(" 菜单")
        self.menu_btn.setIcon(qta.icon('fa6s.gear', color='#1F1D1A'))
        self.menu_btn.setObjectName("menuButton")

        menu = QMenu(self)
        # 菜单外观由全局 QSS 的 QMenu 规则统一控制

        # ---------- ① 展示过滤组 ----------
        show_menu = QMenu ( "展示指令" , self )
        show_menu.setIcon ( qta.icon ( 'fa6s.folder' , color = '#1F1D1A' ) )

        show_all_action = QAction ( "   显示全部   " , self )
        show_all_action.setIcon ( qta.icon ( 'fa6s.list' , color = '#1F1D1A' ) )
        show_all_action.triggered.connect ( lambda : self.on_display_mode_changed ( 'all' ) )
        show_menu.addAction ( show_all_action )

        show_preset_action = QAction ( " 仅显示预设 " , self )
        show_preset_action.setIcon ( qta.icon ( 'fa6s.thumbtack' , color = '#1F1D1A' ) )
        show_preset_action.triggered.connect ( lambda : self.on_display_mode_changed ( 'preset' ) )
        show_menu.addAction ( show_preset_action )

        show_custom_action = QAction ( "仅显示自定义" , self )
        show_custom_action.setIcon ( qta.icon ( 'fa6s.screwdriver-wrench' , color = '#1F1D1A' ) )
        show_custom_action.triggered.connect ( lambda : self.on_display_mode_changed ( 'custom' ) )
        show_menu.addAction ( show_custom_action )

        show_menu.addSeparator ( )
        show_select_action = QAction ( " 自定义显示 " , self )
        show_select_action.setIcon ( qta.icon ( 'fa6s.sliders' , color = '#1F1D1A' ) )
        show_select_action.triggered.connect ( self.on_select_commands )
        show_menu.addAction ( show_select_action )

        menu.addMenu ( show_menu )

        menu.addSeparator ( )

        # ---------- ② 指令管理组 ----------
        self.add_cmd_action = QAction ( "新增指令" , self )
        self.add_cmd_action.setIcon ( qta.icon ( 'fa6s.plus' , color = '#1F1D1A' ) )
        self.add_cmd_action.triggered.connect ( self.on_add_command )
        menu.addAction ( self.add_cmd_action )

        self.edit_cmd_action = QAction ( "编辑指令" , self )
        self.edit_cmd_action.setIcon ( qta.icon ( 'fa6s.pen' , color = '#1F1D1A' ) )
        self.edit_cmd_action.triggered.connect ( self.on_edit_command )
        menu.addAction ( self.edit_cmd_action )

        self.del_cmd_action = QAction ( "删除指令" , self )
        self.del_cmd_action.setIcon ( qta.icon ( 'fa6s.trash-can' , color = '#1F1D1A' ) )
        self.del_cmd_action.triggered.connect ( self.on_delete_command )
        menu.addAction ( self.del_cmd_action )

        menu.addSeparator ( )

        # ---------- ③ 数据管理组 ----------
        self.export_cmd_action = QAction ( "导出指令" , self )
        self.export_cmd_action.setIcon ( qta.icon ( 'fa6s.download' , color = '#1F1D1A' ) )
        self.export_cmd_action.triggered.connect ( self.on_export_commands )
        menu.addAction ( self.export_cmd_action )

        self.import_cmd_action = QAction ( "导入指令" , self )
        self.import_cmd_action.setIcon ( qta.icon ( 'fa6s.upload' , color = '#1F1D1A' ) )
        self.import_cmd_action.triggered.connect ( self.on_import_commands )
        menu.addAction ( self.import_cmd_action )

        menu.addSeparator ( )

        # ---------- ④ 工具 & 帮助组 ----------
        self.shortcut_action = QAction ( "快 捷 键" , self )
        self.shortcut_action.setIcon ( qta.icon ( 'fa6s.keyboard' , color = '#1F1D1A' ) )
        self.shortcut_action.triggered.connect ( self.on_shortcut_settings )
        menu.addAction ( self.shortcut_action )

        self.help_action = QAction ( "帮助中心" , self )
        self.help_action.setIcon ( qta.icon ( 'fa6s.circle-question' , color = '#1F1D1A' ) )
        self.help_action.triggered.connect ( self.on_help )
        menu.addAction ( self.help_action )

        # ---- 新增：检查更新 ----
        self.update_action = QAction("检查更新", self)
        self.update_action.setIcon(qta.icon('fa6s.cloud-arrow-up', color='#1F1D1A'))
        self.update_action.triggered.connect(self.on_check_update)
        menu.addAction(self.update_action)

        menu.addSeparator()

        self.about_action = QAction ( "  关  于 " , self )
        self.about_action.setIcon ( qta.icon ( 'fa6s.circle-info' , color = '#1F1D1A' ) )
        self.about_action.triggered.connect ( self.on_about )
        menu.addAction ( self.about_action )

        self.menu_btn.setMenu ( menu )
        toolbar.addWidget ( self.menu_btn )

        # ---- 清空日志：破坏性操作，描边砖红 ----
        self.clear_log_btn = QPushButton(" 清空")
        self.clear_log_btn.setIcon(qta.icon('fa6s.trash-can', color='#A6402F'))
        self.clear_log_btn.setObjectName("dangerBtn")
        self.clear_log_btn.clicked.connect(self.on_clear_log)
        toolbar.addWidget(self.clear_log_btn)

        # ---------- 主分割器 ----------
        splitter = QSplitter(Qt.Horizontal)
        main_layout.addWidget(splitter)

        # 左侧：搜索框 + 命令列表
        left_widget = QWidget()
        left_layout = QVBoxLayout(left_widget)
        left_layout.setContentsMargins(0, 0, 0, 0)

        search_layout = QHBoxLayout()
        search_layout.setSpacing(8)
        self.search_edit = QLineEdit()
        self.search_edit.setObjectName("searchEdit")
        self.search_edit.setPlaceholderText("搜索预设 / 自定义 / ADB 库中的指令")
        self.search_edit.returnPressed.connect(self.on_search_from_box)
        search_layout.addWidget(self.search_edit)

        # ---- 查找按钮：次级描边 ----
        self.search_btn = QPushButton(" 查找")
        self.search_btn.setIcon(qta.icon('fa6s.magnifying-glass', color='#1F1D1A'))
        self.search_btn.clicked.connect(self.on_search_from_box)
        search_layout.addWidget(self.search_btn)

        left_layout.addLayout(search_layout)

        self.command_list = CommandListWidget(self.command_manager)
        left_layout.addWidget(self.command_list)

        splitter.addWidget(left_widget)

        # 右侧：快捷功能栏 + 日志输出
        right_widget = QWidget()
        right_layout = QVBoxLayout(right_widget)
        right_layout.setContentsMargins(0, 0, 1, 0)

        # ---------- 快捷功能栏（图标暂未迁移，后续修改） ----------
        quick_buttons = [
            ("无线" , self.on_wireless , 'fa6s.wifi') ,
            ("投屏" , self.on_scrcpy , 'fa6s.desktop') ,
            ("安装" , self.on_install_app , 'fa6s.box') ,
            ("推送" , self.on_push_file_folder , 'fa6s.upload') ,
            ("应用管理" , self.on_app_manager , 'fa6s.list') ,
            ("硬件" , self.on_device_info , 'fa6s.microchip') ,
            ("堆转储" , self.on_hprof_dump , 'fa6s.database') ,
            ("Monkey" , self.on_monkey , 'fa6s.robot') ,
            ("Crash" , self.on_crash_log , 'fa6s.bug') ,
            ("ANR" , self.on_anr_analyzer , 'fa6s.hourglass-half') ,
            ("MD5" , self.on_md5_query , 'fa6s.key') ,
            ("弱网" , self.on_weak_network , 'fa6s.signal') ,
            ("抓包" , self.on_packet_capture , 'fa6s.network-wired') ,
        ]

        # 创建容器和网格布局
        quick_widget = QWidget()
        quick_widget.setObjectName("quickPanel")
        quick_layout = QGridLayout(quick_widget)
        quick_layout.setContentsMargins(6, 6, 6, 6)
        quick_layout.setHorizontalSpacing(4)
        quick_layout.setVerticalSpacing(2)

        cols = 7
        for idx , (text , slot , icon_name) in enumerate ( quick_buttons ) :
            row = idx // cols
            col = idx % cols
            btn = QPushButton ( f" {text}" )
            btn.setObjectName("quickBtn")
            btn.setIcon ( qta.icon ( icon_name , color = '#1F1D1A' ) )
            btn.setFixedWidth ( 100 )
            btn.setFixedHeight ( 30 )
            btn.setCursor(Qt.PointingHandCursor)
            btn.clicked.connect ( slot )
            quick_layout.addWidget ( btn , row , col )

        for col in range(cols):
            quick_layout.setColumnStretch(col, 0)

        scroll_area = QScrollArea()
        scroll_area.setWidgetResizable(True)
        scroll_area.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        scroll_area.setVerticalScrollBarPolicy(Qt.ScrollBarAsNeeded)
        scroll_area.setStyleSheet("""
            QScrollArea {
                border: 1px solid #E3DED5;
                border-radius: 8px;
                background-color: #FBFAF7;
            }
            QScrollArea > QWidget > QWidget#quickPanel {
                border: none;
                border-radius: 8px;
                background-color: #FBFAF7;
            }
        """)
        scroll_area.setWidget(quick_widget)

        quick_label = QLabel("快 捷 工 具")
        quick_label.setObjectName("sectionLabel")
        right_layout.setSpacing(8)
        right_layout.addWidget(quick_label)
        right_layout.addWidget(scroll_area)

        self.log_output = QTextEdit()
        self.log_output.setReadOnly(True)
        self.log_output.setLineWrapMode(QTextEdit.WidgetWidth)

        log_label = QLabel("运 行 日 志")
        log_label.setObjectName("sectionLabel")
        right_layout.addWidget(log_label)
        right_layout.addWidget(self.log_output, stretch=1)

        splitter.addWidget(right_widget)
        splitter.setSizes([430, 630])

    def apply_shortcuts(self):
        """根据配置创建所有快捷键"""
        if hasattr(self, '_shortcuts'):
            for sc in self._shortcuts:
                try:
                    sc.activated.disconnect()
                except:
                    pass
                sc.deleteLater()
        self._shortcuts = []

        shortcuts = Settings.get_shortcuts()

        actions = [
            ("refresh_devices", self.device_selector.refresh_devices),
            ("execute_selected", self.on_execute_selected),
            ("clear_log", self.on_clear_log),
            ("search_commands", self.on_search_from_box),
            ("add_command", self.on_add_command),
            ("edit_command", self.on_edit_command),
            ("delete_command", self.on_delete_command),
            ("wireless", self.on_wireless),
            ("scrcpy", self.on_scrcpy),
            ("install_app", self.on_install_app),
            ("push_file", self.on_push_file_folder),
            ("app_manager", self.on_app_manager),
            ("device_info", self.on_device_info),
            ("hprof_dump", self.on_hprof_dump),
            ("monkey", self.on_monkey),
            ("crash_log", self.on_crash_log),
            ("anr_analyzer", self.on_anr_analyzer),
            ("md5_query", self.on_md5_query),
            ("weak_network", self.on_weak_network),
            ("export_commands", self.on_export_commands),
            ("import_commands", self.on_import_commands),
            ("help_center", self.on_help),
            ("packet_capture", self.on_packet_capture),
            ("about", self.on_about),
        ]

        for key, slot in actions:
            ks_str = shortcuts.get(key, "")
            if ks_str:
                try:
                    sc = QShortcut(QKeySequence(ks_str), self)
                    sc.activated.connect(slot)
                    self._shortcuts.append(sc)
                except:
                    pass

    # -------------------- 更新相关 --------------------

    @Slot()
    def on_check_update(self, manual=True):
        if self.updater:
            return  # 已有更新操作进行中，忽略
        self.updater = Updater(self.current_version, self)
        self.updater.check_finished.connect(self._on_update_check_result)
        self.updater.check_for_updates()
        if manual:
            self.statusBar().showMessage("正在检查更新...")

    def _release_updater(self):
        """
        释放更新器引用。

        Updater 以 self 为父对象，只把引用置 None 并不能释放它，
        每次检查更新都会多留一个对象（及其信号连接）。
        这里解除父子关系，交给引用计数在线程结束后回收。
        """
        updater = self.updater
        self.updater = None
        if updater is not None:
            try:
                updater.setParent(None)
            except Exception:
                pass

    def _on_update_check_result(self, has_update, msg, update_info):
        if has_update and update_info is not None:
            from src.gui.dialogs.update_dialog import UpdateDialog
            dlg = UpdateDialog(update_info, self)
            self._exec_dialog(dlg)
            if dlg.action == UpdateDialog.ACTION_UPDATE:
                self._start_download(update_info)
            else:
                self._release_updater()
        else:
            if msg:
                QMessageBox.information(self, "检查更新", msg)
            self._release_updater()

    def _start_download(self, update_info):
        if self.updater is None:
            return

        progress = QProgressDialog("正在下载更新...", "取消", 0, 100, self)
        progress.setWindowTitle("更新下载")
        progress.setWindowModality(Qt.WindowModal)
        progress.setMinimumDuration(0)
        progress.setAutoClose(False)
        progress.setAutoReset(False)
        progress.setValue(0)

        def on_progress(received, total):
            if total > 0:
                progress.setValue(int(received * 100 / total))
                progress.setLabelText(
                    f"正在下载更新... {received / 1024 / 1024:.1f} / {total / 1024 / 1024:.1f} MB"
                )
            else:
                progress.setLabelText(f"正在下载更新... {received / 1024 / 1024:.1f} MB")

        def on_download_finished(success, result):
            progress.close()
            progress.deleteLater()     # 以 self 为父对象，不销毁会累积
            if success:
                from src.gui.dialogs.update_dialog import UpdateReadyDialog
                ready = UpdateReadyDialog(update_info, result, self)
                self._exec_dialog(ready)
                if ready.install_now and self.updater is not None:
                    self.updater.apply_update(result)
                else:
                    self._release_updater()
            else:
                if result != "用户取消":  # 用户取消不弹出错误
                    QMessageBox.critical(self, "下载失败", result)
                self._release_updater()

        self.updater.download_progress.connect(on_progress)
        self.updater.download_finished.connect(on_download_finished)
        self.updater.download_update(update_info.download_url)

        # 取消时调用 cancel_download
        progress.canceled.connect(
            lambda: self.updater.cancel_download() if self.updater else None
        )

    # -------------------- 原有方法（未改动） --------------------

    def on_command_stopped(self, cmd_id, cmd_name):
        """命令被手动停止时立即更新 UI 状态"""
        self.command_list.set_command_state(cmd_id, 'idle')

    def show_info(self, title, text):
        msg = QMessageBox(self)
        msg.setWindowTitle(title)
        msg.setText(text)
        msg.setIcon(QMessageBox.Information)
        msg.setFixedWidth(500)
        try:
            msg.exec()
        finally:
            msg.deleteLater()      # 以 self 为父对象，不销毁会一直累积

    def show_warning(self, title, text):
        msg = QMessageBox(self)
        msg.setWindowTitle(title)
        msg.setText(text)
        msg.setIcon(QMessageBox.Warning)
        msg.setFixedWidth(500)
        try:
            msg.exec()
        finally:
            msg.deleteLater()

    def show_question(self, title, text):
        msg = QMessageBox(self)
        msg.setWindowTitle(title)
        msg.setText(text)
        msg.setIcon(QMessageBox.Question)
        msg.setStandardButtons(QMessageBox.Yes | QMessageBox.No)
        msg.setFixedWidth(500)
        try:
            return msg.exec() == QMessageBox.Yes
        finally:
            msg.deleteLater()

    @Slot()
    def on_device_changed(self, serial):
        self.adb_manager.set_current_device(serial)
        if serial:
            self.append_log(f"<span style='color:#3E6E8E;'>📱 {get_timestamp()} 当前设备切换为: {serial}</span>")
        else:
            self.append_log(f"<span style='color:#A6402F;'>📵 {get_timestamp()} 当前无可用设备</span>")

    @Slot()
    def on_execute_selected(self):
        if self._executing_selected:
            return
        selected = self.command_list.get_selected_commands()
        if not selected:
            QMessageBox.information(self, "提示", "           请至少勾选一个命令          ")
            return

        # ---- 新增：检查已勾选的命令中是否有正在运行的 ----
        running_ids = self.command_list.get_running_ids()
        running_selected = [cmd for cmd in selected if cmd.id in running_ids]
        if running_selected:
            names = "、".join([cmd.name for cmd in running_selected])
            QMessageBox.warning(
                self,
                "提示",
                f"以下已勾选的命令正在运行中，请先手动停止它们再执行选中：\n{names}"
            )
            return
        # -----------------------------------------------

        current_device = self.device_selector.current_device()
        if not current_device:
            self.show_warning("提示", "未检测到可用设备，请连接 Android 设备并授权 USB 调试")
            return
        self._executing_selected = True
        self._set_execute_selected_button_state(False, "执行中...")
        self._selected_executing_ids = {cmd.id for cmd in selected}
        self.log_output.setFocus()
        for cmd in selected:
            self.command_list.set_command_state(cmd.id, 'starting')
            self.adb_manager.execute_command(cmd, current_device, self.path_edit.text())

    @Slot()
    def on_browse_output_dir(self):
        dir_path = QFileDialog.getExistingDirectory(self, "选择输出目录", self.path_edit.text())
        if dir_path:
            self.path_edit.setText(dir_path)
            Settings.set_output_dir(dir_path)

    @Slot()
    def on_add_command(self):
        dlg = CustomCommandDialog(self)
        if self._exec_dialog(dlg):
            cmd = dlg.get_command()
            self.command_manager.add_command(cmd)
            self.command_list.refresh()
            self._apply_display_filter()

    @Slot()
    def on_edit_command(self):
        selected = self.command_list.get_selected_commands()
        if not selected:
            QMessageBox.warning(self, "提示", "       请先勾选一条指令        ")
            return
        if len(selected) > 1:
            QMessageBox.warning(self, "提示", "       请仅勾选一条指令进行编辑        ")
            return
        cmd = selected[0]
        dlg = CustomCommandDialog(self, cmd)
        if self._exec_dialog(dlg):
            updated = dlg.get_command()
            self.command_manager.update_command(cmd.id, updated)
            self.command_list.refresh()
            self._apply_display_filter()

    @Slot ( )
    def on_delete_command ( self ) :
        selected = self.command_list.get_selected_commands ( )
        if not selected :
            QMessageBox.warning ( self , "提示" , "       请至少选中一条指令       " )
            return

        # 检查是否有正在运行的命令
        running_ids = self.command_list.get_running_ids ( )
        running_selected = [ cmd for cmd in selected if cmd.id in running_ids ]
        if running_selected :
            names = '、'.join ( [ cmd.name for cmd in running_selected ] )
            QMessageBox.warning ( self , "无法删除" , f"以下命令正在执行中，请先停止后再删除：\n{names}" )
            return

        # 区分预设和自定义
        preset_cmds = [ cmd for cmd in selected if cmd.is_preset ]
        custom_cmds = [ cmd for cmd in selected if not cmd.is_preset ]

        # 如果全部是预设命令
        if not custom_cmds :
            QMessageBox.warning ( self , "无法删除" , "       预设命令不可删除，请取消勾选后重试       " )
            return

        # 如果有预设命令，提示用户预设将被跳过
        if preset_cmds :
            preset_names = '、'.join ( [ cmd.name for cmd in preset_cmds ] )
            reply = QMessageBox.question (
                self ,
                "确认删除" ,
                f"以下预设命令不可删除，将跳过：\n{preset_names}\n\n"
                f"确定要删除其余自定义指令吗？"
                )
            if reply != QMessageBox.Yes :
                return
            to_delete = custom_cmds
        else :
            # 全部是自定义命令，直接确认
            reply = QMessageBox.question (
                self ,
                "删除" ,
                f"      确定删除指令？       "
                )
            if reply != QMessageBox.Yes :
                return
            to_delete = custom_cmds

        # 执行删除（只删除自定义命令）
        for cmd in to_delete :
            self.command_manager.delete_command ( cmd.id )

        self.command_list.refresh ( )
        self._apply_display_filter ( )

    @Slot()
    def on_wireless(self):
        current_device = self.device_selector.current_device()
        if not current_device:
            self.show_warning("提示", "未检测到可用设备，请连接 Android 设备并授权 USB 调试")
            return
        dlg = WirelessDialog(self.adb_manager, self.device_selector.current_device(), self)
        self._exec_dialog(dlg)

    def _on_mirror_dialog_destroyed(self):
        """投屏对话框销毁时清理引用"""
        self._mirror_dialog = None

    @Slot()
    def on_scrcpy(self):
        current_device = self.device_selector.current_device()
        if not current_device:
            self.show_warning("提示", "未检测到可用设备，请连接 Android 设备并授权 USB 调试")
            return

        if hasattr(self, '_mirror_dialog'):
            try:
                if self._mirror_dialog is not None:
                    self._mirror_dialog.close()
                    self._mirror_dialog.deleteLater()
            except Exception:
                pass
            self._mirror_dialog = None

        device_model = "Android 设备"
        try:
            cmd = ["adb", "-s", current_device, "shell", "getprop", "ro.product.model"]
            if sys.platform == 'win32':
                result = subprocess.run(
                    cmd, capture_output=True, text=True, timeout=5,
                    creationflags=subprocess.CREATE_NO_WINDOW,
                    encoding='utf-8', errors='replace'
                )
            else:
                result = subprocess.run(
                    cmd, capture_output=True, text=True, timeout=5,
                    encoding='utf-8', errors='replace'
                )
            if result.returncode == 0 and result.stdout.strip():
                device_model = result.stdout.strip()
        except:
            pass

        from src.gui.dialogs.screen_mirror_dialog import ScreenMirrorDialog
        self._mirror_dialog = ScreenMirrorDialog(
            current_device, device_model,
            self.path_edit.text(),
            self
        )
        self._mirror_dialog.destroyed.connect(self._on_mirror_dialog_destroyed)
        self._mirror_dialog.show()
        self._mirror_dialog.raise_()
        self._mirror_dialog.activateWindow()

    @Slot ( )
    def on_about ( self ) :
        about_html = """
        <style>
            body {
                font-family: "Microsoft YaHei", "PingFang SC", sans-serif;
                margin: 0;
                padding: 20px 24px;
                background-color: #ffffff;
                line-height: 1.7;
                color: #1F1D1A;
            }
            h1 {
                color: #1F1D1A;
                font-size: 24px;
                margin-top: 0;
                margin-bottom: 4px;
                border-bottom: 3px solid #3E6E8E;
                padding-bottom: 10px;
            }
            .subtitle {
                color: #6E675D;
                font-size: 14px;
                margin-bottom: 18px;
            }
            h3 {
                color: #1F1D1A;
                font-size: 15px;
                margin-top: 16px;
                margin-bottom: 6px;
            }
            ul {
                margin: 2px 0 10px 18px;
                padding: 0;
            }
            li {
                margin: 3px 0;
                font-size: 13px;
                color: #1F1D1A;
            }
            .version {
                font-size: 18px;
                color: #335B75;
                font-weight: bold;
            }
            .footer {
                margin-top: 16px;
                font-size: 12px;
                color: #9C9488;
                text-align: right;
                border-top: 1px solid #F6F4F0;
                padding-top: 10px;
            }
            a {
                color: #335B75;
                text-decoration: none;
            }
            a:hover {
                text-decoration: underline;
            }
            .info-grid {
                display: grid;
                grid-template-columns: 65px 1fr;
                gap: 2px 12px;
                font-size: 13px;
                color: #1F1D1A;
                background: #FBFAF7;
                padding: 10px 14px;
                border-radius: 6px;
                margin-bottom: 12px;
            }
            .info-grid .label {
                color: #6E675D;
            }
            .info-grid .value {
                font-weight: 500;
            }
            .tag {
                display: inline-block;
                background: #2F6B45;
                color: #fff;
                font-size: 11px;
                padding: 0 10px;
                border-radius: 3px;
                margin-left: 6px;
            }
            .tag-blue {
                background: #3E6E8E;
            }
            .changelog li {
                list-style: none;
                margin-left: -10px;
            }
            .changelog li::before {
                content: "✅ ";
            }
            .changelog li.fix::before {
                content: "🔧 ";
            }
            .changelog li.new::before {
                content: "✨ ";
            }
            .changelog li.opt::before {
                content: "⚡ ";
            }
            .changelog li.ui::before {
                content: "🎨 ";
            }
            .key-grid {
                display: grid;
                grid-template-columns: 110px 1fr;
                gap: 2px 14px;
                font-size: 12px;
                color: #1F1D1A;
            }
            .key-grid .key {
                color: #6E675D;
            }
            .key-grid .desc {
                color: #1F1D1A;
            }
            .feature-grid {
                display: grid;
                grid-template-columns: 1fr 1fr;
                gap: 2px 20px;
                font-size: 13px;
                color: #1F1D1A;
                margin: 2px 0 6px 0;
            }
            .feature-grid .item {
                padding: 2px 0;
            }
            .feature-grid .item::before {
                content: "• ";
                color: #3E6E8E;
            }
        </style>

        <h1>🐞 捕虫师</h1>
        <div class="subtitle">一款面向 Android 开发与测试人员的 ADB 图形化效率工具</div>

        <div class="info-grid">
            <span class="label">版本</span>
            <span class="value">{APP_VERSION_TAG} <span class="tag">Release</span></span>
            <span class="label">开发者</span>
            <span class="value">李金钊</span>
            <span class="label">反馈</span>
            <span class="value"><a href="mailto:1573553463@qq.com">1573553463@qq.com</a></span>
        </div>

        <h3>✨ 核心特性</h3>
        <div class="feature-grid">
            <div class="item">设备管理与无线联调</div>
            <div class="item">40+ 预设 ADB 命令</div>
            <div class="item">自定义指令管理</div>
            <div class="item">实时日志抓取</div>
            <div class="item">应用管理（安装/卸载/清除）</div>
            <div class="item">Monkey 测试图形化配置</div>
            <div class="item">网络抓包（tcpdump）</div>
            <div class="item">弱网模拟（netem）</div>
            <div class="item">ANR / Crash 日志分析</div>
            <div class="item">hprof 堆转储</div>
            <div class="item">设备信息查看</div>
            <div class="item">文件推送</div>
            <div class="item">设备投屏（scrcpy）</div>
            <div class="item">MD5 查询</div>
            <div class="item">联网自动更新</div>
        </div>

        <h3>📋 更新日志</h3>
        <ul class="changelog">
            <li class="new">✨ 新增联网自动更新功能（支持 Gitee 源）</li>
            <li class="ui">🎨 全面替换按钮图标为 Font Awesome 图标，统一视觉风格</li>
            <li class="ui">🎨 优化按钮禁用状态：禁用时清除图标仅显示纯文本，避免视觉干扰</li>
            <li class="ui">🎨 优化多个对话框的焦点行为，打开时输入框不再自动获取焦点</li>
            <li class="opt">⚡ 命令列表项的执行/停止按钮增加图标（idle 显示播放，running 显示停止）</li>
            <li class="opt">⚡ 弱网模拟按钮点击不再导致另一个按钮闪烁</li>
            <li class="fix">🔧 修复若干界面细节问题，提升交互流畅度</li>
            <li class="new">✨ 新增快捷键设置页面图标</li>
            <li class="ui">🎨 更新帮助中心与关于页面内容</li>
        </ul>

        <h3>⌨️ 常用快捷键</h3>
        <div class="key-grid">
            <span class="key">F5</span><span class="desc">刷新设备列表</span>
            <span class="key">M</span><span class="desc">执行选中的命令</span>
            <span class="key">Delete</span><span class="desc">清空日志窗口</span>
            <span class="key">Ctrl+F</span><span class="desc">搜索指令</span>
            <span class="key">Ctrl+N</span><span class="desc">新增指令</span>
            <span class="key">Ctrl+E</span><span class="desc">编辑指令</span>
            <span class="key">Ctrl+D</span><span class="desc">删除指令</span>
            <span class="key">Ctrl+W</span><span class="desc">无线联调</span>
            <span class="key">Ctrl+P</span><span class="desc">设备投屏</span>
            <span class="key">Ctrl+I</span><span class="desc">安装应用</span>
            <span class="key">Ctrl+U</span><span class="desc">推送文件</span>
            <span class="key">Ctrl+A</span><span class="desc">应用管理</span>
            <span class="key">Ctrl+H</span><span class="desc">硬件信息</span>
            <span class="key">Ctrl+J</span><span class="desc">hprof 堆转储</span>
            <span class="key">Ctrl+M</span><span class="desc">Monkey 测试</span>
            <span class="key">Ctrl+L</span><span class="desc">崩溃日志</span>
            <span class="key">Ctrl+R</span><span class="desc">ANR 分析</span>
            <span class="key">Ctrl+Q</span><span class="desc">MD5 查询</span>
            <span class="key">Ctrl+Y</span><span class="desc">弱网模拟</span>
            <span class="key">Ctrl+G</span><span class="desc">网络抓包</span>
            <span class="key">F1</span><span class="desc">帮助中心</span>
        </div>
        <p style="font-size: 12px; color: #9C9488; margin-top: 4px;">💡 更多快捷键可在「菜单 → 快捷键设置」中自定义</p>

        <div class="footer">
            捕虫师 —— 让 Android 调试更简单
        </div>
        """

        dialog = QDialog ( self )
        dialog.setWindowTitle ( "关于" )
        dialog.resize ( 540 , 580 )

        layout = QVBoxLayout ( dialog )
        layout.setContentsMargins ( 0 , 0 , 0 , 0 )

        text_edit = QTextEdit ( )
        text_edit.setReadOnly ( True )
        text_edit.setHtml ( about_html.replace ( "{APP_VERSION_TAG}" , f"V{APP_VERSION}" ) )
        text_edit.setStyleSheet (
            """
            QTextEdit {
                background-color: white;
                border: none;
                font-size: 13px;
            }
        """
            )
        layout.addWidget ( text_edit )

        dialog.exec ( )

    @Slot()
    def on_clear_log(self):
        self.log_output.clear()

    @Slot(str)
    def append_log(self, text):
        # 添加新日志
        self.log_output.append(text)

        # 限制日志显示行数为 25 行
        max_lines = 25
        doc = self.log_output.document()

        # 如果块（行）数量超过限制，删除最旧的块
        while doc.blockCount() > max_lines:
            cursor = QTextCursor(doc)
            cursor.movePosition(QTextCursor.Start)  # 移到文档开头
            cursor.movePosition(QTextCursor.Down, QTextCursor.KeepAnchor, 1)  # 选中第一行
            cursor.removeSelectedText()  # 删除该行

        # 3. 自动滚动到最新一行（方便查看最新日志）
        self.log_output.verticalScrollBar().setValue(
            self.log_output.verticalScrollBar().maximum()
        )

    @Slot(int, str)
    def on_command_started(self, cmd_id, cmd_name):
        self.command_list.set_command_state(cmd_id, 'running')

    @Slot(int, str, bool)
    def on_command_finished(self, cmd_id, cmd_name, success):
        self.command_list.set_command_state(cmd_id, 'idle')

        if "logcat" in cmd_name.lower() and success:
            self.append_log(f"<span style='color:#3E6E8E;'>🛑 命令 {cmd_name} 已手动停止</span>")
        else:
            if success:
                icon = "✅"
                color = "#2F6B45"
                status = "成功"
            else:
                icon = "❌"
                color = "#A6402F"
                status = "失败"
            self.append_log(f"<span style='color:{color};'>{icon} 命令 {cmd_name} 执行{status}</span>")

        if cmd_id in self._selected_executing_ids:
            self._selected_executing_ids.remove(cmd_id)
            if not self._selected_executing_ids:
                self._executing_selected = False
                self._set_execute_selected_button_state(True, "执行选中")

    @Slot(Command)
    def on_execute_single_command(self, command):
        current_device = self.device_selector.current_device()
        if not current_device:
            self.show_warning("提示", "未检测到可用设备，请连接 Android 设备并授权 USB 调试")
            self.command_list.set_command_state(command.id, 'idle')
            return
        self.command_list.set_command_state(command.id, 'starting')
        self.adb_manager.execute_command(command, current_device, self.path_edit.text())

    @Slot(Command)
    def on_stop_command(self, command):
        self.adb_manager.stop_command(command.id)

    @Slot(str)
    def on_copy_command(self, command_text):
        clipboard = QApplication.clipboard()
        clipboard.setText(command_text)
        self.append_log(f"<span style='color: blue;'>命令已复制到剪贴板：{command_text}</span>")

    @Slot()
    def on_search_from_box(self):
        keyword = self.search_edit.text().strip()
        if not keyword:
            QMessageBox.information(self, "提示", "   请输入搜索关键词    ")
            return
        results = self.command_manager.search_all(keyword)
        total = (len(results["preset"]) + len(results["custom"]) + len(results["adb_library"]))
        if total == 0:
            QMessageBox.information(self, "搜索结果", f"未找到与“{keyword}”相关的命令")
            return
        dialog = SearchResultsDialog(results, self)
        dialog.set_keyword(keyword)
        dialog.execute_command.connect(self.on_execute_single_command)
        dialog.copy_command.connect(self.on_copy_command)
        self._exec_dialog(dialog)

    @Slot()
    def on_select_commands(self):
        dlg = SelectCommandsDialog(self.command_manager, self)
        if self._exec_dialog(dlg):
            selected_ids = dlg.get_selected_ids()
            Settings.save_display_command_ids(selected_ids)
            Settings.save_display_filter_mode('')
            if selected_ids:
                self.command_list.set_filter_by_ids(selected_ids)
            else:
                self.command_list.set_filter(True, True)
                Settings.save_display_filter_mode('all')

    def on_display_mode_changed(self, mode):
        Settings.save_display_command_ids([])
        Settings.save_display_filter_mode(mode)
        self._apply_display_filter()

    @Slot ( )
    def on_push_file_folder ( self ) :
        current_device = self.device_selector.current_device ( )
        if not current_device :
            self.show_warning ( "提示" , "未检测到可用设备，请连接 Android 设备并授权 USB 调试" )
            return

        dialog = QDialog ( self )
        dialog.setWindowTitle ( "推送" )
        dialog.setFixedSize ( 420 , 150 )
        dialog.setModal ( True )

        layout = QVBoxLayout ( dialog )
        layout.setContentsMargins ( 20 , 20 , 20 , 20 )

        label = QLabel ( "请选择要push的内容类型" , dialog )
        label.setAlignment ( Qt.AlignCenter )
        label.setStyleSheet ( "font-size: 16px; color: #1F1D1A;" )
        layout.addWidget ( label )

        btn_layout = QHBoxLayout ( )
        btn_layout.addStretch ( )

        # ---- 文件按钮：次级描边 ----
        file_btn = QPushButton ( " 文件" , dialog )
        file_btn.setIcon ( qta.icon ( 'fa6s.file' , color = '#1F1D1A' ) )
        file_btn.setFixedHeight ( 32 )
        file_btn.setStyleSheet ( "min-width: 120px; max-width: 120px;" )
        btn_layout.addWidget ( file_btn )

        btn_layout.addSpacing ( 12 )

        # ---- 文件夹按钮：次级描边 ----
        folder_btn = QPushButton ( " 文件夹" , dialog )
        folder_btn.setIcon ( qta.icon ( 'fa6s.folder' , color = '#1F1D1A' ) )
        folder_btn.setFixedHeight ( 32 )
        folder_btn.setStyleSheet ( "min-width: 120px; max-width: 120px;" )
        btn_layout.addWidget ( folder_btn )

        btn_layout.addStretch ( )
        layout.addLayout ( btn_layout )

        dialog._selected = None

        def on_file ( ) :
            dialog._selected = "file"
            dialog.accept ( )

        def on_folder ( ) :
            dialog._selected = "folder"
            dialog.accept ( )

        file_btn.clicked.connect ( on_file )
        folder_btn.clicked.connect ( on_folder )

        if dialog.exec ( ) != QDialog.Accepted :
            return

        if dialog._selected == "file" :
            file_path , _ = QFileDialog.getOpenFileName ( self , "选择要推送的文件" , "" , "所有文件 (*.*)" )
            if not file_path :
                return
            local_path = file_path
        elif dialog._selected == "folder" :
            folder_path = QFileDialog.getExistingDirectory ( self , "选择要推送的文件夹" )
            if not folder_path :
                return
            local_path = folder_path
        else :
            return

        if re.search ( r'[\u4e00-\u9fff]' , local_path ) :
            self.show_warning ( "路径错误" , "本地路径不能包含中文字符，请将文件/文件夹移动到不含中文的目录后重试" )
            return

        from PySide6.QtWidgets import QInputDialog
        default_remote = "/sdcard/naviTracker/"
        remote_path , ok = QInputDialog.getText (
            self , "设备端路径" ,
            "请输入要push路径，例如：\n\n"
            " monkey白名单：/data/local/tmp/\n"
            " 轨迹文件：/sdcard/naviTracker/\n"
            " 折线图配置文件：/sdcard/dumpmeminfo/\n"
            " 车道级数据回放文件：/sdcard/BaiduMapAuto/LLNRecordLog/playback/\n\n"
            "默认为轨迹文件推送路径" ,
            text = default_remote
            )
        if not ok or not remote_path.strip ( ) :
            return

        remote_path = remote_path.strip ( )
        if os.path.isdir ( local_path ) and not remote_path.endswith ( '/' ) :
            remote_path += '/'

        from src.gui.dialogs.push_progress_dialog import PushProgressDialog
        from src.utils.helpers import get_adb_path
        adb_path = get_adb_path ( )
        dialog = PushProgressDialog ( adb_path , current_device , local_path , remote_path , self )
        dialog.log_message.connect ( self.append_log )
        dialog.start ( )
        dialog.exec ( )

    @Slot()
    def on_md5_query(self):
        file_path, _ = QFileDialog.getOpenFileName(self, "选择APK文件", "", "APK文件 (*.apk);;所有文件 (*.*)")
        if not file_path:
            return
        try:
            md5_hash = hashlib.md5()
            with open(file_path, "rb") as f:
                for chunk in iter(lambda: f.read(8192), b""):
                    md5_hash.update(chunk)
            md5_value = md5_hash.hexdigest()
            msg_box = QMessageBox(self)
            msg_box.setWindowTitle("MD5查询结果")
            msg_box.setText(f"文件：{os.path.basename(file_path)}\n\nMD5值：\n{md5_value}")
            msg_box.setIcon(QMessageBox.Information)
            copy_btn = msg_box.addButton("复制到剪贴板", QMessageBox.ActionRole)
            msg_box.addButton("关闭", QMessageBox.RejectRole)
            msg_box.exec()
            if msg_box.clickedButton() == copy_btn:
                clipboard = QApplication.clipboard()
                clipboard.setText(md5_value)
                self.append_log("<span style='color: blue;'>MD5值已复制到剪贴板</span>")
        except Exception as e:
            QMessageBox.critical(self, "错误", f"计算MD5失败：{str(e)}")

    def parse_install_error(self, output):
        if "INSTALL_FAILED_UPDATE_INCOMPATIBLE" in output:
            return ("签名不一致：设备上已安装的同名应用签名与当前 APK 不匹配。\n"
                    "→ 请先卸载旧版本再安装，或使用相同签名的 APK。")
        elif "INSTALL_FAILED_ALREADY_EXISTS" in output:
            return "应用已存在，且无法覆盖安装（可能因为版本降级或签名不同）。"
        elif "INSTALL_FAILED_INVALID_APK" in output:
            return "APK 文件无效或已损坏，请重新获取。"
        elif "INSTALL_FAILED_INSUFFICIENT_STORAGE" in output:
            return "设备存储空间不足，请清理后重试。"
        elif "INSTALL_FAILED_DUPLICATE_PERMISSION" in output:
            return "权限声明重复，请检查 APK 的 manifest 文件。"
        elif "INSTALL_FAILED_NO_MATCHING_ABIS" in output:
            return "APK 架构与设备 CPU 不兼容（例如 x86 APK 安装到 ARM 设备）。"
        elif "INSTALL_FAILED_MISSING_SHARED_LIBRARY" in output:
            return "缺少依赖的共享库，设备不支持该应用所需的功能。"
        elif "INSTALL_FAILED_OLDER_SDK" in output:
            return "设备的 Android 版本过低，不满足 APK 的最低 SDK 要求。"
        elif "INSTALL_FAILED_VERIFICATION_FAILURE" in output:
            return "APK 验证失败，可能被系统安全策略拦截。"
        else:
            return None

    @Slot()
    def on_install_app(self):
        current_device = self.device_selector.current_device()
        if not current_device:
            self.show_warning("提示", "未检测到可用设备，请连接 Android 设备并授权 USB 调试")
            return
        file_path, _ = QFileDialog.getOpenFileName(self, "选择 APK 文件", "", "APK文件 (*.apk);;所有文件 (*.*)")
        if not file_path:
            return
        if re.search(r'[\u4e00-\u9fff]', file_path):
            self.show_warning("路径错误", "APK 文件路径不能包含中文字符，请将文件移动到不含中文的目录后重试")
            return
        filename = os.path.basename(file_path)
        if re.search(r'\([0-9]+\)', filename):
            self.show_warning("文件名不规范", "APK 文件名包含类似 '(1)' 的字符，请重命名文件（移除括号及数字）后再试")
            return

        def install_worker():
            try:
                adb = get_adb_path()
                cmd = [adb, "-s", current_device, "install", "-r", "-t", "-d", "-g", file_path]
                self.append_log(f"<span style='color:blue;'>开始安装: {filename} 到设备 {current_device}</span>")
                creationflags = 0
                if sys.platform == 'win32':
                    creationflags = subprocess.CREATE_NO_WINDOW
                process = subprocess.Popen(
                    cmd,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                    text=True,
                    encoding='utf-8',
                    errors='replace',
                    creationflags=creationflags
                )
                stdout, stderr = process.communicate(timeout=120)
                output = stdout + stderr
                if process.returncode == 0:
                    self.append_log(f"<span style='color:green;'>安装成功: {filename}</span>")
                else:
                    self.append_log(f"<span style='color:red;'>安装失败: {filename}</span>")
                    friendly_msg = self.parse_install_error(output)
                    if friendly_msg:
                        self.append_log(f"<span style='color:#A6402F; font-weight:bold;'>⚠️ 原因：{friendly_msg}</span>")
            except subprocess.TimeoutExpired:
                process.kill()
                self.append_log(f"<span style='color:red;'>安装超时（120秒），已终止</span>")
            except Exception as e:
                self.append_log(f"<span style='color:red;'>安装异常: {str(e)}</span>")

        threading.Thread(target=install_worker, daemon=True).start()

    @Slot ( )
    def on_help ( self ) :
        help_html = """
        <style>
            body {
                font-family: "Microsoft YaHei", "PingFang SC", sans-serif;
                margin: 0;
                padding: 20px 24px;
                background-color: #ffffff;
                line-height: 1.7;
                color: #1F1D1A;
            }
            h1 {
                color: #1F1D1A;
                font-size: 22px;
                margin-top: 0;
                margin-bottom: 4px;
                border-bottom: 3px solid #3E6E8E;
                padding-bottom: 10px;
            }
            .subtitle {
                color: #6E675D;
                font-size: 13px;
                margin-bottom: 16px;
            }
            h3 {
                color: #1F1D1A;
                font-size: 15px;
                margin-top: 18px;
                margin-bottom: 6px;
            }
            h4 {
                color: #3E6E8E;
                font-size: 13px;
                margin-top: 12px;
                margin-bottom: 2px;
            }
            ul {
                margin: 2px 0 8px 18px;
                padding: 0;
            }
            li {
                margin: 2px 0;
                font-size: 13px;
                color: #1F1D1A;
            }
            code {
                background-color: #FBFAF7;
                padding: 2px 6px;
                border-radius: 4px;
                font-family: "Courier New", monospace;
                font-size: 12px;
            }
            .note {
                background-color: #FBF3E6;
                border-left: 4px solid #B0761F;
                padding: 8px 12px;
                margin: 10px 0;
                font-size: 13px;
            }
            .warning {
                background-color: #FBEFEC;
                border-left: 4px solid #A6402F;
                padding: 8px 12px;
                margin: 10px 0;
                font-size: 13px;
            }
            .step {
                font-weight: bold;
                color: #335B75;
            }
            .footer {
                margin-top: 16px;
                font-size: 12px;
                color: #9C9488;
                text-align: right;
                border-top: 1px solid #F6F4F0;
                padding-top: 10px;
            }
        </style>

        <h1>📖 帮助中心</h1>
        <div class="subtitle">捕虫师使用指南 · 常见问题与功能说明</div>

        <h3>🚀 快速上手</h3>
        <ol style="font-size:13px; margin:2px 0 8px 18px;">
            <li>连接 Android 设备，确保已开启 <b>USB 调试</b></li>
            <li>在左侧命令列表中选择要执行的命令，点击「执行选中」</li>
            <li>常用功能可在右侧快捷栏一键访问</li>
            <li>支持自定义指令（菜单 → 新增指令）</li>
        </ol>

        <h3>🔌 设备连接问题排查</h3>
        <ol style="font-size:13px; margin:2px 0 8px 18px;">
            <li><span class="step">确认 USB 调试已开启：</span>「设置 → 开发者选项」中开启「USB 调试」</li>
            <li><span class="step">授权调试：</span>首次连接时手机弹出「允许 USB 调试吗？」，点击「允许」</li>
            <li><span class="step">检查 USB 连接：</span>更换数据线或 USB 端口</li>
            <li><span class="step">驱动问题（Windows）：</span>安装手机厂商提供的 USB 驱动</li>
            <li><span class="step">重启 ADB 服务：</span>执行 <code>adb kill-server</code> 再执行 <code>adb start-server</code></li>
            <li><span class="step">无线连接：</span>先用 USB 连接，点击「无线联调」获取 IP 后连接</li>
        </ol>

        <h3>⚙️ 功能说明</h3>

        <h4>📌 命令执行</h4>
        <ul>
            <li><b>单次执行：</b>勾选命令前复选框，点击「执行选中」；或直接点击命令右侧「执行」按钮</li>
            <li><b>定时/循环：</b>在自定义命令对话框中设置间隔和次数（预设命令不支持）</li>
            <li><b>停止命令：</b>对于持续运行的命令（如 logcat、录屏），点击「停止」按钮终止</li>
        </ul>

        <h4>📦 安装应用</h4>
        <ul>
            <li>点击快捷栏「install安装」，选择 APK 文件即可安装</li>
            <li>安装失败时会显示具体原因（签名不一致、空间不足等）</li>
            <li>APK 路径请勿包含中文，文件名避免出现 <code>(1)</code> 等特殊字符</li>
        </ul>

        <h4>🌐 无线联调</h4>
        <ul>
            <li>先用 USB 连接设备，点击「无线联调」→「获取IP」，然后点击「连接」</li>
            <li>确保手机与电脑处于同一 Wi-Fi 网络</li>
            <li>⚠️ Android 12 及以上设备不支持此功能（系统安全限制）</li>
        </ul>

        <h4>🐵 Monkey 测试</h4>
        <ul>
            <li>点击「Monkey」打开配置对话框，可设置事件数量、种子、各事件比例</li>
            <li>支持选择多个应用包名；可勾选忽略崩溃、超时等异常</li>
            <li>Monkey 运行时日志窗口不会刷屏，仅显示状态</li>
        </ul>

        <h4>📡 网络抓包</h4>
        <ul>
            <li>需要设备已 <b>root</b>，首次使用自动推送 tcpdump</li>
            <li>支持设置过滤表达式（如 <code>host 192.168.1.100</code> 或 <code>port 80</code>）</li>
            <li>抓取结果保存为 pcap 文件，可用 Wireshark 分析</li>
        </ul>

        <h4>🌩️ 弱网模拟</h4>
        <ul>
            <li>需要设备已 <b>root</b> 且内核支持 netem</li>
            <li>可设置延迟、丢包率、带宽限制，选择网卡后应用规则</li>
            <li>断开 WiFi 或重启后规则自动清除</li>
        </ul>

        <h4>📊 日志分析</h4>
        <ul>
            <li><b>Crash 日志：</b>查看设备崩溃日志，支持复制和导出</li>
            <li><b>ANR 分析：</b>从 <code>/data/anr/</code> 拉取 ANR 文件并解析，需 root 权限</li>
            <li><b>hprof 堆转储：</b>选择进程后 dump 内存堆，用于内存分析</li>
        </ul>

        <h3>⚠️ 注意事项</h3>
        <div class="warning">
            <ul style="margin:4px 0 4px 18px;">
                <li><b>Root 权限：</b>弱网模拟、网络抓包、部分文件操作需要设备已 root</li>
                <li><b>路径规范：</b>推送文件/文件夹、APK 路径请勿包含中文字符</li>
                <li><b>应用数据清除：</b>「清除数据」会清空应用所有用户数据，请谨慎操作</li>
                <li><b>Monkey 测试：</b>建议在测试专用机上进行，避免影响日常使用</li>
            </ul>
        </div>

        <div class="footer">
            捕虫师 —— 让 Android 调试更简单
        </div>
        """

        dialog = QDialog ( self )
        dialog.setWindowTitle ( "帮助中心" )
        dialog.resize ( 600 , 620 )

        layout = QVBoxLayout ( dialog )
        layout.setContentsMargins ( 0 , 0 , 0 , 0 )

        text_edit = QTextEdit ( )
        text_edit.setReadOnly ( True )
        text_edit.setHtml ( help_html )
        text_edit.setStyleSheet (
            """
            QTextEdit {
                background-color: white;
                border: none;
                font-size: 13px;
            }
        """
            )
        layout.addWidget ( text_edit )

        dialog.exec ( )

    @Slot()
    def on_app_manager(self):
        current_device = self.device_selector.current_device()
        if not current_device:
            self.show_warning("提示", "未检测到可用设备，请连接 Android 设备并授权 USB 调试")
            return
        from src.gui.dialogs.app_manager_dialog import AppManagerDialog
        dlg = AppManagerDialog(self.adb_manager, current_device, self)
        self._exec_dialog(dlg)

    @Slot()
    def on_device_info(self):
        current_device = self.device_selector.current_device()
        if not current_device:
            self.show_warning("提示", "未检测到可用设备，请连接 Android 设备并授权 USB 调试")
            return
        from src.gui.dialogs.device_info_dialog import DeviceInfoDialog
        dlg = DeviceInfoDialog(self.adb_manager, current_device, self)
        self._exec_dialog(dlg)

    @Slot()
    def on_crash_log(self):
        current_device = self.device_selector.current_device()
        if not current_device:
            self.show_warning("提示", "未检测到可用设备，请连接 Android 设备并授权 USB 调试")
            return
        from src.gui.dialogs.crash_log_dialog import CrashLogDialog
        dlg = CrashLogDialog(self.adb_manager, current_device, self)
        self._exec_dialog(dlg)

    @Slot()
    def on_hprof_dump(self):
        current_device = self.device_selector.current_device()
        if not current_device:
            self.show_warning("提示", "未检测到可用设备，请连接 Android 设备并授权 USB 调试")
            return
        from src.gui.dialogs.process_selector_dialog import ProcessSelectorDialog
        dlg = ProcessSelectorDialog(self.adb_manager, current_device, self)
        if self._exec_dialog(dlg):
            pid = dlg.get_selected_pid()
            if pid:
                self._dump_hprof(pid)

    def _dump_hprof(self, pid):
        current_device = self.device_selector.current_device()
        output_dir = self.path_edit.text()
        timestamp = get_timestamp().replace(":", "-")
        safe_serial = current_device.replace(':', '_')
        device_output_dir = os.path.join(output_dir, safe_serial)
        os.makedirs(device_output_dir, exist_ok=True)
        remote_path = f"/data/local/tmp/heap_{timestamp}.hprof"
        local_path = os.path.join(device_output_dir, f"heap_{timestamp}.hprof")
        def worker():
            try:
                cmd1 = ["adb", "-s", current_device, "shell", "am", "dumpheap", str(pid), remote_path]
                self.append_log(f"开始堆转储，PID: {pid}")
                subprocess.run(cmd1, capture_output=True, timeout=30, check=True,
                               creationflags=subprocess.CREATE_NO_WINDOW if sys.platform == 'win32' else 0)
                cmd2 = ["adb", "-s", current_device, "pull", remote_path, local_path]
                subprocess.run(cmd2, capture_output=True, timeout=60, check=True,
                               creationflags=subprocess.CREATE_NO_WINDOW if sys.platform == 'win32' else 0)
                cmd3 = ["adb", "-s", current_device, "shell", "rm", remote_path]
                subprocess.run(cmd3, capture_output=True, timeout=5,
                               creationflags=subprocess.CREATE_NO_WINDOW if sys.platform == 'win32' else 0)
                self.append_log(f"堆转储成功，保存至: {local_path}")
            except Exception as e:
                self.append_log(f"堆转储失败: {str(e)}")
        threading.Thread(target=worker, daemon=True).start()

    @Slot()
    def on_monkey(self):
        current_device = self.device_selector.current_device()
        if not current_device:
            self.show_warning("提示", "未检测到可用设备，请连接 Android 设备并授权 USB 调试")
            return
        from src.gui.dialogs.monkey_dialog import MonkeyDialog
        dlg = MonkeyDialog(self.adb_manager, current_device, self)
        self._exec_dialog(dlg)

    @Slot()
    def on_anr_analyzer(self):
        current_device = self.device_selector.current_device()
        if not current_device:
            self.show_warning("提示", "未检测到可用设备，请连接 Android 设备并授权 USB 调试")
            return
        from src.gui.dialogs.anr_analyzer_dialog import AnrAnalyzerDialog
        dlg = AnrAnalyzerDialog(self.adb_manager, current_device, self)
        self._exec_dialog(dlg)

    @Slot()
    def on_weak_network(self):
        current_device = self.device_selector.current_device()
        if not current_device:
            self.show_warning("提示", "未检测到可用设备，请连接 Android 设备并授权 USB 调试")
            return
        from src.gui.dialogs.weak_network_dialog import WeakNetworkDialog
        dlg = WeakNetworkDialog(self.adb_manager, current_device, self)
        self._exec_dialog(dlg)

    @Slot()
    def on_export_commands(self):
        file_path, _ = QFileDialog.getSaveFileName(
            self, "导出指令", "custom_commands.json", "JSON文件 (*.json)"
        )
        if file_path:
            try:
                self.command_manager.export_custom_commands(file_path)
                QMessageBox.information(
                    self, "成功", f"    已导出 {len(self.command_manager.get_custom_commands())} 条指令  "
                )
            except Exception as e:
                QMessageBox.critical(self, "错误", f"导出失败: {str(e)}")

    @Slot()
    def on_import_commands(self):
        file_path, _ = QFileDialog.getOpenFileName(
            self, "导入指令", "", "JSON文件 (*.json)"
        )
        if not file_path:
            return

        reply = QMessageBox.question(
            self, "导入选项",
            "选择导入方式：\n\n• 是 - 合并导入（跳过已存在的ID）\n• 否 - 替换所有自定义命令（原自定义命令将丢失）",
            QMessageBox.Yes | QMessageBox.No | QMessageBox.Cancel
        )
        if reply == QMessageBox.Cancel:
            return
        merge = (reply == QMessageBox.Yes)

        success, errors = self.command_manager.import_custom_commands(file_path, merge=merge)
        if errors:
            error_msg = "\n".join(errors[:10])
            if len(errors) > 10:
                error_msg += f"\n... 共 {len(errors)} 条错误"
            QMessageBox.warning(self, "导入完成", f"成功导入 {success} 条指令\n\n错误信息:\n{error_msg}")
        else:
            QMessageBox.information(self, "导入成功", f"成功导入 {success} 条指令")
        self.command_list.refresh()
        self._apply_display_filter()

    @Slot()
    def on_shortcut_settings(self):
        from src.gui.dialogs.shortcut_settings_dialog import ShortcutSettingsDialog
        dlg = ShortcutSettingsDialog(self)
        dlg.shortcuts_changed.connect(self.apply_shortcuts)
        self._exec_dialog(dlg)

    @Slot()
    def on_packet_capture(self):
        current_device = self.device_selector.current_device()
        if not current_device:
            self.show_warning("提示", "未检测到可用设备，请连接 Android 设备并授权 USB 调试")
            return
        from src.gui.dialogs.packet_capture_dialog import PacketCaptureDialog
        dlg = PacketCaptureDialog(self.adb_manager, current_device, self.path_edit.text(), self)
        self._exec_dialog(dlg)

    @Slot()
    def on_device_list_updated(self, devices):
        new_devices = {d['serial'] for d in devices if d['status'] == 'device'}
        disconnected = self.last_devices - new_devices
        if disconnected and self.tray and not self.isActiveWindow():
            for serial in disconnected:
                self.tray.showMessage(
                    "设备断开连接",
                    f"设备 {serial} 已断开",
                    QSystemTrayIcon.Information,
                    3000
                )
        self.last_devices = new_devices

    def _exec_dialog(self, dlg):
        """
        以模态方式运行对话框，结束后立即安排销毁。

        这些对话框都以 self 为父对象，如果只在局部变量中持有，
        Qt 的父子关系会让它们常驻到主窗口销毁为止；
        反复打开会不断累积整棵控件树（内存只增不减）。
        deleteLater 是延迟销毁，返回后仍可安全读取 dlg 的结果。
        """
        try:
            return dlg.exec()
        finally:
            dlg.deleteLater()

    def _shutdown_children(self):
        """退出前的收尾：停定时器、关子窗口、停命令、等线程池"""
        # 1. 停止设备列表轮询
        timer = getattr(self.device_selector, 'timer', None)
        if timer is not None:
            try:
                timer.stop()
            except Exception:
                pass

        # 2. 关闭投屏对话框（内部会终止 scrcpy / 录制进程并停掉状态定时器）
        dlg = getattr(self, '_mirror_dialog', None)
        if dlg is not None:
            try:
                dlg.force_close()
            except Exception:
                try:
                    dlg.close()
                except Exception:
                    pass
            self._mirror_dialog = None

        # 3. 中断未完成的更新下载
        updater = getattr(self, 'updater', None)
        if updater is not None:
            try:
                updater.cancel_download()
            except Exception:
                pass

        # 4. 停止所有 ADB 命令并等待线程池收尾
        #    QThreadPool 在解释器退出时会等待所有运行中的任务，logcat 这类
        #    无限命令会让进程永远退不出去；子进程 adb.exe 也会变成孤儿进程
        try:
            self.adb_manager.stop_all(5000)
        except Exception:
            pass

    def closeEvent(self, event):
        try:
            self._shutdown_children()
        except Exception:
            pass

        tray = getattr(self, 'tray', None)
        if tray is not None:
            try:
                tray.hide()
            except Exception:
                pass

        event.accept()
