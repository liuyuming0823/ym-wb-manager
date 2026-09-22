---
name: ym-wb-manager
slug: ym-wb-manager
display_name: WorkBuddy 管理中心
display_name_en: WorkBuddy Manager
displayName: WorkBuddy 管理中心
description: "把本机 WorkBuddy 的任务、产物、项目、定时任务、资料库、已装扩展汇成一个本地网页面板，可搜索筛选，并能改名/软删除/恢复/备份，所有写操作都能回滚。当用户说「WorkBuddy 管理中心」「管理我的任务」「任务太多找不到」「查看历史任务」「定时任务管理」「清理已删任务」「产物管理」「资料库浏览」「我装了哪些技能」「装了哪些专家」「技能列表」「连接器管理」时使用。 首次运行会自动探测本机环境生成配置，换电脑也能直接跑。"
description_zh: "本地网页面板：集中查看和管理 WorkBuddy 的任务、产物、项目、定时任务、资料库与已安装的技能/专家/连接器"
description_en: "A local web dashboard to browse and manage WorkBuddy tasks, artifacts, projects, automations and installed skills/experts/connectors"
summary: "本地网页面板：集中查看和管理 WorkBuddy 的任务、产物、项目、定时任务、资料库与已装扩展"
category: dev-programming
version: 1.2.2
author: 刘玉明
tags: [WorkBuddy管理中心, 任务管理, 产物管理, 定时任务, 资料库, 技能管理, 专家管理, 连接器]
trigger:
  - WorkBuddy管理中心
  - 管理中心
  - 管理我的任务
  - 任务太多
  - 查看任务
  - 任务管理面板
  - 定时任务管理
  - 清理任务
  - 产物管理
  - 资料库浏览
  - 任务列表
  - 找回删除的任务
  - 我装了哪些技能
  - 装了哪些专家
  - 技能列表
  - 专家团
  - 连接器管理
  - 已安装插件
agent_created: true
---

# WorkBuddy 管理中心 (ym-wb-manager)

WorkBuddy 用久了会有几百个任务散在会话记录里，想找「上周那个跑通了 Flink 的任务」只能翻。
这个技能装一个**本地网页面板**，把任务、产物、项目、定时任务、资料库汇到一个页面里，可搜索、可筛选、可改名、可删可恢复。

面板一共十个页面：

| 页面 | 看什么 |
|---|---|
| 总览 | 数据全景 + 快捷筛选入口（无人问津 / 30 天未动 / 产物丢失） |
| 任务 | 所有会话任务，10 列，可按状态/模式/类型/时间筛、按 6 个字段排 |
| 项目 | 工作目录（真实项目 / 临时目录分开） |
| 定时任务 | 周期与提醒，可改名、暂停、删除 |
| **扩展** | **已安装的技能、专家、专家团、连接器、内置功能**（带中文名） |
| **在线技能** | **搜 SkillHub 并一键安装**（免登录公开接口；安装前需本地服务） |
| **账户** | **积分 / 签到 / 成长计划 / 设置 / 记忆 / 检查更新** |
| 资料库 | **按资料类型分页签**（技能/任务产物/文档方案/系统外观）+ 文件浏览；并说明与客户端**云端**资料库的区别 |
| 回收站 | 已删除的任务与定时任务 |

**交互约定（v1.2.0 起）**：

- **列表没有「操作」列**。点整行任意位置展开详情，再点一次收缩（行内控件不受影响：
  点铅笔仍是改名、点按钮仍是按钮）。少一列，窄屏也不用横向滚。
- **所有列头都能调宽**：拖表头右侧那条竖线。双击手柄恢复这一列，在表头右键恢复本页全部。
  宽度按**百分比**存 localStorage，换个分辨率的屏幕打开也不会错位。
- **接口报错讲人话**（v1.2.1 起）：服务端返回非 JSON（404 纯文本、500 HTML）时，
  不再把 JS 的 `SyntaxError: Unexpected token` 甩给用户，而是说清是哪个接口、
  是不是旧版本服务进程。

**纯标准库，零依赖。** 采集与只读浏览全程离线；只有「在线技能」与「检查更新」
两个功能会访问 `api.skillhub.cn`（公开接口，不需要登录）。所有写操作执行前自动备份数据库，可回滚。

## 何时使用

- 用户说「任务太多找不到」「想找某次任务」「看看我跑过哪些」
- 用户要管理定时任务（改名、启用/暂停、删除）
- 用户想清理已删任务（软删除的还在回收站里占着）
- 用户想按项目聚合看任务，或浏览产物文件清单
- **用户问「我装了哪些技能 / 专家 / 连接器」「某个技能在哪」「这东西是自带的还是我装的」**
- **用户想搜技能商店、装一个新技能（「有没有 XX 技能」「帮我装个 XX」）**
- **用户问积分余额、每日签到、成长计划，或想查技能有没有更新**
- 用户想有个统一的入口看 WorkBuddy 的数据

## 交付标准（先看这条）

终点是**用户双击就能打开一个网页面板**，不是一堆脚本。

1. `scripts/doctor.py` 跑通（11 项无 bad）—— 这一步顺便会生成配置
2. **桌面出现「WorkBuddy 管理中心」图标**（首次运行自动创建）
3. 用户双击桌面图标（或目录里的 `启动管理中心.vbs`）看到页面
4. 页面能读到他自己的任务数据

