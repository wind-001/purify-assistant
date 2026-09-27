"""Purify 入口:启动看板窗口。"""

import sys
import tkinter as tk
from tkinter import messagebox

MUTEX_NAME = "Purify_SingleInstance_Mutex"
_mutex_handle = None  # 抓住互斥体句柄,防止被垃圾回收导致锁失效


def _already_running():
    """用 Windows 命名互斥体判断是否已有实例在跑。

    CreateMutexW 第二次创建同名互斥体时会得到 ERROR_ALREADY_EXISTS(183),
    内核会在进程退出时自动释放,程序崩溃也不会留死锁。
    """
    global _mutex_handle
    try:
        import ctypes

        _mutex_handle = ctypes.windll.kernel32.CreateMutexW(None, True, MUTEX_NAME)
        return ctypes.windll.kernel32.GetLastError() == 183
    except Exception:
        return False  # 判断不了就放行,别把程序锁死在外面


def main():
    if _already_running():
        root = tk.Tk()
        root.withdraw()
        messagebox.showinfo(
            "Purify", "主人您好,您的进程管理器已经启动了哦! 天天开心!!!")
        sys.exit(0)

    try:
        from ctypes import windll
        windll.shcore.SetProcessDpiAwareness(1)  # 高分屏下不模糊
    except Exception:
        pass

    try:
        from ui import PurifyApp
    except ModuleNotFoundError as e:
        # 依赖没装时给人话提示,而不是一串英文报错
        root = tk.Tk()
        root.withdraw()
        messagebox.showerror(
            "Purify 启动失败",
            f"缺少依赖:{e.name}\n\n请先执行:\npip install -r requirements.txt")
        sys.exit(1)

    app = PurifyApp()
    try:
        app.mainloop()
    except tk.TclError:
        # 关窗口的一瞬间窗口已销毁,收尾回调会抛这个,无害
        pass


if __name__ == "__main__":
    main()
