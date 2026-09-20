#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
WorkBuddy 管理中心 · Python 解释器查找
========================================
给 .bat / .vbs 启动器用的：把「找一个能用的 Python」这件事从启动脚本里搬出来。

**为什么要搬**：
早前三个 .bat 和 .vbs 各自写死了带完整版本号的解释器路径
（形如 …/versions/<版本号>/python.exe）。版本号一变（WorkBuddy 升级自带 Python），
启动器就全废了，而且改的时候要改四个文件、五个地方，漏一个就出现
「这个能启动那个不能」的怪现象。

现在版本号只出现在这一个文件里，而且是**扫描目录得出**的，不是写死的字符串。

输出：找到就打印绝对路径，找不到打印空并以退出码 1 结束。

    python tools/findpy.py            # 找 python.exe（控制台版）
    python tools/findpy.py --windowed # 找 pythonw.exe（无窗口版）
"""

import argparse
import os
import sys

# WorkBuddy 自带的 Python 放在这里。把解释器排在第一位是有意的：
# 系统 PATH 里的 python 可能是 32 位、可能是 Microsoft Store 的占位程序
# （运行它会弹应用商店而不是报错），自带的那个才是确定可用的。
WB_PY_ROOT = os.path.join(os.path.expanduser("~"), ".workbuddy", "binaries",
                          "python", "versions")


def _version_key(name):
    """把 '3.13.12' 变成 (3, 13, 12) 好排序。

    不能用字符串排序 —— 那样 '3.9' > '3.13'（因为 '9' > '1'），
    结果会挑到一个更老的版本。
    """
    parts = []
    for seg in name.split("."):
        try:
            parts.append(int(seg))
        except ValueError:
            parts.append(0)
    return tuple(parts)


def bundled_candidates(exec_name):
    """扫描 WorkBuddy 自带的 Python 版本目录，**新版本优先**。"""
    if not os.path.isdir(WB_PY_ROOT):
        return []
    try:
        names = [n for n in os.listdir(WB_PY_ROOT)
                 if os.path.isdir(os.path.join(WB_PY_ROOT, n))]
    except OSError:
        return []

    out = []
    for name in sorted(names, key=_version_key, reverse=True):
        exe = os.path.join(WB_PY_ROOT, name, exec_name)
        if os.path.isfile(exe):
            out.append(exe)
    return out


def path_candidates(exec_name):
    """PATH 里的候选。"""
    out = []
    for d in (os.environ.get("PATH") or "").split(os.pathsep):
        d = d.strip('"').strip()
        if not d:
            continue
        exe = os.path.join(d, exec_name)
        if os.path.isfile(exe):
            out.append(exe)
    return out


def common_candidates(exec_name):
    """常见安装位置。

    盘符不写死：从 SystemDrive 环境变量取（Windows 上必然存在），
    没有就跳过这一组候选。写死盘符只在作者那台机器上成立，
    换台机器就是永远匹配不上的死分支。
    """
    out = []
    local = os.environ.get("LOCALAPPDATA") or ""
    if local:
        for ver in ("Python313", "Python312", "Python311", "Python310"):
            out.append(os.path.join(local, "Programs", "Python", ver, exec_name))

    # 老式「装在盘符根目录」的安装方式，例如 <系统盘>:\Python313\python.exe
    drive = os.environ.get("SystemDrive") or ""
    if drive:
        root = drive.rstrip("\\/") + os.sep
        for ver in ("313", "312", "311", "310"):
            out.append(os.path.join(root, "Python" + ver, exec_name))
    return out


def find(exec_name):
    for group in (bundled_candidates(exec_name),
                  path_candidates(exec_name),
                  common_candidates(exec_name)):
        for exe in group:
            if os.path.isfile(exe):
                return exe
    return None


def main():
    ap = argparse.ArgumentParser(add_help=True)
    ap.add_argument("--windowed", action="store_true",
                    help="找 pythonw.exe（无窗口）而不是 python.exe")
    ap.add_argument("--list", action="store_true",
                    help="列出所有候选及可用性，用于排查")
    args = ap.parse_args()

    exec_name = "pythonw.exe" if args.windowed else "python.exe"
    if os.name != "nt":
        exec_name = "python3" if not args.windowed else "python3"

    if args.list:
        print("WorkBuddy 自带目录：%s" % WB_PY_ROOT)
        print("  存在：%s" % os.path.isdir(WB_PY_ROOT))
        for label, group in (("自带", bundled_candidates(exec_name)),
                             ("PATH", path_candidates(exec_name)),
                             ("常见位置", common_candidates(exec_name))):
            print("\n[%s]" % label)
            if not group:
                print("  (无)")
            for exe in group:
                print("  %s %s" % ("[有]" if os.path.isfile(exe) else "[无]", exe))

    found = find(exec_name)
    if found:
        print(found)
        return 0
    sys.stderr.write("找不到 %s\n" % exec_name)
    return 1


if __name__ == "__main__":
    sys.exit(main())
