# 如果出现无法同步请检查下代码是否最新如果非最新请重新fork sync代码后删除db/目录下文件重跑一遍！！！

## 关于本仓库

本仓库（原名 `garmin-sync-coros`，已更名为 `garmin-coros-sync-plus`）基于 [XiaoSiHwang/garmin-sync-coros](https://github.com/XiaoSiHwang/garmin-sync-coros) 进行二次开发，在原项目「佳明同步到高驰」基础上做了大量扩展，包含以下改动：

| # | 改动内容 | 说明 |
|---|---------|------|
| 1 | **Python 版本升级** | 从 Python 3.10 升级到 3.13，升级 Actions 版本，修复 set-output 废弃语法 |
| 2 | **新增「高驰 → 佳明」反向同步** | 新增 `coros-sync-garmin` 工作流 |
| 3 | **新增「佳明中国区 ⇆ 国际区」双向跨区同步** | 新增 CN→INTL 和 INTL→CN 两个工作流 |
| 4 | **工作流全局同步数量灵活切换** | 所有工作流都遵守 `GARMIN_NEWEST_NUM`，0 为全量，＞0 每次够数停止 |
| 5 | **sync_time_log 时间重叠防重机制** | 废弃旧的 ID 映射防重，改用运动起止时间重叠判断，物理隔离 DB 文件避免覆盖 |
| 6 | **各工作流 DB 文件独立** | 每个工作流使用自己的 `.db` 文件，git push 互不覆盖 |
| 7 | **异常处理优化** | 下载失败标记异常、上传失败不 exit 继续处理下一个、同步后清理临时文件 |
| 8 | **依赖精简与升级** | 从 56 个固定版本精简为 23 个最小直接依赖 |
| 9 | **修复多项 bug** | 缓存步骤缺失、initDB 多语句、upload_activity 返回值比较错误、参数名误导等 |

## 双向防数据往返机制

当同时开启多个工作流时，系统使用 **sync_time_log 时间重叠防重** 机制，避免数据在平台间死循环。

### 核心原理

每次同步成功后会记录源平台活动的起止时间到 `sync_log.db` 中。同步前检查目标方向是否有时间重叠的活动，有则跳过。

### 时序图

```
高驰原生活动 19:00-20:00
  │ coros-sync-garmin 运行
  │ 查 sync_time_log: 佳明来源 19:00-20:00 有重叠吗？
  ├─ 无重叠 → 上传到佳明CN → 写入 sync_log (coros→garmin_cn, 19:00-20:00)
  │
  │ garmin-coros-sync 运行（下一轮）
  │ 佳明新增活动 19:00-20:00（刚才同步过去的）
  │ 查 sync_time_log: coros来源 19:00-20:00 有重叠吗？
  └─ 有重叠！→ ✅ 跳过这条活动，不传回高驰

佳明原生活动 08:00-09:00
  │ garmin-coros-sync 运行
  │ 查 sync_time_log: coros来源 08:00-09:00 有重叠吗？
  ├─ 无重叠 → 上传到高驰 → 写入 sync_log (garmin_cn→coros, 08:00-09:00)
  │
  │ coros-sync-garmin 运行（下一轮）
  │ 高驰新增活动 08:00-09:00（刚才同步过去的）
  │ 查 sync_time_log: garmin_cn来源 08:00-09:00 有重叠吗？
  └─ 有重叠！→ ✅ 跳过这条活动，不传回佳明
```

### 为什么不同平台之间还需要区分？

因为佳明和高驰使用不同的 ID 体系（佳明用 `activityId`，高驰用 `labelId`），无法直接比对 ID。通过**时间重叠**来判断是否是同一个运动，避免了跨平台 ID 映射的复杂性。

### 多层防护

| 层 | 防护 | 说明 |
|:--:|------|------|
| 1️⃣ | **is_sync 状态标记** | 每个工作流在自己的 DB 文件中标记已同步的活动 ID，不被其他工作流覆盖 |
| 2️⃣ | **sync_time_log 时间重叠检查** | 共享时间日志，防止因时间段重叠导致数据往返 |
| 3️⃣ | **平台自身去重** | 即使前两层失效，佳明/高驰 API 会返回 `DUPLICATE_ACTIVITY`，不会重复导入 |

### ⚠️ 注意事项

`sync_log.db` 通过 GitHub Actions Artifact 在所有工作流之间共享传递。如果 Artifact 丢失（例如首次运行或缓存过期），某些活动可能会被重复同步，但平台自身会拒绝重复导入，不会产生实际重复数据。运行几个周期后防重标记会自动恢复。

## 四大同步工作流说明

| 工作流名称 | 同步方向 | 运行时间（北京时间） | 独立 DB | 说明 |
|-----------|:--------:|:------------------:|:-------:|------|
| `garmin-coros-sync` | 佳明 CN → 高驰 | 12:00 / 23:00 | `garmin_coros.db` | 将佳明中国区活动同步到高驰 |
| `coros-sync-garmin` | 高驰 → 佳明 CN | 12:15 / 23:15 | `coros_garmin.db` | 将高驰活动同步到佳明中国区 |
| `garmincn-sync-garminintl` | 佳明 CN → 佳明 INTL | 12:30 / 23:30 | `cn_intl.db` | 将国区活动跨区同步到国际区 |
| `garminintl-sync-garmincn` | 佳明 INTL → 佳明 CN | 12:45 / 23:45 | `intl_cn.db` | 将国际区活动跨区同步到国区 |

> ⏰ 四个工作流各间隔 15 分钟运行，避免 DB 文件提交冲突。
>
> **调整运行时间**：打开对应的 `.yml` 工作流文件，修改 `schedule` 下的 `cron` 表达式即可。
> **格式说明**：`cron: '分钟 小时 * * *'`，使用 UTC 时间，北京时间 = UTC + 8。
> 例如北京时间 `12:00` → UTC `4:00` → `cron: '0 4 * * *'`。

## DeepWiki源码解析
[![Ask DeepWiki](https://deepwiki.com/badge.svg)](https://deepwiki.com/XiaoSiHwang/garmin-sync-coros)

## 注意
由于高驰平台只允许单设备登录，同步期间如果打开网页会影响到数据同步导致同步失败，同步期间切记不要打开网页。

## 参数配置（全局总表）

| 参数名 | 用途 | 所属工作流 | 备注 |
|-------|:----:|:----------:|:----:|
| `GARMIN_EMAIL` | 佳明中国区账号邮箱 | 全部 | 国区账号 |
| `GARMIN_PASSWORD` | 佳明中国区账号密码 | 全部 | |
| `GARMIN_AUTH_DOMAIN` | 佳明中国区区域 | 全部 | 国区填 `CN` |
| `GARMIN_NEWEST_NUM` | 每次拉取活动上限（全局统一） | 全部 | 默认 `50`，设为 `0` 全量拉取；建议首次运行可设为 `0`，后续改回 `50` 或者更小 |
| `COROS_EMAIL` | 高驰登录邮箱 | garmin-coros-sync / coros-sync-garmin | |
| `COROS_PASSWORD` | 高驰登录密码 | 同上 | |
| `GARMIN_INTL_EMAIL` | 佳明国际区账号邮箱 | garmincn-sync-garminintl / garminintl-sync-garmincn | |
| `GARMIN_INTL_PASSWORD` | 佳明国际区账号密码 | 同上 | |
| `GARMIN_INTL_AUTH_DOMAIN` | 佳明国际区区域 | 同上 | 填 `COM` |

## Github配置步骤
### 1.参数配置
打开**Setting**
![打开Setting](doc/3451692931372_.pic.jpg)
找到**Secrets and variables**点击**New repository secret**按钮
![Secrets and variables](/doc/3461692931472_.pic.jpg)
打开**New repository secret**后将上述的参数填入，下图以佳明帐号为例,**Name**填写参数名,**Secret**填写你的信息，重复以上步骤填入参数即可
![填入参数](doc/3471692931624_.pic.jpg)

### 2.配置WorkFlow权限
打开**Setting**找到**Actions**点击**General**按钮,按照下图勾选并save
![配置WorkFlow权限](doc/3481692931856_.pic.jpg)

### 3. workflow配置

项目包含四个工作流文件，按需修改：

**① garmin-sync-coros.yml**（佳明 CN → 高驰，工作流名为 `garmin-coros-sync`）
打开文件，将 `GITHUB_NAME` 更改为你的 Github 用户名、`GITHUB_EMAIL` 更改为你的 Github 登录邮箱。

**② garmin-sync-garmin.yml**（佳明 CN → 佳明 INTL，工作流名为 `garmincn-sync-garminintl`）
同上，修改 `GITHUB_NAME` 和 `GITHUB_EMAIL`。
同时需在 Github Secrets 中配置 `GARMIN_INTL_EMAIL`、`GARMIN_INTL_PASSWORD`、`GARMIN_INTL_AUTH_DOMAIN`。

**③ coros-sync-garmin.yml**（高驰 → 佳明 CN，工作流名为 `coros-sync-garmin`）
同上，修改 `GITHUB_NAME` 和 `GITHUB_EMAIL`。

**④ garminintl-sync-garmincn.yml**（佳明 INTL → 佳明 CN，工作流名为 `garminintl-sync-garmincn`）【新增】
同上，修改 `GITHUB_NAME` 和 `GITHUB_EMAIL`。
使用同一组 `GARMIN_INTL_*` 和 `GARMIN_*` 配置。

更改完成后点击右上角 **Commit changes...** 提交即可。

## 重新fork项目步骤
点击页面上**Sync Fork**然后点击**Discard commit**即可
![fork sync](doc/image.png)

## 删除db步骤
按照图片顺序执行即可
![alt text](doc/image5.png)
![alt text](doc/image-1.png)
![alt text](doc/image-2.png)
![alt text](doc/image-3.png)
![alt text](doc/image-4.png)
删除完后等脚本自己执行即可

## 致谢
- 本脚本佳明模块代码来自@[yihong0618](https://github.com/yihong0618) 的 [running_page](https://github.com/yihong0618/running_page) 个人跑步主页项目,在此非常感谢@[yihong0618](https://github.com/yihong0618)大佬的无私奉献！！！
- 本仓库二次开发基于@[XiaoSiHwang](https://github.com/XiaoSiHwang) 的 [garmin-sync-coros](https://github.com/XiaoSiHwang/garmin-sync-coros) 跑步项目主页，感谢原作者的开源贡献！（本仓库已更名为 `garmin-coros-sync-plus`）
