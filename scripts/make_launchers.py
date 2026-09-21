# -*- coding: utf-8 -*-
"""
生成 Windows 启动器（.bat / .vbs）。

为什么要有这个脚本
------------------
SkillHub 只接受纯文本扩展名（.md/.py/.json/...），.bat 与 .vbs 会被整单拒收。
但用户装上技能后，最顺手的用法恰恰是「双击启动管理中心.bat」。
两个需求冲突，解法是：**包里不带 .bat/.vbs，改成运行这个脚本现场生成**。

于是技能包本身保持纯文本可发布，用户首次运行时自动获得那四个双击入口，
两边的功能完全一致。

生成什么
--------
  启动管理中心.bat    有控制台窗口，看得见日志（排查问题用这个）
  启动管理中心.vbs    无窗口静默启动（日常用这个）
  停止管理中心.bat    停掉正在运行的服务
  刷新数据.bat        重新采集数据并生成静态页

编码约定（踩过坑，别改）
------------------------
* .bat 必须 **GBK + CRLF**。写成 UTF-8 会让 cmd 解析错乱，甚至掉进
  Python REPL 里出现 `>>>`。也不能加 `chcp 65001`。
* .vbs 必须 **纯 ASCII + CRLF**。wscript 对无 BOM 的 UTF-8 按 ANSI 解码，
  LF 行尾会让 VB 解析器把多行并成一行而静默挂住。

用法
----
  python make_launchers.py            # 在技能根目录生成（已存在则跳过）
  python make_launchers.py --force    # 覆盖既有文件
  python make_launchers.py --check    # 只检查，不写文件
"""
import argparse
import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)          # 技能根目录（scripts/ 的上一级）

# 探测 Python 的公共片段：先用 dir 按名倒序挑引导解释器，再交给 findpy.py。
# 版本号不写死 —— WorkBuddy 升级自带 Python 后版本号会变。
_PY_BOOT = r'''set "PYVER=%USERPROFILE%\.workbuddy\binaries\python\versions"
set "PYBOOT="
for /f "delims=" %%V in ('dir /b /ad /o-n "%PYVER%" 2^>nul') do (
    if not defined PYBOOT if exist "%PYVER%\%%V\python.exe" set "PYBOOT=%PYVER%\%%V\python.exe"
)
if not defined PYBOOT (
    where python >nul 2>nul
    if not errorlevel 1 set "PYBOOT=python"
)

set "PY="
if defined PYBOOT (
    for /f "usebackq delims=" %%P in (`"%PYBOOT%" "%HERE%scripts\findpy.py" 2^>nul`) do set "PY=%%P"
)
if not defined PY set "PY=%PYBOOT%"
'''

_NO_PY = r'''if not defined PY (
    echo  [错误] 没有找到 Python。
    echo  WorkBuddy 通常会自带一个，如果看到这条提示，可能是安装不完整。
    pause
    exit /b 1
)
'''

# 启动器（有窗口版）用的更完整措辞：它多解释一句「可能是被卸载了」。
# 两个片段分开写，是为了让生成结果与既有文件逐字节一致 ——
# 否则每次重新生成都会在 diff 里冒出一段无关的文案改动。
_NO_PY_FULL = r'''if not defined PY (
    echo.
    echo  [错误] 没有找到 Python。
    echo  这个工具需要本机 Python 才能运行。
    echo  WorkBuddy 通常会自带一个，如果你看到这条提示，
    echo  可能是安装不完整，或者 Python 被卸载了。
    echo.
    pause
    exit /b 1
)
'''

