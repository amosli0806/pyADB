# src/core/wireless_manager.py
import subprocess
import re
import sys
import socket
import time

from src.core.adb_client import AdbClient

class WirelessManager:
    def __init__(self, adb_manager):
        self.adb_manager = adb_manager

    def has_wireless_connected(self):
        for d in self.adb_manager.devices:
            if ':' in d['serial'] and d['status'] == 'device':
                return True
        return False

    def get_current_wireless_ip(self):
        for d in self.adb_manager.devices:
            if ':' in d['serial']:
                return d['serial'].split(':')[0]
        return None

    def _get_local_ip(self):
        """获取本机局域网 IP（非回环地址）"""
        try:
            s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            s.connect(("8.8.8.8", 80))
            ip = s.getsockname()[0]
            s.close()
            return ip
        except Exception:
            return None

    def _same_subnet(self, ip1, ip2):
        if not ip1 or not ip2:
            return False
        parts1 = ip1.split('.')
        parts2 = ip2.split('.')
        if len(parts1) != 4 or len(parts2) != 4:
            return False
        # return parts1[0] == parts2[0] and parts1[1] == parts2[1] and parts1[2] == parts2[2]
        return parts1 [ 0 ] == parts2 [ 0 ] and parts1 [ 1 ] == parts2 [ 1 ]

    def _ping_device(self, ip, timeout=3):
        """ping 设备 IP，隐藏 CMD 窗口"""
        param = '-n' if sys.platform == 'win32' else '-c'
        if sys.platform == 'win32':
            cmd = ['ping', param, '1', '-w', str(timeout * 1000), ip]
            creationflags = subprocess.CREATE_NO_WINDOW
        else:
            cmd = ['ping', param, '1', '-W', str(timeout), ip]
            creationflags = 0
        try:
            result = subprocess.run(cmd, capture_output=True, timeout=timeout+1, creationflags=creationflags)
            return result.returncode == 0
        except Exception:
            return False

    def get_device_ip(self, serial):
        """获取设备IP，优先从 WiFi 接口获取，兼容 Android 8-14"""
        try:
            # 常见 WiFi 接口名（含车机特殊名称）
            wifi_ifaces = ['wlan0', 'wlan1', 'wifi0', 'wlan2', 'eth0', 'eth1']

            # 方法1：尝试从指定接口直接获取 IP（使用 ip addr）
            adb = AdbClient(serial=serial)
            for iface in wifi_ifaces:
                result = adb.shell(f"ip addr show {iface}", timeout=5)
                if result.ok:
                    # 匹配 inet 地址，格式如 "inet 192.168.1.100/24"
                    match = re.search(r'inet (\d+\.\d+\.\d+\.\d+)/', result.stdout)
                    if match:
                        ip = match.group(1)
                        if not ip.startswith('127.'):
                            return ip

            # 方法2：扫描所有接口，使用 ip addr 或 ifconfig，优先提取 WiFi 接口的 IP
            commands = [
                "ip addr",  # 现代 Android 通用
                "ifconfig",  # 兼容旧版本
                "netcfg"  # 极旧版本
            ]
            all_ips = []
            wifi_ips = []
            for cmd_base in commands:
                result = adb.shell(cmd_base, timeout=5)
                if not result.ok:
                    continue
                output = result.stdout

                # 根据不同命令格式解析接口和 IP
                if cmd_base == "ip addr":
                    # 格式示例：
                    # 2: wlan0: <BROADCAST,MULTICAST,UP> ...
                    #     inet 192.168.1.100/24 ...
                    lines = output.splitlines()
                    current_iface = None
                    for line in lines:
                        if line.strip() and not line.startswith(' '):
                            # 接口行
                            iface_match = re.match(r'\d+:\s+(\S+):', line)
                            if iface_match:
                                current_iface = iface_match.group(1)
                        elif 'inet ' in line and current_iface:
                            ip_match = re.search(r'inet (\d+\.\d+\.\d+\.\d+)/', line)
                            if ip_match:
                                ip = ip_match.group(1)
                                if ip.startswith('127.'):
                                    continue
                                all_ips.append(ip)
                                if current_iface in wifi_ifaces:
                                    wifi_ips.append(ip)
                elif cmd_base == "ifconfig":
                    # 格式示例：
                    # wlan0: ip 192.168.1.100 mask ...
                    # 或 inet addr:192.168.1.100
                    blocks = output.split('\n\n')
                    for block in blocks:
                        # 提取接口名（行首）
                        iface_match = re.match(r'(\S+):', block)
                        if not iface_match:
                            continue
                        iface = iface_match.group(1)
                        ip_match = re.search(r'inet addr:(\d+\.\d+\.\d+\.\d+)', block) or re.search(
                            r'inet (\d+\.\d+\.\d+\.\d+)', block)
                        if ip_match:
                            ip = ip_match.group(1)
                            if ip.startswith('127.'):
                                continue
                            all_ips.append(ip)
                            if iface in wifi_ifaces:
                                wifi_ips.append(ip)
                elif cmd_base == "netcfg":
                    # 格式示例：wlan0    UP   192.168.1.100   0x...
                    for line in output.splitlines():
                        parts = line.split()
                        if len(parts) >= 3:
                            iface = parts[0]
                            ip = parts[2]
                            if ip.startswith('127.') or ip == '0.0.0.0':
                                continue
                            if re.match(r'\d+\.\d+\.\d+\.\d+', ip):
                                all_ips.append(ip)
                                if iface in wifi_ifaces:
                                    wifi_ips.append(ip)

            # 优先返回 WiFi 接口的 IP
            if wifi_ips:
                return wifi_ips[0]
            if not all_ips:
                return None

            # 没有明确 WiFi IP 时，使用原有优先级逻辑
            local_ip = self._get_local_ip()
            # 优先级1：同网段
            for ip in all_ips:
                if local_ip and self._same_subnet(local_ip, ip):
                    return ip
            # 优先级2：192.168.x.x
            for ip in all_ips:
                if ip.startswith('192.168.'):
                    return ip
            # 优先级3：10.x.x.x 或 172.16-31.x.x
            for ip in all_ips:
                if ip.startswith('10.'):
                    return ip
                if ip.startswith('172.'):
                    parts = ip.split('.')
                    if len(parts) >= 2 and 16 <= int(parts[1]) <= 31:
                        return ip
            # 优先级4：第一个
            return all_ips[0]

        except Exception as e:
            print(f"获取IP异常: {e}")
            return None

    def _run_adb_command(self, args, serial=None, timeout=10):
        """执行 adb 子命令（args 不含 adb 路径与 -s），返回 AdbResult"""
        return AdbClient(serial=serial).run(args, timeout=timeout)

    def connect(self, ip, port=5555, serial=None):
        # 1. 检查同网段
        local_ip = self._get_local_ip()
        if local_ip and not self._same_subnet(local_ip, ip):
            return False, f"电脑 IP ({local_ip}) 与设备 IP ({ip}) 不在同一局域网段，请检查网络连接。"

        # 2. ping 检测
        if not self._ping_device(ip):
            return False, f"无法 ping 通设备 IP ({ip})，请确认设备已连接到同一 Wi-Fi 且 IP 正确。"

        # 3. 如果有线设备序列号存在，执行 tcpip 切换
        if serial:
            check_args = ["shell", "getprop", "service.adb.tcp.port"]
            check_result = self._run_adb_command(check_args, serial=serial, timeout=5)
            if check_result.returncode == 0 and check_result.stdout.strip() == str(port):
                pass
            else:
                tcpip_result = self._run_adb_command(["tcpip", str(port)], serial=serial, timeout=10)
                if tcpip_result.returncode != 0:
                    return False, f"设置 TCP/IP 模式失败: {tcpip_result.stderr}"
                time.sleep(5)
                for _ in range(3):
                    check_result = self._run_adb_command(check_args, serial=serial, timeout=5)
                    if check_result.returncode == 0 and check_result.stdout.strip() == str(port):
                        break
                    time.sleep(2)
                else:
                    return False, "设备未能成功切换到 TCP/IP 模式，请确保设备已通过 USB 连接并授权。"

        # 4. 执行 connect
        connect_result = self._run_adb_command(["connect", f"{ip}:{port}"], timeout=30)
        output = connect_result.stdout + connect_result.stderr
        if "connected to" in output or "already connected" in output:
            return True, output.strip()
        else:
            if "unable to connect" in output:
                return False, "无法连接，请确认设备 IP 正确且设备已开启无线调试（Android 11+ 需在开发者选项中开启无线调试并配对）。"
            elif "Connection refused" in output:
                return False, "连接被拒绝，请确认设备已执行 tcpip 命令或已开启无线调试端口。"
            else:
                return False, output.strip()

    def disconnect(self, ip=None):
        try:
            args = ["disconnect", f"{ip}:5555"] if ip else ["disconnect"]
            result = self._run_adb_command(args, timeout=10)
            return True, result.stdout.strip()
        except Exception as e:
            return False, str(e)