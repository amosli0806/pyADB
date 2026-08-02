import os
import sqlite3
from datetime import datetime
from pathlib import Path
from PIL import Image

class ScreenshotManager:
    """截图历史管理器，负责数据库操作和文件扫描"""

    def __init__(self, db_path=None):
        if db_path is None:
            db_path = Path.home() / ".capture_bug" / "screenshots.db"
        db_path.parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(str(db_path))
        self._init_db()
        self.thumbnail_size = (120, 120)

    def _init_db(self):
        self.conn.execute('''
            CREATE TABLE IF NOT EXISTS screenshots (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                device_serial TEXT NOT NULL,
                file_path TEXT UNIQUE NOT NULL,
                timestamp TEXT NOT NULL,
                thumbnail_path TEXT
            )
        ''')
        self.conn.commit()

    def scan_and_update(self, output_dir):
        """扫描输出目录，添加新截图到数据库"""
        output_path = Path(output_dir)
        if not output_path.exists():
            return 0

        # 获取所有截图文件
        new_count = 0
        for device_dir in output_path.iterdir():
            if not device_dir.is_dir():
                continue
            device_serial = device_dir.name
            for img_file in device_dir.glob("screenshot_*.png"):
                file_path = str(img_file.absolute())
                # 检查是否已存在
                cur = self.conn.execute("SELECT id FROM screenshots WHERE file_path=?", (file_path,))
                if cur.fetchone():
                    continue
                # 提取时间戳
                try:
                    timestamp_str = img_file.stem.replace("screenshot_", "")
                    # 格式如 20240101_120000
                    timestamp = datetime.strptime(timestamp_str, "%Y%m%d_%H%M%S").isoformat()
                except:
                    timestamp = datetime.now().isoformat()
                # 生成缩略图
                thumb_path = self._generate_thumbnail(file_path)
                self.conn.execute(
                    "INSERT INTO screenshots (device_serial, file_path, timestamp, thumbnail_path) VALUES (?,?,?,?)",
                    (device_serial, file_path, timestamp, thumb_path)
                )
                new_count += 1
        self.conn.commit()
        return new_count

    def _generate_thumbnail(self, img_path):
        """生成缩略图，返回缩略图路径"""
        try:
            img = Image.open(img_path)
            img.thumbnail(self.thumbnail_size, Image.Resampling.LANCZOS)
            thumb_dir = Path(img_path).parent / ".thumbnails"
            thumb_dir.mkdir(exist_ok=True)
            thumb_name = Path(img_path).stem + "_thumb.png"
            thumb_path = thumb_dir / thumb_name
            img.save(thumb_path, "PNG")
            return str(thumb_path.absolute())
        except Exception as e:
            print(f"缩略图生成失败: {e}")
            return ""

    def get_all_screenshots(self, device_serial=None):
        """获取截图列表，可按设备筛选，按时间倒序"""
        if device_serial:
            cur = self.conn.execute(
                "SELECT id, device_serial, file_path, timestamp, thumbnail_path FROM screenshots WHERE device_serial=? ORDER BY timestamp DESC",
                (device_serial,)
            )
        else:
            cur = self.conn.execute(
                "SELECT id, device_serial, file_path, timestamp, thumbnail_path FROM screenshots ORDER BY timestamp DESC"
            )
        return cur.fetchall()

    def delete_screenshot(self, screenshot_id, delete_file=True):
        """删除截图记录，可选择是否删除原文件及缩略图"""
        cur = self.conn.execute("SELECT file_path, thumbnail_path FROM screenshots WHERE id=?", (screenshot_id,))
        row = cur.fetchone()
        if not row:
            return False
        file_path, thumb_path = row
        if delete_file:
            try:
                if os.path.exists(file_path):
                    os.remove(file_path)
                if thumb_path and os.path.exists(thumb_path):
                    os.remove(thumb_path)
            except:
                pass
        self.conn.execute("DELETE FROM screenshots WHERE id=?", (screenshot_id,))
        self.conn.commit()
        return True

    def close(self):
        self.conn.close()