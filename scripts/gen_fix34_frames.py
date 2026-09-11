"""fix34 R4 等级头像框：样图（默认）+ 资产/清单落盘（--write-assets 过门后）。

用法：
  python -m scripts.gen_fix34_frames              # 只出样图 → uploads/fix34-samples/
  python -m scripts.gen_fix34_frames --write-assets   # 样图门通过后：落 4 张框 + 生成 miniapp 清单

四档（等级分组，甲方可推翻）：A-B 星芒 / C-E 银环 / F-H 金冠 / I+ 彩虹
四档**形态本身不同**（不只是换色），且每档在 96px 信息流尺寸下仍可辨。

几何约定（前端叠层必须照抄）：
  - 框画布 FRAME_SIZE=440，透明底；头像（256 资产）居中贴入 → 头像占框的 256/440 = 58.18%
  - 环占据半径 118..152（厚度 34）：内沿 118 恰好抱住头像圆币外沿
    （头像圆币半径 ≈ 0.447 × 256 ≈ 114.5）；外圈留白 38px 供皇冠/星芒等装饰
  - Pillow 的 arc/ellipse 描边**由 bbox 向内**画 → 渐变环的 bbox 取**外沿半径**
    （fix34 首版踩过：bbox 取内沿 → 环画进头像底下被盖住，看起来"没有环"）
  - 前端叠层：<image 框> 宽高 = 头像的 440/256 = 171.88%，居中偏移 -35.94%
    （与 miniapp/utils/frames.js 的 FRAME_SCALE 同源，改框几何必须同步改两处）
"""

from __future__ import annotations

import json
import math
import os

from PIL import Image

from backend.domain.reading_circle.art import (
    INK,
    PAPER,
    SS,
    Canvas,
    font_cn,
    hex2rgb,
    sparkle,
    star,
)

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
OUT_SAMPLES = os.path.join(ROOT, "uploads", "fix34-samples")
OUT_ASSETS = os.path.join(ROOT, "miniapp", "icons", "frames")
MANIFEST = os.path.join(ROOT, "miniapp", "utils", "frames.js")
AVATAR_DIR = os.path.join(ROOT, "miniapp", "icons", "avatars")
MUTED = "#9A8C80"  # 次级说明文字（与卡片/头像同一暖色系）

FRAME_SIZE = 440
AVATAR_SIZE = 256
CX = CY = FRAME_SIZE / 2
R_IN, R_OUT = 118.0, 152.0
RING_W = R_OUT - R_IN

# 四档定义（单一事实源：同时供样图、资产、miniapp 清单使用）
#   min_level = 该档起始等级序号（A=1）——A-B 星芒 / C-E 银环 / F-H 金冠 / I+ 彩虹
#   华丽度沿三条轴递进（judge 二轮返工要求"不能只换色"）：
#     环厚 28 → 34 → 34 → 34；环层 单 → 双(内刻线) → 双 → 双(金内环)；装饰 环上星 → 铆钉8 → 皇冠+星 → 金环+闪光
FRAMES = [
    {
        "id": "star",
        "label": "星芒框",
        "levels": "A-B",
        "min_level": 1,
        "ring": ["#7CC8FF", "#3E9BE8", "#7CC8FF", "#3E9BE8"],
        "thickness": 26.0,
        "deco": "star",
    },
    {
        "id": "silver",
        "label": "银环框",
        "levels": "C-E",
        "min_level": 3,
        "ring": ["#A6B3C4", "#8794A8", "#98A6B9", "#7A8798"],
        "thickness": 36.0,
        "deco": "silver",
    },
    {
        "id": "gold",
        "label": "金冠框",
        "levels": "F-H",
        "min_level": 6,
        "ring": ["#FFE9AE", "#E9A72C", "#FFDB86", "#D9941C"],
        "thickness": 40.0,
        "deco": "crown",
    },
    {
        "id": "rainbow",
        "label": "彩虹框",
        "levels": "I 以上",
        "min_level": 9,
        "ring": ["#FF7B7B", "#FFB65C", "#FFE45C", "#7EDC86", "#5FBCF5", "#A98BEE"],
        "thickness": 40.0,
        "deco": "rainbow",
    },
]


