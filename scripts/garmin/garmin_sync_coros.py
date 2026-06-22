import os
import sys 

CURRENT_DIR = os.path.split(os.path.abspath(__file__))[0]  # 当前目录
config_path = CURRENT_DIR.rsplit('/', 1)[0]  # 上三级目录
sys.path.append(config_path)

from config import DB_DIR, GARMIN_FIT_DIR
from garmin.garmin_client import GarminClient
from garmin.garmin_db import GarminDB
from coros.coros_client import CorosClient
from oss.ali_oss_client import AliOssClient
from oss.aws_oss_client import AwsOssClient
from utils.md5_utils import calculate_md5_file
from sync_log_db import SyncTimeLogDB

# 配置默认值：NEWEST_NUM=50（增量同步，每次最多处理 50 条）
SYNC_CONFIG = {
    'GARMIN_AUTH_DOMAIN': '',
    'GARMIN_EMAIL': '',
    'GARMIN_PASSWORD': '',
    'GARMIN_NEWEST_NUM': 50,
    "COROS_EMAIL": '',
    "COROS_PASSWORD": '',
}


def init(garmin_db):
    """初始化 DB 和下载目录"""
    print(os.path.join(DB_DIR, garmin_db.garmin_db_name))
    garmin_db.initDB()
    if not os.path.exists(GARMIN_FIT_DIR):
        os.mkdir(GARMIN_FIT_DIR)


def get_activity_time(activity):
    """从佳明活动数据中提取起止时间（ISO UTC 格式）"""
    start_time = activity.get("startTimeGMT", "")
    end_time = activity.get("endTimeGMT", "")
    # 如果 startTimeGMT 不存在，退而求其次用 startTimeLocal
    if not start_time:
        start_time = activity.get("startTimeLocal", "")
        end_time = activity.get("endTimeLocal", "")
    return start_time, end_time


