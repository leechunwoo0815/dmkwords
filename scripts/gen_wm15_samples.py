"""WM15 样图门生成器：3 卡 + 3 头像 + 3 勋章 = 9 样（+ 1 无字缩略图对比 + 1 触板）。

用法：python -m scripts.gen_wm15_samples
输出：uploads/wm15-samples/（过门后并入 scripts/gen_wm15_visuals.py 全量资产）

样图门纪律（专家裁决 D2）：过用户目视前**禁止批量绘制 34 张图**——先 9 样定风格。

排版纪律（judge 二轮返工结论）：
- 装饰只放**安全边距**（y<170 顶部带 / x<60、x>690 左右窄条），绝不与卡片框交叠，杜绝裁切
- 卡片内：主数字用 deep 深色（浅色压白底不可读）+ 吉祥物**放在卡内**撑住下半（消除空带）
- 三张卡的标题胶囊统一 accent 填充 + 白字（首版卡 1 用 deep 描边导致同批不一致）
"""

from __future__ import annotations

import math
import os

from PIL import Image, ImageDraw

from backend.domain.reading_circle.art import (
    INK,
    PALETTES,
    PAPER,
    Canvas,
    bubble,
    cloud,
    flame_outline,
    font_cn,
    font_round,
    glow,
    hex2rgb,
    mascot,
    rainbow,
    soft_shadow,
    paper_grain,
    sparkle,
    star,
    sticker_pair,
    sticker_text,
)

OUT = os.path.join(os.path.dirname(__file__), "..", "uploads", "wm15-samples")
W, H = 750, 1000  # 卡片规格（与现有卡片同尺寸，便于替换）

# 安全边距：卡片框（66,176,684,770）之外才允许放页面级装饰
CARD = (66, 176, 684, 770)


def _page_decor(cv: Canvas, pal: dict) -> None:
    """页面级装饰：只落在顶部带与左右窄条（绝不压卡片框/页脚）。"""
    glow(cv, 628, 92, 165, "#FFFFFF", 100)
    glow(cv, 120, 78, 120, "#FFFFFF", 70)
    cloud(cv, 112, 104, 128, "#FFFFFF", 205)
    cloud(cv, 646, 116, 96, "#FFFFFF", 165)
    # 左右窄条（x<60 / x>690）
    star(cv, 40, 330, 11, "#FFFFFF", outline=pal["accent"], width=2.0, rotate=0.3)
    star(cv, 710, 566, 10, "#FFFFFF", outline=pal["accent"], width=1.8, rotate=-0.2)
    star(cv, 36, 706, 9, "#FFFFFF", outline=pal["accent"], width=1.6, rotate=0.1)
    sparkle(cv, 28, 466, 12, "#FFFFFF", 225)
    sparkle(cv, 722, 258, 11, "#FFFFFF", 215)
    sparkle(cv, 718, 792, 10, "#FFFFFF", 200)


