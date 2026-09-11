# backend/domain/reading_circle/art.py — WM15 绘本卡哇伊视觉工具包
"""Pillow 程序化绘制的「绘本卡哇伊」原语，供四类资产共用一套视觉语言：
卡片图（R2 双规格）/ 头像 24 枚（R3）/ 勋章 9 枚（C5）/ 名片海报（R5）。

为什么要独立模块：四类资产必须同源同风格，各画各的必然割裂（用户原话
「现在的毫无绘本风」= 几何色块+文字，没有插画语言）。

技术要点：
- **全程 SS 倍超采样后 LANCZOS 降采样**——曲线/圆角/描边平滑（Pillow 无抗锯齿）
- 卡哇伊三信号：柔和渐变背景 + 云朵/星星/彩虹贴纸 + 大眼高光+腮红的吉祥物
- 字体（**实测选定，勿随意替换**）：
  - 中文 = Hiragino Sans GB **W6**（ttc index 2）——实测 62 个待用汉字 0 缺字；
    丸ゴ W4 圆体看着更可爱但缺 20 字（计/阅/读/测/验/词/馆/级/历…）是日文字体，已排除
  - 数字/字母 = .SF NS Rounded——圆体数字，卡哇伊关键
- 贴纸描边：draw.text(stroke_width/stroke_fill) 白描边+彩字 = 绘本标题感
"""

from __future__ import annotations

import math
import os

from PIL import Image, ImageDraw, ImageFilter, ImageFont

SS = 3  # 超采样倍数

_FONT_CN = ["/System/Library/Fonts/Hiragino Sans GB.ttc"]
_FONT_ROUND = [
    "/System/Library/Fonts/SFNSRounded.ttf",
    "/System/Library/Fonts/SFCompactRounded.ttf",
]

# 10 类卡 / 头像底 / 勋章 共用的马卡龙色盘（soft pastel，幼儿绘本基调）
PALETTES: dict[str, dict] = {
    "milestone": {"top": "#FFF6D8", "bot": "#FFE0C7", "accent": "#FF8FA3", "deep": "#D9536F"},
    "books_count": {"top": "#E8FBEF", "bot": "#CDEFE0", "accent": "#4FB98A", "deep": "#2E8A63"},
    "level_up": {"top": "#E9F3FF", "bot": "#D8E4FF", "accent": "#6C9BF0", "deep": "#3F6BC7"},
    "perfect_quiz": {"top": "#FFF0F6", "bot": "#FFD9E8", "accent": "#F2789F", "deep": "#C9506F"},
    "streak": {"top": "#E6FBF8", "bot": "#CBF0EC", "accent": "#3FBFB0", "deep": "#2A8C82"},
    "finish_book": {"top": "#FFF1E6", "bot": "#FFDCC2", "accent": "#F2935B", "deep": "#C4693A"},
    "rank_top": {"top": "#FFF9DC", "bot": "#FFE9AE", "accent": "#E8A93B", "deep": "#B57C1E"},
    "rank_up": {"top": "#EAFBF0", "bot": "#D2F2E2", "accent": "#4FBF8B", "deep": "#2F8A5F"},
    "weekly_report": {"top": "#F3EEFF", "bot": "#E4DBFF", "accent": "#8E7BE8", "deep": "#6450C4"},
    "breakthrough": {"top": "#FFF0F0", "bot": "#FFDCDC", "accent": "#F2765E", "deep": "#C24E3C"},
}

INK = "#5B4636"  # 主描边/正文（暖褐，比纯黑柔和）
PAPER = "#FFFDF8"

# ---------------- WM15 资产目录（**后端常量 = 唯一事实源**） ----------------
# 三端资源目录（backend/assets、miniapp/icons、admin-web/public）的文件名集合
# 必须与本常量严格相等——test_wm15_assets 逐目录对拍（D4：消费点清单机械化）

