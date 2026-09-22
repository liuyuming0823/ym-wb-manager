#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
WorkBuddy 管理中心 · 在线技能（SkillHub）
==========================================
把 SkillHub 的公开接口接进来，让「扩展」页不只能看**本机装了什么**，
还能**搜线上有什么**并直接装。

用到的接口（全部免登录，公网 REST）：
    GET  /api/v1/search?q=<关键词>   搜索技能（模糊匹配）
    GET  /api/v1/download?slug=<slug> 下载技能 zip

设计取舍：

1. **不引第三方库**。整个项目是纯标准库，这里也只加 urllib —— 装个技能
   不该要求用户先 pip install。代价是 zip 解压要处理编码，见 _extract。

2. **搜索必须是「用户点了才搜」**，不在页面加载时自动打接口。
   理由：公网接口有超时（10 秒），放加载路径上会让整个页面卡住；
   而且每次刷新都打一遍是对别人服务的不尊重。

3. **安装要带隔离意识**。装别人的技能 = 把别人的代码放进自己的技能目录。
   这一步只做「下载 + 解压到正确位置 + 拍平命名空间」，
   安全审查交给调用方（前端会提示，技能 ym-skillhub 有完整审查流程）。
    这里不做静默安装 —— 每次都要用户明确点。

4. 🔴 **命名空间要拍平**。SkillHub 装下来的技能可能是两层
   （@namespace/slug/SKILL.md），而 WorkBuddy 只扫平铺的
   ~/.workbuddy/skills/<name>/SKILL.md —— 不拍平等于装了个看不见的东西。
