"""悬浮球 + 弹出面板(仿 360 加速球,iOS 简洁风)。

视觉遵循 ui-new 设计技能的规范:苹果式灰色板(#007AFF 主蓝 / #F2F2F7 表面)、
8pt 网格间距、最多三级字号、克制用色、行 hover 反馈。
面板和悬浮球都是"透明抠图 + 画布绘制",所以面板是真正的圆角卡片,
球有内存进度环和呼吸动效。

排版防重叠:文字按"磅"缩放而坐标按"像素"固定,高 DPI 下容易叠字,
所以行高和球内文字位置全部按运行时实测的字体像素高度计算。

性能设计(卡顿的三处病根,这里对应改掉):
1. 进程扫描挪到后台线程,主线程只收结果 —— 界面不再被扫描卡住;
2. 列表不用"每行一堆控件",而是直接在画布上画;PID 结构没变时只原地改数字;
3. 面板收起时零渲染开销,悬浮球只改一个百分比数字。
"""

import queue
import threading
import time
import tkinter as tk
import tkinter.font as tkfont

from datetime import datetime

import psutil

import autostart
import ballart
import scanner

SCAN_INTERVAL = 3.0   # 后台扫描间隔(秒)
POLL_MS = 150         # 主线程收取后台结果的节奏(毫秒)
MIN_BALL = 100        # 悬浮球直径下限(像素);实际尺寸按实测字高自动放大
PANEL_W, PANEL_H = 470, 560
CORNER_R = 18         # 面板圆角半径(像素)

# ---- 色彩 token(苹果式灰,来自 ui-new 技能推荐色板)----
BG = "#ffffff"        # 表面
TRACK = "#E5E5EA"     # 进度条轨道 / 分隔
ROW_HOVER = "#F7F7FA"  # 行悬停
ROW_SEL = "#E8F1FF"   # 选中行
BORDER = "#E5E5EA"    # 描边
FG = "#1C1C1E"        # 主文字
FG_DIM = "#8E8E93"    # 次要文字
ACCENT = "#007AFF"    # 主题蓝
DANGER = "#FF3B30"    # 危险操作
DANGER_PRESS = "#D70015"
OK_GREEN = "#34C759"
WARN = "#FF9500"
BOX_BORDER = "#C7C7CC"
SEP = "#F2F2F7"       # 行分隔线
TRANSPARENT = "#010203"  # 透明抠图色(面板圆角外、球外围)

FONT = "Microsoft YaHei UI"


def _round_rect(cv, x1, y1, x2, y2, r, **kw):
    """画圆角矩形:smooth 多边形,12 个锚点。"""
    pts = [x1 + r, y1, x2 - r, y1, x2, y1, x2, y1 + r,
           x2, y2 - r, x2, y2, x2 - r, y2, x1 + r, y2,
           x1, y2, x1, y2 - r, x1, y1 + r, x1, y1]
    return cv.create_polygon(pts, smooth=True, **kw)