> **为什么要在桌面放图标**：用户拿到技能后不知道要去哪个目录、双击哪个文件。
> 与其在文档里写「请手动双击 XXX」，不如直接把图标放到他每天都会看到的地方。
> 由 `scan.py` 的 `_ensure_shortcut()` 完成，**只建一次**（已有同名 `.lnk` 就跳过，
> 不覆盖用户自己改过的图标 / 名字）。不想要桌面图标时设 `WB_NO_SHORTCUT=1`。

> **双击启动器从哪来**：技能包里**不含** `.bat` / `.vbs`（技能平台只收纯文本扩展名，
> 带上它们会整单拒收）。改为由 `scripts/make_launchers.py` 在**首次运行时现场生成**——
> 跑一次 `doctor.py` 或 `scan.py` 就会自动补齐，生成结果与手工放进去的完全一致。
> 也可以单独生成：`python scripts/make_launchers.py`（`--force` 覆盖，`--check` 只检查编码与行尾）。
> 快捷方式同理：`python scripts/make_shortcut.py` 手动建，`--remove` 删除，
> `--start-menu` 顺便放进开始菜单。

## 执行步骤

### 第 1 步：环境体检（必须先跑）

```bash
python scripts/doctor.py
```

检查 Python 版本、数据目录、数据库、页面模板、配置可写性、端口占用，共 11 项。
分三档：`正常` / `注意`（能跑但功能残缺）/ `问题`（跑不起来）。
退出码：0 可通过（允许有「注意」）/ 1 有「问题」。

**第一次跑会自动探测本机环境并生成 `config.json`**（在技能根目录），
顺带打印探测到的资料库目录。不用先建配置文件。

### 第 2 步：起服务

```bash
python scripts/serve.py            # 端口读 config.json，自动开浏览器
python scripts/serve.py --port 9000 --no-open   # 临时换端口 / 不自动开
```

服务只监听 `127.0.0.1`，端口被占用会自动往后试（最多 10 个）。

用户日常用双击：`启动管理中心.bat`（有控制台窗口）/ `启动管理中心.vbs`（无窗口）。
`停止管理中心.bat` 停服务，`刷新数据.bat` 重新采集并生成静态页。
这四个启动器若不存在，跑一次 `python scripts/make_launchers.py` 即可补齐。

### 第 3 步：只想出个静态页（不起服务）

```bash
python scripts/scan.py --open          # 生成 index.html 并打开
python scripts/scan.py --quiet         # 不打印首次运行引导（自动化里用）
```

静态页能看、能搜、能点开任务，但**不能改名/删除**——那些要起服务。

### 第 4 步：非要改配置时

改资料库目录**建议走页面「设置」页**（能所见即所得地增删、还能一键重新探测）。
命令行也可以：

```bash
python scripts/config.py --show     # 看当前配置和每个目录是否存在
python scripts/config.py --probe    # 重新探测资料库目录并写入
python scripts/config.py --reset    # 删掉配置重新生成
python scripts/config.py --port     # 只打印端口号（给脚本读）
```

## 配置说明（config.json，在技能根目录）

三层优先级，从低到高：**内置默认值 → config.json → 环境变量**。
所以**删掉 config.json 也能跑**（回落到默认值），这是刻意的设计。

| 字段 | 说明 |
|---|---|
| `workbuddy_dir` | WorkBuddy 数据目录，默认 `~/.workbuddy`，正常不用改 |
| `library_roots` | 「资料库」页要浏览的目录，可增删；首次运行自动探测 |
| `port` | 服务端口，默认 8777，被占用自动往后找 |
| `keep_backups` | 写操作备份保留份数，默认 8 |

环境变量 `WB_DIR` 可以覆盖 `workbuddy_dir`。**刻意只开放这一个**——
环境变量越多越难排查「为什么我改了配置没生效」。

`library_roots` 里留一条已经不存在的路径是**允许**的：页面会标出来但保留，
不静默吞掉。静默消失会让人以为是自己加错了。

## 目录说明

```
SKILL.md            ← 本文件
.skillignore        ← 打包排除清单（挡掉 .bat/.vbs 与运行时产物）
config.json         ← 运行时生成（不进包，也别提交）
index.html          ← 静态页，scan.py 生成（不进包）
启动管理中心.bat / .vbs
停止管理中心.bat
刷新数据.bat
说明.md              ← 给最终用户看的使用说明
scripts/            ← 全部逻辑
  serve.py          ← 本地服务（页面 + API + 白名单）
  scan.py           ← 采集并生成静态页
  data.py           ← 数据层（只读读 workbuddy.db / projects / artifact-index）
  ops.py            ← 写操作（改名/删除/恢复/备份/日志）
  config.py         ← 唯一路径决策点 + 环境探测
  doctor.py         ← 环境体检
  findpy.py         ← 找 Python 解释器（版本号不写死）
  alive.py          ← 探活（给 .bat 判断要不要重复启动）
  killer.py         ← 精确停服务
  console.py        ← 修控制台编码（GBK 下中文不乱码）
  make_shortcut.py  ← 生成桌面快捷方式
  make_launchers.py ← 运行时生成 .bat / .vbs 启动器（技能包不含它们）
  online.py         ← SkillHub 公开接口（搜索 / 详情 / 安装，含路径消毒）
  selftest.py       ← 自检 399 项
assets/
  template.html     ← 单文件页面模板（数据注入 /*__DATA__*/null）
_domtest/
  run.js            ← 页面 JS 真跑（最小 DOM 桩 + 事件投递），被 selftest 调用
```

