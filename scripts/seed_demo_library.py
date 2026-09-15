# scripts/seed_demo_library.py — 演示书库扩容（36 本 + 绘本风封面 + 每本 5 题）
"""小程序美化批次的测试数据底座：
- 30 本新增经典童书（+ seed_wm11_demo 已有 6 本 = 36 本），覆盖 4 档适读年级 / AR 1.2-5.2 /
  词数 80-6000 / 8 类主题，支撑图书馆筛选、书架、详情、测验全链路视觉验收（2000 本规模预演）。
- Pillow 生成绘本风封面图（uploads/cover/{isbn前4}/），封面路径入 book.cover_path。
- 每本书 5 道测验题（4 单选 + 1 判断，书名嵌入题干），满足上架强校验的测验题≥5 口径。
- 音频复用 uploads 现有 6 个演示音频。按 ISBN 幂等可重跑。
用法：python -m scripts.seed_demo_library
"""

import os
import secrets

from backend.common.file_storage import _mp3_duration
from backend.database import SessionLocal
from backend.domain.catalog.models import Book, BookCopy, QuizQuestion

AUDIO_ISBNS = [f"97820000000{i:02d}" for i in range(1, 7)]

# 12 组绘本风配色：(底色, 装饰主色, 装饰辅色, 标题字色, 作者字色)
PALETTES = [
    ("#FF8A5C", "#FFD166", "#4ADE80", "#FFFFFF", "#FFF3E6"),
    ("#4ADE80", "#FFD166", "#FF6B35", "#1F4733", "#14532D"),
    ("#FFD166", "#FF6B35", "#60A5FA", "#6B4A12", "#7C5A1A"),
    ("#60A5FA", "#FCD34D", "#FFFFFF", "#FFFFFF", "#E0F0FF"),
    ("#F472B6", "#FCD34D", "#FFFFFF", "#7A1F43", "#8E2A50"),
    ("#A78BFA", "#FCD34D", "#4ADE80", "#FFFFFF", "#EFE9FF"),
    ("#FCD34D", "#FF6B35", "#4ADE80", "#6B4A12", "#7C5A1A"),
    ("#5EEAD4", "#FF6B35", "#FCD34D", "#0F4C43", "#14665B"),
    ("#FFA8A8", "#60A5FA", "#FCD34D", "#6B1D1D", "#7E2A2A"),
    ("#94A3B8", "#FCD34D", "#FF6B35", "#1E293B", "#334155"),
    ("#C4B5FD", "#FF6B35", "#FCD34D", "#3730A3", "#4338CA"),
    ("#86EFAC", "#FF6B35", "#60A5FA", "#14532D", "#166534"),
]

TOPICS = [
    "韵文启蒙",
    "自然认知",
    "想象力",
    "幽默桥梁书",
    "奇幻章节书",
    "科普百科",
    "成长故事",
    "侦探冒险",
]
GRADES = [
    "5-6岁（幼儿园大班）",
    "7-8岁（小学低年级）",
    "9-10岁（小学中年级）",
    "11-12岁（小学高年级）",
]

