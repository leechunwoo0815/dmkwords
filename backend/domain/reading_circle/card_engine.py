# backend/domain/reading_circle/card_engine.py — 成就卡片引擎（WM14-A）
"""6 类模板（milestone/books_count/level_up/perfect_quiz/streak/finish_book）：
数据装配（从 WordsLedger/MilestoneAward/ChildGrowthState/QuizAttempt/
CheckinStreakRecord 聚合）→ card_data 快照 dict → Pillow 渲染绘本风卡片图
（复用 report_service 字体链 + gen_cover 配色风格，落 uploads/circle/）。

零 UGC 红线：卡片全部后端生成（无用户文字/图片），不触发微信内容安全审查。
"""

from __future__ import annotations

import json
import os
import uuid
from datetime import datetime

from sqlalchemy.orm import Session

from backend.common.exceptions import NotFoundError, ValidationError
from backend.domain.growth.models import (
    CheckinStreakRecord,
    ChildGrowthState,
    MilestoneAward,
    QuizAttempt,
    WordsLedger,
)
from backend.domain.identity.models import Child
from backend.domain.reading_circle.models import CirclePost

LEVEL_LETTERS = "ABCDEFGHIJKLMNOPQRSTUVWXYZ"

# 卡片类型 → 中文标签（管理端 Tag / 小程序角标共用，防「英文裸输出」）
CARD_TYPE_LABELS = {
    CirclePost.CARD_MILESTONE: "里程碑",
    CirclePost.CARD_BOOKS_COUNT: "读本数",
    CirclePost.CARD_LEVEL_UP: "等级晋级",
    CirclePost.CARD_PERFECT_QUIZ: "测验满分",
    CirclePost.CARD_STREAK: "连续打卡",
    CirclePost.CARD_FINISH_BOOK: "完读",
}

# 6 类卡片配色（底色/装饰/强调/字色——gen_cover 绘本风同源）
CARD_PALETTES = {
    CirclePost.CARD_MILESTONE: ("#FCD34D", "#FF6B35", "#6B4A12"),
    CirclePost.CARD_BOOKS_COUNT: ("#4ADE80", "#FCD34D", "#14532D"),
    CirclePost.CARD_LEVEL_UP: ("#60A5FA", "#FCD34D", "#1E3A5F"),
    CirclePost.CARD_PERFECT_QUIZ: ("#F472B6", "#FCD34D", "#7A1F43"),
    CirclePost.CARD_STREAK: ("#5EEAD4", "#FF6B35", "#0F4C43"),
    CirclePost.CARD_FINISH_BOOK: ("#FF8A5C", "#4ADE80", "#6B1D1D"),
}

FONT_CANDIDATES = [
    "/System/Library/Fonts/Hiragino Sans GB.ttc",
    "/System/Library/Fonts/STHeiti Light.ttc",
    "/System/Library/Fonts/Supplemental/Songti.ttc",
]


def _display_name(child: Child) -> str:
    """孩子英文名兜底口径（榜单同款 R-317/318：英文名空时「小朋友{id:03d}」）。"""
    return child.english_name or f"小朋友{child.id:03d}"


def _font(size: int):
    from PIL import ImageFont

    for path in FONT_CANDIDATES:
        try:
            return ImageFont.truetype(path, size)
        except OSError:
            continue
    return ImageFont.load_default()


# ---------- 数据装配（含成就归属校验：伪造 ref_id → 422） ----------