## 「扩展」页的数据从哪来

这是全项目**唯一一处需要合并五份异构数据**的地方，动之前先看清：

| # | 来源 | 内容 | 元数据取自 |
|---|---|---|---|
| 1 | `plugins/installed_plugins.json` | 官方登记的安装清单（含内置 + 市场装的） | 安装目录下 `.workbuddy-plugin/plugin.json` **或** `.codebuddy-plugin/plugin.json` |
| 2 | `plugins/marketplaces/<市场>/plugins/` | 市场货架（**未安装也算**，标 `installed=False`） | 同上 |
| 3 | `skills/*/SKILL.md` | 目录型技能 | frontmatter（`name` / `description` / `trigger`） |
| 4 | `mcp.json` | 手写的 MCP 连接器 | `url` / `command` / `disabled` |
| 5 | `experts/custom/*/experts.json` | 本地专家登记（**只有名字**） | 无 —— 靠名字去 ② 里**认领**实体 |

几个必须知道的点：

- **元数据目录有两种名字**：内置包用 `.workbuddy-plugin`，市场/自制包用
  `.codebuddy-plugin`。只认一种会读到一片空白（描述、作者全是空）。
- **第 ⑤ 步是「认领」不是「新增」**：`experts.json` 只写了一串名字，
  实体在 ② 里。早期实现无条件新增一条，导致同一个专家出现两回 ——
  一条是市场实体（没装）、一条是「本地登记（找不到目录）」。
  现在按名字先去已有结果里找，**找到就把它标成已安装**。
- **描述兜底**：包里没写 `description` 时，取 `README.md` 第一句有内容的话。
- 任何一处读失败**只影响那一条**，不能让整个列表空掉。

页面上的三个维度：**类型**（技能/专家/专家团/连接器/内置功能）、
**来源**（内置/本地/市场/MCP）、**安装状态**（已安装/货架）。
默认「只看已安装」—— 否则货架上几十个没装的东西会把答案淹掉。

### 中文名从哪来（只显示 slug 等于让人猜）

列表默认显示**中文名**，标识降级成下面那一行小字。数据源按优先级：

| 顺序 | 位置 | 字段 |
|---|---|---|
| 1 | `skills-marketplace/skills/<slug>/_skillhub_meta.json` | `name` |
| 2 | `SKILL.md` frontmatter | `display_name` / `displayName` |
| 3 | `.workbuddy-plugin/plugin.json` 或 `.codebuddy-plugin/plugin.json` | `name` |
| 4 | 专家包 `.codebuddy-plugin/plugin.json` | `name` |

> **已装包的目录里没有 `_skillhub_meta.json`** —— 那是市场缓存才有的文件。
> 所以必须先按 slug 建一份「市场元数据索引」，再回查。
> 本机 191 个扩展里 45 个有中文名，其余是本地自制 / 未上架，显示标识属正常。

**名字与标识重合时不显示副标题**（例如官方连接器 `github` 的名字就是 `github`），
否则同一行出现两遍一样的字。判据是 `display_name == name`，不是「有没有 display_name」。

### 「在线技能」页

搜索走 SkillHub 公开接口，**不需要登录、不需要 token**：

```
GET https://api.skillhub.cn/api/v1/search?q=<关键词>     # 参数名必须是 q
GET https://api.skillhub.cn/api/v1/download?slug=<slug>   # zip 二进制，用于安装
```

- `scripts/online.py` 封装 `search()` / `detail()` / `install()`。
- **安装必须走本地服务**（静态快照页只能看）：`online.py` 落盘前用 `_safe_name()` /
  `_safe_join()` 双重校验，`../evil`、`..\evil`、`a/../../evil` 全部拒绝。
- 覆盖安装要二次确认；装完返回 `restart_hint`（技能要重启客户端才加载）。

### 「账户」页为什么只做「入口」

积分余额、每日签到、成长计划**全部需要登录令牌**，令牌在系统加密存储里。
读不到 —— 也不该去读。所以这一页的分工是：

- **本地能直接读的**照常显示：数据目录、客户端路径、记忆文件清单（点开即读，只收 md/txt/json）。
- **需要登录态的**做成**唤起入口**：`workbuddy://settings/account`、`workbuddy://home` 等，
  网页类入口（签到、成长计划）用 `https://` + `window.open`（不顶掉管理中心）。

**绝不伪造积分数字。** 接口 `account_info()` 返回的 `credits.available` 恒为 `False`
并带上 `reason`，selftest 有断言守着这条。

### 「检查更新」的版本比较

`check_updates()` 逐个拿本地 slug 去线上查同名技能，比版本号。三个必须有：

