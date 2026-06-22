import os
import sys 

CURRENT_DIR = os.path.split(os.path.abspath(__file__))[0]  # 当前目录
config_path = CURRENT_DIR.rsplit('/', 1)[0]  # 上三级目录
sys.path.append(config_path)

from coros_client import CorosClient
from config  import DB_DIR, COROS_FIT_DIR
from coros_db import CorosDB
from garmin.garmin_client import GarminClient
from garmin.garmin_db import GarminDB
from sync_log_db import SyncTimeLogDB


SYNC_CONFIG = {
    'GARMIN_AUTH_DOMAIN': '',
    'GARMIN_EMAIL': '',
    'GARMIN_PASSWORD': '',
    'GARMIN_NEWEST_NUM': 50,
    "COROS_EMAIL": '',
    "COROS_PASSWORD": '',
}

def init(coros_db):
    print(os.path.join(DB_DIR, coros_db.coros_db_name))
    coros_db.initDB()  # 始终执行，CREATE TABLE IF NOT EXISTS 安全
    if not os.path.exists(COROS_FIT_DIR):
        os.mkdir(COROS_FIT_DIR)


def get_activity_time(activity):
    """从高驰活动数据中提取起止时间（ISO UTC 格式）
    
    高驰 API 返回的时间戳是 Unix 秒级时间戳（int），
    需要转换为 ISO 字符串以便与 sync_time_log 中的其他来源格式统一。
    startTime, endTime 都是 Unix 时间戳（秒）
    """
    start_ts = activity.get("startTime", 0)
    end_ts = activity.get("endTime", 0)
    if start_ts and end_ts:
        # 转换为 ISO 格式 UTC 字符串
        import datetime
        start_time = datetime.datetime.utcfromtimestamp(start_ts).strftime('%Y-%m-%dT%H:%M:%SZ')
        end_time = datetime.datetime.utcfromtimestamp(end_ts).strftime('%Y-%m-%dT%H:%M:%SZ')
        return start_time, end_time
    return "", ""