# (isbn, title, author, word_count, ar, grade_idx, topic, audio_isbn)
NEW_BOOKS = [
    (
        "9780399255632",
        "Brown Bear, Brown Bear, What Do You See?",
        "Bill Martin Jr",
        120,
        "1.4",
        0,
        "韵文启蒙",
    ),
    ("9780679882817", "Chicka Chicka Boom Boom", "Bill Martin Jr", 150, "1.9", 0, "韵文启蒙"),
    ("9780670862398", "Corduroy", "Don Freeman", 280, "3.2", 1, "成长故事"),
    ("9780670013868", "The Snowy Day", "Ezra Jack Keats", 180, "2.5", 0, "自然认知"),
    (
        "9780064440219",
        "If You Give a Mouse a Cookie",
        "Laura Numeroff",
        260,
        "2.7",
        0,
        "幽默桥梁书",
    ),
    ("9780064440202", "Frog and Toad Are Friends", "Arnold Lobel", 480, "2.9", 1, "成长故事"),
    (
        "9780679824114",
        "The Magic School Bus Inside the Earth",
        "Joanna Cole",
        900,
        "3.7",
        2,
        "科普百科",
    ),
    (
        "9780679823766",
        "Dinosaurs Before Dark (Magic Tree House)",
        "Mary Pope Osborne",
        2400,
        "2.6",
        1,
        "奇幻章节书",
    ),
    (
        "9780375811004",
        "Junie B. Jones and the Stupid Smelly Bus",
        "Barbara Park",
        5200,
        "2.9",
        1,
        "幽默桥梁书",
    ),
    ("9780440418150", "Nate the Great", "Marjorie Weinman Sharmat", 1200, "2.0", 1, "侦探冒险"),
    (
        "9780590426263",
        "The Boxcar Children",
        "Gertrude Chandler Warner",
        5400,
        "3.9",
        2,
        "侦探冒险",
    ),
    ("9780142406869", "Flat Stanley", "Jeff Brown", 4800, "4.0", 2, "幽默桥梁书"),
    (
        "9780147513183",
        "Cam Jansen and the Mystery of the Stolen Diamonds",
        "David A. Adler",
        3400,
        "3.2",
        1,
        "侦探冒险",
    ),
    ("9780316109212", "Arthur's Eyes", "Marc Brown", 400, "2.4", 0, "成长故事"),
    ("9780547076734", "Curious George", "H. A. Rey", 380, "2.6", 0, "幽默桥梁书"),
    ("9780545218033", "Clifford the Big Red Dog", "Norman Bridwell", 150, "1.8", 0, "成长故事"),
    ("9780064440103", "Amelia Bedelia", "Peggy Parish", 950, "2.5", 1, "幽默桥梁书"),
    ("9780380709582", "Ramona the Pest", "Beverly Cleary", 9800, "5.1", 3, "成长故事"),
    ("9780440491054", "Stuart Little", "E. B. White", 7800, "5.2", 3, "奇幻章节书"),
    ("9780142410347", "The BFG", "Roald Dahl", 11800, "4.8", 3, "奇幻章节书"),
    ("9780142410385", "Matilda", "Roald Dahl", 12100, "5.0", 3, "成长故事"),
    (
        "9780142410323",
        "Charlie and the Chocolate Factory",
        "Roald Dahl",
        9700,
        "4.7",
        2,
        "奇幻章节书",
    ),
    ("9780147512582", "Pippi Longstocking", "Astrid Lindgren", 8900, "4.6", 2, "幽默桥梁书"),
    ("9781419746180", "Diary of a Wimpy Kid", "Jeff Kinney", 12000, "5.2", 3, "幽默桥梁书"),
    ("9780545175222", "Captain Underpants", "Dav Pilkey", 8600, "4.3", 2, "幽默桥梁书"),
    ("9780061992254", "The One and Only Ivan", "Katherine Applegate", 9000, "4.4", 2, "成长故事"),
    ("9780763644321", "Because of Winn-Dixie", "Kate DiCamillo", 9200, "4.5", 2, "成长故事"),
    (
        "9780142412433",
        "The Mouse and the Motorcycle",
        "Beverly Cleary",
        7200,
        "4.4",
        2,
        "奇幻章节书",
    ),
    (
        "9780142414376",
        "Little House in the Big Woods",
        "Laura Ingalls Wilder",
        10400,
        "4.9",
        3,
        "成长故事",
    ),
    (
        "9780439064873",
        "Harry Potter and the Sorcerer's Stone",
        "J.K. Rowling",
        15500,
        "5.2",
        3,
        "奇幻章节书",
    ),
]

TOPIC_TO_HUE_NOTE = {
    "韵文启蒙": "rhythm",
    "自然认知": "nature",
    "想象力": "imagine",
    "幽默桥梁书": "funny",
    "奇幻章节书": "fantasy",
    "科普百科": "science",
    "成长故事": "growth",
    "侦探冒险": "mystery",
}


def _hex(c: str) -> tuple[int, int, int]:
    c = c.lstrip("#")
    return (int(c[0:2], 16), int(c[2:4], 16), int(c[4:6], 16))


def _wrap(draw, text, font, max_w):
    """词级换行：英文书名不拆词（2026-09-13 修复封面标题 'th e' 断词）；
    仅当单个词超行宽时按字符硬切。"""
    lines, cur = [], ""
    for word in text.split(" "):
        if not word:
            continue
        candidate = f"{cur} {word}" if cur else word
        if draw.textlength(candidate, font=font) <= max_w:
            cur = candidate
            continue
        if cur:
            lines.append(cur)
            cur = ""
        for ch in word:
            if cur and draw.textlength(cur + ch, font=font) > max_w:
                lines.append(cur)
                cur = ""
            cur += ch
    if cur:
        lines.append(cur)
    return lines


COVER_W, COVER_H = 600, 900
# aspectFill 裁切安全带（2026-09-15）：各展示位 ratio 不同，露出的纵向区间不同——
#   图书馆方格 ≈1:1 → 可见 y≈144..756      书架/书籍详情缩略图 ≈0.82 → 可见 y≈90..810
#   活动轮播 2.46:1 → 只露中间 y≈282..617
# 书名只准落在本题安全区内，保证任何展示位都不被裁成半个字。
COVER_SAFE_TOP, COVER_SAFE_BOTTOM = 246, 612
# 活动轮播横条的**真实**可见带（2026-09-15 实测）：banner 在卡片内宽 526px、
# 高 187px → ratio 2.81（不是按 750rpx 整宽估的 2.46）。可见高度只有 213px，
# 即原图 y≈343..557。热气球/飞鸟只放这里，否则会被裁掉半截。
COVER_BANNER_TOP, COVER_BANNER_BOTTOM = 350, 550

