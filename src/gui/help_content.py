# src/gui/help_content.py
"""帮助中心的 HTML 内容。

从 main_window.on_help 中抽出：这段是一百多行静态文案，混在槽函数里
既让主窗口臃肿，也不方便单独调整措辞与排版。

注意：字符串内的缩进保持原样，不要顺手做 dedent —— 行首空白会被 HTML
折叠进文本流，实测会让若干行的起始位置偏移一像素宽度。
"""

HELP_HTML = '''\

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
        '''
