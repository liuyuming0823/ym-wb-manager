#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
WorkBuddy 管理中心 · 静态页生成器
====================================
采集本机 WorkBuddy 数据，生成一个自包含的单文件 HTML（index.html）。

静态页能看、能搜、能点开任务，但不能改名 / 删除 ——
那两个动作需要本地服务（启动管理中心.bat）。

用法：
    python scan.py                 # 采集 + 生成 HTML
    python scan.py --open          # 生成后打开浏览器
    python scan.py -o 路径.html    # 指定输出路径
    python scan.py --json 数据.json  # 同时导出原始数据
"""

import argparse
import json
import os
import sys
import webbrowser

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
DEFAULT_OUT = os.path.join(ROOT, "index.html")
TEMPLATE = os.path.join(ROOT, "assets", "template.html")

sys.path.insert(0, HERE)
import console  # noqa: E402


def collect():
    import data as datalayer  # noqa: E402
    return datalayer.snapshot()


def _ensure_launchers():
    """首次运行时补齐双击启动器（幂等，已存在就不动）。

    做成「静默、失败不阻断」：生成启动器只是锦上添花，
    它自己出问题不该让整个采集流程挂掉 —— 用户依然可以直接跑
    `python serve.py` 启动。
    """
    try:
        import make_launchers as mk
    except ImportError:
        return
    try:
        made, _skipped = mk.write_all(force=False)
    except Exception as exc:          # noqa: BLE001 - 任何异常都不该阻断采集
        print("  [提示] 启动器生成失败（不影响使用）：%s" % exc)
        return
    if made:
        print()
        print("  已生成 %d 个双击启动器，下次可以直接用：" % made)
        print("    启动管理中心.bat   有窗口，看得见日志（排查问题用）")
        print("    启动管理中心.vbs   无窗口静默启动（日常用）")
        print("    停止管理中心.bat   停掉正在运行的服务")
        print("    刷新数据.bat       重新采集数据并生成静态页")
        print()


def first_run_check():
    """首次运行引导。

    别人拿到这个工具时，最可能的两种「打开没反应」是：
      1. 本机没跑过 WorkBuddy —— 没有数据目录，采集出来是空的，
         页面上所有数字都是 0，看着像工具坏了；
      2. Python 版本太老 —— 语法就过不去。
    与其让人对着空页面猜，不如在开始采集之前先把这两件事讲清楚。

    返回 True 表示可以继续，False 表示环境不满足、没必要往下走。
    """
    import config as conf  # noqa: E402

    cfg, created = conf.ensure(verbose=True)
    if created:
        print()
        print("  首次运行，已根据本机情况生成配置：%s" % conf.CONFIG_PATH)
        print("  资料库目录是自动探测出来的，不合适可以改这个文件，")
        print("  或者在页面「设置」里增删。")
        print()

    # 补上双击入口。
    #
    # .bat / .vbs 不在技能平台的文件白名单里（会被整单拒收），所以包里
    # 不能带它们 —— 但它们又正是最顺手的用法。解法是包里带一个生成器
    # （make_launchers.py，纯 .py 可发布），首次运行时现场写出来。
    # 已经有了就跳过，不会覆盖你自己改过的启动器。
    _ensure_launchers()

    wb = cfg["workbuddy_dir"]
    if not os.path.isdir(wb):
        print()
        print("  [提示] 没有找到 WorkBuddy 数据目录：%s" % wb)
        print("         如果你确实没在这台电脑上用过 WorkBuddy，")
        print("         那采出来会是空的 —— 这是正常的，不是工具坏了。")
        print("         如果用过，可能是 WorkBuddy 装在别处，")
        print("         把正确的路径填到上面的配置文件里即可。")
        print()

    db = conf.db_path()
    if not os.path.exists(db):
        print("  [提示] 数据库还不存在：%s" % db)
        print("         先启动一次 WorkBuddy 让它建库，再回来刷新。")
        print()

    n_roots = len(conf.existing_library_roots())
    if n_roots == 0:
        print("  [提示] 没有探测到任何可用的资料库目录，"
              "「资料库」页会是空的。可以在设置页手动添加。")
        print()

    return True


def render(payload, out_path):
    if not os.path.exists(TEMPLATE):
        sys.stderr.write("[scan] 找不到模板：%s\n" % TEMPLATE)
        return False
    with open(TEMPLATE, "r", encoding="utf-8") as fh:
        html = fh.read()

    data = json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
    # 防止 JSON 里的 </script> 提前闭合标签
    data = data.replace("</", "<\\/")
    html = html.replace("/*__DATA__*/null", data)

    # 输出目录不存在时给一句人话，而不是甩一个 FileNotFoundError 堆栈。
    # 这个工具的入口是双击 .bat，用户看不到命令行——
    # 一旦崩在堆栈里，他们只会看到窗口一闪而过，完全不知道发生了什么。
    out_dir = os.path.dirname(os.path.abspath(out_path))
    if not os.path.isdir(out_dir):
        sys.stderr.write("[scan] 输出目录不存在：%s\n" % out_dir)
        sys.stderr.write("[scan] 请先建好这个目录，或换一个输出路径（-o）。\n")
        return False

    try:
        with open(out_path, "w", encoding="utf-8") as fh:
            fh.write(html)
    except OSError as exc:
        sys.stderr.write("[scan] 写文件失败：%s\n" % exc)
        sys.stderr.write("[scan] 常见原因：文件正被浏览器占用，或没有写入权限。\n")
        return False
    return True


def main():
    console.fix()
    ap = argparse.ArgumentParser(description="生成 WorkBuddy 管理中心 HTML")
    ap.add_argument("-o", "--out", default=DEFAULT_OUT, help="输出 HTML 路径")
    ap.add_argument("--open", dest="do_open", action="store_true",
                    help="生成后用默认浏览器打开")
    ap.add_argument("--json", dest="json_out", help="同时导出一份 JSON 数据")
    ap.add_argument("--quiet", action="store_true", help="不输出首次运行引导")
    args = ap.parse_args()

    if not args.quiet:
        first_run_check()

    payload = collect()
    if args.json_out:
        with open(args.json_out, "w", encoding="utf-8") as fh:
            json.dump(payload, fh, ensure_ascii=False, indent=2)

    if not render(payload, args.out):
        return 1

    s = payload["stats"]
    print("[scan] %d 个任务（有效 %d / 已删 %d）| %d 个产物 | %d 个项目 | %d 个定时任务"
          % (s["total"], s["live"], s["deleted"], s["artifact_count"],
             s["project_count"], s["automation_count"]))
    print("[scan] 积分合计 %.2f" % s["credits_total"])
    print("[scan] 已生成：%s" % args.out)

    if args.do_open:
        webbrowser.open("file:///" + args.out.replace("\\", "/"))
    return 0


if __name__ == "__main__":
    sys.exit(main())
