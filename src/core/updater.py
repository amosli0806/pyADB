# src/core/updater.py
"""
联网更新模块，支持从 Gitee 获取版本信息、下载更新包并执行更新。
使用 QNetworkAccessManager 异步处理，不阻塞 UI。
"""

import os
import sys
import json
import hashlib
import subprocess
import tempfile
import zipfile
from pathlib import Path
from typing import Optional, Dict, Callable

from PySide6.QtCore import QObject, Signal, QUrl, QByteArray, QTimer
from PySide6.QtNetwork import QNetworkAccessManager, QNetworkRequest, QNetworkReply
from PySide6.QtWidgets import QMessageBox, QProgressDialog, QApplication

# 版本信息文件 URL（替换为你的 Gitee 地址）
VERSION_URL = "https://gitee.com/你的用户名/仓库名/raw/master/version.json"
# 如果仓库私有，可能需要添加 ?access_token=xxx，建议公开仓库。


class UpdateInfo:
    """版本信息数据模型"""
    def __init__(self, data: dict):
        self.version = data.get("version", "")
        self.release_notes = data.get("release_notes", "")
        self.download_url = data.get("download_url", "")
        self.md5 = data.get("md5", "")
        self.force_update = data.get("force_update", False)
        self.min_supported_version = data.get("min_supported_version", "")