def _card(
    path: str,
    key: str,
    title: str,
    big: str,
    line1: str,
    line2: str,
    *,
    kind: str,
    fur: str,
    ear: str | None,
    blush: str = "#FF9CB4",
) -> str:
    """卡片版式（三张卡共用，保证同批完全一致）。"""
    pal = PALETTES[key]
    cv = Canvas(W, H, pal)
    _page_decor(cv, pal)

    # 标题胶囊：统一 accent 填充 + 白字
    bubble(cv, (196, 64, 554, 146), radius=41, fill=pal["accent"], outline=None)
    sticker_text(cv, (375, 105), title, font_cn(44), "#FFFFFF", stroke="#FFFFFF00", stroke_w=0)

    # 卡面（场景卡）
    soft_shadow(cv, CARD, radius=46, blur=12, alpha=58)
    bubble(cv, CARD, radius=46, fill=PAPER, outline=pal["accent"], width=6)
    glow(cv, 375, 340, 190, "#FFFFFF", 90)

    # 主数字（deep 深色 + 白描边 = 贴纸数字）；"7 天"这类数字+量词整体居中
    num, _, unit = big.partition(" ")
    if unit:
        sticker_pair(cv, (375, 330), num, font_round(150), unit, font_cn(74), pal["deep"], stroke="#FFFFFF", stroke_w=10, dy_unit=26)
    else:
        sticker_text(cv, (375, 330), big, font_round(142), pal["deep"], stroke="#FFFFFF", stroke_w=9)
    sticker_text(cv, (375, 462), line1, font_cn(36), INK)
    sticker_text(cv, (375, 518), line2, font_cn(32), pal["deep"])

    # 吉祥物放在卡内左下沉底（消除下半空带）
    mascot(cv, 190, 650, 82, kind=kind, fur=fur, ear=ear, blush=blush)
    # 卡内右侧装饰（x<=656 / y<=742，留 28px 安全边）
    star(cv, 520, 636, 26, "#FFE08A", outline=pal["accent"], width=3.2, rotate=0.22)
    star(cv, 604, 700, 17, "#FFF3C4", outline=pal["accent"], width=2.4, rotate=-0.24)
    sparkle(cv, 486, 566, 15, "#FFFFFF", 235)
    sparkle(cv, 636, 628, 12, "#FFFFFF", 220)

    # 署名 + 页脚 + branding
    bubble(cv, (268, 800, 482, 856), radius=28, fill=PAPER, outline=pal["deep"], width=4)
    sticker_text(cv, (375, 829), "小朋友002", font_cn(28), INK)
    bubble(cv, (48, 876, 702, 936), radius=26, fill=pal["accent"], outline=None)
    sticker_text(cv, (375, 907), "2026-09-11 · 保存分享这份成长", font_cn(26), "#FFFFFF")
    sticker_text(cv, (375, 966), "DmkWords 少儿英语阅读馆", font_cn(23), INK)
    paper_grain(cv)
    return cv.finish(path)


def card_thumb(path: str, key: str, big: str, *, kind: str, fur: str, ear: str | None, blush: str = "#FF9CB4") -> str:
    """无字纯图版（信息流小图专用）：插画 + 主数字，无任何文字。"""
    pal = PALETTES[key]
    cv = Canvas(W, H, pal)
    glow(cv, 375, 400, 260, "#FFFFFF", 120)
    cloud(cv, 128, 150, 150, "#FFFFFF", 190)
    cloud(cv, 630, 210, 118, "#FFFFFF", 160)
    rainbow(cv, 375, 1024, 168, 9.0, 150)
    for cx, cy, r in ((126, 592, 18), (628, 560, 15)):
        star(cv, cx, cy, r, "#FFFFFF", outline=pal["accent"], width=2.6, rotate=0.2)
    for cx, cy, r in ((238, 300, 14), (534, 288, 12), (300, 520, 11), (620, 700, 13)):
        sparkle(cv, cx, cy, r, "#FFFFFF", 235)
    sticker_text(cv, (375, 412), big, font_round(168), pal["deep"], stroke="#FFFFFF", stroke_w=13)
    mascot(cv, 375, 720, 104, kind=kind, fur=fur, ear=ear, blush=blush)
    paper_grain(cv)
    return cv.finish(path)


# ---------------- 头像（24 枚中的 3 枚代表） ----------------


def avatar(path: str, *, kind: str, fur: str, ear: str | None, coin: str, blush: str = "#FF9CB4", size: int = 256) -> str:
    cv = Canvas(size, size, {"top": coin, "bot": coin}, transparent=True)
    r = size * 0.355
    cx, cy = size * 0.5, size * 0.545
    # 圆币底
    cv.d.ellipse(cv.box(cx - r * 1.26, cy - r * 1.26, cx + r * 1.26, cy + r * 1.26), fill=hex2rgb(coin) + (255,), outline=hex2rgb(INK) + (255,), width=int(size * 0.032 * 3))
    cv.d.arc(cv.box(cx - r * 1.06, cy - r * 1.06, cx + r * 1.06, cy + r * 1.06), start=196, end=330, fill=(255, 255, 255, 120), width=int(size * 0.021 * 3))
    mascot(cv, cx, cy, r * 0.95, kind=kind, fur=fur, ear=ear, blush=blush)
    return cv.finish(path)


# ---------------- 勋章（9 枚中的 3 枚代表） ----------------


