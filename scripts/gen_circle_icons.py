"""一次性生成阅读圈 tabBar 图标（星星+圆环意象，96x96 PNG 两套——普通/选中）。
仿现有 8 张绘本风 tabBar 图标规格。用法：python -m scripts.gen_circle_icons
"""

from __future__ import annotations

import os

from PIL import Image, ImageDraw

OUT_DIR = os.path.join(os.path.dirname(__file__), "..", "miniapp", "icons")


def _hex(x: str):
    x = x.lstrip("#")
    return tuple(int(x[i : i + 2], 16) for i in (0, 2, 4))


def star_points(cx: int, cy: int, r: int):
    import math

    return [
        (cx + r * math.cos(a), cy + r * math.sin(a))
        for a in [k * math.pi / 5 - math.pi / 2 for k in range(10)]
    ]


def make_icon(color: str, ring_color: str, path: str) -> None:
    W = H = 96
    img = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    # 圆环（阅读圈意象）
    d.ellipse((10, 12, 86, 88), outline=_hex(ring_color) + (255,), width=7)
    # 五角星居中
    d.polygon(star_points(48, 50, 26), fill=_hex(color) + (255,))
    img.save(path, "PNG")
    print(f"saved {path}")


def main() -> None:
    os.makedirs(OUT_DIR, exist_ok=True)
    # 普通：淡墨棕描边+暖灰星（未选中态，与现有 4 套普通态同调）
    make_icon("#6B5B5B", "#6B5B5B", os.path.join(OUT_DIR, "circle.png"))
    # 选中：活力橙星+橙描边（tabBar selectedColor #FF6B35 同源）
    make_icon("#FF6B35", "#FF6B35", os.path.join(OUT_DIR, "circle-active.png"))


if __name__ == "__main__":
    main()
