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


SYNC_CONFIG = {
    'GARMIN_AUTH_DOMAIN': '',
    'GARMIN_EMAIL': '',
    'GARMIN_PASSWORD': '',
    'GARMIN_NEWEST_NUM': 0,
    "COROS_EMAIL": '',
    "COROS_PASSWORD": '',
}

def init(coros_db):
    print(os.path.join(DB_DIR, coros_db.coros_db_name))
    coros_db.initDB()  # 始终执行，CREATE TABLE IF NOT EXISTS 安全
    if not os.path.exists(COROS_FIT_DIR):
        os.mkdir(COROS_FIT_DIR)


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
  GARMIN_NEWEST_NUM = SYNC_CONFIG["GARMIN_NEWEST_NUM"]

  garminClient = GarminClient(GARMIN_EMAIL, GARMIN_PASSWORD, GARMIN_AUTH_DOMAIN, GARMIN_NEWEST_NUM)


  ## db 名称
  db_name = "coros.db"
  ## 建立DB链接
  coros_db = CorosDB(db_name)
  ## 初始化DB位置和下载文件位置
  init(coros_db)

  ## 同时初始化 garmin.db（用于记录由高驰同步到佳明的活动来源）
  garmin_db = GarminDB("garmin.db")
  garmin_db.initDB()  # 始终执行，CREATE TABLE IF NOT EXISTS 安全
  print(f"已初始化 garmin.db: {os.path.join(DB_DIR, garmin_db.garmin_db_name)}")

  ## 读取 garmin_coros_mapping 中已记录的高驰 labelId 集合
  ## 用于判断高驰上的活动是否源自佳明同步
  garmin_activity_ids = garmin_db.getCorosLabelIds()

  ## 只取最近 100 条活动（通过 max_count 参数限制 API 分页）
  all_activities = corosClient.getAllActivities(max_count=100)
  if all_activities == None or len(all_activities) == 0:
      exit()
  print(f"获取到高驰最近 {len(all_activities)} 条活动")

  for activity in all_activities:
      activity_id = activity["labelId"]
      sport_type = activity["sportType"]
      # 保存到 coros.db（去重）
      coros_db.saveActivity(activity_id, sport_type)

  un_sync_list = coros_db.getUnSyncActivity()
  if un_sync_list == None or len(un_sync_list) == 0:
      exit()

  ## 跳过从佳明同步过来的活动：如果 activity_id 在 garmin.db 的已知记录中，说明
  ## 这个活动是之前从佳明同步到高驰的，不应该再传回佳明，避免数据往返。
  filtered_list = []
  skipped_count = 0
  for un_sync in un_sync_list:
      activity_id_str = str(un_sync["id"])
      if activity_id_str in garmin_activity_ids:
          print(f"  跳过活动 {activity_id_str}（来源：佳明同步至高驰），避免数据往返")
          coros_db.updateSyncStatus(un_sync["id"])  # 标记为已同步（跳过）
          skipped_count += 1
      else:
          filtered_list.append(un_sync)

  print(f"未同步活动中，{skipped_count} 条来自佳明同步已跳过，{len(filtered_list)} 条待处理")

  ## 逐个下载并上传到佳明，成功后标记来源避免数据往返
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
        ## 上传成功且获得新 activity_id，标记来源为高驰
        if upload_status == "SUCCESS" and upload_id:
            garmin_db.saveCorosSourceActivity(int(upload_id))
            print(f"  已标记佳明活动 {upload_id} 来源为高驰同步（避免数据往返）")
        success_count += 1
      else:
        print(f"  {id}.fit 上传失败: {upload_status}")
        coros_db.updateExceptionSyncStatus(id)
        fail_count += 1
      
    except Exception as err:
      print(f"活动 {id} 同步异常: {err}")
      coros_db.updateExceptionSyncStatus(id)
      fail_count += 1

  print(f"\n同步完成。成功: {success_count}, 失败: {fail_count}, 跳过(佳明来源): {skipped_count}")

  ## 清理临时文件
  if os.path.exists(COROS_FIT_DIR):
      import shutil
      shutil.rmtree(COROS_FIT_DIR)
      print(f"临时目录已清理: {COROS_FIT_DIR}")
