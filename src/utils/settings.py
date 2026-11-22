# src/utils/settings.py
"""
设置管理模块
提供 Settings 类，用于加载和保存应用程序的设置（如输出目录）。
设置存储在 JSON 配置文件中。
"""

import json          # 用于读写 JSON 数据
import os            # 用于文件和目录操作

# 导入常量：默认输出目录和配置文件路径
from src.utils.constants import DEFAULT_OUTPUT_DIR, CONFIG_FILE
from src.utils.logger import get_logger

logger = get_logger(__name__)


class Settings:
    """
    设置管理类，使用类方法操作配置文件。
    主要功能：
    - 加载设置
    - 保存设置
    - 获取/设置输出目录
    """

    # 配置文件路径，直接引用常量 CONFIG_FILE
    CONFIG_FILE = CONFIG_FILE
    DEFAULT_SHORTCUTS = {
        "refresh_devices" : "F5" ,
        "execute_selected" : "M" ,
        "clear_log" : "Delete" ,
        "search_commands": "Ctrl+F",
        "add_command": "Ctrl+N",
        "edit_command": "Ctrl+E",
        "delete_command": "Ctrl+D",
        "wireless": "Ctrl+W",
        "scrcpy": "Ctrl+P",
        "install_app": "Ctrl+I",
        "push_file": "Ctrl+U",
        "app_manager": "Ctrl+A",
        "device_info": "Ctrl+H",
        "hprof_dump": "Ctrl+J",
        "monkey": "Ctrl+M",
        "crash_log": "Ctrl+L",
        "anr_analyzer": "Ctrl+R",
        "md5_query": "Ctrl+Q",
        "weak_network": "Ctrl+Y",
        "export_commands": "Ctrl+Shift+E",
        "import_commands": "Ctrl+Shift+I",
        "help_center": "F1",
        "packet_capture" : "Ctrl+G" ,  # 新增：网络抓包
        "about": "",   # 无默认快捷键
    }

    @classmethod
    def get_shortcuts(cls):
        """获取快捷键映射，若未设置则返回默认值"""
        settings = cls.load()
        saved = settings.get('shortcuts', {})
        # 合并默认值（确保所有动作都有快捷键）
        result = cls.DEFAULT_SHORTCUTS.copy()   # 修改：使用 cls.DEFAULT_SHORTCUTS
        result.update(saved)
        return result

    @classmethod
    def set_shortcut(cls, action_name, key_sequence):
        """保存单个快捷键"""
        settings = cls.load()
        if 'shortcuts' not in settings:
            settings['shortcuts'] = {}
        settings['shortcuts'][action_name] = key_sequence
        cls.save(settings)

    @classmethod
    def reset_shortcuts(cls):
        """重置所有快捷键为默认值"""
        settings = cls.load()
        settings['shortcuts'] = cls.DEFAULT_SHORTCUTS.copy()   # 修改：使用 cls.DEFAULT_SHORTCUTS
        cls.save(settings)

    @classmethod
    def load(cls):
        """
        加载配置文件，返回包含所有设置的字典。
        如果配置文件不存在或损坏，返回空字典。
        :return: 设置字典
        """
        # 检查配置文件是否存在
        if os.path.exists(cls.CONFIG_FILE):
            try:
                # 以只读方式打开，UTF-8 编码
                with open(cls.CONFIG_FILE, 'r', encoding='utf-8') as f:
                    return json.load(f)          # 解析 JSON 为字典并返回
            except (OSError, ValueError) as exc:
                # JSON 损坏或被手工改错时会静默退回默认值：快捷键、输出目录全丢。
                # 用户只知道"设置又变回去了"，所以这里记一条 warning 便于定位。
                logger.warning("配置文件读取失败，使用默认设置: %s", exc, exc_info=True)
                return {}
        # 文件不存在也返回空字典
        return {}

    @classmethod
    def save(cls, settings_dict):
        """
        将设置字典保存到配置文件。
        :param settings_dict: 包含设置的字典
        """
        try:
            # 确保配置文件的目录存在（如果不存在则创建）
            os.makedirs(os.path.dirname(cls.CONFIG_FILE), exist_ok=True)
            # 以写入模式打开，UTF-8 编码
            with open(cls.CONFIG_FILE, 'w', encoding='utf-8') as f:
                # 将字典写入 JSON 文件，indent=2 使格式美观，ensure_ascii=False 允许中文
                json.dump(settings_dict, f, indent=2, ensure_ascii=False)
        except Exception as e:
            # 保存失败时打印错误信息（实际项目中可改用日志记录）
            print(f"保存配置失败: {e}")

    @classmethod
    def get_output_dir(cls):
        """
        获取输出目录路径。
        逻辑：
        1. 从设置中读取 'output_dir' 键的值，如果不存在则使用默认目录 DEFAULT_OUTPUT_DIR。
        2. 确保默认目录存在（创建它）。
        3. 如果保存的路径不存在（例如被用户删除），则回退到默认目录并更新配置。
        :return: 有效的输出目录路径（字符串）
        """
        # 加载当前设置
        settings = cls.load()
        # 获取输出目录，若未设置则使用默认值
        path = settings.get('output_dir', DEFAULT_OUTPUT_DIR)

        # 确保默认目录存在（如果默认路径不存在则创建）
        try:
            os.makedirs(DEFAULT_OUTPUT_DIR, exist_ok=True)
        except Exception as e:
            print(f"无法创建默认输出目录 {DEFAULT_OUTPUT_DIR}: {e}")

        # 检查从设置中获取的路径是否存在
        if not os.path.exists(path):
            # 如果路径不存在且不是默认目录，则回退到默认目录并保存更新后的配置
            if path != DEFAULT_OUTPUT_DIR:
                cls.set_output_dir(DEFAULT_OUTPUT_DIR)   # 将配置中的路径更新为默认目录
            return DEFAULT_OUTPUT_DIR

        # 路径存在，直接返回
        return path

    @classmethod
    def set_output_dir(cls, path):
        """
        设置输出目录并保存到配置文件。
        :param path: 新的输出目录路径（字符串）
        """
        # 加载当前设置
        settings = cls.load()
        # 更新 output_dir 键
        settings['output_dir'] = path
        # 保存整个设置字典
        cls.save(settings)

    @classmethod
    def save_display_command_ids(cls, ids):
        settings = cls.load()
        settings['display_command_ids'] = ids
        cls.save(settings)

    @classmethod
    def load_display_command_ids(cls):
        settings = cls.load()
        return settings.get('display_command_ids')

    @classmethod
    def save_display_filter_mode(cls, mode):
        """
        保存显示过滤模式
        :param mode: 'all', 'preset', 'custom' 或空字符串（表示由自定义ID决定）
        """
        settings = cls.load()
        settings['display_filter_mode'] = mode
        cls.save(settings)

    @classmethod
    def load_display_filter_mode(cls):
        """加载显示过滤模式，默认返回 'all'"""
        settings = cls.load()
        return settings.get('display_filter_mode', 'all')

    @classmethod
    def save_checked_commands(cls, ids):
        """保存勾选的命令 ID 列表"""
        settings = cls.load()
        settings['checked_commands'] = ids
        cls.save(settings)

    @classmethod
    def load_checked_commands(cls):
        """加载上次勾选的命令 ID 列表，若不存在则返回空列表"""
        settings = cls.load()
        return settings.get('checked_commands', [])

    @classmethod
    def get_auto_check_update(cls):
        settings = cls.load()
        return settings.get('auto_check_update', True)

    @classmethod
    def set_auto_check_update(cls, enabled):
        settings = cls.load()
        settings['auto_check_update'] = enabled
        cls.save(settings)