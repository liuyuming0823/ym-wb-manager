#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
WorkBuddy 管理中心 · 配置层
============================
这个模块是**全项目唯一的路径决策点**。其他模块一律不许自己拼路径、
不许写死盘符或用户名，要用什么都从这里拿。

为什么要有这一层（设计动机）
---------------------------
这个工具最初是给自己用的，路径直接写成了 D:\\workbuddy、D:\\liuyuming\\GitProject。
要分享给别人时问题就来了：
  - 别人的用户名不是 liuyuming
  - 别人可能根本没有 D 盘，或者项目不在那个位置
  - 别人装的是 Python 3.12，而启动脚本写死了 3.13.12

与其让每个人去改源码，不如把「会因为机器不同而不同的东西」全部集中到
一个配置文件里，代码只读配置。这样换机器就是改一行 JSON，而不是翻遍十六个文件。

配置分层（优先级从低到高）
------------------------
  1. 内置默认值       —— 代码里的 DEFAULTS，保证任何情况下都有兜底
  2. config.json     —— 用户可见可改的主配置，放在面板根目录
  3. 环境变量         —— WB_ 前缀，给自动化/测试用，优先级最高

为什么要三层而不是只留 config.json：
默认值保证「删了配置文件也能跑」，环境变量让自动测试可以在不改用户配置的
前提下替换掉数据目录（否则测一次就得备份还原一次真实配置，太脆弱）。

