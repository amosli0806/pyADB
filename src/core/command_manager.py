# src/core/command_manager.py
"""
命令管理器模块
负责管理所有命令（包括预设命令和自定义命令）的加载、保存、增删改查，
以及搜索功能。命令数据存储在 JSON 配置文件中。
"""

import json        # 用于处理 JSON 数据的读写
import os          # 用于文件路径操作、检查文件是否存在等

# 导入自定义的命令模型和常量
from src.models.command import Command                # 命令数据模型
from src.utils.constants import PRESET_COMMANDS, CONFIG_FILE  # 预设命令列表、配置文件路径
from src.utils.adb_commands import ADB_COMMANDS       # 内置 ADB 命令库（供搜索使用）


class CommandManager:
    """
    命令管理器类，提供对命令的集中管理。
    包括从配置文件加载命令、保存命令、获取所有命令、添加/更新/删除命令，
    以及搜索命令的功能。
    """

    def __init__(self, config_path=None):
        """
        初始化命令管理器。
        :param config_path: 配置文件路径，如果不指定则使用默认的 CONFIG_FILE
        """
        self.config_path = config_path or CONFIG_FILE   # 配置文件路径
        self.commands = []                               # 存储所有命令对象的列表
        self.load()                                      # 初始化时自动加载命令

    def load(self):
        """
        加载命令数据。
        执行流程：
        1. 从 PRESET_COMMANDS 重建预设命令，并强制清除循环/间隔设置（预设命令不支持循环）。
        2. 从配置文件加载自定义命令，并合并到 commands 列表中。
        """
        # 预设命令的 ID 集合（根据 PRESET_COMMANDS 中定义的 id）
        PRESET_IDS = {1, 2, 3, 4}

        # 1. 从 PRESET_COMMANDS 重建预设命令
        # PRESET_COMMANDS 是一个字典列表，每个字典包含命令的属性（id, name, command_text, description 等）
        self.commands = [Command(**cmd) for cmd in PRESET_COMMANDS]   # 使用 Command 模型创建对象
        for cmd in self.commands:
            if cmd.is_preset:                # 确保预设命令的 is_preset 为 True（PRESET_COMMANDS 中应已设置）
                cmd.interval = None          # 预设命令不允许设置间隔
                cmd.loop_count = None         # 预设命令不允许设置循环次数

        # 2. 加载自定义命令（从配置文件）
        if os.path.exists(self.config_path):               # 检查配置文件是否存在
            with open(self.config_path, 'r', encoding='utf-8') as f:
                try:
                    data = json.load(f)                    # 尝试解析 JSON
                except json.JSONDecodeError:               # 如果 JSON 格式错误，则当作空数据
                    data = {}

            # 兼容旧格式：可能是字典（包含 'custom_commands' 键）或直接是列表
            if isinstance(data, dict) and 'custom_commands' in data:
                custom_list = data['custom_commands']       # 新格式：custom_commands 列表
            elif isinstance(data, list):
                custom_list = data                           # 旧格式：直接是命令列表
            else:
                custom_list = []                             # 其他情况置空

            # 遍历自定义命令数据
            for cmd_data in custom_list:
                # 跳过 ID 属于预设范围的条目（防止旧数据中的预设命令被当作自定义命令加载）
                if cmd_data.get('id') in PRESET_IDS:
                    continue
                # 跳过标记为预设的条目（防御性编程）
                if cmd_data.get('is_preset', False):
                    continue
                # 创建 Command 对象并添加到 commands 列表
                self.commands.append(Command(**cmd_data))

    def save(self):
        """
        保存自定义命令到配置文件。
        只保存非预设命令（自定义命令），预设命令不保存。
        配置文件格式为 JSON，包含 'custom_commands' 键，值为自定义命令列表。
        """
        # 如果配置文件已存在，先读取原有内容，保留其他可能的数据（例如未来可能添加的配置项）
        if os.path.exists(self.config_path):
            with open(self.config_path, 'r', encoding='utf-8') as f:
                try:
                    config = json.load(f)          # 加载原有配置
                except json.JSONDecodeError:
                    config = {}                     # 解析失败则重新创建
        else:
            config = {}                              # 文件不存在，创建新字典

        # 提取所有非预设命令（自定义命令），并转换为字典列表
        config['custom_commands'] = [cmd.to_dict() for cmd in self.commands if not cmd.is_preset]

        # 确保配置文件所在目录存在（如果 config_path 包含目录）
        os.makedirs(os.path.dirname(self.config_path), exist_ok=True)

        # 将配置写入文件，使用缩进和 ensure_ascii=False 以支持中文
        with open(self.config_path, 'w', encoding='utf-8') as f:
            json.dump(config, f, indent=2, ensure_ascii=False)

    def get_all_commands(self):
        """返回所有命令（包括预设和自定义）的列表"""
        return self.commands

    def add_command(self, command):
        """
        添加一个新的自定义命令。
        :param command: Command 对象（尚未设置 id）
        会自动分配一个新的唯一 ID（大于预设 ID），并将 is_preset 设为 False，然后保存。
        """
        # 获取当前所有命令的 ID 集合
        existing_ids = {c.id for c in self.commands}
        # 计算新 ID：取最大 ID + 1，如果没有现有 ID 则从 1 开始
        new_id = max(existing_ids) + 1 if existing_ids else 1
        # 如果新 ID 小于等于 4（预设命令 ID 范围），则从 5 开始（避免冲突）
        if new_id <= 4:
            new_id = 5
        command.id = new_id                # 设置新 ID
        command.is_preset = False           # 标记为自定义命令
        self.commands.append(command)       # 添加到列表
        self.save()                         # 保存到文件

    def update_command(self, cmd_id, updated_command):
        """
        更新指定 ID 的命令。
        :param cmd_id: 要更新的命令的 ID
        :param updated_command: 新的 Command 对象
        注意：会保留原有命令的 is_preset 属性不变（预设命令不能被修改为自定义等）
        """
        for i, cmd in enumerate(self.commands):
            if cmd.id == cmd_id:
                # 设置更新后的命令的 ID 和预设标志（保持原有预设属性不变）
                updated_command.id = cmd_id
                updated_command.is_preset = cmd.is_preset   # 保留原有的预设属性
                self.commands[i] = updated_command          # 替换命令
                self.save()                                  # 保存
                break

    def delete_command(self, cmd_id):
        """
        删除指定 ID 的命令。
        注意：预设命令不允许删除，因此只删除非预设命令。
        """
        # 列表推导式：保留所有 ID 不相同 或 是预设命令 的命令（即不删除预设命令）
        self.commands = [c for c in self.commands if c.id != cmd_id or c.is_preset]
        self.save()

    def search_adb_commands(self, keyword):
        """
        在内置 ADB 命令库中搜索。
        :param keyword: 搜索关键词
        :return: 匹配的命令列表，每个元素为字典（包含 command, category, description）
        """
        if not keyword:
            return []

        keyword_lower = keyword.lower()       # 转为小写以进行不区分大小写的搜索
        results = []

        # ADB_COMMANDS 是一个列表，每个元素是包含 'command', 'category', 'description' 的字典
        for cmd in ADB_COMMANDS:
            # 在命令文本、分类、描述中搜索关键词
            if (keyword_lower in cmd["command"].lower() or
                keyword_lower in cmd["category"].lower() or
                keyword_lower in cmd["description"].lower()):
                results.append(cmd.copy())    # 返回副本，避免外部修改原始数据

        return results

    def search_all(self, keyword):
        """
        综合搜索：同时在预设命令、自定义命令和内置 ADB 命令库中搜索。
        :param keyword: 搜索关键词
        :return: 字典，包含三个键：'preset'（预设命令列表）、'custom'（自定义命令列表）、'adb_library'（内置命令列表）
        """
        if not keyword:
            # 如果关键词为空，返回所有预设和自定义命令，但内置库为空列表
            return {
                "preset": self.get_preset_commands(),
                "custom": self.get_custom_commands(),
                "adb_library": []
            }

        keyword_lower = keyword.lower()

        # 1. 搜索预设命令（在名称和描述中搜索）
        preset_results = []
        for cmd in self.commands:
            if cmd.is_preset:
                if (keyword_lower in cmd.name.lower() or
                    keyword_lower in cmd.description.lower()):
                    preset_results.append(cmd)

        # 2. 搜索自定义命令（在名称、描述和命令文本中搜索）
        custom_results = []
        for cmd in self.commands:
            if not cmd.is_preset:
                if (keyword_lower in cmd.name.lower() or
                    keyword_lower in cmd.description.lower() or
                    keyword_lower in cmd.command_text.lower()):
                    custom_results.append(cmd)

        # 3. 搜索内置 ADB 命令库
        adb_results = self.search_adb_commands(keyword)

        return {
            "preset": preset_results,
            "custom": custom_results,
            "adb_library": adb_results
        }

    def get_preset_commands(self):
        """获取所有预设命令的列表"""
        return [cmd for cmd in self.commands if cmd.is_preset]

    def get_custom_commands(self):
        """获取所有自定义命令的列表"""
        return [cmd for cmd in self.commands if not cmd.is_preset]

    # 在 CommandManager 类中添加以下方法

    def export_custom_commands ( self , file_path ) :
        """
        导出所有自定义命令到 JSON 文件
        :param file_path: 目标文件路径（.json）
        """
        custom_cmds = [ cmd.to_dict ( ) for cmd in self.commands if not cmd.is_preset ]
        with open ( file_path , 'w' , encoding = 'utf-8' ) as f :
            json.dump ( custom_cmds , f , indent = 2 , ensure_ascii = False )

    def import_custom_commands ( self , file_path , merge=True ) :
        """
        从 JSON 文件导入自定义命令
        :param file_path: 源文件路径
        :param merge: True 表示合并（跳过已存在的ID），False 表示替换全部自定义命令
        :return: (成功数量, 失败原因列表)
        """
        try :
            with open ( file_path , 'r' , encoding = 'utf-8' ) as f :
                imported = json.load ( f )
            if not isinstance ( imported , list ) :
                return 0 , [ "文件格式错误：应为命令列表" ]

            existing_ids = {cmd.id for cmd in self.commands}
            # 如果 merge=False，先删除所有自定义命令
            if not merge :
                self.commands = [ cmd for cmd in self.commands if cmd.is_preset ]
                existing_ids = {cmd.id for cmd in self.commands}

            success_count = 0
            errors = [ ]
            for cmd_dict in imported :
                # 跳过预设标记（防止用户导入预设）
                if cmd_dict.get ( 'is_preset' , False ) :
                    continue
                # 如果ID已存在且为合并模式，跳过或生成新ID
                if cmd_dict [ 'id' ] in existing_ids :
                    if merge :
                        errors.append ( f"ID {cmd_dict [ 'id' ]} ({cmd_dict [ 'name' ]}) 已存在，跳过" )
                        continue
                    else :
                        # 生成新ID
                        new_id = max ( existing_ids ) + 1 if existing_ids else 1
                        if new_id <= 4 :
                            new_id = 5
                        cmd_dict [ 'id' ] = new_id
                cmd = Command ( **cmd_dict )
                cmd.is_preset = False
                self.commands.append ( cmd )
                existing_ids.add ( cmd.id )
                success_count += 1
            self.save ( )
            return success_count , errors
        except Exception as e :
            return 0 , [ str ( e ) ]