_FONT_ROUND = "/System/Library/Fonts/Supplemental/Arial Rounded Bold.ttf"
_FONT_CJK = "/System/Library/Fonts/Hiragino Sans GB.ttc"


def _mix(c1: tuple, c2: tuple, t: float) -> tuple[int, int, int]:
    return tuple(round(a + (b - a) * t) for a, b in zip(c1, c2, strict=True))


def _tint(c: tuple, t: float) -> tuple[int, int, int]:
    """t>1 往暖白走（变浅），t<1 往黑走（变深）。"""
    return _mix(c, (255, 253, 247), t - 1) if t > 1 else _mix((0, 0, 0), c, t)


def _sky_gradient(img, c_top: tuple, c_bottom: tuple, y0: int, y1: int) -> None:
    from PIL import ImageDraw

    d = ImageDraw.Draw(img)
    h = max(y1 - y0, 1)
    for i in range(h):
        d.line([(0, y0 + i), (img.width, y0 + i)], fill=_mix(c_top, c_bottom, i / h))


def _draw_sun(d, cx: float, cy: float, r: float, col: tuple) -> None:
    d.ellipse((cx - r * 1.45, cy - r * 1.45, cx + r * 1.45, cy + r * 1.45), fill=col + (56,))
    d.ellipse((cx - r, cy - r, cx + r, cy + r), fill=col)


def _draw_cloud(d, cx: float, cy: float, s: float, fill: tuple) -> None:
    for dx, dy, r in (
        (-1.45, 0.16, 0.60),
        (-0.55, -0.30, 0.84),
        (0.48, -0.06, 0.70),
        (1.38, 0.20, 0.50),
    ):
        d.ellipse(
            (cx + (dx - r) * s, cy + (dy - r) * s, cx + (dx + r) * s, cy + (dy + r) * s), fill=fill
        )
    d.rounded_rectangle(
        (cx - 1.9 * s, cy - 0.06 * s, cx + 1.88 * s, cy + 0.60 * s), max(2, int(0.3 * s)), fill=fill
    )


def _draw_tree(d, x: float, base_y: float, s: float, trunk: tuple, lo: tuple, hi: tuple) -> None:
    d.rounded_rectangle(
        (x - 0.10 * s, base_y - 0.95 * s, x + 0.10 * s, base_y), max(2, int(0.08 * s)), fill=trunk
    )
    d.ellipse((x - 0.58 * s, base_y - 1.44 * s, x + 0.58 * s, base_y - 0.48 * s), fill=lo)
    d.ellipse((x - 0.46 * s, base_y - 1.82 * s, x + 0.46 * s, base_y - 0.96 * s), fill=hi)
    d.ellipse((x - 0.29 * s, base_y - 2.12 * s, x + 0.29 * s, base_y - 1.50 * s), fill=lo)


def _draw_house(
    d, x: float, base_y: float, s: float, wall: tuple, roof: tuple, door: tuple
) -> None:
    d.rounded_rectangle(
        (x - 0.56 * s, base_y - 0.64 * s, x + 0.56 * s, base_y), max(2, int(0.1 * s)), fill=wall
    )
    d.polygon(
        [
            (x - 0.72 * s, base_y - 0.56 * s),
            (x, base_y - 1.18 * s),
            (x + 0.72 * s, base_y - 0.56 * s),
        ],
        fill=roof,
    )
    d.rounded_rectangle(
        (x - 0.15 * s, base_y - 0.42 * s, x + 0.15 * s, base_y), max(2, int(0.06 * s)), fill=door
    )


def _draw_balloon(d, cx: float, cy: float, r: float, c1: tuple, c2: tuple, c3: tuple) -> None:
    d.ellipse((cx - r, cy - r * 1.12, cx + r, cy + r * 0.86), fill=c1)
    d.ellipse((cx - r * 0.46, cy - r * 1.12, cx + r * 0.46, cy + r * 0.86), fill=c2)
    d.polygon(
        [
            (cx - r * 0.30, cy + r * 0.70),
            (cx + r * 0.30, cy + r * 0.70),
            (cx + r * 0.15, cy + r * 1.00),
            (cx - r * 0.15, cy + r * 1.00),
        ],
        fill=c2,
    )
    for sx in (-1, 1):
        d.line(
            [(cx + sx * r * 0.20, cy + r * 0.98), (cx + sx * r * 0.17, cy + r * 1.30)],
            fill=c3,
            width=3,
        )
    d.rounded_rectangle((cx - r * 0.21, cy + r * 1.26, cx + r * 0.21, cy + r * 1.60), 5, fill=c3)


