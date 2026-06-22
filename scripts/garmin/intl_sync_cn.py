"""
Garmin 跨区同步脚本 (International -> CN)

从 Garmin 国际区 (garmin.com) 下载活动，上传到 Garmin 中国区 (garmin.cn)。
与 garmin_sync_garmin.py (CN -> INTL) 方向相反，结构对称。

环境变量配置（通过 GitHub Secrets 注入）：
  源账号（INTL 国际区）:
    GARMIN_INTL_EMAIL
    GARMIN_INTL_PASSWORD
    GARMIN_INTL_AUTH_DOMAIN (可留空或设为 "COM")
  目标账号（CN 中国区）:
    GARMIN_EMAIL
    GARMIN_PASSWORD
    GARMIN_AUTH_DOMAIN (应设为 "CN")
  通用:
    GARMIN_NEWEST_NUM (每次拉取的活动上限，0为全量)
"""

import os
import sys

CURRENT_DIR = os.path.split(os.path.abspath(__file__))[0]
config_path = CURRENT_DIR.rsplit('/', 1)[0]
sys.path.append(config_path)

import garth

from config import DB_DIR, GARMIN_FIT_DIR
from garmin.garmin_client import GarminClient
from garmin.garmin_db_cross_region import GarminCrossRegionDB
from sync_log_db import SyncTimeLogDB

import shutil


# 配置默认值：NEWEST_NUM=50
SYNC_CONFIG = {
    # 源账号（INTL 国际区）
    'GARMIN_INTL_EMAIL': '',
    'GARMIN_INTL_PASSWORD': '',
    'GARMIN_INTL_AUTH_DOMAIN': '',
    # 目标账号（CN 中国区）
    'GARMIN_EMAIL': '',
    'GARMIN_PASSWORD': '',
    'GARMIN_AUTH_DOMAIN': '',
    # 通用
    'GARMIN_NEWEST_NUM': 50,
}

CROSS_REGION_DB_NAME = "intl_cn.db"


def ensure_dirs_exist():
    if not os.path.exists(DB_DIR):
        os.makedirs(DB_DIR)
    if not os.path.exists(GARMIN_FIT_DIR):
        os.makedirs(GARMIN_FIT_DIR)


def clear_garth_session():
    if hasattr(garth, 'client') and garth.client:
        try:
            garth.client.garth_token = None
            garth.client.cookiejar = None
            garth.client.session = None
            garth.client.username = None
        except Exception:
            pass


def get_activity_time(activity):
    """从佳明活动数据中提取起止时间（ISO UTC 格式）"""
    start_time = activity.get("startTimeGMT", "") or activity.get("startTimeLocal", "")
    end_time = activity.get("endTimeGMT", "") or activity.get("endTimeLocal", "")
    return start_time, end_time


def phase1_download_from_intl(source_client, cross_region_db, sync_log_db):
    """Phase 1: 登录 Garmin INTL，获取活动列表，下载未同步的 FIT 文件"""
    print("=" * 60)
    print("Phase 1: 从 Garmin 国际区获取活动...")
    print("=" * 60)

    all_activities = source_client.getAllActivities()
    if not all_activities or len(all_activities) == 0:
        print("未获取到任何活动，退出。")
        return None, {}

    print(f"获取到 {len(all_activities)} 条活动，写入数据库...")
    activity_time_map = {}
    for activity in all_activities:
        activity_id = activity["activityId"]
        cross_region_db.saveActivity(activity_id)
        start_time, end_time = get_activity_time(activity)
        if start_time and end_time:
            activity_time_map[activity_id] = (start_time, end_time)

    # 时间重叠防重
    un_sync_id_list = cross_region_db.getUnSyncActivity()
    if not un_sync_id_list or len(un_sync_id_list) == 0:
        print("没有需要同步的新活动。")
        return None, {}

    print(f"需要同步的活动数量: {len(un_sync_id_list)}")

    # 时间重叠过滤：跳过已在 CN 端有记录的活动
    filtered_list = []
    skipped_count = 0
    for activity_id in un_sync_id_list:
        if activity_id in activity_time_map:
            start_time, end_time = activity_time_map[activity_id]
            if sync_log_db.has_time_overlap('garmin_cn', start_time, end_time):
                print(f"  跳过活动 {activity_id}（{start_time}~{end_time}，CN 已有此时间段记录），避免数据往返")
                cross_region_db.updateSyncStatus(activity_id)
                skipped_count += 1
                continue
        filtered_list.append(activity_id)
    un_sync_id_list = filtered_list
    print(f"{skipped_count} 条因时间重叠跳过，{len(un_sync_id_list)} 条待处理")

    if len(un_sync_id_list) == 0:
        return None, {}

    # 下载 FIT 文件
    downloaded_files = []
    for activity_id in un_sync_id_list:
        try:
            print(f"  下载活动 {activity_id}...")
            file_data = source_client.downloadFitActivity(activity_id)
            file_path = os.path.join(GARMIN_FIT_DIR, f"{activity_id}.zip")
            with open(file_path, "wb") as fb:
                fb.write(file_data)
            downloaded_files.append({"activity_id": activity_id, "file_path": file_path})
        except Exception as err:
            print(f"  下载活动 {activity_id} 失败: {err}")
            cross_region_db.updateExceptionSyncStatus(activity_id)

    print(f"Phase 1 完成，共下载 {len(downloaded_files)} 个 FIT 文件。")
    return downloaded_files, activity_time_map


