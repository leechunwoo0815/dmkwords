"""WM15 全量视觉资产生成器：24 头像（12 动物×2 配色）+ 9 勋章 → **三端同源分发**。

用法：python -m scripts.gen_wm15_visuals
输出（三端一份源，同一脚本一次生成，杜绝漂移）：
  头像：miniapp/icons/avatars/  admin-web/public/avatars/  backend/assets/avatars/
  勋章：miniapp/icons/badges/   admin-web/public/badges/   backend/assets/badges/

为什么三份都要（专家裁决 A2/B2）：
- 小程序本地包 = 零加载零 token（头像不进媒体消费点清单）
- 管理端 public = 建档选择器直接 <img>
- backend/assets = **入库目录**（card_engine 合成名片海报时读；uploads/ 被 gitignore，不能放源图）
"""

from __future__ import annotations

import os

from PIL import Image

from backend.domain.reading_circle.art import (
    AVATAR_IDS,
    BADGE_IDS,
    KIND_BASE,
    SCHEMES,
    Canvas,
    flame_outline,
    font_round,
    hex2rgb,
    paper_grain,
    sparkle,
    star,
    sticker_text,
)
from backend.domain.reading_circle.art import INK as _INK
from backend.domain.reading_circle.art_mascot import mascot as art_mascot

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
AVATAR_DIRS = [
    os.path.join(ROOT, "backend", "assets", "avatars"),
    os.path.join(ROOT, "miniapp", "icons", "avatars"),
    os.path.join(ROOT, "admin-web", "public", "avatars"),
]
BADGE_DIRS = [
    os.path.join(ROOT, "backend", "assets", "badges"),
    os.path.join(ROOT, "miniapp", "icons", "badges"),
    os.path.join(ROOT, "admin-web", "public", "badges"),
]

AVATAR_SIZE = 256
# 六档里程碑勋章配色（10万/50万/100万/500万/1000万/5000万）
MILESTONE_TIERS = [
    ("m1", "#E8A66B", "#C97F3F"),  # 铜
    ("m2", "#D8DEE4", "#A9B4BE"),  # 银
    ("m3", "#FFD166", "#E8A93B"),  # 金
    ("m4", "#BFE3F5", "#7FB6D9"),  # 铂
    ("m5", "#D9C6F5", "#A98BE0"),  # 紫晶
    ("m6", "#FFC9DE", "#F2789F"),  # 彩虹（最高档）
]


def _tint(hex_color: str, tint: str, t: float) -> str:
    a, b = hex2rgb(hex_color), hex2rgb(tint)
    return "#" + "".join(f"{max(0, min(255, int(a[i] + (b[i] - a[i]) * t))):02X}" for i in range(3))


def avatar_img(avatar_id: str) -> Image.Image:
    """avatar_id 形如 cat_sun / panda_mint（12 动物 × 2 配色）。"""
    kind, scheme = avatar_id.rsplit("_", 1)
    base = KIND_BASE[kind]
    _, tint, coin_warm = SCHEMES[scheme]
    fur = _tint(base["fur"], tint, 0.22)
    ear = _tint(base["ear"], tint, 0.18)
    coin = _tint(coin_warm, tint, 0.35)
    cv = Canvas(AVATAR_SIZE, AVATAR_SIZE, {"top": coin, "bot": coin}, transparent=True)
    r = AVATAR_SIZE * 0.355
    cx, cy = AVATAR_SIZE * 0.5, AVATAR_SIZE * 0.545
    cv.d.ellipse(
        cv.box(cx - r * 1.26, cy - r * 1.26, cx + r * 1.26, cy + r * 1.26),
        fill=hex2rgb(coin) + (255,),
        outline=hex2rgb(_INK) + (255,),
        width=int(AVATAR_SIZE * 0.032 * 3),
    )
    cv.d.arc(
        cv.box(cx - r * 1.06, cy - r * 1.06, cx + r * 1.06, cy + r * 1.06),
        start=196,
        end=330,
        fill=(255, 255, 255, 90),
        width=int(AVATAR_SIZE * 0.019 * 3),
    )
    art_mascot(cv, cx, cy, r * 0.95, kind=kind, fur=fur, ear=ear, blush=base["blush"])
    paper_grain(cv, 11)
    return cv.img.resize((AVATAR_SIZE, AVATAR_SIZE), Image.LANCZOS)


def badge_medal(size: int, ring: str, deep: str) -> Image.Image:
    main = _badge_canvas({"top": "#FFF6D8", "bot": "#FFE0C7"}, size)
    cv, cx, cy, r = main
    for sx in (-1, 1):
        cv.d.polygon(
            [
                cv.p(cx + sx * r * 0.16, cy + r * 0.70),
                cv.p(cx + sx * r * 0.74, cy + r * 1.18),
                cv.p(cx + sx * r * 0.36, cy + r * 1.26),
                cv.p(cx + sx * r * 0.02, cy + r * 0.86),
            ],
            fill=hex2rgb(deep) + (255,),
            outline=hex2rgb(_INK) + (255,),
            width=int(size * 0.014 * 3),
        )
    cv.d.ellipse(
        cv.box(cx - r, cy - r, cx + r, cy + r),
        fill=hex2rgb(ring) + (255,),
        outline=hex2rgb(_INK) + (255,),
        width=int(size * 0.026 * 3),
    )
    cv.d.ellipse(
        cv.box(cx - r * 0.84, cy - r * 0.84, cx + r * 0.84, cy + r * 0.84),
        fill=hex2rgb("#FFFDF6") + (255,),
    )
    star(cv, cx, cy, r * 0.62, deep, outline=_INK, width=size * 0.014, rotate=0.0)
    paper_grain(cv, 11)
    return cv.img.resize((size, size), Image.LANCZOS)


