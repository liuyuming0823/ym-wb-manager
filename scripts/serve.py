#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
WorkBuddy 管理中心 · 本地服务
===============================
在本地起一个**只监听 127.0.0.1** 的小服务，承载整个管理中心的读写能力。

页面与接口：
  GET  /                    管理中心页面（每次访问都实时采集）
  POST /api/refresh         重新采集并返回最新数据
  GET  /api/ls?path=        列目录（资料库浏览）
  GET  /api/open?path=      用系统默认程序打开文件
  GET  /api/reveal?path=    在资源管理器中定位文件/目录
  GET  /api/reopen?id=      唤起 WorkBuddy 打开指定会话
  GET  /api/preview?id=     删除任务前的预检（返回影响清单）
  POST /api/rename          改任务名         {id, title}
  POST /api/delete          删除任务（可恢复）{id}
  POST /api/restore         恢复任务         {id}
  POST /api/purge           彻底删除任务      {id, confirm:"DELETE"}
  GET  /api/auto-preview?id= 删除定时任务预检
  POST /api/auto-rename     改定时任务名     {id, name}
  POST /api/auto-toggle     暂停/启用定时任务 {id}
  POST /api/auto-delete     删除定时任务      {id}
  POST /api/auto-restore    恢复定时任务      {id}
  POST /api/backup          立即备份数据库
  GET  /api/log?limit=      操作日志
  GET  /api/online/search?q= 搜线上技能（SkillHub，免登录）
  GET  /api/online/skill?slug= 线上技能详情
  POST /api/online/install  安装线上技能  {slug, namespace, overwrite}
  GET  /api/account         账户、积分、记忆与版本信息
  GET  /api/memory?path=    读一个记忆文件
  POST /api/check-update    检查更新（客户端 + 本机技能）

安全约束（重要）：
  - 只绑定 127.0.0.1，不对外网开放
  - /api/open 与 /api/reveal 只允许操作**白名单根目录**下的路径
  - 拒绝任何包含 .. 的路径，防止目录穿越
  - 写操作全部走 ops.py，执行前自动备份数据库

用法：
    python serve.py                # 默认 127.0.0.1:8777，并自动打开浏览器
    python serve.py --port 8888
    python serve.py --no-open
