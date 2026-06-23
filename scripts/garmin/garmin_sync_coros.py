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
    start_time = activity.get("startTimeGMT", "") or activity.get("startTimeLocal", "")
    end_time = activity.get("endTimeGMT", "") or activity.get("endTimeLocal", "")
    return start_time, end_time


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

  # ========== 初始流程：DB 为空时首次初始化 ==========
  # 第一次运行时，garmin_coros.db 是空的。如果直接跑同步，会把佳明上
  # 所有活动都传回高驰（高驰上本来就有），产生重复数据。
  # 初始化逻辑：拉取双方活动列表，全部标记为已同步，不做上传。
  # 后续运行时这些活动不会再被处理。
  garmin_db_first_run = garmin_db.isFirstRun()
  if garmin_db_first_run:
      print("检测到首次运行，执行初始流程：记录双方现有活动，避免产生重复数据...")
      init_max = GARMIN_NEWEST_NUM if GARMIN_NEWEST_NUM > 0 else 50
      # 拉取佳明活动，全部标记为已同步
      init_garmin = garminClient.getAllActivities()
      if init_garmin:
          for act in init_garmin:
              garmin_db.saveActivity(act["activityId"])
              garmin_db.updateSyncStatus(act["activityId"])
          print(f"  已记录 {len(init_garmin)} 条佳明活动")
      # 拉取高驰活动（用高驰的 labelId 写入 garmin_activity 表标记已同步）
      # 注意：labelId 可能和佳明 activityId 冲突，但 garmin_activity 表
      # 只做去重用，不涉及跨平台比对，所以没问题
      corosClient.login()
      init_coros = corosClient.getAllActivities(max_count=init_max)
      if init_coros:
          for act in init_coros:
              garmin_db.saveActivity(act.get("labelId", 0))
              garmin_db.updateSyncStatus(act.get("labelId", 0))
          print(f"  已记录 {len(init_coros)} 条高驰活动")
      print("初始流程完成。后续运行将只同步新活动。")
      exit()

  # ========== 获取双方活动列表 ==========
  # 拉取佳明活动列表
  all_activities = garminClient.getAllActivities()
  if all_activities == None or len(all_activities) == 0:
      exit()

  # 拉取高驰活动列表（用于时间重叠比对，防回传）
  coros_max_count = GARMIN_NEWEST_NUM if GARMIN_NEWEST_NUM > 0 else 200
  coros_activities = corosClient.getAllActivities(max_count=coros_max_count)
  if coros_activities is None:
      print("⚠️ 获取高驰活动列表失败，将只依赖 is_sync 标记防重")
      coros_activities = []  # 标记为 空列表（API 失败），回退到 is_sync 防重
  elif len(coros_activities) == 0:
      print("高驰上暂无活动记录（列表为空），时间重叠比对跳过")
      coros_activities = []  # 高驰确实没有活动，也是空列表
  else:
      print(f"获取到高驰最近 {len(coros_activities)} 条活动（用于时间重叠参考）")

  # ========== 写入佳明活动到 DB ==========
  for activity in all_activities:
      activity_id = activity["activityId"]
      garmin_db.saveActivity(activity_id)

  # ========== 时间重叠防重 ==========
  # 对每个待同步的佳明活动，检查高驰侧是否有时间重叠的活动
  # 如果有，说明这条佳明活动在高驰上已经有对应的原生记录（或之前同步过去的），跳过
  un_sync_id_list = garmin_db.getUnSyncActivity()
  if un_sync_id_list == None or len(un_sync_id_list) == 0:
      exit()

  filtered_id_list = []
  skipped_count = 0

  for activity_id in un_sync_id_list:
      # 找到该活动的起止时间
      activity_data = None
      for a in all_activities:
          if a["activityId"] == activity_id:
              activity_data = a
              break
      if activity_data is None:
          filtered_id_list.append(activity_id)
          continue

      start_time, end_time = get_activity_time(activity_data)
      if start_time and end_time and coros_activities:
          # 比对高驰活动列表
          def get_coros_time(a):
              import datetime
              st = a.get("startTime", 0)
              et = a.get("endTime", 0)
              if st and et:
                  return (
                      datetime.datetime.fromtimestamp(st, tz=datetime.timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ'),
                      datetime.datetime.fromtimestamp(et, tz=datetime.timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')
                  )
              return ("", "")
          if has_time_overlap(start_time, end_time, coros_activities, get_coros_time):
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

  # ========== 下载 FIT 文件 ==========
  file_path_list = []
  for un_sync_id in un_sync_id_list:
    try:
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

  # ========== 上传到高驰 ==========
  success_count = 0
  fail_count = 0
  for un_sync_info in file_path_list:
    try:
      client = None
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
      else:
          print(f"  活动 {un_sync_id}.zip 上传失败: {upload_result}")
          garmin_db.updateExceptionSyncStatus(un_sync_id)
          fail_count += 1
    except Exception as err:
      print(f"上传活动 {un_sync_id} 失败: {err}")
      garmin_db.updateExceptionSyncStatus(un_sync_id)
      fail_count += 1

  print(f"\n同步完成。成功: {success_count}, 失败: {fail_count}")

  # ========== 清理临时文件 ==========
  if os.path.exists(GARMIN_FIT_DIR):
      import shutil
      shutil.rmtree(GARMIN_FIT_DIR)
      print(f"临时目录已清理: {GARMIN_FIT_DIR}")
