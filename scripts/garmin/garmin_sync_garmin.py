"""
Garmin 跨区同步脚本 (CN -> International)

从 Garmin 中国区 (garmin.cn) 下载活动，上传到 Garmin 国际区 (garmin.com)。

环境变量配置（通过 GitHub Secrets 注入）：
  源账号（CN 中国区）:
    GARMIN_EMAIL
    GARMIN_PASSWORD
    GARMIN_AUTH_DOMAIN (应设为 "CN")
  目标账号（INTL 国际区）:
    GARMIN_INTL_EMAIL
    GARMIN_INTL_PASSWORD
    GARMIN_INTL_AUTH_DOMAIN (可留空或设为 "COM")
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

import shutil


# 配置默认值：NEWEST_NUM=50（增量同步，每次最多处理 50 条）
SYNC_CONFIG = {
    # 源账号（CN 中国区）
    'GARMIN_EMAIL': '',
    'GARMIN_PASSWORD': '',
    'GARMIN_AUTH_DOMAIN': '',
    # 目标账号（INTL 国际区）
    'GARMIN_INTL_EMAIL': '',
    'GARMIN_INTL_PASSWORD': '',
    'GARMIN_INTL_AUTH_DOMAIN': '',
    # 通用
    'GARMIN_NEWEST_NUM': 50,
}

CROSS_REGION_DB_NAME = "cn_intl.db"


def ensure_dirs_exist():
    """确保需要的目录存在"""
    if not os.path.exists(DB_DIR):
        os.makedirs(DB_DIR)
    if not os.path.exists(GARMIN_FIT_DIR):
        os.makedirs(GARMIN_FIT_DIR)


def clear_garth_session():
    """
    清除 garth 全局单例的登录状态，以便切换账号登录。
    garth 是全局单例，GarminClient 的所有实例共享同一个登录态。
    跨区同步需要先登录 CN 下载，再切换登录 INTL 上传。
    """
    # 通过重新配置让 garth 清除旧 session
    # garth 没有显式的 logout API，但 configure 会重置 session
    if hasattr(garth, 'client') and garth.client:
        try:
            # 清除 cookies 和 token
            garth.client.garth_token = None
            garth.client.cookiejar = None
            garth.client.session = None
            garth.client.username = None
        except Exception:
            pass


def phase1_download_from_cn(source_client, cross_region_db):
    """Phase 1: 登录 Garmin CN，获取活动列表，下载未同步的 FIT 文件"""
    print("=" * 60)
    print("Phase 1: 从 Garmin 中国区获取活动...")
    print("=" * 60)

    all_activities = source_client.getAllActivities()
    if not all_activities or len(all_activities) == 0:
        print("未获取到任何活动，退出。")
        return None

    print(f"获取到 {len(all_activities)} 条活动，写入数据库...")
    for activity in all_activities:
        activity_id = activity["activityId"]
        cross_region_db.saveActivity(activity_id)

    # 获取未同步的活动列表
    un_sync_id_list = cross_region_db.getUnSyncActivity()
    if not un_sync_id_list or len(un_sync_id_list) == 0:
        print("没有需要同步的新活动，全部已同步完成。")
        return None

    print(f"需要同步的活动数量: {len(un_sync_id_list)}")

    # 下载 FIT 文件
    downloaded_files = []
    for activity_id in un_sync_id_list:
        try:
            print(f"  下载活动 {activity_id}...")
            file_data = source_client.downloadFitActivity(activity_id)
            file_path = os.path.join(GARMIN_FIT_DIR, f"{activity_id}.zip")
            with open(file_path, "wb") as fb:
                fb.write(file_data)
            downloaded_files.append({
                "activity_id": activity_id,
                "file_path": file_path,
            })
        except Exception as err:
            print(f"  下载活动 {activity_id} 失败: {err}")
            cross_region_db.updateExceptionSyncStatus(activity_id)

    print(f"Phase 1 完成，共下载 {len(downloaded_files)} 个 FIT 文件。")
    return downloaded_files


def phase2_upload_to_intl(downloaded_files, cross_region_db):
    """Phase 2: 登录 Garmin INTL，上传 FIT 文件"""
    print("=" * 60)
    print("Phase 2: 上传到 Garmin 国际区...")
    print("=" * 60)

    if not downloaded_files:
        print("没有需要上传的文件。")
        return

    intl_email = SYNC_CONFIG['GARMIN_INTL_EMAIL']
    intl_password = SYNC_CONFIG['GARMIN_INTL_PASSWORD']
    intl_auth_domain = SYNC_CONFIG.get('GARMIN_INTL_AUTH_DOMAIN', '')
    newest_num = int(SYNC_CONFIG.get('GARMIN_NEWEST_NUM', 50))

    # 清除 garth 的 CN 登录态，切换到 INTL
    print("切换登录到 Garmin 国际区...")
    clear_garth_session()

    # 创建 INTL 客户端（此时 @login 装饰器会触发登录）
    target_client = GarminClient(intl_email, intl_password, intl_auth_domain, newest_num)

    success_count = 0
    fail_count = 0

    for item in downloaded_files:
        activity_id = item["activity_id"]
        file_path = item["file_path"]

        if not os.path.exists(file_path):
            print(f"  文件不存在，跳过 {activity_id}: {file_path}")
            continue

        try:
            print(f"  上传活动 {activity_id}...")
            upload_status, upload_id = target_client.upload_activity(file_path)
            if upload_status == "SUCCESS":
                print(f"    ✅ 上传成功, upload_id={upload_id}")
                cross_region_db.updateSyncStatus(activity_id)
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
    """主流程"""
    # 从环境变量读取配置
    for k in SYNC_CONFIG:
        if os.getenv(k):
            v = os.getenv(k)
            SYNC_CONFIG[k] = v

    print("Garmin 跨区同步 (CN -> International)")
    print(f"源账号(CN):     {SYNC_CONFIG['GARMIN_EMAIL']}")
    print(f"目标账号(INTL): {SYNC_CONFIG['GARMIN_INTL_EMAIL']}")
    print(f"NEWEST_NUM:    {SYNC_CONFIG['GARMIN_NEWEST_NUM']}")
    print()

    # 检查必要配置
    if not SYNC_CONFIG['GARMIN_EMAIL'] or not SYNC_CONFIG['GARMIN_INTL_EMAIL']:
        print("错误: 源账号和目标账号均需配置！")
        print("请设置 GARMIN_EMAIL / GARMIN_PASSWORD (CN 源)")
        print("以及 GARMIN_INTL_EMAIL / GARMIN_INTL_PASSWORD (INTL 目标)")
        sys.exit(1)

    # 确保目录存在
    ensure_dirs_exist()

    # 初始化数据库
    db_name = CROSS_REGION_DB_NAME
    cross_region_db = GarminCrossRegionDB(db_name)
    cross_region_db.initDB()

    # ---- Phase 1: 从 CN 下载 ----
    cn_email = SYNC_CONFIG['GARMIN_EMAIL']
    cn_password = SYNC_CONFIG['GARMIN_PASSWORD']
    cn_auth_domain = SYNC_CONFIG.get('GARMIN_AUTH_DOMAIN', '')
    newest_num = int(SYNC_CONFIG.get('GARMIN_NEWEST_NUM', 50))

    source_client = GarminClient(cn_email, cn_password, cn_auth_domain, newest_num)
    downloaded_files = phase1_download_from_cn(source_client, cross_region_db)

    if not downloaded_files:
        print("没有需要同步的活动，退出。")
        return

    # ---- Phase 2: 上传到 INTL ----
    phase2_upload_to_intl(downloaded_files, cross_region_db)

    # 清理下载的临时文件
    if os.path.exists(GARMIN_FIT_DIR):
        shutil.rmtree(GARMIN_FIT_DIR)
        print(f"临时目录已清理: {GARMIN_FIT_DIR}")

    print("\n✅ 跨区同步完成！")


if __name__ == "__main__":
    main()
