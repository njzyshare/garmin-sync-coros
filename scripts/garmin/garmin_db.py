import os
from sqlite_db import  SqliteDB
from config import DB_DIR


# 活动来源常量
SOURCE_GARMIN = 0   # 佳明原生（手表直接创建）
SOURCE_COROS = 1    # 从高驰同步过来的


class GarminDB:
    
    def __init__(self, garmin_db_name):
        ## Garmin数据库
        self._garmin_db_name = garmin_db_name

    @property
    def garmin_db_name(self):
        return self._garmin_db_name

     ## 保存活动，默认来源为佳明原生
    def saveActivity(self, id, source=SOURCE_GARMIN):
        """原子插入，已存在则不操作（依赖 activity_id UNIQUE 约束）"""
        with SqliteDB(self._garmin_db_name) as db:
            db.execute(
                'INSERT OR IGNORE INTO garmin_activity (activity_id, source) VALUES (?, ?)',
                (id, source)
            )
    
    def saveActivityIfNotExists(self, id, source=SOURCE_GARMIN):
        """与 saveActivity 相同，语义别名"""
        return self.saveActivity(id, source)
    
    def getUnSyncActivity(self):
        select_un_upload_sql = 'SELECT activity_id FROM garmin_activity WHERE is_sync_coros = 0 limit 100'
        with SqliteDB(self._garmin_db_name) as db:
            un_upload_result = db.execute(select_un_upload_sql).fetchall()
            query_size = len(un_upload_result)
            if query_size == 0:
                return None
            else:
                activity_id_list = []
                for result in un_upload_result:
                    activity_id_list.append(result[0])
                return activity_id_list
            
    def updateSyncStatus(self, activity_id:int):
        update_sql = "update garmin_activity set is_sync_coros = 1 WHERE activity_id = ?"
        with SqliteDB(self._garmin_db_name) as db:
          db.execute(update_sql, (activity_id,))
    
    def updateExceptionSyncStatus(self, activity_id:int):
        update_sql = "update garmin_activity set is_sync_coros = 2 WHERE activity_id = ?"
        with SqliteDB(self._garmin_db_name) as db:
          db.execute(update_sql, (activity_id,))

    def getSource(self, activity_id: int):
        """查询活动的来源标记"""
        select_sql = 'SELECT source FROM garmin_activity WHERE activity_id = ?'
        with SqliteDB(self._garmin_db_name) as db:
            row = db.execute(select_sql, (activity_id,)).fetchone()
            if row:
                return row[0]
        return None

    def saveCorosSourceActivity(self, activity_id: int):
        """记录由高驰同步到佳明的活动，标记 source=SOURCE_COROS
           使用 INSERT OR REPLACE 处理新增或更新两种情况。
        """
        with SqliteDB(self._garmin_db_name) as db:
            db.execute('''
                INSERT OR REPLACE INTO garmin_activity
                (activity_id, source, is_sync_coros)
                VALUES (?, ?, 1)
            ''', (activity_id, SOURCE_COROS))

    def saveGarminCorosMapping(self, garmin_activity_id: int, coros_label_id: int):
        """记录佳明活动 ID 与高驰 labelId 的映射关系。
           用于 coros_sync_garmin.py 判断高驰活动是否源自佳明。
        """
        with SqliteDB(self._garmin_db_name) as db:
            db.execute('''
                INSERT OR REPLACE INTO garmin_coros_mapping
                (garmin_activity_id, coros_label_id)
                VALUES (?, ?)
            ''', (garmin_activity_id, coros_label_id))

    def getCorosLabelIds(self):
        """返回所有已记录的高驰 labelId 集合，供 coros_sync_garmin.py 做去重判断。"""
        result = set()
        with SqliteDB(self._garmin_db_name) as db:
            rows = db.execute('SELECT coros_label_id FROM garmin_coros_mapping').fetchall()
            for row in rows:
                result.add(str(row[0]))
        return result

    def initDB(self):
      with SqliteDB(os.path.join(DB_DIR, self._garmin_db_name)) as db:
          db.execute('''
          CREATE TABLE IF NOT EXISTS garmin_activity(
              id INTEGER NOT NULL PRIMARY KEY  AUTOINCREMENT ,
              activity_id INTEGER NOT NULL UNIQUE , 
              source INTEGER NOT NULL  DEFAULT 0,
              is_sync_coros INTEGER NOT NULL  DEFAULT 0,
              create_time TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
              update_time TIMESTAMP DEFAULT CURRENT_TIMESTAMP
          );
          ''')
          db.execute('''
          CREATE TABLE IF NOT EXISTS garmin_coros_mapping(
              id INTEGER NOT NULL PRIMARY KEY  AUTOINCREMENT,
              garmin_activity_id INTEGER NOT NULL,
              coros_label_id INTEGER NOT NULL,
              create_time TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
              UNIQUE(garmin_activity_id, coros_label_id)
          );
          ''')