首次运行行为
-----------
如果 config.json 不存在，会自动**探测本机环境**生成一份，并打印探测结果。
探测只做「读目录、看存在性」这类无害操作，绝不写 WorkBuddy 的数据。
"""

import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)                     # 面板根目录
CONFIG_PATH = os.path.join(ROOT, "config.json")

HOME = os.path.expanduser("~")


# ------------------------------------------------------------ 默认值

def _default_wb_dir():
    """WorkBuddy 的数据目录。

    正常就是 ~/.workbuddy。留成函数是为了将来万一要支持自定义位置时，
    改动只需要落在这一个地方，而不是散落在各模块里。
    """
    return os.path.join(HOME, ".workbuddy")


DEFAULTS = {
    "workbuddy_dir": _default_wb_dir(),
    # 资料库：想在面板里浏览的目录。为空数组也没问题，页面会提示去设置里添加。
    "library_roots": [],
    # 服务端口。被占用时 serve.py 会自动往上找。
    "port": 8777,
    # 备份保留份数（见 ops.py 里关于「为什么是 8」的说明）
    "keep_backups": 8,
}


# ------------------------------------------------------------ 环境探测

def _probe_library_roots():
    """探测本机可能想浏览的目录。

    设计原则：**只收真实存在、且看起来有内容的目录**，宁缺毋滥。
    因为资料库面板的价值在于「点进去能看见东西」，塞一堆不存在的路径进去，
    用户点开全是报错，反而让人以为工具坏了。

    探测顺序大致是「从最可能是自己的东西，到最可能是共享的东西」：
    WorkBuddy 自己的数据目录一定存在（否则整个工具没意义），所以优先放进去；
    然后是当前工作区、常见代码目录、桌面/文档这类通用位置。
    """
    cands = []

    # 1) WorkBuddy 自身的数据与技能目录 —— 几乎必然存在
    wb = _default_wb_dir()
    cands.append(("WorkBuddy 数据", wb, "技能、方案、任务数据"))
    cands.append(("技能目录", os.path.join(wb, "skills"), "已安装的技能"))

    # 2) 面板所在目录（别人解压到哪里，工作区就在哪里）
    cands.append(("本工具所在目录", ROOT, "管理中心自身"))

    # 3) 常见代码目录：按盘符扫描顶层，找名字像代码仓库的
    code_names = ("GitProject", "GitProjects", "Projects", "Code", "code",
                  "workspace", "Workspace", "repos", "src", "dev")
    for drive in _candidate_drives():
        root = drive + os.sep
        try:
            entries = os.listdir(root)
        except OSError:
            continue
        for name in entries:
            p = os.path.join(root, name)
            if not os.path.isdir(p):
                continue
            # 3a) 盘符顶层就叫 GitProject 这种
            if name in code_names and _has_content(p):
                cands.append(("代码目录", p, "代码仓库"))
                continue
            # 3b) 用户名目录下面挂的（D:\<用户名>\GitProject 这种结构很常见）。
            #     只往下探一层，不做递归 —— 递归扫盘会很慢，而且容易把
            #     系统目录、node_modules 这类噪音收进来。
            if name.lower() in ("users", "home", "user", "用户"):
                continue
            try:
                sub_entries = os.listdir(p)
            except OSError:
                continue
            if len(sub_entries) > 60:
                continue               # 东西太多，多半不是「用户名目录」
            for sub in sub_entries:
                if sub in code_names:
                    sp = os.path.join(p, sub)
                    if _has_content(sp):
                        cands.append(("代码目录", sp, "代码仓库"))

    # 4) 通用位置：桌面 / 文档 / 下载
    for label, sub in (("桌面", "Desktop"), ("文档", "Documents"),
                       ("下载", "Downloads")):
        p = os.path.join(HOME, sub)
        if _has_content(p):
            cands.append((label, p, "%s目录" % label))

    # 去重（同一个路径只留第一次出现的那个名字）
    seen, out = set(), []
    for name, path, desc in cands:
        try:
            key = os.path.normcase(os.path.abspath(path))
        except Exception:
            continue
        if key in seen:
            continue
        seen.add(key)
        out.append({"name": name, "path": path, "desc": desc})
    return out


def _candidate_drives():
    """列出本机可能有意义的盘符（Windows），非 Windows 返回根目录。"""
    if os.name != "nt":
        return ["/" if os.path.isdir("/") else ""]
    drives = []
    for letter in "CDEFGH":
        d = letter + ":"
        if os.path.isdir(d + os.sep):
            drives.append(d)
    return drives


def _has_content(path):
    """目录存在且至少有内容。

    只看**第一项**就返回 —— 判断「是不是空目录」不需要数完，
    Desktop / GitProject 这种目录可能塞了几千个条目，全量数一遍纯属浪费。
    """
    try:
        if not os.path.isdir(path):
            return False
        with os.scandir(path) as it:
            for _ in it:
                return True
            return False
    except OSError:
        return False


# ------------------------------------------------------------ 读写

_CACHE = {"data": None, "mtime": None, "path": None}


def _coerce(raw):
    """把外部读进来的配置规整成可信的形状。

    配置文件是给人手改的，不能指望它格式永远正确 —— 少个键、类型写错、
    路径写成相对路径都发生过。这里统一纠一遍，坏值退回默认值，
    保证后面所有模块拿到的都是干净的。
    """
    cfg = dict(DEFAULTS)

    wb = raw.get("workbuddy_dir")
    if isinstance(wb, str) and wb.strip():
        cfg["workbuddy_dir"] = os.path.abspath(wb.strip())

    roots = raw.get("library_roots")
    clean = []
    if isinstance(roots, list):
        for item in roots:
            if isinstance(item, str) and item.strip():
                item = {"name": os.path.basename(item.rstrip("\\/")) or item,
                        "path": item, "desc": ""}
            if not isinstance(item, dict):
                continue
            p = item.get("path")
            if not isinstance(p, str) or not p.strip():
                continue
            p = p.strip()
            if not os.path.isabs(p):
                # 相对路径按面板目录解析 —— 手写配置时很容易写成相对路径
                p = os.path.join(ROOT, p)
            clean.append({
                "name": str(item.get("name") or os.path.basename(p.rstrip("\\/")) or p),
                "path": os.path.abspath(p),
                "desc": str(item.get("desc") or ""),
            })
    cfg["library_roots"] = clean

    for key, lo, hi in (("port", 1024, 65535), ("keep_backups", 1, 100)):
        v = raw.get(key)
        if isinstance(v, bool):
            continue                       # True/False 不是合法端口，忽略
        if isinstance(v, int) and lo <= v <= hi:
            cfg[key] = v

    return cfg


def _env_overrides(cfg):
    """环境变量覆盖（优先级最高，主要给自动测试用）。

    约定：WB_DIR 覆盖 workbuddy_dir。刻意只开放这一个 ——
    环境变量越多越难排查「为什么我改了配置没生效」。
    """
    v = os.environ.get("WB_DIR")
    if v and v.strip():
        cfg["workbuddy_dir"] = os.path.abspath(v.strip())
    return cfg


def load(force=False):
    """读配置。带 mtime 缓存：同一次进程内反复调用不会反复读盘，
    但文件一被修改就自动失效（用户在设置页改完不用重启）。"""
    try:
        mt = os.path.getmtime(CONFIG_PATH)
    except OSError:
        mt = None

    if (not force and _CACHE["data"] is not None
            and _CACHE["path"] == CONFIG_PATH and _CACHE["mtime"] == mt):
        return _CACHE["data"]

    raw = {}
    if mt is not None:
        try:
            with open(CONFIG_PATH, "r", encoding="utf-8") as fh:
                loaded = json.load(fh)
            if isinstance(loaded, dict):
                raw = loaded
        except Exception:
            # 配置文件坏了不能让整个工具起不来 —— 退回默认值继续跑，
            # 后面 doctor.py 会告诉用户「配置文件读不了」。
            raw = {}

    cfg = _env_overrides(_coerce(raw))
    _CACHE.update({"data": cfg, "mtime": mt, "path": CONFIG_PATH})
    return cfg


def save(cfg):
    """写回配置文件。失败返回 (False, 原因)，成功返回 (True, 路径)。"""
    try:
        payload = {
            "_说明": "WorkBuddy 管理中心的配置。改完保存，刷新页面即生效。",
            "_workbuddy_dir": "WorkBuddy 的数据目录，正常不用改",
            "workbuddy_dir": cfg.get("workbuddy_dir"),
            "_library_roots": "资料库面板里要浏览的目录，可增删。name 是显示名",
            "library_roots": cfg.get("library_roots") or [],
            "_port": "服务端口，被占用时会自动往后找",
            "port": cfg.get("port"),
            "_keep_backups": "写操作自动备份的保留份数",
            "keep_backups": cfg.get("keep_backups"),
        }
        tmp = CONFIG_PATH + ".tmp"
        with open(tmp, "w", encoding="utf-8") as fh:
            json.dump(payload, fh, ensure_ascii=False, indent=2)
            fh.write("\n")
        os.replace(tmp, CONFIG_PATH)      # 原子替换，避免写一半断电留下坏文件
        _CACHE["mtime"] = None            # 让下次 load() 重新读
        _CACHE["data"] = None
        return True, CONFIG_PATH
    except OSError as exc:
        return False, str(exc)


def ensure(verbose=False):
    """确保配置文件存在；不存在就探测环境生成一份。

    返回 (cfg, created)。created=True 表示这次是新建的。
    首次运行会打印探测到了什么 —— 用户能看到「工具认识我这台机器」，
    比默默生成一个文件更让人放心。
    """
    if os.path.exists(CONFIG_PATH):
        return load(force=True), False

    cfg = dict(DEFAULTS)
    cfg["library_roots"] = _probe_library_roots()
    ok, _ = save(cfg)

    if verbose and ok:
        print("[config] 首次运行，已生成配置文件：%s" % CONFIG_PATH)
        print("[config] 自动探测到 %d 个资料库目录：" % len(cfg["library_roots"]))
        for r in cfg["library_roots"]:
            print("           %-14s %s" % (r["name"], r["path"]))
        if not cfg["library_roots"]:
            print("           没有探测到可用目录，可在设置页手动添加")
    return load(force=True), ok


def wb_dir():
    return load()["workbuddy_dir"]


def db_path():
    return os.path.join(wb_dir(), "workbuddy.db")


def artifact_dir():
    return os.path.join(wb_dir(), "artifact-index")


def skills_dir():
    """技能安装目录（装线上技能时的落位点）。

    为什么不写死 ~/.workbuddy/skills：用户完全可能把 WB_DIR 配到别处
    （换机器、多份数据目录并存），路径一律从 wb_dir() 现推。
    """
    return os.path.join(wb_dir(), "skills")


def plugins_dir():
    return os.path.join(wb_dir(), "plugins")


def projects_dir():
    return os.path.join(wb_dir(), "projects")


def library_roots():
    """资料库根目录。

    只返回**当前真实存在**的目录 —— 配置文件里留了一条已经不存在的路径
    （换了电脑、删了文件夹）时，不该在页面上显示成一个点了就报错的入口。
    """
    out = []
    for r in load()["library_roots"]:
        if os.path.isdir(r["path"]):
            out.append(dict(r))
        else:
            item = dict(r)
            item["missing"] = True
            out.append(item)
    return out


def existing_library_roots():
    """只要存在的那些（给文件浏览用）。"""
    return [r for r in library_roots() if not r.get("missing")]


if __name__ == "__main__":
    import argparse
    sys.path.insert(0, HERE)
    try:
        import console
        console.fix()
    except Exception:
        pass

    ap = argparse.ArgumentParser(description="管理中心配置")
    ap.add_argument("--show", action="store_true", help="显示当前配置")
    ap.add_argument("--probe", action="store_true", help="重新探测资料库目录并写入")
    ap.add_argument("--reset", action="store_true", help="删掉配置重新生成")
    ap.add_argument("--port", action="store_true",
                    help="只打印端口号（给 .bat 启动器读，输出干净到可以 for /f 直接取）")
    args = ap.parse_args()

    # 这个分支要放在最前面，而且**只能打印端口本身**。
    # .bat 里是 `for /f ... do set "PORT=%%P"` —— 多打一行就会把 PORT
    # 覆盖成那行文字，最后拼出一个不存在的 URL。
    if args.port:
        print(load().get("port", DEFAULTS["port"]))
        sys.exit(0)

    if args.reset:
        if os.path.exists(CONFIG_PATH):
            os.remove(CONFIG_PATH)
            print("已删除配置文件")
        cfg, created = ensure(verbose=True)
        print("路径：%s" % CONFIG_PATH)
    elif args.probe:
        cfg = load(force=True)
        cfg["library_roots"] = _probe_library_roots()
        ok, msg = save(cfg)
        print("探测到 %d 个目录，写入%s：%s"
              % (len(cfg["library_roots"]), "成功" if ok else "失败", msg))
        for r in cfg["library_roots"]:
            print("  %-14s %s" % (r["name"], r["path"]))
    else:
        cfg = load(force=True)
        print("配置文件：%s（%s）"
              % (CONFIG_PATH, "存在" if os.path.exists(CONFIG_PATH) else "尚未生成"))
        print("WorkBuddy 目录：%s  %s"
              % (cfg["workbuddy_dir"],
                 "存在" if os.path.isdir(cfg["workbuddy_dir"]) else "不存在"))
        print("数据库：%s  %s"
              % (db_path(), "存在" if os.path.exists(db_path()) else "不存在"))
        print("端口：%s   备份保留：%s 份" % (cfg["port"], cfg["keep_backups"]))
        print("资料库目录（%d 个）：" % len(cfg["library_roots"]))
        for r in cfg["library_roots"]:
            mark = "" if os.path.isdir(r["path"]) else "   [不存在]"
            print("  %-14s %s%s" % (r["name"], r["path"], mark))