def _short(text, limit=40):
    return text if len(text) <= limit else text[: limit // 2] + "…" + text[-(limit // 2):]


def _mem_text(mb):
    return f"{mb:.0f} MB" if mb >= 1 else "<1 MB"


class PanelWindow(tk.Toplevel):
    """点开悬浮球后弹出的看板:圆角卡片、置顶、点外面自动收起。"""

    def __init__(self, app):
        super().__init__(app)
        self._app = app
        self.overrideredirect(True)
        self.attributes("-topmost", True)
        try:
            self.attributes("-transparentcolor", TRANSPARENT)
        except tk.TclError:
            pass
        self.configure(bg=TRANSPARENT, highlightthickness=0)
        self.geometry(f"{PANEL_W}x{PANEL_H}")

        # 圆角白卡片底板:铺满窗口垫在最底层,四角透出桌面
        self._base = tk.Canvas(self, bg=TRANSPARENT, highlightthickness=0, bd=0)
        self._base.place(x=0, y=0, relwidth=1, relheight=1)
        self._base.bind("<Configure>", self._draw_base)

        head = tk.Frame(self, bg=BG)
        head.pack(fill="x", padx=20, pady=(18, 0))
        tk.Label(head, text="应用看板", bg=BG, fg=FG,
                 font=(FONT, 14, "bold")).pack(side="left")
        self.count_lbl = tk.Label(head, text="", bg=BG, fg=FG_DIM, font=(FONT, 10))
        self.count_lbl.pack(side="right", padx=12)
        tk.Button(head, text="✕", command=app.hide_panel, bg=BG, fg=FG_DIM,
                  activebackground=BG, activeforeground=FG, relief="flat",
                  bd=0, font=(FONT, 12), cursor="hand2", padx=6).pack(side="right")

        # 内存占用进度条
        track = tk.Frame(self, bg=TRACK, height=6)
        track.pack(fill="x", padx=20, pady=(12, 0))
        self.bar = tk.Frame(track, bg=ACCENT, height=6)
        self.bar.place(x=0, y=0, relwidth=0, relheight=1)

        ctrl = tk.Frame(self, bg=BG)
        ctrl.pack(fill="x", padx=20, pady=(12, 4))
        self.select_all_btn = tk.Button(ctrl, text="全选",
                                        command=app._toggle_select_all,
                                        bg=BG, fg=ACCENT, activebackground=BG,
                                        activeforeground=ACCENT, relief="flat",
                                        bd=0, font=(FONT, 9), cursor="hand2",
                                        padx=4)
        self.select_all_btn.pack(side="left")
        tk.Checkbutton(ctrl, text="开机自启", variable=app._autostart_var,
                       command=app._toggle_autostart, bg=BG, fg=FG_DIM,
                       activebackground=BG, activeforeground=FG,
                       selectcolor="#DDE8F7", highlightthickness=0, bd=0,
                       font=(FONT, 9)).pack(side="right")
        self.search_var = tk.StringVar()
        self.search_var.trace_add("write",
                                  lambda *_: app.set_filter(self.search_var.get()))
        tk.Entry(ctrl, textvariable=self.search_var, bg="#EFEFF4", fg=FG,
                 insertbackground=FG, relief="flat", width=14,
                 font=(FONT, 9)).pack(side="right", padx=8)

        mid = tk.Frame(self, bg=BG)
        mid.pack(fill="both", expand=True, padx=14)
        self.canvas = tk.Canvas(mid, bg=BG, highlightthickness=0, bd=0)
        self.canvas.pack(side="left", fill="both", expand=True)
        self.canvas.bind("<Enter>", lambda e: self.bind_all("<MouseWheel>", self._wheel))
        self.canvas.bind("<Leave>", lambda e: self.unbind_all("<MouseWheel>"))

        foot = tk.Frame(self, bg=BG)
        foot.pack(fill="x", padx=20, pady=(8, 16))
        self.status = tk.Label(foot, text="", bg=BG, fg=FG_DIM, anchor="w",
                               font=(FONT, 8))
        self.status.pack(side="left", fill="x", expand=True)
        self.kill_btn = tk.Button(foot, text="结束所选 (0)", command=app._kill_clicked,
                                  bg=DANGER, fg="white", activebackground=DANGER_PRESS,
                                  activeforeground="white", relief="flat", bd=0,
                                  font=(FONT, 10, "bold"), cursor="hand2",
                                  padx=18, pady=6)
        self.kill_btn.pack(side="right")

        self.bind("<FocusOut>", self._on_focus_out)
        self.bind("<Escape>", lambda e: app.hide_panel())

        # 贴着悬浮球弹出,屏幕边缘自动收进来
        bx, by = app.winfo_x(), app.winfo_y()
        sw, sh = app.winfo_screenwidth(), app.winfo_screenheight()
        x = bx + app._ball_size + 10
        if x + PANEL_W > sw - 8:
            x = bx - PANEL_W - 10
        y = max(8, min(by - 30, sh - PANEL_H - 8))
        x = max(8, min(x, sw - PANEL_W - 8))
        self.geometry(f"{PANEL_W}x{PANEL_H}+{x}+{y}")

    def _draw_base(self, event=None):
        """圆角白卡片:窗口四角是透明色,视觉上就是圆角面板。"""
        cv = self._base
        cv.delete("all")
        w = cv.winfo_width()
        h = cv.winfo_height()
        if w < 2 or h < 2:
            return
        _round_rect(cv, 1, 1, w - 2, h - 2, CORNER_R,
                    fill=BG, outline=BORDER, width=1)

    def _wheel(self, e):
        # 滚动到底/顶时把事件还给系统,避免面板"吞"滚轮
        self.canvas.yview_scroll(-1 if e.delta > 0 else 1, "units")

    def _on_focus_out(self, event):
        if not self._app._auto_hide_enabled:
            return
        app = self._app
        if app._panel_hide_job is not None:
            app.after_cancel(app._panel_hide_job)
        app._panel_hide_job = app.after(250, app.hide_panel)


class PurifyApp(tk.Tk):
    """悬浮球本体:常驻桌面、可拖动,左键弹出/收起面板,右键菜单。"""

    def __init__(self):
        super().__init__()
        self.overrideredirect(True)
        self.attributes("-topmost", True)
        try:
            self.attributes("-transparentcolor", TRANSPARENT)
        except tk.TclError:
            pass
        self.configure(bg=TRANSPARENT, highlightthickness=0)
        # 窗口尺寸交给画布自适应收缩,位置在 _place_ball_bottom_right 里设

        self._apps = []
        self._selected = set()
        self._filter_text = ""
        self._order = []   # 画布上当前的行顺序(pid),用来判断结构是否变化
        self._items = {}   # pid -> 行内画布条目(底色/勾选圈,增量改色用)
        self._panel = None
        self._panel_hide_job = None
        self._confirm_job = None
        self._poll_job = None
        self._auto_hide_enabled = True
        self._autostart_var = tk.BooleanVar(value=autostart.is_enabled())
        self._queue = queue.Queue()
        self._wake = threading.Event()
        self._stop = threading.Event()
        self._drag = None
        self._last_scan_time = datetime.now()
        self._total_ram_mb = psutil.virtual_memory().total / 1048576

        # 运行时实测字体像素高度,排版全靠它,任何 DPI 下都不叠字
        f_name = tkfont.Font(family=FONT, size=10, weight="bold")
        f_sub = tkfont.Font(family=FONT, size=8)
        self._name_h = f_name.metrics("linespace")
        self._sub_h = f_sub.metrics("linespace")
        self._row_h = 8 + self._name_h + 4 + self._sub_h + 8

        # 球的字体与尺寸:球径 = 内容需要的高度,高 DPI 下自动变大不挤压
        self._f_title = tkfont.Font(family=FONT, size=9, weight="bold")
        self._f_pct = tkfont.Font(family="Segoe UI", size=15, weight="bold")
        self._f_mem = tkfont.Font(family=FONT, size=7)
        self._title_h = self._f_title.metrics("linespace")
        self._pct_h = self._f_pct.metrics("linespace")
        self._mem_h = self._f_mem.metrics("linespace")
        pill_h = self._mem_h + 8
        self._ball_size = max(MIN_BALL, round(
            12 + self._title_h + 3 + self._pct_h + 4
            + self._mem_h + 6 + pill_h + 6))

        # 球的灵动变换状态:t∈[0,1],0=静止立绘+数据盘,1=悬停脸部特写
        self._ball_t = 0.0
        self._ball_target = 0.0
        self._panel_slide = 0.0
        self._trans_job = None
        self._pulse_job = None
        self._pulse_on = False

        self._build_ball()
        self._place_ball_bottom_right()
        self._bind_ball()
        self._start_worker()
        self.after(POLL_MS, self._poll)
        self._pulse()
        self.protocol("WM_DELETE_WINDOW", self.quit_app)

    # ---------- 悬浮球 ----------

    def _build_ball(self):
        size = self._ball_size
        self._canvas = tk.Canvas(self, width=size, height=size,
                                 bg=TRANSPARENT, highlightthickness=0, bd=0,
                                 cursor="hand2")
        self._canvas.pack()

        # 立绘帧 + 毛玻璃盘(资源缺失时走纯色球兜底)
        self._art_frames, self._glass_frames = ballart.build(size)
        self._art_id = None
        self._glass_id = None
        c = size / 2
        if self._art_frames:
            self._art_id = self._canvas.create_image(c, c,
                                                     image=self._art_frames[0])
            self._glass_id = self._canvas.create_image(c, c,
                                                       image=self._glass_frames[0])
        else:
            pad = 4
            self._canvas.create_oval(pad, pad, size - pad, size - pad,
                                     fill="#1A1A2E", outline="#2B2B4A", width=2)

        # 玻璃盘上的数据文字:自上而下排,球径就是按这段高度算出来的,必不遮挡
        th, ph, mh = self._title_h, self._pct_h, self._mem_h
        y_title = 12 + th / 2
        y_pct = y_title + th / 2 + 3 + ph / 2
        y_mem = y_pct + ph / 2 + 4 + mh / 2
        pill_h = mh + 8
        y_pill = y_mem + mh / 2 + 6 + pill_h / 2

        self._title_id = self._canvas.create_text(c, y_title, text="加速球",
                                                  fill="#3A3A4A",
                                                  font=self._f_title)
        self._pct_id = self._canvas.create_text(c, y_pct, text="--",
                                                fill="#1C1C1E", font=self._f_pct)
        self._mem_id = self._canvas.create_text(c, y_mem, text="扫描中",
                                                fill="#4A4A5A", font=self._f_mem)
        label = "天天开心!!!"
        pw = min(self._f_mem.measure(label) + 16, size * 0.86)
        self._pill_id = _round_rect(self._canvas, c - pw / 2,
                                    y_pill - pill_h / 2, c + pw / 2,
                                    y_pill + pill_h / 2, pill_h / 2,
                                    fill="#FFA940", outline="#F08C00")
        self._pill_text_id = self._canvas.create_text(c, y_pill, text=label,
                                                      fill="white",
                                                      font=self._f_mem)
        self._panel_ids = [self._title_id, self._pct_id, self._mem_id,
                           self._pill_id, self._pill_text_id]

    def _place_ball_bottom_right(self):
        sw = self.winfo_screenwidth()
        sh = self.winfo_screenheight()
        size = self._ball_size
        self.geometry(f"+{sw - size - 24}+{sh - size - 120}")

    def _bind_ball(self):
        self._canvas.bind("<ButtonPress-1>", self._on_press)
        self._canvas.bind("<B1-Motion>", self._on_drag)
        self._canvas.bind("<ButtonRelease-1>", self._on_release)
        self._canvas.bind("<Enter>", lambda e: self._ball_set_target(1.0))
        self._canvas.bind("<Leave>", lambda e: self._ball_set_target(0.0))
        self.bind("<Button-3>", self._show_menu)

    # ---------- 球的灵动变换(悬停推近脸部,玻璃盘淡出、文字下滑) ----------

    def _ball_set_target(self, target):
        self._ball_target = target
        if self._trans_job is None:
            self._ball_tick()

    def _ball_tick(self):
        step = 0.12
        t = self._ball_t
        t = t + step if self._ball_target > t else t - step
        t = min(1.0, max(0.0, t))
        self._ball_t = t
        self._apply_ball_frame(t)
        if t != self._ball_target:
            self._trans_job = self.after(28, self._ball_tick)
        else:
            self._trans_job = None

    def _apply_ball_frame(self, t):
        e = 1 - (1 - t) ** 3  # easeOutCubic:先快后慢,收尾柔和
        if self._art_frames:
            idx = round(e * (len(self._art_frames) - 1))
            self._canvas.itemconfigure(self._art_id, image=self._art_frames[idx])
        if self._glass_frames:
            gi = min(len(self._glass_frames) - 1,
                     round(e * (len(self._glass_frames) - 1)))
            if e > 0.92:
                self._canvas.itemconfigure(self._glass_id, state="hidden")
            else:
                self._canvas.itemconfigure(self._glass_id, state="normal",
                                           image=self._glass_frames[gi])
        # 数据盘整体向下滑出,比缩放稍慢一拍,层次感更强
        slide = e * 38
        dy = slide - self._panel_slide
        self._panel_slide = slide
        if dy:
            for iid in self._panel_ids:
                self._canvas.move(iid, 0, dy)
        st = "hidden" if e > 0.75 else "normal"
        for iid in self._panel_ids:
            self._canvas.itemconfigure(iid, state=st)

    def _pulse(self):
        """静止时的呼吸:立绘在头两帧之间轻推,球是活的。"""
        if self._ball_t == 0.0 and self._art_frames:
            self._pulse_on = not self._pulse_on
            self._canvas.itemconfigure(self._art_id,
                                       image=self._art_frames[1 if self._pulse_on else 0])
        self._pulse_job = self.after(900, self._pulse)

    def _on_press(self, e):
        self._drag = (e.x_root - self.winfo_x(), e.y_root - self.winfo_y(),
                      e.x_root, e.y_root)

    def _on_drag(self, e):
        if self._drag is None:
            return
        self.geometry(f"+{e.x_root - self._drag[0]}+{e.y_root - self._drag[1]}")

    def _on_release(self, e):
        if self._drag is None:
            return
        moved = abs(e.x_root - self._drag[2]) + abs(e.y_root - self._drag[3])
        self._drag = None
        if moved < 6:  # 基本没动 → 算点击,弹出/收起面板
            self._toggle_panel()

    def _show_menu(self, event):
        menu = tk.Menu(self, tearoff=0, bg="#ffffff", fg=FG,
                       activebackground=ROW_HOVER, activeforeground=FG,
                       bd=0, font=(FONT, 9))
        menu.add_command(label="打开 / 收起面板", command=self._toggle_panel)
        menu.add_checkbutton(label="开机自启", variable=self._autostart_var,
                             command=self._toggle_autostart)
        menu.add_separator()
        menu.add_command(label="退出 Purify", command=self.quit_app)
        menu.tk_popup(event.x_root, event.y_root)

    # ---------- 面板显隐 ----------

    def _toggle_panel(self):
        if self._panel is not None:
            self.hide_panel()
        else:
            self.show_panel()

    def show_panel(self):
        if self._panel is not None:
            return
        self._panel = PanelWindow(self)
        self._panel.canvas.tag_bind("row", "<Button-1>", self._on_row_click)
        self._panel.canvas.tag_bind("row", "<Enter>", self._on_row_enter)
        self._panel.canvas.tag_bind("row", "<Leave>", self._on_row_leave)
        self._panel.canvas.bind("<Configure>", self._on_canvas_resize)
        self._update_panel_chrome()
        self._update_list()
        self._panel.focus_set()

    def hide_panel(self, event=None):
        if self._panel_hide_job is not None:
            self.after_cancel(self._panel_hide_job)
            self._panel_hide_job = None
        if self._panel is not None:
            self._panel.destroy()
            self._panel = None
            self._order = []
            self._items = {}
            self._reset_kill_btn()

    def set_filter(self, text):
        self._filter_text = text.strip().lower()
        self._update_list()

    def _toggle_select_all(self):
        """一键全选/取消全选:只作用于当前过滤后可见的应用。"""
        shown = [a["pid"] for a in self._apps if self._match(a)]
        if shown and all(p in self._selected for p in shown):
            self._selected.difference_update(shown)
        else:
            self._selected.update(shown)
        if self._panel is not None:
            self._draw_rows([a for a in self._apps if self._match(a)])
        self._sync_kill_btn()

    # ---------- 画布列表(增量 + 整版重画) ----------

    def _match(self, app):
        f = self._filter_text
        if not f:
            return True
        return (f in (app.get("display") or "").lower()
                or f in app["name"].lower()
                or f in str(app["pid"]))

    def _update_list(self):
        """面板开着时刷新:PID 结构变了才整版重画,否则只原地改内存数字。"""
        if self._panel is None:
            return
        want = [a for a in self._apps if self._match(a)]
        if [a["pid"] for a in want] != self._order:
            self._draw_rows(want)
        else:
            for a in want:
                self._panel.canvas.itemconfigure(f"m{a['pid']}",
                                                 text=_mem_text(a["memory_mb"]))
        self._sync_kill_btn()

    def _draw_rows(self, want):
        canvas = self._panel.canvas
        canvas.delete("all")
        self._order = [a["pid"] for a in want]
        self._items = {}
        width = max(canvas.winfo_width(), PANEL_W - 40)
        rh = self._row_h
        name_cy = 8 + self._name_h / 2          # 行内:名字中心
        sub_cy = 8 + self._name_h + 4 + self._sub_h / 2  # 行内:副行中心
        for i, a in enumerate(want):
            pid = a["pid"]
            y0 = i * rh
            sel = pid in self._selected
            tags = ("row", f"p{pid}")

            bg = canvas.create_rectangle(1, y0, width - 1, y0 + rh,
                                         fill=ROW_SEL if sel else BG,
                                         outline="", tags=tags)
            # iOS 式圆圈勾选:空心圈 → 选中变蓝底白勾
            cx = 24
            circle = canvas.create_oval(cx - 8, y0 + rh / 2 - 8,
                                        cx + 8, y0 + rh / 2 + 8,
                                        outline="" if sel else BOX_BORDER,
                                        fill=ACCENT if sel else BG, width=1,
                                        tags=tags)
            chk = None
            if sel:
                chk = canvas.create_text(cx, y0 + rh / 2, text="✓", fill="white",
                                         font=(FONT, 9, "bold"), tags=tags)
            display = a.get("display") or a["name"]
            sub = a["name"] if display.lower() != a["name"].lower() else ""
            sub_text = f"{sub} · {_short(a['path'])}" if sub else _short(a["path"], 44)
            canvas.create_text(46, y0 + name_cy, text=display, anchor="w",
                               fill=FG, font=(FONT, 10, "bold"), tags=tags)
            canvas.create_text(46, y0 + sub_cy, text=sub_text, anchor="w",
                               fill=FG_DIM, font=(FONT, 8), tags=tags)
            canvas.create_text(width - 14, y0 + rh / 2,
                               text=_mem_text(a["memory_mb"]),
                               anchor="e", fill=FG, font=(FONT, 10),
                               tags=tags + (f"m{pid}",))
            # 行底细分隔线(iOS 列表 hairline,左侧与文字对齐)
            canvas.create_rectangle(46, y0 + rh - 1, width - 14, y0 + rh,
                                    fill=SEP, outline="", tags=("sep",))
            self._items[pid] = {"bg": bg, "circle": circle, "chk": chk, "cx": cx,
                                "cy": y0 + rh / 2}
        canvas.configure(scrollregion=(0, 0, 0, len(want) * rh))

    def _on_canvas_resize(self, event):
        # 首次显示/尺寸变化时重画,让右侧内存列对齐当前宽度
        if self._panel is not None:
            self._draw_rows([a for a in self._apps if self._match(a)])

    def _pid_from_event(self, event):
        for t in self._panel.canvas.gettags("current"):
            if t.startswith("p") and t[1:].isdigit():
                return int(t[1:])
        return None

    def _set_row_bg(self, pid, color):
        items = self._items.get(pid)
        if items is not None and pid not in self._selected:
            self._panel.canvas.itemconfigure(items["bg"], fill=color)

    def _on_row_enter(self, event):
        self._set_row_bg(self._pid_from_event(event), ROW_HOVER)

    def _on_row_leave(self, event):
        self._set_row_bg(self._pid_from_event(event), BG)

    def _on_row_click(self, event):
        canvas = self._panel.canvas
        pid = self._pid_from_event(event)
        if pid is None:
            return
        if pid in self._selected:
            self._selected.discard(pid)
        else:
            self._selected.add(pid)
        items = self._items.get(pid)
        if items is not None:
            if pid in self._selected:
                canvas.itemconfigure(items["bg"], fill=ROW_SEL)
                canvas.itemconfigure(items["circle"], fill=ACCENT, outline="")
                if items["chk"] is None:
                    items["chk"] = canvas.create_text(
                        items["cx"], items["cy"], text="✓", fill="white",
                        font=(FONT, 9, "bold"), tags=("row", f"p{pid}"))
            else:
                canvas.itemconfigure(items["bg"], fill=BG)
                canvas.itemconfigure(items["circle"], fill=BG, outline=BOX_BORDER)
                if items["chk"] is not None:
                    canvas.delete(items["chk"])
                    items["chk"] = None
        self._sync_kill_btn()

    def _sync_kill_btn(self):
        if self._panel is None or self._confirm_job is not None:
            return
        alive = {a["pid"] for a in self._apps}
        n = len([p for p in self._selected if p in alive])
        self._panel.kill_btn.config(text=f"结束所选 ({n})")
        shown = [a["pid"] for a in self._apps if self._match(a)]
        all_sel = bool(shown) and all(p in self._selected for p in shown)
        self._panel.select_all_btn.config(text="取消全选" if all_sel else "全选")

    # ---------- 结束进程(两段式确认,不用弹窗) ----------

    def _kill_clicked(self):
        alive = {a["pid"]: a["name"] for a in self._apps}
        pids = [p for p in self._selected if p in alive]
        if not pids:
            self._panel.status.config(text="先点行勾选要结束的应用", fg=WARN)
            return
        if self._confirm_job is None:  # 第一次点:进入确认状态,3 秒后自动复位
            self._panel.kill_btn.config(text=f"再点一次确认 ({len(pids)})",
                                        bg=DANGER_PRESS)
            self._confirm_job = self.after(3000, self._reset_kill_btn)
            return
        self.after_cancel(self._confirm_job)
        self._confirm_job = None
        self._panel.kill_btn.config(text="结束中…", state="disabled")
        threading.Thread(target=self._kill_worker, args=(pids,),
                         daemon=True).start()

    def _kill_worker(self, pids):
        killed, failed = scanner.kill_pids(pids)
        self._queue.put(("killed", killed, failed))
        self._wake.set()  # 杀完立刻补一轮扫描

    def _on_killed(self, killed, failed):
        self._selected.clear()
        self._reset_kill_btn()
        if self._panel is not None:
            # 选中清空了,把行上的勾选圈也同步刷掉
            self._draw_rows([a for a in self._apps if self._match(a)])
        if self._panel is None:
            return
        if failed:
            names = "、".join(n for n, _ in failed[:3])
            self._panel.status.config(
                text=f"{len(failed)} 个没杀掉({names}):权限不足,可右键 exe 用管理员运行",
                fg=WARN)
        else:
            self._panel.status.config(
                text=f"已结束 {len(killed)} 个 · {datetime.now():%H:%M:%S}",
                fg=OK_GREEN)

    def _reset_kill_btn(self):
        if self._confirm_job is not None:
            self.after_cancel(self._confirm_job)
            self._confirm_job = None
        self._sync_kill_btn()
        if self._panel is not None:
            self._panel.kill_btn.config(state="normal", bg=DANGER)

    def _toggle_autostart(self):
        try:
            if self._autostart_var.get():
                autostart.enable()
            else:
                autostart.disable()
        except OSError as e:
            self._autostart_var.set(not self._autostart_var.get())
            if self._panel is not None:
                self._panel.status.config(text=f"自启设置失败:{e}", fg=DANGER)

    # ---------- 后台扫描线程 + 主线程轮询 ----------

    def _start_worker(self):
        threading.Thread(target=self._scan_loop, daemon=True).start()

    def _scan_loop(self):
        while not self._stop.is_set():
            t0 = time.time()
            try:
                apps = scanner.scan_user_apps()
            except Exception:
                apps = []
            self._queue.put(("scan", apps))
            self._wake.wait(timeout=max(0.5, SCAN_INTERVAL - (time.time() - t0)))
            self._wake.clear()

    def _poll(self):
        try:
            while True:
                kind, *payload = self._queue.get_nowait()
                if kind == "scan":
                    self._on_scan(payload[0])
                elif kind == "killed":
                    self._on_killed(payload[0], payload[1])
        except queue.Empty:
            pass
        self._poll_job = self.after(POLL_MS, self._poll)

    def _on_scan(self, apps):
        self._apps = apps
        self._selected &= {a["pid"] for a in apps}
        self._last_scan_time = datetime.now()
        # 大数字 = 看板应用内存 ÷ 全局内存,和面板顶栏的百分比同源,不会对不上
        apps_mb = sum(a["memory_mb"] for a in apps)
        pct = apps_mb / self._total_ram_mb * 100
        self._canvas.itemconfigure(self._pct_id, text=f"{pct:.0f}%")
        self._canvas.itemconfigure(self._mem_id, text=f"{len(apps)} 个应用")
        self._update_panel_chrome()
        if self._panel is not None:
            self._update_list()

    def _update_panel_chrome(self):
        """标题右侧的 数字 + 顶部蓝色进度条。"""
        if self._panel is None:
            return
        total_mb = sum(a["memory_mb"] for a in self._apps)
        pct = total_mb / self._total_ram_mb * 100
        self._panel.count_lbl.config(
            text=f"{total_mb / 1024:.1f}GB/{self._total_ram_mb / 1024:.0f}GB ({pct:.0f}%)")
        self._panel.bar.place_configure(relwidth=min(1.0, pct / 100))
        self._panel.status.config(
            text=f"共 {len(self._apps)} 个 · 上次刷新 {self._last_scan_time:%H:%M:%S}")

    def quit_app(self):
        self._stop.set()
        self._wake.set()
        if self._poll_job is not None:
            self.after_cancel(self._poll_job)
        if self._trans_job is not None:
            self.after_cancel(self._trans_job)
        if self._pulse_job is not None:
            self.after_cancel(self._pulse_job)
        self.destroy()
