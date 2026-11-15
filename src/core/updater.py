# src/core/updater.py
"""检查更新 / 下载 / 安装。

设计要点（参考虫师项目的成熟做法，按本项目的体量做了裁剪）：

1. **版本号单点来源**：本地版本取自 src/utils/version.py 的 APP_VERSION。
2. **主源 GitHub Releases**：`/releases/latest` 拿 tag、说明、资产与摘要；
   Gitee 上的 version.json 只作为国内网络兜底。
3. **检查节流**：GitHub 未认证 API 限额 60 次/小时（按出口 IP），
   30 秒内重复点「检查更新」直接返回上次结果，不打网络。
4. **限流要说人话**：403 + 配额耗尽时提示「请在 HH:MM 后重试」，而不是一句 HTTP 403。
5. **下载稳健性**：GitHub 实体域名国内常超时，下载走加速镜像；失败 3 次自动重试，
   并支持从 .part 断点续传；下载完按 sha256（GitHub 资产摘要）逐字节校验。
6. **安装**：本体是单文件 exe，无法自替换，改为释放一个 .bat 等待进程退出后覆盖并重启。
"""

import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import threading
import time
import zipfile
from urllib import request, error
from urllib.parse import urlparse
from typing import Optional, Callable

from PySide6.QtCore import QObject, Signal, QTimer
from PySide6.QtWidgets import QMessageBox, QApplication

from src.utils.version import APP_VERSION

# ---------- 更新源 ----------
# 仓库："owner/repo"。留空 = 关闭检查（不联网）。
GITHUB_REPO = "amosli0806/pyADB"
GITHUB_API_LATEST = f"https://api.github.com/repos/{GITHUB_REPO}/releases/latest"
# 兜底源：GitHub 不可达时读 Gitee 上的版本文件
FALLBACK_VERSION_URL = "https://gitee.com/amosli_1_0/py-adb/raw/master/version.json"

# ---------- 下载加速 ----------
# 只对 github.com 官方域的资产 URL 拼镜像前缀；检查更新（API）始终直连，
# 版本与摘要必须来自官方，镜像只负责搬字节，下完仍按 sha256 校验。
DOWNLOAD_MIRROR = "https://gh-proxy.com/"
DOWNLOAD_MIRROR_ENV = "BUGCATCHER_DOWNLOAD_MIRROR"

_TIMEOUT = (5, 20)              # (连接, 读取)
_DOWNLOAD_TIMEOUT = 60          # 流式下载的读超时，比对 API 宽松
_DOWNLOAD_ATTEMPTS = 3
_CHUNK = 256 * 1024

# ---------- 发布包形态 ----------
# 本体是 onedir（目录 + _internal），所以更新资产是**整个目录的 zip**，
# 更新时要整目录替换，不能只换 exe（_internal 里的依赖版本会变）。
# 包名固定前缀 + 版本 + 后缀，和 CI 的产出规则一致；旧客户端若只认 .exe，也保留了兜底。
ASSET_PREFIX = "bugcatcher-"
ASSET_SUFFIX = "-win64.zip"
EXE_ASSET_FALLBACK = True       # 找不到 zip 时退回单文件 exe（兼容早期发布）

# ---------- 检查节流 ----------
_CHECK_MIN_INTERVAL = 30.0
_last_check = {"at": 0.0, "result": None}


class UpdateCancelled(RuntimeError):
    """用户主动取消，与真失败区分开。"""


