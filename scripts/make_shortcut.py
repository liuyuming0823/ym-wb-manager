#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
WorkBuddy 管理中心 · 创建桌面快捷方式
========================================
在桌面创建「WorkBuddy 管理中心」快捷方式，双击即启动（无黑窗口）。

用 VBScript 的 WScript.Shell 创建 .lnk，不依赖任何第三方库。

用法：
    python make_shortcut.py                     # 在桌面创建
    python make_shortcut.py --name "我的面板"
    python make_shortcut.py --remove            # 删除快捷方式
    python make_shortcut.py --start-menu        # 同时放进开始菜单
"""

import argparse
import os
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
VBS = os.path.join(ROOT, "启动管理中心.vbs")
DEFAULT_NAME = "WorkBuddy 管理中心"


def desktop_dir():
    """拿到桌面路径（兼容 OneDrive 重定向的桌面）。"""
    try:
        out = subprocess.run(
            ["powershell", "-NoProfile", "-NonInteractive", "-Command",
             "[Environment]::GetFolderPath('Desktop')"],
            capture_output=True, timeout=20,
        )
        p = out.stdout.decode("utf-8", errors="replace").strip()
        if p and os.path.isdir(p):
            return p
    except Exception:
        pass
    home = os.path.expanduser("~")
    for cand in (os.path.join(home, "Desktop"), os.path.join(home, "OneDrive", "Desktop")):
        if os.path.isdir(cand):
            return cand
    return os.path.join(home, "Desktop")


def start_menu_dir():
    appdata = os.environ.get("APPDATA") or os.path.join(os.path.expanduser("~"), "AppData", "Roaming")
    return os.path.join(appdata, "Microsoft", "Windows", "Start Menu", "Programs")


def make_link(lnk_path, name, target, args, workdir, icon, desc):
    """用 VBScript 写一个 .lnk。"""
    vbs = "\n".join([
        'Set sh = CreateObject("WScript.Shell")',
        'Set lnk = sh.CreateShortcut("%s")' % lnk_path.replace('"', '""'),
        'lnk.TargetPath = "%s"' % target.replace('"', '""'),
        'lnk.Arguments = "%s"' % args.replace('"', '""'),
        'lnk.WorkingDirectory = "%s"' % workdir.replace('"', '""'),
        'lnk.Description = "%s"' % desc.replace('"', '""'),
        'lnk.WindowStyle = 7',   # 7 = 最小化，配合 wscript 基本看不到窗口
        'lnk.IconLocation = "%s"' % icon.replace('"', '""'),
        'lnk.Save',
    ])
    tmp = os.path.join(os.environ.get("TEMP") or HERE, "_mk_lnk.vbs")
    with open(tmp, "w", encoding="gbk", errors="replace") as fh:
        fh.write(vbs)
    try:
        cmd = ["cscript", "//nologo", tmp]
        out = subprocess.run(cmd, capture_output=True, timeout=25)
        if out.returncode == 0 and os.path.exists(lnk_path):
            return True, ""
        # cscript 落空（被安全策略拦、被裁掉）时再试 wscript —— 两者
        # 用的是同一个脚本引擎，换一个宿主往往就能过。
        out2 = subprocess.run(["wscript", "//nologo", tmp],
                              capture_output=True, timeout=25)
        if out2.returncode == 0 and os.path.exists(lnk_path):
            return True, ""
        err = (out.stderr or b"") + (out2.stderr or b"")
        return False, err.decode("gbk", errors="replace")
    except Exception as exc:              # noqa: BLE001
        return False, str(exc)
    finally:
        try:
            os.remove(tmp)
        except OSError:
            pass


def main_quiet(name=DEFAULT_NAME, start_menu=False):
    """给程序内部调用的入口：不解析命令行、不打印。

    返回 0 表示快捷方式已经存在（不管是刚建的还是本来就有）。
    创建失败返回 1 —— 调用方可以据此决定要不要提示用户。
    """
    targets = [os.path.join(desktop_dir(), name + ".lnk")]
    if start_menu:
        targets.append(os.path.join(start_menu_dir(), name + ".lnk"))

    if not os.path.exists(VBS):
        return 1

    # wscript.exe 的位置从环境变量推导，不写死盘符：
    # SystemRoot / WINDIR 在 Windows 上必然存在；万一都被裁掉，
    # 就退回裸命令名交给系统去 PATH 里找，而不是硬猜某个盘符。
    _sysroot = os.environ.get("SystemRoot") or os.environ.get("WINDIR")
    wscript = (os.path.join(_sysroot, "System32", "wscript.exe")
               if _sysroot else "wscript.exe")
    icon = wscript if os.path.exists(wscript) else "shell32.dll,21"

    ok_any = False
    for t in targets:
        ok, _err = make_link(
            t, name, wscript, '"%s"' % VBS, ROOT, icon,
            "启动 WorkBuddy 管理中心（无窗口）",
        )
        if ok and os.path.exists(t):
            ok_any = True
    return 0 if ok_any else 1


def main():
    console.fix()
    ap = argparse.ArgumentParser(description="创建/删除桌面快捷方式")
    ap.add_argument("--name", default=DEFAULT_NAME)
    ap.add_argument("--remove", action="store_true", help="删除快捷方式")
    ap.add_argument("--start-menu", action="store_true", help="同时放进开始菜单")
    args = ap.parse_args()

    targets = [os.path.join(desktop_dir(), args.name + ".lnk")]
    if args.start_menu:
        targets.append(os.path.join(start_menu_dir(), args.name + ".lnk"))

    if args.remove:
        n = 0
        for t in targets:
            if os.path.exists(t):
                try:
                    os.remove(t)
                    print("  已删除：%s" % t)
                    n += 1
                except OSError as exc:
                    print("  删除失败 %s：%s" % (t, exc))
        if not n:
            print("  没有找到快捷方式。")
        return 0

    if not os.path.exists(VBS):
        print("  找不到启动器：%s" % VBS, file=sys.stderr)
        return 1

    rc = main_quiet(name=args.name, start_menu=args.start_menu)
    for t in targets:
        if os.path.exists(t):
            print("  已创建：%s" % t)
        else:
            print("  创建失败：%s" % t)
    return rc


if __name__ == "__main__":
    sys.exit(main())