1. **比大小，不比相等**。写成 `version != latest` 会把「本地更新」误报成「可更新」，
   然后建议用户降级（真实案例：expert-packager 本地 1.3.10 / 线上 1.0.2）。
   现在用 `_ver_cmp()`，只在线上**确实更大**时算 `outdated`，否则标 `ahead` 单列一段。
2. **slug / version 缺失要兜底**，不能整条跳过。实测大量技能只写 `name` 不写 `slug`、
   一批不写 `version` —— 这是正常写法。slug 缺用目录名兜底，version 缺只确认线上存在
   并标 `note`，与「线上查不到」分开报告。
3. **列表排序要优先自己**。按字母序取前 N 个会把 `ym-*` 整段砍掉（含 ym-wb-manager 本身），
   所以 `sort()` 先把 `ym-` 开头排前面，上限提到 80，超出的进 `errors` 说清原因。

### 「资料库」页为什么分页签

最初的资料库首页只有几张**根目录卡片**，而 WorkBuddy 自己的东西（技能、产物、方案、
记忆、日志…）全塞在「WorkBuddy 数据」这一张卡后面 —— 得点进去再一层层翻。
用户的反馈很直接：**「资料库现在是只有设置里面的东西了吗，那原来 workbuddy 里的资料在哪里了呢」**。
东西一个没少，是**没有索引**。

所以按**资料类型**分成四个页签（`data.py:_LIB_GROUPS`）：

| 页签 | 装什么 | 典型入口 |
|---|---|---|
| 技能与专家 | 装了什么、市场货架、备份 | `skills` / `experts` / `plugins` / `connectors` / `mcp-servers` / `skills-marketplace` / `skill-backups` |
| 任务与产物 | 会话、产物、定时任务、变更 | `projects` / `artifact-index` / `tasks` / `sessions` / `changes-index` / `changes-detail` / `automation-backups` |
| 文档与方案 | 方案、记忆、灵感 | `plans` / `memory` / `inspiration` / `file-history` |
| 系统与外观 | 日志、皮肤、剪贴板图片 | `logs` / `clipboard-images` / `appearance-resources` / `wb-skin` / `storage` / `blobs` |

几条设计决定：

- **只收录真实存在的目录**。`library_groups()` 与 `library_roots()` 策略**相反**：
  后者是用户自己配的，失效了也要返回并标 `missing`（「你配错了，去设置改」）；
  前者是自动分组，「本机没有的东西」不该出现在页签里让用户点了报错。
- **计数读不出来给 `None`，不许给 0**。0 会被理解成「这是个空目录」，
  而真实情况往往是权限或 IO 错了。页面见 `None` 就显示 `—`。
- **大目录的计数要设上限**。`_dir_count()` 递归数到 9999 就返回，
  不能让一个几万文件的目录把页面拖死。
- **分组是「视图」不是新的权限**。每个入口都指向真实目录，
  浏览仍走 `/api/ls` 的白名单校验，这一层只负责**列出来**，不放宽任何限制。
- **只在首页分页签**。进了子目录还是原来的面包屑 + 表格（`renderLibraryBrowse()`）。
- **下拉/点击的边界**：资料库的行 `data-row="lib:<路径>"` 带 `lib:` 前缀，
  行点击展开逻辑必须 `early-return` —— 否则进 `openSet` 集合只留垃圾。
- **「我配置的目录」区块保留在下方**，用户自己配的东西不能被页签顶掉。

### 🔴 「资料库」有两个，必须说清哪个是哪个

这是**用户连问两次**的同一个困惑：「原来 workbuddy 里的资料在哪里了呢」→
「这个资料库的信息我在管理中心还是没看到」。

客户端侧边栏有一个「资料库」（英文 `My Files`），里面分**三块**，
我们只管其中一块：

| 客户端分区 | i18n key | 真实位置 | 管理中心 |
|---|---|---|---|
| **本地产物** | `myFiles.taskArtifacts` | `~/.workbuddy/artifact-index` 等本地磁盘 | ✅ 能看，就是「任务与产物」页签的产物索引 |
| **云端网盘** | `myFiles.cloudFiles` | 腾讯云 COS（`drive.tencent.com`） | ❌ 读不到 |
| **我的资料库** | `myFiles.spaceFiles` | 云端知识空间 / Space，含「团队空间」 | ❌ 读不到 |

用户看到的「我的资料 → SkillPay资料」属于 `spaceFiles`，**在云端，本机没有副本**。

**怎么确认这件事（别猜，去看客户端源码）**：客户端的 renderer 全在
`%LOCALAPPDATA%\Programs\WorkBuddy\resources\app.asar` 里，可以直接当二进制搜关键字：

```python
buf = open(r'...\resources\app.asar','rb').read()
buf.count('资料库'.encode('utf-8'))          # 211 次
# myFiles.title / myFiles.taskArtifacts / myFiles.cloudFiles / myFiles.spaceFiles
# 「知识空间（Space）资料库入口开关（enable 语义，默认关闭）」
# 「tencentDocs: 云端资料库 CRUD 与鉴权（需 OAuth）」
```

本地也确实**没有任何云端资料库的缓存**：`storage/` 下只有 `my-files.json`，
内容是 `{"favoriteIds.<uid>: []}` —— 只有收藏夹指针，没有文件清单。
真正的云端文件走 `edge-sync-mapping-v*.db` 的 `edge_sync_artifact_cache`
（字段 `cos_uri` / `download_url` / `smh_path`，全指向线上）。