def _lerp(c1: str, c2: str, t: float) -> tuple[int, int, int]:
    a, b = hex2rgb(c1), hex2rgb(c2)
    return tuple(int(a[i] + (b[i] - a[i]) * t) for i in range(3))


def _ring(
    cv: Canvas,
    colors: list[str],
    *,
    steps: int = 96,
    thickness: float = RING_W,
    r_out: float = R_OUT,
    outline_w: float = 3.4,
    highlight: bool = True,
) -> None:
    """渐变圆环：**bbox 取外沿半径**（Pillow 描边向内画 → 环占 r_out-thickness..r_out）。

    首尾同色时天然无缝；外沿深棕描边 + 左上高光弧（与头像圆币同一手法）。
    内刻线/内细环用 `outline_w=0, highlight=False` 再叠一圈细环。
    """
    n = len(colors)
    for i in range(steps):
        t = i / steps
        seg = t * n
        i0 = int(seg) % n
        col = _lerp(colors[i0], colors[(i0 + 1) % n], seg - int(seg))
        a0 = -90 + i * 360 / steps
        a1 = a0 + 360 / steps + 1.4  # 轻微重叠，防段间白缝
        cv.d.arc(
            cv.box(CX - r_out, CY - r_out, CX + r_out, CY + r_out),
            start=a0,
            end=a1,
            fill=col + (255,),
            width=int(thickness * SS),
        )
    if outline_w > 0:
        cv.d.ellipse(
            cv.box(CX - r_out, CY - r_out, CX + r_out, CY + r_out),
            outline=hex2rgb(INK) + (255,),
            width=int(outline_w * SS),
        )
    if highlight:
        r_hi = r_out - thickness + 5
        cv.d.arc(
            cv.box(CX - r_hi, CY - r_hi, CX + r_hi, CY + r_hi),
            start=200,
            end=280,
            fill=(255, 255, 255, 140),
            width=int(5.0 * SS),
        )


def _ring_stars(cv: Canvas, count: int, *, r: float, size: float, color: str, outline: str) -> None:
    """环带上的小星（落在环体上而非游离在外 → 96px 下仍看得见）。"""
    for k in range(count):
        ang = -90 + k * 360 / count
        rad = ang * math.pi / 180
        star(
            cv,
            CX + r * math.cos(rad),
            CY + r * math.sin(rad),
            size,
            color,
            outline=outline,
            width=1.8,
            rotate=0.2,
        )


def _crown(cv: Canvas) -> None:
    """金冠档：五齿皇冠**骑在环带上**（冠底压住环带内沿，不盖头像头顶/耳朵）。

    尺寸按"真实显示尺寸下仍读得出皇冠"定（画布宽 128 ≈ 信息流 108px 时约 31px 宽）。
    """
    half, teeth, body = 64.0, 56.0, 22.0
    base_y = CY - R_IN - 24  # 冠底半径 ≈ 120（落在环带 118..152 上），不与头像圆币(114.5)交叠
    pts = [
        (CX - half, base_y),
        (CX - half * 0.72, base_y - teeth),
        (CX - half * 0.36, base_y - teeth * 0.42),
        (CX, base_y - teeth * 1.16),
        (CX + half * 0.36, base_y - teeth * 0.42),
        (CX + half * 0.72, base_y - teeth),
        (CX + half, base_y),
        (CX + half, base_y + body),
        (CX - half, base_y + body),
    ]
    cv.d.polygon(
        [cv.p(x, y) for x, y in pts],
        fill=hex2rgb("#FFD166") + (255,),
        outline=hex2rgb(INK) + (255,),
        width=int(4.2 * SS),
    )
    # 冠带（深一号的色条）+ 冠上三颗小星（大尺寸下靠"黄色块 + 深描边"读作皇冠）
    cv.d.rectangle(
        cv.box(CX - half, base_y + body - 10, CX + half, base_y + body),
        fill=hex2rgb("#EFAE3B") + (255,),
        outline=hex2rgb(INK) + (255,),
        width=int(2.6 * SS),
    )
    for dx, r in ((-half * 0.72, 7.5), (0, 9.5), (half * 0.72, 7.5)):
        star(cv, CX + dx, base_y - teeth * 0.86, r, "#FFFFFF", outline=None, width=0, rotate=0.0)


