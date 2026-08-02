# 🐞 捕虫师（BugCatcher）

一款面向 Android 开发与测试人员的 ADB 图形化效率工具，旨在简化 ADB 命令的日常使用，提升调试与测试效率。
📜 许可证：本项目仅供学习与个人使用，未经授权不得用于商业用途。
---

## ✨ 主要特性

- **设备管理**：自动识别连接的 Android 设备，支持 USB / 无线连接，设备状态实时刷新。
- **命令执行**：内置 40+ 常用 ADB 预设命令（设备管理、应用管理、日志抓取、系统信息等），支持自定义命令、定时/循环执行。
- **快捷工具**：一键安装 APK、推送文件、设备投屏（scrcpy）、无线联调、MD5 校验等。
- **日志分析**：实时抓取 logcat、崩溃日志（Crash）、ANR 日志，并支持本地解析与导出。
- **性能测试**：Monkey 测试图形化配置、弱网模拟（netem）、网络抓包（tcpdump）、hprof 堆转储。
- **应用管理**：查看/搜索已安装应用，支持卸载、清除数据、强制停止。
- **设备信息**：查看分辨率、CPU、内存、分区可用空间等硬件信息，一键复制。
- **完全可定制**：支持自定义指令、快捷键，命令列表可按需筛选显示。
- **跨平台**：支持 Windows / macOS / Linux（需安装对应平台的 ADB 和 scrcpy 工具）。

---

## 📦 环境要求

- **Python**：3.8 及以上版本
- **操作系统**：Windows / macOS / Linux
- **外部依赖**：ADB 工具（已内置在 `tools/` 目录）  
  如需投屏功能，需额外下载 `scrcpy` 并放置于 `tools/` 目录（或配置系统 PATH）。

---

## 🚀 快速开始

1. 创建虚拟环境（推荐）
   python -m venv .venv
# Windows
   .venv\Scripts\activate
# macOS/Linux
   source .venv/bin/activate

2. 安装依赖
   pip install -r requirements.txt

3. 运行程序
   python main.py

4. 打包命令
   pyinstaller main.spec


## 项目结构
.
├── main.py                  # 程序入口
├── src/
│   ├── core/                # ADB 管理器、命令管理器、无线管理
│   ├── gui/                 # 主窗口及各对话框
│   ├── models/              # 命令数据模型
│   └── utils/               # 工具函数、常量、设置管理
├── resources/               # 图标、样式表、脚本资源
├── tools/                   # 存放 adb、scrcpy 等二进制工具
├── config.json              # 用户配置文件（自动生成）
└── requirements.txt         # Python 依赖列表

### 📝 配置文件说明
config.json 存储用户设置，包括：
1. 输出目录（默认为桌面）
2. 自定义命令列表
3. 快捷键映射
4. 命令列表显示过滤模式
首次运行时会自动生成。

### macOS 打包注意事项
1. 资源文件：main.spec 中resources/icons/app_icon.ico图标文件改为resources/icons/app_icon.icns

2. 打包模式：推荐使用 --onedir（目录模式），便于用户替换 adb 等工具；若使用 --onefile(单文件模式)，资源路径仍有效，但启动较慢,
   具体可把main.spec文件让AI改为目录模式。

3. 应用图标：macOS 需使用 .icns 格式，在 .spec 的 BUNDLE 段指定 icon='resources/icons/app_icon.icns'。

4. 二进制工具：将 macOS 版本的 adb 和 scrcpy 放入 tools/ 目录，打包后程序会通过 resource_path 自动查找。

   4.1 scrcpy的最新MAC版本下载链接（最好下载兼容性好的）：github.com/Genymobile/scrcpy/releases/latest
   4.2 抓包：mac本如果能识别tcpdump 命令，则不需要下载

5. 安全提示：首次运行打包后的 .app 可能被 macOS 安全策略阻止，可右键打开或前往“系统偏好设置 → 安全性与隐私”允许。

6. 打包命令：pyinstaller main.spec即可，无需多余参数

### 🤝 贡献与反馈
如有问题或建议，可通过邮箱 1573553463@qq.com 联系开发者。