def badge_milestone(path: str, size: int = 256) -> str:
    cv = Canvas(size, size, {"top": "#FFF6D8", "bot": "#FFE0C7"}, transparent=True)
    cx = cy = size * 0.5
    r = size * 0.34
    for sx in (-1, 1):
        cv.d.polygon(
            [cv.p(cx + sx * r * 0.16, cy + r * 0.70), cv.p(cx + sx * r * 0.74, cy + r * 1.18), cv.p(cx + sx * r * 0.36, cy + r * 1.26), cv.p(cx + sx * r * 0.02, cy + r * 0.86)],
            fill=hex2rgb("#F2765E") + (255,), outline=hex2rgb(INK) + (255,), width=int(size * 0.014 * 3),
        )
    cv.d.ellipse(cv.box(cx - r, cy - r, cx + r, cy + r), fill=hex2rgb("#FFD166") + (255,), outline=hex2rgb(INK) + (255,), width=int(size * 0.026 * 3))
    cv.d.ellipse(cv.box(cx - r * 0.84, cy - r * 0.84, cx + r * 0.84, cy + r * 0.84), fill=hex2rgb("#FFF6D8") + (255,))
    star(cv, cx, cy, r * 0.62, "#FFC93C", outline=INK, width=size * 0.014, rotate=0.0)
    paper_grain(cv, 12)
    return cv.finish(path)


def badge_level(path: str, letter: str = "A", size: int = 256) -> str:
    pal = PALETTES["level_up"]
    cv = Canvas(size, size, pal, transparent=True)
    cx, cy = size * 0.5, size * 0.47
    w, h = size * 0.34, size * 0.375

    def pentagon(scale: float) -> list:
        # 统一按同一组基准点整体缩放 → 内外圈等宽，不会左右不对称
        base = [(0.0, -1.15), (1.0, -0.60), (0.72, 0.70), (0.0, 1.22), (-0.72, 0.70), (-1.0, -0.60)]
        return [cv.p(cx + bx * w * scale, cy + by * h * scale) for bx, by in base]

    cv.d.polygon(pentagon(1.0), fill=hex2rgb(pal["accent"]) + (255,), outline=hex2rgb(INK) + (255,), width=int(size * 0.026 * 3))
    cv.d.polygon(pentagon(0.84), fill=hex2rgb("#E9F3FF") + (255,))
    sticker_text(cv, (cx, cy + h * 0.06), letter, font_round(int(size * 0.42)), pal["deep"], stroke="#FFFFFF", stroke_w=size * 0.022)
    paper_grain(cv, 12)
    return cv.finish(path)


def badge_streak(path: str, size: int = 256) -> str:
    """连续打卡 = 火焰（贝塞尔轮廓：尖顶/S 收腰/圆底）+ 火焰上的可爱脸。"""
    cv = Canvas(size, size, {"top": "#EAFBF8", "bot": "#D3F0EC"}, transparent=True)
    cx, cy = size * 0.5, size * 0.545
    w, h = size * 0.30, size * 0.375

    def draw_flame(scale: float, fill: str, outline: str | None, lw: float) -> None:
        pts = [cv.p(x, cy + (y - cy) * scale) for x, y in flame_outline(cx, cy, w, h)]
        cv.d.polygon(pts, fill=hex2rgb(fill) + (255,), outline=hex2rgb(outline) + (255,) if outline else None, width=int(lw * 3) if outline else 0)

    draw_flame(1.0, "#FF9E7A", INK, size * 0.024)
    draw_flame(0.60, "#FFD98A", None, 0)
    # 可爱脸（与吉祥物同一视觉语言）
    for sx in (-1, 1):
        ex, ey = cx + sx * size * 0.052, cy + size * 0.020
        cv.d.ellipse(cv.box(ex - size * 0.017, ey - size * 0.020, ex + size * 0.017, ey + size * 0.020), fill=hex2rgb(INK) + (255,))
    cv.d.arc(cv.box(cx - size * 0.040, cy + size * 0.030, cx + size * 0.040, cy + size * 0.086), start=20, end=160, fill=hex2rgb(INK) + (255,), width=int(size * 0.012 * 3))
    for sx in (-1, 1):
        bx = cx + sx * size * 0.088
        cv.d.ellipse(cv.box(bx - size * 0.028, cy + size * 0.036, bx + size * 0.028, cy + size * 0.078), fill=(255, 156, 180, 150))
    sparkle(cv, size * 0.13, size * 0.87, size * 0.048, "#FFFFFF", 220)
    paper_grain(cv, 12)
    return cv.finish(path)