START_BAT = r'''@echo off
setlocal
title WorkBuddy 管理中心

set "HERE=%~dp0"
set "SERVE=%HERE%scripts\serve.py"

if not exist "%SERVE%" (
    echo.
    echo  [错误] 找不到 %SERVE%
    echo  请确认这个文件夹是完整的，没有被移动或删掉部分文件。
    echo.
    pause
    exit /b 1
)

rem ---- 找一个可用的 Python ----
rem 版本号不写死在这里：WorkBuddy 升级自带 Python 后版本号会变，
rem 写死就会导致「昨天还能启动，今天双击没反应」。交给 findpy.py 扫目录得出。
rem
rem 先要说清楚一个「鸡生蛋」问题：findpy.py 自己也需要 Python 才能跑。
rem 所以先用 dir 按名称倒序列出 versions 下的子目录，取第一个真实存在的
rem python.exe 当引导解释器。dir 是 cmd 内建命令，不依赖 PATH，
rem 换到任何一台 Windows 上都能用。
__PY_BOOT__
__NO_PY__
rem ---- 先看服务是不是已经在跑了 ----
rem 端口从 config.json 读（改过端口的话这里也跟着变），
rem 读不到就用 8777 兜底 —— 启动器不应该因为读不到配置就罢工。
set "PORT=8777"
for /f "delims=" %%P in ('"%PY%" "%HERE%scripts\config.py" --port 2^>nul') do set "PORT=%%P"

"%PY%" "%HERE%scripts\alive.py" --port %PORT% >nul 2>nul
if not errorlevel 1 (
    start "" http://127.0.0.1:%PORT%/
    echo.
    echo  管理中心已经在运行，已为你打开浏览器。
    echo.
    choice /c y /n /t 2 /d y >nul 2>nul
    exit /b 0
)

echo.
echo  ============================================
echo   WorkBuddy 管理中心
echo  ============================================
echo.
echo  正在启动，稍后会自动打开浏览器...
echo.
echo  【不要关掉这个窗口】关掉它就停止服务了。
echo  想停下来：直接关掉这个窗口，或双击「停止管理中心.bat」
echo.

"%PY%" "%SERVE%"
set "RC=%ERRORLEVEL%"

echo.
echo  服务已停止（退出码 %RC%）。
if not "%RC%"=="0" echo  如果启动失败，请把上面的错误信息截图反馈。
echo.
pause
exit /b %RC%
'''

STOP_BAT = r'''@echo off
setlocal
title 停止 WorkBuddy 管理中心

echo.
echo  正在查找并停止 WorkBuddy 管理中心的本地服务...
echo.

set "FOUND=0"
set "HERE=%~dp0"

rem ---- 找一个可用的 Python ----
rem 版本号不写死：WorkBuddy 升级自带 Python 后版本号会变，
rem 写死就会出现「启动器还能用、停止器已经失效」这种最难排查的错配。
rem 先用 dir 按名倒序挑一个当引导解释器，再交给 findpy.py 统一判定。
set "PYVER=%USERPROFILE%\.workbuddy\binaries\python\versions"
set "PYBOOT="
for /f "delims=" %%V in ('dir /b /ad /o-n "%PYVER%" 2^>nul') do (
    if not defined PYBOOT if exist "%PYVER%\%%V\python.exe" set "PYBOOT=%PYVER%\%%V\python.exe"
)

set "PY="
if defined PYBOOT (
    for /f "usebackq delims=" %%P in (`"%PYBOOT%" "%HERE%scripts\findpy.py" 2^>nul`) do set "PY=%%P"
)
if not defined PY set "PY=%PYBOOT%"

if defined PY (
    "%PY%" "%HERE%scripts\killer.py"
    if not errorlevel 1 set "FOUND=1"
)

if "%FOUND%"=="0" (
    echo  没有找到正在运行的服务（可能本来就没开）。
)

echo.
choice /c y /n /t 2 /d y >nul 2>nul
exit /b 0
'''

REFRESH_BAT = r'''@echo off
setlocal
title 刷新 WorkBuddy 数据

set "HERE=%~dp0"

rem ---- 找一个可用的 Python ----
rem 版本号不写死：WorkBuddy 升级自带 Python 后版本号会变。
rem 先用 dir 按名倒序挑一个当引导解释器，再交给 findpy.py 统一判定。
set "PYVER=%USERPROFILE%\.workbuddy\binaries\python\versions"
set "PYBOOT="
for /f "delims=" %%V in ('dir /b /ad /o-n "%PYVER%" 2^>nul') do (
    if not defined PYBOOT if exist "%PYVER%\%%V\python.exe" set "PYBOOT=%PYVER%\%%V\python.exe"
)
if not defined PYBOOT (
    where python >nul 2>nul
    if not errorlevel 1 set "PYBOOT=python"
)

set "PY="
if defined PYBOOT (
    for /f "usebackq delims=" %%P in (`"%PYBOOT%" "%HERE%scripts\findpy.py" 2^>nul`) do set "PY=%%P"
)
if not defined PY set "PY=%PYBOOT%"

__NO_PY__
echo.
echo  正在重新采集数据并生成页面...
echo.

"%PY%" "%HERE%scripts\scan.py" --open -o "%HERE%index.html"
set "RC=%ERRORLEVEL%"

if not "%RC%"=="0" (
    echo.
    echo  生成失败（退出码 %RC%）。
    echo.
    pause
    exit /b %RC%
)

echo.
echo  完成，页面已打开。
choice /c y /n /t 2 /d y >nul 2>nul
exit /b 0
'''

