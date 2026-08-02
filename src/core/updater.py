# src/core/updater.py
import os
import sys
import json
import hashlib
import subprocess
import tempfile
from typing import Optional, Callable

from PySide6.QtCore import QObject, Signal, QUrl
from PySide6.QtNetwork import QNetworkAccessManager, QNetworkRequest, QNetworkReply
from PySide6.QtWidgets import QMessageBox, QApplication

VERSION_URL = "https://gitee.com/amosli_1_0/py-adb/raw/master/version.json"

class UpdateInfo:
    def __init__(self, data: dict):
        self.version = data.get("version", "")
        self.release_notes = data.get("release_notes", "")
        self.download_url = data.get("download_url", "")
        self.md5 = data.get("md5", "")
        self.force_update = data.get("force_update", False)
        self.min_supported_version = data.get("min_supported_version", "")

class Updater(QObject):
    check_finished = Signal(bool, str, object)
    download_progress = Signal(int, int)
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
        self._is_cancelled = False  # 新增

    def check_for_updates(self):
        url = QUrl(VERSION_URL)
        request = QNetworkRequest(url)
        request.setHeader(QNetworkRequest.UserAgentHeader, "BugCatcher-Client")
        self.reply = self.manager.get(request)

    def _on_metadata_reply(self, reply: QNetworkReply):
        # 只处理元数据请求，忽略下载请求
        if reply != self.reply:
            reply.deleteLater()
            return

        if reply.error() != QNetworkReply.NoError:
            self.check_finished.emit(False, "网络错误：" + reply.errorString(), None)
            reply.deleteLater()
            self.reply = None
            return

        raw_data = reply.readAll()
        try:
            text = bytes(raw_data).decode('utf-8')
        except UnicodeDecodeError as e:
            self.check_finished.emit(False, f"版本文件编码错误，请确认 URL 指向纯文本 JSON 文件。\n错误：{str(e)}", None)
            reply.deleteLater()
            self.reply = None
            return

        try:
            data = json.loads(text)
            info = UpdateInfo(data)
            self._update_info = info
            is_new = self._compare_versions(self.current_version, info.version) < 0
            if is_new:
                self.check_finished.emit(True, f"发现新版本 {info.version}", info)
            else:
                self.check_finished.emit(False, f"当前已是最新版本 ({self.current_version})", None)
        except json.JSONDecodeError as e:
            self.check_finished.emit(False, f"解析 JSON 失败：{str(e)}\n内容预览：{text[:200]}", None)
        finally:
            reply.deleteLater()
            self.reply = None

    @staticmethod
    def _compare_versions(v1: str, v2: str) -> int:
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
        self._is_cancelled = False  # 重置取消标志
        if progress_callback:
            self.download_progress.connect(progress_callback)

        fd, temp_path = tempfile.mkstemp(suffix='.exe')
        os.close(fd)
        self._temp_file = temp_path

        req = QNetworkRequest(QUrl(url))
        req.setHeader(QNetworkRequest.UserAgentHeader, "BugCatcher-Client")
        self._download_reply = self.manager.get(req)
        self._download_reply.downloadProgress.connect(self._on_download_progress)
        self._download_reply.finished.connect(self._on_download_finished)

    def _on_download_progress(self, bytes_received, bytes_total):
        self.download_progress.emit(bytes_received, bytes_total)

    def cancel_download(self):
        """取消下载"""
        self._is_cancelled = True
        if self._download_reply and self._download_reply.isRunning():
            self._download_reply.abort()

    def _on_download_finished(self):
        reply = self._download_reply
        if reply.error() != QNetworkReply.NoError:
            # 如果是用户取消，不触发错误信号，而是发送取消信息
            if self._is_cancelled:
                self.download_finished.emit(False, "用户取消")
            else:
                self.download_finished.emit(False, "下载失败：" + reply.errorString())
            reply.deleteLater()
            self._download_reply = None
            return

        data = reply.readAll()
        with open(self._temp_file, 'wb') as f:
            f.write(data)
        reply.deleteLater()
        self._download_reply = None

        # 如果下载过程中被取消，则删除临时文件并发送取消信息
        if self._is_cancelled:
            try:
                os.remove(self._temp_file)
            except:
                pass
            self.download_finished.emit(False, "用户取消")
            return

        if self._update_info and self._update_info.md5:
            if not self._verify_md5(self._temp_file, self._update_info.md5):
                self.download_finished.emit(False, "文件校验失败，请重新下载")
                return

        self.download_finished.emit(True, self._temp_file)

    def _verify_md5(self, file_path: str, expected_md5: str) -> bool:
        hash_md5 = hashlib.md5()
        with open(file_path, "rb") as f:
            for chunk in iter(lambda: f.read(4096), b""):
                hash_md5.update(chunk)
        return hash_md5.hexdigest().lower() == expected_md5.lower()

    def apply_update(self, downloaded_file_path: str):
        if not getattr(sys, 'frozen', False):
            QMessageBox.information(None, "提示", "开发环境不支持更新")
            return

        current_exe = sys.executable
        updater_script = self._create_updater_script(current_exe, downloaded_file_path)
        if sys.platform == 'win32':
            subprocess.Popen([sys.executable, updater_script], shell=True, creationflags=subprocess.CREATE_NO_WINDOW)
        else:
            subprocess.Popen([sys.executable, updater_script])
        QApplication.quit()

    def _create_updater_script(self, current_exe: str, downloaded_file: str) -> str:
        script_content = f'''
import os, sys, time, shutil, subprocess
time.sleep(2)
try:
    shutil.copy2(r"{downloaded_file}", r"{current_exe}")
    try:
        os.remove(r"{downloaded_file}")
    except:
        pass
    subprocess.Popen([r"{current_exe}"], creationflags=subprocess.CREATE_NO_WINDOW if sys.platform == 'win32' else 0)
except Exception as e:
    with open(os.path.join(os.path.dirname(r"{current_exe}"), "update_error.log"), "w") as f:
        f.write(str(e))
'''
        script_path = os.path.join(tempfile.gettempdir(), "bugcatcher_updater.py")
        with open(script_path, 'w', encoding='utf-8') as f:
            f.write(script_content)
        return script_path