# Purify · Windows 进程清理悬浮球

> 当前版本 **v1.0.0**(2026-09-27)

仿 360 加速球的轻量 Windows 小工具:桌面角落常驻一个角色立绘小圆球,**左键点球**弹出看板,里面列出**你自己手动打开的应用进程**(微信、Codex、B站客户端……),系统进程一律过滤掉,勾选后**一键结束**。

- 无需安装,单个 exe,双击即用
- 四层过滤只显示"你打开的应用":当前用户 → **只保留有可见窗口的进程**(这正是关机时会被关闭的那批)→ 排除系统目录 → 排除系统组件黑名单;无窗口的后台帮手、更新器不会出现,它们会在主进程被结束时一并带走
- 应用显示"人话名字":读 exe 自带的文件说明,`cloudmusic.exe` 显示为 NetEase Cloud Music、`Thunder.exe` 显示为「迅雷」;小字附进程名和路径
- 白色简洁面板:顶部内存进度条,行首蓝点渐变,左名称右内存
- 扫描在后台线程进行,界面永不卡顿;列表画布化绘制 + 增量刷新
- 面板收起时零渲染开销,悬浮球只更新一个百分比数字

## 使用方法

- **左键点悬浮球**:弹出 / 收起看板(点面板外面也会自动收起,Esc 同效)
- **按住拖动**:悬浮球可以拖到屏幕任意位置
- **鼠标悬停**:立绘向脸部推近、数据盘淡出(仿 360 加速球的两态设计);移开恢复
- **右键悬浮球**:菜单里有"开机自启"开关和"退出"
- 球上大数字 = 全局物理内存占用,小字 = 看板内应用合计
- 看板里勾选进程(支持左上角**全选**)→ 点红色 **结束所选** 按钮 → **再点一次确认**(两段式确认,防止手滑)
- **防重复启动**:程序已在运行时再次双击,只会弹窗提醒"Purify 已经启动了哦",不会开出第二个球
- 个别进程提示"权限不足"时,右键 exe 选 **以管理员身份运行** 再杀

## 下载

永久固定链接(永远指向最新版):

**https://github.com/wind-001/purify-assistant/releases/latest/download/Purify.exe**

## 版本历史

- **v1.0.0**(2026-09-27):首个公开版本。立绘加速球(悬停推近脸部)、圆角看板、四层进程过滤、人话名字、一键全选、开机自启、防重复启动。

## 本地开发

```bash
python -m venv .venv
.venv\Scripts\pip install -r requirements.txt
.venv\Scripts\python main.py
```

## 打包成 exe

双击 `build.bat`,或在命令行执行:

```bash
.venv\Scripts\python -m PyInstaller --noconfirm --onefile --windowed --name Purify main.py
```

产物在 `dist\Purify.exe`。

## 发布到 GitHub Releases(给别人下载)

1. 本地打包出 `dist\Purify.exe`(仓库 `wind-001/purify-assistant`)
2. 仓库页面 → **Releases** → **Draft a new release** → 选择已有标签(如 `v1.0.0`)→ 把 `Purify.exe` 拖进附件区 → **Publish release**
3. 固定下载链接(永远指向最新版):
   `https://github.com/wind-001/purify-assistant/releases/latest/download/Purify.exe`

## 已知问题

- **杀毒软件 / SmartScreen 误报**:无签名的小众 exe 常见,选择"仍要运行"即可;随下载量增加会缓解,根治需要购买代码签名证书
- **列表只显示"有窗口"的应用**:最小化到托盘、无界面的后台进程不会单独出现(和任务管理器"应用"分类口径一致);想看全部进程可自行调整 `scanner.py` 里 `_visible_window_pids` 的使用
- **UWP 应用**(如微软商店版应用)路径可能显示不全,仍可正常勾选结束
- 黑名单在 `scanner.py` 的 `SYSTEM_PROCESS_NAMES`,想调整过滤规则直接改它
