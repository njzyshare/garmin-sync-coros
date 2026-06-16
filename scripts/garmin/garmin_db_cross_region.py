import os
from sqlite_db import SqliteDB
from config import DB_DIR


class GarminCrossRegionDB:
    """Garmin 跨区同步数据库 (CN -> International)"""

    def __init__(self, db_name):
        self._db_name = db_name

    @property
    def db_name(self):
        return self._db_name

    def saveActivity(self, activity_id):
        """保存活动ID（去重）"""
        exists_select_sql = 'SELECT * FROM garmin_cross_region_activity WHERE activity_id = ?'
        with SqliteDB(self._db_name) as db:
            exists_query_set = db.execute(exists_select_sql, (activity_id,)).fetchall()
            if len(exists_query_set) == 0:
                db.execute('insert into garmin_cross_region_activity (activity_id) values (?)', (activity_id,))

    def getUnSyncActivity(self):
        """获取未同步到国际区的活动ID列表"""
        select_sql = 'SELECT activity_id FROM garmin_cross_region_activity WHERE is_sync_intl = 0 limit 1000'
        with SqliteDB(self._db_name) as db:
            result = db.execute(select_sql).fetchall()
            if len(result) == 0:
                return None
            return [row[0] for row in result]

    def updateSyncStatus(self, activity_id):
        """标记已同步到国际区"""
        update_sql = "update garmin_cross_region_activity set is_sync_intl = 1 WHERE activity_id = ?"
        with SqliteDB(self._db_name) as db:
            db.execute(update_sql, (activity_id,))

    def updateExceptionSyncStatus(self, activity_id):
        """标记同步异常"""
        update_sql = "update garmin_cross_region_activity set is_sync_intl = 2 WHERE activity_id = ?"
        with SqliteDB(self._db_name) as db:
            db.execute(update_sql, (activity_id,))

    def initDB(self):
        """初始化数据库表"""
        with SqliteDB(self._db_name) as db:
            db.execute('''
                CREATE TABLE IF NOT EXISTS garmin_cross_region_activity(
                    id INTEGER NOT NULL PRIMARY KEY AUTOINCREMENT,
                    activity_id INTEGER NOT NULL,
                    is_sync_intl INTEGER NOT NULL DEFAULT 0,
                    create_time TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    update_time TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            ''')
