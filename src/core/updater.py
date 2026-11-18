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
6. **增量更新**：Release 里若有同名 patch-<from>-to-<to>.dpatch，优先下补丁 ——
   只传变化的那几 MB 业务代码，Qt 之类的库从本机旧安装复用；补丁出任何问题都回退全量。
7. **安装**：onedir 包整目录替换（补丁组装的目录与解压出来的目录走同一套替换脚本），
   释放一个 .bat 等进程退出后改名换目录并重启，失败自动回滚。
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
# 按顺序尝试，前一个不通就换下一个，最后直连兜底。
# 单个镜像经常限速或抽风（实测 gh-proxy 只有 200KB/s 左右），多备几个能明显提升成功率。
DOWNLOAD_MIRRORS = (
    "https://gh-proxy.com/",
    "https://ghfast.top/",
    "https://ghproxy.net/",
)
DOWNLOAD_MIRROR = DOWNLOAD_MIRRORS[0]      # 兼容旧引用
DOWNLOAD_MIRROR_ENV = "BUGCATCHER_DOWNLOAD_MIRROR"

# urllib 只接受单个秒数；(连接, 读取) 是 requests 的写法，传进来会直接 TypeError
_TIMEOUT = 20
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

# ---------- 增量更新（文件级补丁）----------
# 思路照搬虫师：更新包里真正每次变的只有业务代码，PySide6 那几十 MB 的 Qt 库
# 在版本之间一个字节都不变。补丁只传"新增或变化"的文件，其余从本机旧安装复制。
# 产物命名必须是 patch-<起点>-to-<目标>.dpatch，绝不能长得像 .zip ——
# 老客户端的 _pick_asset 只挑 .zip，补丁一旦像全量包会被它当全量包下载。
PATCH_ASSET_TEMPLATE = "patch-{from_ver}-to-{to_ver}.dpatch"
PATCH_MANIFEST_NAME = "manifest.json"
PATCH_PAYLOAD_DIR = "payload"
PATCH_FORMAT = 1

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
        # 增量补丁：Release 里存在同名 patch 资产时才有值
        self.patch_name = str(data.get("patch_name", "") or "")
        self.patch_url = str(data.get("patch_url", "") or "")
        self.patch_size = int(data.get("patch_size", 0) or 0)
        self.patch_digest = str(data.get("patch_digest", "") or "")

    @property
    def sha256(self) -> str:
        """GitHub 资产摘要形如 "sha256:xxxx"；没有则空串（= 跳过哈希校验）。"""
        d = self.digest.strip().lower()
        return d.split("sha256:", 1)[1] if d.startswith("sha256:") else ""

    @property
    def patch_sha256(self) -> str:
        d = self.patch_digest.strip().lower()
        return d.split("sha256:", 1)[1] if d.startswith("sha256:") else ""

    @property
    def size_text(self) -> str:
        if self.asset_size <= 0:
            return ""
        mb = self.asset_size / 1024 / 1024
        return f"{mb:.1f} MB" if mb >= 1 else f"{self.asset_size / 1024:.0f} KB"

    @property
    def download_size_text(self) -> str:
        """实际要下的体积：有增量补丁就报补丁大小，否则报全量"""
        if self.patch_size > 0:
            mb = self.patch_size / 1024 / 1024
            return f"增量 {mb:.1f} MB" if mb >= 1 else f"增量 {self.patch_size / 1024:.0f} KB"
        return self.size_text

    @property
    def date_text(self) -> str:
        return self.published_at[:10] if self.published_at else ""


def _mirror_prefixes() -> list:
    """当前可用的镜像前缀列表（末尾都带 /）。环境变量存在时只认它。"""
    env = os.environ.get(DOWNLOAD_MIRROR_ENV, "").strip()
    raw = [env] if env else list(DOWNLOAD_MIRRORS)
    out = []
    for prefix in raw:
        if not prefix:
            continue
        out.append(prefix if prefix.endswith("/") else prefix + "/")
    return out


def _is_github_url(url: str) -> bool:
    try:
        host = (urlparse(url).hostname or "").lower()
    except Exception:
        return False
    return host == "github.com" or host.endswith(".github.com")