if __name__ == "__main__":
  # 首先读取 面板变量 或者 github action 运行变量
  for k in SYNC_CONFIG:
      if os.getenv(k):
          v = os.getenv(k)
          SYNC_CONFIG[k] = v

  COROS_EMAIL = SYNC_CONFIG["COROS_EMAIL"]
  COROS_PASSWORD = SYNC_CONFIG["COROS_PASSWORD"]
  corosClient = CorosClient(COROS_EMAIL, COROS_PASSWORD)
  corosClient.login()

  GARMIN_EMAIL = SYNC_CONFIG["GARMIN_EMAIL"]
  GARMIN_PASSWORD = SYNC_CONFIG["GARMIN_PASSWORD"]
  GARMIN_AUTH_DOMAIN = SYNC_CONFIG["GARMIN_AUTH_DOMAIN"]

  ## 读取 GARMIN_NEWEST_NUM 控制拉取数量
  ## 0 为全量，＞0 时每次够数停止
  GARMIN_NEWEST_NUM = int(SYNC_CONFIG.get('GARMIN_NEWEST_NUM', 50))
  COROS_NEWEST_NUM = GARMIN_NEWEST_NUM

  garminClient = GarminClient(GARMIN_EMAIL, GARMIN_PASSWORD, GARMIN_AUTH_DOMAIN, GARMIN_NEWEST_NUM)


  ## db 名称（独立文件）
  db_name = "coros_garmin.db"
  ## 建立DB链接
  coros_db = CorosDB(db_name)
  ## 初始化DB位置和下载文件位置
  init(coros_db)

  ## 初始化共享的时间日志（防重用）
  sync_log_db = SyncTimeLogDB()
  sync_log_db.initDB()

  ## 读取 sync_time_log 中记录的佳明来源活动的时间信息（用于时间重叠判断）
  ## 不需要单独的 garmin.db 了，统一由 sync_log_db 处理

  ## 通过 GARMIN_NEWEST_NUM 控制拉取上限：0 为全量，＞0 时每次够数停止
  max_count = COROS_NEWEST_NUM if COROS_NEWEST_NUM > 0 else 0
  all_activities = corosClient.getAllActivities(max_count=max_count)
  if all_activities == None or len(all_activities) == 0:
      exit()
  print(f"获取到高驰最近 {len(all_activities)} 条活动")

  for activity in all_activities:
      activity_id = activity["labelId"]
      sport_type = activity["sportType"]
      # 保存到 coros_garmin.db（去重）
      coros_db.saveActivity(activity_id, sport_type)

  un_sync_list = coros_db.getUnSyncActivity()
  if un_sync_list == None or len(un_sync_list) == 0:
      exit()

  ## 时间重叠防重：查 sync_time_log 是否有佳明来源的活动与此时间段重叠
  ## 如果有，说明这个高驰活动之前已经同步到过佳明，跳过
  filtered_list = []
  skipped_count = 0
  for un_sync in un_sync_list:
      activity_id = un_sync["id"]
      # 在高驰活动列表中查找对应活动的时间
      activity_data = None
      for a in all_activities:
          if str(a.get("labelId", "")) == str(activity_id):
              activity_data = a
              break
      if activity_data is None:
          filtered_list.append(un_sync)
          continue

      start_time, end_time = get_activity_time(activity_data)
      if start_time and end_time:
          # 检查是否有佳明来源（garmin_cn）的活动与此时间段重叠
          if sync_log_db.has_time_overlap('garmin_cn', start_time, end_time):
              print(f"  跳过活动 {activity_id}（{start_time}~{end_time}，佳明已有此时间段记录），避免数据往返")
              coros_db.updateSyncStatus(activity_id)
              skipped_count += 1
              continue
      filtered_list.append(un_sync)

  print(f"未同步活动中，{skipped_count} 条因时间重叠跳过，{len(filtered_list)} 条待处理")

  ## 逐个下载并上传到佳明，成功后记录 sync_log
  success_count = 0
  fail_count = 0
  for un_sync in filtered_list:
    try:
      id = un_sync["id"]
      sport_type = un_sync["sportType"]
      file = corosClient.downloadActivitie(id, sport_type)
      file_path = os.path.join(COROS_FIT_DIR, f"{id}.fit")
      with open(file_path, "wb") as fb:
          fb.write(file.data)
      upload_status, upload_id = garminClient.upload_activity(file_path)
      print(f"{id}.fit upload status {upload_status}, upload_id={upload_id}")
      if upload_status in ("SUCCESS", "DUPLICATE_ACTIVITY"):
        coros_db.updateSyncStatus(id)
        ## 上传成功，记录 sync_log
        if upload_status == "SUCCESS" and upload_id:
            # 从高驰活动数据中提取时间
            activity_data = None
            for a in all_activities:
                if str(a.get("labelId", "")) == str(id):
                    activity_data = a
                    break
            if activity_data:
                start_time, end_time = get_activity_time(activity_data)
                if start_time and end_time:
                    try:
                        sync_log_db.save_sync_log(str(upload_id), 'coros', start_time, end_time, 'garmin_cn')
                        print(f"  已记录 sync_log：高驰 {id} → 佳明 {upload_id} ({start_time}~{end_time})")
                    except Exception as e:
                        print(f"  记录 sync_log 失败（可忽略）: {e}")
        success_count += 1
      else:
        print(f"  {id}.fit 上传失败: {upload_status}")
        coros_db.updateExceptionSyncStatus(id)
        fail_count += 1
      
    except Exception as err:
      print(f"活动 {id} 同步异常: {err}")
      coros_db.updateExceptionSyncStatus(id)
      fail_count += 1

  print(f"\n同步完成。成功: {success_count}, 失败: {fail_count}, 跳过(时间重叠): {skipped_count}")

  ## 清理 sync_log 旧记录
  max_records = max(GARMIN_NEWEST_NUM * 2, 50) if GARMIN_NEWEST_NUM > 0 else 100
  sync_log_db.clean_old_records(max_records_per_platform=max_records)

  ## 清理临时文件
  if os.path.exists(COROS_FIT_DIR):
      import shutil
      shutil.rmtree(COROS_FIT_DIR)
      print(f"临时目录已清理: {COROS_FIT_DIR}")
