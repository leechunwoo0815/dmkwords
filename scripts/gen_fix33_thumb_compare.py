"""fix33 R1 自检生成器：三卡型「旧缩略图 / 新缩略图 / 完整版」对比图（judge 视觉门用）。

用法：python -m scripts.gen_fix33_thumb_compare
输出：uploads/wm15-samples/fix33-thumb-compare/（含 00-compare-sheet.png 触板）

旧规格实现是 **fix33 前的冻结副本**（`_old_thumb`）——只为对比取证，不参与生产；
新缩略图/完整版一律调用 `card_engine._render_thumb` / `_render_full`（真实管线）。
"""

from __future__ import annotations

import os

from PIL import Image, ImageDraw

from backend.domain.reading_circle import art, card_engine
from backend.domain.reading_circle.art import (
    INK,
    PALETTES,
    PAPER,
    Canvas,
    cloud,
    font_cn,
    font_round,
    hex2rgb,
    paper_grain,
    rainbow,
    sparkle,
    star,
    sticker_text,
)
from backend.domain.reading_circle.art_mascot import mascot as art_mascot

OUT = os.path.join(
    os.path.dirname(__file__), "..", "uploads", "wm15-samples", "fix33-thumb-compare"
)
W, H = card_engine.CARD_W, card_engine.CARD_H

# 三卡型样张（数据取 card_data 同构字段，文本与真帖同形）
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
    ),
]


def _old_thumb(path: str, card_data: dict) -> str:
    """fix33 前的缩略图实现（冻结副本，仅用于对比取证）。"""
    card_type = card_data["card_type"]
    pal = PALETTES[card_type]
    kind = card_engine.CARD_MASCOT[card_type]
    base = art.KIND_BASE[kind]
    cv = Canvas(W, H, pal)
    art.glow(cv, 375, 400, 260, "#FFFFFF", 120)
    cloud(cv, 128, 150, 150, "#FFFFFF", 190)
    cloud(cv, 630, 210, 118, "#FFFFFF", 160)
    rainbow(cv, 375, 1024, 168, 9.0, 150)
    for cx, cy, r in ((126, 592, 18), (628, 560, 15)):
        star(cv, cx, cy, r, "#FFFFFF", outline=pal["accent"], width=2.6, rotate=0.2)
    for cx, cy, r in ((238, 300, 14), (534, 288, 12), (300, 520, 11), (620, 700, 13)):
        sparkle(cv, cx, cy, r, "#FFFFFF", 235)
    sticker_text(
        cv,
        (375, 412),
        str(card_data.get("value_text", "")),
        font_round(168),
        pal["deep"],
        stroke="#FFFFFF",
        stroke_w=13,
    )
    art_mascot(cv, 375, 720, 104, kind=kind, fur=base["fur"], ear=base["ear"], blush=base["blush"])
    paper_grain(cv)
    return cv.finish(path)


def _sheet(rows: list[list[tuple[str, str]]], out: str) -> str:
    cell_w, pad, label_h = 300, 24, 40
    inner_w = cell_w - 16
    # 高度按**缩放后的宽度**同比例算：否则 3:4 卡片被横向挤压（judge 一轮指出的触板缺陷）
    cell_h = int(inner_w * H / W)
    cols = len(rows[0])
    sheet_w = cols * cell_w + pad * (cols + 1)
    sheet_h = 90 + len(rows) * (cell_h + label_h + pad)
    sheet = Image.new("RGB", (sheet_w, sheet_h), hex2rgb(PAPER))
    d = ImageDraw.Draw(sheet)
    d.text(
        (pad, 24),
        "fix33 R1 缩略图同构图自检（左→右：旧缩略图 / 新缩略图 / 完整版）",
        font=font_cn(28),
        fill=hex2rgb(INK),
    )
    y = 90
    for row in rows:
        for c_i, (p, label) in enumerate(row):
            im = Image.open(p).convert("RGBA").resize((inner_w, cell_h), Image.LANCZOS)
            x = pad + c_i * (cell_w + pad)
            bg = Image.new("RGB", im.size, hex2rgb("#FFFFFF"))
            bg.paste(im, (0, 0), im)
            sheet.paste(bg, (x + 8, y))
            d.text(
                (x + 8 + inner_w // 2, y + cell_h + 18),
                label,
                font=font_cn(22),
                fill=hex2rgb(INK),
                anchor="ma",
            )
        y += cell_h + label_h + pad
    sheet.save(out, "PNG")
    return out


def main() -> None:
    from backend.domain.reading_circle import art

    os.makedirs(OUT, exist_ok=True)
    rows = []
    for key, label, data in SAMPLES:
        pal = art.PALETTES[key]
        kind = card_engine.CARD_MASCOT[key]
        base = art.KIND_BASE[kind]
        old = _old_thumb(os.path.join(OUT, f"old-{key}.png"), data)
        # 复用真实管线：新版缩略图 + 完整版（同 tag 便于对拍）
        full = card_engine._render_full(data, pal, kind, base, OUT, "cmp")
        new = card_engine._render_thumb(data, pal, kind, base, OUT, "cmp")
        rows.append(
            [
                (old, f"{label} 旧缩略图"),
                (os.path.join(OUT, os.path.basename(new)), f"{label} 新缩略图"),
                (os.path.join(OUT, os.path.basename(full)), f"{label} 完整版"),
            ]
        )
    sheet = _sheet(rows, os.path.join(OUT, "00-compare-sheet.png"))
    print("fix33 R1 对比图：")
    print(f"  触板 {os.path.abspath(sheet)}")
    for row in rows:
        for p, label in row:
            print(f"  {label:16s} {os.path.abspath(p)}")


if __name__ == "__main__":
    main()
