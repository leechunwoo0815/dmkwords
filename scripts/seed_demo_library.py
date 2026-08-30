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
    lines, cur = [], ""
    for ch in text:
        if draw.textlength(cur + ch, font=font) <= max_w:
            cur += ch
        else:
            lines.append(cur)
            cur = ch
    if cur:
        lines.append(cur)
    return lines


def gen_cover(title: str, author: str, palette_idx: int, topic: str) -> bytes:
    """绘本风封面：色块底 + 几何装饰 + 大字书名 + 作者条。600x900 JPG。"""
    from io import BytesIO

    from PIL import Image, ImageDraw, ImageFont

    W, H = 600, 900
    bg, deco, deco2, tcol, acol = [_hex(x) for x in PALETTES[palette_idx % len(PALETTES)]]
    img = Image.new("RGB", (W, H), bg)
    d = ImageDraw.Draw(img, "RGBA")

    # 顶部圆弧天窗 + 底部山丘波浪
    d.ellipse((-160, -220, W + 160, 260), fill=deco + (70,))
    d.ellipse((-120, H - 220, 320, H + 140), fill=deco2 + (110,))
    d.ellipse((300, H - 260, W + 220, H + 120), fill=deco + (90,))

    # 几何贴纸：圆点 / 圆环 / 三角 / 星
    import math

    for i in range(14):
        x = secrets.randbelow(W - 60) + 30
        y = secrets.randbelow(H - 320) + 90
        r = secrets.randbelow(26) + 10
        col = (deco if i % 2 else deco2) + (150,)
        if i % 4 == 0:
            d.ellipse((x - r, y - r, x + r, y + r), outline=col, width=5)
        elif i % 4 == 1:
            d.ellipse((x - r, y - r, x + r, y + r), fill=col)
        elif i % 4 == 2:
            d.polygon([(x, y - r), (x - r, y + r), (x + r, y + r)], fill=col)
        else:
            pts = [
                (x + r * math.cos(a), y + r * math.sin(a))
                for a in [k * math.pi / 5 for k in range(10)]
            ]
            d.polygon(pts, fill=col)

    # 书名（Arial Rounded 大字自动换行）+ 主题小标签 + 作者条
    f_title = ImageFont.truetype("/System/Library/Fonts/Supplemental/Arial Rounded Bold.ttf", 52)
    f_tag = ImageFont.truetype("/System/Library/Fonts/Supplemental/Arial Rounded Bold.ttf", 30)
    f_author = ImageFont.truetype("/System/Library/Fonts/Supplemental/Arial Rounded Bold.ttf", 28)

    tag = topic
    tw = d.textlength(tag, font=f_tag)
    d.rounded_rectangle((40, 120, 40 + tw + 36, 172), 26, fill=deco2 + (230,))
    d.text((58, 128), tag, font=f_tag, fill=tcol)

    lines = _wrap(d, title, f_title, W - 100)
    y = 250
    for ln in lines[:6]:
        d.text((50, y), ln, font=f_title, fill=tcol, stroke_width=0)
        y += 64

    d.rounded_rectangle((40, H - 120, W - 40, H - 60), 30, fill=bg + (200,))
    d.text((60, H - 108), f"by {author[:30]}", font=f_author, fill=acol)

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
        qs.append(
            QuizQuestion(
                book_id=book.id,
                question_type=qtype,
                question_text=text.format(title_short=short, topic=book.topic, author=book.author),
                options=__import__("json").dumps(opts, ensure_ascii=False),
                answer=opts[0],
                sort_order=i + 1,
                is_active=1,
            )
        )
    return qs


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
        return 0
    finally:
        db.close()


if __name__ == "__main__":
    raise SystemExit(main())