"""

import io
import json
import os
import re
import shutil
import ssl
import time
import urllib.error
import urllib.parse
import urllib.request
import zipfile

BASE = "https://api.skillhub.cn"
TIMEOUT = 12

# 技能目录里最多允许放多少文件 —— 挡住「解压炸弹」。
# 一个技能包正常是几十个文件，5000 已经非常宽松。
MAX_FILES = 5000
# 解压后总大小上限（字节）。100MB 远超任何正常技能。
MAX_BYTES = 100 * 1024 * 1024


def _opener():
    """造一个**不走代理**的 opener。

    沙箱/内网环境里 HTTP_PROXY 常被劫持到不存在的代理上，
    表现是「接口明明通，程序就是超时」。这里显式清掉代理设置。
    """
    handlers = [urllib.request.ProxyHandler({})]
    try:
        ctx = ssl.create_default_context()
        # 部分内网环境要抓包/自签证书；这里保留校验，
        # 只有确证是证书问题时才退让（见 _get 的 fallback）。
        handlers.append(urllib.request.HTTPSHandler(context=ctx))
    except Exception:                     # noqa: BLE001
        pass
    return urllib.request.build_opener(*handlers)


def _get(url, timeout=TIMEOUT, raw=False):
    """发一个 GET，返回 (ok, 数据或错误说明)。"""
    req = urllib.request.Request(url, headers={
        "User-Agent": "wb-manager/1.2 (+local dashboard)",
        "Accept": "application/json, application/zip, */*",
    })
    try:
        with _opener().open(req, timeout=timeout) as r:
            body = r.read()
            return (True, body) if raw else (True, json.loads(body.decode("utf-8", "replace")))
    except urllib.error.HTTPError as exc:
        return False, "接口返回 HTTP %s" % exc.code
    except urllib.error.URLError as exc:
        # SSL 证书问题在内网很常见，退一步用不校验的上下文再试一次。
        if "CERTIFICATE" in str(exc).upper() or "SSL" in str(exc).upper():
            try:
                ctx = ssl.create_default_context()
                ctx.check_hostname = False
                ctx.verify_mode = ssl.CERT_NONE
                op = urllib.request.build_opener(
                    urllib.request.ProxyHandler({}),
                    urllib.request.HTTPSHandler(context=ctx))
                with op.open(req, timeout=timeout) as r:
                    body = r.read()
                    return (True, body) if raw else (True, json.loads(body.decode("utf-8", "replace")))
            except Exception as exc2:      # noqa: BLE001
                return False, "网络请求失败：%s" % exc2
        return False, "网络请求失败：%s" % exc
    except Exception as exc:               # noqa: BLE001
        return False, "网络请求失败：%s" % exc


# ------------------------------------------------------------------ 搜索

def _norm_item(it):
    """把搜索结果归一成前端好用的结构。"""
    if not isinstance(it, dict):
        return None
    ns = it.get("namespace") or {}
    slug = it.get("slug") or ""
    if not slug:
        return None
    return {
        "slug": slug,
        "name": it.get("name") or it.get("displayName") or slug,
        # 中文名：displayName 常常就是中文；description_zh 是中文说明
        "display_name": it.get("displayName") or it.get("name") or slug,
        "desc": it.get("description_zh") or it.get("description") or "",
        "category": it.get("category") or "",
        "version": it.get("version") or (it.get("latestVersion") or {}).get("version") or "",
        "author": (it.get("owner") or {}).get("displayName") or it.get("owner_name") or "",
        "namespace": ns.get("handle") or "",
        "canonical": ns.get("canonicalName") or ("@" + (ns.get("handle") or "") + "/" + slug),
        "downloads": it.get("downloads") or 0,
        "installs": it.get("installs") or 0,
        "stars": it.get("stars") or 0,
        "icon": it.get("icon_url") or "",
        "homepage": it.get("homepage") or "",
        "source": it.get("source") or "",
        "needs_key": (it.get("labels") or {}).get("requires_api_key") == "true",
        "updated_at": it.get("updatedAt") or it.get("updated_at"),
    }


def search(query, limit=30):
    """搜线上技能。返回 {"ok":bool, "results":[...], "error":str}。

    参数名是 **q** —— 传 query / keyword 会走另一套逻辑（返回兜底热门榜），
    看着像「有结果」其实跟关键词无关。这是踩过的坑。
    """
    q = (query or "").strip()
    if not q:
        return {"ok": True, "results": [], "query": ""}
    ok, data = _get("%s/api/v1/search?q=%s" % (BASE, urllib.parse.quote(q)))
    if not ok:
        return {"ok": False, "results": [], "error": data, "query": q}
    arr = data.get("results") if isinstance(data, dict) else None
    if not isinstance(arr, list):
        return {"ok": True, "results": [], "query": q}
    out = []
    for it in arr[:max(1, min(limit, 60))]:
        n = _norm_item(it)
        if n:
            out.append(n)
    return {"ok": True, "results": out, "query": q}


def detail(slug):
    """取单个技能的详情（搜索结果里字段不够时补一下）。"""
    slug = (slug or "").strip()
    if not slug or not re.match(r"^[A-Za-z0-9._\-]+$", slug):
        return {"ok": False, "error": "slug 非法"}
    ok, data = _get("%s/api/v1/skills/%s" % (BASE, urllib.parse.quote(slug)))
    if not ok:
        return {"ok": False, "error": data}
    return {"ok": True, "skill": data}


# ------------------------------------------------------------------ 安装

_SAFE_SEG = re.compile(r"[^A-Za-z0-9._\-\u4e00-\u9fff]+")


def _safe_name(s):
    """把 slug / 目录名规整成安全的单层目录名。"""
    s = _SAFE_SEG.sub("-", (s or "").strip()) or "skill"
    return s.strip(".-") or "skill"


def _safe_join(root, name):
    """把 zip 内的相对路径拼成绝对路径，并挡住 ../ 穿越。"""
    target = os.path.normpath(os.path.join(root, name.replace("\\", "/")))
    root_abs = os.path.normpath(root)
    if target != root_abs and not target.startswith(root_abs + os.sep):
        return None
    return target


def install(slug, skills_dir, namespace="", overwrite=False):
    """下载并安装一个技能。

    返回 {"ok":bool, "installed_dir":str, "message":str, "files":int, "error":str}

    🔴 拍平逻辑：包里如果是 `<ns>/<slug>/SKILL.md` 这种嵌套结构，
    必须把最内层含 SKILL.md 的那层提到 skills_dir 下 ——
    WorkBuddy 只认平铺的一层，嵌套的扫不到（装了等于没装）。
    """
    slug = (slug or "").strip()
    if not slug or not re.match(r"^[A-Za-z0-9._\-]+$", slug):
        return {"ok": False, "error": "slug 非法"}
    if not skills_dir:
        return {"ok": False, "error": "技能目录未配置"}

    target_root = os.path.join(skills_dir, _safe_name(slug))
    if os.path.exists(target_root) and not overwrite:
        return {"ok": False, "installed_dir": target_root,
                "error": "同名技能已存在（%s）。要覆盖请勾选「覆盖安装」。" % target_root}

    url = "%s/api/v1/download?slug=%s" % (BASE, urllib.parse.quote(slug))
    ok, blob = _get(url, timeout=60, raw=True)
    if not ok:
        return {"ok": False, "error": blob}
    if not isinstance(blob, (bytes, bytearray)) or len(blob) < 32:
        return {"ok": False, "error": "下载内容异常（太小）"}
    if blob[:2] != b"PK":
        return {"ok": False, "error": "下载的不是 zip（可能是登录跳转页）"}

    try:
        zf = zipfile.ZipFile(io.BytesIO(blob))
    except zipfile.BadZipFile as exc:
        return {"ok": False, "error": "压缩包损坏：%s" % exc}

    infos = zf.infolist()
    if len(infos) > MAX_FILES:
        return {"ok": False, "error": "包内文件过多（%d），拒绝解压" % len(infos)}
    total = sum(i.file_size for i in infos)
    if total > MAX_BYTES:
        return {"ok": False, "error": "解压后超过 %d MB，拒绝解压" % (MAX_BYTES // 1048576)}

    # 先解到临时目录，确认结构没问题再搬过去 ——
    # 直接往正式目录解，中途失败会留下半个技能，比不装更糟（扫到没 SKILL.md 的目录）。
    tmp = target_root + ".installing"
    if os.path.isdir(tmp):
        shutil.rmtree(tmp, ignore_errors=True)
    os.makedirs(tmp, exist_ok=True)
    try:
        for info in infos:
            name = info.filename
            if not name or name.endswith("/"):
                continue
            dest = _safe_join(tmp, name)
            if dest is None:
                continue                       # 穿越路径，丢掉
            os.makedirs(os.path.dirname(dest), exist_ok=True)
            with zf.open(info) as src, open(dest, "wb") as dst:
                shutil.copyfileobj(src, dst)
    except Exception as exc:                   # noqa: BLE001
        shutil.rmtree(tmp, ignore_errors=True)
        return {"ok": False, "error": "解压失败：%s" % exc}

    # 找 SKILL.md 所在的那一层，把它当作技能根（拍平）
    src_root = _find_skill_root(tmp)

    if overwrite and os.path.isdir(target_root):
        # 旧目录进回收站式的备份，而不是直接删 —— 覆盖装失败了还能捞回来
        bak = target_root + ".bak-%s" % time.strftime("%Y%m%d%H%M%S")
        try:
            os.rename(target_root, bak)
        except OSError:
            shutil.rmtree(target_root, ignore_errors=True)
    elif os.path.exists(target_root):
        shutil.rmtree(tmp, ignore_errors=True)
        return {"ok": False, "error": "目标已存在：%s" % target_root}

    try:
        if src_root != tmp:
            os.rename(src_root, target_root)
            shutil.rmtree(tmp, ignore_errors=True)
        else:
            os.rename(tmp, target_root)
    except OSError as exc:
        shutil.rmtree(tmp, ignore_errors=True)
        return {"ok": False, "error": "落位失败：%s" % exc}

    # 补一份 _skillhub_meta.json，让「扩展」页能显示中文名与来源。
    # 官方客户端装技能时会写这个文件，我们直接装（绕过客户端）就得自己补，
    # 否则列表里这个技能没有中文名，还会被当成「本地目录」而不是「市场装的」。
    try:
        meta_p = os.path.join(target_root, "_skillhub_meta.json")
        if not os.path.exists(meta_p):
            with open(meta_p, "w", encoding="utf-8") as fh:
                json.dump({"name": "", "slug": slug, "namespace": namespace,
                           "source": "skillhub", "installedAt": int(time.time() * 1000)},
                          fh, ensure_ascii=False, indent=2)
    except OSError:
        pass

    n_files = sum(len(f) for _, _, f in os.walk(target_root))
    return {"ok": True, "installed_dir": target_root, "files": n_files,
            "message": "已装到 %s" % target_root}


def _find_skill_root(root):
    """在解压出来的目录树里找「技能根」。

    判据优先级：
      1) 根本身就有 SKILL.md —— 直接用它
      2) 往下最多两层，找唯一一个含 SKILL.md 的目录（处理 @ns/slug/ 嵌套）
      3) 都找不到就退回 root（可能是个不规范的包，交给用户自己看）
    """
    if os.path.isfile(os.path.join(root, "SKILL.md")):
        return root
    hits = []
    for dp, dn, fn in os.walk(root):
        depth = dp[len(root):].count(os.sep)
        if depth >= 3:
            dn[:] = []
            continue
        if "SKILL.md" in fn:
            hits.append(dp)
    if len(hits) == 1:
        return hits[0]
    if hits:
        # 多个候选时取最浅的那个（通常是包名层）
        return sorted(hits, key=len)[0]
    return root


def installed_slugs(skills_dir):
    """列出本机技能目录的目录名，用来在搜索结果里标「已安装」。"""
    try:
        return sorted(n for n in os.listdir(skills_dir)
                      if os.path.isdir(os.path.join(skills_dir, n)))
    except OSError:
        return []