class UpdateInfo:
    """一次「发现新版本」的全部事实。兼容 GitHub Release 与 Gitee version.json。"""

    def __init__(self, data: dict = None, **kwargs):
        data = dict(data or {})
        data.update(kwargs)
        self.version = str(data.get("version", "") or "")
        self.tag = str(data.get("tag", "") or "")
        self.release_notes = str(data.get("release_notes", "") or data.get("notes", "") or "")
        self.download_url = str(data.get("download_url", "") or data.get("asset_url", "") or "")
        self.asset_name = str(data.get("asset_name", "") or "")
        self.asset_size = int(data.get("asset_size", 0) or data.get("size", 0) or 0)
        self.published_at = str(data.get("published_at", "") or "")
        self.html_url = str(data.get("html_url", "") or "")
        self.digest = str(data.get("digest", "") or "")
        # 旧版 Gitee version.json 里的 md5（有就校验，没有就跳过）
        self.md5 = str(data.get("md5", "") or "")

    @property
    def sha256(self) -> str:
        """GitHub 资产摘要形如 "sha256:xxxx"；没有则空串（= 跳过哈希校验）。"""
        d = self.digest.strip().lower()
        return d.split("sha256:", 1)[1] if d.startswith("sha256:") else ""

    @property
    def size_text(self) -> str:
        if self.asset_size <= 0:
            return ""
        mb = self.asset_size / 1024 / 1024
        return f"{mb:.1f} MB" if mb >= 1 else f"{self.asset_size / 1024:.0f} KB"

    @property
    def date_text(self) -> str:
        return self.published_at[:10] if self.published_at else ""


def _mirror_prefix() -> str:
    """当前生效的镜像前缀（末尾带 /），空串 = 直连。环境变量可整体覆盖。"""
    prefix = os.environ.get(DOWNLOAD_MIRROR_ENV, "").strip() or DOWNLOAD_MIRROR
    if not prefix:
        return ""
    return prefix if prefix.endswith("/") else prefix + "/"


def _mirrored_url(asset_url: str) -> str:
    """只对 github.com 官方域拼镜像；本地桩服务/内网镜像保持直连。"""
    prefix = _mirror_prefix()
    if not prefix or not asset_url:
        return asset_url
    try:
        host = (urlparse(asset_url).hostname or "").lower()
    except Exception:
        host = ""
    if not (host == "github.com" or host.endswith(".github.com")):
        return asset_url
    return prefix + asset_url


def _parse_version(v) -> list:
    """'v2.1.0' / '2.1' / '2.1.0-beta' 统一解析成数字列表"""
    nums = re.findall(r"\d+", str(v))
    return [int(x) for x in nums[:4]] or [0]


def compare_versions(v1, v2) -> int:
    """返回 -1/0/1，容忍 v 前缀与后缀"""
    a, b = _parse_version(v1), _parse_version(v2)
    for i in range(max(len(a), len(b))):
        x = a[i] if i < len(a) else 0
        y = b[i] if i < len(b) else 0
        if x < y:
            return -1
        if x > y:
            return 1
    return 0


def _sha256_of(path: str) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def _md5_of(path: str) -> str:
    h = hashlib.md5()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


