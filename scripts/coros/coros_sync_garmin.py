import os
import sys 
import datetime

CURRENT_DIR = os.path.split(os.path.abspath(__file__))[0]  # 当前目录
config_path = CURRENT_DIR.rsplit('/', 1)[0]  # 上三级目录
sys.path.append(config_path)

from coros_client import CorosClient
from config  import DB_DIR, COROS_FIT_DIR
from coros_db import CorosDB
from garmin.garmin_client import GarminClient
from garmin.garmin_db import GarminDB


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
    coros_db.initDB()
    if not os.path.exists(COROS_FIT_DIR):
        os.mkdir(COROS_FIT_DIR)


def get_coros_time(activity):
    """从高驰活动数据中提取起止时间（ISO UTC 格式）"""
    st = activity.get("startTime", 0)
    et = activity.get("endTime", 0)
    if st and et:
        return (
            datetime.datetime.fromtimestamp(st, tz=datetime.timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ'),
            datetime.datetime.fromtimestamp(et, tz=datetime.timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')
        )
    return "", ""


def get_garmin_time(activity):
    """从佳明活动数据中提取起止时间（ISO UTC 格式）"""
    start = activity.get("startTimeGMT", "") or activity.get("startTimeLocal", "")
    end = activity.get("endTimeGMT", "") or activity.get("endTimeLocal", "")
    return start, end


def has_time_overlap(target_start, target_end, reference_list, get_start_end_func):
    """
    检查目标时间段是否与参考活动列表中的任何活动有时间重叠。
    重叠条件：A.start < B.end AND A.end > B.start
    """
    for ref in reference_list:
        ref_start, ref_end = get_start_end_func(ref)
        if ref_start and ref_end:
            if ref_start < target_end and ref_end > target_start:
                return True
    return False


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

  GARMIN_NEWEST_NUM = int(SYNC_CONFIG.get('GARMIN_NEWEST_NUM', 50))
  COROS_NEWEST_NUM = GARMIN_NEWEST_NUM

  garminClient = GarminClient(GARMIN_EMAIL, GARMIN_PASSWORD, GARMIN_AUTH_DOMAIN, GARMIN_NEWEST_NUM)

  ## db 名称（独立文件）
  db_name = "coros_garmin.db"
  coros_db = CorosDB(db_name)
  init(coros_db)

  # ========== 获取双方活动列表 ==========
  # 拉取高驰活动列表
  max_count = COROS_NEWEST_NUM if COROS_NEWEST_NUM > 0 else 0
  all_activities = corosClient.getAllActivities(max_count=max_count)
  if all_activities == None or len(all_activities) == 0:
      exit()
  print(f"获取到高驰最近 {len(all_activities)} 条活动")

  # 拉取佳明活动列表（用于时间重叠比对，防回传）
  garmin_max_num = GARMIN_NEWEST_NUM if GARMIN_NEWEST_NUM > 0 else 200
  garmin_activities = garminClient.getAllActivities()
  garmin_activities = garmin_activities[:garmin_max_num] if garmin_activities else []
  if garmin_activities is None:
      print("⚠️ 获取佳明活动列表失败，将只依赖 is_sync 标记防重")
      garmin_activities = []
  elif len(garmin_activities) == 0:
      print("佳明上暂无活动记录（列表为空），时间重叠比对跳过")
      garmin_activities = []
  else:
      print(f"获取到佳明最近 {len(garmin_activities)} 条活动（用于时间重叠参考）")

  # ========== 写入高驰活动到 DB ==========
  for activity in all_activities:
      activity_id = activity["labelId"]
      sport_type = activity["sportType"]
      coros_db.saveActivity(activity_id, sport_type)

  # ========== 时间重叠防重 ==========
  un_sync_list = coros_db.getUnSyncActivity()
  if un_sync_list == None or len(un_sync_list) == 0:
      exit()

  filtered_list = []
  skipped_count = 0
  for un_sync in un_sync_list:
      activity_id = un_sync["id"]
      # 找到该高驰活动的起止时间
      activity_data = None
      for a in all_activities:
          if str(a.get("labelId", "")) == str(activity_id):
              activity_data = a
              break
      if activity_data is None:
          filtered_list.append(un_sync)
          continue

      start_time, end_time = get_coros_time(activity_data)
      if start_time and end_time and garmin_activities:
          # 比对佳明活动列表
          if has_time_overlap(start_time, end_time, garmin_activities, get_garmin_time):
              print(f"  跳过活动 {activity_id}（{start_time}~{end_time}，佳明已有此时间段记录），避免数据往返")
              coros_db.updateSyncStatus(activity_id)
              skipped_count += 1
              continue
      filtered_list.append(un_sync)

  print(f"未同步活动中，{skipped_count} 条因时间重叠跳过，{len(filtered_list)} 条待处理")

  # ========== 逐个下载并上传到佳明 ==========
  success_count = 0
  fail_count = 0
  for un_sync in filtered_list:
    try:
      activity_id = un_sync["id"]
      sport_type = un_sync["sportType"]
      file = corosClient.downloadActivitie(activity_id, sport_type)
      file_path = os.path.join(COROS_FIT_DIR, f"{activity_id}.fit")
      with open(file_path, "wb") as fb:
          fb.write(file.data)
      upload_status, upload_id = garminClient.upload_activity(file_path)
      print(f"{activity_id}.fit upload status {upload_status}, upload_id={upload_id}")
      if upload_status in ("SUCCESS", "DUPLICATE_ACTIVITY"):
        coros_db.updateSyncStatus(activity_id)
        success_count += 1
      else:
        print(f"  {activity_id}.fit 上传失败: {upload_status}")
        coros_db.updateExceptionSyncStatus(activity_id)
        fail_count += 1
      
    except Exception as err:
      print(f"活动 {activity_id} 同步异常: {err}")
      coros_db.updateExceptionSyncStatus(activity_id)
      fail_count += 1

  print(f"\n同步完成。成功: {success_count}, 失败: {fail_count}, 跳过(时间重叠): {skipped_count}")

  # ========== 清理临时文件 ==========
  if os.path.exists(COROS_FIT_DIR):
      import shutil
      shutil.rmtree(COROS_FIT_DIR)
      print(f"临时目录已清理: {COROS_FIT_DIR}")