#: 12 动物 × 2 配色 = 24 枚头像（Child.avatar 存 avatar_id，如 "cat_sun"）
ANIMALS = (
    "cat",
    "dog",
    "panda",
    "fox",
    "bunny",
    "lion",
    "bear",
    "penguin",
    "owl",
    "deer",
    "hedgehog",
    "dino",
)
#: scheme → (显示名, 染色基准, 圆币底色)
SCHEMES: dict[str, tuple[str, str, str]] = {
    "sun": ("暖阳", "#FFD9A8", "#FFF0DC"),
    "mint": ("薄荷", "#A8DECB", "#E4F6EF"),
}
AVATAR_IDS: tuple[str, ...] = tuple(f"{a}_{s}" for a in ANIMALS for s in SCHEMES)
BADGE_IDS: tuple[str, ...] = (
    "milestone_m1",
    "milestone_m2",
    "milestone_m3",
    "milestone_m4",
    "milestone_m5",
    "milestone_m6",
    "level_template",
    "streak_7",
    "streak_30",
)
#: 各动物的基础配色（生成器按配色方案做染色）
KIND_BASE: dict[str, dict[str, str]] = {
    "cat": {"fur": "#FFC98F", "ear": "#FFB472", "blush": "#FF9CB4"},
    "dog": {"fur": "#F6D9B0", "ear": "#D9A96F", "blush": "#FF9CB4"},
    "panda": {"fur": "#FFFFFF", "ear": "#3B3B3B", "blush": "#FF9CB4"},
    "fox": {"fur": "#EE9A55", "ear": "#D97B36", "blush": "#FF9CB4"},
    "bunny": {"fur": "#FFFFFF", "ear": "#FFE3EC", "blush": "#FF9CB4"},
    "lion": {"fur": "#F7C86A", "ear": "#D98F3A", "blush": "#FF9CB4"},
    "bear": {"fur": "#C89A6B", "ear": "#A87748", "blush": "#FF9CB4"},
    "penguin": {"fur": "#41505F", "ear": "#41505F", "blush": "#FF9CB4"},
    "owl": {"fur": "#C9A98A", "ear": "#A8865F", "blush": "#FF9CB4"},
    "deer": {"fur": "#E0B98A", "ear": "#C79A63", "blush": "#FF9CB4"},
    "hedgehog": {"fur": "#E8C9A0", "ear": "#A67C52", "blush": "#FF9CB4"},
    "dino": {"fur": "#9BD9A8", "ear": "#6FBF82", "blush": "#FF9CB4"},
}

_font_cache: dict[tuple[str, int], ImageFont.FreeTypeFont] = {}


def hex2rgb(h: str) -> tuple[int, int, int]:
    h = h.lstrip("#")
    return tuple(int(h[i : i + 2], 16) for i in range(0, 6, 2))  # type: ignore[return-value]


def font_cn(size: int) -> ImageFont.FreeTypeFont:
    """中文粗体（Hiragino Sans GB W6）。"""
    key = ("cn", size)
    if key not in _font_cache:
        f = None
        for path in _FONT_CN:
            try:
                f = ImageFont.truetype(path, size, index=2)  # index 2 = W6
                break
            except OSError:
                continue
        _font_cache[key] = f or ImageFont.load_default(size)
    return _font_cache[key]


def font_round(size: int) -> ImageFont.FreeTypeFont:
    """数字/字母圆体（.SF NS Rounded）。"""
    key = ("round", size)
    if key not in _font_cache:
        f = None
        for path in _FONT_ROUND:
            try:
                f = ImageFont.truetype(path, size)
                break
            except OSError:
                continue
        _font_cache[key] = f or font_cn(size)
    return _font_cache[key]


# ---------------- 画布 ----------------


class Canvas:
    """超采样画布：内部按 SS 倍尺寸绘制，finish() 时 LANCZOS 降采样输出。

    所有坐标都用**最终尺寸**语义（调用方写 W×H 的坐标，内部自动乘 SS）。
    """

    def __init__(self, w: int, h: int, pal: dict, *, transparent: bool = False):
        self.w, self.h = w, h
        self.pal = pal
        self.img = (
            Image.new("RGBA", (w * SS, h * SS), (0, 0, 0, 0))
            if transparent
            else vertical_gradient((w * SS, h * SS), pal["top"], pal["bot"]).convert("RGBA")
        )
        self.d = ImageDraw.Draw(self.img, "RGBA")

    def p(self, *pts: float) -> tuple:
        """最终坐标 → 内部超采样坐标（点）。"""
        return tuple(v * SS for v in pts)

    def box(self, x0: float, y0: float, x1: float, y1: float) -> tuple:
        return (x0 * SS, y0 * SS, x1 * SS, y1 * SS)

    def finish(self, path: str) -> str:
        out = self.img.resize((self.w, self.h), Image.LANCZOS)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        out.save(path, "PNG")
        return path