def contact_sheet(paths: list[tuple[str, str]], out: str) -> str:
    cols, pad = 3, 26
    cell_w = 300
    rows = [paths[0:3], paths[3:6], paths[6:9]]
    sheet_w = cols * cell_w + pad * (cols + 1)
    row_h = [int(cell_w * H / W) + 44, cell_w + 44, cell_w + 44]
    sheet_h = 96 + sum(h + pad for h in row_h)
    sheet = Image.new("RGB", (sheet_w, sheet_h), hex2rgb(PAPER))
    d = ImageDraw.Draw(sheet)
    d.text((pad, 26), "WM15 样图门 · 9 样（3 卡 / 3 头像 / 3 勋章）", font=font_cn(34), fill=hex2rgb(INK))
    y = 96
    for r_i, row in enumerate(rows):
        for c_i, (p, label) in enumerate(row):
            im = Image.open(p).convert("RGBA")
            box_w = cell_w - 20
            box_h = int(box_w * im.height / im.width) if r_i == 0 else box_w
            im = im.resize((box_w, box_h), Image.LANCZOS)
            x = pad + c_i * (cell_w + pad)
            bg = Image.new("RGB", (box_w, box_h), hex2rgb("#FFFFFF"))
            bg.paste(im, (0, 0), im)
            sheet.paste(bg, (x + 10, y))
            d.text((x + 10 + box_w // 2, y + box_h + 20), label, font=font_cn(24), fill=hex2rgb(INK), anchor="ma")
        y += row_h[r_i] + pad
    sheet.save(out, "PNG")
    return out


def main() -> None:
    os.makedirs(OUT, exist_ok=True)
    made: list[tuple[str, str]] = []
    made.append((_card(os.path.join(OUT, "01-card-milestone.png"), "milestone", "里程碑达成", "100,000", "累计有效阅读词数", "10 万词 达成！", kind="cat", fur="#FFC98F", ear="#FFB472"), "卡1 里程碑"))
    made.append((_card(os.path.join(OUT, "02-card-quiz.png"), "perfect_quiz", "测验满分！", "5 / 5", "《Brown Bear》", "全部答对 太厉害啦", kind="bunny", fur="#FFFFFF", ear="#FFE3EC"), "卡2 测验满分"))
    made.append((_card(os.path.join(OUT, "03-card-streak.png"), "streak", "连续打卡", "7 天", "每天阅读 坚持到底", "第 1 次连续达标", kind="panda", fur="#FFFFFF", ear="#3B3B3B"), "卡3 连续打卡"))
    made.append((avatar(os.path.join(OUT, "04-avatar-cat-orange.png"), kind="cat", fur="#FFC98F", ear="#FFB472", coin="#FFF0DC"), "头像 猫"))
    made.append((avatar(os.path.join(OUT, "05-avatar-panda.png"), kind="panda", fur="#FFFFFF", ear="#3B3B3B", coin="#CDE7DE"), "头像 熊猫"))
    made.append((avatar(os.path.join(OUT, "06-avatar-bunny.png"), kind="bunny", fur="#FFFFFF", ear="#FFE3EC", coin="#FDEBF3"), "头像 兔"))
    made.append((badge_milestone(os.path.join(OUT, "07-badge-milestone.png")), "勋章 里程碑"))
    made.append((badge_level(os.path.join(OUT, "08-badge-level.png"), "A"), "勋章 等级"))
    made.append((badge_streak(os.path.join(OUT, "09-badge-streak.png")), "勋章 打卡"))
    sheet = contact_sheet(made, os.path.join(OUT, "00-contact-sheet.png"))
    extra = card_thumb(os.path.join(OUT, "10-card-milestone-thumb.png"), "milestone", "100,000", kind="cat", fur="#FFC98F", ear="#FFB472")
    print("样图门 9 样 + 触板 + 无字缩略图：")
    for p, label in made:
        print(f"  {label:14s} {os.path.abspath(p)}")
    print(f"  {'触板':14s} {os.path.abspath(sheet)}")
    print(f"  {'无字缩略图':14s} {os.path.abspath(extra)}")


if __name__ == "__main__":
    main()
