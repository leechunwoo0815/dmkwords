"""fix34 R1-C 样图：三卡型「缩略图（裁切）/ 完整版 / 信息流观感」对比触板。

用法：python -m scripts.gen_fix34_cards
输出：uploads/fix34-samples/02-cards.png（+ 单张原图，供放大细看）

缩略图与完整版都走生产管线 `card_render.card_images`（**同一像素来源**）——取证图
与线上出的图同源，不是另画一张示意。
"""

from __future__ import annotations

import os

from PIL import Image, ImageDraw

from backend.domain.reading_circle import card_render
from backend.domain.reading_circle.art import INK, PAPER, font_cn, hex2rgb

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
OUT = os.path.join(ROOT, "uploads", "fix34-samples")
AVATAR_DIR = os.path.join(ROOT, "miniapp", "icons", "avatars")
MUTED = "#9A8C80"

SAMPLES = [
    (
        "milestone",
        "里程碑",
        {
            "card_type": "milestone",
            "label": "里程碑",
            "title": "里程碑达成",
            "value_text": "10 万",
            "value_label": "累计有效阅读词数",
            "english_name": "Tommy",
            "date": "2026-09-11",
        },
        "cat_sun",
        "Tommy妈妈",
    ),
    (
        "perfect_quiz",
        "满分",
        {
            "card_type": "perfect_quiz",
            "label": "测验满分",
            "title": "测验满分！",
            "value_text": "5/5",
            "value_label": "《Brown Bear》",
            "english_name": "Lisa",
            "date": "2026-09-11",
        },
        "bunny_sun",
        "Lisa妈妈",
    ),
    (
        "streak",
        "连击",
        {
            "card_type": "streak",
            "label": "连续打卡",
            "title": "连续打卡",
            "value_text": "30 天",
            "value_label": "每天阅读 坚持到底",
            "english_name": "Tommy",
            "date": "2026-09-11",
        },
        "panda_mint",
        "Tommy妈妈",
    ),
]


def _feed_mock(size: tuple[int, int], thumb: Image.Image, data: dict, avatar: str, parent: str):
    """信息流实际观感模拟（对齐 circle.wxml 结构：头像+名字+时间 / 原生文字 / 缩略图 / 点赞）。"""
    w, h = size
    row = Image.new("RGB", (w, h), hex2rgb("#FFFFFF"))
    d = ImageDraw.Draw(row)
    off = 16
    av = Image.open(os.path.join(AVATAR_DIR, f"{avatar}.png")).convert("RGBA").resize((56, 56))
    row.paste(av, (off, off), av)
    d.text((off + 68, off + 4), f"{data['english_name']}", font=font_cn(24), fill=hex2rgb(INK))
    d.text(
        (off + 68, off + 36),
        f"{parent} · {data['label']} · 3 小时前",
        font=font_cn(17),
        fill=hex2rgb(MUTED),
    )
    tx = off
    d.text((tx, off + 72), data["title"], font=font_cn(26), fill=hex2rgb(INK))
    d.text((tx, off + 110), data["value_label"], font=font_cn(19), fill=hex2rgb(MUTED))
    tw = int(w * 0.6)
    row.paste(thumb.resize((tw, tw), Image.LANCZOS), (tx, off + 146))
    # 点赞行（形状示意，不引 emoji 字体）
    ly = off + 146 + tw + 16
    for i in range(3):
        cx = tx + 14 + i * 22
        d.ellipse(
            (cx - 11, ly - 11, cx + 11, ly + 11), fill=hex2rgb("#FFD9C7"), outline=hex2rgb(INK)
        )
    d.text((tx + 76, ly - 11), "3 位小伙伴赞过", font=font_cn(18), fill=hex2rgb(MUTED))
    d.line((0, h - 1, w, h - 1), fill=hex2rgb("#F0E9E2"), width=1)
    return row


def _sheet(rows: list[tuple], out: str) -> str:
    pad, label_h = 24, 40
    col_w = [300, 366, 440]
    row_h = 560
    sheet_w = sum(col_w) + pad * 4
    sheet_h = 96 + len(rows) * (row_h + label_h + pad)
    sheet = Image.new("RGB", (sheet_w, sheet_h), hex2rgb(PAPER))
    d = ImageDraw.Draw(sheet)
    d.text(
        (pad, 30),
        "fix34 R1-C 缩略图同源自检（左→右：缩略图 / 完整版 / 信息流实际观感）",
        font=font_cn(30),
        fill=hex2rgb(INK),
    )
    y = 96
    for key, label, data, avatar, parent in rows:
        full, thumb = card_render.card_images(data)
        full.save(os.path.join(OUT, f"full-{key}.png"), "PNG")
        thumb.save(os.path.join(OUT, f"thumb-{key}.png"), "PNG")

        cells = []
        cells.append(thumb.resize((col_w[0] - 20, col_w[0] - 20), Image.LANCZOS))
        cells.append(full.resize((int(row_h * full.width / full.height), row_h), Image.LANCZOS))
        cells.append(_feed_mock((col_w[2] - 20, row_h), thumb, data, avatar, parent))

        x = pad
        for c_i, im in enumerate(cells):
            box_w, box_h = col_w[c_i] - 20, row_h
            bg = Image.new("RGB", (box_w, box_h), hex2rgb("#FFFFFF"))
            bg.paste(im, ((box_w - im.width) // 2, 0), im if im.mode == "RGBA" else None)
            sheet.paste(bg, (x + 10, y))
            x += col_w[c_i] + pad
        for c_i, name in enumerate((f"{label} 缩略图", f"{label} 完整版", f"{label} 信息流观感")):
            cx = pad + sum(col_w[:c_i]) + pad * c_i + (col_w[c_i] - 20) // 2
            d.text((cx, y + row_h + 18), name, font=font_cn(21), fill=hex2rgb(INK), anchor="ma")
        y += row_h + label_h + pad
    sheet.save(out, "PNG")
    return out


def main() -> None:
    os.makedirs(OUT, exist_ok=True)
    sheet = _sheet(SAMPLES, os.path.join(OUT, "02-cards.png"))
    print("fix34 R1-C 卡片样图：")
    print(f"  触板 {sheet}")
    for key, label, *_ in SAMPLES:
        print(
            f"  {label:4s} 缩略图/完整版 {os.path.join(OUT, f'thumb-{key}.png')} "
            f"{os.path.join(OUT, f'full-{key}.png')}"
        )


if __name__ == "__main__":
    main()