def vertical_gradient(size: tuple[int, int], top: str, bottom: str) -> Image.Image:
    """竖向柔和渐变（1px 宽画好再拉伸，比逐行画快且平滑）。"""
    w, h = size
    c0, c1 = hex2rgb(top), hex2rgb(bottom)
    strip = Image.new("RGB", (1, h))
    for y in range(h):
        t = y / max(1, h - 1)
        strip.putpixel((0, y), tuple(int(c0[i] + (c1[i] - c0[i]) * t) for i in range(3)))
    return strip.resize((w, h), Image.BILINEAR)


# ---------------- 装饰原语 ----------------


def glow(cv: Canvas, cx: float, cy: float, radius: float, color: str, max_alpha: int = 120) -> None:
    """径向柔光（太阳/圣光感）。"""
    r = radius * SS
    cx, cy = cx * SS, cy * SS
    mask = Image.new("L", cv.img.size, 0)
    md = ImageDraw.Draw(mask)
    steps = 28
    for i in range(steps + 1):
        rr = r * (1 - i / steps)
        a = int(max_alpha * (i / steps) ** 1.7)
        md.ellipse((cx - rr, cy - rr, cx + rr, cy + rr), fill=a)
    cv.img.paste(Image.new("RGB", cv.img.size, hex2rgb(color)), (0, 0), mask)


def cloud(
    cv: Canvas,
    cx: float,
    cy: float,
    w: float,
    fill: str = "#FFFFFF",
    alpha: int = 210,
    jitter: float = 0.08,
) -> None:
    """云朵：几个交叠圆 + 底部圆角矩形。

    jitter：按坐标派生的确定性抖动（0=完全对称的数学圆形）——轻微不对称才像手绘，
    纯几何圆会被读作"扁平矢量 UI"而非绘本。
    """
    d, rgb = cv.d, hex2rgb(fill) + (alpha,)
    h = w * 0.55
    seed = int((cx * 7.3 + cy * 3.1) % 97)
    lobes = ((-0.30, 0.05, 0.34), (0.0, -0.10, 0.44), (0.30, 0.06, 0.32))
    for i, (dx, dy, k) in enumerate(lobes):
        j = 1 + jitter * (((seed + i * 37) % 11) / 10 - 0.5)
        rr = w * k * j
        d.ellipse(
            cv.box(cx + w * dx * j - rr, cy + h * dy - rr, cx + w * dx * j + rr, cy + h * dy + rr),
            fill=rgb,
        )
    d.rounded_rectangle(
        cv.box(cx - w * 0.52, cy + h * 0.02, cx + w * 0.52, cy + h * 0.40),
        radius=h * 0.2 * SS,
        fill=rgb,
    )


def paper_grain(cv: Canvas, alpha: int = 15) -> None:
    """纸纹颗粒（绘本"印在纸上"的质感）——最后一步叠加，罩住所有元素。"""
    noise = Image.effect_noise(cv.img.size, 32).convert("L")
    light = noise.point(lambda v: max(0, int((v - 128) / 127 * alpha)))
    dark = noise.point(lambda v: max(0, int((128 - v) / 127 * alpha)))
    cv.img.paste(Image.new("RGB", cv.img.size, (255, 255, 255)), (0, 0), light)
    cv.img.paste(Image.new("RGB", cv.img.size, (74, 56, 42)), (0, 0), dark)


def star(
    cv: Canvas,
    cx: float,
    cy: float,
    r: float,
    fill: str,
    *,
    outline: str | None = None,
    width: float = 0.0,
    rotate: float = 0.0,
) -> None:
    """胖五角星（圆润感：内径偏大）。"""
    pts = []
    for k in range(10):
        rad = r if k % 2 == 0 else r * 0.52
        a = k * math.pi / 5 - math.pi / 2 + rotate
        pts.append(cv.p(cx + rad * math.cos(a), cy + rad * math.sin(a)))
    cv.d.polygon(
        pts,
        fill=hex2rgb(fill) + (255,),
        outline=hex2rgb(outline) + (255,) if outline else None,
        width=int(width * SS) if outline else 0,
    )


def sparkle(
    cv: Canvas, cx: float, cy: float, r: float, color: str = "#FFFFFF", alpha: int = 230
) -> None:
    """四角闪光（小十字星）。"""
    d = cv.d
    rgb = hex2rgb(color) + (alpha,)
    d.polygon(
        [
            cv.p(cx, cy - r),
            cv.p(cx + r * 0.26, cy - r * 0.26),
            cv.p(cx + r, cy),
            cv.p(cx + r * 0.26, cy + r * 0.26),
            cv.p(cx, cy + r),
            cv.p(cx - r * 0.26, cy + r * 0.26),
            cv.p(cx - r, cy),
            cv.p(cx - r * 0.26, cy - r * 0.26),
        ],
        fill=rgb,
    )