def _deco(cv: Canvas, spec: dict) -> None:
    """档位装饰：**环层数 / 装饰件从不遮环体、形态逐档加码**（judge：不能只换色）。

    ① 星芒（A-B）单环 + 环上 3 颗大星；② 银环（C-E）深色刻线 + 4 颗大铆钉（明显比 ① 讲究）；
    ③ 金冠（F-H）亮金刻线 + 3 星 + 骑环皇冠；④ 彩虹（I+）金内环 + 4 处大闪光 + 2 星。
    装饰件尺寸按"信息流真实尺寸（约 108px = 画布 0.245 倍）下仍可辨"定，不是按大头像好看定。
    """
    kind = spec["deco"]
    ring = spec["ring"]
    band_r = (R_IN + R_OUT) / 2
    if kind == "star":  # A-B：单环 + 环上 3 颗大星（跨在环带上 → 小尺寸也在）
        _ring_stars(cv, 3, r=band_r, size=30.0, color="#FFFFFF", outline=ring[1])
    elif kind == "silver":  # C-E：厚石板银环 + 3 颗大铆钉（真实尺寸下的"升级标记"）
        for ang in (-90, 30, 150):
            rad = ang * math.pi / 180
            r = 15.0
            x = CX + band_r * math.cos(rad)
            y = CY + band_r * math.sin(rad)
            cv.d.ellipse(
                cv.box(x - r, y - r, x + r, y + r),
                fill=(255, 255, 255, 252),
                outline=hex2rgb(INK) + (240,),
                width=int(3.4 * SS),
            )
            hr = 5.0
            cv.d.ellipse(
                cv.box(x - hr, y - hr, x + hr, y + hr),
                fill=hex2rgb("#DCE4EC") + (255,),
            )
    elif kind == "crown":  # F-H：亮金刻线 + 3 颗大星 + 骑环皇冠
        _ring(cv, ["#FFE7A8", "#EFB03A"], thickness=7.0, r_out=125.0, outline_w=0, highlight=False)
        _ring_stars(cv, 3, r=band_r, size=30.0, color="#FFFFFF", outline=ring[1])
        _crown(cv)
    else:  # I+：金色内环 + 4 处大闪光 + 2 颗星（"光彩"感）
        _ring(cv, ["#FFF3C4", "#F3B93F"], thickness=7.5, r_out=125.0, outline_w=0, highlight=False)
        for x, y, r in (
            (CX - R_OUT - 26, CY - 74, 20),
            (CX + R_OUT + 22, CY + 66, 18),
            (CX - 132, CY + R_OUT + 14, 16),
            (CX + 96, CY - R_OUT - 10, 15),
        ):
            sparkle(cv, x, y, r, "#FFFFFF", 245)
        _ring_stars(cv, 2, r=band_r, size=32.0, color="#FFFFFF", outline=ring[4])


def frame_image(spec: dict) -> Image.Image:
    """画一枚框（透明底，FRAME_SIZE×FRAME_SIZE）——不含头像本身。"""
    cv = Canvas(FRAME_SIZE, FRAME_SIZE, {"top": "#FFFFFF", "bot": "#FFFFFF"}, transparent=True)
    _ring(cv, spec["ring"], thickness=spec.get("thickness", RING_W))
    _deco(cv, spec)
    return cv.img.resize((FRAME_SIZE, FRAME_SIZE), Image.LANCZOS)


def compose(spec: dict, avatar_id: str, size: int = FRAME_SIZE) -> Image.Image:
    """头像 + 框叠层（与前端叠层同几何：头像 256 居中于 440 画布）。"""
    frame = frame_image(spec)
    av = Image.open(os.path.join(AVATAR_DIR, f"{avatar_id}.png")).convert("RGBA")
    off = (FRAME_SIZE - AVATAR_SIZE) // 2
    frame.alpha_composite(av, (off, off))
    if size != FRAME_SIZE:
        frame = frame.resize((size, size), Image.LANCZOS)
    return frame


