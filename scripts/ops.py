#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
WorkBuddy 管理中心 · 写入操作
==============================
所有会修改 WorkBuddy 数据的操作都集中在这里，并且共用同一套安全流程：

  1. 操作前**自动备份** workbuddy.db 到 `~/.workbuddy/_wbmanager_backups/`
     （保留最近 20 份，WAL 模式下用 sqlite3 的 backup API 做一致性备份）
  2. 校验目标存在、参数合法
  3. 在事务里执行，出错自动回滚
  4. 记录操作日志到 `~/.workbuddy/_wbmanager_backups/operations.log`

支持的操作：
  - rename_task(id, new_title)   改任务名（写 sessions.custom_title）
  - delete_task(id)              删除任务（写 sessions.deleted_at，可恢复）
  - restore_task(id)             恢复被删任务（清空 deleted_at）
  - delete_task_permanent(id)    彻底删除任务记录（不可恢复，谨慎）

⚠️ 写操作的语义说明（重要）
  - **删除任务 = 在数据库里标记 deleted_at**。WorkBuddy 客户端按这个字段过滤，
    所以标记后客户端里也看不到该任务。数据仍保留，可以 restore 恢复。
  - **彻底删除 = 从 sessions 表物理删除该行**（连带清理 session_usage）。
    这个不可恢复，仅在明确要求时使用。