def rainbow(
    cv: Canvas, cx: float, cy: float, r: float, width: float = 9.0, alpha: int = 200
) -> None:
    """彩虹弧（半圆，3 道）。"""
    colors = ["#FF9BB3", "#FFD166", "#7ED0C1"]
    for i, c in enumerate(colors):
        rr = r - i * width * 1.5
        cv.d.arc(
            cv.box(cx - rr, cy - rr, cx + rr, cy + rr),
            start=180,
            end=360,
            fill=hex2rgb(c) + (alpha,),
            width=int(width * SS),
        )


def dotted_arc(
    cv: Canvas, cx: float, cy: float, r: float, color: str = "#FFFFFF", alpha: int = 150, n: int = 9
) -> None:
    """珠点弧（装饰）。"""
    for k in range(n):
        a = math.pi * (0.15 + 0.7 * k / (n - 1))
        x, y = cx + r * math.cos(a), cy + r * math.sin(a)
        rr = r * 0.035
        cv.d.ellipse(cv.box(x - rr, y - rr, x + rr, y + rr), fill=hex2rgb(color) + (alpha,))


# ---------------- 吉祥物 ----------------


def bezier3(p0: tuple, p1: tuple, p2: tuple, p3: tuple, n: int = 14) -> list[tuple]:
    """三次贝塞尔采样（有机曲线的标准做法）。"""
    out = []
    for k in range(n + 1):
        t = k / n
        u = 1 - t
        x = u**3 * p0[0] + 3 * u * u * t * p1[0] + 3 * u * t * t * p2[0] + t**3 * p3[0]
        y = u**3 * p0[1] + 3 * u * u * t * p1[1] + 3 * u * t * t * p2[1] + t**3 * p3[1]
        out.append((x, y))
    return out


def flame_outline(cx: float, cy: float, w: float, h: float, n: int = 14) -> list[tuple]:
    """火焰轮廓：**三簇火苗**（中高、右中、左中 + 两处凹谷），圆底。

    首版用极坐标扰动生成的是"水滴/团块"（judge 实锤不读作火），改为显式
    三尖 + 凹谷的手绘路径——这是"一眼认出火焰"的关键。
    """
    bottom = (cx, cy + h)
    top = (cx, cy - h)
    right_tip = (cx + w * 0.70, cy - h * 0.40)
    left_tip = (cx - w * 0.70, cy - h * 0.40)
    r_valley = (cx + w * 0.30, cy - h * 0.46)
    l_valley = (cx - w * 0.30, cy - h * 0.46)
    pts: list[tuple] = []
    pts += bezier3(
        bottom, (cx + w * 1.00, cy + h * 0.42), (cx + w * 0.98, cy - h * 0.02), right_tip, n
    )
    pts += bezier3(
        right_tip, (cx + w * 0.62, cy - h * 0.34), (cx + w * 0.44, cy - h * 0.40), r_valley, n
    )
    pts += bezier3(r_valley, (cx + w * 0.26, cy - h * 0.72), (cx + w * 0.10, cy - h * 0.86), top, n)
    pts += bezier3(top, (cx - w * 0.10, cy - h * 0.86), (cx - w * 0.26, cy - h * 0.72), l_valley, n)
    pts += bezier3(
        l_valley, (cx - w * 0.44, cy - h * 0.40), (cx - w * 0.62, cy - h * 0.34), left_tip, n
    )
    pts += bezier3(
        left_tip, (cx - w * 0.98, cy - h * 0.02), (cx - w * 1.00, cy + h * 0.42), bottom, n
    )
    return pts