def _mirrored_url(asset_url: str, prefix: str = None) -> str:
    """只对 github.com 官方域拼镜像；本地桩服务/内网镜像保持直连。"""
    prefix = _mirror_prefixes()[0] if prefix is None and _mirror_prefixes() else prefix
    if not prefix or not asset_url or not _is_github_url(asset_url):
        return asset_url
    return prefix + asset_url


def _candidate_urls(asset_url: str) -> list:
    """下载候选：各镜像依次尝试，最后直连官方地址。"""
    if not asset_url or not _is_github_url(asset_url):
        return [asset_url]
    urls = [prefix + asset_url for prefix in _mirror_prefixes()]
    urls.append(asset_url)
    return urls


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
        self._downloading = False      # 是否正在下载（决定 cancel 能不能清理临时文件）

    # ---------------- 检查更新 ----------------
    @staticmethod
    def _fetch_json(url: str, timeout=_TIMEOUT) -> dict:
        # 兜一层：万一有人按 requests 习惯传 (连接, 读取) 元组，取读超时而不是让 urlopen 崩
        if isinstance(timeout, (tuple, list)):
            timeout = timeout[-1]
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
    def _pick_patch_asset(assets, from_version: str, to_version: str):
        """
        精确匹配 patch-<from>-to-<to>.dpatch。

        不做模糊匹配：补丁的起点版本必须和用户当前版本完全对上，
        拿错补丁组装出来的目录是坏的，宁可退回全量下载。
        """
        want = PATCH_ASSET_TEMPLATE.format(from_ver=from_version, to_ver=to_version)
        for a in assets or []:
            if str(a.get("name") or "").strip() == want:
                return a
        return {}

    def _info_from_github(self, data: dict) -> UpdateInfo:
        """GitHub Release -> UpdateInfo：优先挑整包 zip，兼容早期的单文件 exe"""
        tag = str(data.get("tag_name") or "").strip()
        version = tag[1:] if tag[:1].lower() == "v" else tag

        asset = Updater._pick_asset(data.get("assets"))
        patch = Updater._pick_patch_asset(data.get("assets"), self.current_version, version)

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
            "patch_name": str(patch.get("name") or ""),
            "patch_url": str(patch.get("browser_download_url") or ""),
            "patch_size": int(patch.get("size") or 0),
            "patch_digest": str(patch.get("digest") or ""),
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
            # 带上具体原因（DNS / 证书 / 超时…），否则用户只知道"不可达"却无从判断
            detail = getattr(e, "reason", None) or e
            reason = f"GitHub 不可达（{str(detail)[:60]}）"

        # 2) 兜底：Gitee version.json
        try:
            info = UpdateInfo(self._fetch_json(FALLBACK_VERSION_URL))
            if info.version:
                if compare_versions(self.current_version, info.version) < 0:
                    return True, f"发现新版本 {info.version}", info
                return False, f"当前已是最新版本（{self.current_version}）", None
            reason += "；Gitee 未返回版本号"
        except Exception as e:
            detail = getattr(e, "reason", None) or e
            reason += f"；Gitee 不可达（{str(detail)[:60]}）"

        return False, f"检查更新失败：{reason}", None

    # ---------------- 下载 ----------------
    def cancel_download(self):
        """
        只在**下载进行中**才有意义。

        为什么加这道判断：界面上的 QProgressDialog 关闭时也会发 canceled 信号，
        而下载完成时我们正好要先 close 掉它 —— 那时若照旧执行清理，
        刚下好的更新包会被删掉，用户点「重启」就报「No such file or directory」。
        """
        if not self._downloading:
            return
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

        info = self._update_info
        # 有增量补丁就优先下补丁：一次更新从上百 MB 降到几 MB
        patch_mode = bool(url is None and info and info.patch_url)
        url = url or (info.patch_url if patch_mode else (info.download_url if info else ""))
        if not url:
            self.download_finished.emit(False, "该版本没有提供安装包")
            return

        def worker():
            self._is_cancelled = False
            self._downloading = True
            try:
                if patch_mode:
                    try:
                        path = self._download(url, patch=True)
                    except UpdateCancelled:
                        raise
                    except Exception:
                        # 补丁坏了/下载失败/校验不过 —— 回退全量，不能让用户卡在这
                        self._cleanup_temp_file()
                        path = self._download(info.download_url, patch=False)
                else:
                    path = self._download(url, patch=False)
                # 先收旗再发信号：界面收到信号会关闭进度框，进而触发 canceled
                self._downloading = False
                self.download_finished.emit(True, path)
            except UpdateCancelled:
                self._downloading = False
                self._cleanup_temp_file()
                self.download_finished.emit(False, "用户取消")
            except Exception as e:
                self._downloading = False
                self._cleanup_temp_file()
                self.download_finished.emit(False, f"下载失败：{e}")

        threading.Thread(target=worker, daemon=True).start()

    def _download(self, url: str, patch: bool = False) -> str:
        info = self._update_info
        if patch:
            ext = ".dpatch"
            size_hint = info.patch_size if info else 0
            prefix = "bugcatcher_patch_"
        else:
            name = (info.asset_name if info else "") or ""
            ext = os.path.splitext(name)[1].lower() or (
                ".zip" if url.lower().split("?")[0].endswith(".zip") else ".exe")
            size_hint = info.asset_size if info else 0
            prefix = "bugcatcher_update_"

        fd, target = tempfile.mkstemp(suffix=ext, prefix=prefix)
        os.close(fd)
        self._temp_file = target
        self._part_file = target + ".part"

        # 多个镜像轮流试，最后直连；同一个源连续失败两次就换下一个
        last_err = None
        done = False
        for dl_url in _candidate_urls(url):
            for attempt in range(1, 3):
                if self._is_cancelled:
                    raise UpdateCancelled()
                try:
                    self._download_once(dl_url, self._part_file, size_hint)
                    os.replace(self._part_file, target)
                    self._part_file = None
                    done = True
                    break
                except UpdateCancelled:
                    raise
                except Exception as e:
                    last_err = e
                    if attempt < 2 and not self._is_cancelled:
                        time.sleep(1)
            if done:
                break
        if not done:
            raise RuntimeError(f"{last_err}（镜像与直连均失败）")

        self._verify(target, patch=patch)
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

    def _verify(self, path: str, patch: bool = False):
        """优先 sha256（GitHub 资产摘要），其次 md5（version.json 里写的），都没有就跳过"""
        info = self._update_info
        if not info:
            return
        if patch:
            expect = info.patch_sha256
            if expect and _sha256_of(path) != expect:
                raise RuntimeError("补丁校验失败（sha256 不符）")
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

    @staticmethod
    def _safe_join(root: str, rel: str) -> str:
        """拼路径并挡住 ../ 之类的越界（补丁内容来自网络，不能无条件信任）"""
        root_abs = os.path.abspath(root)
        target = os.path.abspath(os.path.join(root_abs, rel))
        if not (target == root_abs or target.startswith(root_abs + os.sep)):
            raise RuntimeError(f"补丁里的路径越界：{rel}")
        return target

    def _assemble_from_patch(self, patch_path: str, install_dir: str) -> str:
        """
        按补丁在本机拼出新版本目录，返回它的路径。

        清单列的是**新版本的完整文件清单**：进了 payload 的用补丁里的内容，
        其余的从当前安装复制（顺带校验 sha256）。于是几十 MB 的 Qt 库一个字节都不用下。
        任何一步不对就抛异常 —— 调用方（界面）可以据此退回全量下载。
        """
        staging = install_dir + ".new"
        if os.path.exists(staging):
            shutil.rmtree(staging, ignore_errors=True)
        os.makedirs(staging, exist_ok=True)

        with zipfile.ZipFile(patch_path) as z:
            bad = z.testzip()
            if bad:
                raise RuntimeError(f"增量补丁损坏（第一个坏文件：{bad}）")
            try:
                manifest = json.loads(z.read(PATCH_MANIFEST_NAME).decode("utf-8"))
            except KeyError:
                raise RuntimeError("增量补丁缺少 manifest.json")
            except Exception as e:
                raise RuntimeError(f"增量补丁清单无法解析：{e}")

            if int(manifest.get("format") or 0) != PATCH_FORMAT:
                raise RuntimeError(f"增量补丁格式不支持：{manifest.get('format')!r}")

            files = manifest.get("files") or {}
            if not files:
                raise RuntimeError("增量补丁清单是空的")
            payload = set(manifest.get("payload") or [])

            for rel, meta in files.items():
                dst = self._safe_join(staging, rel)
                if os.path.dirname(dst):
                    os.makedirs(os.path.dirname(dst), exist_ok=True)

                if rel in payload:
                    with z.open(f"{PATCH_PAYLOAD_DIR}/{rel}") as src, open(dst, "wb") as out:
                        shutil.copyfileobj(src, out, 1024 * 1024)
                    continue

                src_path = self._safe_join(install_dir, rel)
                if not os.path.exists(src_path):
                    raise RuntimeError(f"本机缺少文件，无法增量更新：{rel}")
                shutil.copy2(src_path, dst)

                # reuse=True（打包必抖的文件，如 onefile exe）：哈希对不上视为构建抖动，
                # 复用旧文件继续；本机缺失上面已经拦住了。
                if not bool(meta.get("reuse")):
                    expect = str(meta.get("sha256") or "")
                    if expect and _sha256_of(dst) != expect:
                        raise RuntimeError(f"本机文件与该版本清单不一致：{rel}")

        return staging

    def apply_update(self, downloaded_file_path: str):
        """释放替换脚本并退出，由脚本在本进程结束后替换目录/文件并重启"""
        if not getattr(sys, "frozen", False):
            QMessageBox.information(None, "提示", "开发环境不支持自动更新，请在打包后的版本中使用")
            return

        if not downloaded_file_path or not os.path.exists(downloaded_file_path):
            QMessageBox.critical(None, "更新失败",
                                 "更新包已不存在（可能被清理工具或杀毒软件删掉），请重新检查更新后再试。")
            return

        current_exe = sys.executable
        install_dir = os.path.dirname(current_exe)
        self._stop_background_processes(install_dir)

        try:
            if downloaded_file_path.lower().endswith(".dpatch"):
                payload = self._assemble_from_patch(downloaded_file_path, install_dir)
                script = self._create_swap_script(install_dir, payload, current_exe, downloaded_file_path)
            elif downloaded_file_path.lower().endswith(".zip"):
                payload = self._extract_package(downloaded_file_path, install_dir)
                script = self._create_swap_script(install_dir, payload, current_exe, downloaded_file_path)
            else:
                script = self._create_switch_script(current_exe, downloaded_file_path)
        except Exception as e:
            QMessageBox.critical(None, "更新失败", f"准备更新文件失败：{e}")
            return

        try:
            if sys.platform == "win32":
                if script.lower().endswith(".ps1"):
                    # 只给 CREATE_NO_WINDOW：之前叠了 DETACHED_PROCESS，两者互相冲突，
                    # 结果是 cmd 控制台反而可见（用户更新时看到的那个黑窗口）。
                    subprocess.Popen(
                        ["powershell", "-NoProfile", "-NonInteractive",
                         "-ExecutionPolicy", "Bypass", "-File", script],
                        creationflags=subprocess.CREATE_NO_WINDOW,
                        close_fds=True,
                    )
                else:
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
            # 为什么用 .ps1 而不是 .bat：
            # 安装目录叫「捕虫师」，.bat 由 cmd 按当前代码页（GBK）逐行解析，
            # 而我们写文件用的是 UTF-8 —— 中文路径被读成乱码，move 一律失败。
            # 线上的表现就是：弹出一个 cmd 窗口、旧目录没换掉、应用也没起来。
            # .ps1 用 -File 读取带 BOM 的 UTF-8，中文路径不会出错；
            # 顺便把「等 2 秒」换成「轮询 PID」，本体重启慢时也不会抢锁。
            content = f'''$ErrorActionPreference = 'SilentlyContinue'
$install = '{install_dir}'
$newDir  = '{new_dir}'
$bak     = '{bak}'
$exe     = '{current_exe}'
$zip     = '{zip_path}'
$log     = '{log}'
$self    = $PSCommandPath

function Note($msg) {{
    Add-Content -LiteralPath $log -Value ("[{{0}}] {{1}}" -f (Get-Date -Format 'HH:mm:ss'), $msg) -Encoding UTF8
}}

Note "开始更新，等待本体退出"
for ($i = 0; $i -lt 150; $i++) {{
    if (-not (Get-Process -Id {os.getpid()} -ErrorAction SilentlyContinue)) {{ break }}
    Start-Sleep -Milliseconds 200
}}
Start-Sleep -Milliseconds 600

$moved = $false
for ($i = 0; $i -lt 40; $i++) {{
    if (Test-Path -LiteralPath $bak) {{ Remove-Item -LiteralPath $bak -Recurse -Force }}
    Move-Item -LiteralPath $install -Destination $bak -ErrorAction SilentlyContinue
    if (Test-Path -LiteralPath $bak) {{ $moved = $true; break }}
    Start-Sleep -Milliseconds 500
}}

if (-not $moved) {{
    Note "旧目录改名失败：文件被占用，保持原版本"
    Start-Process -FilePath $exe
    Remove-Item -LiteralPath $self -Force
    exit 1
}}

Move-Item -LiteralPath $newDir -Destination $install -ErrorAction SilentlyContinue
$ok = Test-Path -LiteralPath (Join-Path $install '捕虫师.exe')
if (-not $ok) {{
    Note "新目录落位失败，回滚"
    if (Test-Path -LiteralPath $install) {{ Remove-Item -LiteralPath $install -Recurse -Force }}
    Move-Item -LiteralPath $bak -Destination $install -ErrorAction SilentlyContinue
    Start-Process -FilePath $exe
    Remove-Item -LiteralPath $self -Force
    exit 1
}}

Note "更新完成，启动新版本"
Start-Process -FilePath $exe
Remove-Item -LiteralPath $zip -Force
Remove-Item -LiteralPath $bak -Recurse -Force
Remove-Item -LiteralPath $log -Force
Remove-Item -LiteralPath $self -Force
exit 0
'''
            path = os.path.join(parent, "bugcatcher_update.ps1")
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

        # .ps1 必须带 BOM：Windows PowerShell 5.1 读无 BOM 的 UTF-8 会按 ANSI 解析，
        # 中文路径又会被打乱 —— 这正是 .bat 版本更新失败的原因，别在 .ps1 上重蹈
        encoding = "utf-8-sig" if path.lower().endswith(".ps1") else "utf-8"
        with open(path, "w", encoding=encoding) as f:
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
            # 与整目录替换同理：.ps1 + BOM + 轮询 PID，避开 cmd 的中文路径编码问题
            content = f'''$ErrorActionPreference = 'SilentlyContinue'
$src  = '{downloaded_file}'
$dst  = '{current_exe}'
$self = $PSCommandPath
$log  = '{os.path.splitext(current_exe)[0]}_update_error.log'

for ($i = 0; $i -lt 150; $i++) {{
    if (-not (Get-Process -Id {os.getpid()} -ErrorAction SilentlyContinue)) {{ break }}
    Start-Sleep -Milliseconds 200
}}
Start-Sleep -Milliseconds 500

$done = $false
for ($i = 0; $i -lt 40; $i++) {{
    Copy-Item -LiteralPath $src -Destination $dst -Force -ErrorAction SilentlyContinue
    if (-not (Test-Path -LiteralPath $src)) {{ $done = $true; break }}
    if ((Get-Item -LiteralPath $dst).Length -eq (Get-Item -LiteralPath $src).Length) {{ $done = $true; break }}
    Start-Sleep -Milliseconds 500
}}

if (-not $done) {{
    Add-Content -LiteralPath $log -Value "更新失败：文件被占用，请手动替换。" -Encoding UTF8
}}

Start-Process -FilePath $dst
Remove-Item -LiteralPath $src -Force
Remove-Item -LiteralPath $self -Force
exit 0
'''
            path = os.path.join(tempfile.gettempdir(), "bugcatcher_update.ps1")
        else:
            content = f'''#!/bin/sh
sleep 2
cp -f "{downloaded_file}" "{current_exe}" && rm -f "{downloaded_file}"
open "{current_exe}"
rm -f "$0"
'''
            path = os.path.join(tempfile.gettempdir(), "bugcatcher_update.sh")

        encoding = "utf-8-sig" if path.lower().endswith(".ps1") else "utf-8"
        with open(path, "w", encoding=encoding) as f:
            f.write(content)
        return path