def assemble_card_data(db: Session, child: Child, card_type: str, ref_id: int) -> dict:
    """按类型装配 card_data 快照（成就归属校验 + 文案冻结要素）。

    权限红线：查不到/不属于该孩子的成就 → ValidationError(422)。
    """
    if card_type not in CirclePost.CARD_TYPES:
        raise ValidationError("卡片类型不正确")

    def _fail():
        raise ValidationError("该成就不存在或不属于当前孩子")

    today = datetime.now().strftime("%Y-%m-%d")
    base = {
        "card_type": card_type,
        "child_name": child.name,
        "english_name": _display_name(child),
        "date": today,
    }

    if card_type == CirclePost.CARD_MILESTONE:
        row = (
            db.query(MilestoneAward)
            .filter(
                MilestoneAward.id == ref_id,
                MilestoneAward.child_id == child.id,
                MilestoneAward.is_deleted == 0,
            )
            .first()
        )
        if not row:
            _fail()
        node = row.node_words
        text = f"{node / 10000:.0f} 万" if node >= 10000 else str(node)
        return {
            **base,
            "title": "里程碑达成",
            "value_text": text,
            "value_label": "累计有效阅读词数",
            "label": CARD_TYPE_LABELS[card_type],
        }

    if card_type == CirclePost.CARD_BOOKS_COUNT:
        from backend.common.config_service import ConfigService

        nodes = [
            int(n)
            for n in ConfigService(db).get_value("circle_books_count_nodes").split(",")
            if n.strip()
        ]
        state = (
            db.query(ChildGrowthState)
            .filter(ChildGrowthState.child_id == child.id, ChildGrowthState.is_deleted == 0)
            .first()
        )
        if not state or ref_id not in nodes or state.books_total < ref_id:
            _fail()
        return {
            **base,
            "title": "读本数突破",
            "value_text": f"{ref_id} 本",
            "value_label": "累计读完书目",
            "label": CARD_TYPE_LABELS[card_type],
        }

    if card_type == CirclePost.CARD_LEVEL_UP:
        state = (
            db.query(ChildGrowthState)
            .filter(ChildGrowthState.child_id == child.id, ChildGrowthState.is_deleted == 0)
            .first()
        )
        if not state:
            _fail()
        current_idx = LEVEL_LETTERS.index(state.level) if state.level in LEVEL_LETTERS else 0
        # 等级序号（A=1）：只可晒已达成的等级；A 是初始等级无晋级卡（ref_id 从 2 起）
        if ref_id < 2 or ref_id > current_idx + 1:
            _fail()
        return {
            **base,
            "title": "阅读等级晋升",
            "value_text": f"{LEVEL_LETTERS[ref_id - 1]} 级",
            "value_label": "只升不降 · 见证成长",
            "label": CARD_TYPE_LABELS[card_type],
        }

    if card_type == CirclePost.CARD_PERFECT_QUIZ:
        from backend.domain.catalog.models import Book

        row = (
            db.query(QuizAttempt, Book)
            .join(Book, QuizAttempt.book_id == Book.id)
            .filter(
                QuizAttempt.id == ref_id,
                QuizAttempt.child_id == child.id,
                QuizAttempt.is_deleted == 0,
            )
            .first()
        )
        if not row or row[0].score != row[0].total_questions:
            _fail()
        return {
            **base,
            "title": "测验满分！",
            "value_text": f"{row[0].score}/{row[0].total_questions}",
            "value_label": f"《{row[1].title}》",
            "label": CARD_TYPE_LABELS[card_type],
        }

    if card_type == CirclePost.CARD_STREAK:
        row = (
            db.query(CheckinStreakRecord)
            .filter(
                CheckinStreakRecord.id == ref_id,
                CheckinStreakRecord.child_id == child.id,
                CheckinStreakRecord.is_deleted == 0,
            )
            .first()
        )
        if not row:
            _fail()
        return {
            **base,
            "title": "连续打卡",
            "value_text": f"{row.streak_at} 天",
            "value_label": "每天阅读 坚持到底",
            "label": CARD_TYPE_LABELS[card_type],
        }

    # finish_book（完读卡：ref_id=book_id——词数取该 (child, book) 入账行；
    # WordsLedger 有 uq_words_child_book 终身唯一，"重读产生新账目"不成立，
    # 故 book_id 语义与账目行 id 等价但更直白，且不依赖账目行存活）
    from backend.domain.catalog.models import Book

    row = (
        db.query(WordsLedger, Book)
        .join(Book, WordsLedger.book_id == Book.id)
        .filter(
            WordsLedger.book_id == ref_id,
            WordsLedger.child_id == child.id,
            WordsLedger.is_deleted == 0,
        )
        .first()
    )
    if not row:
        _fail()
    return {
        **base,
        "title": "读完一本书",
        "value_text": f"+{row[0].word_count} 词",
        "value_label": f"《{row[1].title}》",
        "label": CARD_TYPE_LABELS[card_type],
    }