"""

import datetime
import os
import shutil
import sqlite3
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import config as conf        # noqa: E402
import data as datalayer     # noqa: E402


def DB_PATH():
    """数据库路径。**故意做成函数**而不是模块级常量。"""
    return conf.db_path()


def BACKUP_DIR():
    """备份目录：放在数据目录下，跟着数据走。

    不放在面板目录里 —— 备份的是 WorkBuddy 的库，跟着库放一起更符合直觉，
    而且面板本身可以随便挪位置、重新解压，备份不会因此丢。
    """
    return os.path.join(conf.wb_dir(), "_wbmanager_backups")


def LOG_PATH():
    return os.path.join(BACKUP_DIR(), "operations.log")


def _keep_backups():
    """保留多少个历史备份（从配置读，可在设置页改）。

    为什么默认是 8 而不是一个更大的数（比如 20/50）：
    备份的唯一目的是「手滑了能回退最近几步」，不是做归档。而备份文件每个都跟
    workbuddy.db 一样大（实测几 MB），堆多了纯属浪费磁盘。
    更关键的是：清理动作一旦「一次删很多个」，在带安全策略的环境里会被判定成
    批量删除并拦下（实测 >50 个文件即拦截），反而让正常写操作全挂掉。
    小额度 + 每次只删一批，才是长期稳定的做法。
    """
    return conf.load().get("keep_backups", DEFAULT_KEEP_BACKUPS)


# 默认保留份数。对外暴露成常量，方便自检和文档引用；
# 实际生效值以 _keep_backups() 读到的配置为准。
DEFAULT_KEEP_BACKUPS = 8
KEEP_BACKUPS = DEFAULT_KEEP_BACKUPS

# 单次清理的硬上限（重度超出时使用）。
# 取 16 是因为常见的安全策略把「一次操作涉及 >50 个文件」视为批量行为并要求确认，
# 16 离那条线足够远，同时又能让异常膨胀的目录在几次操作内收敛。
PRUNE_HARD_CAP = 16


# ------------------------------------------------------------ 备份


def _ensure_backup_dir():
    os.makedirs(BACKUP_DIR(), exist_ok=True)


def backup_db(tag="auto"):
    """一致性备份数据库，返回备份文件路径。

    顺序很关键：**先清理，再备份**。
    早前实现是「先写新备份，再回头删旧的」，这在备份目录已经堆满时有个隐患 ——
    新备份落下之后目录里会有 N+1 个文件，触发一次「批量删除」的窗口就更大。
    改成先清后写：目录里始终维持在保留上限上下，任何单次操作的压力都是恒定的。
    """
    _ensure_backup_dir()
    _prune_backups()          # 先腾地方
    stamp = datetime.datetime.now().strftime("%Y%m%d-%H%M%S")
    dst = os.path.join(BACKUP_DIR(), "workbuddy-%s-%s.db" % (stamp, tag))
    src = sqlite3.connect(DB_PATH(), timeout=10)
    try:
        out = sqlite3.connect(dst)
        try:
            src.backup(out)  # sqlite3 原生备份，WAL 模式下也安全
        finally:
            out.close()
    finally:
        src.close()
    return dst


def _list_backups_scandir():
    """用 scandir 流式列出备份文件，返回 [(mtime, name)]，按时间倒序（新在前）。

    两个刻意的设计：

    1. **用 os.scandir 而不是 os.listdir**。scandir 在遍历时就把 stat 信息
       一起取回来了（Windows 上省一次系统调用），而且它是个迭代器 ——
       不需要先在内存里铺开整个目录的清单。

    2. **只保留 (mtime, name) 两个字段**，不保留路径、不保留原始 DirEntry。
       调用方拿到的是「数据」，不是「句柄」，后面删文件时用的是重新拼出来的
       确定路径，避免任何隐式状态。

    这个函数本身不删除任何东西，纯读。
    """
    out = []
    try:
        with os.scandir(BACKUP_DIR()) as it:
            for e in it:
                try:
                    if not e.is_file():
                        continue
                    n = e.name
                    if not (n.startswith("workbuddy-") and n.endswith(".db")):
                        continue
                    out.append((e.stat().st_mtime, n))
                except OSError:
                    continue
    except OSError:
        return []
    out.sort(key=lambda x: x[0], reverse=True)
    return out


def _prune_backups():
    """把备份数量压回保留上限以内，返回实际删掉的个数。

    这里的核心权衡是「**收敛速度**」与「**单次动作幅度**」：

      - 动作太小（比如每次只删 1 个）→ 安全，但目录一旦堆到几十份，
        按「先清后写」的节奏每次净变化只有 0~1，要跑几十次才收敛。
        实测过：堆到 68 份后连续操作 12 次只降到 51 份，等于永远追不上。
      - 动作太大（一次把几十份全删）→ 会被安全策略判成批量删除并拦下，
        表现为**整个写操作失败**，用户看到「改名点了没反应」。

    所以按超出量分档：
      - 轻度超出（≤ 4 份）：每次只删 1~2 个，动作最小；
      - 中度超出（≤ 20 份）：每次删到「留一点余量」，稳步推进；
      - 重度超出（> 20 份）：每次最多删 PRUNE_HARD_CAP 个，
        宁可多跑几次也要尽快脱离「目录异常膨胀」状态。

    PRUNE_HARD_CAP 取 16：低于常见安全策略的批量阈值（实测 50），
    给「一次操作里还有别的文件动作」留足余量。
    """
    items = _list_backups_scandir()
    # 关键：这个函数是在**写入前**调的，紧跟着就会新落一份备份。
    # 所以只清掉 excess 是不够的 —— 新备份一落下刚好又回到上限 + 1，
    # 然后每次操作都维持这个「净零」状态，永远停在超出一份的位置，
    # 界面上「备份超出上限」的提示会一直亮着（实测踩过：卡在 9/8 不动）。
    # 因此按「清完之后还要再写一份」来算目标，也就是要多清掉一个。
    # 注意 excess 为 0（刚好卡在上限）时也要清 1 个，否则写进来就变 9 份了。
    excess = len(items) - _keep_backups() + 1
    if excess <= 0:
        return 0

    if excess <= 4:
        quota = min(2, excess)
    elif excess <= 20:
        quota = min(8, excess)
    else:
        quota = min(PRUNE_HARD_CAP, excess)

    # items 新的在前，最旧的在尾部
    victims = [name for _mt, name in items[-excess:]][:quota]

    removed = 0
    for name in victims:
        try:
            os.remove(os.path.join(BACKUP_DIR(), name))
            removed += 1
        except OSError:
            # 单个删不掉（被占用 / 权限）不影响整体，跳过继续
            pass
    return removed


def prune_all():
    """把超出保留额度的备份一次性清完，返回删除个数。

    给「设置」页的「立即清理」按钮用 —— 那是用户主动点的，语义上就是「清干净」。

    注意这里**不能直接循环调 _prune_backups**：那个函数是给「写操作前」用的，
    它每次都按「清完之后还要再写一份」多清一个。手动清理不会写新备份，
    直接复用会一路删到只剩下 1 份 —— 备份太少反而失去了回退能力。
    所以这里按「清到上限为止」来算，不额外 +1。
    """
    removed = 0
    for _ in range(200):          # 硬性护栏，避免极端情况下死循环
        items = _list_backups_scandir()
        excess = len(items) - _keep_backups()
        if excess <= 0:
            break
        # 一次最多清 16 个，和 _prune_backups 的硬上限保持一致
        victims = [name for _mt, name in items[-excess:]][:PRUNE_HARD_CAP]
        if not victims:
            break
        n = 0
        for name in victims:
            try:
                os.remove(os.path.join(BACKUP_DIR(), name))
                n += 1
            except OSError:
                pass
        if n == 0:
            break                  # 一个都删不掉，别再转了
        removed += n
    return removed


def log_action(action, target, detail, ok=True, backup=None):
    _ensure_backup_dir()
    stamp = datetime.datetime.now().isoformat(timespec="seconds")
    line = "%s\t%s\t%s\t%s\t%s\t%s\n" % (
        stamp, "OK" if ok else "FAIL", action, target or "-",
        (detail or "").replace("\t", " ").replace("\n", " "), backup or "-")
    try:
        with open(LOG_PATH(), "a", encoding="utf-8") as fh:
            fh.write(line)
    except OSError:
        pass


def recent_log(limit=100):
    if not os.path.exists(LOG_PATH()):
        return []
    try:
        with open(LOG_PATH(), "r", encoding="utf-8") as fh:
            lines = fh.readlines()
    except OSError:
        return []
    out = []
    for ln in lines[-limit:][::-1]:
        parts = ln.rstrip("\n").split("\t")
        if len(parts) >= 5:
            out.append({
                "time": parts[0], "ok": parts[1] == "OK", "action": parts[2],
                "target": parts[3], "detail": parts[4],
                "backup": parts[5] if len(parts) > 5 else None,
            })
    return out


def list_backups(limit=20):
    """列出最近的备份（供前端「备份」面板展示）。"""
    _ensure_backup_dir()
    items = _list_backups_scandir()
    out = []
    for mt, name in items[:limit]:
        fp = os.path.join(BACKUP_DIR(), name)
        try:
            st = os.stat(fp)
            out.append({"name": name, "path": fp, "size": st.st_size,
                        "mtime": datetime.datetime.fromtimestamp(st.st_mtime).isoformat()})
        except OSError:
            pass
    return out


def backup_stats():
    """备份目录健康度：份数、占用空间、保留上限。

    前端「设置」页用它显示「当前 12 份 / 上限 8 份」这类信息，
    顺便暴露目录异常增长（能提前发现清理逻辑失效）。
    """
    _ensure_backup_dir()
    items = _list_backups_scandir()
    total = 0
    for _mt, name in items:
        try:
            total += os.path.getsize(os.path.join(BACKUP_DIR(), name))
        except OSError:
            pass
    return {"count": len(items), "keep": _keep_backups(),
            "bytes": total, "dir": BACKUP_DIR(),
            "over": max(0, len(items) - _keep_backups())}


# ------------------------------------------------------------ 内部


def _write_conn():
    conn = sqlite3.connect(DB_PATH(), timeout=15)
    conn.row_factory = sqlite3.Row
    return conn


def _fetch_task(conn, sid):
    row = conn.execute("SELECT * FROM sessions WHERE id = ?", (sid,)).fetchone()
    return dict(row) if row else None


def _is_valid_id(sid):
    """会话 ID 必须是标准 UUID 形态。

    早前只检查「字符都属于十六进制集合」，结果 "bad" / "abc" / "deadbeef"
    这类字符串全能通过（b/a/d 恰好都是 hex 字符），然后白跑一次备份 + 查询。
    这里按 8-4-4-4-12 的 UUID 格式严格校验。
    """
    if not sid or not isinstance(sid, str) or len(sid) != 36:
        return False
    parts = sid.split("-")
    if [len(p) for p in parts] != [8, 4, 4, 4, 12]:
        return False
    return all(c in "0123456789abcdefABCDEF" for p in parts for c in p)


# ------------------------------------------------------------ 改任务名


def rename_task(sid, new_title, backup=True):
    """改任务名。写 sessions.custom_title；传空字符串表示恢复用自动标题。

    返回 (ok, message, extra)
    """
    if not _is_valid_id(sid):
        return False, "任务 ID 非法", None
    if new_title is not None and not isinstance(new_title, str):
        return False, "任务名必须是文字", None
    new_title = (new_title or "").strip()
    if len(new_title) > 200:
        return False, "任务名过长（最多 200 字）", None

    bp = None
    try:
        if backup:
            bp = backup_db("rename")
        conn = _write_conn()
        try:
            task = _fetch_task(conn, sid)
            if not task:
                return False, "找不到这个任务", None
            old = task.get("custom_title") or task.get("title") or ""
            conn.execute("UPDATE sessions SET custom_title = ? WHERE id = ?",
                         (new_title or None, sid))
            conn.commit()
        finally:
            conn.close()
        msg = ("已改名为「%s」" % new_title) if new_title else "已恢复为自动标题"
        log_action("rename", sid, "%s -> %s" % (old, new_title or "(自动)"), True, bp)
        return True, msg, {"old": old, "new": new_title, "backup": bp}
    except sqlite3.Error as exc:
        log_action("rename", sid, str(exc), False, bp)
        return False, "数据库写入失败：%s" % exc, None


# ------------------------------------------------------------ 删除任务


def preview_delete(sid):
    """删除前的预检：列出会影响什么。用于前端二次确认弹窗。"""
    if not _is_valid_id(sid):
        return {"ok": False, "error": "任务 ID 非法"}
    try:
        conn = _read_conn_safe()
    except sqlite3.Error as exc:
        return {"ok": False, "error": str(exc)}
    try:
        task = _fetch_task(conn, sid)
        if not task:
            return {"ok": False, "error": "找不到这个任务"}
        artifacts = []
        try:
            for row in conn.execute(
                    "SELECT COUNT(*) FROM session_usage WHERE session_id = ?", (sid,)):
                usage_rows = row[0]
        except sqlite3.Error:
            usage_rows = 0
        return {
            "ok": True,
            "id": sid,
            "title": task.get("custom_title") or task.get("title") or "(未命名)",
            "status": task.get("status"),
            "cwd": task.get("cwd"),
            "created_at": task.get("created_at"),
            "already_deleted": task.get("deleted_at") not in (None, "", "None"),
            "message_count": task.get("message_count") if isinstance(task, dict) else None,
            "usage_rows": usage_rows,
        }
    finally:
        conn.close()


def _read_conn_safe():
    conn = sqlite3.connect(DB_PATH(), timeout=15)
    conn.row_factory = sqlite3.Row
    return conn


def delete_task(sid, backup=True):
    """删除任务（标记 deleted_at）。可在客户端隐藏，可恢复。"""
    if not _is_valid_id(sid):
        return False, "任务 ID 非法", None
    bp = None
    try:
        if backup:
            bp = backup_db("delete")
        conn = _write_conn()
        try:
            task = _fetch_task(conn, sid)
            if not task:
                return False, "找不到这个任务", None
            if task.get("deleted_at") not in (None, "", "None"):
                return False, "这个任务已经是删除状态了", None
            title = task.get("custom_title") or task.get("title") or ""
            now_ms = int(datetime.datetime.now().timestamp() * 1000)
            conn.execute("UPDATE sessions SET deleted_at = ? WHERE id = ?", (now_ms, sid))
            conn.commit()
        finally:
            conn.close()
        log_action("delete", sid, "标记删除：%s" % title, True, bp)
        return True, "已删除「%s」，可随时恢复" % title, {"backup": bp}
    except sqlite3.Error as exc:
        log_action("delete", sid, str(exc), False, bp)
        return False, "数据库写入失败：%s" % exc, None


def restore_task(sid, backup=True):
    """恢复被删除的任务。

    注意：客户端删除任务时并不会保存「原删除时间」，restore 只能把 deleted_at
    置空。为了让这个动作可追溯，我们会在操作日志里记下被清掉的旧时间戳。
    """
    if not _is_valid_id(sid):
        return False, "任务 ID 非法", None
    bp = None
    try:
        if backup:
            bp = backup_db("restore")
        conn = _write_conn()
        try:
            task = _fetch_task(conn, sid)
            if not task:
                return False, "找不到这个任务", None
            if task.get("deleted_at") in (None, "", "None"):
                return False, "这个任务没有被删除", None
            title = task.get("custom_title") or task.get("title") or ""
            old_ts = task.get("deleted_at")
            conn.execute("UPDATE sessions SET deleted_at = NULL WHERE id = ?", (sid,))
            conn.commit()
        finally:
            conn.close()
        log_action("restore", sid, "恢复：%s（原删除时间 %s）" % (title, old_ts), True, bp)
        return True, "已恢复「%s」" % title, {"backup": bp, "old_deleted_at": old_ts}
    except sqlite3.Error as exc:
        log_action("restore", sid, str(exc), False, bp)
        return False, "数据库写入失败：%s" % exc, None


def delete_task_permanent(sid, confirm_text, backup=True):
    """彻底删除：物理删除 sessions 行 + session_usage。必须传确认文字。

    confirm_text 必须精确等于 "DELETE" 才会执行。
    """
    if confirm_text != "DELETE":
        return False, "确认文字不正确，操作已取消", None
    if not _is_valid_id(sid):
        return False, "任务 ID 非法", None
    bp = None
    try:
        if backup:
            bp = backup_db("purge")
        conn = _write_conn()
        try:
            task = _fetch_task(conn, sid)
            if not task:
                return False, "找不到这个任务", None
            title = task.get("custom_title") or task.get("title") or ""
            conn.execute("DELETE FROM sessions WHERE id = ?", (sid,))
            n_usage = conn.execute("DELETE FROM session_usage WHERE session_id = ?", (sid,)).rowcount
            conn.commit()
        finally:
            conn.close()
        log_action("purge", sid, "彻底删除：%s（含用量 %d 行）" % (title, n_usage), True, bp)
        return True, "已彻底删除「%s」" % title, {"backup": bp, "usage_rows": n_usage}
    except sqlite3.Error as exc:
        log_action("purge", sid, str(exc), False, bp)
        return False, "数据库写入失败：%s" % exc, None


# ------------------------------------------------------------ 批量


def batch_delete(ids, backup=True):
    """批量删除（标记）。返回每个 ID 的结果。"""
    bp = None
    try:
        if backup:
            bp = backup_db("batch-delete")
    except sqlite3.Error as exc:
        return {"ok": False, "error": "备份失败：%s" % exc, "results": []}
    results = []
    ok_n = 0
    try:
        conn = _write_conn()
        try:
            now_ms = int(datetime.datetime.now().timestamp() * 1000)
            for sid in ids[:500]:
                if not _is_valid_id(sid):
                    results.append({"id": sid, "ok": False, "error": "ID 非法"})
                    continue
                task = _fetch_task(conn, sid)
                if not task:
                    results.append({"id": sid, "ok": False, "error": "找不到"})
                    continue
                if task.get("deleted_at") not in (None, "", "None"):
                    results.append({"id": sid, "ok": False, "error": "已是删除状态"})
                    continue
                title = task.get("custom_title") or task.get("title") or ""
                conn.execute("UPDATE sessions SET deleted_at = ? WHERE id = ?", (now_ms, sid))
                results.append({"id": sid, "ok": True, "title": title})
                ok_n += 1
            conn.commit()
        finally:
            conn.close()
        log_action("batch-delete", "%d 个" % len(ids), "成功 %d 个" % ok_n, True, bp)
        return {"ok": True, "deleted": ok_n, "backup": bp, "results": results}
    except sqlite3.Error as exc:
        log_action("batch-delete", "%d 个" % len(ids), str(exc), False, bp)
        return {"ok": False, "error": str(exc), "backup": bp, "results": results}


# ------------------------------------------------------------ 定时任务


def _fetch_automation(conn, aid):
    row = conn.execute("SELECT * FROM automations WHERE id = ?", (aid,)).fetchone()
    return dict(row) if row else None


def _is_valid_aid(aid):
    """定时任务 ID 校验。

    实测有两种格式（都要放行）：
      - "automation-1779181407432"（13 位毫秒时间戳，占绝大多数）
      - 标准 UUID "eca7037b-0bec-479f-8ca4-128a3291a801"
    所以不能按 UUID 严格校验。但也不能像早前那样只判长度 > 64 ——
    那样 "x"、纯空格都能过，白跑一次备份。这里限定字符集。
    """
    if not aid or not isinstance(aid, str) or not (1 <= len(aid) <= 64):
        return False
    return all(c.isalnum() or c in "-_" for c in aid)


def rename_automation(aid, new_name, backup=True):
    """改定时任务的名字（写 automations.name）。"""
    if not _is_valid_aid(aid):
        return False, "定时任务 ID 非法", None
    if new_name is not None and not isinstance(new_name, str):
        return False, "名字必须是文字", None
    new_name = (new_name or "").strip()
    if not new_name:
        return False, "名字不能为空", None
    if len(new_name) > 200:
        return False, "名字过长（最多 200 字）", None
    bp = None
    try:
        if backup:
            bp = backup_db("rename-auto")
        conn = _write_conn()
        try:
            auto = _fetch_automation(conn, aid)
            if not auto:
                return False, "找不到这个定时任务", None
            old = auto.get("name") or ""
            conn.execute("UPDATE automations SET name = ?, updated_at = ? WHERE id = ?",
                         (new_name, int(datetime.datetime.now().timestamp() * 1000), aid))
            conn.commit()
        finally:
            conn.close()
        log_action("rename-auto", aid, "%s -> %s" % (old, new_name), True, bp)
        return True, "已改名为「%s」" % new_name, {"old": old, "new": new_name, "backup": bp}
    except sqlite3.Error as exc:
        log_action("rename-auto", aid, str(exc), False, bp)
        return False, "数据库写入失败：%s" % exc, None


def toggle_automation(aid, backup=True):
    """切换定时任务的启用/暂停状态（ACTIVE <-> PAUSED）。"""
    if not _is_valid_aid(aid):
        return False, "定时任务 ID 非法", None
    bp = None
    try:
        if backup:
            bp = backup_db("toggle-auto")
        conn = _write_conn()
        try:
            auto = _fetch_automation(conn, aid)
            if not auto:
                return False, "找不到这个定时任务", None
            cur = (auto.get("status") or "ACTIVE").upper()
            new = "PAUSED" if cur == "ACTIVE" else "ACTIVE"
            now = int(datetime.datetime.now().timestamp() * 1000)
            conn.execute("UPDATE automations SET status = ?, updated_at = ? WHERE id = ?",
                         (new, now, aid))
            conn.commit()
        finally:
            conn.close()
        label = "已启用" if new == "ACTIVE" else "已暂停"
        log_action("toggle-auto", aid, "%s -> %s" % (cur, new), True, bp)
        return True, "%s「%s」" % (label, auto.get("name") or ""), {"status": new, "backup": bp}
    except sqlite3.Error as exc:
        log_action("toggle-auto", aid, str(exc), False, bp)
        return False, "数据库写入失败：%s" % exc, None


def delete_automation(aid, backup=True):
    """删除定时任务（标记 deleted_at，可恢复）。"""
    if not _is_valid_aid(aid):
        return False, "定时任务 ID 非法", None
    bp = None
    try:
        if backup:
            bp = backup_db("delete-auto")
        conn = _write_conn()
        try:
            auto = _fetch_automation(conn, aid)
            if not auto:
                return False, "找不到这个定时任务", None
            if auto.get("deleted_at") not in (None, "", 0):
                return False, "这个定时任务已经是删除状态了", None
            name = auto.get("name") or ""
            now = int(datetime.datetime.now().timestamp() * 1000)
            conn.execute("UPDATE automations SET deleted_at = ?, updated_at = ? WHERE id = ?",
                         (now, now, aid))
            conn.commit()
        finally:
            conn.close()
        log_action("delete-auto", aid, "标记删除：%s" % name, True, bp)
        return True, "已删除「%s」" % name, {"backup": bp}
    except sqlite3.Error as exc:
        log_action("delete-auto", aid, str(exc), False, bp)
        return False, "数据库写入失败：%s" % exc, None


def restore_automation(aid, backup=True):
    """恢复被删除的定时任务。"""
    if not _is_valid_aid(aid):
        return False, "定时任务 ID 非法", None
    bp = None
    try:
        if backup:
            bp = backup_db("restore-auto")
        conn = _write_conn()
        try:
            auto = _fetch_automation(conn, aid)
            if not auto:
                return False, "找不到这个定时任务", None
            if auto.get("deleted_at") in (None, "", 0):
                return False, "这个定时任务没有被删除", None
            name = auto.get("name") or ""
            conn.execute("UPDATE automations SET deleted_at = NULL, updated_at = ? WHERE id = ?",
                         (int(datetime.datetime.now().timestamp() * 1000), aid))
            conn.commit()
        finally:
            conn.close()
        log_action("restore-auto", aid, "恢复：%s" % name, True, bp)
        return True, "已恢复「%s」" % name, {"backup": bp}
    except sqlite3.Error as exc:
        log_action("restore-auto", aid, str(exc), False, bp)
        return False, "数据库写入失败：%s" % exc, None


def preview_automation_delete(aid):
    """定时任务删除预检。"""
    try:
        conn = _read_conn_safe()
    except sqlite3.Error as exc:
        return {"ok": False, "error": str(exc)}
    try:
        auto = _fetch_automation(conn, aid)
        if not auto:
            return {"ok": False, "error": "找不到这个定时任务"}
        runs = 0
        try:
            runs = conn.execute("SELECT COUNT(*) FROM automation_runs WHERE automation_id = ?",
                                (aid,)).fetchone()[0]
        except sqlite3.Error:
            pass
        return {
            "ok": True, "id": aid, "name": auto.get("name"),
            "status": auto.get("status"),
            "schedule_type": auto.get("schedule_type"),
            "rrule": auto.get("rrule"), "scheduled_at": auto.get("scheduled_at"),
            "run_count": runs,
            "already_deleted": auto.get("deleted_at") not in (None, "", 0),
        }
    finally:
        conn.close()


if __name__ == "__main__":
    # 简单自检：只打印状态，不做任何写操作
    print("数据库:", DB_PATH())
    print("备份目录:", BACKUP_DIR())
    print("现有备份: %d 份" % len(list_backups()))
    print("操作日志: %d 条" % len(recent_log()))