"""

import argparse
import json
import os
import subprocess
import sys
import threading
import time
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse, parse_qs, unquote

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, HERE)

import config as conf         # noqa: E402
import data as datalayer     # noqa: E402
import ops                   # noqa: E402
import console               # noqa: E402
import online                # noqa: E402

TEMPLATE = os.path.join(ROOT, "assets", "template.html")
WB_DIR = conf.wb_dir()

# --------------------------------------------------------------- 白名单

_ALLOWED_CACHE = {"dirs": None, "at": 0}


def norm(p):
    """统一成小写、反斜杠结尾无关的规范形式，便于前缀比较。"""
    if not p:
        return ""
    p = os.path.abspath(os.path.normpath(p))
    return p.replace("/", "\\").rstrip("\\").lower()


def allowed_roots():
    """允许访问的根目录：WorkBuddy 数据目录 + 各任务 cwd + 资料库根 + 面板目录。

    安全注意：**不要把整个用户主目录加进来**。
    早前版本写成了 norm(HOME)，等于把 C:\\Users\\<用户> 整棵树开放出去 ——
    .ssh / .aws / Documents / Desktop 全都能读，属于越权。
    真正需要的是 HOME/.workbuddy，而它已经由 WB_DIR 覆盖了。
    """
    now = time.time()
    if _ALLOWED_CACHE["dirs"] is not None and now - _ALLOWED_CACHE["at"] < 30:
        return _ALLOWED_CACHE["dirs"]

    # 资料库根来自 config.json。这里只收**真实存在**的目录 ——
    # 配置里留了一条已经删掉的路径时，把它加进白名单没有任何意义
    # （path_allowed 会因为「路径不存在」拒掉），但会白白扩大匹配面。
    roots = {norm(WB_DIR), norm(ROOT)}
    for r in datalayer.existing_library_roots():
        try:
            roots.add(norm(r["path"]))
        except Exception:
            pass
    try:
        for s in datalayer.load_sessions().values():
            c = s.get("cwd")
            if c:
                try:
                    roots.add(norm(c))
                except Exception:
                    pass
    except Exception as exc:
        sys.stderr.write("[serve] 收集 cwd 失败：%s\n" % exc)

    # 兜底：万一 HOME 本身被当成某个 cwd 或资料库根收了进来（比如任务就在主目录下
    # 跑过），那也只会收 cwd 那一层，不会把整个 HOME 树开放。
    _ALLOWED_CACHE["dirs"] = roots
    _ALLOWED_CACHE["at"] = now
    return roots


def path_allowed(p):
    """判断路径是否在白名单内（防目录穿越 + 防越权访问）。"""
    if not p:
        return False, "路径为空"
    if ".." in p.replace("/", "\\").split("\\"):
        return False, "路径包含 .."
    try:
        ap = os.path.abspath(p)
    except Exception:
        return False, "路径非法"
    if not os.path.exists(ap):
        return False, "路径不存在"
    n = norm(ap)
    for r in allowed_roots():
        if n == r or n.startswith(r + "\\"):
            return True, None
    return False, "路径不在允许范围内"


# --------------------------------------------------------------- 采集

_cache = {"data": None, "at": 0}
_lock = threading.Lock()


def get_data(force=False):
    """采集数据，带 2 秒缓存，避免页面多次请求时反复扫盘。"""
    with _lock:
        now = time.time()
        if not force and _cache["data"] is not None and now - _cache["at"] < 2:
            return _cache["data"]
        data = datalayer.snapshot()
        _cache["data"] = data
        _cache["at"] = now
        return data


def invalidate():
    with _lock:
        _cache["data"] = None
        _cache["at"] = 0


def render_page():
    data = get_data()
    with open(TEMPLATE, "r", encoding="utf-8") as fh:
        html = fh.read()
    payload = json.dumps(data, ensure_ascii=False, separators=(",", ":"))
    payload = payload.replace("</", "<\\/")
    html = html.replace("/*__DATA__*/null", payload)
    # 标记为服务模式，前端据此启用实时刷新与写入操作
    html = html.replace("</head>", "<script>window.__SERVER__=true;</script>\n</head>", 1)
    return html.encode("utf-8")


# --------------------------------------------------------------- 动作


def do_open_file(path):
    ok, err = path_allowed(path)
    if not ok:
        return False, err
    try:
        if sys.platform == "win32":
            os.startfile(path)  # noqa: S606
        elif sys.platform == "darwin":
            subprocess.Popen(["open", path])
        else:
            subprocess.Popen(["xdg-open", path])
        return True, None
    except OSError as exc:
        return False, str(exc)


def do_reveal(path):
    ok, err = path_allowed(path)
    if not ok:
        return False, err
    try:
        if sys.platform == "win32":
            if os.path.isfile(path):
                subprocess.Popen('explorer /select,"%s"' % path)
            else:
                os.startfile(path)  # noqa: S606
        elif sys.platform == "darwin":
            subprocess.Popen(["open", "-R", path] if os.path.isfile(path) else ["open", path])
        else:
            target = path if os.path.isdir(path) else os.path.dirname(path)
            subprocess.Popen(["xdg-open", target])
        return True, None
    except OSError as exc:
        return False, str(exc)


def _memory_path_allowed(path):
    """记忆文件读取的路径闸门。

    只允许读这几个目录下的 .md / .json：
      · ~/.workbuddy/memory/          —— 云端记忆缓存
      · ~/.workbuddy/MEMORY.md 等文件  —— 用户级长期记忆
      · 配置里各项目 memory 目录       —— 项目记忆
    除此之外一律拒绝 —— 否则这个接口就是个任意文件读取漏洞。
    """
    if not path:
        return False, "路径为空"
    if ".." in path.replace("/", "\\").split("\\"):
        return False, "路径包含 .."
    try:
        ap = os.path.abspath(path)
    except Exception:                      # noqa: BLE001
        return False, "路径非法"
    if not os.path.isfile(ap):
        return False, "文件不存在"
    if os.path.splitext(ap)[1].lower() not in (".md", ".txt", ".json"):
        return False, "只允许读取 .md / .txt / .json"
    n = norm(ap)
    roots = set()
    for d in datalayer.memory_dirs():
        try:
            roots.add(norm(d))
        except Exception:                  # noqa: BLE001
            pass
    for f in datalayer.memory_files():
        try:
            roots.add(norm(f))
        except Exception:                  # noqa: BLE001
            pass
    for r in roots:
        if n == r or n.startswith(r + "\\") or r.startswith(n + "\\"):
            return True, None
    return False, "路径不在记忆目录范围内"


def do_reopen(sid):
    if not sid or not all(c in "0123456789abcdefABCDEF-" for c in sid):
        return False, "会话 ID 非法"
    url = "workbuddy://chat/" + sid
    try:
        if sys.platform == "win32":
            os.startfile(url)  # noqa: S606
        elif sys.platform == "darwin":
            subprocess.Popen(["open", url])
        else:
            subprocess.Popen(["xdg-open", url])
        return True, None
    except OSError as exc:
        return False, str(exc)


# --------------------------------------------------------------- HTTP


class Handler(BaseHTTPRequestHandler):
    server_version = "WBManager/2.0"
    protocol_version = "HTTP/1.1"

    def log_message(self, fmt, *args):  # 静音默认日志
        pass

    def _send(self, code, body, ctype="application/json; charset=utf-8"):
        if isinstance(body, str):
            body = body.encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        try:
            self.wfile.write(body)
        except (BrokenPipeError, ConnectionAbortedError):
            pass

    def _json(self, obj, code=200):
        self._send(code, json.dumps(obj, ensure_ascii=False),
                   "application/json; charset=utf-8")

    def _read_json(self):
        try:
            n = int(self.headers.get("Content-Length") or 0)
        except ValueError:
            return {}
        if n <= 0 or n > 1_000_000:
            return {}
        try:
            raw = self.rfile.read(n)
            return json.loads(raw.decode("utf-8"))
        except (ValueError, UnicodeDecodeError):
            return {}

    # -------------------------------------------------------- GET
    def do_GET(self):
        u = urlparse(self.path)
        p = u.path
        qs = parse_qs(u.query)

        def g(k):
            return unquote(qs.get(k, [""])[0])

        if p in ("/", "/index.html"):
            try:
                self._send(200, render_page(), "text/html; charset=utf-8")
            except Exception as exc:
                self._send(500, "采集失败：%s" % exc, "text/plain; charset=utf-8")
            return

        if p == "/favicon.ico":
            self._send(204, b"", "image/x-icon")
            return

        if p == "/api/refresh":
            try:
                invalidate()
                data = get_data(force=True)
                self._json({"ok": True, "data": data, "stats": data.get("stats")})
            except Exception as exc:
                self._json({"ok": False, "error": str(exc)}, 500)
            return

        if p == "/api/data":
            self._json(get_data())
            return

        if p == "/api/ls":
            target = g("path")
            ok, err = path_allowed(target)
            if not ok:
                self._json({"ok": False, "error": err})
                return
            info = datalayer.list_dir(target)
            info["parent"] = info.get("parent")
            self._json(info)
            return

        if p == "/api/open":
            ok, err = do_open_file(g("path"))
            self._json({"ok": ok, "error": err})
            return

        if p == "/api/reveal":
            ok, err = do_reveal(g("path"))
            self._json({"ok": ok, "error": err})
            return

        if p == "/api/reopen":
            ok, err = do_reopen(g("id"))
            self._json({"ok": ok, "error": err})
            return

        if p == "/api/preview":
            self._json(ops.preview_delete(g("id")))
            return

        if p == "/api/auto-preview":
            self._json(ops.preview_automation_delete(g("id")))
            return

        if p == "/api/log":
            try:
                limit = int(g("limit") or 60)
            except ValueError:
                limit = 60
            limit = max(1, min(limit, 500))
            self._json({"ok": True, "entries": ops.recent_log(limit)})
            return

        if p == "/api/config":
            # 设置页读配置。顺带把「哪些资料库目录已经失效」算出来 ——
            # 只给路径列表的话，前端还得自己判断存在与否，不如一次给全。
            try:
                cfg = conf.load(force=True)
                self._json({
                    "ok": True,
                    "config_path": conf.CONFIG_PATH,
                    "workbuddy_dir": cfg["workbuddy_dir"],
                    "workbuddy_dir_exists": os.path.isdir(cfg["workbuddy_dir"]),
                    "db_path": conf.db_path(),
                    "db_exists": os.path.exists(conf.db_path()),
                    "db_size": (os.path.getsize(conf.db_path())
                                if os.path.exists(conf.db_path()) else 0),
                    "port": cfg["port"],
                    "keep_backups": cfg["keep_backups"],
                    "library_roots": conf.library_roots(),
                })
            except Exception as exc:
                self._json({"ok": False, "error": str(exc)}, 500)
            return

        if p == "/api/doctor":
            # 环境体检。跑起来可能要 1~2 秒（要探测端口），
            # 前端那边是点按钮触发的，等一下可以接受。
            try:
                import doctor
                self._json(doctor.run())
            except Exception as exc:
                self._json({"ok": False, "error": "%s: %s" % (type(exc).__name__, exc)}, 500)
            return

        if p == "/api/suggest":
            # 让用户能重新探测一次资料库目录（换电脑后想一键恢复）。
            try:
                self._json({"ok": True, "roots": conf._probe_library_roots()})
            except Exception as exc:
                self._json({"ok": False, "error": str(exc)}, 500)
            return

        if p == "/api/online/search":
            # 搜线上技能。**只在用户点「搜索」时才调** —— 公网接口有 12 秒超时，
            # 放页面加载路径上会让整个面板卡住；而且每次刷新都打一遍不礼貌。
            try:
                res = online.search(g("q"), limit=40)
                # 标出哪些已经装了，省得用户重复装
                have = set()
                try:
                    have = {n.lower() for n in online.installed_slugs(conf.skills_dir())}
                except Exception:                       # noqa: BLE001
                    pass
                for it in res.get("results", []):
                    it["installed"] = it["slug"].lower() in have
                self._json(res)
            except Exception as exc:
                self._json({"ok": False, "error": "%s: %s" % (type(exc).__name__, exc)}, 500)
            return

        if p == "/api/online/skill":
            try:
                self._json(online.detail(g("slug")))
            except Exception as exc:
                self._json({"ok": False, "error": "%s: %s" % (type(exc).__name__, exc)}, 500)
            return

        if p == "/api/account":
            try:
                self._json(datalayer.account_info())
            except Exception as exc:
                self._json({"ok": False, "error": "%s: %s" % (type(exc).__name__, exc)}, 500)
            return

        if p == "/api/memory":
            # 读一个记忆文件。**路径限制在本机记忆目录内** ——
            # 不做限制等于给了一个任意文件读取接口。
            path = g("path")
            ok, err = _memory_path_allowed(path)
            if not ok:
                self._json({"ok": False, "error": err})
                return
            try:
                with open(path, "r", encoding="utf-8", errors="replace") as fh:
                    txt = fh.read(200_000)
                self._json({"ok": True, "path": path, "text": txt,
                            "size": os.path.getsize(path)})
            except OSError as exc:
                self._json({"ok": False, "error": str(exc)})
            return

        if p == "/api/check-update":
            try:
                self._json(datalayer.check_updates())
            except Exception as exc:
                self._json({"ok": False, "error": "%s: %s" % (type(exc).__name__, exc)}, 500)
            return

        self._send(404, "not found", "text/plain; charset=utf-8")

    # -------------------------------------------------------- POST
    def _write_op(self, fn, *a, **kw):
        """执行一个写操作并把结果回给前端。

        必须有异常兜底：早前版本这里直接裸调 ops.*，
        一旦 ops 抛异常，do_POST 就崩了 —— 客户端只会看到连接被重置
        （RemoteDisconnected），根本拿不到失败原因，完全没法排查。
        现在任何异常都转成 {"ok": false, "error": ...} 正常返回。
        """
        try:
            ok, msg, _extra = fn(*a, **kw)
            if ok:
                invalidate()
            self._json({"ok": bool(ok), "message": msg,
                        "error": None if ok else msg})
        except Exception as exc:
            self._json({"ok": False, "message": str(exc),
                        "error": "%s: %s" % (type(exc).__name__, exc)}, 500)

    def do_POST(self):
        u = urlparse(self.path)
        p = u.path
        body = self._read_json()

        if p == "/api/refresh":
            try:
                invalidate()
                data = get_data(force=True)
                self._json({"ok": True, "data": data, "stats": data.get("stats")})
            except Exception as exc:
                self._json({"ok": False, "error": str(exc)}, 500)
            return

        if p == "/api/rename":
            self._write_op(ops.rename_task, body.get("id"), body.get("title") or "")
            return

        if p == "/api/delete":
            self._write_op(ops.delete_task, body.get("id"))
            return

        if p == "/api/restore":
            self._write_op(ops.restore_task, body.get("id"))
            return

        if p == "/api/purge":
            self._write_op(ops.delete_task_permanent, body.get("id"),
                           body.get("confirm") or "")
            return

        if p == "/api/auto-rename":
            self._write_op(ops.rename_automation, body.get("id"),
                           body.get("name") or "")
            return

        if p == "/api/auto-toggle":
            self._write_op(ops.toggle_automation, body.get("id"))
            return

        if p == "/api/auto-delete":
            self._write_op(ops.delete_automation, body.get("id"))
            return

        if p == "/api/auto-restore":
            self._write_op(ops.restore_automation, body.get("id"))
            return

        if p == "/api/backup":
            try:
                bp = ops.backup_db("manual")
                self._json({"ok": True, "path": bp, "name": os.path.basename(bp),
                            "message": "备份完成"})
            except Exception as exc:
                self._json({"ok": False, "error": str(exc)}, 500)
            return

        if p == "/api/online/install":
            # 装线上技能。
            #
            # 安全取向：**不静默、不批量、不覆盖**。
            #   · slug 必须显式给（不允许「装搜索结果第一条」这种偷懒调用）
            #   · 覆盖要显式 overwrite=true，覆盖前旧目录改名保留
            #   · 落位后 invalidate() 让下次刷新能看见新技能
            try:
                slug = (body.get("slug") or "").strip()
                if not slug:
                    self._json({"ok": False, "error": "缺少 slug"})
                    return
                res = online.install(
                    slug,
                    conf.skills_dir(),
                    namespace=(body.get("namespace") or "").strip(),
                    overwrite=bool(body.get("overwrite")),
                )
                if res.get("ok"):
                    invalidate()
                    # 记一笔操作日志 —— 装了别人的代码进本机，必须留痕
                    try:
                        ops.log_action("install-skill", slug,
                                       "安装线上技能 -> %s" % (res.get("installed_dir") or ""))
                    except Exception:               # noqa: BLE001
                        pass
                self._json(res)
            except Exception as exc:
                self._json({"ok": False, "error": "%s: %s" % (type(exc).__name__, exc)}, 500)
            return

        if p == "/api/config":
            # 设置页保存配置。
            #
            # 只接受白名单字段 —— 配置里有个 workbuddy_dir（数据目录），
            # 前端理论上不会改它，但接口不能假设调用方老实：把整个 body
            # 直接写进配置，等于任何人只要能访问这个端口就能把数据目录
            # 指到别处去。这里按字段逐个取值，没提到的字段一律保持原样。
            try:
                cfg = conf.load(force=True)
                changed = []

                if "library_roots" in body:
                    cfg["library_roots"] = body["library_roots"]
                    changed.append("资料库目录")

                pv = body.get("port")
                if pv is not None:
                    try:
                        pv = int(pv)
                    except (TypeError, ValueError):
                        pv = -1
                    if not (1024 <= pv <= 65535):
                        self._json({"ok": False,
                                    "error": "端口要在 1024–65535 之间"})
                        return
                    cfg["port"] = pv
                    changed.append("端口")

                kv = body.get("keep_backups")
                if kv is not None:
                    try:
                        kv = int(kv)
                    except (TypeError, ValueError):
                        kv = -1
                    if not (1 <= kv <= 100):
                        self._json({"ok": False,
                                    "error": "备份保留份数要在 1–100 之间"})
                        return
                    # 上限受单次清理硬上限约束：配置成 100 而清理一次只能删 16 个，
                    # 会让目录长期收敛不下去。
                    if kv > ops.PRUNE_HARD_CAP * 4:
                        self._json({"ok": False,
                                    "error": "备份保留份数不要超过 %d"
                                             % (ops.PRUNE_HARD_CAP * 4)})
                        return
                    cfg["keep_backups"] = kv
                    changed.append("备份保留份数")

                ok, msg = conf.save(cfg)
                if not ok:
                    self._json({"ok": False, "error": "写配置失败：%s" % msg})
                    return
                self._json({"ok": True,
                            "message": ("已保存：%s" % "、".join(changed)) if changed
                                       else "没有任何改动",
                            "config_path": conf.CONFIG_PATH})
            except Exception as exc:
                self._json({"ok": False,
                            "error": "%s: %s" % (type(exc).__name__, exc)}, 500)
            return

        self._json({"ok": False, "error": "未知接口"}, 404)


def main():
    console.fix()
    # 首次运行先确保配置存在（会自动探测本机环境生成一份），
    # 然后端口从配置读 —— 早前这里写死 default=8777，
    # 用户在设置页把端口改成别的，下次启动又被拉回 8777，改了等于没改。
    conf.ensure(verbose=True)
    cfg = conf.load()

    ap = argparse.ArgumentParser(description="WorkBuddy 管理中心本地服务")
    ap.add_argument("--port", type=int, default=cfg.get("port", 8777),
                    help="监听端口（默认取配置文件里的值）")
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--no-open", dest="do_open", action="store_false", default=True)
    args = ap.parse_args()

    port = args.port
    srv = None
    for _ in range(10):
        try:
            srv = ThreadingHTTPServer((args.host, port), Handler)
            break
        except OSError:
            port += 1
    if srv is None:
        print("[serve] 找不到可用端口", file=sys.stderr)
        return 1

    url = "http://%s:%d/" % (args.host, port)
    print("=" * 56)
    print(" WorkBuddy 管理中心已启动")
    print(" 地址：%s" % url)
    print(" 关掉这个窗口就停止服务")
    print("=" * 56)
    if args.do_open:
        threading.Timer(0.6, lambda: webbrowser.open(url)).start()
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        print("\n[serve] 已停止")
    finally:
        srv.server_close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