class Updater(QObject):
    check_finished = Signal(bool, str, object)      # (有新版本, 文案, UpdateInfo|None)
    download_progress = Signal(int, int)            # (已下载, 总大小)
    download_finished = Signal(bool, str)           # (成功, 本地路径 | 错误信息)

    def __init__(self, current_version: str = None, parent=None):
        super().__init__(parent)
        self.current_version = current_version or APP_VERSION
        self._update_info: Optional[UpdateInfo] = None
        self._temp_file = None
        self._part_file = None
        self._is_cancelled = False

    # ---------------- 检查更新 ----------------
    @staticmethod
    def _fetch_json(url: str, timeout=_TIMEOUT) -> dict:
        req = request.Request(url, headers={
            "User-Agent": "BugCatcher-Client",
            "Accept": "application/vnd.github+json",
        })
        with request.urlopen(req, timeout=timeout) as resp:
            return json.loads(resp.read().decode("utf-8"))

    @staticmethod
    def _pick_asset(assets):
        """
        挑更新包：优先「bugcatcher-<版本>-win64.zip」这个规范名，
        其次任意 -win64.zip，再次第一个 zip，最后才退回 .exe。

        为什么不直接取第一个资产：Release 里常混着说明附件、补丁包，
        一旦挑错，用户下到的东西装不上。
        """
        assets = assets or []
        zips = [a for a in assets if str(a.get("name", "")).lower().endswith(".zip")]
        for a in zips:
            name = str(a.get("name", ""))
            if name.startswith(ASSET_PREFIX) and name.endswith(ASSET_SUFFIX):
                return a
        for a in zips:
            if str(a.get("name", "")).endswith(ASSET_SUFFIX):
                return a
        if zips:
            return zips[0]

        if EXE_ASSET_FALLBACK:
            exes = [a for a in assets if str(a.get("name", "")).lower().endswith(".exe")]
            if exes:
                return next((a for a in exes if "bugcatcher" in str(a.get("name", "")).lower()
                             or "捕虫师" in str(a.get("name", ""))), exes[0])
        return {}

    @staticmethod
    def _info_from_github(data: dict) -> UpdateInfo:
        """GitHub Release -> UpdateInfo：优先挑整包 zip，兼容早期的单文件 exe"""
        tag = str(data.get("tag_name") or "").strip()
        version = tag[1:] if tag[:1].lower() == "v" else tag

        asset = Updater._pick_asset(data.get("assets"))

        body = str(data.get("body") or "")
        # 兼容在说明里写 MD5 的老做法（GitHub 资产本身带 sha256 时优先用 sha256）
        m = re.search(r"md5\s*[:：]\s*([0-9a-fA-F]{32})", body, re.IGNORECASE)

        return UpdateInfo({
            "version": version,
            "tag": tag,
            "release_notes": body.strip(),
            "download_url": str(asset.get("browser_download_url") or ""),
            "asset_name": str(asset.get("name") or ""),
            "asset_size": int(asset.get("size") or 0),
            "published_at": str(data.get("published_at") or ""),
            "html_url": str(data.get("html_url") or ""),
            "digest": str(asset.get("digest") or ""),
            "md5": m.group(1) if m else "",
        })

    def check_for_updates(self, force: bool = False):
        """
        后台检查更新，结果通过 check_finished 返回。

        30 秒内重复调用直接返回上次结果（挡住连点导致的 403 限流）。
        """
        cached = _last_check["result"]
        if not force and cached is not None and (time.monotonic() - _last_check["at"]) < _CHECK_MIN_INTERVAL:
            # 命中节流：别再打网络，但结果仍异步投递，避免在按钮点击栈里直接弹窗
            QTimer.singleShot(0, lambda: self._dispatch(cached))
            return

        def worker():
            result = self._check_impl()
            _last_check["at"] = time.monotonic()
            _last_check["result"] = result
            self._dispatch(result)

        threading.Thread(target=worker, daemon=True).start()

    def _dispatch(self, result):
        has_update, msg, info = result
        if has_update:
            self._update_info = info
        self.check_finished.emit(has_update, msg, info)

    def _check_impl(self):
        """真正发请求的一层。返回 (有新版本, 文案, info|None)"""
        if not GITHUB_REPO or "/" not in GITHUB_REPO:
            return False, "未配置更新仓库", None

        # 1) 主源：GitHub Releases
        try:
            info = self._info_from_github(self._fetch_json(GITHUB_API_LATEST))
            if info.version:
                if compare_versions(self.current_version, info.version) < 0:
                    return True, f"发现新版本 {info.version}", info
                return False, f"当前已是最新版本（{self.current_version}）", None
            reason = "GitHub 未返回版本号"
        except error.HTTPError as e:
            if e.code == 404:
                # 仓库还没有任何 Release，不算错误
                return False, f"当前已是最新版本（{self.current_version}）", None
            if e.code == 403:
                when = ""
                try:
                    reset = e.headers.get("X-RateLimit-Reset") if e.headers else None
                    if reset:
                        when = time.strftime("%H:%M", time.localtime(int(reset)))
                except Exception:
                    pass
                reason = f"请求过于频繁，请在 {when} 后重试" if when else "请求过于频繁，请稍后再试"
            else:
                reason = f"GitHub HTTP {e.code}"
        except Exception as e:
            reason = f"GitHub 不可达（{type(e).__name__}）"

        # 2) 兜底：Gitee version.json
        try:
            info = UpdateInfo(self._fetch_json(FALLBACK_VERSION_URL))
            if info.version:
                if compare_versions(self.current_version, info.version) < 0:
                    return True, f"发现新版本 {info.version}", info
                return False, f"当前已是最新版本（{self.current_version}）", None
            reason += "；Gitee 未返回版本号"
        except Exception as e:
            reason += f"；Gitee 不可达（{type(e).__name__}）"

        return False, f"检查更新失败：{reason}", None

    # ---------------- 下载 ----------------
    def cancel_download(self):
        self._is_cancelled = True
        self._cleanup_temp_file()

    def _cleanup_temp_file(self):
        for path in (self._part_file, self._temp_file):
            if path and os.path.exists(path):
                try:
                    os.remove(path)
                except Exception:
                    pass
        self._part_file = None
        self._temp_file = None

    def download_update(self, url: str = None, progress_callback: Optional[Callable[[int, int], None]] = None):
        """
        下载更新包到临时目录，完成后通过 download_finished 返回本地路径。

        重试 3 次、支持 .part 断点续传、下载后按 sha256/md5 校验。
        """
        if progress_callback:
            self.download_progress.connect(progress_callback)

        url = url or (self._update_info.download_url if self._update_info else "")
        if not url:
            self.download_finished.emit(False, "该版本没有提供安装包")
            return

        def worker():
            self._is_cancelled = False
            try:
                path = self._download(url)
                self.download_finished.emit(True, path)
            except UpdateCancelled:
                self._cleanup_temp_file()
                self.download_finished.emit(False, "用户取消")
            except Exception as e:
                self._cleanup_temp_file()
                self.download_finished.emit(False, f"下载失败：{e}")

        threading.Thread(target=worker, daemon=True).start()

    def _download(self, url: str) -> str:
        name = (self._update_info.asset_name if self._update_info else "") or ""
        ext = os.path.splitext(name)[1].lower() or (
            ".zip" if url.lower().split("?")[0].endswith(".zip") else ".exe")
        fd, target = tempfile.mkstemp(suffix=ext, prefix="bugcatcher_update_")
        os.close(fd)
        self._temp_file = target
        self._part_file = target + ".part"

        dl_url = _mirrored_url(url)
        size_hint = self._update_info.asset_size if self._update_info else 0

        last_err = None
        for attempt in range(1, _DOWNLOAD_ATTEMPTS + 1):
            if self._is_cancelled:
                raise UpdateCancelled()
            try:
                self._download_once(dl_url, self._part_file, size_hint)
                os.replace(self._part_file, target)
                self._part_file = None
                break
            except UpdateCancelled:
                raise
            except Exception as e:
                last_err = e
                if attempt < _DOWNLOAD_ATTEMPTS and not self._is_cancelled:
                    time.sleep(2)       # 稍等再续传
        else:
            raise RuntimeError(f"{last_err}（已重试 {_DOWNLOAD_ATTEMPTS - 1} 次）")

        self._verify(target)
        return target

    def _download_once(self, url: str, part: str, size_hint: int):
        """单次下载；已有 .part 时带 Range 续传（服务端不支持则从头来）"""
        done = os.path.getsize(part) if os.path.exists(part) else 0
        headers = {"User-Agent": "BugCatcher-Client"}
        if done > 0:
            headers["Range"] = f"bytes={done}-"

        req = request.Request(url, headers=headers)
        with request.urlopen(req, timeout=_DOWNLOAD_TIMEOUT) as resp:
            if done > 0 and getattr(resp, "status", 200) == 200:
                done = 0                      # 镜像忽略了 Range，推倒重来
                mode = "wb"
            else:
                mode = "ab" if done else "wb"

            total = int(resp.headers.get("Content-Length") or 0) + done or size_hint or 0
            with open(part, mode) as f:
                while True:
                    if self._is_cancelled:
                        raise UpdateCancelled()
                    chunk = resp.read(_CHUNK)
                    if not chunk:
                        break
                    f.write(chunk)
                    done += len(chunk)
                    self.download_progress.emit(done, total)

    def _verify(self, path: str):
        """优先 sha256（GitHub 资产摘要），其次 md5（version.json 里写的），都没有就跳过"""
        info = self._update_info
        if not info:
            return
        if info.sha256:
            if _sha256_of(path) != info.sha256:
                raise RuntimeError("文件校验失败（sha256 不符），请重新下载")
            return
        if info.md5:
            if _md5_of(path).lower() != info.md5.lower():
                raise RuntimeError("文件校验失败（md5 不符），请重新下载")

    # ---------------- 安装 ----------------
    def _stop_background_processes(self, install_dir: str):
        """
        更新前先收尾：adb server 是常驻进程，它跑在安装目录的 tools 下，
        不停掉会占着目录句柄，导致整个目录换不掉（虫师里也是先 stop adb server）。
        """
        adb = os.path.join(install_dir, "tools", "adb.exe")
        if not os.path.exists(adb):
            return
        try:
            subprocess.run([adb, "kill-server"], capture_output=True, timeout=8,
                           creationflags=subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0)
        except Exception:
            pass

    def _extract_package(self, zip_path: str, install_dir: str) -> str:
        """
        把更新包解压到安装目录的同级暂存目录（同一分区，替换时用 rename 才够快且原子）。

        zip 里通常带一层同名目录（捕虫师/…），这里自动下钻一层找含 exe 的那级。
        """
        staging = install_dir + ".new"
        if os.path.exists(staging):
            shutil.rmtree(staging, ignore_errors=True)
        with zipfile.ZipFile(zip_path) as z:
            z.extractall(staging)

        def has_exe(path):
            try:
                return any(f.lower().endswith(".exe") for f in os.listdir(path))
            except OSError:
                return False

        if has_exe(staging):
            return staging
        for entry in os.listdir(staging):
            inner = os.path.join(staging, entry)
            if os.path.isdir(inner) and has_exe(inner):
                return inner
        raise RuntimeError("更新包里没有找到可执行文件，包可能已损坏")

    def apply_update(self, downloaded_file_path: str):
        """释放替换脚本并退出，由脚本在本进程结束后替换目录/文件并重启"""
        if not getattr(sys, "frozen", False):
            QMessageBox.information(None, "提示", "开发环境不支持自动更新，请在打包后的版本中使用")
            return

        current_exe = sys.executable
        install_dir = os.path.dirname(current_exe)
        self._stop_background_processes(install_dir)

        try:
            if downloaded_file_path.lower().endswith(".zip"):
                payload = self._extract_package(downloaded_file_path, install_dir)
                script = self._create_swap_script(install_dir, payload, current_exe, downloaded_file_path)
            else:
                script = self._create_switch_script(current_exe, downloaded_file_path)
        except Exception as e:
            QMessageBox.critical(None, "更新失败", f"准备更新文件失败：{e}")
            return

        try:
            if sys.platform == "win32":
                DETACHED_PROCESS = 0x00000008
                subprocess.Popen(
                    ["cmd", "/c", script],
                    creationflags=DETACHED_PROCESS | subprocess.CREATE_NO_WINDOW,
                    close_fds=True,
                )
            else:
                subprocess.Popen(["/bin/sh", script], start_new_session=True)
        except Exception as e:
            QMessageBox.critical(None, "更新失败", f"无法启动更新程序：{e}")
            return
        QApplication.quit()

    def _create_swap_script(self, install_dir: str, new_dir: str,
                            current_exe: str, zip_path: str) -> str:
        """
        整目录替换脚本（onedir 更新用）。

        放在安装目录的**上一级**：同分区才能用 rename 快速换目录，
        而且脚本自己不在被替换的目录里，执行中不会被删掉。
        流程：等本体退出 → 旧目录改名为 .old → 新目录改名为正式目录 → 启动 → 清理旧目录。
        任何一步失败都回滚，绝不让用户落到「目录没了」的状态。
        """
        parent = os.path.dirname(install_dir)
        bak = install_dir + ".old"
        log = os.path.join(parent, "update_error.log")

        if sys.platform == "win32":
            content = f'''@echo off
setlocal enabledelayedexpansion
chcp 65001 >nul
rem 等待本体退出
timeout /t 2 /nobreak >nul
set N=0
:retry
if exist "{bak}" rmdir /s /q "{bak}" >nul 2>&1
move "{install_dir}" "{bak}" >nul 2>&1
if not errorlevel 1 goto swapped
set /a N+=1
if !N! geq 20 goto fail
timeout /t 1 /nobreak >nul
goto retry
:swapped
move "{new_dir}" "{install_dir}" >nul 2>&1
if errorlevel 1 goto rollback
start "" "{current_exe}"
del "{zip_path}" >nul 2>&1
rmdir /s /q "{bak}" >nul 2>&1
del "%~f0" >nul 2>&1
exit /b 0
:rollback
move "{bak}" "{install_dir}" >nul 2>&1
:fail
echo 更新失败：文件被占用，已回滚到原版本。> "{log}"
start "" "{current_exe}"
del "%~f0" >nul 2>&1
exit /b 1
'''
            path = os.path.join(parent, "bugcatcher_update.bat")
        else:
            content = f'''#!/bin/sh
sleep 2
rm -rf "{bak}"
mv "{install_dir}" "{bak}" || exit 1
mv "{new_dir}" "{install_dir}" || {{ mv "{bak}" "{install_dir}"; exit 1; }}
rm -f "{zip_path}"
rm -rf "{bak}"
open "{current_exe}"
rm -f "$0"
'''
            path = os.path.join(parent, "bugcatcher_update.sh")

        with open(path, "w", encoding="utf-8") as f:
            f.write(content)
        return path

    def _create_switch_script(self, current_exe: str, downloaded_file: str) -> str:
        """
        生成替换脚本。

        为什么不用 Python 脚本：打包后 sys.executable 就是本体 exe，
        拿它去执行 .py 只会把程序再启动一遍（旧版实现就是这样失效的）。
        用 .bat 只依赖系统 cmd：等 2 秒让本进程退出后覆盖，再拉起新版本。
        """
        if sys.platform == "win32":
            content = f'''@echo off
setlocal enabledelayedexpansion
chcp 65001 >nul
rem 等待本体退出
timeout /t 2 /nobreak >nul
set N=0
:retry
copy /y "{downloaded_file}" "{current_exe}" >nul 2>&1
if not errorlevel 1 goto done
set /a N+=1
if !N! geq 20 goto fail
timeout /t 1 /nobreak >nul
goto retry
:done
start "" "{current_exe}"
del "{downloaded_file}" >nul 2>&1
del "%~f0" >nul 2>&1
exit /b 0
:fail
echo 更新失败：文件被占用，请手动替换。> "{os.path.splitext(current_exe)[0]}_update_error.log"
del "%~f0" >nul 2>&1
exit /b 1
'''
            path = os.path.join(tempfile.gettempdir(), "bugcatcher_update.bat")
        else:
            content = f'''#!/bin/sh
sleep 2
cp -f "{downloaded_file}" "{current_exe}" && rm -f "{downloaded_file}"
open "{current_exe}"
rm -f "$0"
'''
            path = os.path.join(tempfile.gettempdir(), "bugcatcher_update.sh")

        with open(path, "w", encoding="utf-8") as f:
            f.write(content)
        return path
