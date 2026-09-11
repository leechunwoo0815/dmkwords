# backend/domain/reading_circle/art_mascot.py — 吉祥物（WM15，从 art.py 拆出防 god file）
"""大眼腮红吉祥物：12 种动物的耳/鬃/角/刺/喙特征 + 五官。

与 art.py 是**单向依赖**（本模块 import art；art 不反向引用），避免循环导入。
"""

from __future__ import annotations

import math

from .art import INK, PAPER, SS, Canvas, hex2rgb  # noqa: F401


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
