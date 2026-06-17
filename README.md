# 如果出现无法同步请检查下代码是否最新如果非最新请重新fork sync代码后删除db/garmin.db文件重跑一遍！！！
## 致谢
- 本脚本佳明模块代码来自@[yihong0618](https://github.com/yihong0618) 的 [running_page](https://github.com/yihong0618/running_page) 个人跑步主页项目,在此非常感谢@[yihong0618](https://github.com/yihong0618)大佬的无私奉献！！！

## DeepWiki源码解析
[![Ask DeepWiki](https://deepwiki.com/badge.svg)](https://deepwiki.com/XiaoSiHwang/garmin-sync-coros)

## 注意
由于高驰平台只允许单设备登录，同步期间如果打开网页会影响到数据同步导致同步失败，同步期间切记不要打开网页。

## 参数配置
|       参数名       |                备注                |        案例        |
| :----------------: | :--------------------------------: | :----------------: |
|    GARMIN_EMAIL    |          佳明登录帐号邮箱          |                    |
|  GARMIN_PASSWORD   |            佳明登录密码            |                    |
| GARMIN_AUTH_DOMAIN | 佳明区域（国际区填:COM 国区填:CN） |    (COM or CN)     |
| GARMIN_NEWEST_NUM  |            最新记录条数            | (默认0，可写大于0) |
|    COROS_EMAIL     |           高驰 登录邮箱            |                    |
|   COROS_PASSWORD   |             高驰 密码              |                    |

**【新增】佳明中国区 → 国际区 跨区同步参数**

|       参数名          |                备注                |        案例        |
| :-----------------: | :--------------------------------: | :----------------: |
|  GARMIN_INTL_EMAIL  |       佳明国际区登录帐号邮箱        |                    |
| GARMIN_INTL_PASSWORD |         佳明国际区登录密码          |                    |
|GARMIN_INTL_AUTH_DOMAIN|  佳明国际区域名（填 COM）     |      (COM)        |
|  GARMIN_INTL_NEWEST_NUM  |   国际区同步最新记录条数  | (默认100，可写大于0) |

## 三大同步工作流说明

本项目支持三个独立的 GitHub Actions 工作流，可按需启用：

| 工作流文件 | 同步方向 | 运行时间（北京时间） | 说明 |
|-----------|:--------:|:------------------:|------|
| `garmin-sync-coros` | 佳明 CN → 高驰 | 12:00 / 23:00 | 将佳明中国区运动数据同步到高驰 |
| `garmin-sync-garmin` | 佳明 CN → 佳明 INTL | 12:15 / 23:15 | **【新增】** 将佳明中国区数据跨区同步到佳明国际区 |
| `coros-sync-garmin` | 高驰 → 佳明 CN | 12:30 / 23:30（默认停用） | 将高驰原生运动数据同步到佳明中国区 |

> ⚠️ 三个工作流已错开运行时间（各间隔15分钟），避免 DB 文件提交冲突。

## 双向防数据往返机制

当同时开启多个工作流时，系统内置了**双向防往返机制**，避免数据在平台间死循环：

### 原理

在 `garmin.db` 中新增 `source` 字段标记每条活动的来源：

| source 值 | 含义 | 说明 |
|:---------:|------|------|
| 0（默认）| 佳明原生 | 手表直接记录的活动，正常同步到高驰 |
| 1 | 来自高驰同步 | 由 `coros-sync-garmin` 从高驰同步到佳明的活动 |

### 防循环逻辑

```
高驰原生活动 ──coros-sync-garmin──→ 佳明CN（标记source=1）
                                            │
                                            ↓
佳明CN（含source=1的活动）──garmin-sync-coros──→ 高驰？
                                            │
                                            ✅ 跳过！（source=1 不传回高驰）
```

- **`garmin-sync-coros`**：上传到高驰前，检查活动 `source`，若为 `1`（来自高驰）则跳过并标记为已同步
- **`coros-sync-garmin`**：上传成功后，将佳明返回的新 `activity_id` 写入 `garmin.db` 并标记 `source=1`
- **`garmin-sync-garmin`**：只操作两个佳明账号，不涉及高驰，无循环风险

### 效果

✅ 开启三个工作流也不会出现数据死循环
✅ 每个运动记录只在源头平台产生一次，不会在平台间反复传递

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

**② garmin-sync-garmin.yml**（佳明 CN → 佳明 INTL）【新增】
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