def manifest_js() -> str:
    """生成 miniapp 清单（与 4 张框同源）——前端唯一取框入口。"""
    rows = ",\n".join(
        "  {{ id: '{i}', label: '{l}', levels: '{v}', minLevel: {m},"
        " file: '/icons/frames/{i}.png' }}".format(
            i=f["id"], l=f["label"], v=f["levels"], m=f["min_level"]
        )
        for f in FRAMES
    )
    return (
        "// miniapp/utils/frames.js — 等级头像框清单（**生成物**，源 = scripts/gen_fix34_frames.py）\n"
        "// 叠层几何：框图 440×440、头像 256 居中 → 框显示尺寸 = 头像的 FRAME_SCALE，居中偏移 -35.94%\n"
        f"const FRAME_SCALE = {FRAME_SIZE} / {AVATAR_SIZE}\n\n"
        "const FRAMES = [\n"
        f"{rows},\n"
        "]\n\n"
        "// 等级字母（A-Z）→ 档位；未知/空等级按最低档（A 档）\n"
        "function frameForLevel(level) {\n"
        "  const idx = 'ABCDEFGHIJKLMNOPQRSTUVWXYZ'.indexOf(String(level || 'A').toUpperCase())\n"
        "  const n = idx >= 0 ? idx + 1 : 1\n"
        "  let hit = FRAMES[0]\n"
        "  FRAMES.forEach((f) => { if (n >= f.minLevel) hit = f })\n"
        "  return hit\n"
        "}\n\n"
        "module.exports = { FRAME_SCALE, FRAMES, frameForLevel }\n"
    )


def _sheet(out: str) -> str:
    """样图触板：上行大头像（看细节）/ 下行信息流真实尺寸（看档位可辨性）。

    行高按各行实际图高算（judge 二轮指出：固定行高会让小尺寸行出现大片空白）。
    """
    from PIL import ImageDraw

    pad, cols = 26, 4
    big, small = 300, 108  # small ≈ 信息流里框的实际显示尺寸
    cell_w = 300
    rows = [(big, "大头像效果（看细节）"), (small, "信息流真实尺寸（看档位可辨性）")]
    sheet_w = cols * cell_w + pad * (cols + 1)
    sheet_h = 96 + sum(im_h + 76 + pad for im_h, _ in rows)
    sheet = Image.new("RGB", (sheet_w, sheet_h), hex2rgb(PAPER))
    d = ImageDraw.Draw(sheet)
    d.text(
        (pad, 28),
        "fix34 R4 等级头像框样（四档：星芒 A-B / 银环 C-E / 金冠 F-H / 彩虹 I+）",
        font=font_cn(30),
        fill=hex2rgb(INK),
    )
    y = 96
    for r_i, (im_h, row_title) in enumerate(rows):
        d.text((pad, y - 26), row_title, font=font_cn(21), fill=hex2rgb(MUTED))
        # 小尺寸行统一用同一只头像：档位差异必须来自"框"本身，不能被换吉祥物掩盖
        av = "cat_sun" if r_i == 0 else "panda_sun"
        for c_i, spec in enumerate(FRAMES):
            im = compose(spec, av, FRAME_SIZE).resize((im_h, im_h), Image.LANCZOS)
            x = pad + c_i * (cell_w + pad)
            box = Image.new("RGB", (cell_w, im_h), hex2rgb("#FFFFFF"))
            box.paste(im, ((cell_w - im_h) // 2, 0), im)
            sheet.paste(box, (x, y))
            d.text(
                (x + cell_w // 2, y + im_h + 8),
                f"{spec['label']}（{spec['levels']}）",
                font=font_cn(21),
                fill=hex2rgb(INK),
                anchor="ma",
            )
        y += im_h + 76 + pad
    sheet.save(out, "PNG")
    return out


def main() -> None:
    import sys

    os.makedirs(OUT_SAMPLES, exist_ok=True)
    sheet = _sheet(os.path.join(OUT_SAMPLES, "01-frames.png"))
    print("fix34 R4 框样：")
    print(f"  触板 {sheet}")
    if "--write-assets" in sys.argv:
        os.makedirs(OUT_ASSETS, exist_ok=True)
        for spec in FRAMES:
            p = os.path.join(OUT_ASSETS, f"{spec['id']}.png")
            frame_image(spec).save(p, "PNG")
            print(f"  资产 {p}")
        with open(MANIFEST, "w", encoding="utf-8") as fh:
            fh.write(manifest_js())
        print(f"  清单 {MANIFEST}")
        print(json.dumps({"frames": [f["id"] for f in FRAMES]}, ensure_ascii=False))


if __name__ == "__main__":
    main()
