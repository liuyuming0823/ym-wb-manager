#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
WorkBuddy 管理中心 · 控制台编码对齐
====================================
Windows 中文版的控制台输出代码页是 936(GBK)，但 Python 3 在很多环境下
会以 UTF-8 模式运行（sys.stdout.encoding == 'utf-8'）。往 GBK 控制台写
UTF-8 字节，中文就会变成乱码 —— 用户双击 bat 看到一堆花屏，会以为程序坏了。

各入口脚本在 main() 开头调一次 fix() 即可。

设计原则：只做「对齐」，不做「强制」。
- 控制台本来就是 UTF-8(65001) → 不动
- 控制台代码页表示不了中文 → 不动
- 输出被重定向到文件 / 拿不到控制台 → 不动
- 任何异常都吞掉，绝不能因为改编码把主流程搞崩
"""

import os
import sys

_APPLIED = False


def _is_real_console(stream):
    """判断这个流是不是真的连着控制台窗口（而不是管道 / 文件 / 被重定向）。

    关键：GetConsoleOutputCP() 在输出被重定向时**照样**返回控制台代码页(936)，
    但那时候字节是写进管道/文件的，读取方一般按 UTF-8 解 ——
    如果这时还硬按 GBK 写，被重定向捕获的日志就会花屏。
    所以必须先确认「确实是终端」再动手。
    """
    try:
        return bool(stream.isatty())
    except Exception:
        return False


def fix():
    """把 stdout/stderr 的编码对齐到当前控制台输出代码页。幂等。"""
    global _APPLIED
    if _APPLIED or os.name != "nt":
        return
    _APPLIED = True

    if not _is_real_console(sys.stdout) and not _is_real_console(sys.stderr):
        return                          # 输出被重定向了，保持 UTF-8 最安全

    try:
        import ctypes
        cp = ctypes.windll.kernel32.GetConsoleOutputCP()
    except Exception:
        return

    if not cp or cp == 65001:          # 控制台本来就是 UTF-8
        return

    try:
        enc = "cp%d" % cp
        "\u6d4b\u8bd5".encode(enc)     # 确认这编码能表示中文（"测试"）
    except Exception:
        return

    for stream in (sys.stdout, sys.stderr):
        if not _is_real_console(stream):
            continue
        try:
            stream.reconfigure(encoding=enc, errors="replace")
        except Exception:
            pass