def phase2_upload_to_cn(downloaded_files, cross_region_db, sync_log_db, activity_time_map):
    """Phase 2: 登录 Garmin CN，上传 FIT 文件"""
    print("=" * 60)
    print("Phase 2: 上传到 Garmin 中国区...")
    print("=" * 60)

    if not downloaded_files:
        print("没有需要上传的文件。")
        return

    cn_email = SYNC_CONFIG['GARMIN_EMAIL']
    cn_password = SYNC_CONFIG['GARMIN_PASSWORD']
    cn_auth_domain = SYNC_CONFIG.get('GARMIN_AUTH_DOMAIN', '')
    newest_num = int(SYNC_CONFIG.get('GARMIN_NEWEST_NUM', 50))

    print("切换登录到 Garmin 中国区...")
    clear_garth_session()

    target_client = GarminClient(cn_email, cn_password, cn_auth_domain, newest_num)

    success_count = 0
    fail_count = 0

    for item in downloaded_files:
        activity_id = item["activity_id"]
        file_path = item["file_path"]

        if not os.path.exists(file_path):
            print(f"  文件不存在，跳过 {activity_id}")
            continue

        try:
            print(f"  上传活动 {activity_id}...")
            upload_status, upload_id = target_client.upload_activity(file_path)
            if upload_status == "SUCCESS":
                print(f"    ✅ 上传成功, upload_id={upload_id}")
                cross_region_db.updateSyncStatus(activity_id)
                if activity_id in activity_time_map:
                    start_time, end_time = activity_time_map[activity_id]
                    try:
                        sync_log_db.save_sync_log(str(activity_id), 'garmin_intl', start_time, end_time, 'garmin_cn')
                        print(f"    📝 已记录 sync_log：INTL {activity_id} → CN ({start_time}~{end_time})")
                    except Exception as e:
                        print(f"    记录 sync_log 失败（可忽略）: {e}")
                success_count += 1
            elif upload_status == "DUPLICATE_ACTIVITY":
                print(f"    ⏭️  重复活动，标记为已同步")
                cross_region_db.updateSyncStatus(activity_id)
                success_count += 1
            else:
                print(f"    ❌ 上传失败: {upload_status}")
                cross_region_db.updateExceptionSyncStatus(activity_id)
                fail_count += 1
        except Exception as err:
            print(f"    ❌ 上传异常: {err}")
            cross_region_db.updateExceptionSyncStatus(activity_id)
            fail_count += 1

    print(f"\nPhase 2 完成。成功: {success_count}, 失败: {fail_count}")


def main():
    for k in SYNC_CONFIG:
        if os.getenv(k):
            v = os.getenv(k)
            SYNC_CONFIG[k] = v

    print("Garmin 跨区同步 (International -> CN)")
    print(f"源账号(INTL): {SYNC_CONFIG['GARMIN_INTL_EMAIL']}")
    print(f"目标账号(CN):  {SYNC_CONFIG['GARMIN_EMAIL']}")
    print(f"NEWEST_NUM:    {SYNC_CONFIG['GARMIN_NEWEST_NUM']}")
    print()

    if not SYNC_CONFIG['GARMIN_INTL_EMAIL'] or not SYNC_CONFIG['GARMIN_EMAIL']:
        print("错误: 源账号和目标账号均需配置！")
        sys.exit(1)

    ensure_dirs_exist()

    db_name = CROSS_REGION_DB_NAME
    cross_region_db = GarminCrossRegionDB(db_name)
    cross_region_db.initDB()

    sync_log_db = SyncTimeLogDB()
    sync_log_db.initDB()

    # Phase 1: 从 INTL 下载
    intl_email = SYNC_CONFIG['GARMIN_INTL_EMAIL']
    intl_password = SYNC_CONFIG['GARMIN_INTL_PASSWORD']
    intl_auth_domain = SYNC_CONFIG.get('GARMIN_INTL_AUTH_DOMAIN', '')
    newest_num = int(SYNC_CONFIG.get('GARMIN_NEWEST_NUM', 50))

    source_client = GarminClient(intl_email, intl_password, intl_auth_domain, newest_num)
    downloaded_files, activity_time_map = phase1_download_from_intl(source_client, cross_region_db, sync_log_db)

    if not downloaded_files:
        print("没有需要同步的活动，退出。")
        return

    # Phase 2: 上传到 CN
    phase2_upload_to_cn(downloaded_files, cross_region_db, sync_log_db, activity_time_map)

    # 清理 sync_log 旧记录
    max_records = max(newest_num * 2, 50) if newest_num > 0 else 100
    sync_log_db.clean_old_records(max_records_per_platform=max_records)

    if os.path.exists(GARMIN_FIT_DIR):
        shutil.rmtree(GARMIN_FIT_DIR)
        print(f"临时目录已清理: {GARMIN_FIT_DIR}")

    print("\n✅ 跨区同步完成！")


if __name__ == "__main__":
    main()