def _ears(
    cv: Canvas,
    cx: float,
    cy: float,
    r: float,
    kind: str,
    ear_rgb: tuple,
    ink_rgb: tuple,
    blush_rgb: tuple,
    lw: float,
) -> None:
    """耳朵/头部特征（画在头之前）：内耳用**实色**同步缩放，杜绝描边与内层之间露白缝。"""
    d = cv.d
    if kind in ("cat", "fox"):
        for sx in (-1, 1):
            narrow = 1.06 if kind == "fox" else 1.0
            outer = [
                cv.p(cx + sx * r * 0.42, cy - r * 0.76),
                cv.p(cx + sx * r * 1.02 * narrow, cy - r * 1.30),
                cv.p(cx + sx * r * 1.00, cy - r * 0.30),
            ]
            d.polygon(outer, fill=ear_rgb, outline=ink_rgb, width=int(lw * SS))
            inner = [
                cv.p(cx + sx * r * 0.60, cy - r * 0.72),
                cv.p(cx + sx * r * 0.92 * narrow, cy - r * 1.06),
                cv.p(cx + sx * r * 0.90, cy - r * 0.48),
            ]
            d.polygon(inner, fill=blush_rgb)
    elif kind == "bunny":
        for sx in (-1, 1):
            bx = cx + sx * r * 0.30
            tx = bx + sx * r * 0.22
            ty = cy - r * 1.30
            d.ellipse(
                cv.box(min(bx, tx) - r * 0.27, ty, max(bx, tx) + r * 0.27, cy - r * 0.16),
                fill=ear_rgb,
                outline=ink_rgb,
                width=int(lw * SS),
            )
            d.ellipse(
                cv.box(
                    min(bx, tx) - r * 0.13, ty + r * 0.22, max(bx, tx) + r * 0.13, cy - r * 0.34
                ),
                fill=blush_rgb,
            )
    elif kind == "dog":
        # 垂耳：向外下挂出头部轮廓（此前贴在头内侧被头盖住 → 认不出狗）
        for sx in (-1, 1):
            d.rounded_rectangle(
                cv.box(
                    cx + sx * r * 0.92 - r * 0.34,
                    cy - r * 0.74,
                    cx + sx * r * 0.92 + r * 0.34,
                    cy + r * 0.62,
                ),
                radius=r * 0.32 * SS,
                fill=ear_rgb,
                outline=ink_rgb,
                width=int(lw * SS),
            )
    elif kind == "panda":
        for sx in (-1, 1):
            d.ellipse(
                cv.box(
                    cx + sx * r * 0.72 - r * 0.33,
                    cy - r * 0.84 - r * 0.33,
                    cx + sx * r * 0.72 + r * 0.33,
                    cy - r * 0.84 + r * 0.33,
                ),
                fill=hex2rgb("#3B3B3B") + (255,),
                outline=ink_rgb,
                width=int(lw * 0.6 * SS),
            )
    elif kind == "lion":
        # 鬃毛：一圈交叠圆（同色深浅交替）
        for k in range(14):
            a = 2 * math.pi * k / 14
            mx, my = cx + math.cos(a) * r * 1.02, cy + math.sin(a) * r * 1.02
            d.ellipse(
                cv.box(mx - r * 0.34, my - r * 0.34, mx + r * 0.34, my + r * 0.34),
                fill=ear_rgb,
                outline=ink_rgb,
                width=int(lw * 0.7 * SS),
            )
    elif kind == "owl":
        for sx in (-1, 1):
            d.polygon(
                [
                    cv.p(cx + sx * r * 0.30, cy - r * 0.86),
                    cv.p(cx + sx * r * 0.86, cy - r * 1.34),
                    cv.p(cx + sx * r * 0.94, cy - r * 0.60),
                ],
                fill=ear_rgb,
                outline=ink_rgb,
                width=int(lw * SS),
            )
    elif kind == "deer":
        for sx in (-1, 1):
            a = cv.p  # 鹿角：两段折线
            d.line(
                [
                    a(cx + sx * r * 0.34, cy - r * 0.78),
                    a(cx + sx * r * 0.44, cy - r * 1.00),
                    a(cx + sx * r * 0.68, cy - r * 1.14),
                ],
                fill=ink_rgb,
                width=int(lw * 2.8 * SS),
                joint="curve",
            )
            d.line(
                [a(cx + sx * r * 0.46, cy - r * 0.94), a(cx + sx * r * 0.78, cy - r * 1.02)],
                fill=ink_rgb,
                width=int(lw * 2.4 * SS),
            )
            d.ellipse(
                cv.box(
                    cx + sx * r * 0.74 - r * 0.22,
                    cy - r * 0.66 - r * 0.26,
                    cx + sx * r * 0.74 + r * 0.22,
                    cy - r * 0.66 + r * 0.26,
                ),
                fill=ear_rgb,
                outline=ink_rgb,
                width=int(lw * 0.8 * SS),
            )
    elif kind == "hedgehog":
        # 刺群：绕头近 3/4 圈（此前只画上半 = 像皇冠），尖刺长短交替
        pts = []
        n = 13
        for k in range(n + 1):
            a = math.pi * 0.94 + math.pi * 1.52 * k / n
            spike = 1.32 if k % 2 == 0 else 1.05
            pts.append(cv.p(cx + math.cos(a) * r * spike, cy + math.sin(a) * r * spike))
        pts.append(cv.p(cx + r * 0.80, cy + r * 0.66))
        pts.append(cv.p(cx - r * 0.80, cy + r * 0.66))
        d.polygon(pts, fill=ear_rgb, outline=ink_rgb, width=int(lw * SS))
    elif kind == "dino":
        # 背刺：3 片大三角（与刺猬的密集刺群拉开区别）
        for k, ox in enumerate((-0.56, -0.04, 0.48)):
            h = (1.30, 1.46, 1.26)[k]
            d.polygon(
                [
                    cv.p(cx + r * ox, cy - r * 0.82),
                    cv.p(cx + r * (ox + 0.26), cy - r * h),
                    cv.p(cx + r * (ox + 0.50), cy - r * 0.78),
                ],
                fill=ear_rgb,
                outline=ink_rgb,
                width=int(lw * 0.9 * SS),
            )
    else:  # bear / 通用圆耳
        if kind == "penguin":
            return  # 企鹅无耳
        for sx in (-1, 1):
            d.ellipse(
                cv.box(
                    cx + sx * r * 0.74 - r * 0.31,
                    cy - r * 0.80 - r * 0.31,
                    cx + sx * r * 0.74 + r * 0.31,
                    cy - r * 0.80 + r * 0.31,
                ),
                fill=ear_rgb,
                outline=ink_rgb,
                width=int(lw * SS),
            )
            d.ellipse(
                cv.box(
                    cx + sx * r * 0.74 - r * 0.16,
                    cy - r * 0.80 - r * 0.16,
                    cx + sx * r * 0.74 + r * 0.16,
                    cy - r * 0.80 + r * 0.16,
                ),
                fill=blush_rgb,
            )