def _draw_birds(d, x: float, y: float, s: float, col: tuple) -> None:
    for dx, dy, k in ((0.0, 0.0, 1.0), (s * 1.6, -s * 0.55, 0.74), (s * 2.9, s * 0.28, 0.58)):
        cxx, cyy, r = x + dx, y + dy, s * k
        d.line(
            [(cxx - r, cyy), (cxx, cyy - r * 0.60), (cxx + r, cyy)],
            fill=col,
            width=3,
            joint="curve",
        )


def _draw_buddy(
    d, cx: float, base_y: float, s: float, kind: str, body: tuple, muzzle: tuple
) -> None:
    """正脸小动物（s=头部半径基准，base_y=脚底）。三种造型轮换避免封面千篇一律。"""
    ink = (58, 44, 38)
    blush = (242, 150, 152, 120)
    head_c = base_y - 1.62 * s

    if kind == "bear":
        for sx in (-1, 1):
            d.ellipse(
                (
                    cx + sx * 0.60 * s - 0.26 * s,
                    head_c - 0.58 * s - 0.26 * s,
                    cx + sx * 0.60 * s + 0.26 * s,
                    head_c - 0.58 * s + 0.26 * s,
                ),
                fill=body,
            )
            d.ellipse(
                (
                    cx + sx * 0.60 * s - 0.12 * s,
                    head_c - 0.58 * s - 0.12 * s,
                    cx + sx * 0.60 * s + 0.12 * s,
                    head_c - 0.58 * s + 0.12 * s,
                ),
                fill=muzzle,
            )
    elif kind == "bunny":
        for sx in (-1, 1):
            d.ellipse(
                (
                    cx + sx * 0.34 * s - 0.20 * s,
                    head_c - 2.05 * s,
                    cx + sx * 0.34 * s + 0.20 * s,
                    head_c - 0.42 * s,
                ),
                fill=body,
            )
            d.ellipse(
                (
                    cx + sx * 0.34 * s - 0.10 * s,
                    head_c - 1.90 * s,
                    cx + sx * 0.34 * s + 0.10 * s,
                    head_c - 0.58 * s,
                ),
                fill=muzzle,
            )
    else:  # duck
        d.polygon(
            [
                (cx - 0.46 * s, head_c + 0.18 * s),
                (cx + 0.46 * s, head_c + 0.18 * s),
                (cx, head_c + 0.66 * s),
            ],
            fill=(247, 168, 62),
        )

    # 身体 + 手脚
    d.ellipse((cx - 0.76 * s, head_c + 0.66 * s, cx + 0.76 * s, base_y), fill=body)
    d.ellipse((cx - 0.42 * s, head_c + 0.92 * s, cx + 0.42 * s, base_y - 0.06 * s), fill=muzzle)
    for sx in (-1, 1):
        d.ellipse(
            (
                cx + sx * 0.80 * s - 0.20 * s,
                head_c + 1.02 * s,
                cx + sx * 0.80 * s + 0.20 * s,
                head_c + 1.68 * s,
            ),
            fill=body,
        )
    # 头 + 眼 + 腮红
    d.ellipse((cx - 0.82 * s, head_c - 0.84 * s, cx + 0.82 * s, head_c + 0.84 * s), fill=body)
    if kind != "duck":
        d.ellipse((cx - 0.40 * s, head_c + 0.02 * s, cx + 0.40 * s, head_c + 0.60 * s), fill=muzzle)
        d.ellipse((cx - 0.13 * s, head_c + 0.16 * s, cx + 0.13 * s, head_c + 0.40 * s), fill=ink)
    for sx in (-1, 1):
        d.ellipse(
            (
                cx + sx * 0.30 * s - 0.10 * s,
                head_c - 0.16 * s,
                cx + sx * 0.30 * s + 0.10 * s,
                head_c + 0.10 * s,
            ),
            fill=ink,
        )
        d.ellipse(
            (
                cx + sx * 0.54 * s - 0.13 * s,
                head_c + 0.22 * s,
                cx + sx * 0.54 * s + 0.13 * s,
                head_c + 0.44 * s,
            ),
            fill=blush,
        )


