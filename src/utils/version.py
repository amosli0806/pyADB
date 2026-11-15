# src/utils/version.py
"""应用版本号的**唯一来源**。

为什么单独放一个文件：版本号原先散在三处 —— 「关于」页标题、检查更新用的
main_window.current_version、以及仓库根目录的 version.json —— 三处各说各话，
发版时最容易漏改一处（比如检查更新永远提示"发现新版本"）。

现在只改这里：
  * 检查更新（src/core/updater.py）直接 import APP_VERSION；
  * 「关于」对话框直接 import 这两个常量；
  * 打包时（main.spec / 发版脚本）也可以从这里读版本号。

改完记得把 GitHub Release 的 tag 打成 v{APP_VERSION}。
"""

APP_VERSION = "2.4.4"

# 构建时间（「关于」对话框里跟版本号一起显示）
BUILD_DATE = "2026-11-15"