**所以页面上的做法**：资料库首页挂一条黄底提示，直接写明三块的区别 + 那句
「本机没有副本」，再给一个按钮 `data-open-url="workbuddy://my-files"`
跳到客户端的资料库。**用真实的深链，不要编**：

- `workbuddy://my-files`（已确认 6 处引用，含 `?tab=cloudFiles` 用法）
- `workbuddy://library/open?nodeId=<id>`（跳具体节点）
- 只有 `tab=cloudFiles` 被证实；`spaceFiles` 是内部 key，**不是**已验证的 tab 参数值，
  所以按钮不要带 tab，让它落到资料库首页即可。

> `data-open-url` 的处理：`workbuddy://` 用 `location.href`（协议已注册），
> `http(s)://` 用 `window.open` —— 别把管理中心这一页顶掉。

**教训**：本地工具永远只能看到本地的东西。当用户说「我的文件在这里找不到」时，
先把「它到底在不在本机」查清楚，再决定是**修 bug**还是**写清边界**。
这次是后者：数据一直在云上，我们没有漏做，但**没告诉用户边界在哪**，等于让他白找两轮。

### 接口返回非 JSON 时，别把 JS 黑话甩给用户

用户截过这样一张图：**账户页**和**在线技能搜索**都显示

```
读取失败：SyntaxError: Unexpected token 'o', "not found" is not valid JSON
```

这句话对排查零帮助 —— 它说的是「`not found` 这七个字母走了 JSON 解析器」，
而用户需要知道的是「哪个接口没了、怎么办」。

**根因有两层，缺一不可**：

1. **页面配了旧版服务进程**。`serve.py` 进程一旦启动，加载的就是当时的代码；
   后来新增的路由（`/api/account`、`/api/online/search`…）在旧进程里根本没注册，
   `BaseHTTPRequestHandler` 对未知路径返回**纯文本 `not found`**，HTTP 404。
   → **改完 Python 后端必须重启服务进程**，否则新页面 + 旧后端 = 一片 404。
2. **前端直接 `r.json()`**。非 JSON 响应会抛 `SyntaxError`，且错误信息完全
   暴露的是解析器视角。这是**真 bug**，独立于进程问题。

**修复**：新增统一入口 `parseJson(r)`，用 `r.text()` 拿到原始文本再 `JSON.parse`，
把三种情况翻译成人话：

| 情况 | 给用户的话 |
|---|---|
| 404 且非 JSON | 「服务端没有这个接口（HTTP 404）。多半是管理中心的服务还是旧版本进程，请关掉后重新双击『启动管理中心』再试。」 |
| 5xx | 「服务端出错了（HTTP 500）…」 |
| 其它非 JSON | 「服务端返回的不是 JSON（HTTP xxx）：<前 120 字>」 |

全部 **11 处 fetch 调用点**改用 `parseJson`（`/api/open`、`/api/reveal`、`/api/reopen`、
`api()`、`/api/online/search`、`/api/online/install`、`/api/ls`、`/api/account`、
`/api/memory`、`/api/check-update`、`/api/refresh`）。selftest 有一条断言
**钉住代码里不再出现裸 `r.json()`**（剥掉注释行再检查），防止回退。

> 这一条配合上一节看：**「先重启服务」是这个项目最常见的自证手段。**
> 任何「接口 404 / 返回 not found」的第一反应都是它。

## 安全设计（改代码前必读）

改这个技能时，下面几条**不能破**：

1. **路径白名单**：`serve.py` 的 `allowed_roots()` 只放行
   `workbuddy_dir` + 各任务 cwd + 资料库根 + 面板目录。
   **绝对不要把整个用户主目录加进去** —— 那等于把 `.ssh` / `.aws` /
   `Documents` / `Desktop` 全开放，属于越权。
   selftest 里有专门的用例守着这条（`.ssh` / 主目录本身 / 系统目录 / 目录穿越必须被拒）。
2. **只读优先**：所有读取都不动 WorkBuddy 数据。写操作前先备份数据库。
3. **软删除**：删除只是打标记，进回收站可恢复。彻底删除要手输 `DELETE`。
4. **配置接口只收白名单字段**：`POST /api/config` **不接受** `workbuddy_dir`。
   否则谁能访问这个端口就能把数据目录指到别处去。
5. **端口只绑 127.0.0.1**。

### 会写什么、不会碰什么（知情声明）

本技能**确实会写数据**，范围仅限下面两处，全部可回滚：

| 写入目标 | 什么操作 | 能不能回滚 |
|---|---|---|
| SQLite `sessions` 表 | 改名（`custom_title`）、软删除（`deleted_at`） | 能。删除是打标记；改名可清空恢复自动标题 |
| `<数据目录>/_wbmanager_backups/` | 写操作前自动备份 `workbuddy.db` | 备份文件本身可删 |
| `~/.workbuddy/skills/<slug>/` | **仅**「在线技能」页点安装时新增目录 | 能。删掉该目录即可；覆盖安装前会二次确认 |

**联网范围（明确写死）**：默认离线。只有两个功能会出网，且都打向
`api.skillhub.cn` 的**公开接口**（不需登录、不带任何账号信息）：

