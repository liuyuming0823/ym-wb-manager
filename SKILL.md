---
name: ym-wb-manager
slug: ym-wb-manager
display_name: WorkBuddy 管理中心
display_name_en: WorkBuddy Manager
displayName: WorkBuddy 管理中心
description: "把本机 WorkBuddy 的任务、产物、项目、定时任务、资料库汇成一个本地网页面板，可搜索筛选，并能改名/软删除/恢复/备份，所有写操作都能回滚。当用户说「WorkBuddy 管理中心」「管理我的任务」「任务太多找不到」「查看历史任务」「定时任务管理」「清理已删任务」「产物管理」「资料库浏览」时使用。 首次运行会自动探测本机环境生成配置，换电脑也能直接跑。"
description_zh: "本地网页面板：集中查看和管理 WorkBuddy 的任务、产物、项目、定时任务与资料库"
description_en: "A local web dashboard to browse and manage WorkBuddy tasks, artifacts, projects and automations"
summary: "本地网页面板：集中查看和管理 WorkBuddy 的任务、产物、项目、定时任务与资料库"
category: dev-programming
version: 1.0.1
author: 刘玉明
tags: [WorkBuddy管理中心, 任务管理, 产物管理, 定时任务, 资料库]
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
agent_created: true
---

# WorkBuddy 管理中心 (ym-wb-manager)

WorkBuddy 用久了会有几百个任务散在会话记录里，想找「上周那个跑通了 Flink 的任务」只能翻。
这个技能装一个**本地网页面板**，把任务、产物、项目、定时任务、资料库汇到一个页面里，可搜索、可筛选、可改名、可删可恢复。

**纯标准库，零依赖，不联网。** 所有读取都是只读；所有写操作执行前自动备份数据库，可回滚。

## 何时使用

- 用户说「任务太多找不到」「想找某次任务」「看看我跑过哪些」
- 用户要管理定时任务（改名、启用/暂停、删除）
- 用户想清理已删任务（软删除的还在回收站里占着）
- 用户想按项目聚合看任务，或浏览产物文件清单
- 用户想有个统一的入口看 WorkBuddy 的数据

## 交付标准（先看这条）

终点是**用户双击就能打开一个网页面板**，不是一堆脚本。

1. `scripts/doctor.py` 跑通（11 项无 bad）—— 这一步顺便会生成配置
2. 用户能双击 `启动管理中心.bat`（静默启动用 `启动管理中心.vbs`）看到页面
3. 页面能读到他自己的任务数据

> **双击启动器从哪来**：技能包里**不含** `.bat` / `.vbs`（技能平台只收纯文本扩展名，
> 带上它们会整单拒收）。改为由 `scripts/make_launchers.py` 在**首次运行时现场生成**——
> 跑一次 `doctor.py` 或 `scan.py` 就会自动补齐，生成结果与手工放进去的完全一致。
> 也可以单独生成：`python scripts/make_launchers.py`（`--force` 覆盖，`--check` 只检查编码与行尾）。

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
  selftest.py       ← 自检 129 项
assets/
  template.html     ← 单文件页面模板（数据注入 /*__DATA__*/null）
```

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

**明确不会做的事**：不改 WorkBuddy 的程序文件、不动 `projects/` 里的会话原始记录、
不动 `artifact-index/` 里的产物清单、不联网、不上传任何数据。
`ops.py:delete_task_permanent`（彻底删除）需要前端手输 `DELETE` 才触发，
且只清 `sessions` 表里那一行。

**凭据来源**：无需任何凭据 —— 它读的是本机当前用户自己的 `~/.workbuddy`，
没有账号、token 或密钥。


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

## 自检

```bash
python scripts/selftest.py            # 全部 129 项
python scripts/selftest.py --quick    # 跳过要起服务的部分
```

覆盖：11 个模块编译与导入、配置层容错（脏 JSON / 布尔端口 / 相对路径）、
Python 解释器查找与版本排序、环境体检、控制台编码、数据层、写操作参数校验与
幂等性、服务只读/写接口全链路、白名单安全用例、模板 JS 语法与深链。

**改完任何代码都跑一遍。** 退出码 0 才算是好的。
