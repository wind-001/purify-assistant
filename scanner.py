"""扫描"用户真正打开的应用",过滤掉一切后台杂音。

四层过滤,层层递进:
1. 只保留"当前登录用户"名下的进程 —— 系统进程都挂在 SYSTEM 等账户下;
2. 只保留"有可见窗口"的进程 —— 这正是任务管理器里"应用"的定义:
   你打开的微信/B站/浏览器都有窗口,而更新器、后台帮手、托盘进程没有;
   这些没窗口的家伙在关机时本来就会被直接结束,不需要出现在清理列表里,
   真要杀主进程时,它们的子进程会被一并带走;
3. 可执行文件位于 C:\\Windows 目录里的一律排除 —— 那是系统的地盘;
4. 剩下的再对照一份"系统组件黑名单"(explorer、输入法、搜索框这类)。
"""

import getpass
import os
import ctypes
from ctypes import wintypes
from datetime import datetime

import psutil

# 跑在用户会话里、但属于 Windows 自带组件的进程(不是用户手动开的软件)
SYSTEM_PROCESS_NAMES = {
    "explorer.exe", "conhost.exe", "openconsole.exe", "runtimebroker.exe",
    "dllhost.exe", "sihost.exe", "taskhostw.exe", "shellexperiencehost.exe",
    "startmenuexperiencehost.exe", "searchhost.exe", "searchapp.exe",
    "textinputhost.exe", "lockapp.exe", "applicationframehost.exe",
    "ctfmon.exe", "smartscreen.exe", "widgets.exe", "widgetservice.exe",
    "phoneexperiencehost.exe", "msedgewebview2.exe", "svchost.exe",
    "csrss.exe", "smss.exe", "services.exe", "lsass.exe", "wininit.exe",
    "winlogon.exe", "dwm.exe", "fontdrvhost.exe", "audiodg.exe",
    "spoolsv.exe", "searchindexer.exe", "wmiprvse.exe", "msmpeng.exe",
    "securityhealthservice.exe", "securityhealthsystray.exe", "wudfhost.exe",
}


_desc_cache = {}


def _file_description(exe_path):
    """读 exe 版本信息里的"文件说明",把 cloudmusic.exe 翻译成"网易云音乐"。"""
    cached = _desc_cache.get(exe_path)
    if cached is not None:
        return cached
    desc = ""
    try:
        ver = ctypes.windll.version
        size = ver.GetFileVersionInfoSizeW(exe_path, None)
        if size:
            data = ctypes.create_string_buffer(size)
            if ver.GetFileVersionInfoW(exe_path, 0, size, data):
                sub = ctypes.c_void_p()
                sub_len = wintypes.UINT(0)
                lang_id, codepage = 0x0409, 0x04B0  # 兜底:英语/Unicode
                if ver.VerQueryValueW(data, "\\VarFileInfo\\Translation",
                                      ctypes.byref(sub), ctypes.byref(sub_len)) \
                        and sub_len.value >= 4:
                    pair = ctypes.cast(sub, ctypes.POINTER(wintypes.WORD * 2)).contents
                    lang_id, codepage = pair[0], pair[1]
                for probe in (f"\\StringFileInfo\\{lang_id:04x}{codepage:04x}\\FileDescription",
                              "\\StringFileInfo\\040904b0\\FileDescription",
                              "\\StringFileInfo\\080404b0\\FileDescription"):
                    buf = ctypes.c_void_p()
                    blen = wintypes.UINT(0)
                    if ver.VerQueryValueW(data, probe, ctypes.byref(buf),
                                          ctypes.byref(blen)) and blen.value:
                        desc = ctypes.cast(buf, ctypes.c_wchar_p).value
                        break
    except Exception:
        desc = ""
    _desc_cache[exe_path] = desc
    return desc


