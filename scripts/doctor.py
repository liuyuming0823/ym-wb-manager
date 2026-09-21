#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
WorkBuddy 管理中心 · 环境体检
================================
一条命令告诉你「这台机器上能不能正常跑这个工具」，以及不能的话差什么。

    python tools/doctor.py            # 人话报告
    python tools/doctor.py --json     # 给页面用的结构化结果

**为什么需要它**：
这个工具原本是给自己写的，路径、Python 版本、端口全是写死的。改成通用工具
分享给别人之后，遇到问题的第一现场变成了「别人的电脑」—— 那里没有我，
只有一句「双击没反应」。环境体检就是把「我打个远程电话问半天」变成
「跑一下这条命令，把结果发我」。

检查分三档：
  ok    正常
  warn  能跑，但功能会残缺（比如资料库是空的）
  bad   跑不起来，必须先解决

退出码：0 全部正常（允许有 warn）/ 1 存在 bad 项
"""

import argparse
import json
import os
import socket
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, HERE)


def _ok(name, detail, hint=None):
    return {"level": "ok", "name": name, "detail": detail, "hint": hint}


def _warn(name, detail, hint=None):
    return {"level": "warn", "name": name, "detail": detail, "hint": hint}


def _bad(name, detail, hint=None):
    return {"level": "bad", "name": name, "detail": detail, "hint": hint}


# ---------------------------------------------------------------- 各项检查

def check_python():
    v = sys.version_info
    ver = "%d.%d.%d" % (v.major, v.minor, v.micro)
    if v < (3, 8):
        return _bad("Python 版本", "%s（太老）" % ver,
                    "需要 Python 3.8 或更新。建议直接装 WorkBuddy，它会自带一个。")
    if v < (3, 10):
        return _warn("Python 版本", "%s" % ver,
                     "能用，但 3.10 以下没测过，遇到奇怪问题可以先升级。")
    return _ok("Python 版本", "%s（%s）" % (ver, sys.executable))


def check_tools_dir():
    """确认配套脚本都在。缺文件是最常见的「解压不完整」症状。"""
    need = ["serve.py", "scan.py", "data.py", "ops.py", "config.py", "findpy.py"]
    missing = [n for n in need if not os.path.exists(os.path.join(HERE, n))]
    if missing:
        return _bad("程序文件", "缺少 %d 个：%s" % (len(missing), "、".join(missing)),
                    "解压可能不完整。重新解压一次整个文件夹。")
    return _ok("程序文件", "%d 个脚本齐全" % len(need))


def check_template():
    tpl = os.path.join(ROOT, "assets", "template.html")
    if not os.path.exists(tpl):
        return _bad("页面模板", "找不到 assets/template.html",
                    "解压可能不完整。重新解压一次整个文件夹。")
    size = os.path.getsize(tpl)
    if size < 10000:
        return _warn("页面模板", "只有 %d 字节，看起来不完整" % size,
                     "重新解压一次整个文件夹。")
    return _ok("页面模板", "%.0f KB" % (size / 1024.0))


def check_workbuddy_dir(cfg):
    wb = cfg["workbuddy_dir"]
    if not os.path.isdir(wb):
        return _bad("WorkBuddy 数据目录", "%s（不存在）" % wb,
                    "如果这台电脑没装/没用过 WorkBuddy，这个工具就没有数据可看。"
                    "装好并启动一次 WorkBuddy 再回来。")
    return _ok("WorkBuddy 数据目录", wb)


def check_database(cfg):
    import config as conf
    db = conf.db_path()
    if not os.path.exists(db):
        return _warn("数据库", "%s（还没有）" % db,
                     "启动一次 WorkBuddy 让它建库，数据就会出现。")
    size = os.path.getsize(db)
    return _ok("数据库", "%.1f MB" % (size / 1024.0 / 1024.0))


def check_sessions_dir(cfg):
    p = os.path.join(cfg["workbuddy_dir"], "projects")
    if not os.path.isdir(p):
        return _warn("会话记录目录", "%s（不存在）" % p,
                     "「任务」页可能显示不全。正常用过 WorkBuddy 就会有。")
    try:
        n = len(os.listdir(p))
    except OSError:
        n = 0
    return _ok("会话记录目录", "%d 个会话目录" % n)


def check_library(cfg):
    import config as conf
    allr = conf.library_roots()
    live = [r for r in allr if not r.get("missing")]
    dead = [r for r in allr if r.get("missing")]
    if not allr:
        return _warn("资料库目录", "一个都没有",
                     "「资料库」页会是空的。可以在设置页手动添加目录。")
    if not live:
        return _warn("资料库目录", "%d 个配置项全部失效" % len(dead),
                     "目录都被删了或换了电脑。去设置页重新添加。")
    if dead:
        return _warn("资料库目录", "%d 个可用，%d 个已失效" % (len(live), len(dead)),
                     "失效的：%s" % "；".join(r["path"] for r in dead[:3]))
    return _ok("资料库目录", "%d 个可用" % len(live))


def check_config_writable(cfg):
    import config as conf
    d = os.path.dirname(conf.CONFIG_PATH)
    probe = os.path.join(d, ".write_probe")
    try:
        with open(probe, "w") as fh:
            fh.write("x")
        os.remove(probe)
    except OSError as exc:
        return _warn("配置可写", "写不进去：%s" % exc,
                     "设置页改了保存不上。可能是文件夹设了只读，"
                     "或者放在需要管理员权限的位置。")
    return _ok("配置可写", "配置可以正常保存")


def check_port(cfg):
    """端口是否可用。

    注意区分三件事：
      - 端口被**本工具自己**占着 → 说明已经开着了，属于 ok（不用重复启动）
      - 端口被**别的程序**占着 → 服务会自动往后找，属于 warn
      - 端口空着 → ok

    两个坑，都是实测踩出来的：
      1. **必须显式绕过代理**。沙箱 / 企业环境里的 http_proxy 会劫持
         127.0.0.1 的请求，表现为超时或 502，看着像「端口被别的程序占了」，
         实际是自己人。alive.py 里已经有同样的处理。
      2. **首次请求要给足超时**。/api/data 第一次是冷启动采集，实测要 ~800ms，
         之前给 0.8s 正好卡在边界上，时快时慢 —— 报出的结论也就时对时错。
         这里给 3s：探测慢一点没关系，结论错了才要命。
    """
    port = cfg.get("port", 8777)

    def listening(p):
        s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        s.settimeout(0.3)
        try:
            return s.connect_ex(("127.0.0.1", p)) == 0
        finally:
            s.close()

    if not listening(port):
        return _ok("端口 %d" % port, "可用")

    # 端口有监听 —— 确认是不是我们自己（绕过代理，给足超时）
    try:
        import urllib.request
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
        req = urllib.request.Request("http://127.0.0.1:%d/api/data" % port)
        with opener.open(req, timeout=3.0) as resp:
            body = resp.read().decode("utf-8", "replace")
        if '"stats"' in body and '"version"' in body:
            return _ok("端口 %d" % port, "管理中心已经在运行")
    except Exception:
        pass

    return _warn("端口 %d" % port, "被别的程序占用了",
                 "服务会自动往后找空端口（最多试 10 个），一般不影响使用。"
                 "想固定下来可以在设置页换一个端口。")


def check_browser_open():
    """检查能不能调起浏览器（体检只是确认模块可用）。"""
    try:
        import webbrowser  # noqa: F401
        return _ok("浏览器调用", "可用")
    except Exception as exc:
        return _warn("浏览器调用", "不可用：%s" % exc,
                     "启动后不会自动弹浏览器，手动打开提示的地址即可。")


def check_backup_dir(cfg):
    import config as conf
    d = os.path.join(cfg["workbuddy_dir"], "_wbmanager_backups")
    if not os.path.isdir(d):
        return _ok("备份目录", "还没建（第一次写操作时自动创建）")
    try:
        n = len([f for f in os.listdir(d) if f.endswith(".db")])
    except OSError:
        n = 0
    keep = cfg.get("keep_backups", 8)
    return _ok("备份目录", "%d 份（上限 %d）" % (n, keep))


CHECK_GROUPS = [
    ("运行环境", [check_python, check_tools_dir, check_template]),
    ("WorkBuddy 数据", [check_workbuddy_dir, check_database, check_sessions_dir]),
    ("本工具配置", [check_library, check_config_writable, check_backup_dir]),
    ("网络与端口", [check_port, check_browser_open]),
]


def run():
    import config as conf
    cfg, created = conf.ensure()
    results = []
    # 有的检查项不关心配置（比如 Python 版本、模板文件），所以统一用
    # 参数个数判断要不要传 cfg —— 比给每个无参函数硬塞一个用不上的形参干净。
    import inspect
    for group, fns in CHECK_GROUPS:
        for fn in fns:
            try:
                nargs = len(inspect.signature(fn).parameters)
                item = fn(cfg) if nargs else fn()
            except Exception as exc:
                item = _warn(fn.__name__, "检查时出错：%s" % exc)
            item["group"] = group
            results.append(item)
    return {
        "ok": True,
        "config_created": created,
        "config_path": conf.CONFIG_PATH,
        "results": results,
        "summary": {
            "ok": sum(1 for r in results if r["level"] == "ok"),
            "warn": sum(1 for r in results if r["level"] == "warn"),
            "bad": sum(1 for r in results if r["level"] == "bad"),
        },
    }


MARK = {"ok": "[OK]", "warn": "[注意]", "bad": "[问题]"}


def main():
    ap = argparse.ArgumentParser(description="WorkBuddy 管理中心环境体检")
    ap.add_argument("--json", action="store_true", help="输出 JSON（给页面用）")
    args = ap.parse_args()

    try:
        import console
        console.fix()
    except Exception:
        pass

    rep = run()

    if args.json:
        print(json.dumps(rep, ensure_ascii=False, indent=2))
        return 1 if rep["summary"]["bad"] else 0

    print()
    print("=" * 62)
    print("  WorkBuddy 管理中心 · 环境体检")
    print("=" * 62)

    last = None
    for r in rep["results"]:
        if r["group"] != last:
            print()
            print("  %s" % r["group"])
            last = r["group"]
        print("    %-6s %-16s %s" % (MARK[r["level"]], r["name"], r["detail"]))
        if r["hint"]:
            print("           %s" % ("→ " + r["hint"]))

    s = rep["summary"]
    print()
    print("-" * 62)
    print("  正常 %d · 注意 %d · 问题 %d" % (s["ok"], s["warn"], s["bad"]))
    if s["bad"]:
        print()
        print("  有必须先解决的问题（上面标 [问题] 的），")
        print("  按每一项下面的 → 提示处理后重新体检。")
    elif s["warn"]:
        print()
        print("  可以正常使用。标 [注意] 的只是功能会残缺，不影响启动。")
    else:
        print()
        print("  一切正常，可以用了。")
    print()
    print("  配置文件：%s" % rep["config_path"])

    # 体检通过之后，一定要明确告诉用户「下一步做什么」。
    # 这是凭空多出来的一步：体检结果本身只说明环境没问题，
    # 而用户拿到一堆 .py 文件时最想问的就是「那我怎么打开它」。
    # 只有环境真的能跑时才引导，否则等于在坏地基上教人进门。
    if not s["bad"]:
        has_icon = False
        try:
            import scan as _scan
            # quiet：体检输出里不该混进「跳过（已存在）」这种噪声，
            # 用户要看的是「接下来怎么打开」。
            _scan._ensure_launchers(quiet=True)
            has_icon = _scan._ensure_shortcut(quiet=True)
        except Exception:                 # noqa: BLE001 - 引导失败不影响体检结论
            pass
        print()
        print("  下一步 —— 打开管理中心：")
        print()
        if has_icon:
            print("    桌面双击「WorkBuddy 管理中心」图标")
            print("    （图标不好找的话，双击目录里的 启动管理中心.vbs）")
        else:
            print("    双击目录里的 启动管理中心.vbs")
        print()
        print("  第一次用建议先看一眼：说明.md")
        print()
    return 1 if s["bad"] else 0


if __name__ == "__main__":
    sys.exit(main())