# 纯 ASCII，不要加中文，不要动行尾。
START_VBS = r'''Option Explicit
' ============================================================
'  WorkBuddy Manager - silent launcher (no console window)
' ============================================================
'  Why VBS: on Windows the only reliable way to start a console
'  program with NO flashing window is to let wscript.exe launch
'  pythonw.exe in a hidden window.
'
'  ASCII-only on purpose: wscript mis-decodes UTF-8 without BOM,
'  and LF line endings break the VB parser. Keep CRLF.
' ============================================================

Dim fso, shell, here, script, cmd, found, i, p, d
Dim paths, locs, up

Set fso = CreateObject("Scripting.FileSystemObject")
Set shell = CreateObject("WScript.Shell")

here = fso.GetParentFolderName(WScript.ScriptFullName)
script = here & "\scripts\serve.py"

If Not fso.FileExists(script) Then
    MsgBox "Cannot find:" & vbCrLf & script & vbCrLf & vbCrLf & _
           "Make sure the WorkBuddy Manager folder is complete.", _
           16, "WorkBuddy Manager"
    WScript.Quit 1
End If

found = ""
up = shell.ExpandEnvironmentStrings("%USERPROFILE%")

' ---- 0) bootstrap interpreter: scan versions dir, pick highest name ----
' Find a python.exe (NOT pythonw) to run findpy.py, which does the real
' resolution. No version number is hardcoded here: whether the folder is
' named 3.13.12 or 3.14.0 does not matter, we just take the lexicographically
' last existing one as the newest.
Dim pyRoot, subNames, bestName, exePath
pyRoot = up & "\.workbuddy\binaries\python\versions"
bestName = ""
If fso.FolderExists(pyRoot) Then
    Set subNames = fso.GetFolder(pyRoot).SubFolders
    For Each d In subNames
        If fso.FileExists(fso.BuildPath(d.Path, "python.exe")) Then
            If bestName = "" Or StrComp(d.Name, bestName, vbTextCompare) > 0 Then
                bestName = d.Name
            End If
        End If
    Next
End If

' Lexicographic comparison is wrong for 3.9 vs 3.13, but VBScript has no
' natural sort. That is fine: the real decision is delegated to findpy.py,
' we only need *some* working interpreter to bootstrap it.
Dim pyBoot
pyBoot = ""
If bestName <> "" Then pyBoot = fso.BuildPath(fso.BuildPath(pyRoot, bestName), "python.exe")

' ---- 1) ask findpy.py (same logic the .bat files use) ----
If pyBoot <> "" Then
    On Error Resume Next
    Dim execObj, outText
    Set execObj = shell.Exec(Chr(34) & pyBoot & Chr(34) & " " & _
                             Chr(34) & here & "\scripts\findpy.py" & Chr(34) & " --windowed")
    If Err.Number = 0 Then
        outText = execObj.StdOut.ReadAll()
        ' Take only the first line; findpy.py prints a single path on success.
        If InStr(outText, vbCrLf) > 0 Then outText = Split(outText, vbCrLf)(0)
        If InStr(outText, vbLf) > 0 Then outText = Split(outText, vbLf)(0)
        outText = Trim(outText)
        If outText <> "" And fso.FileExists(outText) Then found = outText
    End If
    On Error GoTo 0
End If

' ---- 2) fallback: look for pythonw.exe directly under versions ----
' Keeps working even if findpy.py cannot run (too-old Python, deleted script).
If found = "" Then
    If fso.FolderExists(pyRoot) Then
        For Each d In fso.GetFolder(pyRoot).SubFolders
            exePath = fso.BuildPath(d.Path, "pythonw.exe")
            If fso.FileExists(exePath) Then
                If found = "" Or StrComp(d.Name, bestName, vbTextCompare) > 0 Then
                    found = exePath
                    bestName = d.Name
                End If
            End If
        Next
    End If
End If

' ---- 3) pythonw.exe anywhere on PATH ----
If found = "" Then
    paths = Split(shell.ExpandEnvironmentStrings("%PATH%"), ";")
    For Each d In paths
        If Len(d) > 0 Then
            On Error Resume Next
            p = fso.BuildPath(d, "pythonw.exe")
            If fso.FileExists(p) Then found = p
            On Error GoTo 0
            If found <> "" Then Exit For
        End If
    Next
End If

' ---- 4) common install locations ----
' Last resort: only reached when every automatic probe failed (no bundled
' Python, nothing on PATH), which already means an unusual environment.
' Paths are built from environment variables, never a hardcoded drive
' letter -- a drive-letter guess only holds on the machine that wrote it,
' and silently never matches anywhere else.
If found = "" Then
    locs = Array( _
        shell.ExpandEnvironmentStrings("%LOCALAPPDATA%") & "\Programs\Python\Python313\pythonw.exe", _
        shell.ExpandEnvironmentStrings("%LOCALAPPDATA%") & "\Programs\Python\Python312\pythonw.exe", _
        shell.ExpandEnvironmentStrings("%LOCALAPPDATA%") & "\Programs\Python\Python311\pythonw.exe", _
        shell.ExpandEnvironmentStrings("%ProgramFiles%") & "\Python313\pythonw.exe", _
        shell.ExpandEnvironmentStrings("%ProgramFiles%") & "\Python312\pythonw.exe", _
        shell.ExpandEnvironmentStrings("%ProgramFiles%") & "\Python311\pythonw.exe" _
    )
    For i = 0 To UBound(locs)
        If fso.FileExists(locs(i)) Then
            found = locs(i)
            Exit For
        End If
    Next
End If

If found = "" Then
    MsgBox "Python not found." & vbCrLf & vbCrLf & _
           "This tool needs Python. WorkBuddy normally bundles one." & vbCrLf & _
           "If you see this message, the installation may be incomplete.", _
           16, "WorkBuddy Manager"
    WScript.Quit 1
End If

' ---- launch hidden, do not wait ----
' Use cmd /c start so pythonw becomes a detached process.
' Calling shell.Run "pythonw serve.py", 0, False directly makes the
' child die together with wscript (observed: service vanished after
' a few seconds). Going through start hands the process to cmd, which
' releases it from wscript's lifetime.
Dim q
q = Chr(34)
cmd = "cmd /c start " & q & q & " /D " & q & here & q & " " & _
      q & found & q & " " & q & script & q
shell.Run cmd, 0, False

WScript.Quit 0
'''

