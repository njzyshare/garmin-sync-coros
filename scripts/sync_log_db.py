import os
import json
from sqlite_db import SqliteDB
from config import SYNC_LOG_DB


class SyncTimeLogDB:
    """
    共享同步时间日志数据库 (sync_log.db)
    用于所有工作流之间的时间重叠防重查询。
    不通过 git commit，通过 GitHub Actions Artifact 在工作流之间传递。
    """

    def __init__(self, db_path=None):
        self._db_path = db_path or SYNC_LOG_DB
        self._ensure_dir()

    def _ensure_dir(self):
        db_dir = os.path.dirname(self._db_path)
        if not os.path.exists(db_dir):
            os.makedirs(db_dir)

    @property
    def db_path(self):
        return self._db_path

    def initDB(self):
        """初始化 sync_time_log 表"""
        with SqliteDB(self._db_path) as db:
            db.execute('''
            CREATE TABLE IF NOT EXISTS sync_time_log(
                id INTEGER NOT NULL PRIMARY KEY AUTOINCREMENT,
                activity_id TEXT NOT NULL,
                source_platform TEXT NOT NULL,
                start_time TEXT NOT NULL,
                end_time TEXT NOT NULL,
                sync_to TEXT NOT NULL,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            );
            ''')

    def has_time_overlap(self, source_platform, start_time, end_time):
        """
        检查指定来源平台是否有活动与给定的时间区间重叠。
        重叠条件：A.start < B.end AND A.end > B.start
        """
        with SqliteDB(self._db_path) as db:
            row = db.execute(
                'SELECT COUNT(*) FROM sync_time_log '
                'WHERE source_platform = ? '
                '  AND start_time < ? '
                '  AND end_time > ?',
                (source_platform, end_time, start_time)
            ).fetchone()
            count = row[0] if row else 0
            return count > 0

    def save_sync_log(self, activity_id, source_platform, start_time, end_time, sync_to):
        """记录一条同步记录"""
        with SqliteDB(self._db_path) as db:
            db.execute(
                'INSERT INTO sync_time_log (activity_id, source_platform, start_time, end_time, sync_to) '
                'VALUES (?, ?, ?, ?, ?)',
                (str(activity_id), source_platform, start_time, end_time, sync_to)
            )

    def clean_old_records(self, max_records_per_platform=50):
        """
        每个 source_platform 分组清理，只保留最近 N 条记录。
        不会误删其他工作流的记录。
        """
        with SqliteDB(self._db_path) as db:
            platforms = db.execute(
                'SELECT DISTINCT source_platform FROM sync_time_log'
            ).fetchall()
            for (platform,) in platforms:
                # 先统计该平台的总记录数
                row = db.execute(
                    'SELECT COUNT(*) FROM sync_time_log WHERE source_platform = ?',
                    (platform,)
                ).fetchone()
                count = row[0] if row else 0
                if count <= max_records_per_platform:
                    continue
                # 删除超出 limit 的最旧记录
                delete_count = count - max_records_per_platform
                db.execute(
                    'DELETE FROM sync_time_log WHERE id IN ('
                    '  SELECT id FROM sync_time_log '
                    '  WHERE source_platform = ? '
                    '  ORDER BY id ASC '
                    '  LIMIT ?'
                    ')',
                    (platform, delete_count)
                )

    def count_records(self):
        """统计总记录数"""
        with SqliteDB(self._db_path) as db:
            row = db.execute('SELECT COUNT(*) FROM sync_time_log').fetchone()
            return row[0] if row else 0