| 功能 | 请求 |
|---|---|
| 在线技能 · 搜索 | `GET /api/v1/search?q=…` |
| 在线技能 · 安装 | `GET /api/v1/download?slug=…` |
| 检查更新 | 复用 search 接口逐条比对版本 |

**不上传任何数据**：请求里只有用户手输的关键词或技能 slug，不携带
本机任务、路径、账号或 token。

**明确不会做的事**：不改 WorkBuddy 的程序文件、不动 `projects/` 里的会话原始记录、
不动 `artifact-index/` 里的产物清单、不读系统里存的登录令牌。
`ops.py:delete_task_permanent`（彻底删除）需要前端手输 `DELETE` 才触发，
且只清 `sessions` 表里那一行。

**凭据来源**：本地功能无需任何凭据（读的是本机当前用户自己的 `~/.workbuddy`）；
在线技能与检查更新走免登录公开接口，同样不需要 token 或密钥。


## 常见坑

这些都是实际踩出来的，改之前先看：

1. **🔴 模块级路径常量会在 import 时被冻结。**
   `ops.py` 里原来写 `DB_PATH = datalayer.DB_PATH`，import 那一刻就把值拷下来了，
   之后改配置**根本不生效**，而且在测试里表现为「明明改了 WB_DIR 却还是读到旧库」。
   → 现在全部是函数 `DB_PATH()` / `BACKUP_DIR()` / `LOG_PATH()`，每次现取。
   改代码时**别再写回模块级常量**。

2. **🔴 白名单是安全关键路径，改完必须跑 selftest。**
   删掉 `datalayer.LIBRARY_ROOTS` 时漏改 `serve.py` 的引用，会直接
   `AttributeError` 让服务起不来；更糟的是如果改了判断逻辑却没测，
   可能把 `.ssh` 放进来。`selftest.py` 的「拒绝私钥目录」等用例就是守这个的。

3. **🔴 `config.save()` 必须原子写。**
   用 `os.replace(tmp, CONFIG_PATH)`。直接覆写的话，写一半断电会留下
   半个坏 JSON，下次启动整个工具起不来。

4. **🔴 体检判端口占用要绕过代理，并且超时给够。**
   沙箱 / 企业环境的 `http_proxy` 会劫持 `127.0.0.1` 请求，表现为超时，
   看着像「端口被别的程序占了」，其实自己人。
   用 `urllib.request.build_opener(urllib.request.ProxyHandler({}))`。
   另外 `/api/data` 首次是冷启动采集，实测要 ~800ms，
   超时给 0.8s 会卡在边界上导致结论时对时错 —— **给 3s**。

5. **🔴 启动器里不要写死 Python 版本号。**
   WorkBuddy 升级自带 Python 后版本号会变，`.../versions/3.13.12/python.exe`
   这种写法会让启动器整个失效，而**报错是「双击没反应」**，极难排查。
   → 用 `scripts/findpy.py` 扫目录取最新；`.bat` 里有「鸡生蛋」问题
   （findpy 自己也要 Python 跑），所以先用 `dir /b /ad /o-n` 按名倒序挑一个引导解释器。
   **版本号比较必须按数字**（`_version_key`）：字符串排序会把 `3.9` 排到 `3.13` 后面。

6. **🔴 `.vbs` 必须保持 ASCII-only + CRLF。**
   `wscript` 在无 BOM 时会把 UTF-8 中文解错，LF 换行会让 VB 解析器直接报错。
   改这个文件时**注释一律写英文**，别顺手加中文。
   检查方法：读 bytes 看有没有非 ASCII 字节、`\r\n` 数量是否等于行数。

7. **清理备份要「先清后写 + 按超出量分档」。**
   先写新备份再清理的话，目录会有瞬间峰值；而**一次删几十个文件**在带安全策略的
   环境里会被判定为批量删除并拦下，表现是整个写操作失败（用户只看到「改名没反应」）。
   `PRUNE_HARD_CAP = 16` 刻意离常见阈值（50）留足距离。
   所以 `keep_backups` 不建议配太大 —— 它唯一的用途是「手滑了能退几步」，不是归档。

8. **首次运行的引导要讲人话。**
   别人拿到工具最常见的两种「打开没反应」是：没跑过 WorkBuddy（数据为空）、
   Python 太老。`scan.py` 的 `first_run_check()` 会明确告诉他是哪种，
   而不是让人对着空页面猜。别把这段删了。

9. **输出目录不存在时要给人话，不要甩堆栈。**
   入口是双击 `.bat`，用户看不到命令行；崩在堆栈里就是窗口一闪而过。
   `scan.py:render()` 已经处理，改的时候别退回裸 `open()`。

10. **Bash 工具在本机要手动 export PATH**，否则 `grep` / `dirname` / `sleep`
    会 `command not found` —— 更危险的是写成 `grep ... || echo "(无)"` 时
    兜底照样输出，**检查根本没跑却看着像通过了**。