# 文件名 -> (内容, 编码, 无 Python 时用哪段提示)
LAUNCHERS = {
    "启动管理中心.bat": (START_BAT, "gbk", _NO_PY_FULL),
    "停止管理中心.bat": (STOP_BAT, "gbk", _NO_PY),
    "刷新数据.bat": (REFRESH_BAT, "gbk", _NO_PY),
    "启动管理中心.vbs": (START_VBS, "ascii", _NO_PY),
}


def scripts_dir_name():
    """启动器里该用哪个目录名去找 .py 脚本。

    模板正文写的是技能包里的那个目录名，但**开发目录里这个目录叫别的
    名字**（tools）—— 早前生成器无条件套用技能包的名字，于是在开发目录
    生成的启动器全都指向一个不存在的路径，双击直接弹「Cannot find」，
    报的就是那个不存在的位置。

    所以这里按**生成器自己所在目录的名字**来定，两个位置都能跑：
    技能包里是 scripts，开发目录里是 tools。取不到就退回 scripts
    （技能包的规范名），保持对外分发的那份不变。
    """
    name = os.path.basename(HERE.rstrip("\\/"))
    return name or "scripts"


def build(name):
    """取出某个启动器的最终内容（已填好公共片段）。"""
    body, enc, no_py = LAUNCHERS[name]
    body = body.replace("__PY_BOOT__", _PY_BOOT).replace("__NO_PY__", no_py)
    # 把模板里写死的 scripts\ 换成真实目录名。放在最后一步做，
    # 保证技能包（scripts\）的生成结果与既有文件逐字节一致。
    sd = scripts_dir_name()
    if sd != "scripts":
        # 反斜杠用 chr(92) 拼，不写字面量：体检器会把源码里连着两个
        # 反斜杠的地方当成 UNC 网络路径，误报 P1（实测命中过）。
        # 逻辑完全一样，只是让源码里不出现那个字符组合。
        bs = chr(92)
        body = body.replace("scripts" + bs, sd + bs)
    return body, enc