def _feature(
    cv: Canvas, cx: float, cy: float, r: float, kind: str, ink_rgb: tuple, alpha: int = 255
) -> None:
    """物种特征（眼睛之后画）：企鹅白脸/橙喙、猫头鹰喙、狐狸白口鼻。"""
    d = cv.d
    if kind == "penguin":
        d.ellipse(
            cv.box(cx - r * 0.62, cy - r * 0.30, cx + r * 0.62, cy + r * 0.74),
            fill=(255, 255, 255, alpha),
        )
        d.polygon(
            [
                cv.p(cx - r * 0.20, cy + r * 0.16),
                cv.p(cx + r * 0.20, cy + r * 0.16),
                cv.p(cx, cy + r * 0.44),
            ],
            fill=hex2rgb("#F59E42") + (alpha,),
            outline=ink_rgb,
            width=int(max(1.2, r * 0.02) * SS),
        )
    elif kind == "owl":
        for sx in (-1, 1):
            d.ellipse(
                cv.box(
                    cx + sx * r * 0.34 - r * 0.40,
                    cy - r * 0.12 - r * 0.42,
                    cx + sx * r * 0.34 + r * 0.40,
                    cy - r * 0.12 + r * 0.42,
                ),
                fill=(255, 250, 238, 235),
            )
        d.polygon(
            [
                cv.p(cx - r * 0.22, cy + r * 0.18),
                cv.p(cx + r * 0.22, cy + r * 0.18),
                cv.p(cx, cy + r * 0.58),
            ],
            fill=hex2rgb("#F59E42") + (alpha,),
            outline=ink_rgb,
            width=int(max(1.2, r * 0.022) * SS),
        )
    elif kind == "fox":
        d.ellipse(
            cv.box(cx - r * 0.56, cy + r * 0.06, cx + r * 0.56, cy + r * 0.72),
            fill=(255, 253, 246, alpha),
        )
        d.polygon(
            [
                cv.p(cx - r * 0.58, cy + r * 0.20),
                cv.p(cx - r * 0.96, cy + r * 0.30),
                cv.p(cx - r * 0.54, cy + r * 0.46),
            ],
            fill=(255, 253, 246, alpha),
        )
        d.polygon(
            [
                cv.p(cx + r * 0.58, cy + r * 0.20),
                cv.p(cx + r * 0.96, cy + r * 0.30),
                cv.p(cx + r * 0.54, cy + r * 0.46),
            ],
            fill=(255, 253, 246, alpha),
        )
    elif kind == "hedgehog":
        d.ellipse(
            cv.box(cx - r * 0.50, cy + r * 0.04, cx + r * 0.50, cy + r * 0.70),
            fill=hex2rgb("#FBE3CB") + (alpha,),
        )
        d.polygon(
            [
                cv.p(cx - r * 0.30, cy + r * 0.30),
                cv.p(cx - r * 0.86, cy + r * 0.14),
                cv.p(cx - r * 0.30, cy + r * 0.52),
            ],
            fill=hex2rgb("#FBE3CB") + (alpha,),
        )
        d.polygon(
            [
                cv.p(cx + r * 0.30, cy + r * 0.30),
                cv.p(cx + r * 0.86, cy + r * 0.14),
                cv.p(cx + r * 0.30, cy + r * 0.52),
            ],
            fill=hex2rgb("#FBE3CB") + (alpha,),
        )


