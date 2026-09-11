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
from datetime import date, datetime, timedelta

from sqlalchemy import func
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
from backend.domain.reading_circle.models import CirclePost, CircleRankSnapshot
from backend.domain.reading_circle.snapshot_service import CircleSnapshotService

LEVEL_LETTERS = "ABCDEFGHIJKLMNOPQRSTUVWXYZ"

# 上榜卡门槛：周榜 TOP N（与 PRD §7.5 二期「上榜/上升」口径一致）
RANK_TOP_N = 10

# 卡片类型 → 中文标签（管理端 Tag / 小程序角标共用，防「英文裸输出」）
CARD_TYPE_LABELS = {
    CirclePost.CARD_MILESTONE: "里程碑",
    CirclePost.CARD_BOOKS_COUNT: "读本数",
    CirclePost.CARD_LEVEL_UP: "等级晋级",
    CirclePost.CARD_PERFECT_QUIZ: "测验满分",
    CirclePost.CARD_STREAK: "连续打卡",
    CirclePost.CARD_FINISH_BOOK: "完读",
    CirclePost.CARD_RANK_TOP: "周榜上榜",
    CirclePost.CARD_RANK_UP: "名次上升",
    CirclePost.CARD_WEEKLY_REPORT: "阅读周报",
    CirclePost.CARD_BREAKTHROUGH: "单日突破",
}

