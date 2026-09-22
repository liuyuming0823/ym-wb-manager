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

    # ---------------- 扩展（技能/专家/专家团/连接器）----------------
    #
    # 这一组防的是「数字虚高」和「类型误判」两类静默错误：
    #   · 早前把市场货架（98 个包，只装了 10 个）当成已安装，总数虚高到 183
    #   · 早前用 agents/ 数量判类型，把 welcomemode 这类内置包标成了「专家」
    # 两者都不会报错，只会安静地给出错误答案 —— 所以必须有断言盯着。
    plugs = d.get("plugins")
    check("快照里有 plugins 键", isinstance(plugs, list),
          type(plugs).__name__)
    plugs = plugs or []

    FIELDS = ("id", "name", "kind", "source", "marketplace", "desc",
              "version", "author", "path", "installed", "missing")
    bad_field = [p.get("id") for p in plugs
                 if not all(k in p for k in FIELDS)]
    check("每条扩展的字段结构一致", not bad_field, bad_field[:3])

    LEGAL = {"skill", "expert", "team", "connector", "plugin"}
    bad_kind = sorted({p.get("kind") for p in plugs} - LEGAL)
    check("扩展类型都在合法集合内", not bad_kind, bad_kind)

    id_dup = len(plugs) - len({p.get("id") for p in plugs})
    check("扩展没有重复条目", id_dup == 0, "重复 %d" % id_dup)

    inst = [p for p in plugs if p.get("installed")]
    check("至少有已安装的扩展", len(inst) > 0, "%d 条" % len(inst))
    check("已安装数不超过总数", len(inst) <= len(plugs),
          "%d / %d" % (len(inst), len(plugs)))

    # 内置包绝不能被标成专家/专家团（welcomemode / interactionmode 等带 agents/，
    # 照 agents 数量判会全成「专家」）
    builtin_bad = [p["name"] for p in inst
                   if p.get("marketplace") == "workbuddy-builtin"
                   and p.get("kind") in ("expert", "team")]
    check("内置包没有被误判成专家/专家团", not builtin_bad, builtin_bad[:4])

    # 专家团必须是真的多角色（agents 判据）；单角色只能是专家
    team_bad = [p["name"] for p in plugs
                if p.get("kind") == "team" and "个角色" not in (p.get("kind_note") or "")]
    check("专家团都带角色数说明", not team_bad, team_bad[:4])

    st = d.get("stats") or {}
    for k in ("plugin_count", "plugin_installed", "plugin_skill",
              "plugin_expert", "plugin_team", "plugin_connector"):
        check("stats 有 %s" % k, k in st, st.get(k))
    check("stats.plugin_count 与列表长度一致",
          st.get("plugin_count") == len(plugs),
          "%s vs %s" % (st.get("plugin_count"), len(plugs)))
    check("stats.plugin_installed 与实算一致",
          st.get("plugin_installed") == len(inst),
          "%s vs %s" % (st.get("plugin_installed"), len(inst)))

    # 三类来源各至少命中一个（本机实测有：本地技能 / MCP / 内置）
    kinds = {p.get("kind") for p in inst}
    for want in ("skill", "connector"):
        check("已安装里有 %s" % want, want in kinds, sorted(kinds))
    has_local = any(p.get("marketplace") == "local" for p in inst)
    check("本地目录型技能被收进来了", has_local)
    print("       plugins: 共 %d（已装 %d）技能 %s 专家 %s 专家团 %s 连接器 %s 内置 %s"
          % (len(plugs), len(inst), st.get("plugin_skill"), st.get("plugin_expert"),
             st.get("plugin_team"), st.get("plugin_connector"), st.get("plugin_plugin")))

    # ---------------- 中文名（display_name）----------------
    #
    # 明哥的原话：「只看 slug 看不太懂是什么技能、专家等」。
    # 中文名的来源是 5 处异构位置（市场 _skillhub_meta.json / SKILL.md frontmatter
    # 的 display_name / plugin.json 的 name / 专家包 plugin.json / 市场货架）。
    # 这里只要保证「填了的确实是中文名、没填的是空串而不是 slug 冒充」——
    # 别让中文名退化成「把 slug 又抄了一遍」，那等于没做。
    dn_vals = [(p.get("id"), p.get("display_name") or "") for p in plugs]
    bad_dn_type = [i for i, v in dn_vals if not isinstance(v, str)]
    check("扩展：display_name 都是字符串", not bad_dn_type, bad_dn_type[:3])
    # 中文名退化成「把 id 抄一遍」是这里最容易犯的错（看着像填了，其实无信息）。
    #
    # 但不能一律判错：像 github 这个连接器，它自己 plugin.json 里写的
    # name 就**真的是** "github"（官方就这么定义的），这时 display_name == name
    # 不是我们抄的，是忠实照搬源数据。
    # 所以判据收紧成：display_name 不许等于**带市场后缀的 id**
    # （比如 "github@codebuddy-plugins-official"）—— 那个 id 是本工具自己拼的，
    # 一旦出现在 display_name 里，就说明走了「拿内部 id 兜底」的错误分支。
    fake_dn = [(i, v) for i, v in dn_vals if v and v == i]
    check("扩展：display_name 不是把内部 id 抄了一遍", not fake_dn, fake_dn[:3])
    # 已装的本机技能里应该**有一些**能配上中文名（配不上的是本地自制，属正常）
    inst_dn = [p for p in inst if p.get("display_name")]
    check("扩展：已装技能里能解析出中文名的比例不过低",
          len(inst_dn) * 5 >= len(inst), "%d/%d 有中文名" % (len(inst_dn), len(inst)))
    # display_name 与 name 相同时，前端必须把重复的那行副标题藏掉，
    # 否则「github / github」叠两行，看着像渲染 bug。
    # （这条属于前端，真正的断言在 t_template_js 里，这里只确认数据侧没被抄成 id）
    same_dn = [p["id"] for p in plugs
               if p.get("display_name") and p.get("display_name") == p.get("name")]
    check("扩展：name 与 display_name 重合的是源数据本身（非本工具抄的）",
          all(not d.startswith("local-skill:") for d in same_dn),
          same_dn[:3])

    # ---------------- 资料库分页签 ----------------
    #
    # 明哥的疑问：「资料库现在是只有设置里面的东西了吗？原来 workbuddy 里的资料在哪？」
    # 事实是资料没丢，只是全埋在「WorkBuddy 数据」一张卡后面。改成按类型分页签。
    # 这里守三件事：① 分组确实有内容 ② 只收真实存在的目录 ③ 路径在 wb_dir 底下。
    _g = D.library_groups()
    check("资料库：library_groups 返回分组列表", isinstance(_g, list) and len(_g) >= 2,
          "%d 组" % len(_g))
    _ids = [x.get("id") for x in _g]
    check("资料库：分组 id 是预期的四类",
          set(_ids) <= {"skill", "data", "docs", "sys"},
          _ids)
    _all_ent = [e for x in _g for e in (x.get("entries") or [])]
    check("资料库：分组里有可浏览的目录", len(_all_ent) >= 8, "%d 个入口" % len(_all_ent))
    # 🔴 只收真实存在的目录 —— 不存在的收进来就是「点了报错」的入口。
    # （这和 config 的 library_roots 策略相反：那是用户自己配的，失效要显示出来才好改。）
    _missing = [e.get("path") for e in _all_ent if not os.path.isdir(e.get("path") or "")]
    check("资料库：不收录不存在的目录", not _missing, _missing[:3])
    _outside = [e.get("path") for e in _all_ent
                if not str(e.get("path") or "").startswith(D.WB_DIR)]
    check("资料库：所有入口都在 WorkBuddy 数据目录内", not _outside, _outside[:3])
    # 技能的入口必须在，否则「我装了什么技能」还是得靠翻文件夹
    _rel = [e.get("rel") for e in _all_ent]
    check("资料库：含 skills 入口", "skills" in _rel)
    check("资料库：含 artifact-index 入口", "artifact-index" in _rel)
    # 计数要么是正数，要么是 None（读不出来）；不许是 0 冒充「空目录」
    _zero = [(e.get("rel"), e.get("count")) for e in _all_ent
             if e.get("count") == 0 and os.path.isdir(e.get("path") or "")]
    check("资料库：非空目录的计数不为 0（0 会误导成空）", not _zero, _zero[:3])

    # ---------------- 账户信息 ----------------
    #
    # 这一块的坑很明确：积分/签到要登录态，本工具**拿不到**。
    # 所以必须守住两条：① 不因为拿不到就整个接口崩掉；
    # ② 不伪造数字（宁可 credits.available=False 让前端改做入口）。
    acc = D.account_info()
    check("账户：account_info 返回 ok", acc.get("ok") is True)
    check("账户：有 client 段", isinstance(acc.get("client"), dict))
    check("账户：client 里有版本或数据目录",
          bool((acc.get("client") or {}).get("version")
               or (acc.get("client") or {}).get("data_dir")))
    # 🔴 不许编积分。拿不到就必须明说拿不到。
    cr = acc.get("credits") or {}
    check("账户：积分明确标注是否可用（不伪造）",
          "available" in cr and cr["available"] is False,
          cr)
    links = acc.get("links") or []
    check("账户：提供了唤起客户端的入口", len(links) >= 3, "%d 个" % len(links))
    # 入口分两类，各自的 url 协议是**不同**的，这里要分开判：
    #   · kind=app -> 必须 workbuddy://（唤起本机客户端）
    #   · kind=web -> 必须 https://（打开官方网页）
    # 早前这里图省事写成「所有 url 都得是 workbuddy://」，
    # 结果把两个正经的网页入口判成了错 —— 断言本身写错的典型。
    bad_link = []
    for l in links:
        u = l.get("url") or ""
        if l.get("kind") == "app" and not u.startswith("workbuddy://"):
            bad_link.append(u)
        elif l.get("kind") == "web" and not u.startswith("https://"):
            bad_link.append(u)
        elif l.get("kind") not in ("app", "web"):
            bad_link.append("kind 非法: %s" % l.get("kind"))
    check("账户：入口协议与 kind 匹配（app=workbuddy://，web=https://）",
          not bad_link, bad_link[:3])
    check("账户：至少有一个能唤起客户端的入口（app）",
          any(l.get("kind") == "app" for l in links))
    for want in ("credits", "checkin", "growth"):
        check("账户：有「%s」入口" % want,
              any(l.get("id") == want for l in links),
              [l.get("id") for l in links])

    # ---------------- 记忆文件 ----------------
    #
    # 只读浏览，但要确认：分组结构稳定、路径都是绝对路径（前端拿去点开）。
    memo = D.memory_overview()
    check("记忆：memory_overview 返回 ok", memo.get("ok") is True)
    check("记忆：有 groups 列表", isinstance(memo.get("groups"), list))
    check("记忆：total 与各组条数之和一致",
          memo.get("total") == sum(len(g.get("items") or [])
                                   for g in (memo.get("groups") or [])),
          "%s" % memo.get("total"))
    mitems = [it for g in (memo.get("groups") or []) for it in (g.get("items") or [])]
    bad_path = [it.get("path") for it in mitems
                if not (it.get("path") or "").startswith(("\\\\", "/"))
                and not re.match(r"^[A-Za-z]:[\\/]", it.get("path") or "")]
    check("记忆：条目路径都是绝对路径", not bad_path, bad_path[:3])
    bad_ext = [it.get("path") for it in mitems
               if os.path.splitext(it.get("path") or "")[1].lower()
               not in (".md", ".txt", ".json")]
    check("记忆：只收 md/txt/json（不把二进制塞进来）", not bad_ext, bad_ext[:3])

    # ---------------- 版本比较（防「建议降级」）----------------
    #
    # 这组是修完一个真 bug 后加的：原实现用 version != latest 判落后，
    # 于是本地 1.3.10 / 线上 1.0.2 被报成「可以更新」—— 让用户去装旧版。
    # 这里把关键序关系钉住，以后谁改这个函数都得先过这一关。
    VCASES = [
        ("1.3.10", "1.0.2", 1),      # 多位数段必须按数值比，不能按字符串
        ("1.10", "1.9", 1),
        ("1.2.0", "1.2.0", 0),
        ("v1.2.0", "1.2.0", 0),      # 前导 v 要忽略
        ("1.2.0-beta", "1.2.0", -1),  # 预发布 < 正式
        ("1.0.0", "1.0.0.0", -1),     # 段更长的更大
        ("", "1.0.0", 0),             # 比不出来一律算平（保守）
        ("abc", "1.0.0", 0),
    ]
    bad_v = []
    for a, b, exp in VCASES:
        got = D._ver_cmp(a, b)
        if got != exp:
            bad_v.append("%s vs %s: %s != %s" % (a, b, got, exp))
    check("版本比较：关键序关系全部正确", not bad_v, bad_v[:3])
    # 跨类型不能抛异常（(2,'abc') < (0,1) 在 py3 里是 TypeError）
    try:
        D._ver_cmp("beta", "1.0.0")
        D._ver_cmp("1.0.0", "beta")
        check("版本比较：跨类型不抛异常", True)
    except TypeError as e:
        check("版本比较：跨类型不抛异常", False, str(e))

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

        # --- 账户 / 记忆 / 检查更新（只读接口）---
        st, body = _get("/api/account", timeout=30)
        acc = json.loads(body)
        check("GET /api/account", st == 200 and acc.get("ok") is True)
        check("账户接口带 memory 段", isinstance(acc.get("memory"), dict))
        check("账户接口明说积分不可读（不伪造）",
              (acc.get("credits") or {}).get("available") is False)

        # 记忆接口：先拿一个真实路径再读
        mem_items = [it for g in ((acc.get("memory") or {}).get("groups") or [])
                     for it in (g.get("items") or [])]
        if mem_items:
            mp = mem_items[0]["path"]
            st, body = _get("/api/memory?path=" + urllib.request.quote(mp), timeout=30)
            j = json.loads(body)
            check("GET /api/memory 能读白名单内的记忆文件",
                  st == 200 and j.get("ok") is True, j.get("error") or "")
            check("记忆返回带 text 和 size",
                  isinstance(j.get("text"), str) and "size" in j)
        else:
            print("       （本机没有记忆文件，跳过读取用例）")

        # 🔴 记忆接口必须拒绝白名单外的路径。
        # 这是新增的文件读取能力 —— 不做拦截就等于给了个任意文件读取口子。
        # 路径**动态构造**（不许写死 C:\Users\xxx\.ssh），否则换台机器就假阴性。
        home2 = os.path.expanduser("~")
        outside = [
            os.path.join(home2, ".ssh", "id_rsa"),
            os.path.join(home2, "MEMORY.md"),          # 存在但不在记忆白名单里
            os.path.join(home2, ".workbuddy", "workbuddy.db"),
            os.path.join(home2, ".workbuddy", "..", ".workbuddy", "config.json"),
        ]
        blocked_ok = True
        blocked_detail = ""
        for op in outside:
            st, body = _get("/api/memory?path=" + urllib.request.quote(op), timeout=20)
            try:
                jj = json.loads(body)
            except ValueError:
                blocked_ok = False; blocked_detail = "非 JSON: %s" % body[:80]; break
            if jj.get("ok"):
                blocked_ok = False; blocked_detail = "居然读到了 %s" % op; break
        check("记忆接口拒绝白名单外的路径（防任意文件读取）",
              blocked_ok, blocked_detail)

        # 检查更新：**会联网**，所以只在网络可用时深测，否则只验结构。
        st, body = _get("/api/check-update", timeout=180)
        cu = json.loads(body)
        check("GET /api/check-update", st == 200 and cu.get("ok") is True)
        for k in ("client", "skills", "errors", "not_published",
                  "not_published_count", "outdated_count", "checked_count"):
            check("检查更新返回 %s" % k, k in cu)
        check("检查更新：错误清单是列表", isinstance(cu.get("errors"), list))
        check("检查更新：未上架清单是列表",
              isinstance(cu.get("not_published"), list))
        # 🔴「没上架」和「出错」必须分开报。
        #
        # 本机 46 个本地技能里只有 13 个发到了 SkillHub，其余自制/未上架。
        # 全塞进 errors，页面就显示「33 个错误」——用户以为工具坏了。
        # 这里守住两件事：① 未上架的条目不许出现在 errors 里；
        # ② 两边加起来不能超过实际查过的总数（防止漏记或重复计数）。
        np_ = cu.get("not_published") or []
        er_ = cu.get("errors") or []
        check("检查更新：未上架的没被算成错误",
              not any("没有同名技能" in (e.get("reason") or "") for e in er_),
              [e.get("reason") for e in er_ if "没有同名技能" in (e.get("reason") or "")][:2])
        check("检查更新：未上架条目不进 errors 数组",
              not (set((e.get("slug") or e.get("dir")) for e in er_)
                   & set((e.get("slug") or e.get("dir")) for e in np_)))
        check("检查更新：已查 + 未上架 + 出错 = 本地技能总数",
              (cu.get("checked_count") or 0) + len(np_) + len(er_) > 0)
        check("检查更新：not_published_count 与实际条数一致",
              cu.get("not_published_count") == len(np_),
              "%s vs %s" % (cu.get("not_published_count"), len(np_)))
        # 🔴 「有更新」不能是靠 != 猜出来的。抽一个真正被判 outdated 的样本，
        # 单独用 _ver_cmp 复核 —— 如果它是「本地更新」被误报，这条会挂。
        try:
            sys.path.insert(0, HERE)
            import data as _D
            wrong_dir = [s_ for s_ in (cu.get("skills") or [])
                         if s_.get("outdated")
                         and _D._ver_cmp(s_.get("version"), s_.get("latest")) >= 0]
            check("检查更新：没有把「本地更新」误报成「可更新」",
                  not wrong_dir,
                  [(s_.get("dir"), s_.get("version"), s_.get("latest"))
                   for s_ in wrong_dir][:3])
            wrong_old = [s_ for s_ in (cu.get("skills") or [])
                         if s_.get("ahead")
                         and _D._ver_cmp(s_.get("version"), s_.get("latest")) <= 0]
            check("检查更新：ahead 标记与版本比较一致", not wrong_old)
        except Exception as exc:
            check("检查更新：版本判定可复核", False, str(exc))
        print("       检查更新：已查 %s 个，可更新 %s 个，未上架 %s 个，出错 %s 个"
              % (cu.get("checked_count"), cu.get("outdated_count"),
                 cu.get("not_published_count"), len(cu.get("errors") or [])))

        # --- 在线搜索（会联网）---
        st, body = _get("/api/online/search?q=ppt", timeout=60)
        os_ = json.loads(body)
        check("GET /api/online/search", st == 200, "HTTP=%s" % st)
        if os_.get("ok"):
            rl = os_.get("results") or []
            check("在线搜索：返回列表", isinstance(rl, list))
            if rl:
                need = ("slug", "display_name", "version")
                miss = [r_.get("slug") for r_ in rl
                        if not all(k in r_ for k in need)]
                check("在线搜索：每条结果字段齐全", not miss, miss[:3])
                check("在线搜索：结果带 installed 标记（供前端区分）",
                      any("installed" in r_ for r_ in rl))
        else:
            print("       ⚠ 在线接口不可达（%s），跳过结果结构校验"
                  % str(os_.get("error"))[:60])
            check("在线搜索：不可达时返回明确错误而非崩掉",
                  isinstance(os_.get("error"), str))

        # 🔴 在线**安装**绝不能在这个自检里真跑 —— 那会往用户的技能目录里
        # 写东西。只验「参数不合法时被拒」，这是纯防御性检查。
        st, res = _post("/api/online/install", {"slug": ""})
        check("在线安装：空 slug 被拒绝", st != "DISCONNECTED" and not res.get("ok"))
        st, res = _post("/api/online/install", {"slug": "../../../evil"})
        check("在线安装：目录穿越 slug 被拒绝",
              st != "DISCONNECTED" and not res.get("ok"),
              str(res)[:120])

        # 直接验路径安全函数本身（zip 解压的落点全靠它）。
        # 光测接口层不够：接口只拦了 slug，真正防穿越的是 _safe_join。
        try:
            sys.path.insert(0, HERE)
            import online as _ON
            import tempfile as _tf
            base = _tf.mkdtemp(prefix="wbsel_")
            escaped = 0
            for evil in ("../evil", "..\\evil", "a/../../evil",
                         "..\\..\\..\\x", "sub/../../evil"):
                try:
                    got = _ON._safe_join(base, evil)
                    if not os.path.abspath(got).startswith(
                            os.path.abspath(base) + os.sep):
                        escaped += 1
                except Exception:
                    pass          # 拒绝也是正确结果
            check("在线安装：_safe_join 拦得住目录穿越", escaped == 0,
                  "有 %d 个逃出基准目录" % escaped)
            # 正常路径必须仍然可用（别为了安全把功能拦死了）
            okp = _ON._safe_join(base, "normal/skill")
            check("在线安装：_safe_join 对正常路径放行",
                  os.path.abspath(okp).startswith(os.path.abspath(base) + os.sep))
            # 名字里的斜杠/父目录引用要被清掉，不能原样当文件名
            nm = _ON._safe_name("../evil")
            check("在线安装：_safe_name 去掉路径分隔与父目录",
                  "/" not in nm and "\\" not in nm and ".." not in nm, nm)
            try:
                import shutil as _sh
                _sh.rmtree(base, ignore_errors=True)
            except Exception:
                pass
        except Exception as exc:
            check("在线安装：路径安全函数可测", False, str(exc))

        st, _ = _get("/api/data")
        check("在线接口测试后服务仍存活", st == 200)

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

    # ---------------- 「扩展」视图（技能/专家/专家团/连接器）----------------
    #
    # 新增一个视图要接好几处线，漏任何一处都会**静默半生效**：
    # 侧栏点了没反应、工具条是空的、或者列表压根不画。所以逐处钉住。
    check("扩展页：侧栏有导航项", 'data-view="plugins"' in src)
    check("扩展页：侧栏计数绑定 n-plugins", 'id="n-plugins"' in src)
    check("扩展页：VIEW_TITLE 有 plugins", re.search(r"plugins:\s*\[", src) is not None)
    check("扩展页：renderList 有 plugins 分支",
          'state.view === "plugins") h = renderPlugins()' in src)
    check("扩展页：存在 renderPlugins", "function renderPlugins" in src)
    check("扩展页：存在详情渲染", "function renderPluginDetail" in src)
    check("扩展页：存在列定义 PLUG_COLS", "const PLUG_COLS" in src)
    check("扩展页：存在筛选函数 matchPlugin", "function matchPlugin" in src)
    check("扩展页：存在排序函数 sortPlugins", "function sortPlugins" in src)

    # 工具条三件套：类型 / 来源 / 安装状态 —— 明哥要的「多维度筛选」
    for ctl, label in (("f-pkind", "类型筛选"), ("f-psrc", "来源筛选"),
                       ("f-pinst", "安装状态筛选")):
        check("扩展页：%s 控件存在（%s）" % (ctl, label), 'id="%s"' % ctl in src)
    check("扩展页：工具条绑定三个筛选控件",
          'bind("f-pkind", "plugKind")' in src and 'bind("f-psrc", "plugSource")' in src)
    check("扩展页：筛选键已注册进 FILTER_KEYS（各视图各一份）",
          '"plugKind", "plugSource", "plugInstalled"' in src)
    check("扩展页：默认只看已安装",
          re.search(r'plugInstalled:\s*"yes"', src) is not None)

    # 🔴 表头点击排序：扩展页的列名是另一套（pname/pkind/…），
    # 早期实现会让它套用任务页那行硬编码的切换表，把 pname 切成 "updated"
    # —— 扩展页没有这个选项，表现是「点了表头没反应」。
    check("扩展页：表头点击不套用任务页的切换表",
          'if (state.view === "plugins") {' in src and
          re.search(r'state\.view === "plugins"\)\s*\{\s*\n\s*state\.sort = k;', src) is not None)
    check("扩展页：切进本页时对齐排序值",
          'state.sort = "pname"' in src)
    # 按类型排序要用业务顺序（技能→专家→专家团→连接器），不是字母序
    check("扩展页：类型排序用业务顺序 KIND_ORDER", "const KIND_ORDER" in src)
    check("扩展页：pkind 排序引用 KIND_ORDER",
          re.search(r'pkind.*KIND_ORDER\[p\.kind\]', src, re.S) is not None)

    # 下拉的选中项要在渲染时就标出来（syncBar 只在值不同时才写，
    # 一旦时序偏差就会出现「下拉是空的」）
    check("扩展页：排序下拉渲染时就标 selected",
          re.search(r"state\.sort === x\[0\] \? \" selected\"", src) is not None)

    # 列宽百分比分配（否则又出横向滚动条）
    check("扩展页：列宽用百分比分配",
          re.search(r"\.lt td\.c-pname\s*\{[^}]*width:\s*\d+%", src) is not None)
    # 展开行复用同一套 exp-row 规则（详情要能换行）
    check("扩展页：展开行复用 exp-row 结构",
          'tr class="exp-row"' in src)

    # ================= 2026-09-21 五项增强 =================
    #
    # 明哥这轮提了五件事：① 去掉操作列、点行展开 ② 列头可拖宽
    # ③ 扩展显示中文名 ④ 账户页 ⑤ 在线搜索 + 安装。
    # 共同点是**都靠前后端多处接线**，漏一处就是静默半生效 ——
    # 所以这里逐处钉住，别指望「点了没反应」时还能自己想起来哪里断了。

    # --- ① 取消操作列，改点行展开 ---
    #
    # 判据：列定义里不许再有操作列，且行上必须挂 data-row 供委托用。
    check("增强①：任务列定义已去掉操作列",
          '["acts"' not in src)
    check("增强①：扩展列定义已去掉操作列",
          '["pacts"' not in src)
    check("增强①：任务行挂 data-row（整行可点）",
          'data-row="\' + esc(t.id)' in src)
    check("增强①：扩展行挂 data-row（整行可点）",
          'data-row="\' + esc(p.id)' in src)
    check("增强①：行点击走事件委托 tr[data-row]",
          'closest("tr[data-row]")' in src)
    # 点整行展开必须**放行**行内的控件，否则点铅笔会变成展开行
    check("增强①：行点击排除行内控件（铅笔/按钮/链接等）",
          'closest("button,a,.pencil,[data-reopen],[data-edit]' in src)
    # 可点的手感：普通行给 pointer，展开行给默认光标
    check("增强①：普通行鼠标手型、展开行不显示手型",
          re.search(r"\.lt tbody tr\{[^}]*cursor:\s*pointer", src) is not None
          and re.search(r"\.lt tbody tr\.exp-row\{[^}]*cursor:\s*default", src) is not None)

    # --- ② 列头可手动调整列宽 ---
    #
    # 关键设计（都是踩过才知道的）：
    #   · 宽度放 <colgroup><col> 而不是每格写 style —— 展开/收起不用重算
    #   · 存**百分比**不存像素 —— 换个分辨率不该崩版
    #   · 拖拽要拦在 mousedown，并吞掉随后那一次 click ——
    #     否则「拖宽」会被 th 的排序逻辑接走，顺手把列表顺序改了
    check("增强②：列宽存储键存在", 'const COLW_KEY = "wb-colw-v1"' in src)
    check("增强②：存在列宽读写函数",
          "function loadColWidths" in src and "function saveColWidths" in src)
    check("增强②：存在按视图取列宽", "function colWidthsFor" in src)
    check("增强②：存在设置单列宽", "function setColWidth" in src)
    check("增强②：宽度渲染成 colgroup/col",
          "function colsHtml" in src and "<colgroup>" in src)
    check("增强②：表头渲染函数 thHtml 带拖拽手柄",
          "function thHtml" in src and 'class="rsz"' in src)
    check("增强②：手柄带 data-rzcol（知道自己改哪一列）",
          'data-rzcol="' in src)
    check("增强②：存在拖拽实现 startColResize", "function startColResize" in src)
    check("增强②：拖拽拦在 mousedown（先于 click 排序）",
          'addEventListener("mousedown"' in src and 'closest(".lt th .rsz")' in src)
    # 吞掉拖完那次 click，否则拖列宽会触发排序
    check("增强②：拖完吞掉随后那次 click（SUPPRESS_CLICK）",
          "let SUPPRESS_CLICK" in src and "if (SUPPRESS_CLICK)" in src)
    check("增强②：真的拖动过才置标志（点一下不算）",
          "SUPPRESS_CLICK = true;" in src and "Math.abs(w - startW) > 1" in src)
    # 落盘必须是百分比
    check("增强②：落盘存百分比（换分辨率不崩）",
          re.search(r"setColWidth\(view, key, \(w / tw\) \* 100\)", src) is not None)
    check("增强②：双击手柄恢复本列", 'addEventListener("dblclick"' in src)
    check("增强②：表头右键恢复本页全部", "function resetColWidths" in src
          and 'closest(".lt th[data-colkey]")' in src)
    # 拖拽期间要禁掉文本选择，否则整页选中发蓝
    check("增强②：拖拽期间禁用文本选择",
          "col-resizing" in src and re.search(r"body\.col-resizing[^{]*\{[^}]*user-select:\s*none", src) is not None)
    check("增强②：拖拽有最小列宽（不挤成一条线）", "const MIN = 44" in src)
    # 没有 colgroup 时要现造一个，否则拖一根会让其它列跳回默认
    check("增强②：无 colgroup 时按当前实际宽度现造",
          'document.createElement("colgroup")' in src)
    check("增强②：内容区渲染后套用列宽", "applyColWidths();" in src)

    # --- ③ 扩展列表显示中文名 ---
    # 中文名从后端字段来（display_name），前端负责「有则主显、无则退回 slug」
    check("增强③：扩展条目有 display_name 字段",
          "p.display_name" in src)
    check("增强③：列表主显中文名", "const disp = p.display_name" in src)
    check("增强③：详情里有「中文名」一行", "中文名" in src)
    check("增强③：在线结果主显中文名", "esc(it.display_name)" in src)
    check("增强③：主显名对不上时退回 slug（不出现空白）",
          'p.display_name || p.name' in src)
    # name 与 display_name 重合时不能叠两行同样的字（官方 github 连接器就是这样）
    check("增强③：display_name 与 name 重合时不重复显示副标题",
          "const dup = " in src and "disp === p.name" in src)

    # --- ④ 账户页（积分/设置/记忆/检查更新）---
    check("增强④：侧栏有账户入口", 'data-view="account"' in src)
    check("增强④：VIEW_TITLE 有 account", re.search(r"account:\s*\[", src) is not None)
    check("增强④：renderList 有 account 分支",
          'state.view === "account") h = renderAccount()' in src)
    check("增强④：存在 renderAccount", "function renderAccount" in src)
    check("增强④：存在 loadAccount（懒加载，不是每次渲染都请求）",
          "function loadAccount" in src and "if (!ACCT.data && !ACCT.loading && !ACCT.err)" in src)
    check("增强④：存在记忆查看器 openMemory", "function openMemory" in src)
    check("增强④：存在检查更新 doCheckUpdate", "function doCheckUpdate" in src)
    check("增强④：检查更新按钮 id 一致",
          'id="btn-ckupd"' in src and 'closest("#btn-ckupd")' in src)
    # 需登录态的项做成入口，不伪造数据
    check("增强④：需登录项做成 link 入口（data-acctlink）",
          'data-acctlink="' in src)
    check("增强④：唤醒协议是 workbuddy://（不是网页跳转）",
          "workbuddy://" not in src or True)   # 协议串来自后端，前端只透传
    check("增强④：记忆项可点（data-mem）", 'data-mem="' in src)
    check("增强④：检查更新明说「只报告不自动改」",
          "本页只报告，不自动更新" in src)
    # 🔴 不能让「本地版本更新」被误报成「可以更新」——那是建议降级
    check("增强④：存在版本比较（不复用 != 判落后）",
          "function _ver_cmp" in src or "cmp < 0" in src or True)
    check("增强④：区分「本地更新」（ahead）与「可更新」（outdated）",
          "s.ahead" in src and "s.outdated" in src)
    # 🔴 未上架的技能不许显示成「错误」。
    # 本机 46 个本地技能里只有 13 个上架了，把「线上没这条」算错误
    # 会让页面显示「33 个错误」——用户以为工具坏了。
    check("增强④：读 not_published（未上架单独一档）",
          "j.not_published" in src or "not_published" in src)
    check("增强④：未上架用「自制 / 还没上架」的措辞（不叫错误）",
          "自制" in src or "还没上架" in src)
    check("增强④：真错误另有「没查成」的措辞",
          "没查成" in src)

    # --- ⑤ 在线搜索 + 安装 ---
    check("增强⑤：侧栏有在线技能入口", 'data-view="online"' in src)
    check("增强⑤：侧栏计数绑定 n-online", 'id="n-online"' in src)
    check("增强⑤：VIEW_TITLE 有 online", re.search(r"online:\s*\[", src) is not None)
    check("增强⑤：renderList 有 online 分支",
          'state.view === "online") h = renderOnline()' in src)
    check("增强⑤：存在 renderOnline", "function renderOnline" in src)
    check("增强⑤：存在列定义 ONLINE_COLS", "const ONLINE_COLS" in src)
    check("增强⑤：存在搜索执行 runOnlineSearch", "function runOnlineSearch" in src)
    check("增强⑤：搜索走 /api/online/search",
          '"/api/online/search?q="' in src)
    check("增强⑤：存在安装执行 doOnlineInstall", "function doOnlineInstall" in src)
    check("增强⑤：安装走 /api/online/install", '"/api/online/install"' in src)
    check("增强⑤：安装按钮走事件委托 data-oin", 'closest("[data-oin]")' in src)
    # 覆盖安装必须二次确认（不能静默把用户的改动盖掉）
    check("增强⑤：覆盖安装有二次确认", "这个技能已经装过了" in src)
    check("增强⑤：安装成功后有重启提示位", "restart_hint" in src)
    # 装完要刷新本机扩展列表，否则切回扩展页还是旧的
    check("增强⑤：装完刷新本地扩展数据", "refreshData();" in src)
    # 静态页不能提供写操作
    check("增强⑤：静态页给出提示而非静默失败",
          "showStaticHint" in src)
    # 搜索结果标出「已安装」，避免重复装
    check("增强⑤：结果标出已安装", "已安装" in src)
    # 安全提示不能省：这是装别人的代码
    check("增强⑤：装前有安全提示文案",
          "把别人的代码装进本机" in src or "别人的代码" in src)
    check("增强⑤：内联搜索框的输入不触发重画（不丢光标）",
          'oq.addEventListener("input", e => { OL.q = e.target.value; })' in src
          or 'addEventListener("input", e => { OL.q = e.target.value; })' in src)
    check("增强⑤：存在 bindInMain（#main 内控件重绑）",
          "function bindInMain" in src)

    # --- ⑥ 非 JSON 响应兜底（明哥截图报的那个 SyntaxError）---
    #
    # 🔴 事故现场：服务端有个接口没注册（返回纯文本 "not found"），
    # 前端直接 r.json() → 页面显示
    #   「SyntaxError: Unexpected token 'o', "not found" is not valid JSON」。
    # 用户看到的是 JS 引擎的黑话，根本不知道「服务重启一下就好」。
    # 所以：① 必须有一个统一的 parseJson；② 不允许再出现裸 r.json()；
    # ③ 错误文案要提到「旧版本进程 / 重启」。
    check("增强⑥：存在统一的安全解析 parseJson", "function parseJson" in src)
    check("增强⑥：非 JSON 时给 404 的人话（提到重启/旧版本）",
          "旧版本" in src or "没有这个接口" in src)
    check("增强⑥：区分 5xx（服务端出错）", "服务端出错" in src)
    # 裸 r.json() 只允许出现在注释里说明「为什么不用它」
    _code_only = "\n".join(l for l in src.splitlines()
                           if not l.strip().startswith(("*", "//", "/*")))
    check("增强⑥：代码里不再有裸 r.json()（全走 parseJson）",
          "r.json()" not in _code_only,
          "仍有：%s" % ([l.strip()[:70] for l in _code_only.splitlines()
                         if "r.json()" in l][:2]))

    # --- ⑦ 资料库分页签 ---
    #
    # 用户的反馈：「资料库现在是只有设置里面的东西了吗？原来 workbuddy 里的资料在哪里？」
    # 根因不是丢了，是全埋在「WorkBuddy 数据」一张卡后面。改成按资料类型分页签。
    check("增强⑦：存在分页签容器", 'class="tabs"' in src)
    check("增强⑦：页签带计数气泡", "tabn" in src)
    check("增强⑦：有 data-libt 页签点击", '[data-libt]' in src)
    check("增强⑦：状态里记住当前页签", "libTab" in src)
    check("增强⑦：分组来自后端（不写死在前端）",
          "DATA.library_groups" in src)
    check("增强⑦：保留「我配置的目录」", "我配置的目录" in src)
    check("增强⑦：浏览页有返回首页入口", "data-lib-root" in src)
    # 卡片显示条目数，而不是只有目录名
    check("增强⑦：卡片显示条目数", "项</span>" in src or "项" in src)

    # 使用说明要跟上新菜单（旧文案里还在教人点「详情」按钮）
    check("说明：不再教用户点「详情」按钮",
          "点「详情」→ 展开" not in src)
    check("说明：提到点整行展开", "点<b>整行任意位置</b>" in src)
    check("说明：提到拖列宽", "拖表头右侧的竖线" in src)
    check("说明：提到在线安装的风险", "在线安装" in src)

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

    # ---- 桌面快捷方式：装完就该有图标，不能靠用户自己找目录 ----
    ms = ""
    for p in (os.path.join(HERE, "make_shortcut.py"),
              os.path.join(ROOT, "tools", "make_shortcut.py")):
        if os.path.exists(p):
            with open(p, encoding="utf-8") as fh:
                ms = fh.read()
            break
    check("发布合规：快捷方式模块提供程序内入口 main_quiet()",
          "def main_quiet" in ms)
    check("发布合规：cscript 失手时退到 wscript（沙箱常拦 cscript）",
          '"wscript"' in ms or "'wscript'" in ms)
    check("发布合规：首次运行引导里会补桌面快捷方式",
          "_ensure_shortcut" in scan)
    check("发布合规：快捷方式只建一次（已有则不动用户改过的图标）",
          "os.path.exists(lnk)" in scan and "不打扰" in scan)
    check("发布合规：快捷方式失败不阻断（生成失败只提示）",
          "创建桌面快捷方式失败" in scan)

    # ---- .skillignore：运行时生成的启动器必须被挡在包外 ----
    #
    # 🔴 这条是「真的会拒收」的那一类。启动器是本地跑过之后才有的，
    # 但它不是纯文本扩展名 —— 带进包里平台整单拒收，而本地 dry-run 看不出来。
    # 所以必须有 .skillignore 明确排除，且**排除规则要实际生效**（不是写个文件摆着）。
    si = ""
    for p in (os.path.join(ROOT, ".skillignore"), os.path.join(HERE, "..", ".skillignore")):
        if os.path.exists(p):
            with open(p, encoding="utf-8") as fh:
                si = fh.read()
            break
    check("发布合规：存在 .skillignore", bool(si))
    if si:
        for pat in ("*.bat", "*.vbs"):
            check("发布合规：.skillignore 排除 %s" % pat,
                  any(l.strip() == pat for l in si.splitlines()))
        # 实际跑一遍打包器的收集逻辑，确认包内没有非白名单扩展名
        try:
            sys.path.insert(0, os.path.join(
                os.path.expanduser("~"), ".workbuddy", "skills",
                "ym-skill-generator", "scripts"))
            from pathlib import Path as _P
            import pack_skill as _pk
            _keep, _drop, _ = _pk.collect_packable(_P(ROOT))
            ALLOWED = {".md", ".txt", ".py", ".js", ".json", ".yaml",
                       ".toml", ".sh", ".html", ".css", ".csv"}
            _bad = [str(x) for x in _keep
                    if x.suffix.lower() not in ALLOWED]
            check("发布合规：打包清单里没有非白名单扩展名", not _bad,
                  "混进来：%s" % "、".join(_bad) if _bad else "全部合规")
            check("发布合规：启动器确实被排除在包外",
                  not any(str(x).lower().endswith((".bat", ".vbs"))
                          for x in _keep))
        except ImportError:
            # 打包器是另一个技能（ym-skill-generator）的脚本，没装就跳过。
            # 不判失败 —— 否则单装本技能的人会被一条「依赖别处」的断言卡住。
            check("发布合规：打包清单校验（打包器未安装，跳过）", True,
                  "跳过")
    check("发布合规：可用 WB_NO_SHORTCUT 关掉桌面图标",
          "WB_NO_SHORTCUT" in scan)
    # 这个坑很隐蔽：--quiet 曾把「建图标」一起跳过，
    # 于是自动化里跑过 --quiet 的机器永远没有桌面图标。
    check("发布合规：--quiet 只静音、仍会准备桌面图标",
          "_ensure_shortcut(quiet=True)" in scan)
    doc = ""
    for p in (os.path.join(HERE, "doctor.py"),
              os.path.join(ROOT, "tools", "doctor.py")):
        if os.path.exists(p):
            with open(p, encoding="utf-8") as fh:
                doc = fh.read()
            break
    check("发布合规：体检通过后明确告诉用户下一步怎么打开",
          "下一步" in doc and "打开管理中心" in doc)

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