11. **🔴 「静音」不等于「跳过准备入口」。**
    `scan.py --quiet` 原本把 `first_run_check()` 整个跳过 —— 本意是
    「不打印引导」，实际连**桌面图标和启动器都不建了**。用户事后想双击，
    发现压根没图标。现在 `--quiet` 分支里仍会 `_ensure_launchers(quiet=True)`
    + `_ensure_shortcut(quiet=True)`，只是不吭声。
    **加静音开关时，先分清「别说话」和「别干活」。**

12. **🔴 桌面快捷方式只建一次，不能每次刷新都重建。**
    用户可能改过图标、改过名字、挪过位置。每次都覆盖等于
    「你刚调好的东西被程序擦掉了」。`_ensure_shortcut()` 先查
    `os.path.exists(lnk)`，有就返回。
    另外建完要**回读确认文件真的出现了** —— `cscript` 可能返回 0 但没写成功
    （沙箱拦截时就是这样，退出码骗人）。

13. **🔴 静默生成的路径统一走 `main_quiet()`。**
    `make_shortcut.py` 原来只有 `main()`（argparse + 打印），程序内部想调用
    就只能再起一个进程，或者硬凑 `argparse.Namespace`。
    → 拆出 `main_quiet()` 返回退出码、不打印；`main()` 复用它。
    `make_launchers.write_all(quiet=True)` 同理。**给内部调用留一个口子。**

14. **🔴 「市场货架」不等于「已安装」—— 数字会虚高一倍。**
    扩展页的数据来自 `~/.workbuddy/plugins/` 下**两个不同的目录**：

    ```
    cache/<市场>/<名字>/<版本>/       ← 真的装了（installPath 指这里）
    marketplaces/<市场>/plugins/<名字>/ ← 货架，能下载但没装
    ```

    实测本机：货架上 98 个包，**真正装了的只有 10 个**。
    早前一视同仁全算「已安装」，总数从 95 虚报到 183 —— 用户看到
    「我装了 110 个技能」，其中一大半根本没装。
    → 市场目录扫到的条目标 `installed=False`，页面默认只看已安装的。
    **但也不能整个不扫**：用户自己做的专家包只在 `marketplaces/` 下
    （`experts/custom/*/experts.json` 里登记了名字，实体在那儿），
    跳过会让用户以为「我的专家没扫出来」。

15. **🔴 内置包的 `agents/` 目录不代表它是「专家」。**
    `welcomemode-code`、`interactionmode-ask`、`tencent-docx` 这些内置包
    底下都有 `agents/`，但那是「欢迎模式的根 agent」「文档工具的子代理」。
    照 agents 数量判类型的话，`tencent-docx`（3 个子代理）会被标成「专家团」——
    荒谬。→ **内置包（`workbuddy-builtin`）一律按名字前缀判**
    （`skill-` → 技能、`mcp-` → 连接器、其余 → 内置功能），
    只有市场包才用 agents 判定。

16. **🔴 「专家」和「专家团」用 agents 数量区分，不是看名字里有没有 team。**
    实测判据：包内 `agents/` 里 .md 文件个数，**1 个 = 专家，多个 = 专家团**
    （`ym-opd-dev-team` 有 11 个角色文件）。名字带不带 team 完全不作数 ——
    有些团队包名字里根本没 team。

17. **🔴 新增视图要接六处线，漏一处就是「静默半生效」。**
    以「扩展」页为例，缺任何一处都会出现「点了没反应 / 工具条空白 / 列表不画」：
    ① 侧栏 `<button data-view=…>` + 计数元素 ② `VIEW_TITLE` 词条
    ③ `renderList()` 里的分支 ④ `buildBar()` 里的工具条
    ⑤ `blankFilter()` 加筛选字段 + `FILTER_KEYS` 注册 ⑥ `syncBar()` 同步值。
    → selftest 里有一条专门钉这个，**加视图时照抄那组断言**。

18. **🔴 表头点击排序：不同视图的列名是两套，别套用同一张切换表。**
    任务页列名是 `name` / `created`…，扩展页是 `pname` / `pkind`…。
    事件委托里那段「点同一列 = 切换升/降」的表是照着任务页写死的，
    扩展页套上去会把 `pname` 切成 `"updated"` —— 而扩展页没这个选项，
    表现是「点了表头没反应」。→ 按 `state.view` 分流，
    并让 `sortPlugins()` 自己决定方向（时间类倒序、其余升序）。

19. **🔴 下拉框的选中项要在渲染时就标 `selected`。**
    `syncBar()` 只在「值不一样」时才写，而它默认值恰好也是第一项，
    写不写一个样 —— 一旦时序有偏差就会出现「下拉是空的」。
    生成 `<option>` 时就把 `state.sort === x[0]` 标成 `selected` 最稳。

20. **🔴 拖列宽要「抢先接管 + 吞掉后随 click」，两条缺一不可。**
    手柄在 `th` 里面，而 `th[data-sortkey]` 是点表头排序。
    - 拖拽在 `mousedown` 阶段就起手（别等 click），否则松手那一刻先排序。
    - 拖完浏览器**还会补一次 click**，这一下会被当成「点表头排序」。
      `stopPropagation()` 挡不住 —— 同一元素上后注册的 click 照样跑。
      用一次性标记 `SUPPRESS_CLICK`，在**捕获阶段**把它吃掉。
    - 宽度存**百分比**不存像素（`(w / tableWidth) * 100`），
      换个分辨率的屏幕打开才不会错位。

