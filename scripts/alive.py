#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
WorkBuddy 管理中心 · 服务探活
==============================
检查管理中心本地服务是否已经在运行（供启动 bat 判断要不要重复启动）。

退出码：
  0  已在运行
  1  没在运行

用法：
    python alive.py            # 默认探测 8777-8791
    python alive.py --port 8777
"""

import argparse
import json
import os
import socket
import sys
import time
import urllib.request

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import console  # noqa: E402

DEFAULT_PORTS = list(range(8777, 8792))

# 显式绕过代理：沙箱 / 企业环境里的 http_proxy 会劫持 127.0.0.1 请求，
# 表现为 502 Bad Gateway 或超时，看着像「服务没起来」，实际是探活路径错了。
_NO_PROXY = urllib.request.build_opener(urllib.request.ProxyHandler({}))


def port_open(port, timeout=0.25):
    """快速判断端口是否有进程在监听（本机回环，超时给得很短）。"""
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.settimeout(timeout)
    try:
        return s.connect_ex(("127.0.0.1", port)) == 0
    finally:
        s.close()


def confirm(port, timeout=2.0, retries=1):
    """完整确认：端口上确实是我们的服务（带重试，避开启动瞬间的空窗）。"""
    for attempt in range(retries + 1):
        try:
            req = urllib.request.Request("http://127.0.0.1:%d/api/data" % port)
            with _NO_PROXY.open(req, timeout=timeout) as resp:
                data = json.loads(resp.read().decode("utf-8"))
            # 认准是自己人：同时有 version 和 stats
            if isinstance(data, dict) and "stats" in data and "version" in data:
                return True
        except Exception:
            if attempt < retries:
                time.sleep(0.3)
    return False


def probe(port, timeout=1.5, retries=2):
    """单端口探测（先看端口通不通，再做 HTTP 确认）。"""
    if not port_open(port):
        # 端口没开，重试也无意义
        if retries > 0:
            time.sleep(0.15)
            if not port_open(port):
                return False
        else:
            return False
    return confirm(port, timeout=timeout, retries=retries)


def find(ports=None, deep=False):
    """找出第一个在跑的服务端口。

    deep=False（默认）：先用极短超时扫一遍端口，只对**有监听**的做 HTTP 确认。
                        15 个端口全是空的时候也就 1~2 秒返回。
    deep=True：对每个端口都做完整探测（慢，仅调试时用）。
    """
    ports = ports or DEFAULT_PORTS
    if deep:
        for p in ports:
            if probe(p):
                return p
        return None
    listen = [p for p in ports if port_open(p)]
    for p in listen:
        if confirm(p):
            return p
    return None


def main():
    console.fix()
    ap = argparse.ArgumentParser(description="探测管理中心服务")
    ap.add_argument("--port", type=int, default=None)
    ap.add_argument("--quiet", action="store_true")
    args = ap.parse_args()

    ports = [args.port] if args.port else None
    hit = find(ports)
    if hit:
        if not args.quiet:
            print("[alive] 服务在 %d 端口运行中" % hit)
        return 0
    if not args.quiet:
        print("[alive] 没有检测到运行中的服务")
    return 1


if __name__ == "__main__":
    sys.exit(main())