# ---------- 可晒成就枚举（my_cards 数据源） ----------


def enumerate_cards(db: Session, child: Child) -> list[dict]:
    """枚举孩子全部已达成成就（晒/未晒由 service 分组，此处只列成就）。"""
    from backend.common.config_service import ConfigService

    cards: list[dict] = []

    def _book_title(book_id: int) -> str:
        from backend.domain.catalog.models import Book

        b = db.query(Book).filter(Book.id == book_id).first()
        return b.title if b else ""

    # 里程碑
    for row in (
        db.query(MilestoneAward)
        .filter(MilestoneAward.child_id == child.id, MilestoneAward.is_deleted == 0)
        .all()
    ):
        node = row.node_words
        cards.append(
            {
                "card_type": CirclePost.CARD_MILESTONE,
                "ref_id": row.id,
                "title": f"里程碑 · 累计阅读 {node / 10000:.0f} 万词"
                if node >= 10000
                else f"里程碑 · 累计阅读 {node} 词",
            }
        )

    state = (
        db.query(ChildGrowthState)
        .filter(ChildGrowthState.child_id == child.id, ChildGrowthState.is_deleted == 0)
        .first()
    )
    if state:
        # 读本数（节点体系可配置）
        nodes = [
            int(n)
            for n in ConfigService(db).get_value("circle_books_count_nodes").split(",")
            if n.strip()
        ]
        for n in sorted(nodes):
            if state.books_total >= n:
                cards.append(
                    {
                        "card_type": CirclePost.CARD_BOOKS_COUNT,
                        "ref_id": n,
                        "title": f"读本数 · 累计读完 {n} 本",
                    }
                )
        # 等级晋级（B 级起每个已达成的等级一张卡）
        current_idx = LEVEL_LETTERS.index(state.level) if state.level in LEVEL_LETTERS else 0
        for idx in range(1, current_idx + 1):
            cards.append(
                {
                    "card_type": CirclePost.CARD_LEVEL_UP,
                    "ref_id": idx + 1,
                    "title": f"等级晋升 · 升到 {LEVEL_LETTERS[idx]} 级",
                }
            )

    # 满分测验
    for row in (
        db.query(QuizAttempt)
        .filter(
            QuizAttempt.child_id == child.id,
            QuizAttempt.score == QuizAttempt.total_questions,
            QuizAttempt.is_deleted == 0,
        )
        .all()
    ):
        cards.append(
            {
                "card_type": CirclePost.CARD_PERFECT_QUIZ,
                "ref_id": row.id,
                "title": f"测验满分 · 《{_book_title(row.book_id)}》 {row.score}/{row.total_questions}",
            }
        )

    # 连击打卡
    for row in (
        db.query(CheckinStreakRecord)
        .filter(CheckinStreakRecord.child_id == child.id, CheckinStreakRecord.is_deleted == 0)
        .all()
    ):
        cards.append(
            {
                "card_type": CirclePost.CARD_STREAK,
                "ref_id": row.id,
                "title": f"连续打卡 {row.streak_at} 天",
            }
        )

    # 完读（词数入账行=真正有效读完；ref_id=book_id，Q10 裁决口径）
    for row in (
        db.query(WordsLedger)
        .filter(WordsLedger.child_id == child.id, WordsLedger.is_deleted == 0)
        .all()
    ):
        cards.append(
            {
                "card_type": CirclePost.CARD_FINISH_BOOK,
                "ref_id": row.book_id,
                "title": f"读完《{_book_title(row.book_id)}》 +{row.word_count} 词",
            }
        )
    return cards


