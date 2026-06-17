import os
import sys 

CURRENT_DIR = os.path.split(os.path.abspath(__file__))[0]  # 当前目录
config_path = CURRENT_DIR.rsplit('/', 1)[0]  # 上三级目录
sys.path.append(config_path)

from config import DB_DIR, GARMIN_FIT_DIR
from garmin.garmin_client import GarminClient
from garmin.garmin_db import GarminDB, SOURCE_GARMIN, SOURCE_COROS
from coros.coros_client import CorosClient
from oss.ali_oss_client import AliOssClient
from oss.aws_oss_client import AwsOssClient
from utils.md5_utils import calculate_md5_file

# 配置默认值：NEWEST_NUM=100（历史数据已传过，只需增量）
SYNC_CONFIG = {
    'GARMIN_AUTH_DOMAIN': '',
    'GARMIN_EMAIL': '',
    'GARMIN_PASSWORD': '',
    'GARMIN_NEWEST_NUM': 100,
    "COROS_EMAIL": '',
    "COROS_PASSWORD": '',
}


def init(coros_db):
    ## 判断RQ数据库是否存在
    print(os.path.join(DB_DIR, coros_db.garmin_db_name))
    if not os.path.exists(os.path.join(DB_DIR, coros_db.garmin_db_name)):
        ## 初始化建表
        coros_db.initDB()
    if not os.path.exists(GARMIN_FIT_DIR):
        os.mkdir(GARMIN_FIT_DIR)

if __name__ == "__main__":

   # 首先读取 面板变量 或者 github action 运行变量
  for k in SYNC_CONFIG:
      if os.getenv(k):
          v = os.getenv(k)
          SYNC_CONFIG[k] = v
  
  ## db 名称
  db_name = "garmin.db"
  ## 建立DB链接
  garmin_db = GarminDB(db_name)
  ## 初始化DB位置和下载文件位置
  init(garmin_db)

  GARMIN_EMAIL = SYNC_CONFIG["GARMIN_EMAIL"]
  GARMIN_PASSWORD = SYNC_CONFIG["GARMIN_PASSWORD"]
  GARMIN_AUTH_DOMAIN = SYNC_CONFIG["GARMIN_AUTH_DOMAIN"]
  GARMIN_NEWEST_NUM = SYNC_CONFIG["GARMIN_NEWEST_NUM"]
    
  garminClient = GarminClient(GARMIN_EMAIL, GARMIN_PASSWORD, GARMIN_AUTH_DOMAIN, GARMIN_NEWEST_NUM)

  COROS_EMAIL = SYNC_CONFIG["COROS_EMAIL"]
  COROS_PASSWORD = SYNC_CONFIG["COROS_PASSWORD"]
  corosClient = CorosClient(COROS_EMAIL, COROS_PASSWORD)
  corosClient.login()
  all_activities = garminClient.getAllActivities()
  if all_activities == None or len(all_activities) == 0:
      exit()
  
  ## 记录活动到 garmin.db，区分来源
  ## 同时收集从高驰同步来的活动 ID，跳过不上传回高驰
  skip_source_coros_count = 0
  for activity in all_activities:
      activity_id = activity["activityId"]
      # 检查是否已经是高驰来源（之前通过 coros-sync-garmin 同步过来的）
      garmin_db.saveActivity(activity_id, SOURCE_GARMIN)

  

  ## 过滤掉 source=1（来自高驰同步）的活动，避免数据往返
  un_sync_id_list = garmin_db.getUnSyncActivity()
  if un_sync_id_list == None or len(un_sync_id_list) == 0:
      exit()

  ## 二次过滤：排除 source=1 的活动（高驰来源，不应传回高驰）
  filtered_id_list = []
  skipped_count = 0
  for activity_id in un_sync_id_list:
      src = garmin_db.getSource(activity_id)
      if src == SOURCE_COROS:
          print(f"  跳过活动 {activity_id}（来源：高驰同步至佳明），避免数据往返")
          garmin_db.updateSyncStatus(activity_id)  # 标记为已同步（跳过）
          skipped_count += 1
      else:
          filtered_id_list.append(activity_id)
  un_sync_id_list = filtered_id_list
  print(f"未同步活动中，{skipped_count} 条来自高驰已跳过，{len(un_sync_id_list)} 条待处理")

  if len(un_sync_id_list) == 0:
      print("没有需要同步到高驰的活动，退出。")
      exit()

  ## 下载未同步活动的FIT文件
  # 修复：下载失败标记异常状态，避免下次重复尝试
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

  ## 逐个上传到高驰
  # 修复：上传失败不exit()，继续处理下一个活动
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
          # 尝试从响应中提取高驰 labelId，建立映射关系
          label_id = None
          if isinstance(upload_result, dict):
              for key in ("labelId", "activityId", "id", "activity_id"):
                  if key in upload_result:
                      try:
                          label_id = int(upload_result[key])
                          break
                      except (ValueError, TypeError):
                          pass
          if label_id:
              try:
                  garmin_db.saveGarminCorosMapping(int(un_sync_id), label_id)
                  print(f"  已记录映射：佳明 {un_sync_id} ↔ 高驰 {label_id}")
              except Exception as e:
                  print(f"  保存映射失败（可忽略）: {e}")
          else:
              print(f"  ⚠️ 警告：无法从高驰响应中提取 labelId，双向防重机制可能失效")
      else:
          print(f"  活动 {un_sync_id}.zip 上传失败: {upload_result}")
          garmin_db.updateExceptionSyncStatus(un_sync_id)
          fail_count += 1
    except Exception as err:
      print(f"上传活动 {un_sync_id} 失败: {err}")
      garmin_db.updateExceptionSyncStatus(un_sync_id)
      fail_count += 1

  print(f"\n同步完成。成功: {success_count}, 失败: {fail_count}")

  ## 下载的临时文件清理
  if os.path.exists(GARMIN_FIT_DIR):
      import shutil
      shutil.rmtree(GARMIN_FIT_DIR)
      print(f"临时目录已清理: {GARMIN_FIT_DIR}")