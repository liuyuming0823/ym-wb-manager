#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
WorkBuddy 管理中心 · 数据层
============================
统一采集 WorkBuddy 本机的各类数据：任务、项目、定时任务、资料库、总览统计。

所有读取均为只读；写操作集中在 ops.py。

数据来源：
  1. ~/.workbuddy/workbuddy.db
     - sessions        任务（任务名/路径/时间/状态/模式/专家）
     - session_usage   token 用量与积分
     - automations     定时任务
     - automation_runs 定时任务执行记录
     - workspaces      项目（最近打开的工作目录）
  2. ~/.workbuddy/artifact-index/<会话ID>.json   产物清单、任务清单
  3. ~/.workbuddy/projects/<编码路径>/<会话ID>.jsonl  首条提问、消息数
"""

import glob
import json
import os
import re
import sqlite3
import sys
from datetime import datetime, timezone
from urllib.parse import unquote, urlparse

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import config as conf  # noqa: E402

# ---- 路径一律从 config 取，本模块不自己拼盘符或用户名 ----
#
# 这些原本是 HOME / WB_DIR / DB_PATH 三个模块级常量，写死了「数据目录就是 ~/.workbuddy」。
# 改成函数调用后有个额外好处：环境变量 WB_DIR 可以随时改变数据源，
# 自动测试就能在「不碰真实数据」的前提下跑完整流程。
#
# 下面三个别名是为了兼容既有代码里大量 `datalayer.DB_PATH` 式的引用，
# 它们在同一进程内看起来就像常量一样好用。


def _paths():
    return (conf.wb_dir(), conf.db_path(), conf.artifact_dir(),
            conf.projects_dir())


HOME = os.path.expanduser("~")
WB_DIR = None          # 见 __getattr__（惰性求值，避免 import 时就固定下来）
DB_PATH = None
ARTIFACT_DIR = None
PROJECTS_DIR = None


def _refresh_paths():
    """把 config 里的路径同步到本模块的模块级变量。

    别的模块（ops / serve）在 import 时就会取 DB_PATH，所以必须有个明确的
    刷新入口，而不是让它悄悄变。
    """
    global WB_DIR, DB_PATH, ARTIFACT_DIR, PROJECTS_DIR
    WB_DIR, DB_PATH, ARTIFACT_DIR, PROJECTS_DIR = _paths()


_refresh_paths()


# ============================================================ 通用工具


def ts(ms):
    """毫秒时间戳 -> ISO 字符串（本地时区）。"""
    if ms in (None, "", "None"):
        return None
    try:
        v = int(ms)
    except (TypeError, ValueError):
        return None
    if v <= 0:
        return None
    if v > 1e11:
        v = v / 1000.0
    try:
        return datetime.fromtimestamp(v, tz=timezone.utc).astimezone().isoformat()
    except (OSError, OverflowError, ValueError):
        return None


def nn(v):
    """把 None / 字符串 'None' / 空串统一成 None。"""
    if v is None or v == "None" or v == "":
        return None
    return v


def ro_conn():
    """只读连接数据库。"""
    uri = "file:%s?mode=ro" % DB_PATH.replace("\\", "/").replace("?", "%3f").replace("#", "%23")
    conn = sqlite3.connect(uri, uri=True)
    conn.row_factory = sqlite3.Row
    return conn


def table_cols(conn, table):
    try:
        return [r[1] for r in conn.execute("PRAGMA table_info(%s)" % table)]
    except sqlite3.Error:
        return []


def safe_json(s, default=None):
    if not s:
        return default
    try:
        return json.loads(s)
    except (ValueError, TypeError):
        return default


# ============================================================ 任务


def load_sessions():
    if not os.path.exists(DB_PATH):
        sys.stderr.write("[data] 找不到 workbuddy.db\n")
        return {}
    conn = ro_conn()
    out = {}
    try:
        for row in conn.execute("SELECT * FROM sessions"):
            r = dict(row)
            sid = r.get("id")
            if not sid:
                continue
            title = nn(r.get("custom_title")) or nn(r.get("title")) or "(未命名任务)"
            out[sid] = {
                "id": sid,
                "title": title,
                "auto_title": nn(r.get("title")),
                "custom_title": nn(r.get("custom_title")),
                "cwd": nn(r.get("cwd")),
                "status": nn(r.get("status")),
                "mode": nn(r.get("mode")),
                "source_mode": nn(r.get("source_mode")),
                "model": nn(r.get("model")),
                "expert_id": nn(r.get("expert_id")),
                "permission_mode": nn(r.get("permission_mode")),
                "is_playground": str(nn(r.get("is_playground")) or "0") == "1",
                "is_automation": str(nn(r.get("is_background_automation")) or "0") == "1",
                "created_at": ts(r.get("created_at")),
                "updated_at": ts(r.get("updated_at")),
                "last_activity_at": ts(r.get("last_activity_at")),
                "deleted": r.get("deleted_at") not in (None, "", "None"),
                "deleted_at": ts(r.get("deleted_at")),
                "usage": None,
                "artifacts": [],
                "todos": [],
                "prompt": None,
                "ai_title": None,
                "message_count": 0,
                "jsonl_path": None,
                "jsonl_size": 0,
            }
    finally:
        conn.close()
    return out


def load_usage(sessions):
    if not os.path.exists(DB_PATH):
        return
    conn = ro_conn()
    try:
        for row in conn.execute("SELECT * FROM session_usage"):
            r = dict(row)
            sid = r.get("session_id")
            if sid not in sessions:
                continue
            credits = []
            for k, v in (safe_json(r.get("credit_json"), {}) or {}).items():
                try:
                    credits.append({"id": k, "amount": round(float(v), 4)})
                except (TypeError, ValueError):
                    pass
            credits.sort(key=lambda x: -x["amount"])
            try:
                used = int(r.get("used") or 0)
                size = int(r.get("size") or 0)
            except (TypeError, ValueError):
                used = size = 0
            sessions[sid]["usage"] = {
                "tokens_used": used,
                "context_size": size,
                "credits": credits,
                "credits_total": round(sum(c["amount"] for c in credits), 4),
                "updated_at": ts(r.get("updated_at")),
            }
    finally:
        conn.close()


def file_uri_to_path(uri):
    if not uri:
        return None
    if uri.startswith("file-changes://"):
        return unquote(uri[len("file-changes://"):])
    if uri.startswith("agent://"):
        return None
    if uri.startswith("file://"):
        p = urlparse(uri)
        path = unquote(p.path)
        if len(path) > 2 and path[0] == "/" and path[2] == ":":
            path = path[1:]
        return path.replace("/", "\\")
    return None


def load_artifacts(sessions):
    if not os.path.isdir(ARTIFACT_DIR):
        return
    for fp in glob.glob(os.path.join(ARTIFACT_DIR, "*.json")):
        sid = os.path.basename(fp)[:-5]
        s = sessions.get(sid)
        if s is None:
            continue
        data = None
        try:
            with open(fp, "r", encoding="utf-8") as fh:
                data = json.load(fh)
        except (OSError, ValueError):
            continue
        items, todo_lists = [], []
        for a in data.get("artifacts", []):
            atype = a.get("type")
            if atype == "tasks":
                tasks = [{"content": t.get("content"), "status": t.get("status")}
                         for t in a.get("tasks", [])]
                if tasks:
                    todo_lists.append(tasks)
                continue
            if atype == "file-changes":
                info = a.get("fileChangeInfo") or {}
                path = info.get("uri") or file_uri_to_path(a.get("uri"))
                items.append({
                    "type": "change",
                    "name": a.get("name") or (os.path.basename(path) if path else ""),
                    "path": path,
                    "exists": bool(path and os.path.exists(path)),
                    "change_type": info.get("changeType"),
                    "size": None,
                    "content_type": "diff",
                    "source_tool": (a.get("_meta") or {}).get("sourceTool"),
                    "created_at": ts(a.get("createdAt") or a.get("lastUpdated")),
                })
                continue
            path = file_uri_to_path(a.get("uri"))
            size = a.get("size")
            items.append({
                "type": atype,
                "name": a.get("name") or a.get("title") or "",
                "title": a.get("title"),
                "path": path,
                "exists": bool(path and os.path.exists(path)) if path else None,
                "size": size if isinstance(size, int) else None,
                "content_type": a.get("contentType"),
                "mime": a.get("mimeType"),
                "source_tool": (a.get("_meta") or {}).get("sourceTool"),
                "created_at": ts(a.get("createdAt") or a.get("lastUpdated")),
            })
        seen = {}
        for it in items:
            seen[(it.get("path") or it.get("name"), it.get("type"))] = it
        items = sorted(seen.values(), key=lambda x: (x.get("created_at") or ""), reverse=True)
        s["artifacts"] = items
        s["todos"] = todo_lists[-1] if todo_lists else []


def _extract_text(content):
    if isinstance(content, str):
        return _strip_system(content)
    if isinstance(content, list):
        buf = []
        for blk in content:
            if isinstance(blk, dict) and blk.get("type") in ("text", "input_text"):
                buf.append(blk.get("text", ""))
            elif isinstance(blk, str):
                buf.append(blk)
        return _strip_system("\n".join(buf))
    return ""


def _strip_system(text):
    if not text:
        return ""
    cleaned = re.sub(r"<system-reminder\b[^>]*>.*?</system-reminder>", " ", text, flags=re.S | re.I)
    for tag in ("identity_context", "project_context", "user_info", "additional_data"):
        cleaned = re.sub(r"<%s\b[^>]*>.*?</%s>" % (tag, tag), " ", cleaned, flags=re.S | re.I)
    cleaned = re.sub(r"<[^>]{1,80}>", " ", cleaned)
    cleaned = re.sub(r"^\s*The user (wants|asked)[^.]*\.\s*", "", cleaned, flags=re.I)
    if not cleaned.strip():
        return re.sub(r"\s+", " ", re.sub(r"<[^>]{1,120}>", " ", text)).strip()[:200]
    return re.sub(r"\s+", " ", cleaned).strip()


def load_jsonl_meta(sessions):
    if not os.path.isdir(PROJECTS_DIR):
        return
    found = {}
    for proj in os.listdir(PROJECTS_DIR):
        pd = os.path.join(PROJECTS_DIR, proj)
        if not os.path.isdir(pd):
            continue
        for fp in glob.glob(os.path.join(pd, "*.jsonl")):
            sid = os.path.basename(fp)[:-6]
            if sid in sessions:
                found[sid] = fp

    for sid, fp in found.items():
        s = sessions[sid]
        s["jsonl_path"] = fp
        try:
            s["jsonl_size"] = os.path.getsize(fp)
            with open(fp, "r", encoding="utf-8", errors="replace") as fh:
                for i, line in enumerate(fh):
                    if i > 200:
                        break
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        obj = json.loads(line)
                    except ValueError:
                        continue
                    s["message_count"] += 1
                    if obj.get("type") == "ai-title" and obj.get("aiTitle"):
                        s.setdefault("ai_title", obj["aiTitle"])
                        continue
                    if obj.get("role") != "user" or s["prompt"]:
                        continue
                    text = " ".join(_extract_text(obj.get("content")).split())
                    if text:
                        s["prompt"] = text[:800]
                    if not s.get("first_user_at") and obj.get("timestamp"):
                        s["first_user_at"] = ts(obj.get("timestamp"))
        except OSError:
            pass


# ============================================================ 项目


# 临时任务目录的特征：WorkBuddy\<日期时间> 或 <日期>-task-N
_TEMP_DIR_RE = re.compile(
    r"[\\/]WorkBuddy[\\/](\d{8,}|\d{4}-\d{2}-\d{2}(-\d{2}-\d{2}-\d{2})?|\d{4}-\d{2}-\d{2}-task-\d+)$",
    re.I,
)


def is_temp_dir(path):
    """判断是否为 WorkBuddy 自动创建的临时任务目录（这类不算「项目」）。"""
    if not path:
        return False
    p = path.rstrip("\\/")
    if _TEMP_DIR_RE.search(p):
        return True
    # 形如 ...\WorkBuddy\2026-09-20-09-10-47
    base = os.path.basename(p)
    if re.fullmatch(r"\d{8,}", base) or re.fullmatch(r"\d{4}-\d{2}-\d{2}-\d{2}-\d{2}-\d{2}", base):
        parent = os.path.basename(os.path.dirname(p)).lower()
        if parent in ("workbuddy", "workbuddy", "claw"):
            return True
    if re.fullmatch(r"\d{4}-\d{2}-\d{2}-task-\d+", base):
        return True
    return False


def load_projects(sessions):
    """项目 = 非临时的真实工作目录。含 workspaces 记录的 + 从任务聚合的。"""
    projects = {}

    def ensure(p):
        if p not in projects:
            projects[p] = {
                "path": p, "last_opened_at": None,
                "task_count": 0, "artifact_count": 0, "credits": 0.0, "tokens": 0,
                "last_task_at": None, "exists": os.path.isdir(p), "is_temp": is_temp_dir(p),
                "pinned": False, "top_level_bytes": None, "child_count": None,
            }
        return projects[p]

    if os.path.exists(DB_PATH):
        conn = ro_conn()
        try:
            for row in conn.execute("SELECT * FROM workspaces"):
                p = nn(row["path"])
                if p:
                    ensure(p)["last_opened_at"] = ts(row["last_opened_at"])
        finally:
            conn.close()

    for t in sessions.values():
        cwd = t.get("cwd")
        if not cwd:
            continue
        p = ensure(cwd)
        p["task_count"] += 1
        p["artifact_count"] += len(t.get("artifacts") or [])
        u = t.get("usage") or {}
        p["credits"] += u.get("credits_total") or 0
        p["tokens"] += u.get("tokens_used") or 0
        ref = t.get("updated_at") or t.get("created_at")
        if ref and (not p["last_task_at"] or ref > p["last_task_at"]):
            p["last_task_at"] = ref

    # 目录信息
    for p in projects.values():
        p["credits"] = round(p["credits"], 2)
        if p["exists"]:
            try:
                children = os.listdir(p["path"])
                p["child_count"] = len(children)
                total = 0
                for name in children:
                    fp = os.path.join(p["path"], name)
                    try:
                        if os.path.isfile(fp):
                            total += os.path.getsize(fp)
                    except OSError:
                        pass
                p["top_level_bytes"] = total
            except OSError:
                pass

    out = list(projects.values())
    out.sort(key=lambda x: ((not x["is_temp"]),
                            -(x["task_count"] or 0),
                            x.get("last_task_at") or ""), reverse=False)
    # 稳定排序：先按是否临时，再按任务数降序，再按最近活动降序
    out.sort(key=lambda x: (x["is_temp"], -(x["task_count"] or 0)))
    return out


def count_projects(projects):
    """真正的项目数（排除临时目录）。"""
    return sum(1 for p in projects if not p["is_temp"])


# ============================================================ 定时任务


def load_automations():
    if not os.path.exists(DB_PATH):
        return []
    conn = ro_conn()
    out = []
    try:
        for row in conn.execute("SELECT * FROM automations"):
            r = dict(row)
            cwds = safe_json(r.get("cwds"), [])
            if not isinstance(cwds, list):
                cwds = [str(cwds)] if cwds else []
            out.append({
                "id": r.get("id"),
                "name": r.get("name"),
                "prompt": (r.get("prompt") or "")[:400],
                "status": r.get("status"),
                "schedule_type": r.get("schedule_type"),
                "rrule": r.get("rrule") or "",
                "scheduled_at": r.get("scheduled_at"),
                "next_run_at": ts(r.get("next_run_at")),
                "last_run_at": ts(r.get("last_run_at")),
                "cwds": cwds,
                "model_id": nn(r.get("model_id")),
                "created_at": ts(r.get("created_at")),
                "expert_id": nn(r.get("expert_id")),
                "deleted": r.get("deleted_at") not in (None, "", "None"),
                "deleted_at": ts(r.get("deleted_at")),
                "runs": [],
                "run_count": 0,
            })
    finally:
        conn.close()

    by_id = {a["id"]: a for a in out}
    # 执行记录
    conn = ro_conn()
    try:
        for row in conn.execute("SELECT * FROM automation_runs"):
            r = dict(row)
            aid = r.get("automation_id")
            a = by_id.get(aid)
            if a is None:
                continue
            runs = safe_json(r.get("runs_json"), []) or []
            detail = []
            for run in runs if isinstance(runs, list) else []:
                if not isinstance(run, dict):
                    continue
                detail.append({
                    "cwd": run.get("cwd"),
                    "success": run.get("success"),
                    "started_at": ts(run.get("startedAt")),
                    "finished_at": ts(run.get("finishedAt")),
                })
            a["runs"].append({
                "thread_id": r.get("thread_id"),
                "title": r.get("thread_title"),
                "status": r.get("status"),
                "success": r.get("result_success"),
                "source_cwd": r.get("source_cwd"),
                "created_at": ts(r.get("created_at")),
                "updated_at": ts(r.get("updated_at")),
                "detail": detail,
            })
    finally:
        conn.close()

    for a in out:
        a["runs"].sort(key=lambda x: (x.get("updated_at") or ""), reverse=True)
        a["run_count"] = len(a["runs"])
    out.sort(key=lambda x: (x.get("last_run_at") or x.get("created_at") or ""), reverse=True)
    return out


# ============================================================ 资料库

# 资料库的根目录一律从 config.json 读（首次运行会自动探测生成）。
#
# 早前这里是写死的五条本机绝对路径（D:\workbuddy\创建和更新知识库、
# D:\liuyuming\GitProject 等）。分享给别人时这些路径根本不存在，
# 页面会显示一堆点了就报错的入口 —— 所以改成配置驱动。
# 具体实现见下面的 library_roots()。


def existing_library_roots():
    """只保留真实存在的目录，供文件浏览使用。"""
    return conf.existing_library_roots()


def list_dir(path, limit=500):
    """列目录（一层）。返回条目与统计。"""
    if not path or not os.path.isdir(path):
        return {"ok": False, "error": "目录不存在", "entries": []}
    entries = []
    try:
        names = sorted(os.listdir(path), key=lambda n: (not os.path.isdir(os.path.join(path, n)), n.lower()))
    except OSError as exc:
        return {"ok": False, "error": str(exc), "entries": []}

    for name in names[:limit]:
        fp = os.path.join(path, name)
        try:
            is_dir = os.path.isdir(fp)
            st = os.stat(fp)
            entries.append({
                "name": name,
                "path": fp,
                "is_dir": is_dir,
                "size": None if is_dir else st.st_size,
                "mtime": ts(st.st_mtime * 1000),
                "ext": (os.path.splitext(name)[1].lower() if not is_dir else ""),
            })
        except OSError:
            continue

    # 父目录
    parent = os.path.dirname(path.rstrip("\\/"))
    return {
        "ok": True,
        "path": path,
        "parent": parent if parent and parent != path else None,
        "entries": entries,
        "truncated": len(names) > limit,
        "total": len(names),
    }


def library_roots():
    """返回资料库根目录（含失效项），并补充目录统计信息。

    路径来自 config.json（首次运行自动探测生成），不再写死在代码里。
    失效的目录也会返回，但带 missing=True，让页面能明确提示而不是静默消失。
    """
    out = []
    for r in conf.library_roots():
        info = dict(r)
        if r.get("missing"):
            info["child_count"] = 0
            info["top_bytes"] = 0
            out.append(info)
            continue
        try:
            children = os.listdir(r["path"])
            info["child_count"] = len(children)
            total = 0
            for n in children:
                fp = os.path.join(r["path"], n)
                try:
                    if os.path.isfile(fp):
                        total += os.path.getsize(fp)
                except OSError:
                    pass
            info["top_bytes"] = total
        except OSError:
            info["child_count"] = 0
            info["top_bytes"] = 0
        out.append(info)
    return out


# ============================================================ 资料库分页签
#
# 为什么要有这一层 —— 原来「资料库」首页只有几张根目录卡片，
# 而 WorkBuddy 自己的东西（技能、产物、方案、记忆…）全塞在
# 「WorkBuddy 数据」这一张卡后面，得点进去再一层层翻找。
# 用户的反馈很直接：「原来 workbuddy 里的资料在哪里了呢」。
#
# 所以按**资料类型**分组：技能 / 专家 / 连接器 / 任务 / 产物 / 方案 / 记忆 /
# 日志 / 系统。每组一个页签，进了就能直接看到，不用先猜该点哪张卡。
#
# 🔴 分组是「视图」，不是新的权限：每个入口都指向真实存在的目录，
# 浏览仍然走 /api/ls 的白名单校验。这里只负责**列出来**，不放宽任何访问限制。

# (组名, 说明, [(显示名, 相对 workbuddy_dir 的路径, 说明, 统计什么)])
#
# count 字段决定卡片上显示什么数量：
#   "children" —— 直接子项个数（技能目录 53 个 = 53 个技能）
#   "files"    —— 递归数文件个数（产物索引里每个 json 是一次任务的产物）
#   None       —— 只显示目录大小/不统计（如 cache 这种没必要数的）
_LIB_GROUPS = [
    ("skill", "技能与专家", "自己装的和从市场下的技能、专家、专家团", [
        ("已装技能", "skills", "skills/ 下每个目录是一个技能", "children"),
        ("专家与专家团", "experts", "自定义专家与团队", "children"),
        ("插件", "plugins", "已安装插件与本地市场缓存", "children"),
        ("连接器", "connectors", "已连接的 MCP 服务", "children"),
        ("MCP 配置", "mcp-servers", "手写的 MCP 服务目录", "children"),
        ("市场缓存", "skills-marketplace", "市场货架缓存（不一定装了）", "children"),
        ("技能备份", "skill-backups", "安装覆盖前的自动备份", "children"),
    ]),
    ("data", "任务与产物", "会话记录、产物清单、定时任务", [
        ("任务会话", "projects", "每个目录一个工作区，内含会话 jsonl", "children"),
        ("产物索引", "artifact-index", "每个文件对应一次任务的产物清单", "files"),
        ("任务数据", "tasks", "任务运行数据", "children"),
        ("会话元数据", "sessions", "会话与工作目录登记", "children"),
        ("变更记录", "changes-index", "文件改动索引", "children"),
        ("变更详情", "changes-detail", "文件改动的完整 diff", "children"),
        ("自动化备份", "automation-backups", "定时任务改动前的备份", "children"),
    ]),
    ("docs", "文档与方案", "方案、记忆、灵感", [
        ("方案", "plans", "Plan 模式写下的方案", "children"),
        ("记忆", "memory", "MEMORY.md 与各项目的记忆", "children"),
        ("灵感", "inspiration", "灵感记录", "children"),
        ("文件历史", "file-history", "编辑器文件历史快照", "children"),
    ]),
    ("sys", "系统与外观", "日志、皮肤、剪贴板图片", [
        ("日志", "logs", "运行日志（排查问题看这里）", "children"),
        ("剪贴板图片", "clipboard-images", "截图粘贴产生的图片", "children"),
        ("外观资源", "appearance-resources", "主题与壁纸", "children"),
        ("皮肤", "wb-skin", "换肤生成的文件", "children"),
        ("文件存储", "storage", "本地文件存储", "children"),
        ("二进制块", "blobs", "内容寻址的二进制块", "children"),
    ]),
]


def _dir_count(abspath, mode):
    """数一个目录里的条目。失败一律返回 None（不显示数字，不报 0 误导人）。"""
    if not os.path.isdir(abspath):
        return None
    try:
        if mode == "children":
            return len(os.listdir(abspath))
        if mode == "files":
            n = 0
            for _root, _dirs, files in os.walk(abspath):
                n += len(files)
                if n > 9999:            # 大目录别把页面拖死
                    return n
            return n
    except OSError:
        return None
    return None


def library_groups():
    """把 WorkBuddy 数据目录按资料类型分组，供资料库页签使用。

    只收录**真实存在**的目录 —— 不存在的不返回，页面就不会出现点了报错的入口
    （这和 library_roots 的处理不同：那里失效项也返回并标 missing，
    是因为那是用户自己配的，得让他看见「配错了」；这里是自动分组的，
    本机没有的东西就不该出现）。
    """
    base = WB_DIR
    groups = []
    for gid, gname, gdesc, items in _LIB_GROUPS:
        entries = []
        for label, rel, desc, mode in items:
            fp = os.path.join(base, rel)
            if not os.path.isdir(fp):
                continue
            entries.append({
                "label": label,
                "path": fp,
                "rel": rel,
                "desc": desc,
                "count": _dir_count(fp, mode),
            })
        if entries:
            groups.append({"id": gid, "name": gname, "desc": gdesc,
                           "entries": entries})
    return groups


# ============================================================ 扩展（技能 / 专家 / 连接器）
#
# 这一块回答的问题是「我这台机器上到底装了什么」。
#
# 难点在于**这些数据散在五个地方，而且格式各不相同**：
#   1. plugins/installed_plugins.json  —— 官方登记的安装清单（内置 + 市场装的）
#   2. plugins/marketplaces/*/plugins/ —— 本地市场（自己做的专家包住这儿，
#      而且**不在** installed_plugins.json 里，只扫清单会漏掉）
#   3. ~/.workbuddy/skills/*/SKILL.md  —— 目录型技能，元数据在 frontmatter
#   4. ~/.workbuddy/mcp.json           —— 手写的 MCP 连接器
#   5. experts/custom/*/experts.json   —— 只登记了个名字的本地专家
#
# 所以这里做的事和「读一个表」完全不同：是把五份异构数据**归一成同一种
# 形状**，才能放进同一张列表里做筛选排序。
#
# 🔴 类型判定不能靠猜前缀。实测结论：
#   · 一个包里只要有 agents/ 目录，它就是专家；里面 1 个 .md 是**单个专家**，
#     多个 .md 是**专家团**（实测 ym-opd-dev-team 有 11 个角色文件）。
#     —— 这是唯一靠谱的专家/专家团判据，名字里有没有 team 不作数。
#   · 专家包带 skills/ 子目录很常见（如 wechat-channels-strategist），
#     但那是从属技能，主体仍是专家 → 所以 agents 的优先级要高于 skills。
#   · 插件元数据目录有两种名字：内置的用 .workbuddy-plugin，
#     市场/自制包用 .codebuddy-plugin。只认一种会读到一片空白。
#
# 任何一处读失败都只影响那一条，不能让整个列表空掉 —— 这是只读展示，
# 用户宁可看到「有 3 条信息不全」，也不能看到「页面打不开」。


# 元数据目录的两种名字（内置的 vs 市场/自制的）
_META_DIRS = (".workbuddy-plugin", ".codebuddy-plugin")

# 展示名（中文名）在包里可能出现在这几个键下，按优先级取第一个非空的。
# 为什么要有这么一串：明哥的技能同时写 display_name 和 displayName（SkillHub 与
# WorkBuddy 两套规范），市场包写 displayName，有的包什么都不写只有 slug。
# 只认一个键会漏掉一半。
_DISP_KEYS = ("display_name", "displayName", "display_name_zh", "displayNameZh",
              "title", "title_zh")


def _plugin_meta(path):
    """读一个插件包里的 plugin.json。读不到就返回空字典。"""
    for d in _META_DIRS:
        fp = os.path.join(path, d, "plugin.json")
        if not os.path.isfile(fp):
            continue
        try:
            with open(fp, "r", encoding="utf-8") as fh:
                j = json.load(fh)
            return j if isinstance(j, dict) else {}
        except (OSError, ValueError):
            continue
    return {}


def _has_cjk(s):
    """字符串里有没有中文字符。用来判断「这个名字对不对照着看」。"""
    return any("一" <= ch <= "鿿" for ch in (s or ""))


def _pick_display(meta, fm, slug):
    """从各种元数据里挑一个中文展示名。挑不到返回 ""。

    来源按可信度排序：
      1) 包自带的展示名字段（display_name / displayName / title …）
      2) _skillhub_meta.json 的 name（市场技能，实测是中文，如「12306 订票助手」）
      3) SKILL.md frontmatter 的同名字段
    英文展示名（AI Video Generation）也不丢 —— 它不是中文，但比 slug 好认，
    只有当它和标识完全一样时才认为是「没写」。
    """
    for src in (meta, fm):
        if not src:
            continue
        for k in _DISP_KEYS:
            v = src.get(k)
            # 有些包把 title 写成对象（{ "zh": "..." }），链式 .strip() 会炸
            if isinstance(v, dict):
                v = v.get("zh") or v.get("zh_CN") or v.get("en") or ""
            if not isinstance(v, str):
                continue
            v = v.strip()
            if v and v.lower() != (slug or "").lower():
                return v
    return ""


def _skillhub_meta(path):
    """读包目录里的 _skillhub_meta.json（市场渠道装下来的技能都有）。

    实测它长这样：{"name": "12306 订票助手", "slug": "...", "source": "marketplace"}
    —— name 是中文展示名，正是列表里缺的那一半信息。
    """
    fp = os.path.join(path, "_skillhub_meta.json")
    if not os.path.isfile(fp):
        return {}
    try:
        with open(fp, "r", encoding="utf-8") as fh:
            j = json.load(fh)
        return j if isinstance(j, dict) else {}
    except (OSError, ValueError):
        return {}


def _display_index():
    """扫一遍市场目录，建一份「标识 -> 中文名」的索引。

    为什么需要索引：已装包的目录里（plugins/cache/...）**没有** _skillhub_meta.json，
    中文名只存在于市场目录 skills-marketplace/skills/<slug>/ 下。
    不建索引的话，装好的技能反而查不到中文名 —— 正是要显示的那一批。
    """
    idx = {}
    roots = [
        os.path.join(WB_DIR, "skills-marketplace", "skills"),
        os.path.join(WB_DIR, "plugins", "marketplaces"),
    ]
    for root in roots:
        if not os.path.isdir(root):
            continue
        for dp, dn, fn in os.walk(root):
            depth = dp[len(root):].count(os.sep)
            if depth > 3:
                continue
            if "_skillhub_meta.json" not in fn:
                continue
            try:
                with open(os.path.join(dp, "_skillhub_meta.json"), "r",
                          encoding="utf-8") as fh:
                    j = json.load(fh)
            except (OSError, ValueError):
                continue
            if not isinstance(j, dict):
                continue
            nm = (j.get("name") or "").strip()
            if not nm:
                continue
            for key in (j.get("slug"), os.path.basename(dp)):
                if key and key not in idx:
                    idx[str(key).lower()] = nm
    return idx


def _read_text(path, limit=4000):
    """读文本文件的前几个字符（用于 SKILL.md / README 的描述兜底）。"""
    try:
        with open(path, "r", encoding="utf-8", errors="replace") as fh:
            return fh.read(limit)
    except OSError:
        return ""


def _frontmatter(path):
    """解析 SKILL.md 顶部的 YAML frontmatter。

    这里**不引第三方库**（项目是纯标准库），所以只做「够用」的解析：
    认 key: value，以及 `key: >-` / `key: |` 开头的多行块（description 常这么写）。
    解析不出来只是描述字段少一点，不影响列表出得来。
    """
    txt = _read_text(path)
    if not txt.startswith("---"):
        return {}
    end = txt.find("\n---", 3)
    if end < 0:
        return {}
    out = {}
    key = None
    buf = []
    for line in txt[3:end].splitlines():
        if not line.strip():
            continue
        # 续行：上一行是 >- / | 之类的块标量，或本行缩进
        if line[:1] in (" ", "\t") and key:
            buf.append(line.strip())
            continue
        if ":" in line:
            if key and buf:
                out[key] = " ".join(buf)
            key, _, val = line.partition(":")
            key = key.strip()
            val = val.strip()
            buf = []
            # 块标量起始符：真正的内容在下面几行
            if val in (">-", ">", "|", "|-", ">+", "|+"):
                continue
            # 去掉包裹的引号
            if len(val) >= 2 and val[0] == val[-1] and val[0] in "\"'":
                val = val[1:-1]
            out[key] = val
    if key and buf:
        out[key] = " ".join(buf)
    return out


def _classify(path, meta, marketplace):
    """判定一个包属于哪种扩展。返回 (kind, 附加说明)。

    kind 取值：expert（专家）/ team（专家团）/ skill（技能）/
              connector（连接器）/ plugin（内置功能）

    🔴 内置包不能靠 agents/ 判定。
    实测：welcomemode-code、interactionmode-ask、tencent-docx 这些内置包
    底下都有 agents/ 目录，但那是「欢迎模式的根 agent」「文档工具的子代理」，
    不是明哥认知里的「专家」。照 agents 数量判会把一堆内置功能误标成
    专家/专家团（tencent-docx 有 3 个子代理，会被判成「专家团」—— 荒谬）。
    所以内置包一律按名字前缀判，市场包才用 agents 判。
    """
    if marketplace == "workbuddy-builtin":
        nm = (meta.get("name") or os.path.basename(path) or "").lower()
        if nm.startswith("skill-"):
            return "skill", None
        if nm.startswith("mcp-"):
            return "connector", None
        return "plugin", None

    agents_dir = os.path.join(path, "agents")
    n_agents = 0
    if os.path.isdir(agents_dir):
        try:
            n_agents = sum(1 for n in os.listdir(agents_dir)
                           if n.lower().endswith(".md"))
        except OSError:
            n_agents = 0
    if n_agents > 1:
        return "team", "%d 个角色" % n_agents
    if n_agents == 1:
        return "expert", "1 个角色"
    if os.path.isfile(os.path.join(path, ".mcp.json")):
        return "connector", None
    if os.path.isdir(os.path.join(path, "skills")):
        return "skill", None
    if os.path.isfile(os.path.join(path, "SKILL.md")):
        return "skill", None
    # plugin.json 里写明带 skills 的（如 document-skills / finance-data）
    if meta.get("skills"):
        return "skill", None
    return "plugin", None


def _mk_entry(**kw):
    """补齐所有字段，保证前端拿到的每一条结构一致。"""
    base = {
        "id": "", "name": "", "display_name": "", "kind": "plugin", "kind_note": None,
        "source": "", "marketplace": "", "desc": "", "version": "",
        "author": "", "path": "", "installed_at": None, "updated_at": None,
        "keywords": [], "enabled": True, "scope": "", "agents": 0,
        "missing": False, "installed": True,
    }
    base.update(kw)
    return base


def _marketplace_label(mk):
    """marketplace 标识 -> 中文来源名。"""
    return {
        "workbuddy-builtin": "内置",
        "codebuddy-plugins-official": "官方市场",
        "cb_teams_marketplace": "团队市场",
        "experts": "专家市场",
        "my-experts": "我的专家",
        "local": "本地目录",
        "mcp": "MCP 配置",
    }.get(mk, mk or "其他")


def load_plugins():
    """采集本机已安装的全部扩展，归一成同一种结构返回列表。"""
    out = []
    wb = WB_DIR
    disp_idx = _display_index()

    # ---------- 1) 官方安装清单 ----------
    ins_path = os.path.join(wb, "plugins", "installed_plugins.json")
    try:
        with open(ins_path, "r", encoding="utf-8") as fh:
            ins = json.load(fh)
    except (OSError, ValueError):
        ins = {}
    for key, arr in (ins.get("plugins") or {}).items():
        if not isinstance(arr, list):
            arr = [arr]
        name, _, mk = key.partition("@")
        for e in arr:
            if not isinstance(e, dict):
                continue
            ip = e.get("installPath") or ""
            meta = _plugin_meta(ip) if ip else {}
            kind, note = _classify(ip, meta, mk)
            # 展示名优先用包里写的 name（可读），没有才退回标识名
            disp = meta.get("name") or name
            author = meta.get("author")
            if isinstance(author, dict):
                author = author.get("name") or ""
            out.append(_mk_entry(
                id=key,
                name=disp,
                # 中文名：包里写的没中文时，去市场索引里按标识捞一次
                display_name=_pick_display(meta, None, name)
                    or disp_idx.get(name.lower()) or disp_idx.get(
                        (ip or "").replace("\\", "/").rstrip("/").split("/")[-1].lower())
                    or "",
                kind=kind,
                kind_note=note,
                source=_marketplace_label(mk),
                marketplace=mk,
                desc=(meta.get("description") or meta.get("description_en") or ""),
                version=e.get("version") or meta.get("version") or "",
                author=author or "",
                path=ip,
                installed_at=nn(e.get("installedAt")),
                updated_at=nn(e.get("lastUpdated")),
                keywords=meta.get("keywords") or [],
                scope=e.get("scope") or "",
                missing=bool(ip) and not os.path.isdir(ip),
            ))

    # ---------- 2) 市场货架（可下载，不一定装了）----------
    # 🔴 这个目录是**货架不是已装列表**，两者差得远：实测本机货架上 98 个包，
    # 真正装了的只有 10 个。早前一视同仁全算进「已安装」，总数虚高到 183，
    # 明哥看到的「我装了 110 个技能」里有一大半根本没装 —— 宁可少报也不能虚报。
    #
    # 但也不能整个不扫：明哥自己做的专家包（ym-dev-team、tianwei-bid-expert）
    # 就住在这里，且没进 installed_plugins.json。完全跳过会让他以为
    # 「我的专家没扫出来」。
    #
    # 折中：照样收进来，但标 installed=False，页面上默认只看已装的，
    # 想看货架上的可以自己切 —— 事实准确，东西也不丢。
    seen_ids = {x["id"] for x in out}
    mkt_root = os.path.join(wb, "plugins", "marketplaces")
    try:
        markets = sorted(os.listdir(mkt_root))
    except OSError:
        markets = []
    for mk in markets:
        pdir = os.path.join(mkt_root, mk, "plugins")
        if not os.path.isdir(pdir):
            continue
        try:
            names = sorted(os.listdir(pdir))
        except OSError:
            continue
        for nm in names:
            fp = os.path.join(pdir, nm)
            if not os.path.isdir(fp):
                continue
            pid = "%s@%s" % (nm, mk)
            if pid in seen_ids:
                continue                      # 已装清单里有了，不重复
            seen_ids.add(pid)
            meta = _plugin_meta(fp)
            kind, note = _classify(fp, meta, mk)
            author = meta.get("author")
            if isinstance(author, dict):
                author = author.get("name") or ""
            try:
                st = os.stat(fp)
                mt = ts(st.st_mtime * 1000)
            except OSError:
                mt = None
            out.append(_mk_entry(
                id=pid,
                name=meta.get("name") or nm,
                display_name=_pick_display(meta, _skillhub_meta(fp), nm)
                    or disp_idx.get(nm.lower()) or "",
                kind=kind,
                kind_note=note,
                source=_marketplace_label(mk),
                marketplace=mk,
                desc=(meta.get("description") or ""),
                version=meta.get("version") or "",
                author=author or "",
                path=fp,
                updated_at=mt,
                keywords=meta.get("keywords") or [],
                scope="local",
                installed=False,      # 货架上的，不等于装了
            ))

    # ---------- 3) 本地目录型技能 ----------
    sk_root = os.path.join(wb, "skills")
    try:
        names = sorted(os.listdir(sk_root))
    except OSError:
        names = []
    for nm in names:
        d = os.path.join(sk_root, nm)
        sm = os.path.join(d, "SKILL.md")
        if not os.path.isfile(sm):
            continue                          # 备份目录 / 散落的 txt，不是技能
        pid = "local-skill:" + nm
        if pid in seen_ids:
            continue
        seen_ids.add(pid)
        fm = _frontmatter(sm)
        n_agents = 0
        if os.path.isdir(os.path.join(d, "agents")):
            try:
                n_agents = sum(1 for n in os.listdir(os.path.join(d, "agents"))
                               if n.lower().endswith(".md"))
            except OSError:
                n_agents = 0
        try:
            st = os.stat(sm)
            mt = ts(st.st_mtime * 1000)
        except OSError:
            mt = None
        # 目录名（ym-skillhub）和 slug（可能叫 skillhub-toolkit）不一定一致，
        # 所以先用目录名查市场索引，查不到再用 frontmatter 里的 slug / name 查 ——
        # 只查目录名的话，已装技能里有一大半查不到中文名。
        dn = _pick_display(_skillhub_meta(d), fm, nm)
        if not dn:
            for cand in (fm.get("slug"), fm.get("name")):
                if isinstance(cand, str) and cand:
                    dn = disp_idx.get(cand.strip().lower())
                    if dn:
                        break
        out.append(_mk_entry(
            id=pid,
            name=fm.get("name") or nm,
            display_name=dn or disp_idx.get(nm.lower()) or "",
            kind="skill",
            source="本地目录",
            marketplace="local",
            desc=fm.get("description") or "",
            version=fm.get("version") or "",
            author=fm.get("author") or "",
            path=d,
            updated_at=mt,
            keywords=fm.get("trigger") if isinstance(fm.get("trigger"), list) else [],
            scope="user",
            agents=n_agents,
        ))

    # ---------- 4) MCP 连接器（手写配置）----------
    try:
        with open(os.path.join(wb, "mcp.json"), "r", encoding="utf-8") as fh:
            mj = json.load(fh)
    except (OSError, ValueError):
        mj = {}
    for srv, cfg in (mj.get("mcpServers") or {}).items():
        if not isinstance(cfg, dict):
            continue
        pid = "mcp:" + srv
        if pid in seen_ids:
            continue
        seen_ids.add(pid)
        # 连接目标：本地命令还是远程 URL
        tgt = cfg.get("url") or cfg.get("command") or ""
        out.append(_mk_entry(
            id=pid,
            name=srv,
            kind="connector",
            source="MCP 配置",
            marketplace="mcp",
            desc=tgt,
            version=cfg.get("type") or "",
            path=tgt,
            enabled=not bool(cfg.get("disabled")),
            scope="user",
        ))

    # ---------- 5) 本地专家登记（experts.json 里只有名字）----------
    #
    # 🔴 这一步不是「新增」，而是「认领」。
    # experts.json 只写了一串名字，实体其实在第 2 步扫到的市场目录里
    # （实测 tianwei-bid-expert / ym-dev-team 都在 marketplaces/my-experts 下）。
    # 早前这里无条件新增一条，结果同一个专家在列表里出现两回：
    #   一条是市场实体（installed=False，因为没进官方安装清单）
    #   一条是「本地登记」（找不到目录，信息不全）
    # 明哥看到的是「我明明装了这个专家，怎么显示未安装还缺目录」。
    #
    # 正确做法：先按名字去已有结果里找，**找到了就把它认领成已安装**
    # （登记在 experts.json 里就等于在用），找不到才补一条「只有名字」的。
    by_name = {}
    for e in out:
        by_name.setdefault((e["name"] or "").lower(), []).append(e)

    for ej in glob.glob(os.path.join(wb, "experts", "custom", "*", "experts.json")):
        try:
            with open(ej, "r", encoding="utf-8") as fh:
                arr = json.load(fh)
        except (OSError, ValueError):
            continue
        if not isinstance(arr, list):
            continue
        for nm in arr:
            if not isinstance(nm, str) or not nm:
                continue
            cands = by_name.get(nm.lower()) or []
            # 优先认领「我的专家」市场里的那条，其次任意同名
            hit = next((c for c in cands if c["marketplace"] == "my-experts"), None)
            if hit is None:
                hit = next((c for c in cands
                            if c["marketplace"] not in ("workbuddy-builtin",)), None)
            if hit is not None:
                hit["installed"] = True
                hit["source"] = "我的专家"
                hit["missing"] = False
                if not hit["desc"]:
                    hit["desc"] = "（本地登记使用）"
                continue
            pid = "custom-expert:" + nm
            if pid in seen_ids:
                continue
            seen_ids.add(pid)
            out.append(_mk_entry(
                id=pid,
                name=nm,
                kind="expert",
                source="本地登记",
                marketplace="local",
                desc="（只在本地登记了名字，未找到安装目录）",
                path=os.path.dirname(ej),
                scope="user",
                missing=True,
            ))

    # 描述兜底：包里没写 description 但有 README 的，取 README 第一段
    for e in out:
        if e["desc"] or not e["path"] or e["missing"]:
            continue
        rd = os.path.join(e["path"], "README.md")
        if not os.path.isfile(rd):
            continue
        for line in _read_text(rd, 1200).splitlines():
            line = line.strip()
            # 跳过标题行和空行，取第一句有内容的话
            if not line or line.startswith("#") or line.startswith("!"):
                continue
            e["desc"] = line[:200]
            break

    out.sort(key=lambda x: (x["kind"], (x["name"] or "").lower()))
    return out


# ============================================================ 总览


def snapshot(with_ops=True):
    """采集全部数据。with_ops=True 时附加备份/日志等运营信息。"""
    sessions = load_sessions()
    load_usage(sessions)
    load_artifacts(sessions)
    load_jsonl_meta(sessions)
    tasks = sorted(sessions.values(), key=lambda t: (t.get("updated_at") or ""), reverse=True)

    projects = load_projects(sessions)
    automations = load_automations()
    plugins = load_plugins()

    # ------- 统计 -------
    now = datetime.now().astimezone()
    def days_ago(n):
        return (now.timestamp() - n * 86400) * 1000

    def recent(t, days):
        ref = t.get("updated_at") or t.get("created_at")
        if not ref:
            return False
        try:
            return datetime.fromisoformat(ref).timestamp() * 1000 >= days_ago(days)
        except ValueError:
            return False

    no_artifact = [t for t in tasks if not t["artifacts"] and not t["deleted"]]
    live = [t for t in tasks if not t["deleted"]]
    active_auto = [a for a in automations if not a["deleted"]]
    stats = {
        "total": len(tasks),
        "live": len(live),
        "deleted": len(tasks) - len(live),
        "with_artifacts": sum(1 for t in live if t["artifacts"]),
        "artifact_count": sum(len(t["artifacts"]) for t in live),
        "no_artifact": len(no_artifact),
        "stale_30": sum(1 for t in live if not recent(t, 30)),
        "active_7": sum(1 for t in live if recent(t, 7)),
        "lost_files": sum(1 for t in live for a in t["artifacts"] if a.get("exists") is False),
        "credits_total": round(sum((t["usage"] or {}).get("credits_total") or 0 for t in live), 2),
        "tokens_total": sum((t["usage"] or {}).get("tokens_used") or 0 for t in live),
        "project_count": count_projects(projects),
        "temp_dir_count": sum(1 for p in projects if p["is_temp"]),
        "automation_count": len(active_auto),
        "automation_deleted": len(automations) - len(active_auto),
        "generated_at": now.isoformat(),
    }
    # 扩展统计：侧栏要显示「装了多少个」，按类型拆开好做筛选入口
    pk = {}
    for p in plugins:
        pk[p["kind"]] = pk.get(p["kind"], 0) + 1
    stats["plugin_count"] = len(plugins)
    stats["plugin_skill"] = pk.get("skill", 0)
    stats["plugin_expert"] = pk.get("expert", 0)
    stats["plugin_team"] = pk.get("team", 0)
    stats["plugin_connector"] = pk.get("connector", 0)
    stats["plugin_plugin"] = pk.get("plugin", 0)
    stats["plugin_missing"] = sum(1 for p in plugins if p["missing"])
    stats["plugin_installed"] = sum(1 for p in plugins if p["installed"])
    if with_ops:
        stats.update(_ops_stats())

    return {
        "version": 3,
        "generated_at": stats["generated_at"],
        "stats": stats,
        "tasks": tasks,
        "projects": projects,
        "automations": automations,
        "plugins": plugins,
        "library_roots": library_roots(),
        "library_groups": library_groups(),
    }


def _ops_stats():
    """备份与日志信息（供设置页展示）。不依赖 ops.py，避免循环导入。"""
    backup_dir = os.path.join(WB_DIR, "_wbmanager_backups")
    out = {
        "db_path": DB_PATH,
        "db_size": None,
        "backup_dir": backup_dir,
        "backup_count": 0,
        "backups": [],
        "log_count": 0,
    }
    try:
        out["db_size"] = os.path.getsize(DB_PATH)
    except OSError:
        pass
    files = []
    try:
        # 用 scandir 流式读取：这里只读不删，不会被安全策略拦，
        # 但 scandir 顺带把 stat 取回来了，比 listdir + 逐个 stat 少一轮系统调用。
        with os.scandir(backup_dir) as it:
            for e in it:
                try:
                    if not e.is_file():
                        continue
                    name = e.name
                    if not (name.startswith("workbuddy-") and name.endswith(".db")):
                        continue
                    st = e.stat()
                    files.append({"name": name, "path": e.path, "size": st.st_size,
                                  "mtime": datetime.fromtimestamp(st.st_mtime).isoformat()})
                except OSError:
                    continue
    except OSError:
        pass
    files.sort(key=lambda x: x["mtime"], reverse=True)
    out["backup_count"] = len(files)
    out["backups"] = files[:8]
    try:
        with open(os.path.join(backup_dir, "operations.log"), "r", encoding="utf-8") as fh:
            out["log_count"] = sum(1 for _ in fh)
    except OSError:
        pass
    return out


# ============================================================ 账户 / 记忆 / 更新
#
# 这一块服务的是「账户」页。三条能力各自的边界要说清楚：
#
#   · 账户信息 —— 只读**本地快照**（storage/skeleton/account-snapshot.json）。
#     登录令牌存在系统加密存储里（读不到，也不该读），所以积分余额这类
#     需要鉴权的数据拿不到，只能提供「唤起客户端」的入口。
#
#   · 记忆文件 —— 只**列 + 读**，绝不改。这是明哥的资产。
#
#   · 检查更新 —— 客户端版本读本地，技能版本打 SkillHub 公开接口。

# 账户快照的位置可能随客户端版本变，这里按可能性从高到低找。
_ACCOUNT_SNAPSHOT_REL = (
    os.path.join("storage", "skeleton", "account-snapshot.json"),
    os.path.join("storage", "skeleton", "account.json"),
)


def account_info():
    """账户页要的全部本地信息（不联网、不读凭据）。"""
    out = {
        "ok": True,
        "account": None,
        "wb_dir": WB_DIR,
        "client": {},
        "credits": {"available": False, "reason": "积分需要登录态，本页只读本地数据"},
        "links": [],        # 可跳转的入口（唤起客户端 / 打开网页）
        "memory": memory_overview(),
    }

    # ---- 账户快照 ----
    for rel in _ACCOUNT_SNAPSHOT_REL:
        fp = os.path.join(WB_DIR, rel)
        if not os.path.isfile(fp):
            continue
        try:
            with open(fp, "r", encoding="utf-8") as fh:
                j = json.load(fh)
        except (OSError, ValueError):
            continue
        prim = j.get("primary") if isinstance(j, dict) else None
        if isinstance(prim, dict):
            out["account"] = {
                "nickname": prim.get("nickname") or "",
                "uid": prim.get("uid") or "",
                "type": prim.get("type") or "",
                "edition": prim.get("editionType") or "",
                "is_pro": bool(prim.get("isPro")),
                "is_admin": bool(prim.get("isAdmin")),
                "saved_at": ts(prim.get("savedAt")),
            }
            break

    # ---- 客户端信息 ----
    out["client"] = _client_info()

    # ---- 入口（唤起客户端 / 官方网页）----
    #
    # 为什么是「入口」而不是「把数据抓进来」：
    # 积分余额、成长计划、签到这些接口都要登录令牌，令牌在系统加密存储里。
    # 一个本地小工具去解密主程序的凭据，本身就不该做（也不稳）。
    # 正确做法是把用户送到**已经登录好的客户端/网页**里去，那里数据本来就在。
    # v1.2.3 修正：签到 / 成长计划**过去指向官网首页**（https://www.workbuddy.cn/），
    # 那里根本没有签到入口，点了只会让人更迷糊 —— 这就是「找不到签到入口」的直接原因。
    #
    # 真相（扒 app.asar 得到）：
    #   签到 = 客户端里的「Buddy 加油站」，藏在**左下角头像点开的账号菜单**里，
    #   由 AvatarTopSlot 渲染成一个气泡（.daily-checkin--bubble）。
    #   气泡的显示条件是 `!checkinBubbleDismissed`，是**内存态** ——
    #   关掉之后本次运行不再弹，只有重启才恢复 → 用户「非要重启才能领」的感受来源。
    #
    # 而重新唤出气泡的动作（reopenBubble）只在客户端内部，**没有外部深链**：
    #   DEEP_LINK_ROUTE_MAP = home/chat/projects/experts/skills/connectors/automation/
    #                         colleagues/claw/discover/tencent-docs/my-files/ima/lexiang/
    #                         agent-mail/genie/assistant/templates/project/expert/connector
    #   —— 没有 account / checkin / credits。
    #   （settings 走单独的 parseSettingsDeepLink，支持 workbuddy://settings/<tab>）
    #
    # 也**不能代签**：接口 /v2/billing/meter/daily-checkin 要两样本页拿不到的东西 ——
    #   ① 登录 Bearer 令牌（在系统加密存储里）
    #   ② X-Device-Token —— 腾讯图灵盾设备指纹，只有客户端能生成（非缓存、不可复刻）
    # 硬拿等于盗用用户凭据，所以这里只做入口，不碰凭据、不伪造任何数字。
    out["links"] = [
        {"id": "checkin", "label": "每日签到 / 领积分", "kind": "app",
         "url": "workbuddy://home",
         "hint": "签到在客户端左下角头像里：点头像 →「Buddy加油站」→「签到领积分」"},
        {"id": "credits", "label": "积分余额", "kind": "app",
         "url": "workbuddy://settings/account",
         "hint": "打开客户端账户页（本页读不到登录态）"},
        {"id": "growth", "label": "成长计划", "kind": "app",
         "url": "workbuddy://home",
         "hint": "也在账号菜单里：点头像 →「成长计划」"},
        {"id": "settings", "label": "客户端设置", "kind": "app",
         "url": "workbuddy://settings/appearance", "hint": "唤起本机客户端"},
        {"id": "home", "label": "打开 WorkBuddy 首页", "kind": "app",
         "url": "workbuddy://home", "hint": "唤起本机客户端"},
    ]
    return out


def _client_info():
    """客户端版本 / 安装位置 / 产品配置（都是本地读，不联网）。"""
    out = {"version": "", "exe": "", "resources": "", "endpoint": "",
           "product": "WorkBuddy", "data_dir": WB_DIR}
    # 常见安装位置（按可能性排序）
    cands = [
        os.path.join(os.environ.get("LOCALAPPDATA") or "", "Programs", "WorkBuddy"),
        os.path.join(os.environ.get("PROGRAMFILES") or "", "WorkBuddy"),
        os.path.join(os.environ.get("PROGRAMFILES(X86)") or "", "WorkBuddy"),
    ]
    for base in cands:
        if not base:
            continue
        vf = os.path.join(base, "version")
        if os.path.isfile(vf):
            out["version"] = _read_text(vf, 64).strip()
            out["exe"] = os.path.join(base, "WorkBuddy.exe")
            out["resources"] = os.path.join(base, "resources")
            break
    # 产品配置里有 endpoint（判断内网版 / 公网版）
    cfgp = os.path.join(WB_DIR, "cache", "acc-product-config-v3.json")
    if os.path.isfile(cfgp):
        try:
            with open(cfgp, "r", encoding="utf-8") as fh:
                j = json.load(fh)
            out["endpoint"] = j.get("endpoint") or ""
            out["product"] = j.get("productName") or out["product"]
        except (OSError, ValueError):
            pass
    return out


def memory_dirs():
    """所有「记忆」目录（用户级 + 项目级 + 云端缓存）。

    只返回**真实存在**的目录。
    """
    out = []
    # 1) 用户级：~/.workbuddy/memory/（云端记忆缓存）
    d = os.path.join(WB_DIR, "memory")
    if os.path.isdir(d):
        out.append(d)
    return out


def memory_files():
    """散落的记忆文件（不在 memory/ 目录里的那几个）。"""
    out = []
    for nm in ("MEMORY.md", "USER.md", "IDENTITY.md", "SOUL.md"):
        fp = os.path.join(WB_DIR, nm)
        if os.path.isfile(fp):
            out.append(fp)
    return out


def _mem_item(path, scope, title=""):
    """把一个记忆文件描述成结构体。只 stat，不读内容。"""
    try:
        st = os.stat(path)
    except OSError:
        return None
    return {
        "path": path,
        "name": os.path.basename(path),
        "scope": scope,
        "title": title or os.path.basename(path),
        "size": st.st_size,
        "mtime": ts(st.st_mtime * 1000),
    }


def memory_overview():
    """记忆页要的清单：按作用域分组。

    作用域只有三种，都是明哥实际在用的：
      · 用户级   —— 跨项目，跟着人走（MEMORY.md / USER.md 等）
      · 云端缓存 —— 服务端自动生成的画像（memory/ 目录）
      · 项目级   —— 工作目录下的 .workbuddy/memory/
    """
    groups = []

    # ---- 用户级散落文件 ----
    items = []
    for fp in memory_files():
        it = _mem_item(fp, "user")
        if it:
            items.append(it)
    if items:
        groups.append({"scope": "user", "label": "用户级（跨项目）",
                       "desc": "跟着你走，所有项目共用", "items": items})

    # ---- 云端记忆缓存 ----
    items = []
    d = os.path.join(WB_DIR, "memory")
    if os.path.isdir(d):
        try:
            for nm in sorted(os.listdir(d)):
                if nm.endswith(".bak") or not os.path.isfile(os.path.join(d, nm)):
                    continue
                it = _mem_item(os.path.join(d, nm), "cloud")
                if it:
                    items.append(it)
        except OSError:
            pass
    if items:
        groups.append({"scope": "cloud", "label": "云端记忆缓存",
                       "desc": "服务端自动生成的画像（本机缓存）", "items": items})

    # ---- 项目级 ----
    items = []
    try:
        for s in load_sessions().values():
            cwd = s.get("cwd")
            if not cwd:
                continue
            md = os.path.join(cwd, ".workbuddy", "memory")
            if not os.path.isdir(md):
                continue
            try:
                for nm in sorted(os.listdir(md)):
                    if not nm.endswith(".md"):
                        continue
                    it = _mem_item(os.path.join(md, nm), "project", title=cwd)
                    if it and not any(x["path"] == it["path"] for x in items):
                        items.append(it)
            except OSError:
                continue
            if len(items) > 200:
                break
    except Exception:                      # noqa: BLE001
        pass
    if items:
        items.sort(key=lambda x: x["mtime"] or "", reverse=True)
        groups.append({"scope": "project", "label": "项目级",
                       "desc": "只对某个工作目录生效", "items": items[:120]})

    total = sum(len(g["items"]) for g in groups)
    return {"ok": True, "groups": groups, "total": total}


def _ver_key(v):
    """把版本号拆成可比较的元组，比不出来返回 None。

    规则（够用就好，不追求完整语义化版本）：
      · 前导 v 去掉；按 . - _ + 切段
      · 纯数字段 -> (0, int)，比字符串比较正确（1.10 > 1.9 靠这个）
      · 非数字段 -> 预发布标记，值取 (2, s)

    🔴 用 (0,数字) 和 (2,字符串) 两档，而不是「相等就 0、其它 1」——
    后者会让 1.2.0-beta **大于** 1.2.0（因为 -beta 多出一个段），
    语义化版本里预发布恰恰是**小于**正式版的。用类型档位把预发布段
    排到数字段之后，同类里再按字符串比，长短自然由短的更小决定。
    """
    if v is None:
        return None
    s = str(v).strip().lstrip("vV")
    if not s:
        return None
    parts = []
    for seg in re.split(r"[.\-_+]", s):
        if not seg:
            continue
        if seg.isdigit():
            parts.append((0, int(seg)))
        else:
            parts.append((2, seg))
    return tuple(parts) if parts else None


def _ver_cmp(a, b):
    """比版本：a<b 返回 -1，a==b 返回 0，a>b 返回 1，比不出来返回 0。

    比不出来（任一侧为空 / 解析不出任何段 / 一侧纯数字一侧纯字母这种
    跨类型不可比的）一律返回 0，让调用方走「不报落后」的保守分支。

    🔴 跨类型要显式判掉：`(2,'abc') < (0,1)` 在 Python 里会直接
    TypeError 抛出去（int 和 str 不可比），不是返回 False。
    一个「检查更新」不该因为某个技能版本号写得野就把整个接口打 500。
    """
    ka, kb = _ver_key(a), _ver_key(b)
    if not ka or not kb:
        return 0
    # 逐段比到自己这一侧耗尽为止
    for x, y in zip(ka, kb):
        if x == y:
            continue
        # 两边段类型不同（一侧数字一侧字母）= 版本号写法不统一，
        # 判不出来就说不出来，别硬猜一个顺序
        if x[0] != y[0]:
            return 0
        return -1 if x < y else 1
    if len(ka) == len(kb):
        return 0
    # 前面全一样、只有段数不同：长的更大（1.0.0.0 > 1.0.0）
    # 但如果多出来那段是预发布标记，反而更小（1.2.0-beta < 1.2.0）
    longer, shorter = (ka, kb) if len(ka) > len(kb) else (kb, ka)
    extra = longer[len(shorter):]
    sign = -1 if any(p[0] == 2 for p in extra) else 1
    return sign if len(ka) > len(kb) else -sign


def check_updates():
    """检查更新。

    三件事各查各的：
      1) 客户端本身 —— 只能报出本机版本；有没有新版得问官方，
         这里不擅自打更新接口（那是客户端自己的活）。
      2) 本机已装的 ym-* 技能 —— 打 SkillHub 公开接口比版本。
         **只比对、只报告，绝不自动下载自己改写**（自检器铁律）。
      3) 管理中心自身的版本。

    返回结构里 ok 只代表「整个检查跑完了」，不代表「有更新」。
    """
    import online  # 延迟导入：没有网络需求时不必加载

    out = {"ok": True, "client": _client_info(), "skills": [],
           "checked_at": datetime.now().astimezone().isoformat(),
           "errors": [], "not_published": []}

    # 本机技能版本
    local = []
    sk_root = os.path.join(WB_DIR, "skills")
    try:
        names = sorted(os.listdir(sk_root))
    except OSError:
        names = []
    for nm in names:
        sm = os.path.join(sk_root, nm, "SKILL.md")
        if not os.path.isfile(sm):
            continue
        fm = _frontmatter(sm)
        local.append({
            "dir": nm,
            "slug": fm.get("slug") or nm,
            "version": fm.get("version") or "",
            "display_name": _pick_display(None, fm, nm) or "",
        })

    # 逐条比对
    #
    # 🔴 这里踩过一个坑：初版要求「有 slug 且有 version」才查，其余直接跳过，
    # 结果 60 多个技能里只有 8 个被检查 —— 剩下 35 个报「缺 slug 或 version」。
    # 但翻了下实际的 SKILL.md，大量本地技能本来就**只写 name 不写 slug**，
    # 还有一批压根不写 version。这些不是坏数据，是正常写法：
    #   · slug 缺 -> 用目录名当标识（SkillHub 上就是按目录名认的）
    #   · version 缺 -> 只能确认「在线有没有这个名字」，比不了版本
    # 所以分两档处理，并且把「查不到线上」和「没写版本号」分开报告，
    # 别让用户看到一个笼统的「缺字段」以为工具坏了。
    #
    # 🔴 第二个坑：上限 40 是按**字母序**切的，结果 ym-* 一大堆全排在 ym-v~ym-w，
    # 被整段砍掉 —— 其中就有 ym-wb-manager（管理中心自己），自己检查不了自己
    # 这件事很荒谬。所以上限提到 80，并且**先把 ym- 开头的排到前面**
    # （那是用户自己发的、最关心版本的那批），本地自制的排后面。
    local.sort(key=lambda x: (0 if x["slug"].startswith("ym-") else 1, x["slug"]))

    n_checked = 0
    for it in local:
        if not it["slug"]:
            it["slug"] = it["dir"]           # 兜底：目录名就是标识
        it["no_version"] = not it["version"]

        if n_checked >= 80:                # 别忘了这是公网接口，别一次打几十个
            out["errors"].append({"dir": it["dir"], "reason": "超过本次检查上限（80 个）"})
            continue
        n_checked += 1
        res = online.search(it["slug"], limit=5)
        if not res.get("ok"):
            # 联网失败/超时不等于「没有更新」。记下来让前端说清楚，
            # 否则用户看到「已检查 8 个」会以为剩下的都查过了。
            out["errors"].append({"dir": it["dir"], "slug": it["slug"],
                                  "reason": res.get("error") or "查询失败"})
            continue
        hit = None
        for c in res.get("results", []):
            if (c.get("slug") or "").lower() == it["slug"].lower():
                hit = c
                break
        if not hit:
            # 🔴「线上没有同名技能」不是**错误**，是最常见的一种正常结果。
            #
            # 本机 46 个本地技能里，只有 13 个发到了 SkillHub，其余都是自制/未上架。
            # 早先一律塞进 out["errors"]，页面上就显示「33 个错误」——
            # 用户以为工具坏了，实际什么都没坏。错误要留给**真的出了问题**：
            # 联网失败、超时、接口报错。这两类别混在一个数组里。
            out["not_published"].append({
                "dir": it["dir"], "slug": it["slug"],
                "reason": "SkillHub 上没有同名技能（本地自制 / 未上架）"})
            continue
        it["latest"] = hit.get("version") or ""
        it["online_name"] = hit.get("display_name") or ""
        # 本地没写版本号：能确认「线上有这个技能」，但无从比大小
        if it["no_version"]:
            it["outdated"] = False
            it["ahead"] = False
            it["note"] = "本地未写版本号，只确认了线上存在"
            out["skills"].append(it)
            continue
        # 🔴 判「落后」必须比大小，不能比「相不相等」。
        #
        # 一开始写的是 version != latest，结果 expert-packager 本地 1.3.10、
        # 线上 1.0.2，被标成「可以更新」—— 明明是本地更新，报告却让人去装旧版。
        # 这种「建议降级」的提示比不提示还坏：用户照做就真的把新功能退回去了。
        # 所以这里用版本比较，只在线上**确实更大**时才算落后。
        # 比不出大小（日期后缀、非数字段、格式怪）时一律**不**报落后 ——
        # 宁可漏报（用户去 skillhub 一看就知道），也不要误报误导人。
        cmp = _ver_cmp(it["version"], it["latest"])
        it["outdated"] = bool(it["latest"] and cmp < 0)
        it["ahead"] = bool(it["latest"] and cmp > 0)
        out["skills"].append(it)

    out["outdated_count"] = sum(1 for s in out["skills"] if s.get("outdated"))
    out["checked_count"] = len(out["skills"])
    out["not_published_count"] = len(out["not_published"])

    # 管理中心自己的版本
    try:
        import config as _conf                # noqa: F401
        self_skill = os.path.join(WB_DIR, "skills", "ym-wb-manager", "SKILL.md")
        if os.path.isfile(self_skill):
            fm = _frontmatter(self_skill)
            out["self"] = {"slug": fm.get("slug") or "ym-wb-manager",
                           "version": fm.get("version") or ""}
    except Exception:                          # noqa: BLE001
        pass
    return out


if __name__ == "__main__":
    import argparse
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    import console
    console.fix()
    ap = argparse.ArgumentParser(description="采集 WorkBuddy 全量数据为 JSON")
    ap.add_argument("-o", "--out", help="输出文件（默认 stdout）")
    ap.add_argument("--pretty", action="store_true")
    args = ap.parse_args()
    data = snapshot()
    text = json.dumps(data, ensure_ascii=False, indent=2 if args.pretty else None)
    if args.out:
        with open(args.out, "w", encoding="utf-8") as fh:
            fh.write(text)
        s = data["stats"]
        sys.stderr.write("[data] 任务 %d | 产物 %d | 项目 %d | 定时 %d -> %s\n"
                         % (s["total"], s["artifact_count"], s["project_count"],
                            s["automation_count"], args.out))
    else:
        sys.stdout.write(text)
