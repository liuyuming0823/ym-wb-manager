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
# 早前这里是写死的若干条本机绝对路径。分享给别人时这些路径根本不存在，
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
    if with_ops:
        stats.update(_ops_stats())

    return {
        "version": 3,
        "generated_at": stats["generated_at"],
        "stats": stats,
        "tasks": tasks,
        "projects": projects,
        "automations": automations,
        "library_roots": library_roots(),
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