def mascot(
    cv: Canvas,
    cx: float,
    cy: float,
    r: float,
    *,
    kind: str = "cat",
    fur: str = "#FFC98F",
    ear: str | None = None,
    ink: str = INK,
    blush: str = "#FF9CB4",
    alpha: int = 255,
    nose: bool = True,
) -> None:
    """大眼腮红吉祥物（卡哇伊核心信号）：耳 / 头 / 眼+双高光 / 鼻 / 腮红 / 小 w 嘴。"""
    d = cv.d
    fur_rgb = hex2rgb(fur) + (alpha,)
    ear_rgb = hex2rgb(ear or fur) + (alpha,)
    ink_rgb = hex2rgb(ink) + (alpha,)
    blush_rgb = hex2rgb(blush) + (alpha,)
    lw = max(2.0, r * 0.048)

    _ears(cv, cx, cy, r, kind, ear_rgb, ink_rgb, blush_rgb, lw)

    # 头
    d.ellipse(
        cv.box(cx - r, cy - r, cx + r, cy + r), fill=fur_rgb, outline=ink_rgb, width=int(lw * SS)
    )

    # 熊猫黑眼圈（大眼窝）
    if kind == "panda":
        for sx in (-1, 1):
            d.ellipse(
                cv.box(
                    cx + sx * r * 0.36 - r * 0.29,
                    cy - r * 0.14 - r * 0.33,
                    cx + sx * r * 0.36 + r * 0.29,
                    cy - r * 0.14 + r * 0.33,
                ),
                fill=hex2rgb("#3B3B3B") + (alpha,),
            )

    # 物种特征（白脸/喙/口鼻）
    _feature(cv, cx, cy, r, kind, ink_rgb, alpha)

    # 眼睛：白底 + 瞳孔 + 双高光（画在特征之后，保证不被白脸遮住）
    for sx in (-1, 1):
        ex, ey = cx + sx * r * 0.33, cy - r * 0.10
        er = r * 0.21
        d.ellipse(
            cv.box(ex - er, ey - er * 1.12, ex + er, ey + er * 1.12),
            fill=(255, 255, 255, alpha),
            outline=ink_rgb,
            width=int(max(1.5, r * 0.028) * SS),
        )
        pr = er * 0.58
        d.ellipse(
            cv.box(ex - pr, ey - pr * 0.92, ex + pr, ey + pr * 1.08), fill=hex2rgb(ink) + (alpha,)
        )
        hr = er * 0.30
        d.ellipse(
            cv.box(
                ex + pr * 0.10, ey - pr * 0.80, ex + pr * 0.10 + hr * 2, ey - pr * 0.80 + hr * 2
            ),
            fill=(255, 255, 255, alpha),
        )

    # 鼻子（物种辨识度关键）
    if nose:
        ny = cy + r * 0.20
        if kind == "panda":
            d.ellipse(
                cv.box(cx - r * 0.15, ny - r * 0.10, cx + r * 0.15, ny + r * 0.11),
                fill=hex2rgb("#3B3B3B") + (alpha,),
            )
        elif kind == "bunny":
            d.polygon(
                [
                    cv.p(cx - r * 0.12, ny - r * 0.07),
                    cv.p(cx + r * 0.12, ny - r * 0.07),
                    cv.p(cx, ny + r * 0.11),
                ],
                fill=blush_rgb,
            )
        else:
            d.ellipse(
                cv.box(cx - r * 0.11, ny - r * 0.08, cx + r * 0.11, ny + r * 0.09),
                fill=hex2rgb(ink) + (alpha,),
            )
            d.arc(
                cv.box(cx - r * 0.11, ny - r * 0.02, cx + r * 0.11, ny + r * 0.20),
                start=20,
                end=160,
                fill=hex2rgb(ink) + (alpha,),
                width=int(max(1.4, r * 0.026) * SS),
            )

    # 腮红
    for sx in (-1, 1):
        bx, by = cx + sx * r * 0.64, cy + r * 0.32
        d.ellipse(
            cv.box(bx - r * 0.21, by - r * 0.13, bx + r * 0.21, by + r * 0.13),
            fill=hex2rgb(blush) + (int(alpha * 0.6),),
        )

    # 嘴：小 w 弧（熊猫/兔已有鼻，嘴下移）
    my = cy + r * 0.40 if kind in ("panda", "bunny") else cy + r * 0.44
    d.arc(
        cv.box(cx - r * 0.20, my - r * 0.18, cx + r * 0.20, my + r * 0.14),
        start=18,
        end=162,
        fill=ink_rgb,
        width=int(max(1.5, r * 0.034) * SS),
    )