def _draw_title_plaque(d, title: str, topic: str, with_tag: bool) -> None:
    """书名贴纸：**只落在安全带内**，字号自适应，中文/英文各自换行。

    2026-09-15：首版 pad 30 / 不透明度 224 / 字号 52 → 白框大到把地平线整段糊住，
    像弹窗不像封面。收紧到 pad 20、半透明、字号降一档。
    """
    from PIL import ImageFont

    font_path = _FONT_CJK if any(ord(c) > 127 for c in title) else _FONT_ROUND
    pad, max_w = 20, COVER_W - 2 * 84
    text_w = max_w - 2 * pad
    for size in (46, 41, 36, 31, 27):
        f_title = ImageFont.truetype(font_path, size)
        lines = _wrap(d, title, f_title, text_w)
        if len(lines) <= 4 or size == 27:
            break
    lh = round(size * 1.22)
    f_tag = ImageFont.truetype(_FONT_CJK, 24)
    tag_h = 40 if with_tag else 0
    gap = 10 if with_tag else 0
    h = tag_h + gap + lh * len(lines) + 2 * pad
    top = max(COVER_SAFE_TOP, (COVER_SAFE_TOP + COVER_SAFE_BOTTOM - h) // 2)
    d.rounded_rectangle(
        (84, top, COVER_W - 84, top + h),
        26,
        fill=(255, 253, 247, 198),
        outline=(96, 74, 62, 46),
        width=3,
    )
    y = top + pad
    if with_tag:
        tw = d.textlength(topic, font=f_tag)
        d.rounded_rectangle(
            ((COVER_W - tw - 30) / 2, y, (COVER_W + tw + 30) / 2, y + tag_h - 8),
            16,
            outline=(255, 107, 53, 200),
            width=3,
        )
        d.text(((COVER_W - tw) / 2, y + 4), topic, font=f_tag, fill=(206, 78, 30))
        y += tag_h + gap
    for ln in lines:
        w = d.textlength(ln, font=f_title)
        d.text(((COVER_W - w) / 2, y), ln, font=f_title, fill=(59, 47, 47))
        y += lh


def gen_cover(
    title: str, author: str, palette_idx: int, topic: str, *, with_text: bool = True
) -> bytes:
    """绘本风封面：天空/太阳/云/远近山丘/草地/树/小屋/正脸小动物 + 安全带书名。600x900 JPG。

    2026-09-15 重做：旧版是「纯抽象几何色块」（几个圆和三角），用户判定为毛坯；
    且书名烧在 y=250、作者条烧在 y=H-120，而所有展示位都用 aspectFill 裁切，
    作者条必然被裁成半个字、顶部标签也残缺（E-20260915-02 同族）。

    with_text=False（活动封面用）：活动 banner 是 2.46:1 横条，竖版封面裁切后
    只露中间一段；标题在活动卡片正文已有，故封面不烧字，只出场景插画。
    """
    import random
    from io import BytesIO

    from PIL import Image, ImageDraw

    rng = random.Random(palette_idx * 977 + (7 if with_text else 41))
    W, H = COVER_W, COVER_H
    bg, deco, deco2 = [_hex(x) for x in PALETTES[palette_idx % len(PALETTES)][:3]]

    img = Image.new("RGB", (W, H), _tint(bg, 1.6))
    _sky_gradient(img, _tint(bg, 1.72), _tint(bg, 1.22), 0, 560)
    d = ImageDraw.Draw(img, "RGBA")

    # 太阳 + 云
    _draw_sun(d, rng.randint(440, 500), rng.randint(112, 158), rng.randint(56, 70), deco)
    for cx, cy, cs in (
        (rng.randint(70, 150), rng.randint(120, 180), rng.randint(26, 34)),
        (rng.randint(260, 330), rng.randint(86, 128), rng.randint(22, 30)),
        (rng.randint(150, 250), rng.randint(210, 248), rng.randint(16, 23)),
        # 下面两朵落在「活动横条」可见带（y 350..550）里——否则横条只剩天与山脊
        (rng.randint(56, 130), rng.randint(378, 418), rng.randint(22, 30)),
        (rng.randint(410, 510), rng.randint(398, 436), rng.randint(16, 24)),
    ):
        _draw_cloud(d, cx, cy, cs, (255, 253, 247, 214))

    # 远山（浅）+ 近丘（饱和）+ 底部草地
    far = _mix(_tint(bg, 1.22), deco2, 0.42)
    d.ellipse((-170, 470, 420, 720), fill=far + (255,))
    d.ellipse((300, 452, 830, 700), fill=far + (235,))
    near = _mix(deco2, (255, 255, 255), 0.08)
    d.ellipse((-230, 556, 400, 900), fill=near + (255,))
    d.ellipse((240, 548, 870, 910), fill=_tint(near, 0.93) + (255,))
    d.rectangle((0, 660, W, H), fill=_mix(near, (255, 255, 255), 0.24))

    # 景物：树 / 小屋（树冠高到 y≈470，让横条也见到绿意）
    trunk = (146, 104, 68)
    _draw_tree(
        d,
        rng.randint(48, 96),
        rng.randint(658, 684),
        rng.randint(78, 92),
        trunk,
        _tint(deco2, 0.9),
        _tint(deco2, 1.24),
    )
    _draw_tree(
        d,
        rng.randint(496, 552),
        rng.randint(668, 694),
        rng.randint(62, 76),
        trunk,
        _tint(deco2, 0.86),
        _tint(deco2, 1.2),
    )
    _draw_house(
        d, rng.randint(378, 452), rng.randint(668, 688), 56, (255, 249, 238), deco, (176, 122, 84)
    )

    # 天空聚焦物：热气球 + 飞鸟（整只落在 COVER_BANNER 带内，给横幅一个视觉落点）。
    # y 取 440..456 → 气球重心≈449，正好是安全带 (343..556) 与 hero 带 (314..585) 的
    # 共同中心，横条与详情页两种裁切下都居中；再高就贴到横条上缘（2026-09-15 实测）。
    _draw_balloon(
        d,
        rng.randint(292, 372),
        rng.randint(440, 456),
        rng.randint(26, 32),
        deco,
        _tint(deco2, 1.05),
        (120, 86, 62),
    )
    _draw_birds(d, rng.randint(420, 470), rng.randint(380, 404), 12, (86, 74, 68, 190))

    # 主角：三种造型轮换。
    # with_text=True（书封）→ 站在草地线上（y≈700），1:1 方格裁切完整可见；
    # with_text=False（活动封面，用于 2.46:1 横条）→ 下移到 y≈812，整只落到横条
    # 可见区（y≤617）之下；否则横条里只会露出两只悬空的耳朵（2026-09-15 实测）。
    kind = ("bear", "bunny", "duck")[palette_idx % 3]
    # 身体用暖棕基色，只带 20% 调色板色——纯按调色板混会出灰蓝/灰紫的「泥球」
    body = _mix((216, 146, 96), deco, 0.20)
    buddy_base = rng.randint(700, 726) if with_text else rng.randint(800, 826)
    _draw_buddy(
        d, rng.randint(180, 232), buddy_base, rng.randint(62, 72), kind, body, (252, 240, 222)
    )

    # 天空贴纸（星 / 环 / 点）——少量、克制，且避开车名安全带
    import math

    for _ in range(9):
        x = rng.choice([rng.randint(18, 78), rng.randint(522, 582)])
        y = rng.randint(60, 640)
        if COVER_SAFE_TOP - 24 < y < COVER_SAFE_BOTTOM + 24:
            continue
        r = rng.randint(6, 13)
        col = (deco if rng.random() < 0.55 else deco2) + (176,)
        mode = rng.randrange(3)
        if mode == 0:
            d.ellipse((x - r, y - r, x + r, y + r), fill=col)
        elif mode == 1:
            d.ellipse((x - r, y - r, x + r, y + r), outline=col, width=4)
        else:
            d.polygon(
                [
                    (x + r * math.cos(a), y + r * math.sin(a))
                    for a in [k * math.pi / 5 for k in range(10)]
                ],
                fill=col,
            )

    if with_text:
        _draw_title_plaque(d, title, topic, with_tag=True)

    buf = BytesIO()
    img.save(buf, "JPEG", quality=88)
    return buf.getvalue()


# 活动轮播横条专用尺寸（2026-09-15）
# 为什么不再拿竖版封面去裁：实测 devtools 的 `aspectFill` 在横条里**不是居中裁切**
# （竖版图上位于 y430 的热气球被切在横条顶缘），按比例推算的"安全带"在横条上不可靠。
# 直接按横条比例出图，构图自带边距 → 不存在裁切，也就没有"上半部分被切"。
BANNER_W, BANNER_H = 900, 320


def gen_activity_banner(palette_idx: int) -> bytes:
    """活动轮播横条图（900x320，无文字）。

    构图：天空渐变 + 太阳 + 云 + 热气球 + 飞鸟 + 远/近山丘 + 树 + 小屋 + 小动物。
    所有元素离上下边缘 ≥28px，横条怎么显示都不会切到主体。
    """
    import math
    import random
    from io import BytesIO

    from PIL import Image, ImageDraw

    rng = random.Random(palette_idx * 977 + 41)
    W, H = BANNER_W, BANNER_H
    bg, deco, deco2 = [_hex(x) for x in PALETTES[palette_idx % len(PALETTES)][:3]]

    img = Image.new("RGB", (W, H), _tint(bg, 1.6))
    _sky_gradient(img, _tint(bg, 1.72), _tint(bg, 1.24), 0, 210)
    d = ImageDraw.Draw(img, "RGBA")

    _draw_sun(d, rng.randint(736, 812), rng.randint(52, 74), rng.randint(36, 46), deco)
    for cx, cy, cs in (
        (rng.randint(70, 170), rng.randint(52, 84), rng.randint(20, 27)),
        (rng.randint(360, 452), rng.randint(40, 70), rng.randint(17, 24)),
        (rng.randint(560, 640), rng.randint(96, 124), rng.randint(13, 18)),
    ):
        _draw_cloud(d, cx, cy, cs, (255, 253, 247, 214))

    far = _mix(_tint(bg, 1.24), deco2, 0.42)
    near = _mix(deco2, (255, 255, 255), 0.08)
    d.ellipse((-220, 118, 520, 320), fill=far + (255,))
    d.ellipse((420, 104, 1080, 300), fill=far + (232,))
    d.ellipse((-300, 168, 470, 420), fill=near + (255,))
    d.ellipse((380, 158, 1090, 430), fill=_tint(near, 0.93) + (255,))
    d.rectangle((0, 236, W, H), fill=_mix(near, (255, 255, 255), 0.24))

    trunk = (146, 104, 68)
    _draw_tree(d, rng.randint(40, 96), 250, 58, trunk, _tint(deco2, 0.9), _tint(deco2, 1.24))
    _draw_tree(d, rng.randint(806, 862), 258, 48, trunk, _tint(deco2, 0.86), _tint(deco2, 1.2))
    # 2026-09-15：主体（小屋/动物）收进中间带 —— hero 是 2.21:1，同一张 2.81:1 图
    # 会被左右各裁一截，实测靠边的元素会被切半个。树可以切，主体不行。
    _draw_house(d, rng.randint(574, 626), 244, 48, (255, 249, 238), deco, (176, 122, 84))
    _draw_buddy(
        d,
        rng.randint(252, 306),
        300,
        rng.randint(38, 46),
        ("bear", "bunny", "duck")[palette_idx % 3],
        _mix((216, 146, 96), deco, 0.20),
        (252, 240, 222),
    )

    _draw_balloon(
        d,
        rng.randint(330, 430),
        rng.randint(150, 176),
        rng.randint(26, 34),
        deco,
        _tint(deco2, 1.05),
        (120, 86, 62),
    )
    _draw_birds(d, rng.randint(500, 566), rng.randint(84, 116), 12, (86, 74, 68, 190))

    for _ in range(7):
        x = rng.choice([rng.randint(16, 56), rng.randint(844, 884)])
        y = rng.randint(30, 250)
        r = rng.randint(5, 10)
        col = (deco if rng.random() < 0.55 else deco2) + (176,)
        if rng.randrange(3) == 0:
            d.ellipse((x - r, y - r, x + r, y + r), outline=col, width=3)
        elif rng.randrange(2) == 0:
            d.ellipse((x - r, y - r, x + r, y + r), fill=col)
        else:
            d.polygon(
                [
                    (x + r * math.cos(a), y + r * math.sin(a))
                    for a in [k * math.pi / 5 for k in range(10)]
                ],
                fill=col,
            )

    buf = BytesIO()
    img.save(buf, "JPEG", quality=88)
    return buf.getvalue()


def store_cover(book: Book, data: bytes) -> str:
    from backend.common.file_storage import _uploads_root

    if book.isbn:
        rel = os.path.join("cover", book.isbn[:4], f"{book.isbn}_{secrets.token_hex(6)}.jpg")
    else:
        rel = os.path.join(
            "cover", "local", f"{book.book_code or book.id}_{secrets.token_hex(6)}.jpg"
        )
    abs_path = os.path.join(_uploads_root(), rel)
    os.makedirs(os.path.dirname(abs_path), exist_ok=True)
    with open(abs_path, "wb") as fh:
        fh.write(data)
    return rel


QUIZ_TEMPLATES = [
    ("single", "这本书的作者是谁？", ["{author}", "J.K. Rowling", "Eric Carle", "Dr. Seuss"]),
    ("single", "《{title_short}》属于哪一类书？", ["{topic}", "数学课本", "菜谱", "地图册"]),
    ("boolean", "《{title_short}》是一本英文绘本或章节书。", []),
    (
        "single",
        "在图书馆找到这本书后，想带回家应该怎么做？",
        ["请馆员办理借阅", "直接塞进书包", "藏在书架后面", "让爸爸妈妈偷偷拿走"],
    ),
    (
        "single",
        "读完一本书想留下想法，可以在哪里记录？",
        ["阅读护照/成长档案", "撕掉一页书", "在书上涂画", "不用记录"],
    ),
]


def make_questions(book: Book):
    short = book.title[:34]
    qs = []
    for i, (qtype, text, opts) in enumerate(QUIZ_TEMPLATES):
        if qtype == "boolean":
            opts = ["对", "错"]
        # 占位符必须与题干同口径替换到**选项**（E-20260912-04：早期只替换题干，
        # 选项与答案字面留着 {author}/{topic} —— 孩子看到模板串，且"正确答案"就是它）
        ctx = {"title_short": short, "topic": book.topic, "author": book.author}
        opts = [o.format(**ctx) for o in opts]
        qs.append(
            QuizQuestion(
                book_id=book.id,
                question_type=qtype,
                question_text=text.format(**ctx),
                options=__import__("json").dumps(opts, ensure_ascii=False),
                answer=opts[0],
                sort_order=i + 1,
                is_active=1,
            )
        )
    return qs


def repair_placeholder_questions(db) -> int:
    """修复历史占位符题（E-20260912-04）。

    main() 对已存在的书**整本跳过**，所以修好模板也治不了库里已有的题；
    这里只挑"选项或答案里还含 `{`"的行按同款模板重算，其余一行不碰。"""
    import json as _json

    fixed = 0
    rows = db.query(QuizQuestion).filter(QuizQuestion.is_deleted == 0).all()
    for q in rows:
        if "{" not in (q.options or "") and "{" not in (q.answer or ""):
            continue
        book = db.query(Book).filter(Book.id == q.book_id).first()
        if book is None:
            continue
        ctx = {"title_short": book.title[:34], "topic": book.topic, "author": book.author}
        try:
            opts = _json.loads(q.options or "[]")
        except ValueError:
            continue
        opts = [str(o).format(**ctx) for o in opts]
        q.options = _json.dumps(opts, ensure_ascii=False)
        if opts:
            q.answer = opts[0]
        fixed += 1
    db.flush()
    return fixed


def main() -> int:
    added, skipped = 0, 0
    # 独立 SessionLocal：脚本自用，避免与 seed_wm11_demo 的 session 约定耦合
    db = SessionLocal()
    try:
        for idx, (isbn, title, author, words, ar, gidx, topic) in enumerate(NEW_BOOKS):
            if db.query(Book).filter(Book.isbn == isbn, Book.is_deleted == 0).first():
                skipped += 1
                continue
            audio_isbn = AUDIO_ISBNS[idx % len(AUDIO_ISBNS)]
            audio_rel = f"book_audio/{audio_isbn}/audio.mp3"
            try:
                with open(f"uploads/{audio_rel}", "rb") as fh:
                    duration = _mp3_duration(fh.read()) or 90
            except OSError:
                duration = 90
                audio_rel = None
            book = Book(
                isbn=isbn,
                title=title,
                author=author,
                word_count=words,
                ar_level=ar,
                grade=GRADES[gidx],
                topic=topic,
                description=f"{title} —— {topic}类经典童书演示数据。",
                status=Book.STATUS_ON,
                audio_path=audio_rel,
                audio_duration_seconds=duration,
            )
            db.add(book)
            db.flush()
            book.cover_path = store_cover(book, gen_cover(title, author, idx, topic))
            for seq in (1, 2):
                db.add(
                    BookCopy(
                        book_id=book.id,
                        copy_code=f"DEMO-{isbn}-{seq}",
                        status=BookCopy.STATUS_AVAILABLE,
                    )
                )
            db.add_all(make_questions(book))
            db.flush()
            added += 1
            print(f"+ {title} (AR {ar}, {words} words, cover ok)", flush=True)
        # 旧 6 本若缺封面也补上（seed_wm11_demo 建书时无封面）
        from backend.domain.catalog.models import Book as B

        for b in db.query(B).filter(B.cover_path.is_(None), B.is_deleted == 0).all():
            b.cover_path = store_cover(b, gen_cover(b.title, b.author, b.id, b.topic or "成长故事"))
            print(f"c cover 补齐: {b.title}", flush=True)
        # 上架书缺音频则补（复用 6 个演示音频轮换；小程序听书/筛选"有音频"依赖）
        audio_rels = [f"book_audio/{a}/audio.mp3" for a in AUDIO_ISBNS]
        durations = {}
        for rel in audio_rels:
            try:
                with open(f"uploads/{rel}", "rb") as fh:
                    durations[rel] = _mp3_duration(fh.read()) or 90
            except OSError:
                durations[rel] = 90
        for b in db.query(B).filter(B.audio_path.is_(None), B.is_deleted == 0).all():
            rel = audio_rels[b.id % len(audio_rels)]
            b.audio_path = rel
            b.audio_duration_seconds = durations[rel]
            print(f"c audio 补齐: {b.title}", flush=True)
        # 上架书缺题则补（seed_wm11_demo 建的 6 本原无题；测验页/上架强校验依赖）
        for b in db.query(B).filter(B.is_deleted == 0).all():
            if (
                db.query(QuizQuestion)
                .filter(QuizQuestion.book_id == b.id, QuizQuestion.is_active == 1)
                .count()
                == 0
            ):
                db.add_all(make_questions(b))
                print(f"c quiz 补齐: {b.title}", flush=True)
        db.commit()
        print(
            f"\n完成：新增 {added} 本，跳过 {skipped} 本，旧书封面补齐见上。总计书目 "
            f"{db.query(B).filter(B.is_deleted == 0).count()} 本。"
        )
        n_fixed = repair_placeholder_questions(db)
        if n_fixed:
            db.commit()
            print(f"修复历史占位符题：{n_fixed} 道（选项/答案含 {{author}}/{{topic}}）", flush=True)
        return 0
    finally:
        db.close()


if __name__ == "__main__":
    raise SystemExit(main())