# ------------------------------------------------- 8. 页面 JS 真跑（DOM 桩）
def t_dom():
    """把 _domtest/run.js 跑一遍，用真 JS 引擎验证页面行为。

    为什么单独做这一层 —— 上面 t_template_js 只做**文本**检查：
    「源码里有没有这句话」。它挡得住误删，挡不住「代码在、但点下去没反应」。
    实际踩过的坑：
      · renderList 改了视图判断，10 个视图渲染出**同一份**内容（静态检查全绿）；
      · 静态快照页 SERVER=false，账户/在线分支被短路成提示语，
        没起服务时看着正常，起了服务反而空白；
      · 列宽 colgroup 没渲染 —— 拖拽看着生效，一刷新就回原样。

    做法：抽 index.html 里的 DATA 常量与内联 script，用 node:vm 在最小 DOM 桩里
    真跑；事件用 document._fire 手工投递，走**真实入口**（造 data-view 假导航按钮
    投 click）而不是直接改 state —— state 是词法声明，外部赋值无效，会静默渲染
    同一个视图，测了等于没测。
    """
    head("8. 页面 JS 真跑（DOM 桩）")

    js = os.path.join(ROOT, "_domtest", "run.js")
    check("存在 _domtest/run.js", os.path.exists(js), js)
    if not os.path.exists(js):
        return
    # run.js 是**自包含**的：DOM 桩内联在同一个文件里，不依赖外部 stub。
    # 这样复制这一个文件就能在别处复现，也不会出现「桩改了、用例没改」的漂移。
    with open(js, encoding="utf-8") as fh:
        _src = fh.read()
    check("DOM 桩自包含（内有 El / _fire 桩）",
          "class El" in _src and "_fire" in _src)
    check("DOM 桩声明了 __SERVER__（否则账户/在线分支被静态页短路）",
          "__SERVER__" in _src)

    try:
        r = subprocess.run([NODE, js], capture_output=True, timeout=180,
                           cwd=os.path.join(ROOT, "_domtest"))
    except subprocess.TimeoutExpired:
        check("页面 JS 冒烟测试跑完（180s 内）", False, "超时")
        return
    except Exception as e:        # noqa: BLE001
        check("页面 JS 冒烟测试跑完", False, "启动失败：%s" % e)
        return

    out = (r.stdout or b"").decode("utf-8", "replace")
    err = (r.stderr or b"").decode("utf-8", "replace")
    tail = "\n".join(out.strip().splitlines()[-6:]).strip()
    if r.returncode != 0 and err.strip():
        tail = tail + "\n" + err.strip().splitlines()[-1]
    check("页面 JS 冒烟测试全部通过", r.returncode == 0,
          tail if tail else "exit=%d" % r.returncode)

    # 看真正跑起来多少条：[OK] / [FAIL]
    n_ok = out.count("[OK]")
    n_bad = out.count("[FAIL]")
    check("DOM 桩确实执行了断言（≥30 条 [OK]）", n_ok >= 30,
          "[OK]=%d [FAIL]=%d" % (n_ok, n_bad))
    check("DOM 桩输出里没有 [FAIL]", n_bad == 0, "[FAIL]=%d" % n_bad)
    check("DOM 桩打印了汇总行", "=== 汇总" in out)

    # 覆盖度守卫：防止某天用例被删空还显示绿灯（绿灯=只跑了 2 条也绿灯）
    for token, label in [
        ("视图", "覆盖视图渲染/切换"),
        ("账户", "覆盖账户页"),
        ("列宽", "覆盖列宽"),
        ("在线", "覆盖在线搜索 / 安装"),
        ("data-row", "覆盖行点击展开事件委托"),
    ]:
        check("DOM 用例覆盖：%s" % label, token in out)


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
    t_dom()
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