21. **🔴 行点击展开必须排除行内控件。**
    行是 `<tr data-row="id">`，用 `closest("tr[data-row]")` 做委托，
    但**先排除** `button,a,.pencil,[data-reopen],[data-edit],[data-copy],[data-open],[data-reveal],input,select,.rsz,.badge-soft`。
    漏一个的表现是「点铅笔改名，结果顺带把行展开了」——
    而且展开后整行高度变了，铅笔的位置跟着移，看着像改名失败。

22. **🔴 列宽用 `<colgroup><col>` 承载，不要给每个 th/td 写 style。**
    写 style 的做法在「重新渲染列表」时全部丢失，而且 `<td>` 上的宽度
    会和 `table-layout: fixed` 的百分比列宽打架。
    `applyColWidths()` 在每次 `renderList()` 之后重放；`colsHtml()` 没有自定义宽度时
    返回空串是**设计如此**（回落到 CSS 的默认百分比）。

23. **🔴 前端输入控件区域的打字路径不许走 `render()`。**
    工具条是 `innerHTML` 重建的。打一个字 → input 事件 → `render()` →
    `bar.innerHTML = h` → 输入框被销毁重建 → 光标没了、输入法组字上下文没了。
    表现是「刚敲一个键，字就自己跳走了」。
    → 工具条只画一次，`renderList()` 只碰 `#main`，不碰 `#bar`。
    页面内（`#main` 里）的控件则在 `renderList()` 末尾用 `bindInMain()` 重绑。

24. **🔴 改完 Python 后端必须重启服务进程 —— 这是本项目最坑的一条。**
    `serve.py` 是常驻进程，**它启动那一刻就把代码装进内存了**。
    你改了 `data.py` / `serve.py` 加了新路由，浏览器刷新页面拿到的是**新页面**，
    但服务端还是**旧逻辑**，于是新接口全是 404 纯文本 `not found`。
    用户看到的就是那句 `SyntaxError: Unexpected token 'o'`（见上一节）。
    → 改完后端就 `python scripts/killer.py` 停掉、再重启。
    **判断依据不要靠肉眼**：直接 `curl` 那个新接口，看是不是 200 + 合法 JSON。

25. **🔴 新增「资料库页签」这类分组时，注意它和 `library_roots` 的策略是相反的。**
    `library_roots()` 是**用户配的**，失效必须返回并标 `missing`（要让人看见「配错了」）；
    `library_groups()` 是**自动分组**，不存在的目录**不返回**（本机没有的东西
    不该出现在页签里，点了报错只会让人困惑）。这两条搞混了，要么页签里塞满
    打不开的入口，要么用户配错的目录悄悄消失。

## 自检

```bash
python scripts/selftest.py            # 全部 399 项
python scripts/selftest.py --quick    # 跳过要起服务的部分
```

覆盖：14 个模块编译与导入、配置层容错（脏 JSON / 布尔端口 / 相对路径）、
Python 解释器查找与版本排序、环境体检、控制台编码、数据层（含中文名、版本比较、
资料库分组）、写操作参数校验与幂等性、服务只读/写接口全链路、白名单安全用例、
模板 JS 语法与深链、**页面 JS 真跑（DOM 桩，含资料库页签与非 JSON 兜底）**、发布合规。

**改完任何代码都跑一遍。** 退出码 0 才算是好的。

### 页面 JS 真跑（`_domtest/run.js`）

`t_template_js()` 只做**文本**检查：「源码里有没有这句话」。它挡得住误删，
挡不住「代码在、但点下去没反应」。实际踩过三个静态检查全绿却功能坏掉的坑：

- `renderList()` 改了视图判断，10 个视图渲染出**同一份**内容；
- 静态快照页 `SERVER=false`，账户/在线分支被短路成提示语 —— 没起服务看着正常，起了服务反而空白；
- 列宽 `colgroup` 没渲染 —— 拖拽看着生效，一刷新回原样。

所以另做一层：抽 `index.html` 里的 `DATA` 常量与内联 script，
用 `node:vm` 在最小 DOM 桩里**真跑**，事件用 `document._fire()` 手工投递。
由 `selftest.py:t_dom()` 调用，是自检的一部分。

写这类用例时的三条硬规矩：

1. **必须 `ctx.__SERVER__ = true`**。不然账户/在线/安装分支全被静态页短路，
   测出来永远是 73 字节的提示语。
2. **走真实入口，别直接改 `state`**。`const state` 是词法声明，外部写 `ctx.state.view`
   无效 —— 不报错，静默渲染同一个视图，测了等于没测。正确做法是造 `data-view` 假导航
   按钮投递 click。
3. **fetch 桩要在任何导航之前装好**。`renderAccount()` 的自动加载条件是
   `!ACCT.data && !ACCT.loading && !ACCT.err` —— 一旦错误态写进去就锁住重试，
   后面所有账户用例都会看到那个错误。早期用 while 忙等也不对：忙等期间微任务不执行，
   要 `await`。

`run.js` 是**自包含**的（DOM 桩内联在同一个文件），复制这一个文件就能在别处复现，
也避免「桩改了、用例没改」的漂移。断言数 ≥30 条由 selftest 守着，防止用例被删空还亮绿灯。
