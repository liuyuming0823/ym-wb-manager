#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
WorkBuddy 管理中心 · 自检
==========================
一条命令跑完所有检查，确认这套工具在改动之后仍然是好的。

    python tools/selftest.py            # 全部检查
    python tools/selftest.py --quick    # 跳过要起服务的部分

检查项：
  1. 编译 + 导入：8 个模块都能正常加载
  2. 控制台编码：中文在 GBK 控制台下不乱码
  3. 数据层：能读出任务 / 产物 / 定时任务
  4. 写入层：改名、删除、恢复、彻底删除的参数校验与幂等性
  5. 服务只读接口：页面、数据、目录浏览、白名单拦截
  6. 服务写接口：改名 → 删除 → 恢复全链路 + 异常兜底不断连
  7. 模板 JS 语法

退出码：0 全通过 / 1 有失败
"""

import argparse
import json
import os
import py_compile
import re
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
PY = sys.executable


def _find_node():
    """找一个能跑 JS 语法检查的 node。

    不写死版本号（早前是 `~/.workbuddy/binaries/node/versions/22.22.2-3/`
    —— WorkBuddy 一升级 node 这个自检就废了）。改成扫描 versions 目录，
    版本号最高的优先。
    """
    versions_root = os.path.expanduser("~/.workbuddy/binaries/node/versions")
    if os.path.isdir(versions_root):
        try:
            names = [n for n in os.listdir(versions_root)
                     if os.path.isfile(os.path.join(versions_root, n, "node.exe"))]
        except OSError:
            names = []

        def vkey(name):
            # '22.22.2-3' -> (22, 22, 2, 3)；非数字段按 0 算。
            # 必须按数字比，字符串排序会把 '9.x' 排到 '22.x' 后面。
            out = []
            for seg in name.replace("-", ".").split("."):
                try:
                    out.append(int(seg))
                except ValueError:
                    out.append(0)
            return tuple(out)

        for name in sorted(names, key=vkey, reverse=True):
            return os.path.join(versions_root, name, "node.exe")

    # 兜底只认 PATH：不猜某个具体的安装盘符 —— 那种猜测只在写它那台
    # 机器上成立，换台机器就是永远走不到的死分支。
    # 想指定解释器用环境变量 WB_NODE。
    return "node"


NODE = os.environ.get("WB_NODE") or _find_node()

sys.path.insert(0, HERE)

PASS, FAIL = [], []


def check(name, cond, detail=""):
    (PASS if cond else FAIL).append(name)
    print("  %s %s%s" % ("[OK]" if cond else "[!!]", name,
                         ("  -> " + str(detail)[:150]) if detail else ""))
    return bool(cond)


def head(t):
    print()
    print("=" * 62)
    print(t)
    print("=" * 62)


# ------------------------------------------------------------ 1. 编译导入
def t_compile():
    head("1. 编译与导入")
    mods = ["console.py", "data.py", "ops.py", "serve.py", "scan.py",
            "alive.py", "killer.py", "make_shortcut.py",
            "config.py", "findpy.py", "doctor.py"]
    tmp = tempfile.mkdtemp(prefix="wbselftest_")
    for f in mods:
        p = os.path.join(HERE, f)
        try:
            py_compile.compile(p, doraise=True, cfile=os.path.join(tmp, f + "c"))
        except py_compile.PyCompileError as e:
            check("编译 %s" % f, False, str(e)[:200])
            return
    check("%d 个模块编译通过" % len(mods), True)

    bad = []
    for f in mods:
        code = ("import sys; sys.path.insert(0, r'%s'); import %s"
                % (HERE, f[:-3]))
        r = subprocess.run([PY, "-c", code], capture_output=True, timeout=60)
        if r.returncode != 0:
            bad.append((f, (r.stderr or b"").decode("utf-8", "replace")[-200:]))
    check("%d 个模块可导入" % len(mods), not bad, bad[0] if bad else "")


# ------------------------------------------------------------ 1b. 配置层
def t_config():
    """配置层的核心契约。

    这几条是「通用工具」能不能分享给别人的地基：
    配置要有默认值（删了也能跑）、要能探测环境、要能扛住被写坏的 JSON。
    """
    head("1b. 配置层（通用性 / 容错）")
    import config as conf

    # 默认值必须存在且自洽 —— 删掉 config.json 也能跑起来的前提
    check("有内置默认值", isinstance(conf.DEFAULTS, dict) and bool(conf.DEFAULTS))
    check("默认含 workbuddy_dir", bool(conf.DEFAULTS.get("workbuddy_dir")))
    check("默认端口是合法端口",
          isinstance(conf.DEFAULTS.get("port"), int)
          and 1024 <= conf.DEFAULTS["port"] <= 65535,
          conf.DEFAULTS.get("port"))

    # 形状规整：外部怎么乱写都不该让 load() 炸掉
    bad_inputs = [
        None, [], "not-a-dict", 123,
        {"port": "abc"}, {"port": -1}, {"port": True},
        {"library_roots": "not-a-list"},
        {"library_roots": [None, 1, "x", {"no_path": 1}]},
        {"keep_backups": 0}, {"keep_backups": 99999},
    ]
    for raw in bad_inputs:
        try:
            cfg = conf._coerce(raw if isinstance(raw, dict) else {})
            ok = (isinstance(cfg, dict) and "port" in cfg
                  and 1024 <= cfg["port"] <= 65535)
        except Exception as exc:
            ok = False
            check("脏配置不崩溃：%r" % (raw,), False, exc)
            continue
        check("脏配置不崩溃：%r" % (raw,), ok)

    # 布尔值不能被当成端口 —— isinstance(True, int) 在 Python 里是真的，
    # 不特判就会把 True 当端口 1 收下
    cfg = conf._coerce({"port": True})
    check("端口拒绝布尔值", cfg["port"] == conf.DEFAULTS["port"], cfg["port"])

    # 相对路径要按面板目录解析（手写配置很容易写成相对路径）
    cfg = conf._coerce({"library_roots": [{"path": "somedir"}]})
    got = cfg["library_roots"][0]["path"] if cfg["library_roots"] else ""
    check("相对路径按面板目录解析", os.path.isabs(got) and got.startswith(conf.ROOT), got)

    # 缺 name 时用目录名兜底，不能是空字符串（页面会显示成空行）。
    # 用技能自己的父目录当样例：路径一定存在、且跨平台，断言取的是
    # 「末段目录名」而不是任何具体盘符 —— 写死某个系统目录会让自检
    # 在别的机器上变成假阳性。
    _sample_dir = os.path.dirname(ROOT)
    cfg = conf._coerce({"library_roots": [{"path": _sample_dir}]})
    _expect_name = os.path.basename(_sample_dir.rstrip("\\/")) or _sample_dir
    check("缺 name 时自动补目录名",
          bool(cfg["library_roots"]) and cfg["library_roots"][0]["name"] == _expect_name,
          cfg["library_roots"][0]["name"] if cfg["library_roots"] else "")

    # 探测：只收真实存在且非空的目录
    roots = conf._probe_library_roots()
    check("探测函数返回列表", isinstance(roots, list))
    check("探测结果都是绝对路径",
          all(os.path.isabs(r["path"]) for r in roots), "")
    check("探测结果都真实存在",
          all(os.path.isdir(r["path"]) for r in roots), "")
    check("探测结果无重复",
          len({os.path.normcase(r["path"]) for r in roots}) == len(roots), "")
    check("探测到至少一个目录（WorkBuddy 数据目录必然在）", len(roots) > 0,
          "%d 个" % len(roots))

    # existing_library_roots 必须过滤掉失效项
    allr = conf.library_roots()
    live = conf.existing_library_roots()
    check("失效目录被 existing_ 过滤",
          all(not r.get("missing") for r in live) and len(live) <= len(allr),
          "全部 %d / 可用 %d" % (len(allr), len(live)))
    check("失效目录仍保留在 library_roots 里（带 missing 标记）",
          len(allr) >= len(live))


# ------------------------------------------------------------ 1c. Python 查找
def t_findpy():
    """findpy 的版本排序与查找。

    这是「别人电脑上能不能双击启动」的关键 —— 早前版本号写死在 .bat 里，
    WorkBuddy 一升级自带 Python，启动器就全废。
    """
    head("1c. Python 解释器查找（版本号不写死）")
    import findpy

    check("版本排序：3.13 > 3.9",
          findpy._version_key("3.13.12") > findpy._version_key("3.9.0"),
          "%s vs %s" % (findpy._version_key("3.13.12"),
                        findpy._version_key("3.9.0")))
    check("版本排序：3.9 > 3.8",
          findpy._version_key("3.9.0") > findpy._version_key("3.8.0"))
    check("版本排序：非数字段不崩",
          findpy._version_key("weird") == (0,))
    check("版本排序：3.13.12 > 3.13.2",
          findpy._version_key("3.13.12") > findpy._version_key("3.13.2"))

    cands = findpy.bundled_candidates("python.exe")
    check("能扫到自带 Python", len(cands) > 0, "%d 个" % len(cands))
    if len(cands) > 1:
        check("自带候选按新版本优先",
              findpy._version_key(
                  os.path.basename(os.path.dirname(cands[0]))) >=
              findpy._version_key(
                  os.path.basename(os.path.dirname(cands[1]))), "")

    found = findpy.find("python.exe")
    check("能找到可用的 python.exe",
          bool(found) and os.path.isfile(found), found or "")

    # 注意：pythonw.exe 不是所有环境都有（比如 Linux / 只装了命令行版的 Python），
    # 所以这里只校验「找了不崩」，不要求一定找到。
    foundw = findpy.find("pythonw.exe") if os.name == "nt" else None
    if os.name == "nt":
        check("查找 pythonw.exe 不报错（找不到也算通过，属正常）", True,
              foundw or "未找到（不影响命令行启动）")


# ------------------------------------------------------------ 1d. 环境体检
def t_doctor():
    head("1d. 环境体检")
    import doctor

    rep = doctor.run()
    check("体检返回 ok", rep.get("ok") is True)
    check("结果是非空列表", isinstance(rep.get("results"), list)
          and len(rep["results"]) > 0, "%d 项" % len(rep.get("results", [])))
    check("有 summary", isinstance(rep.get("summary"), dict))
    check("summary 计数与明细一致",
          rep["summary"]["ok"] + rep["summary"]["warn"] + rep["summary"]["bad"]
          == len(rep["results"]),
          str(rep["summary"]))
    check("每项都有 level/name/detail/group",
          all(set(("level", "name", "detail", "group")) <= set(r.keys())
              for r in rep["results"]))
    check("level 取值合法",
          all(r["level"] in ("ok", "warn", "bad") for r in rep["results"]))
    check("在本机能全部通过（不应有 bad）", rep["summary"]["bad"] == 0,
          "bad=%d" % rep["summary"]["bad"])

    # 体检本身必须永不抛异常 —— 它是「出问题时用来诊断」的工具，
    # 自己崩掉就完全失去意义了。
    check("体检带 config_path", bool(rep.get("config_path")), rep.get("config_path"))


# ------------------------------------------------------------ 2. 控制台编码
def t_console_encoding():
    """验证 console.fix() 的行为契约。

    注意：不能用 `cmd /c ... capture_output=True` 来测「真实控制台」场景 ——
    一旦 capture_output=True，子进程的 stdout 就被接管成管道了，
    isatty() 必然是 False，测的其实是管道场景。
    （踩过：这样写会得到 UTF-8 字节，然后误判成「GBK 场景失败」。）
    所以这里直接用假流对象测判断逻辑，不依赖真实终端。
    """
    head("2. 控制台编码")
    import io

    class FakeStream(io.TextIOWrapper):
        def __init__(self, tty):
            super().__init__(io.BytesIO(), encoding="utf-8", errors="replace")
            self._tty = tty

        def isatty(self):
            return self._tty

    import console
    check("识别真实终端", console._is_real_console(FakeStream(True)) is True)
    check("识别管道（非终端）", console._is_real_console(FakeStream(False)) is False)

    # 管道场景：不应改编码（保证被重定向的日志是干净 UTF-8）
    saved_out, saved_err = sys.stdout, sys.stderr
    pipe = FakeStream(False)
    console._APPLIED = False
    sys.stdout = sys.stderr = pipe
    try:
        console.fix()
        enc = (pipe.encoding or "").lower().replace("-", "")
        check("管道场景保持 UTF-8", enc == "utf8", pipe.encoding)
    finally:
        sys.stdout, sys.stderr = saved_out, saved_err

    # 真实终端 + GBK 代码页：应切到 cp936
    if os.name == "nt":
        tty = FakeStream(True)
        console._APPLIED = False
        sys.stdout = sys.stderr = tty
        try:
            console.fix()
            enc = (tty.encoding or "").lower().replace("-", "")
            check("GBK 终端下切到 cp936", enc in ("cp936", "gbk"), tty.encoding)
        finally:
            sys.stdout, sys.stderr = saved_out, saved_err
            console._APPLIED = False


# ------------------------------------------------------------ 3. 数据层
def t_data():
    head("3. 数据层")
    import data as D
    d = D.snapshot(with_ops=False)
    check("快照版本号为 3", d.get("version") == 3, d.get("version"))
    s = d.get("stats") or {}
    check("有任务", s.get("total", 0) > 0, "任务 %s" % s.get("total"))
    check("键名是 tasks（前端依赖）", "tasks" in d)
    check("有 automations", "automations" in d, len(d.get("automations") or []))
    print("       stats: 任务 %s(有效 %s/已删 %s) 产物 %s 项目 %s 定时 %s 积分 %.2f"
          % (s.get("total"), s.get("live"), s.get("deleted"),
             s.get("artifact_count"), s.get("project_count"),
             s.get("automation_count"), s.get("credits_total") or 0))
    # tasks 必须同时有 deleted 和 deleted_at（与 automations 对齐）
    t0 = (d.get("tasks") or [{}])[0]
    check("tasks 同时提供 deleted / deleted_at",
          "deleted" in t0 and "deleted_at" in t0)
    return d


# ------------------------------------------------------------ 4. 写入层
def t_ops():
    head("4. 写入层参数校验（不做真实写入）")
    import ops

    # --- ID 校验：会话要求严格 UUID ---
    for sid, want, label in [
        ("3f2504e0-4f89-11d3-9a0c-0305e82c3301", True, "标准 UUID"),
        ("bad", False, "纯 hex 短串（曾经漏过）"),
        ("abc", False, "纯 hex 短串"),
        ("deadbeef", False, "纯 hex 短串"),
        ("", False, "空"),
        (None, False, "None"),
        (123, False, "数字"),
        ("3f2504e0-4f89-11d3-9a0c-0305e82c330", False, "少一位"),
        ("3f2504e04f8911d39a0c0305e82c3301", False, "无连字符"),
    ]:
        check("会话 ID 校验：%s" % label, ops._is_valid_id(sid) == want)

    # --- ID 校验：定时任务有两种格式 ---
    for aid, want, label in [
        ("automation-1779181407432", True, "automation-时间戳"),
        ("eca7037b-0bec-479f-8ca4-128a3291a801", True, "标准 UUID"),
        ("", False, "空"),
        (None, False, "None"),
        (123, False, "数字"),
        ("a" * 65, False, "超长"),
        ("bad id!", False, "含空格/特殊字符"),
    ]:
        check("定时任务 ID 校验：%s" % label, ops._is_valid_aid(aid) == want)

    # --- 写入参数校验（全部应在落库前被拒） ---
    UUID = "3f2504e0-4f89-11d3-9a0c-0305e82c3301"
    check("rename 拒绝非法 ID", not ops.rename_task("bad", "x")[0])
    check("rename 拒绝非文字标题", not ops.rename_task(UUID, 999)[0])
    check("rename 拒绝超长标题", not ops.rename_task(UUID, "x" * 201)[0])
    check("auto-rename 拒绝空名字", not ops.rename_automation("x", "  ")[0])
    check("auto-rename 拒绝非文字名字", not ops.rename_automation("x", 123)[0])
    check("purge 拒绝错误确认词", not ops.delete_task_permanent(UUID, "nope")[0])
    check("delete 拒绝非法 ID", not ops.delete_task("nope")[0])
    check("restore 拒绝非法 ID", not ops.restore_task("nope")[0])
    check("toggle 拒绝非法 ID", not ops.toggle_automation("")[0])
    check("auto-delete 拒绝非法 ID", not ops.delete_automation("")[0])
    check("auto-restore 拒绝非法 ID", not ops.restore_automation("")[0])

    # --- 备份清理：分档策略必须真的能收敛 ---
    #
    # 这一组是回归测试，防的是「备份目录无限增长」这个真实事故：
    # 早前 _prune_backups 一次删一批，堆积到 126 个文件时被安全策略判成
    # 批量操作并拦下，导致**所有写操作集体失败**（用户看到「改名点了没反应」）。
    # 修好之后必须保证两件事，任何一件退化都会重新引爆那个事故：
    #   1. 单次清理幅度不超过硬上限（否则又被拦）
    #   2. 极度堆积时能在少数几次操作内收敛（否则等于没清）
    # 这里只测纯函数行为，不造真文件、不碰数据库，跑起来零副作用。
    check("备份保留上限是有限值", isinstance(ops.KEEP_BACKUPS, int)
          and 1 <= ops.KEEP_BACKUPS <= 50, ops.KEEP_BACKUPS)
    check("单次清理硬上限低于批量阈值(50)",
          ops.PRUNE_HARD_CAP < 50, ops.PRUNE_HARD_CAP)

    # 直接验证分档函数：伪造一个目录状态，看它会删几个
    def _quota_for(count):
        """复刻 _prune_backups 的分档规则。

        注意输入是「当前份数」不是「超出份数」—— 因为真实实现里
        会额外 +1（为即将写入的那份腾位置），这个 +1 是收敛性的关键，
        复刻时必须一致，否则测的是另一套逻辑，测不出真实行为。
        """
        excess = count - ops.KEEP_BACKUPS + 1
        if excess <= 0:
            return 0
        if excess <= 4:
            return min(2, excess)
        if excess <= 20:
            return min(8, excess)
        return min(ops.PRUNE_HARD_CAP, excess)

    for count, lo, hi, label in [
        (ops.KEEP_BACKUPS - 3, 0, 0, "远低于上限则不动"),
        (ops.KEEP_BACKUPS, 1, 2, "刚好在上限也要腾一格"),
        (ops.KEEP_BACKUPS + 2, 1, 2, "轻度超出（excess<=4）"),
        (ops.KEEP_BACKUPS + 10, 5, 8, "中度超出（excess<=20）"),
        (ops.KEEP_BACKUPS + 60, 9, ops.PRUNE_HARD_CAP, "重度超出"),
    ]:
        q = _quota_for(count)
        check("清理额度：%s" % label, lo <= q <= hi, "删 %d 个" % q)
        check("清理额度不超硬上限：%s" % label, q <= ops.PRUNE_HARD_CAP)

    # 收敛模拟：模拟「每次操作 = 先清一批，再新写一份」，看能否真正回到上限。
    # 断言两条：步骤数不能太多（否则等于没清）、末值必须严格收敛（不能卡在上限+1）。
    n, steps = 200, 0
    while n > ops.KEEP_BACKUPS and steps < 300:
        n -= _quota_for(n)
        n += 1                       # 每次操作还会新写一份备份
        steps += 1
    check("重度堆积能在 30 次内收敛", steps <= 30, "%d 次后剩 %d 份" % (steps, n))
    check("最终严格收敛到上限", n <= ops.KEEP_BACKUPS, "末值 %d 份" % n)


# ------------------------------------------------------------ 服务相关
_OPENER = urllib.request.build_opener(urllib.request.ProxyHandler({}))

# 实际使用的端口。启动服务后由 _start_server() 回填。
#
# 为什么不能写死：serve.py 在目标端口被占用时会自动往上找一个空端口
# （这是刻意的特性 —— 端口被别的程序占了不该让整个工具起不来）。
# 早前这里写死 PORT=8790 去探测，结果服务实际起在 8792，
# 探了 20 秒全是被拒绝连接，后续所有服务相关的用例集体失败 ——
# 看上去像「服务坏了」，其实只是测试自己连错了门。
# 现在改成启动后从服务日志里解析真实地址，跟用户看日志找地址的路径一致。
PORT = None
_PORT_BASE = 8790


def _get(path, timeout=25):
    req = urllib.request.Request("http://127.0.0.1:%d%s" % (PORT, path))
    with _OPENER.open(req, timeout=timeout) as r:
        return r.status, r.read().decode("utf-8")


def _post(path, payload, timeout=60):
    body = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request("http://127.0.0.1:%d%s" % (PORT, path), data=body,
                                 headers={"Content-Type": "application/json"})
    try:
        with _OPENER.open(req, timeout=timeout) as r:
            return r.status, json.loads(r.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        try:
            return e.code, json.loads(e.read().decode("utf-8"))
        except Exception:
            return e.code, None
    except Exception as e:
        return "DISCONNECTED", "%s: %s" % (type(e).__name__, e)


def _parse_port_from_log(log_path):
    """从服务日志里解析真实监听端口，返回 int 或 None。

    serve.py 启动时会打印：
         WorkBuddy 管理中心已启动
         地址：http://127.0.0.1:8792/

    这里就抓 "地址：http://127.0.0.1:<port>/" 这一行。
    """
    try:
        with open(log_path, "rb") as fh:
            txt = fh.read().decode("utf-8", "replace")
    except OSError:
        return None
    m = re.search(r"http://127\.0\.0\.1:(\d+)/", txt)
    return int(m.group(1)) if m else None


def _start_server():
    """启动被测服务，返回 (process, log_handle)。

    启动成功后会把实际端口写回模块级 PORT。

    探测顺序：先等日志里出现地址行（首选，最准），等不到再退回到
    「扫描邻近端口段」兜底（万一日志格式以后变了，测试不至于直接崩）。
    """
    global PORT
    log_path = os.path.join(HERE, "_selftest_srv.log")
    log = open(log_path, "wb")
    p = subprocess.Popen([PY, "-u", os.path.join(HERE, "serve.py"),
                          "--port", str(_PORT_BASE), "--no-open"],
                         cwd=HERE, stdout=log, stderr=subprocess.STDOUT)

    # 首选：从日志解析真实地址
    for _ in range(40):
        time.sleep(0.5)
        if p.poll() is not None:
            break                      # 进程自己退了，别再等
        got = _parse_port_from_log(log_path)
        if got:
            PORT = got
            # 等它真正能接受连接
            for _ in range(12):
                try:
                    if _get("/api/data", timeout=3)[0] == 200:
                        return p, log
                except Exception:
                    pass
                time.sleep(0.4)
            break

    # 兜底：扫邻近端口段
    for cand in range(_PORT_BASE, _PORT_BASE + 10):
        try:
            req = urllib.request.Request("http://127.0.0.1:%d/api/data" % cand)
            with _OPENER.open(req, timeout=2) as r:
                if r.status == 200:
                    PORT = cand
                    return p, log
        except Exception:
            continue
    return p, log


def t_server():
    head("5. 服务（只读 / 白名单 / 写操作 / 异常兜底）")
    p, log = _start_server()

    # 起不来就直接报清楚，不要带着一个错的 PORT 往下跑 ——
    # 那样后续每条用例都会失败，把真正的故障点（服务没起来）淹没在噪音里。
    if PORT is None:
        log.flush()
        txt = ""
        try:
            with open(os.path.join(HERE, "_selftest_srv.log"), "rb") as fh:
                txt = fh.read().decode("utf-8", "replace")
        except OSError:
            pass
        check("服务能启动", False, "日志尾部：%s" % (txt[-300:] or "(空)"))
        try:
            p.kill()
        except Exception:
            pass
        log.close()
        return

    print("       服务端口：%d" % PORT)

    # --- 只读 ---
    st, body = _get("/")
    check("GET / 返回页面", st == 200 and len(body) > 10000, "%d bytes" % len(body))

    st, body = _get("/api/data")
    d = json.loads(body)
    check("GET /api/data", st == 200 and "stats" in d)

    # 白名单必须**允许**的目录：拿配置里第一个真实存在的资料库根来试。
    # 早前这里写死过一个具体的资料库目录名，换台机器、或者用户把资料库
    # 配成别的目录就直接失败 —— 自检本身的假阳性。
    allowed_ok = False
    allowed_label = ""
    import config as _conf
    for r in _conf.existing_library_roots():
        st, body = _get("/api/ls?path=" + urllib.request.quote(r["path"]))
        allowed_ok = bool(json.loads(body).get("ok"))
        allowed_label = r["path"]
        if allowed_ok:
            break
    check("GET /api/ls 允许白名单目录", allowed_ok, allowed_label or "（配置里没有可用目录）")

    # 白名单必须**拒绝**的目录。
    # 全部按当前机器动态构造 —— 用户名写死的话，这份自检在别人机器上
    # 要么测了个不存在的路径（"路径不存在"也能让 ok=False，看着像通过，
    # 其实压根没测到越权拦截），要么直接报错。
    home = os.path.expanduser("~")
    parent = os.path.dirname(home)
    blocked = [
        (os.path.join(parent, "Windows") if os.name == "nt" else "/etc", "系统目录"),
        (os.path.join(home, ".ssh"), "私钥目录"),
        (home, "用户主目录本身"),
        (os.path.join(home, "..", "..", "Windows"), "目录穿越"),
    ]
    # 只测真实存在的那些：白名单的判据里「路径不存在」也会返回 False，
    # 拿一个不存在的路径去测，等于什么都没验证。
    blocked = [(p, l) for p, l in blocked if os.path.exists(p)] or \
              [(os.path.join(home, ".ssh", "nonexistent-just-for-test"), "私钥目录")]
    for path, label in blocked:
        st, body = _get("/api/ls?path=" + urllib.request.quote(path))
        check("GET /api/ls 拒绝%s" % label, not json.loads(body).get("ok"),
              json.loads(body).get("error"))

    st, body = _get("/api/preview?id=not-a-uuid")
    check("preview 拒绝非法 ID", not json.loads(body).get("ok"))

    # --- 写操作全链路（用真实任务，做完还原） ---
    #
    # 这一整段用 try/finally 兜住，理由：
    # 早前没有兜底，一旦中间某条用例抛异常（比如断言写成 res.get 而 res 实际是
    # 字符串），控制流直接冲出去，**既不会还原测试标题、也不会关掉被测服务**。
    # 后果是「自检跑挂了」+「数据库留下一个叫【自检】临时 的标题」+「一个孤儿
    # pythonw 进程占着端口」，下次再跑还会因为端口被占而换端口 ——一连串连锁反应，
    # 排查起来完全看不出源头在哪。
    # 现在无论中间发生什么，收尾都会执行。
    tid = None
    orig_custom = None
    try:
        tasks = json.loads(_get("/api/data")[1])["tasks"]
        live = [x for x in tasks if not x.get("deleted")]
        check("有可测试的任务", len(live) > 0, "%d 个" % len(live))

        # 测试对象要挑「**没有自定义名**」的任务。
        #
        # 为什么不能随便拿第一个：改名测试会把 custom_title 写成【自检】临时，
        # 收尾再还原。如果挑中的任务本来就有自定义名，那还原就是把那个自定义名
        # 写回去 —— 万一它自己就是上一次测试留下的【自检】临时，残留就会
        # 一代代传下去，永远清不掉（实测踩过）。
        # 挑一个 custom_title 为空的任务，测完还原成 None，天然不留痕。
        # 实在没有空的时候才退回第一个。
        pristine = [x for x in live if not x.get("custom_title")]
        tgt = (pristine or live)[0] if live else None
        if tgt:
            tid = tgt["id"]
            orig_custom = tgt.get("custom_title") or None
            print("       测试对象：%s  %s（自定义名：%r%s）"
                  % (tid[:8], (tgt.get("title") or "")[:36], orig_custom,
                     "" if pristine else "，无干净候选"))

            st, res = _post("/api/rename", {"id": tid, "title": "【自检】临时"})
            check("改名成功", st == 200 and res.get("ok"))
            d2 = json.loads(_get("/api/data")[1])
            cur = [x for x in d2["tasks"] if x["id"] == tid]
            check("改名已生效", cur and cur[0].get("title") == "【自检】临时")

            # 传空字符串 = 恢复自动标题
            st, res = _post("/api/rename", {"id": tid, "title": ""})
            check("恢复自动标题", st == 200 and res.get("ok"))

            st, res = _post("/api/delete", {"id": tid})
            check("软删除成功", st == 200 and res.get("ok"))
            d3 = json.loads(_get("/api/data")[1])
            cur = [x for x in d3["tasks"] if x["id"] == tid]
            check("软删除已生效", cur and cur[0].get("deleted"))

            st, res = _post("/api/delete", {"id": tid})
            check("重复删除被拒绝", not res.get("ok"))

            st, res = _post("/api/restore", {"id": tid})
            check("恢复成功", st == 200 and res.get("ok"))
            d4 = json.loads(_get("/api/data")[1])
            cur = [x for x in d4["tasks"] if x["id"] == tid]
            check("恢复已生效", cur and not cur[0].get("deleted"))

            st, res = _post("/api/purge", {"id": tid, "confirm": "wrong"})
            check("purge 错误确认词被拒绝", not res.get("ok"))

        # --- 异常兜底：这些都应该返回 JSON，绝不能断连 ---
        exceptions = [
            ("rename 无 id", "/api/rename", {"title": "x"}),
            ("rename title 非文字", "/api/rename", {"id": "x", "title": 999}),
            ("delete 无 id", "/api/delete", {}),
            ("purge 无 confirm", "/api/purge", {"id": "x"}),
            ("auto-rename 无 id", "/api/auto-rename", {}),
            ("未知接口", "/api/nope", {}),
        ]
        for label, path, payload in exceptions:
            st, res = _post(path, payload)
            check("异常不断连：%s" % label,
                  st != "DISCONNECTED" and isinstance(res, dict), "HTTP=%s" % st)

        st, _ = _get("/api/data")
        check("异常测试后服务仍存活", st == 200)

    finally:
        # ---- 收尾 1：还原自定义名 ----
        # 写回改之前记录的 custom_title（可能是 None → 传空串恢复自动标题）。
        # 用 try 包住：服务可能已经崩了，这时还原请求会失败，
        # 但绝不能让还原动作本身再把异常抛出去掩盖原始故障。
        if tid:
            try:
                _post("/api/rename", {"id": tid, "title": orig_custom or ""},
                      timeout=10)
                check("测试标题已还原", True)
                # 还原后回读确认，避免"以为还原了其实没有"
                d9 = json.loads(_get("/api/data", timeout=10)[1])
                cur9 = [x for x in d9["tasks"] if x["id"] == tid]
                now = (cur9[0].get("custom_title") if cur9 else None) or None
                check("还原结果已确认", now == orig_custom, "现在：%r" % (now,))
            except Exception as exc:
                check("测试标题已还原", False, "还原请求失败：%s" % exc)

        # ---- 收尾 2：关服务 + 检查未捕获异常 ----
        try:
            p.terminate()
            try:
                p.wait(timeout=15)
            except subprocess.TimeoutExpired:
                p.kill()
                p.wait(timeout=10)
        except Exception:
            pass
        try:
            log.close()
        except Exception:
            pass

        txt = ""
        try:
            with open(os.path.join(HERE, "_selftest_srv.log"), "rb") as fh:
                txt = fh.read().decode("utf-8", "replace")
        except OSError:
            pass
        check("服务端无未捕获异常", "Traceback" not in txt,
              txt[-400:] if "Traceback" in txt else "")

        # ---- 收尾 3：顺手把备份压回上限 ----
        # 这一轮测试真跑了好几次写操作，每次都会落一份备份；
        # 让自检自己收尾，避免留一堆备份给下次跑。
        try:
            sys.path.insert(0, HERE)
            import ops as _ops
            _ops.prune_all()
        except Exception:
            pass

        try:
            os.remove(os.path.join(HERE, "_selftest_srv.log"))
        except OSError:
            pass


# ------------------------------------------------------------ 7. 模板 JS
def t_template_js():
    head("6. 模板 JS 语法")
    import re
    tpl = os.path.join(ROOT, "assets", "template.html")
    src = open(tpl, "r", encoding="utf-8").read()
    blocks = re.findall(r"<script[^>]*>(.*?)</script>", src, re.S)
    check("找到内联 script", len(blocks) > 0, "%d 块" % len(blocks))
    for i, b in enumerate(blocks):
        code = b.replace("/*__DATA__*/null", "null")
        tmp = os.path.join(HERE, "_selftest_js.js")
        with open(tmp, "w", encoding="utf-8") as fh:
            fh.write(code)
        r = subprocess.run(
            [NODE, "-e",
             "const fs=require('fs');const c=fs.readFileSync(process.argv[1],'utf8');"
             "try{new Function(c);console.log('OK')}catch(e){console.log('ERR '+e.message)}",
             tmp], capture_output=True, timeout=60)
        out = (r.stdout or b"").decode("utf-8", "replace").strip()
        check("script 块 %d 语法正确" % i, out == "OK", out)

    # --- 深链别名归一：必须存在规范值表 + 归一函数 ---
    #
    # 防的是「静默失效」：界面文案写「无人问津」，内部值却是 no-art。
    # 没有归一逻辑时，手写深链猜成 no-ask 会让 state.quick 变成一个没人认识的
    # 字符串 —— 筛选标签亮着、列表却一条都不过滤，用户完全看不出哪里错了。
    # 这里做静态检查，确保归一表和归一函数没被误删。
    for token, label in [
        ("QUICK_ALIAS", "存在筛选别名表"),
        ("function normalizeQuick", "存在归一函数"),
        ('"no-art": "no-art"', "规范值已登记"),
        ('"no-ask": "no-art"', "常见误拼已兼容"),
        ('"无人问津": "no-art"', "中文文案已兼容"),
    ]:
        check("深链：%s" % label, token in src)

    # 每个合法筛选值都要有对应的过滤分支，否则还是静默失效
    for q in ("no-art", "stale", "recent", "lost"):
        check("深链：筛选值 %s 有过滤分支" % q,
              ('state.quick === "%s"' % q) in src)

    # --- 输入框不能被重建（2026-09-20 修的「打不了字」事故） ---
    #
    # 事故现场：工具条里的 <input id="q"> 是 renderChrome() 用 innerHTML 生成的。
    # 打一个字 → input 事件 → render() → renderChrome() → bar.innerHTML = h
    # → 输入框被销毁重建 → 光标没了、输入法组字上下文没了。
    # 用户看到的是「刚敲一个键，字就自己跳走了，根本打不了字」。
    #
    # 判据：工具条里**不允许**再出现带 value="..." 的 q 输入框
    # （那种写法就是「每次渲染都填充新元素」的标志），
    # 且必须把「只重画列表」的入口 renderList 和「同步值」的 syncBar 留住。
    bad_input = re.search(r'<input id="q"[^>]*value=', src)
    check("工具条：搜索框不再内联 value（避免重建 DOM）", bad_input is None,
          "仍在用 value= 生成输入框" if bad_input else "OK")
    check("工具条：输入事件只调 renderList（不重建工具条）",
          'e.target.value; renderList();' in src)
    check("工具条：存在 renderList（只重画内容区）", "function renderList" in src)
    check("工具条：存在 syncBar（同步值而非重建）", "function syncBar" in src)
    check("工具条：存在 buildBar 且按视图缓存", "let barView = null" in src)
    check("工具条：Esc 清空搜索框不调 render()",
          'state.q = "";\n      renderList();' in src)

    # --- 筛选条件必须按视图隔离（「切换菜单后空白」事故） ---
    #
    # 事故现场：q/status/mode 等是全局单例，在任务页搜了词再切到项目页，
    # 同一个 q 还挂着 —— 项目路径当然不含那个词，于是整页空白。
    #
    # 判据：必须存在按视图分桶的 filters + 用 defineProperty 暴露别名，
    # 且不许再出现「state.q = ""」那种把全局筛选清空的写法（切视图时清）。
    check("筛选隔离：存在 state.filters 分桶", "filters: {}" in src)
    check("筛选隔离：存在 FILTER_KEYS 清单", "const FILTER_KEYS" in src)
    check("筛选隔离：用 defineProperty 暴露按视图别名",
          "Object.defineProperty(state, k" in src)
    check("筛选隔离：切视图不再清空全局 q",
          'state.quick = "";\n    if (state.view !== "library")' not in src)

    # --- 任务列表默认是表格式（带表头），卡片收进详情 ---
    check("列表：存在表头列定义 TASK_COLS", "const TASK_COLS" in src)
    check("列表：存在表格渲染函数", "function renderTaskTable" in src)
    check("列表：存在共用的详情渲染函数", "function renderTaskDetail" in src)
    check("列表：卡片形态复用详情（不重复实现）",
          '<div class="rbody">\' + renderTaskDetail(t)' in src)
    check("列表：默认不是卡片（cards 默认 false）",
          re.search(r"cards:\s*false", src) is not None)
    for col in ("自动标题", "标题（手动）", "对话记录", "模型", "创建时间", "最近更新"):
        check("列表：表头含「%s」" % col, (">" + col + "<") in src or (col in src))
    # 行内改标题
    check("列表：存在行内编辑函数", "function startInlineEdit" in src)
    check("列表：行内编辑绑定到 /api/rename",
          'startInlineEdit' in src and '/api/rename' in src)
    check("列表：行内编辑只填手动标题（不误存自动标题）",
          "只填「手动标题」" in src)

    # --- 列表默认刚好一屏（不出现横向滚动条） ---
    #
    # 事故现场：.lt 用 min-width:1180px + 列宽靠内容撑 → 10 列必然溢出，
    # 页面底部永远挂着横向滚动条。改为 table-layout:fixed + 百分比列宽后，
    # 列宽由容器按比例分配，内容再长也只在自己列内省略。
    check("列表：表格用 fixed 布局（列宽不随内容撑开）",
          "table-layout:fixed" in src)
    check("列表：表格列宽用百分比分配",
          re.search(r"\.lt td\.c-name\s*\{[^}]*width:\s*\d+%", src) is not None)
    check("列表：表格 min-width 不过宽（<=1080px 兜底）",
          re.search(r"\.lt\{[^}]*min-width:\s*(\d+)px", src) is not None
          and int(re.search(r"\.lt\{[^}]*min-width:\s*(\d+)px", src).group(1)) <= 1080)

    # --- 长标题省略号、不换行 ---
    #
    # 事故现场：.ename 用了 word-break:break-word → 长任务名折成 4~5 行，
    # 每行行高被撑爆，一屏只放得下两三条。
    check("列表：单元格超出即省略（三件套）",
          re.search(r"\.lt td\{[^}]*overflow:\s*hidden[^}]*text-overflow:\s*ellipsis", src) is not None)
    check("列表：单元格不换行", re.search(r"\.lt td\{[^}]*white-space:\s*nowrap", src) is not None)
    check("列表：任务名不再用 word-break 折行",
          re.search(r"\.ename\{[^}]*word-break", src) is None)
    check("列表：任务名自身也走省略号",
          re.search(r"\.ename\{[^}]*text-overflow:\s*ellipsis", src) is not None)
    # 详情展开行是块内容，必须把列表的 nowrap 覆盖掉，否则详情全挤成一行
    check("列表：展开行恢复换行（不被列表规则约束）",
          "tr.exp-row>td" in src and re.search(r"\.lt tr\.exp-row>td\{[^}]*white-space:\s*normal", src) is not None)

    # --- 改标题必须点小铅笔（防误操作） ---
    #
    # 明哥的反馈：整格可点即进编辑态，太容易误操作。判据两条：
    #   ① 任务名 .ename 上不许再挂 data-edit（点名称 = 打开任务）
    #   ② 必须存在 .pencil 按钮承载 data-edit，且编辑态由它触发
    check("防误操作：存在小铅笔按钮样式 .pencil",
          re.search(r"\.lt\s+\.pencil\s*\{", src) is not None)
    check("防误操作：铅笔按钮承载 data-edit",
          re.search(r'class="pencil"\s+data-edit=', src) is not None)
    check("防误操作：任务名不再直接可编辑（无 data-edit）",
          re.search(r"class=.ename[^\n]*data-edit=", src) is None)
    check("防误操作：任务名点击改为唤起任务（data-reopen）",
          re.search(r"class=.ename[^\n]*data-reopen=", src) is not None)
    check("防误操作：铅笔默认半透明、悬停才显眼",
          re.search(r"\.lt\s+\.pencil\{[^}]*opacity:\s*\.\d", src) is not None)
    check("防误操作：手写输入框只在编辑态出现（class rin 仅在 startInlineEdit 内创建）",
          src.count('class="rin') <= 1)

    try:
        os.remove(tmp)
    except OSError:
        pass


def t_packaging():
    """发布合规：包里不许出现平台拒收的文件。

    这条断言是有来历的 —— SkillHub 按扩展名逐个校验，命中一个非纯文本
    就整单拒收。最坑的是**本地 dry-run 会通过**（CLI 只校验 metadata +
    能不能打包），只有真正上传后才报「不支持的文件类型: xxx」，白等一轮。

    所以规矩定成：启动器（.bat/.vbs）不进包，改由 make_launchers.py
    在首次运行时现场生成。这里负责保证「生成器在、且在引导里被调用」。
    """
    head("发布合规")

    ml = ""
    for p in (os.path.join(HERE, "make_launchers.py"),
              os.path.join(ROOT, "tools", "make_launchers.py")):
        if os.path.exists(p):
            with open(p, encoding="utf-8") as fh:
                ml = fh.read()
            break
    check("发布合规：存在启动器生成器 make_launchers.py", bool(ml))
    if not ml:
        return
    for name in ("启动管理中心.bat", "停止管理中心.bat",
                 "刷新数据.bat", "启动管理中心.vbs"):
        check("发布合规：生成器内置 %s" % name, name in ml)
    check("发布合规：.bat 按 GBK 写出（UTF-8 会让 cmd 解析错乱）",
          '"gbk"' in ml)
    check("发布合规：.vbs 按 ASCII 写出（wscript 会按 ANSI 解码）",
          '"ascii"' in ml)
    check("发布合规：写出时统一 CRLF",
          'replace("\\n", "\\r\\n")' in ml)

    scan = ""
    for p in (os.path.join(HERE, "scan.py"),):
        if os.path.exists(p):
            with open(p, encoding="utf-8") as fh:
                scan = fh.read()
            break
    check("发布合规：首次运行引导里会补生成启动器",
          "_ensure_launchers" in scan and "make_launchers" in scan)
    check("发布合规：生成启动器失败不阻断采集",
          "不影响使用" in scan)
    check("发布合规：生成器幂等（已存在则跳过，不覆盖用户改动）",
          "已存在" in ml and "force" in ml)

    # 🔴 启动器里的脚本目录名必须跟着实际目录走。
    # 事故：模板正文写死 `scripts\`，而开发目录叫 `tools\` —— 在开发目录
    # 生成的启动器全部指向不存在的路径，生成时毫无报错，**双击才弹**
    # 「Cannot find: ...\scripts\serve.py」。属于最难自查的静默错配。
    check("发布合规：脚本目录名按实际目录推导（不写死 scripts）",
          "scripts_dir_name" in ml and "basename(HERE" in ml)
    check("发布合规：生成后校验引用的脚本真实存在",
          "referenced_scripts" in ml and "引用了不存在的脚本" in ml)

    # 落到实物上验：当前目录下生成的启动器，引用的 .py 都得找得到。
    try:
        if HERE not in sys.path:
            sys.path.insert(0, HERE)
        import make_launchers as _mk
        missing = []
        for _name in ("启动管理中心.bat", "停止管理中心.bat",
                      "刷新数据.bat", "启动管理中心.vbs"):
            _p = os.path.join(_mk.ROOT, _name)
            if not os.path.exists(_p):
                continue          # 技能包未生成属正常，跳过
            for _fn in _mk.referenced_scripts(_name):
                if not os.path.exists(os.path.join(_mk.HERE, _fn)):
                    missing.append("%s -> %s" % (_name, _fn))
        check("发布合规：已生成的启动器指向的脚本全部存在", not missing,
              "缺失：%s" % "、".join(missing) if missing else "全部命中")
    except Exception as _e:       # noqa: BLE001
        check("发布合规：已生成的启动器指向的脚本全部存在", False, "检查出错：%s" % _e)


def main():
    ap = argparse.ArgumentParser(description="管理中心自检")
    ap.add_argument("--quick", action="store_true", help="跳过需要起服务的部分")
    args = ap.parse_args()

    import console
    console.fix()

    print("WorkBuddy 管理中心 · 自检")
    print("目录：%s" % ROOT)

    t_compile()
    t_config()
    t_findpy()
    t_doctor()
    t_console_encoding()
    t_data()
    t_ops()
    if not args.quick:
        t_server()
    t_template_js()
    t_packaging()

    head("汇总")
    print("通过 %d 项，失败 %d 项" % (len(PASS), len(FAIL)))
    if FAIL:
        print()
        print("失败清单：")
        for f in FAIL:
            print("   - %s" % f)
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(main())