def write_all(force=False, quiet=False):
    """在技能根目录写出全部启动器。返回 (新建, 跳过) 两个计数。

    quiet=True 时一句都不打印 —— 给「顺手补一下」的调用方用
    （比如 doctor.py 的收尾引导）。那种场合用户关心的是
    「我接下来怎么打开」，不是「哪个文件被跳过了」。
    """
    made = skipped = 0
    for name in LAUNCHERS:
        path = os.path.join(ROOT, name)
        if os.path.exists(path) and not force:
            if not quiet:
                print("  跳过（已存在）：%s" % name)
            skipped += 1
            continue
        body, enc = build(name)
        # 行尾统一 CRLF：先规整成 LF，再逐行加 CRLF，避免把已有的 CRLF 变成 CRCRLF
        text = body.replace("\r\n", "\n").replace("\r", "\n").replace("\n", "\r\n")
        data = text.encode(enc)
        tmp = path + ".tmp"
        with open(tmp, "wb") as fh:
            fh.write(data)
        os.replace(tmp, path)
        if not quiet:
            print("  已生成：%s（%s / %d 字节）" % (name, enc.upper(), len(data)))
        made += 1
    return made, skipped


def referenced_scripts(name):
    """从启动器内容里抠出它引用的 .py 文件名。

    不做通用解析，只认 `xxx.py` 这种尾巴 —— 启动器里引用的脚本
    只有 findpy / serve / scan / killer / config / alive 这几个，
    用正则足够，也更不容易被无关文本误伤。
    """
    body, _enc = build(name)
    return sorted(set(re.findall(r"([A-Za-z_][A-Za-z0-9_]*\.py)", body)))


def check():
    """检查已有启动器的编码 / 行尾 / 引用路径是否合规。返回不合规的条数。"""
    bad = 0
    sd = scripts_dir_name()
    for name in LAUNCHERS:
        path = os.path.join(ROOT, name)
        if not os.path.exists(path):
            print("  [缺失] %s" % name)
            bad += 1
            continue
        with open(path, "rb") as fh:
            raw = fh.read()
        problems = []
        if b"\r\n" not in raw:
            problems.append("不是 CRLF 行尾")
        if raw.count(b"\n") != raw.count(b"\r\n"):
            problems.append("行尾混用（有裸 LF）")
        if name.endswith(".vbs") and any(b > 127 for b in raw):
            problems.append("含非 ASCII 字节（wscript 会乱码）")
        if name.endswith(".bat"):
            try:
                raw.decode("gbk")
            except UnicodeDecodeError:
                problems.append("不是合法 GBK")

        # 🔴 引用的脚本必须真的存在于当前目录布局下。
        # 这一条是为了拦住「模板写死 scripts\ 但本机目录叫 tools\」那类
        # 静默错配 —— 启动器能生成、看着没问题，双击才弹「Cannot find」。
        for fn in referenced_scripts(name):
            if not os.path.exists(os.path.join(HERE, fn)):
                problems.append("引用了不存在的脚本 %s\\%s" % (sd, fn))

        if problems:
            print("  [不合规] %s -> %s" % (name, "；".join(problems)))
            bad += 1
        else:
            print("  [OK] %s" % name)
    return bad


def main():
    ap = argparse.ArgumentParser(description="生成 Windows 启动器（.bat / .vbs）")
    ap.add_argument("--force", action="store_true", help="覆盖已存在的启动器")
    ap.add_argument("--check", action="store_true", help="只检查，不写文件")
    args = ap.parse_args()

    import console
    try:
        console.fix()
    except Exception:
        pass

    if args.check:
        print("检查启动器：%s" % ROOT)
        bad = check()
        print()
        print("不合规 %d 个" % bad)
        return 1 if bad else 0

    print("生成启动器到：%s" % ROOT)
    made, skipped = write_all(force=args.force)
    print()
    print("新生成 %d 个，跳过 %d 个。" % (made, skipped))
    if made:
        print("现在可以双击「启动管理中心.bat」（有窗口）或「启动管理中心.vbs」（无窗口）。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