class Updater(QObject):
    """
    更新管理器，通过信号通知更新状态。
    """
    # 信号：检查完成 (有新版本, 版本号, 更新信息)
    check_finished = Signal(bool, str, object)
    # 信号：下载进度 (当前字节, 总字节)
    download_progress = Signal(int, int)
    # 信号：下载完成 (成功, 本地路径)
    download_finished = Signal(bool, str)

    def __init__(self, current_version: str, parent=None):
        super().__init__(parent)
        self.current_version = current_version
        self.manager = QNetworkAccessManager()
        self.manager.finished.connect(self._on_metadata_reply)
        self.reply = None
        self._update_info = None
        self._download_reply = None
        self._temp_file = None

    def check_for_updates(self):
        """异步检查更新"""
        url = QUrl(VERSION_URL)
        request = QNetworkRequest(url)
        request.setHeader(QNetworkRequest.UserAgentHeader, "BugCatcher-Client")
        self.reply = self.manager.get(request)

    def _on_metadata_reply(self, reply: QNetworkReply):
        if reply.error() != QNetworkReply.NoError:
            self.check_finished.emit(False, "网络错误：" + reply.errorString(), None)
            reply.deleteLater()
            return

        try:
            data = json.loads(bytes(reply.readAll()).decode('utf-8'))
            info = UpdateInfo(data)
            self._update_info = info
            # 比较版本号
            is_new = self._compare_versions(self.current_version, info.version) < 0
            if is_new:
                self.check_finished.emit(True, f"发现新版本 {info.version}", info)
            else:
                self.check_finished.emit(False, f"当前已是最新版本 ({self.current_version})", None)
        except Exception as e:
            self.check_finished.emit(False, f"解析版本信息失败: {str(e)}", None)
        finally:
            reply.deleteLater()
            self.reply = None

    @staticmethod
    def _compare_versions(v1: str, v2: str) -> int:
        """比较版本号，返回 -1 (v1<v2), 0, 1"""
        parts1 = [int(x) for x in v1.split('.')]
        parts2 = [int(x) for x in v2.split('.')]
        for i in range(max(len(parts1), len(parts2))):
            a = parts1[i] if i < len(parts1) else 0
            b = parts2[i] if i < len(parts2) else 0
            if a < b:
                return -1
            elif a > b:
                return 1
        return 0

    def download_update(self, url: str, progress_callback: Optional[Callable[[int, int], None]] = None):
        """下载更新包"""
        if progress_callback:
            self.download_progress.connect(progress_callback)

        # 创建临时文件
        fd, temp_path = tempfile.mkstemp(suffix='.zip')
        os.close(fd)
        self._temp_file = temp_path

        req = QNetworkRequest(QUrl(url))
        req.setHeader(QNetworkRequest.UserAgentHeader, "BugCatcher-Client")
        self._download_reply = self.manager.get(req)
        self._download_reply.downloadProgress.connect(self._on_download_progress)
        self._download_reply.finished.connect(self._on_download_finished)

    def _on_download_progress(self, bytes_received, bytes_total):
        self.download_progress.emit(bytes_received, bytes_total)

    def _on_download_finished(self):
        reply = self._download_reply
        if reply.error() != QNetworkReply.NoError:
            self.download_finished.emit(False, "下载失败：" + reply.errorString())
            reply.deleteLater()
            self._download_reply = None
            return

        # 保存数据到临时文件
        data = reply.readAll()
        with open(self._temp_file, 'wb') as f:
            f.write(data)
        reply.deleteLater()
        self._download_reply = None

        # 校验 MD5
        if self._update_info and self._update_info.md5:
            if not self._verify_md5(self._temp_file, self._update_info.md5):
                self.download_finished.emit(False, "文件校验失败，请重新下载")
                return

        self.download_finished.emit(True, self._temp_file)

    def _verify_md5(self, file_path: str, expected_md5: str) -> bool:
        """计算文件 MD5 并比对"""
        hash_md5 = hashlib.md5()
        with open(file_path, "rb") as f:
            for chunk in iter(lambda: f.read(4096), b""):
                hash_md5.update(chunk)
        return hash_md5.hexdigest().lower() == expected_md5.lower()

    def apply_update(self, archive_path: str):
        """
        执行更新：解压压缩包，启动外部更新器，退出当前程序。
        """
        # 解压到临时目录
        extract_dir = tempfile.mkdtemp()
        try:
            with zipfile.ZipFile(archive_path, 'r') as zip_ref:
                zip_ref.extractall(extract_dir)
        except Exception as e:
            QMessageBox.critical(None, "更新失败", f"解压失败: {str(e)}")
            return

        # 获取当前 exe 路径
        if getattr(sys, 'frozen', False):
            current_exe = sys.executable
        else:
            # 开发环境，不实际更新
            QMessageBox.information(None, "提示", "开发环境不支持更新")
            return

        # 生成外部更新器脚本
        updater_script = self._create_updater_script(current_exe, extract_dir)
        # 启动更新器
        if sys.platform == 'win32':
            subprocess.Popen([sys.executable, updater_script], shell=True, creationflags=subprocess.CREATE_NO_WINDOW)
        else:
            subprocess.Popen([sys.executable, updater_script])
        # 退出当前程序
        QApplication.quit()

    def _create_updater_script(self, current_exe: str, new_files_dir: str) -> str:
        """
        生成外部更新器 Python 脚本，完成文件替换和重启。
        """
        script_content = f'''
import os, sys, time, shutil, subprocess

# 等待主进程退出
time.sleep(2)

# 目标目录（当前 exe 所在目录）
target_dir = os.path.dirname(r"{current_exe}")
new_dir = r"{new_files_dir}"

# 备份旧 exe（可选）
# backup_path = current_exe + ".bak"
# if os.path.exists(backup_path):
#     os.remove(backup_path)
# os.rename(current_exe, backup_path)

# 将新文件复制到目标目录
for item in os.listdir(new_dir):
    src = os.path.join(new_dir, item)
    dst = os.path.join(target_dir, item)
    if os.path.isdir(src):
        if os.path.exists(dst):
            shutil.rmtree(dst)
        shutil.copytree(src, dst)
    else:
        shutil.copy2(src, dst)

# 清理临时目录
shutil.rmtree(new_dir)

# 重启主程序
subprocess.Popen([r"{current_exe}"], creationflags=subprocess.CREATE_NO_WINDOW if sys.platform == 'win32' else 0)
'''
        script_path = os.path.join(tempfile.gettempdir(), "bugcatcher_updater.py")
        with open(script_path, 'w', encoding='utf-8') as f:
            f.write(script_content)
        return script_path