if __name__ == "__main__":

   # 首先读取 面板变量 或者 github action 运行变量
  for k in SYNC_CONFIG:
      if os.getenv(k):
          v = os.getenv(k)
          SYNC_CONFIG[k] = v
  
  ## db 名称（独立文件，避免被其他工作流覆盖）
  db_name = "garmin_coros.db"
  ## 建立DB链接
  garmin_db = GarminDB(db_name)
  ## 初始化DB位置和下载文件位置
  init(garmin_db)

  GARMIN_EMAIL = SYNC_CONFIG["GARMIN_EMAIL"]
  GARMIN_PASSWORD = SYNC_CONFIG["GARMIN_PASSWORD"]
  GARMIN_AUTH_DOMAIN = SYNC_CONFIG["GARMIN_AUTH_DOMAIN"]
  GARMIN_NEWEST_NUM = int(SYNC_CONFIG["GARMIN_NEWEST_NUM"])
    
  garminClient = GarminClient(GARMIN_EMAIL, GARMIN_PASSWORD, GARMIN_AUTH_DOMAIN, GARMIN_NEWEST_NUM)

  COROS_EMAIL = SYNC_CONFIG["COROS_EMAIL"]
  COROS_PASSWORD = SYNC_CONFIG["COROS_PASSWORD"]
  corosClient = CorosClient(COROS_EMAIL, COROS_PASSWORD)
  corosClient.login()
  all_activities = garminClient.getAllActivities()
  if all_activities == None or len(all_activities) == 0:
      exit()
  
  ## 初始化共享的时间日志（防重用）
  sync_log_db = SyncTimeLogDB()
  sync_log_db.initDB()

  ## 记录活动到 garmin_coros.db（去重）
  skip_time_overlap_count = 0
  for activity in all_activities:
      activity_id = activity["activityId"]
      # 先保存到 DB（去重）
      garmin_db.saveActivity(activity_id)

  ## 获取未同步活动
  un_sync_id_list = garmin_db.getUnSyncActivity()
  if un_sync_id_list == None or len(un_sync_id_list) == 0:
      exit()

  ## 时间重叠防重：查 sync_time_log 是否有高驰来源的活动与此时间段重叠
  ## 如果有，说明这个佳明活动之前已经同步到过高驰，跳过
  filtered_id_list = []
  skipped_count = 0
  for activity_id in un_sync_id_list:
      # 从已缓存的活动数据中找这条活动的时间
      activity_data = None
      for a in all_activities:
          if a["activityId"] == activity_id:
              activity_data = a
              break
      if activity_data is None:
          # 没找到时间信息，直接放行
          filtered_id_list.append(activity_id)
          continue

      start_time, end_time = get_activity_time(activity_data)
      if start_time and end_time:
          if sync_log_db.has_time_overlap('coros', start_time, end_time):
              print(f"  跳过活动 {activity_id}（{start_time}~{end_time}，高驰已有此时间段记录），避免数据往返")
              garmin_db.updateSyncStatus(activity_id)
              skipped_count += 1
              continue
      filtered_id_list.append(activity_id)

  un_sync_id_list = filtered_id_list
  print(f"未同步活动中，{skipped_count} 条因时间重叠跳过，{len(un_sync_id_list)} 条待处理")

  if len(un_sync_id_list) == 0:
      print("没有需要同步到高驰的活动，退出。")
      exit()

  ## 下载未同步活动的FIT文件
  file_path_list = []
  # 记录每条活动的起止时间，上传成功后写 sync_log
  activity_time_map = {}
  for un_sync_id in un_sync_id_list:
    try:
      # 找到时间信息
      for a in all_activities:
          if a["activityId"] == un_sync_id:
              activity_time_map[un_sync_id] = get_activity_time(a)
              break

      file = garminClient.downloadFitActivity(un_sync_id)
      file_path = os.path.join(GARMIN_FIT_DIR, f"{un_sync_id}.zip")
      with open(file_path, "wb") as fb:
          fb.write(file)

      un_sync_info = {
        "un_sync_id": un_sync_id,
        "file_path": file_path
      }

      file_path_list.append(un_sync_info)
      
    except Exception as err:
      print(f"下载活动 {un_sync_id} 失败: {err}")
      garmin_db.updateExceptionSyncStatus(un_sync_id)
  
  if len(file_path_list) == 0:
      print("没有成功下载的活动，退出。")
      exit()

  ## 逐个上传到高驰
  success_count = 0
  fail_count = 0
  for un_sync_info in file_path_list:
    try:
      client = None
      ## 中国区使用阿里云OSS
      if corosClient.regionId == 2:
         client = AliOssClient()
      elif corosClient.regionId == 1 or corosClient.regionId == 3:
         client = AwsOssClient()

      file_path = un_sync_info["file_path"]
      un_sync_id = un_sync_info["un_sync_id"]
      oss_obj = client.multipart_upload(file_path,  f"{corosClient.userId}/{calculate_md5_file(file_path)}.zip")
      size = os.path.getsize(file_path)
      upload_result = corosClient.uploadActivity(f"fit_zip/{corosClient.userId}/{calculate_md5_file(file_path)}.zip", calculate_md5_file(file_path), f"{un_sync_id}.zip", size)
      if upload_result:
          garmin_db.updateSyncStatus(un_sync_id)
          success_count += 1
          # 记录到 sync_time_log（时间重叠防重用）
          start_time, end_time = activity_time_map.get(un_sync_id, ("", ""))
          if start_time and end_time:
              try:
                  sync_log_db.save_sync_log(str(un_sync_id), 'garmin_cn', start_time, end_time, 'coros')
                  print(f"  已记录 sync_log：佳明 {un_sync_id} → 高驰 ({start_time}~{end_time})")
              except Exception as e:
                  print(f"  记录 sync_log 失败（可忽略）: {e}")
      else:
          print(f"  活动 {un_sync_id}.zip 上传失败: {upload_result}")
          garmin_db.updateExceptionSyncStatus(un_sync_id)
          fail_count += 1
    except Exception as err:
      print(f"上传活动 {un_sync_id} 失败: {err}")
      garmin_db.updateExceptionSyncStatus(un_sync_id)
      fail_count += 1

  print(f"\n同步完成。成功: {success_count}, 失败: {fail_count}")

  ## 清理 sync_log 旧记录（按 GARMIN_NEWEST_NUM 保留）
  max_records = max(GARMIN_NEWEST_NUM * 2, 50) if GARMIN_NEWEST_NUM > 0 else 100
  sync_log_db.clean_old_records(max_records_per_platform=max_records)

  ## 下载的临时文件清理
  if os.path.exists(GARMIN_FIT_DIR):
      import shutil
      shutil.rmtree(GARMIN_FIT_DIR)
      print(f"临时目录已清理: {GARMIN_FIT_DIR}")
