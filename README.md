# 如果出现无法同步请检查下代码是否最新如果非最新请重新fork sync代码后删除db/目录下文件重跑一遍！！！

## 关于本仓库

本仓库（原名 `garmin-sync-coros`，已更名为 `garmin-coros-sync-plus`）基于 [XiaoSiHwang/garmin-sync-coros](https://github.com/XiaoSiHwang/garmin-sync-coros) 进行二次开发，在原项目「佳明同步到高驰」基础上做了大量扩展。**同步佳明和高驰两个平台间的运动数据，核心目标是避免高驰上产生重复的运动记录。**

> ⚠️ **Fork 须知**：你的 GitHub Secrets（账号密码等敏感信息）不会随 Fork 继承。Fork 后需要自行配置 Secrets，详见下方「Github配置步骤」。

包含以下改动：

| # | 改动内容 | 说明 |
|---|---------|------|
| 1 | **Python 版本升级** | 从 Python 3.10 升级到 3.13，升级 Actions 版本，修复 set-output 废弃语法 |
| 2 | **新增「高驰 → 佳明」反向同步** | 新增 `coros-sync-garmin` 工作流 |
| 3 | **新增「佳明中国区 ⇆ 国际区」双向跨区同步** | 新增 CN→INTL 和 INTL→CN 两个工作流 |
| 4 | **工作流全局同步数量灵活切换** | 所有工作流都遵守 `GARMIN_NEWEST_NUM`，0 为全量，＞0 每次够数停止 |
| 5 | **实时 API 时间重叠防重机制** | 同步前分别拉取双方平台活动列表，用运动的起止时间判断是否为同一运动，有重叠则跳过，避免数据往返 |
| 6 | **各工作流 DB 文件独立** | 每个工作流使用自己的 `.db` 文件，git push 互不覆盖，同步状态互不影响 |
| 7 | **同步即防重** | 不设独立的初始化流程，第一次运行就是正常同步。时间重叠的直接跳过，不重叠的正常上传 |
| 8 | **异常处理优化** | 下载失败标记异常、上传失败不 exit 继续处理下一个、同步后清理临时文件 |
| 9 | **依赖精简与升级** | 从 56 个固定版本精简为 23 个最小直接依赖 |
| 10 | **修复多项 bug** | 缓存步骤缺失、initDB 多语句、upload_activity 返回值比较错误、参数名误导等 |

## 双向防数据往返机制

本项目的核心目的是**避免高驰上出现多份相同的运动记录**，同时减少无意义的重复上传次数，提升运行效率。系统通过三道防线来实现防重。

### 同步状态标记

每个工作流在自己的独立 DB 文件中维护活动的同步状态（以 `garmin-coros-sync` 的 `garmin_coros.db` 为例）：

| is_sync_coros 值 | 含义 | 说明 |
|:----------------:|------|------|
| 0 | 未同步，待处理 | 新活动写入数据库时自动设为 0 |
| 1 | 已同步成功 | 上传到目标平台成功，或初始化时标记为已存在 |
| 2 | 同步异常 | 下载/上传失败，跳过不再重复尝试 |

`coros-sync-garmin` 的 `coros_garmin.db` 使用 `is_sync_garmin` 字段，含义完全相同。

### 第 1 道防线：同步即防重

本系统不设独立的「初始化流程」——**第一次运行就是正常的同步流程**。

每次运行都会：
1. 拉取源平台 N 条活动
2. 拉取目标平台 N 条活动（用于时间重叠参考）
3. 遍历源平台活动：
   - 与目标平台时间重叠 → 跳过上传，直接标记为已同步
   - 与目标平台不重叠 → 下载 → 上传到目标平台 → 标记为已同步

这样首次运行就会自动处理好全部历史数据：重叠的不会重复上传，不重叠的正常同步过去。

### 第 2 道防线：实时 API 时间重叠比对

每个同步脚本运行时，会同时拉取双方平台的活动列表，用运动的**起止时间**进行比对。如果对方平台已有相同时间段的活动，则跳过本次同步。

> ⚠️ **注意**：时间重叠比对依赖双方 API 正常返回。如果拉取对方平台活动列表失败（网络波动），会回退到仅靠 `is_sync` 标记防重。

```
高驰原生活动 19:00-20:00
  │ coros-sync-garmin 运行
  │ 拉取佳明活动列表 → 无此时间段的记录
  │ → 时间无重叠 → 上传到佳明CN ✅

佳明CN活动 → 已同步到INTL → INTL→CN 运行时发现时间重叠 → 跳过 ✅
```

### 第 3 道防线：佳明平台去重

即使前两道防线失效，佳明 API 自身也会拒绝重复导入（返回 `DUPLICATE_ACTIVITY`）。注意：**高驰目前没有类似的去重能力**，所以第一、二道防线对高驰方向尤其重要。

### 为什么不用 ID 映射？

佳明和高驰使用不同的 ID 体系（佳明用 `activityId`，高驰用 `labelId`），无法直接比对 ID。而同一运动的时间是跨平台一致的，通过时间重叠判断更可靠。

### 各工作流防护总览

| 工作流 | 首次初始化 | 时间重叠比对 | 平台去重 |
|:------:|:----------:|:-----------:|:--------:|
| `garmin-coros-sync` | ✅ | 拉取高驰活动列表比对 | 高驰无去重 |
| `coros-sync-garmin` | ✅ | 拉取佳明CN活动列表比对 | 佳明有去重 |
| `garmincn-sync-garminintl` | ✅ | 拉取佳明INTL活动列表比对 | 佳明有去重 |
| `garminintl-sync-garmincn` | ✅ | 拉取佳明CN活动列表比对 | 佳明有去重 |

> ⚠️ **关于时间重叠检查的兜底**：如果拉取对方平台活动列表时 API 失败（网络波动），会回退到仅靠 `is_sync` 标记防重。此时可能会有少量重复上传，但平台自身的去重能力（佳明）或 `is_sync` 标记会阻止重复同步。

## 四大同步工作流说明

| 工作流名称 | 同步方向 | 运行时间（北京时间） | 独立 DB | 说明 |
|-----------|:--------:|:------------------:|:-------:|------|
| `garmin-coros-sync` | 佳明 CN → 高驰 | 12:00 / 23:00 | `garmin_coros.db` | 将佳明中国区活动同步到高驰 |
| `coros-sync-garmin` | 高驰 → 佳明 CN | 12:15 / 23:15 | `coros_garmin.db` | 将高驰活动同步到佳明中国区
| `garmincn-sync-garminintl` | 佳明 CN → 佳明 INTL | 12:30 / 23:30 | `cn_intl.db` | 将国区活动跨区同步到国际区 |
| `garminintl-sync-garmincn` | 佳明 INTL → 佳明 CN | 12:45 / 23:45 | `intl_cn.db` | 将国际区活动跨区同步到国区 |

> ⏰ 四个工作流各间隔 15 分钟运行，避免 DB 文件提交冲突。
> **注意**：`coros-sync-garmin` 工作流默认启用，同步方向为高驰→佳明CN。
> 如果你不需要某个工作流，可以在 GitHub 仓库页面「Actions」标签页中手动 disable 它。
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
| `GARMIN_NEWEST_NUM` | 每次拉取活动上限（全局统一） | 全部 | 默认 `50`，设为 `0` 全量拉取。**此值同时也控制首次初始化时拉取的数量**——设为 0 则全量拉取所有活动标记为已同步。建议首次设为 `0`，后续改回 `50` 或更小 |
| `COROS_EMAIL` | 高驰登录邮箱 | garmin-coros-sync / coros-sync-garmin | |
| `COROS_PASSWORD` | 高驰登录密码 | 同上 | |
| `GARMIN_INTL_EMAIL` | 佳明国际区账号邮箱 | garmincn-sync-garminintl / garminintl-sync-garmincn | |
| `GARMIN_INTL_PASSWORD` | 佳明国际区账号密码 | 同上 | |
| `GARMIN_INTL_AUTH_DOMAIN` | 佳明国际区区域 | 同上 | 填 `COM`（留空也可） |

> ⚠️ `garminintl-sync-garmincn`（国际→中国）需要同时配置国际区（`GARMIN_INTL_*`）和中国区（`GARMIN_*`）两套凭据。如果你只使用部分工作流，只需配置对应所需的参数即可。

## Github配置步骤
### 1.参数配置
打开**Setting**
![打开Setting](doc/3451692931372_.pic.jpg)
找到**Secrets and variables**点击**New repository secret**按钮
![Secrets and variables](/doc/3461692931472_.pic.jpg)
打开**New repository secret**后将上述的参数填入，下图以佳明帐号为例,**Name**填写参数名,**Secret**填写你的信息，重复以上步骤填入参数即可
![填入参数](doc/3471692931624_.pic.jpg)

### 2.配置WorkFlow权限
打开**Setting**找到**Actions**点击**General**按钮,在 **Workflow permissions** 中选择 **Read and write permissions**，然后点击 Save。否则 git push 会因权限不足而失败。
![配置WorkFlow权限](doc/3481692931856_.pic.jpg)

> ⚠️ 如果之前是 Read-only 权限，记得改成 Read and write，否则 workflow 无法将更新的 DB 文件推送到仓库。

### 3. workflow配置

项目包含四个工作流文件，按需修改：

**① garmin-sync-coros.yml**（佳明 CN → 高驰，工作流名为 `garmin-coros-sync`）
打开文件，将 `GITHUB_NAME` 更改为你的 Github 用户名、`GITHUB_EMAIL` 更改为你的 Github 登录邮箱。

**② garmin-sync-garmin.yml**（佳明 CN → 佳明 INTL，工作流名为 `garmincn-sync-garminintl`）
同上，修改 `GITHUB_NAME` 和 `GITHUB_EMAIL`。
同时需在 Github Secrets 中配置 `GARMIN_INTL_EMAIL`、`GARMIN_INTL_PASSWORD`、`GARMIN_INTL_AUTH_DOMAIN`。

**③ coros-sync-garmin.yml**（高驰 → 佳明 CN，工作流名为 `coros-sync-garmin`）
同上，修改 `GITHUB_NAME` 和 `GITHUB_EMAIL`。

**④ garminintl-sync-garmincn.yml**（佳明 INTL → 佳明 CN，工作流名为 `garminintl-sync-garmincn`）
同上，修改 `GITHUB_NAME` 和 `GITHUB_EMAIL`。
使用同一组 `GARMIN_INTL_*` 和 `GARMIN_*` 配置。

更改完成后点击右上角 **Commit changes...** 提交即可。

## 重新fork项目步骤
点击页面上**Sync Fork**然后点击**Discard commit**即可
![fork sync](doc/image.png)

## 删除db步骤

如果你需要重置同步状态（比如删除后让脚本重新初始化），按照以下步骤操作：

1. 在仓库页面进入 db/ 目录
2. 删除对应的 `.db` 文件
3. 下次 workflow 运行时，会自动检测到 DB 为空，执行初始化流程
4. 初始化流程会将双方现有活动标记为已同步，不会上传任何活动

> 删除 DB 文件后，下次运行会自动执行初始化，无需手动操作。

## 致谢
- 本脚本佳明模块代码来自@[yihong0618](https://github.com/yihong0618) 的 [running_page](https://github.com/yihong0618/running_page) 个人跑步主页项目,在此非常感谢@[yihong0618](https://github.com/yihong0618)大佬的无私奉献！！！
- 本仓库二次开发基于@[XiaoSiHwang](https://github.com/XiaoSiHwang) 的 [garmin-sync-coros](https://github.com/XiaoSiHwang/garmin-sync-coros) 跑步项目主页，感谢原作者的开源贡献！（本仓库已更名为 `garmin-coros-sync-plus`）