def badge_level_template(size: int) -> Image.Image:
    cv, cx, cy, r = _badge_canvas({"top": "#E9F3FF", "bot": "#D8E4FF"}, size)
    w, h = size * 0.34, size * 0.375

    def pentagon(scale: float) -> list:
        base = [(0.0, -1.15), (1.0, -0.60), (0.72, 0.70), (0.0, 1.22), (-0.72, 0.70), (-1.0, -0.60)]
        return [cv.p(cx + bx * w * scale, cy + by * h * scale) for bx, by in base]

    cv.d.polygon(
        pentagon(1.0),
        fill=hex2rgb("#6C9BF0") + (255,),
        outline=hex2rgb(_INK) + (255,),
        width=int(size * 0.040 * 3),
    )
    cv.d.polygon(pentagon(0.84), fill=hex2rgb("#EFF5FF") + (255,))
    paper_grain(cv, 11)
    return cv.img.resize((size, size), Image.LANCZOS)


def badge_flame(size: int, days: str) -> Image.Image:
    cv, cx, cy, _r = _badge_canvas({"top": "#EAFBF8", "bot": "#D3F0EC"}, size)
    cy = size * 0.545
    w, h = size * 0.30, size * 0.375

    def draw_flame(scale: float, fill: str, outline: str | None, lw: float) -> None:
        pts = [cv.p(x, cy + (y - cy) * scale) for x, y in flame_outline(cx, cy, w, h)]
        cv.d.polygon(
            pts,
            fill=hex2rgb(fill) + (255,),
            outline=hex2rgb(outline) + (255,) if outline else None,
            width=int(lw * 3) if outline else 0,
        )

    draw_flame(1.0, "#FF9E7A", _INK, size * 0.024)
    draw_flame(0.62, "#FFE0A0", None, 0)
    for sx in (-1, 1):
        ex, ey = cx + sx * size * 0.062, cy - size * 0.052
        cv.d.ellipse(
            cv.box(ex - size * 0.019, ey - size * 0.023, ex + size * 0.019, ey + size * 0.023),
            fill=hex2rgb(_INK) + (255,),
        )
    sticker_text(
        cv,
        (cx, cy + size * 0.075),
        days,
        font_round(int(size * 0.19)),
        "#C24E3C",
        stroke="#FFFFFF",
        stroke_w=size * 0.009,
    )
    sparkle(cv, size * 0.13, size * 0.87, size * 0.048, "#FFFFFF", 215)
    paper_grain(cv, 11)
    return cv.img.resize((size, size), Image.LANCZOS)


def _badge_canvas(pal: dict, size: int):
    cv = Canvas(size, size, pal, transparent=True)
    cx = cy = size * 0.5
    return cv, cx, cy, size * 0.34


def build_all() -> None:
    """生成全部资产并分发三端（avif 同名同图，D4 断言靠文件名集合对拍）。"""
    for d in AVATAR_DIRS + BADGE_DIRS:
        os.makedirs(d, exist_ok=True)

    for avatar_id in AVATAR_IDS:
        im = avatar_img(avatar_id)
        for d in AVATAR_DIRS:
            im.save(os.path.join(d, f"{avatar_id}.png"), "PNG")

    badges: dict[str, Image.Image] = {}
    for bid, ring, deep in MILESTONE_TIERS:
        badges[f"milestone_{bid}"] = badge_medal(AVATAR_SIZE, ring, deep)
    badges["level_template"] = badge_level_template(AVATAR_SIZE)
    badges["streak_7"] = badge_flame(AVATAR_SIZE, "7")
    badges["streak_30"] = badge_flame(AVATAR_SIZE, "30")
    for bid, im in badges.items():
        for d in BADGE_DIRS:
            im.save(os.path.join(d, f"{bid}.png"), "PNG")

    write_manifests()

    print(
        f"头像 {len(AVATAR_IDS)} 枚 × {len(AVATAR_DIRS)} 端；勋章 {len(BADGE_IDS)} 枚 × {len(BADGE_DIRS)} 端"
    )
    for d in AVATAR_DIRS + BADGE_DIRS:
        print(f"  {len(os.listdir(d)):3d} 个文件  {os.path.relpath(d, ROOT)}")


def write_manifests() -> None:
    """产出两端清单文件（**唯一事实源仍是 art.AVATAR_IDS/BADGE_IDS**）。

    为什么生成而不是手写：手写必然多份漂移（test_wm15_assets 会把清单也一起对拍）。
    """
    import json

    admin_dir = os.path.join(ROOT, "admin-web", "src", "constants")
    os.makedirs(admin_dir, exist_ok=True)
    with open(os.path.join(admin_dir, "avatars.json"), "w", encoding="utf-8") as f:
        json.dump(
            {"avatars": list(AVATAR_IDS), "badges": list(BADGE_IDS)},
            f,
            ensure_ascii=False,
            indent=2,
        )

    lines = [
        "// miniapp/utils/avatars.js — WM15 内置头像/勋章清单（**生成物，勿手改**）",
        "// 源：backend/domain/reading_circle/art.py 的 AVATAR_IDS/BADGE_IDS",
        "// 重新生成：python -m scripts.gen_wm15_visuals",
        f"const AVATAR_IDS = {list(AVATAR_IDS)!r}",
        f"const BADGE_IDS = {list(BADGE_IDS)!r}",
        "module.exports = { AVATAR_IDS, BADGE_IDS }",
        "",
    ]
    with open(os.path.join(ROOT, "miniapp", "utils", "avatars.js"), "w", encoding="utf-8") as f:
        f.write("\n".join(lines))


if __name__ == "__main__":
    build_all()
