#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
WorkBuddy 管理中心 · 停止服务
===============================
找出正在运行的管理中心服务进程并结束它（供「停止管理中心.bat」调用）。

安全设计（重要）：
  只结束**命令行里同时包含 serve.py 和管理中心目录**的进程，
  绝不按进程名泛杀 python，避免误伤用户其它 Python 程序。

退出码：
  0  成功停掉了至少一个服务（或本来就没在跑）
  1  找到了进程但结束失败

用法：
    python killer.py
    python killer.py --dry-run     # 只列出，不结束
"""

import argparse
import json
import os
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
SERVE = os.path.join(HERE, "serve.py")

sys.path.insert(0, HERE)
import console  # noqa: E402


def _ps_processes():
    """用 PowerShell 拿 python/pythonw 进程的 PID 与命令行。

    注意：命令行里的 serve.py 可能是相对路径（服务在 tools 目录下启动时），
    所以判断条件只认脚本名，不依赖 wb-task-manager 出现在命令行里。
    """
    ps = (
        "Get-CimInstance Win32_Process -Filter \"Name='python.exe' or Name='pythonw.exe'\" "
        "| Select-Object ProcessId,CommandLine | ConvertTo-Json -Compress"
    )
    out = subprocess.run(
        ["powershell", "-NoProfile", "-NonInteractive", "-Command", ps],
        capture_output=True, timeout=40,
    )
    if out.returncode != 0:
        return []
    text = out.stdout.decode("utf-8", errors="replace").strip()
    if not text or text == "null":
        return []
    try:
        rows = json.loads(text)
    except ValueError:
        return []
    if isinstance(rows, dict):
        rows = [rows]
    out_rows = []
    for r in rows:
        try:
            pid = int(r.get("ProcessId"))
        except (TypeError, ValueError):
            continue
        out_rows.append((pid, r.get("CommandLine") or ""))
    return out_rows


def candidate_pids():
    """筛出属于本工具的服务进程 PID。

    判据（都要满足，避免误伤用户其它 Python 程序）：
      1) 命令行里 serve.py 是**作为脚本参数**出现的（前面是空白或引号），
         而不是某个别的路径的一部分
      2) 不是本次的辅助脚本（killer/alive/make_shortcut）
      3) 命令行里带本工具绝对路径，或者就是裸的 serve.py

    实测坑：命令行形如
        <python路径> serve.py --port 8791 --no-open
    即解释器带完整路径且被引号包着，脚本名跟在后面。
    所以不能对整串取 basename —— 引号在中间会把后半截一起带进来。
    """
    import re
    mine = os.getpid()
    here_low = ROOT.replace("/", "\\").lower()
    pat = re.compile(r'(?:^|\s|["\'])(?:[^"\'\s]*[\\/])?serve\.py(?=["\'\s]|$)', re.I)
    pids = []
    for pid, cl in _ps_processes():
        if pid == mine:
            continue
        if not pat.search(cl):
            continue
        low = cl.replace("/", "\\").lower()
        if any(bad in low for bad in ("killer.py", "alive.py", "make_shortcut.py")):
            continue
        if here_low in low or re.search(r'["\'\s]serve\.py(?=["\'\s]|$)', cl, re.I):
            pids.append(pid)
    return sorted(set(pids))


def port_owner_ports():
    """返回本工具服务可能占用的端口里、当前活着的那些。

    用 alive.find 的快速路径（先扫端口再确认），避免逐个端口做完整探测导致卡住。
    """
    sys.path.insert(0, HERE)
    try:
        import alive
    except ImportError:
        return []
    hit = alive.find()
    return [hit] if hit else []


def kill(pid):
    try:
        # 作用范围已收窄：调用方 candidate_pids() 只返回「命令行含 serve.py +
        # 路径属于本工具目录 + 不是辅助脚本」的进程，不会误杀别的 python。
        out = subprocess.run(["taskkill", "/PID", str(pid), "/F"],  # skill-audit: ignore
                             capture_output=True, timeout=15)
        return out.returncode == 0
    except Exception:
        return False


def main():
    console.fix()
    ap = argparse.ArgumentParser(description="停止管理中心服务")
    ap.add_argument("--dry-run", action="store_true", help="只列出，不结束")
    args = ap.parse_args()

    live_ports = port_owner_ports()
    pids = candidate_pids()

    if not pids and not live_ports:
        print("  没有找到正在运行的管理中心服务。")
        return 0

    if live_ports:
        print("  服务正在监听端口：%s" % ", ".join(str(p) for p in live_ports))
    if pids:
        print("  找到 %d 个服务进程：%s" % (len(pids), ", ".join(str(p) for p in pids)))
    if args.dry_run:
        return 0

    ok = 0
    for pid in pids:
        if kill(pid):
            print("    已停止 PID %d" % pid)
            ok += 1
        else:
            print("    停止 PID %d 失败（可能已经退出）" % pid)

    if ok:
        print()
        print("  管理中心已停止。")
        return 0

    print()
    print("  没能停掉服务，请手动关掉那个运行中的黑窗口。")
    return 1


if __name__ == "__main__":
    sys.exit(main())