# 10 类卡片配色（底色/装饰/强调/字色——gen_cover 绘本风同源）
CARD_PALETTES = {
    CirclePost.CARD_MILESTONE: ("#FCD34D", "#FF6B35", "#6B4A12"),
    CirclePost.CARD_BOOKS_COUNT: ("#4ADE80", "#FCD34D", "#14532D"),
    CirclePost.CARD_LEVEL_UP: ("#60A5FA", "#FCD34D", "#1E3A5F"),
    CirclePost.CARD_PERFECT_QUIZ: ("#F472B6", "#FCD34D", "#7A1F43"),
    CirclePost.CARD_STREAK: ("#5EEAD4", "#FF6B35", "#0F4C43"),
    CirclePost.CARD_FINISH_BOOK: ("#FF8A5C", "#4ADE80", "#6B1D1D"),
    CirclePost.CARD_RANK_TOP: ("#FBBF24", "#FF6B35", "#7C2D12"),
    CirclePost.CARD_RANK_UP: ("#34D399", "#FCD34D", "#064E3B"),
    CirclePost.CARD_WEEKLY_REPORT: ("#A78BFA", "#FCD34D", "#3B0764"),
    CirclePost.CARD_BREAKTHROUGH: ("#FB7185", "#FCD34D", "#7F1D1D"),
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


# ---------- WM14-B 通用小工具 ----------


def _yyyymmdd(d: date) -> int:
    """日期 → ref_id 整数（如 20260914）：可读、可解析、全局唯一。"""
    return int(d.strftime("%Y%m%d"))


def _parse_yyyymmdd(v: int) -> date | None:
    try:
        return datetime.strptime(str(v), "%Y%m%d").date()
    except (ValueError, TypeError):
        return None


def _week_label(d: date) -> str:
    return f"{d:%m月%d日}"


def _day_words(db: Session, child_id: int, day: date) -> int:
    """某自然日入账词数（区间口径与周报同源）。"""
    start = datetime.combine(day, datetime.min.time())
    end = start + timedelta(days=1)
    return int(
        db.query(func.coalesce(func.sum(WordsLedger.word_count), 0))
        .filter(
            WordsLedger.child_id == child_id,
            WordsLedger.created_at >= start,
            WordsLedger.created_at < end,
            WordsLedger.is_deleted == 0,
        )
        .scalar()
        or 0
    )


def _best_day(db: Session, child_id: int) -> tuple[date, int] | None:
    """历史最高单日词数 (day, words)；无账目返回 None（突破卡数据源）。"""
    rows = (
        db.query(func.date(WordsLedger.created_at), func.sum(WordsLedger.word_count))
        .filter(WordsLedger.child_id == child_id, WordsLedger.is_deleted == 0)
        .group_by(func.date(WordsLedger.created_at))
        .all()
    )
    if not rows:
        return None
    day, words = max(rows, key=lambda r: r[1])
    if isinstance(day, str):
        day = datetime.strptime(day, "%Y-%m-%d").date()
    return day, int(words)


def _breakthrough_min_words(db: Session) -> int:
    from backend.common.config_service import ConfigService

    return int(ConfigService(db).get_value("circle_breakthrough_min_words", "1000"))


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

    # ---------- WM14-B 二期卡（快照 / 周报 / 突破） ----------

    if card_type == CirclePost.CARD_RANK_TOP:
        week = _parse_yyyymmdd(ref_id)
        row = (
            db.query(CircleRankSnapshot)
            .filter(
                CircleRankSnapshot.child_id == child.id,
                CircleRankSnapshot.week_start == week,
                CircleRankSnapshot.is_deleted == 0,
            )
            .first()
            if week
            else None
        )
        if not row or row.rank > RANK_TOP_N:
            _fail()
        return {
            **base,
            "title": "周榜上榜！",
            "value_text": f"第 {row.rank} 名",
            "value_label": f"{_week_label(row.week_start)} 周榜 · {row.words:,} 词",
            "label": CARD_TYPE_LABELS[card_type],
        }

    if card_type == CirclePost.CARD_RANK_UP:
        week = _parse_yyyymmdd(ref_id)
        if not week:
            _fail()
        cur = (
            db.query(CircleRankSnapshot)
            .filter(
                CircleRankSnapshot.child_id == child.id,
                CircleRankSnapshot.week_start == week,
                CircleRankSnapshot.is_deleted == 0,
            )
            .first()
        )
        prev = (
            db.query(CircleRankSnapshot)
            .filter(
                CircleRankSnapshot.child_id == child.id,
                CircleRankSnapshot.week_start == week - timedelta(days=7),
                CircleRankSnapshot.is_deleted == 0,
            )
            .first()
        )
        # 新上榜（上周无 baseline）不算上升；名次持平/下降不算
        if not cur or not prev or prev.rank <= cur.rank:
            _fail()
        delta = prev.rank - cur.rank
        return {
            **base,
            "title": "名次上升！",
            "value_text": f"↑ {delta} 位",
            "value_label": f"{_week_label(week)} 周榜第 {cur.rank} 名 · {cur.words:,} 词",
            "label": CARD_TYPE_LABELS[card_type],
        }

    if card_type == CirclePost.CARD_WEEKLY_REPORT:
        from backend.domain.growth.report_service import ReportService

        week = _parse_yyyymmdd(ref_id)
        # 只可晒已完整结束的自然周（周一为界）——与快照"上一完整周"口径一致
        if not week or week.weekday() != 0 or week > CircleSnapshotService.last_complete_week()[0]:
            _fail()
        start = datetime.combine(week, datetime.min.time())
        summary = ReportService(db).range_summary(child, start, start + timedelta(days=7))
        if summary["words"] <= 0 and summary["checkin_days"] <= 0:
            _fail()
        return {
            **base,
            "title": "阅读周报",
            "value_text": f"{summary['words']:,} 词",
            "value_label": (
                f"读完 {summary['books']} 本 · 打卡 {summary['checkin_days']} 天"
                f"（{_week_label(week)} 那周）"
            ),
            "label": CARD_TYPE_LABELS[card_type],
        }

    if card_type == CirclePost.CARD_BREAKTHROUGH:
        day = _parse_yyyymmdd(ref_id)
        words = _day_words(db, child.id, day) if day else 0
        # 门槛化：太少则刷屏（配置 circle_breakthrough_min_words，默认 1000）
        if not day or words < _breakthrough_min_words(db):
            _fail()
        return {
            **base,
            "title": "单日突破！",
            "value_text": f"{words:,} 词",
            "value_label": f"{day:%Y-%m-%d} 单日新高",
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

    # ---- WM14-B 二期卡 ----

    # 周榜上榜 / 名次上升（消费快照表；晒/未晒分组由 service 负责）
    snaps = (
        db.query(CircleRankSnapshot)
        .filter(CircleRankSnapshot.child_id == child.id, CircleRankSnapshot.is_deleted == 0)
        .order_by(CircleRankSnapshot.week_start.desc())
        .all()
    )
    by_week = {s.week_start: s for s in snaps}
    for s in snaps:
        if s.rank <= RANK_TOP_N:
            cards.append(
                {
                    "card_type": CirclePost.CARD_RANK_TOP,
                    "ref_id": _yyyymmdd(s.week_start),
                    "title": f"周榜上榜 · {_week_label(s.week_start)} 第 {s.rank} 名",
                }
            )
        prev = by_week.get(s.week_start - timedelta(days=7))
        if prev and prev.rank > s.rank:
            cards.append(
                {
                    "card_type": CirclePost.CARD_RANK_UP,
                    "ref_id": _yyyymmdd(s.week_start),
                    "title": f"名次上升 · ↑{prev.rank - s.rank} 位（{_week_label(s.week_start)}）",
                }
            )

    # 阅读周报（仅上一完整周；区间聚合走 ReportService.range_summary 同源口径）
    from backend.domain.growth.report_service import ReportService

    last_monday = CircleSnapshotService.last_complete_week()[0]
    week_start_dt = datetime.combine(last_monday, datetime.min.time())
    week_summary = ReportService(db).range_summary(
        child, week_start_dt, week_start_dt + timedelta(days=7)
    )
    if week_summary["words"] > 0 or week_summary["checkin_days"] > 0:
        cards.append(
            {
                "card_type": CirclePost.CARD_WEEKLY_REPORT,
                "ref_id": _yyyymmdd(last_monday),
                "title": (
                    f"阅读周报 · {week_summary['words']:,} 词 / 读完 {week_summary['books']} 本"
                ),
            }
        )

    # 单日突破（历史最高单日词数过门槛才成卡；更高单日 → 新 ref_id 新成就）
    best = _best_day(db, child.id)
    if best and best[1] >= _breakthrough_min_words(db):
        cards.append(
            {
                "card_type": CirclePost.CARD_BREAKTHROUGH,
                "ref_id": _yyyymmdd(best[0]),
                "title": f"单日突破 · {best[1]:,} 词（{best[0]:%m月%d日}）",
            }
        )
    return cards


# ---------- Pillow 渲染 ----------


# 卡片类型 → 吉祥物（每类卡一个动物，形成系列感；与本批头像库同一套美术语言）
CARD_MASCOT = {
    CirclePost.CARD_MILESTONE: "lion",
    CirclePost.CARD_BOOKS_COUNT: "bear",
    CirclePost.CARD_LEVEL_UP: "fox",
    CirclePost.CARD_PERFECT_QUIZ: "bunny",
    CirclePost.CARD_STREAK: "panda",
    CirclePost.CARD_FINISH_BOOK: "cat",
    CirclePost.CARD_RANK_TOP: "deer",
    CirclePost.CARD_RANK_UP: "owl",
    CirclePost.CARD_WEEKLY_REPORT: "hedgehog",
    CirclePost.CARD_BREAKTHROUGH: "dino",
}

CARD_W, CARD_H = 750, 1000


def _render_full(card_data: dict, pal: dict, kind: str, base: dict, out_dir: str, tag: str) -> str:
    """含字完整版（预览/保存转发用）：标题胶囊 + 主数字 + 说明行 + 吉祥物 + 页脚。"""
    from backend.domain.reading_circle import art
    from backend.domain.reading_circle.art_mascot import mascot as art_mascot

    cv = art.Canvas(CARD_W, CARD_H, pal)
    # 页面级装饰只放安全边距（卡片框外），杜绝首版"被边框裁切"
    art.glow(cv, 628, 92, 165, "#FFFFFF", 100)
    art.glow(cv, 120, 78, 120, "#FFFFFF", 70)
    art.cloud(cv, 112, 104, 128, "#FFFFFF", 205)
    art.cloud(cv, 646, 116, 96, "#FFFFFF", 165)
    art.star(cv, 40, 330, 11, "#FFFFFF", outline=pal["accent"], width=2.0, rotate=0.3)
    art.star(cv, 710, 566, 10, "#FFFFFF", outline=pal["accent"], width=1.8, rotate=-0.2)
    art.sparkle(cv, 28, 466, 12, "#FFFFFF", 225)
    art.sparkle(cv, 722, 258, 11, "#FFFFFF", 215)

    # 标题胶囊（accent 填充 + 白字，全 10 类卡统一）
    art.bubble(cv, (196, 64, 554, 146), radius=41, fill=pal["accent"], outline=None)
    art.sticker_text(cv, (375, 105), str(card_data.get("label", "")), art.font_cn(44), "#FFFFFF")

    # 卡面
    art.soft_shadow(cv, (66, 176, 684, 770), radius=46, blur=12, alpha=58)
    art.bubble(cv, (66, 176, 684, 770), radius=46, fill=art.PAPER, outline=pal["accent"], width=6)
    art.glow(cv, 375, 340, 190, "#FFFFFF", 90)

    big = str(card_data.get("value_text", ""))
    num, _, unit = big.partition(" ")
    if unit:
        art.sticker_pair(
            cv,
            (375, 330),
            num,
            art.font_round(150),
            unit,
            art.font_cn(74),
            pal["deep"],
            stroke="#FFFFFF",
            stroke_w=10,
            dy_unit=26,
        )
    else:
        art.sticker_text(
            cv, (375, 330), big, art.font_round(142), pal["deep"], stroke="#FFFFFF", stroke_w=9
        )
    title = str(card_data.get("title", ""))
    label = str(card_data.get("value_label", ""))
    if title:
        art.sticker_text(cv, (375, 462), title, art.font_cn(36), art.INK)
    if label:
        art.sticker_text(cv, (375, 518), label, art.font_cn(32), pal["deep"])

    art_mascot(cv, 190, 650, 82, kind=kind, fur=base["fur"], ear=base["ear"], blush=base["blush"])
    art.star(cv, 520, 636, 26, "#FFE08A", outline=pal["accent"], width=3.2, rotate=0.22)
    art.star(cv, 604, 700, 17, "#FFF3C4", outline=pal["accent"], width=2.4, rotate=-0.24)
    art.sparkle(cv, 486, 566, 15, "#FFFFFF", 235)

    art.bubble(cv, (268, 800, 482, 856), radius=28, fill=art.PAPER, outline=pal["deep"], width=4)
    art.sticker_text(
        cv, (375, 829), str(card_data.get("english_name", "")), art.font_cn(28), art.INK
    )
    art.bubble(cv, (48, 876, 702, 936), radius=26, fill=pal["accent"], outline=None)
    art.sticker_text(
        cv,
        (375, 907),
        f"{card_data.get('date', '')} · 保存分享这份成长",
        art.font_cn(26),
        "#FFFFFF",
    )
    art.sticker_text(cv, (375, 966), "DmkWords 少儿英语阅读馆", art.font_cn(23), art.INK)
    art.paper_grain(cv)
    return _save(cv, out_dir, f"card_{card_data.get('card_type', 'x')}_{tag}.png")


def _render_thumb(card_data: dict, pal: dict, kind: str, base: dict, out_dir: str, tag: str) -> str:
    """无字缩略版（信息流小图）：插画 + 主数字，无任何文字（文字由列表原生渲染）。"""
    from backend.domain.reading_circle import art
    from backend.domain.reading_circle.art_mascot import mascot as art_mascot

    cv = art.Canvas(CARD_W, CARD_H, pal)
    art.glow(cv, 375, 400, 260, "#FFFFFF", 120)
    art.cloud(cv, 128, 150, 150, "#FFFFFF", 190)
    art.cloud(cv, 630, 210, 118, "#FFFFFF", 160)
    art.rainbow(cv, 375, 1024, 168, 9.0, 150)
    for cx, cy, r in ((126, 592, 18), (628, 560, 15)):
        art.star(cv, cx, cy, r, "#FFFFFF", outline=pal["accent"], width=2.6, rotate=0.2)
    for cx, cy, r in ((238, 300, 14), (534, 288, 12), (300, 520, 11), (620, 700, 13)):
        art.sparkle(cv, cx, cy, r, "#FFFFFF", 235)
    art.sticker_text(
        cv,
        (375, 412),
        str(card_data.get("value_text", "")),
        art.font_round(168),
        pal["deep"],
        stroke="#FFFFFF",
        stroke_w=13,
    )
    art_mascot(cv, 375, 720, 104, kind=kind, fur=base["fur"], ear=base["ear"], blush=base["blush"])
    art.paper_grain(cv)
    return _save(cv, out_dir, f"thumb_{card_data.get('card_type', 'x')}_{tag}.png")


def _save(cv, out_dir: str, filename: str) -> str:
    from PIL import Image

    img = cv.img.resize((cv.w, cv.h), Image.LANCZOS)
    img.save(os.path.join(out_dir, filename), "PNG")
    return f"circle/{filename}"


def render_thumb(card_data: dict) -> str:
    """只渲染无字缩略图（WM15-B3：旧帖一次性回填用，不重渲大图）。"""
    from backend.domain.reading_circle import art

    card_type = card_data.get("card_type", CirclePost.CARD_FINISH_BOOK)
    pal = art.PALETTES.get(card_type, art.PALETTES[CirclePost.CARD_FINISH_BOOK])
    kind = CARD_MASCOT.get(card_type, "cat")
    out_dir = os.path.join(_uploads_root(), "circle")
    os.makedirs(out_dir, exist_ok=True)
    return _render_thumb(card_data, pal, kind, art.KIND_BASE[kind], out_dir, uuid.uuid4().hex[:8])


def render_card(card_data: dict) -> dict:
    """渲染**双规格**卡片图（WM15-R2）→ {"image_path": 含字完整版, "thumb_path": 无字缩略版}。

    两规格同为 uploads/circle/ 下的运行时产物，生命周期绑定同一帖
    （删帖由 CircleImageCleanupService 两列一起清）。
    """
    from backend.domain.reading_circle import art

    card_type = card_data.get("card_type", CirclePost.CARD_FINISH_BOOK)
    pal = art.PALETTES.get(card_type, art.PALETTES[CirclePost.CARD_FINISH_BOOK])
    kind = CARD_MASCOT.get(card_type, "cat")
    base = art.KIND_BASE[kind]
    out_dir = os.path.join(_uploads_root(), "circle")
    os.makedirs(out_dir, exist_ok=True)
    tag = uuid.uuid4().hex[:8]
    return {
        "image_path": _render_full(card_data, pal, kind, base, out_dir, tag),
        "thumb_path": _render_thumb(card_data, pal, kind, base, out_dir, tag),
    }


def _uploads_root() -> str:
    from backend.config import get_settings

    return os.path.abspath(get_settings().UPLOADS_DIR)


def post_thumb_image(db: Session, post_id: int) -> str:
    """取帖子缩略图相对路径（信息流小图；旧帖无缩略图 → 回落大图，前端仍留 wx:if 防空）。"""
    from backend.domain.reading_circle.models import CirclePost as Post

    p = db.query(Post).filter(Post.id == post_id, Post.is_deleted == 0).first()
    if not p:
        raise NotFoundError("帖子不存在")
    return p.thumb_path or p.image_path


def post_card_image(db: Session, post_id: int) -> str:
    """取帖子的卡片图相对路径（双端 image 端点共用；ORM 不进 Router）。"""
    from backend.domain.reading_circle.models import CirclePost as Post

    p = db.query(Post).filter(Post.id == post_id, Post.is_deleted == 0).first()
    if not p:
        raise NotFoundError("帖子不存在")
    return p.image_path


def card_data_json(card_data: dict) -> str:
    return json.dumps(card_data, ensure_ascii=False)