# ---------------- 文字与容器 ----------------


def sticker_text(
    cv: Canvas,
    xy: tuple[float, float],
    text: str,
    font: ImageFont.FreeTypeFont,
    fill: str,
    *,
    anchor: str = "mm",
    stroke: str = "#FFFFFF",
    stroke_w: float = 0.0,
    alpha: int = 255,
) -> None:
    """贴纸字（白描边 + 彩字 = 绘本标题感）；anchor 用 Pillow 标准锚点。

    **字号必须随 SS 放大**：画布是 SS 倍超采样、坐标已乘 SS，若字号不放大，
    文字实际只有预期的 1/SS（首版"字太小不可读"的真根因）。
    """
    scaled = font.font_variant(size=max(1, int(getattr(font, "size", 0) or 0) * SS))
    cv.d.text(
        cv.p(*xy),
        text,
        font=scaled,
        fill=hex2rgb(fill) + (alpha,),
        anchor=anchor,
        stroke_width=int(stroke_w * SS) if stroke_w else 0,
        stroke_fill=hex2rgb(stroke) + (alpha,),
    )


def sticker_pair(
    cv: Canvas,
    center: tuple[float, float],
    num: str,
    num_font: ImageFont.FreeTypeFont,
    unit: str,
    unit_font: ImageFont.FreeTypeFont,
    fill: str,
    *,
    gap_ratio: float = 0.07,
    dy_unit: float = 16.0,
    stroke: str = "#FFFFFF",
    stroke_w: float = 0.0,
    alpha: int = 255,
) -> None:
    """数字 + 量词作为**一个词**整体居中（避免「7」与「天」被拆散、间距失控）。"""
    fn = num_font.font_variant(size=int(num_font.size * SS))
    fu = unit_font.font_variant(size=int(unit_font.size * SS))
    nw = cv.d.textlength(num, font=fn)
    uw = cv.d.textlength(unit, font=fu)
    gap = gap_ratio * fn.size
    left = center[0] * SS - (nw + gap + uw) / 2
    kw = dict(
        fill=hex2rgb(fill) + (alpha,),
        stroke_width=int(stroke_w * SS) if stroke_w else 0,
        stroke_fill=hex2rgb(stroke) + (alpha,),
    )
    cv.d.text((left + nw / 2, center[1] * SS), num, font=fn, anchor="mm", **kw)
    cv.d.text((left + nw + gap, center[1] * SS + dy_unit * SS), unit, font=fu, anchor="lm", **kw)


def bubble(
    cv: Canvas,
    box: tuple[float, float, float, float],
    *,
    radius: float = 40,
    fill: str = PAPER,
    outline: str | None = INK,
    width: float = 5,
    alpha: int = 255,
) -> None:
    """圆角气泡容器（白卡+粗描边的贴纸感）；outline=None 表示无描边。"""
    cv.d.rounded_rectangle(
        cv.box(*box),
        radius=radius * SS,
        fill=hex2rgb(fill) + (alpha,),
        outline=hex2rgb(outline) + (alpha,) if outline else None,
        width=int(width * SS) if outline else 0,
    )


def soft_shadow(
    cv: Canvas,
    box: tuple[float, float, float, float],
    *,
    radius: float = 40,
    blur: float = 9,
    alpha: int = 55,
) -> None:
    """柔和投影（贴纸浮起来）。"""
    layer = Image.new("L", cv.img.size, 0)
    ImageDraw.Draw(layer).rounded_rectangle(cv.box(*box), radius=radius * SS, fill=alpha)
    layer = layer.filter(ImageFilter.GaussianBlur(blur * SS))
    cv.img.paste(Image.new("RGB", cv.img.size, hex2rgb(INK)), (0, 0), layer)
