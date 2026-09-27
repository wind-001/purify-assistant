"""悬浮球美术资源:把角色立绘做成圆形描画帧 + 毛玻璃盘。

纯 Pillow 实现:
- 圆形裁切(4x 超采样抗锯齿遮罩,边缘平滑)
- 多档缩放帧:静止时在头两帧轻推呼吸,悬停时向脸部推近(参考设计图二)
- 毛玻璃盘:半透明白 + 暖橙描边,四档透明度,悬停淡出时逐档切换
"""

import os
import sys

from PIL import Image, ImageDraw, ImageTk

SS = 4  # 超采样倍数,遮罩和缩放都过它,保证边缘平滑


def _resource_path(rel):
    base = getattr(sys, "_MEIPASS", os.path.dirname(os.path.abspath(__file__)))
    return os.path.join(base, rel)


def build(size, face_file="assets/ball_face.png"):
    """返回 (art_frames, glass_frames);立绘缺失时返回 ([], []) 走兜底。"""
    try:
        src = Image.open(_resource_path(face_file)).convert("RGB")
    except Exception:
        return [], []

    # 居中裁方
    s = min(src.size)
    l = (src.width - s) // 2
    t = (src.height - s) // 2
    src = src.crop((l, t, l + s, t + s))

    # 抗锯齿圆形遮罩
    mask = Image.new("L", (size * SS, size * SS), 0)
    ImageDraw.Draw(mask).ellipse((0, 0, size * SS - 1, size * SS - 1), fill=255)
    mask = mask.resize((size, size), Image.LANCZOS)

    # 缩放帧:第 0/1 帧给呼吸用,越往后越贴近脸部
    zooms = [1.0, 1.05, 1.11, 1.18, 1.26, 1.35, 1.45, 1.55]
    frames = []
    for z in zooms:
        zq = round(size * z * SS)
        img = src.resize((zq, zq), Image.LANCZOS)
        off = (zq - size * SS) // 2
        img = img.crop((off, off, off + size * SS, off + size * SS))
        img = img.resize((size, size), Image.LANCZOS)
        out = Image.new("RGBA", (size, size), (0, 0, 0, 0))
        out.paste(img, (0, 0), mask)
        frames.append(ImageTk.PhotoImage(out))

    # 毛玻璃盘:半透明白圆角方 + 暖橙描边,四档透明度做淡出
    glass = []
    for alpha in (110, 72, 38, 10):
        g = Image.new("RGBA", (size * SS, size * SS), (0, 0, 0, 0))
        d = ImageDraw.Draw(g)
        x0, y0 = int(size * SS * 0.09), int(size * SS * 0.07)
        x1, y1 = int(size * SS * 0.91), int(size * SS * 0.94)
        d.rounded_rectangle([x0, y0, x1, y1], radius=int(size * SS * 0.28),
                            fill=(255, 255, 255, alpha),
                            outline=(255, 187, 120, 230), width=2 * SS)
        g = g.resize((size, size), Image.LANCZOS)
        glass.append(ImageTk.PhotoImage(g))
    return frames, glass