# ---------- Pillow 渲染 ----------


def render_card(card_data: dict) -> str:
    """绘本风卡片图 → 相对路径（uploads/circle/xxx.png）。

    要素：类型徽章 + 成就标题 + 大数字 + 孩子英文名 + 馆 branding + 日期。
    """
    from PIL import Image, ImageDraw

    card_type = card_data.get("card_type", CirclePost.CARD_FINISH_BOOK)
    bg, deco, ink = CARD_PALETTES.get(card_type, CARD_PALETTES[CirclePost.CARD_FINISH_BOOK])
    W, H = 750, 1000
    img = Image.new("RGB", (W, H), bg)
    d = ImageDraw.Draw(img)

    f_brand = _font(26)
    f_label = _font(34)
    f_title = _font(56)
    f_big = _font(120)
    f_name = _font(44)
    f_date = _font(26)

    # 顶部类型徽章 + 馆 branding
    tag = card_data.get("label", "")
    tw = d.textlength(tag, font=f_label)
    d.rounded_rectangle((56, 56, 56 + tw + 44, 124), 34, fill="#FFFDF7")
    d.text((78, 66), tag, font=f_label, fill=ink)
    d.text(
        (W - 56 - d.textlength("DmkWords 少儿英语阅读馆", font=f_brand), 72),
        "DmkWords 少儿英语阅读馆",
        font=f_brand,
        fill=ink,
    )

    # 成就标题
    d.text((56, 190), card_data.get("title", ""), font=f_title, fill=ink)

    # 中央大数字（白卡）
    d.rounded_rectangle((48, 300, W - 48, 640), 32, fill="#FFFDF7", outline=deco, width=6)
    d.text(
        (W // 2 - d.textlength(str(card_data.get("value_text", "")), font=f_big) / 2, 350),
        str(card_data.get("value_text", "")),
        font=f_big,
        fill=deco,
    )
    sub = str(card_data.get("value_label", ""))
    d.text((W // 2 - d.textlength(sub, font=f_label) / 2, 540), sub, font=f_label, fill=ink)

    # 孩子英文名（R-317/318 隐私口径：只英文名不露全名）
    name = card_data.get("english_name", "")
    d.text((W // 2 - d.textlength(name, font=f_name) / 2, 700), name, font=f_name, fill=ink)

    # 底部：日期 + 保存分享引导（卡片带馆 branding，转发现实朋友圈=免费拉新）
    d.rounded_rectangle((48, 820, W - 48, 930), 28, fill=deco)
    tip = f"{card_data.get('date', '')} · 保存分享这份成长"
    d.text((W // 2 - d.textlength(tip, font=f_date) / 2, 858), tip, font=f_date, fill="#FFFDF7")

    rel_dir = "circle"
    out_dir = os.path.join(_uploads_root(), rel_dir)
    os.makedirs(out_dir, exist_ok=True)
    filename = f"card_{card_type}_{uuid.uuid4().hex[:8]}.png"
    img.save(os.path.join(out_dir, filename), "PNG")
    return f"{rel_dir}/{filename}"


def _uploads_root() -> str:
    from backend.config import get_settings

    return os.path.abspath(get_settings().UPLOADS_DIR)


def post_card_image(db: Session, post_id: int) -> str:
    """取帖子的卡片图相对路径（双端 image 端点共用；ORM 不进 Router）。"""
    from backend.domain.reading_circle.models import CirclePost as Post

    p = db.query(Post).filter(Post.id == post_id, Post.is_deleted == 0).first()
    if not p:
        raise NotFoundError("帖子不存在")
    return p.image_path


def card_data_json(card_data: dict) -> str:
    return json.dumps(card_data, ensure_ascii=False)