def _visible_window_pids():
    """枚举顶层窗口,返回"有看得见窗口"的进程 PID 集合(约 1~5 毫秒)。"""
    pids = set()
    user32 = ctypes.windll.user32
    dwmapi = ctypes.windll.dwmapi
    GWL_EXSTYLE = -20
    WS_EX_TOOLWINDOW = 0x80
    DWMWA_CLOAKED = 14

    def _cb(hwnd, lparam):
        try:
            if not user32.IsWindowVisible(hwnd):
                return True
            if user32.GetWindowLongW(hwnd, GWL_EXSTYLE) & WS_EX_TOOLWINDOW:
                return True  # 各种小工具条、悬浮提示,不算应用
            cloaked = wintypes.DWORD(0)
            if dwmapi.DwmGetWindowAttribute(hwnd, DWMWA_CLOAKED,
                                            ctypes.byref(cloaked),
                                            ctypes.sizeof(cloaked)) == 0 \
                    and cloaked.value:
                return True  # 被挂起"隐形"的 UWP 窗口
            pid = wintypes.DWORD(0)
            user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
            if pid.value:
                pids.add(pid.value)
        except Exception:
            pass
        return True  # 继续枚举下一个窗口

    enum_proc = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
    user32.EnumWindows.argtypes = [enum_proc, wintypes.LPARAM]
    user32.EnumWindows.restype = wintypes.BOOL
    cb = enum_proc(_cb)  # 引用要活着,不能让回调被垃圾回收
    user32.EnumWindows(cb, 0)
    return pids


def _current_username():
    try:
        return psutil.Process().username().rsplit("\\", 1)[-1]
    except psutil.Error:
        return getpass.getuser()


def scan_user_apps():
    """返回看板要展示的应用列表,按启动时间从新到旧排。"""
    me = _current_username().lower()
    windir = os.environ.get("WINDIR", r"C:\Windows").rstrip("\\").lower() + "\\"
    window_pids = _visible_window_pids()

    apps = []
    for proc in psutil.process_iter(["pid", "name", "username", "exe",
                                     "memory_info", "create_time"]):
        info = proc.info
        pid = info["pid"]
        name = info["name"] or ""
        if pid <= 4 or pid == os.getpid() or not name:
            continue

        # 第 1 层:只看当前用户自己的进程
        user = (info["username"] or "").rsplit("\\", 1)[-1].lower()
        if user != me:
            continue

        # 第 2 层:没有可见窗口的进程不是你"打开"的应用
        if pid not in window_pids:
            continue

        # 第 3 层:住在系统目录里的不算用户软件
        exe = info["exe"] or ""
        if exe and exe.lower().startswith(windir):
            continue

        # 第 4 层:黑名单命中直接排除
        if name.lower() in SYSTEM_PROCESS_NAMES:
            continue

        mem = info["memory_info"]
        display = (_file_description(exe) if exe else "") or name
        apps.append({
            "pid": pid,
            "name": name,
            "display": display,
            "memory_mb": mem.rss / 1024 / 1024 if mem else 0.0,
            "start_time": datetime.fromtimestamp(info["create_time"]).strftime("%m-%d %H:%M")
                          if info["create_time"] else "?",
            "path": exe or "未知(权限不足)",
            "_ct": info["create_time"] or 0,
        })

    apps.sort(key=lambda a: a["_ct"], reverse=True)
    for a in apps:
        del a["_ct"]
    return apps


def kill_pids(pids):
    """结束一组进程:先杀子进程再杀主进程,防止微信这类应用原地复活。

    返回 (结束成功的进程名列表, 失败列表[(进程名, 原因)])。
    """
    killed, failed = [], []
    for pid in pids:
        name = f"PID {pid}"
        try:
            proc = psutil.Process(pid)
            name = proc.name()
            children = proc.children(recursive=True)
            for child in children:
                try:
                    child.kill()
                except psutil.Error:
                    pass
            _, alive = psutil.wait_procs(children, timeout=3)
            for child in alive:
                try:
                    child.kill()
                except psutil.Error:
                    pass
            proc.kill()
            proc.wait(timeout=3)
            killed.append(name)
        except psutil.NoSuchProcess:
            killed.append(name)  # 它已经自己退出了,也算达成目的
        except psutil.AccessDenied:
            failed.append((name, "权限不足"))
        except psutil.Error as e:
            failed.append((name, str(e)))
    return killed, failed
