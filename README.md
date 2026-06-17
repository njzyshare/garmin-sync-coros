# 如果出现无法同步请检查下代码是否最新如果非最新请重新fork sync代码后删除db/garmin.db文件重跑一遍！！！

## 关于本仓库

本仓库基于 [XiaoSiHwang/garmin-sync-coros](https://github.com/XiaoSiHwang/garmin-sync-coros) 进行二次开发，在原项目「佳明同步到高驰」基础上做了大量扩展，包含以下改动：

| # | 改动内容 | 说明 |
|---|---------|------|
| 1 | **Python 版本升级** | 从 Python 3.10 升级到 3.13，升级 Actions 版本 (checkout@v3→v4, setup-python@v3→v5, cache@v3→v4)，修复 set-output 废弃语法 |
| 2 | **新增「高驰 → 佳明」反向同步** | 新增 `coros-sync-garmin` 工作流，将高驰原生活动同步到佳明中国区 |
| 3 | **新增「佳明中国区 → 国际区」跨区同步** | 新增 `garmin-sync-garmin` 工作流，将国区活动同步到国际区（garmin.com），需配置国际区账号 |
| 4 | **新增双向防数据往返机制** | 详见下方「双向防数据往返机制」章节，防止数据在佳明和高驰之间死循环 |
| 5 | **新增 `garmin_coros_mapping` 映射表** | 记录佳明 activity_id ↔ 高驰 labelId 的关联关系，实现精确去重 |
| 6 | **数据库中增加 `source` 字段** | garmin_activity 和 coros_activity 表均增加 source 字段，标记活动来源 |
| 7 | **修复全量拉取问题** | `getAllActivities()` 修复为遵守 `NEWEST_NUM` 限制，避免每次都全量拉取 |
| 8 | **异常处理优化** | 下载失败标记异常状态、上传失败不 exit 继续处理下一个、同步后清理临时文件 |
| 9 | **依赖精简与升级** | `requirements.txt` 从固定版本改为范围版本，升级 pydantic/pydantic_core 以兼容 Python 3.13 |
| 10 | **修复多项 bug** | 缓存步骤 id 缺失、initDB 多语句不支持 Python 3.13、`upload_activity` 返回值比较错误、参数名误导等 |

## 双向防数据往返机制

当同时开启多个工作流时，系统内置了**双向防往返机制**，避免数据在两个平台间死循环。核心原理如下：

### 核心问题

```
场景 A：高驰原生活动 → coros-sync-garmin → 佳明CN → garmin-sync-coros → 又回到高驰 → ❌ 死循环！
场景 B：佳明原生活动 → garmin-sync-coros → 高驰 → coros-sync-garmin → 又回到佳明 → ❌ 死循环！
```

### 解决方案（两层防护）

本项目使用**两层独立防护**来解决上述两个方向的循环：

#### 第一层：source 字段防护（防止场景 A）

在 `garmin.db` 的 `garmin_activity` 表中，每条活动记录一个 `source` 字段：

| source 值 | 含义 |
|:---------:|------|
| 0 (SOURCE\_GARMIN) | 佳明原生：手表直接记录的活动 |
| 1 (SOURCE\_COROS) | 由 `coros-sync-garmin` 从高驰同步到佳明的活动 |

**流程**：
```
高驰原生活动 ──coros-sync-garmin──→ 佳明CN
                                          │ 上传成功后，saveCorosSourceActivity()
                                          │ 将佳明返回的 activity_id 写入 garmin.db
                                          │ 标记 source=1 (SOURCE_COROS)
                                          ▼
                                    garmin_activity 表
                                    activity_id=12345, source=1
                                          │
佳明CN同步到高驰 ──garmin-sync-coros──→
  │ getUnSyncActivity() 获取未同步活动
  │ 遍历并检查 getSource()
  │ 发现 source=1 → ✅ 跳过！不传回高驰
  ▼
✅ 防循环成功
```

#### 第二层：映射表防护（防止场景 B）

在 `garmin.db` 中增加 `garmin_coros_mapping` 映射表，记录佳明 activity_id 与高驰 labelId 的对应关系：

| garmin\_activity\_id | coros\_label\_id |
|:--------------------:|:----------------:|
| 12345 | 67890 |

**流程**：
```
佳明原生活动 ──garmin-sync-coros──→ 高驰
                                          │ 上传成功后，从高驰响应提取 labelId
                                          │ saveGarminCorosMapping(12345, 67890)
                                          ▼
                                    garmin_coros_mapping 表
                                    garmin_activity_id=12345, coros_label_id=67890
                                          │
高驰同步到佳明CN ──coros-sync-garmin──→
  │ getCorosLabelIds() 获取所有已记录的高驰 labelId
  │ 遍历高驰活动，检查 labelId 是否在映射表中
  │ 发现 67890 已存在 → ✅ 跳过！不传回佳明
  ▼
✅ 防循环成功
```

### 为什么需要两层？

因为两个平台使用不同的 ID 体系：
- 佳明活动用 `activityId`（如 `12345`）
- 高驰活动用 `labelId`（如 `67890`）
- 两者之间没有对应关系，无法直接比对

所以：
- **场景 A**（高驰→佳明→高驰）：用佳明侧的 `source` 字段判断——佳明活动 ID 为 `12345`，查 `garmin_activity.source` 是否等于 `SOURCE_COROS(1)`，避免传回高驰
- **场景 B**（佳明→高驰→佳明）：用 `garmin_coros_mapping` 映射表判断——高驰活动 ID 为 `67890`，查映射表中是否有此高驰 ID，避免传回佳明

### 三层防护总览

| 工作流 | 方向 | 防护手段 | 原理 |
|--------|------|---------|------|
| `garmin-sync-coros` | 佳明 CN → 高驰 | `source` 字段 | 跳过 `source=1`（高驰来源）的活动 |
| `coros-sync-garmin` | 高驰 → 佳明 CN | `garmin_coros_mapping` 映射表 | 跳过映射表中已有的高驰活动 |
| `garmin-sync-garmin` | 佳明 CN → 佳明 INTL | 不涉及 | 只操作两个佳明账号，无跨平台循环风险 |

### 效果

✅ 三个工作流同时开启也不会出现数据死循环
✅ 每个运动记录只在源头平台产生一次

### ⚠️ 注意事项

双向防重机制依赖高驰上传接口响应中的 `labelId` 字段建立映射关系。如果高驰接口响应格式变更导致无法提取 `labelId`，`coros-sync-garmin` 可能无法识别已同步活动，导致重复上传尝试（佳明会返回 `DUPLICATE_ACTIVITY` 拒绝，不会重复导入）。如遇此情况，请提交 Issue。

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
| `GARMIN_NEWEST_NUM` | 佳明中国区每次拉取上限 | 全部 | 默认 `100`，增量同步够用 |
| `COROS_EMAIL` | 高驰登录邮箱 | garmin-sync-coros / coros-sync-garmin | |
| `COROS_PASSWORD` | 高驰登录密码 | 同上 | |
| `GARMIN_INTL_EMAIL` | 佳明国际区账号邮箱 | garmin-sync-garmin | |
| `GARMIN_INTL_PASSWORD` | 佳明国际区账号密码 | 同上 | |
| `GARMIN_INTL_AUTH_DOMAIN` | 佳明国际区区域 | 同上 | 填 `COM` |

## 三大同步工作流说明

| 工作流文件 | 同步方向 | 运行时间（北京时间） | 说明 |
|-----------|:--------:|:------------------:|------|
| `garmin-sync-coros` | 佳明 CN → 高驰 | 12:00 / 23:00 | 将佳明中国区运动数据同步到高驰 |
| `garmin-sync-garmin` | 佳明 CN → 佳明 INTL | 12:15 / 23:15 | 将佳明中国区数据跨区同步到佳明国际区 |
| `coros-sync-garmin` | 高驰 → 佳明 CN | 12:30 / 23:30（默认停用） | 将高驰原生活动同步到佳明中国区 |

> ⚠️ 三个工作流已错开运行时间（各间隔15分钟），避免 DB 文件提交冲突。

## Github配置步骤
### 1.参数配置
打开**Setting**
![打开Setting](doc/3451692931372_.pic.jpg)
找到**Secrets and variables**点击**New repository secret**按钮
![Secrets and variables](/doc/3461692931472_.pic.jpg)
打开**New repository secret**后将上述的参数填入，下图以佳明帐号为例,**Name**填写参数名,**Secret**填写你的信息，重复以上步骤填入五个参数即可
![填入参数](doc/3471692931624_.pic.jpg)

### 2.配置WorkFlow权限
打开**Setting**找到**Actions**点击**General**按钮,按照下图勾选并save
![配置WorkFlow权限](doc/3481692931856_.pic.jpg)

### 3. workflow配置

项目包含三个工作流文件，按需修改：

**① garmin-sync-coros.yml**（佳明 CN → 高驰）
打开文件，将 `GITHUB_NAME` 更改为你的 Github 用户名、`GITHUB_EMAIL` 更改为你的 Github 登录邮箱。

**② garmin-sync-garmin.yml**（佳明 CN → 佳明 INTL）
同上，修改 `GITHUB_NAME` 和 `GITHUB_EMAIL`。
同时需在 Github Secrets 中配置 `GARMIN_INTL_EMAIL`、`GARMIN_INTL_PASSWORD`、`GARMIN_INTL_AUTH_DOMAIN`。

**③ coros-sync-garmin.yml**（高驰 → 佳明 CN，默认停用）
同上，修改 `GITHUB_NAME` 和 `GITHUB_EMAIL`。

更改完成后点击右上角 **Commit changes...** 提交即可。

## 重新fork项目步骤
点击页面上**Sync Frok**然后点击**Dicard commit**即可
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
- 本仓库二次开发基于@[XiaoSiHwang](https://github.com/XiaoSiHwang) 的 [garmin-sync-coros](https://github.com/XiaoSiHwang/garmin-sync-coros) 跑步项目主页，感谢原作者的开源贡献！
