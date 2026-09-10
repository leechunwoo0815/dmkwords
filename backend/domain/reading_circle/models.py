# backend/domain/reading_circle/models.py — 阅读圈帖子 / 点赞（WM14-A，FEAT-084）
from datetime import datetime

from sqlalchemy import Column, Date, DateTime, Index, Integer, SmallInteger, String, Text

from backend.common.base_model import BaseModel


class CirclePost(BaseModel):
    """阅读圈帖子（零 UGC：卡片全部后端生成，家长主动授权晒）。

    防滥用：同一成就**同时至多一条活跃帖**——ix_circle_post_achievement 普通索引 +
    service 层 is_deleted=0 查重（MySQL 无部分索引，唯一索引会挡住删除后重晒，
    同 BorrowRecord 副本先例）；删除（家长自删/超管删）即恢复晒权；
    card_data JSON 快照冻结（帖子不可编辑）。
    """

    __tablename__ = "circle_posts"
    __table_args__ = (
        Index(
            "ix_circle_post_achievement",
            "child_id",
            "card_type",
            "ref_id",
            unique=False,
        ),
    )

    CARD_MILESTONE = "milestone"  # 里程碑卡（ref_id=MilestoneAward.id）
    CARD_BOOKS_COUNT = "books_count"  # 读本数卡（ref_id=节点本数）
    CARD_LEVEL_UP = "level_up"  # 等级卡（ref_id=等级序号 A=1...Z=26）
    CARD_PERFECT_QUIZ = "perfect_quiz"  # 满分卡（ref_id=QuizAttempt.id）
    CARD_STREAK = "streak"  # 连击卡（ref_id=CheckinStreakRecord.id）
    CARD_FINISH_BOOK = "finish_book"  # 完读卡（ref_id=book_id；词数取 (child,book) 账目）
    # ---- WM14-B 二期 ----
    CARD_RANK_TOP = "rank_top"  # 上榜卡（ref_id=周起始日 YYYYMMDD；周榜 TOP10）
    CARD_RANK_UP = "rank_up"  # 上升卡（ref_id=本周周起始日 YYYYMMDD；位次上升 delta）
    CARD_WEEKLY_REPORT = "weekly_report"  # 周报卡（ref_id=上周一 YYYYMMDD；实时算上周）
    CARD_BREAKTHROUGH = "breakthrough"  # 突破卡（ref_id=新高日 YYYYMMDD；单日词数新高）

    CARD_TYPES = (
        CARD_MILESTONE,
        CARD_BOOKS_COUNT,
        CARD_LEVEL_UP,
        CARD_PERFECT_QUIZ,
        CARD_STREAK,
        CARD_FINISH_BOOK,
        CARD_RANK_TOP,
        CARD_RANK_UP,
        CARD_WEEKLY_REPORT,
        CARD_BREAKTHROUGH,
    )

    parent_id = Column(Integer, nullable=False, index=True, comment="发布家长ID")
    child_id = Column(Integer, nullable=False, index=True, comment="成就孩子ID")
    card_type = Column(String(30), nullable=False, comment="卡片类型")
    ref_id = Column(Integer, nullable=False, comment="成就关联ID（按类型语义见常量注释）")
    card_data = Column(Text, nullable=False, default="{}", comment="卡片数据 JSON 快照（冻结）")
    image_path = Column(String(255), nullable=True, comment="卡片图（uploads/circle/）")
    like_count = Column(Integer, nullable=False, default=0, comment="点赞数（原子更新）")
    admin_liked = Column(SmallInteger, nullable=False, default=0, comment="1=馆长赞")
    is_pinned = Column(SmallInteger, nullable=False, default=0, comment="1=置顶（同时最多1条）")
    created_at = Column(DateTime, nullable=False, default=datetime.now)


class CircleLike(BaseModel):
    """阅读圈点赞（仅点赞无评论；一心一赞库级兜底 uq_circle_like）。

    软删+复活模式（同 FavoriteService 先例：唯一索引不含 is_deleted，
    复赞撞索引时复活软删行）。
    """

    __tablename__ = "circle_likes"
    __table_args__ = (Index("uq_circle_like", "post_id", "parent_id", unique=True),)

    post_id = Column(Integer, nullable=False, index=True)
    parent_id = Column(Integer, nullable=False, index=True)
    created_at = Column(DateTime, nullable=False, default=datetime.now)


class CircleRankSnapshot(BaseModel):
    """周榜快照（WM14-B）：每周一结算上一完整自然周的名次，供上榜卡/上升卡消费。

    口径与 `LeaderboardService._entries(start, active_only=True, end)` 周榜**同源**
    （计数同源纪律）——落 words>0 的上榜孩子，rank 按词数倒序 i+1。
    幂等：唯一索引 (child_id, week_start)，重跑天然跳过。
    """

    __tablename__ = "circle_rank_snapshots"
    __table_args__ = (Index("uq_circle_rank_snapshot", "child_id", "week_start", unique=True),)

    child_id = Column(Integer, nullable=False, index=True)
    week_start = Column(Date, nullable=False, index=True, comment="周一（该周起始日）")
    rank = Column(Integer, nullable=False, comment="该周名次（词数倒序）")
    words = Column(Integer, nullable=False, comment="该周词数")
    created_at = Column(DateTime, nullable=False, default=datetime.now)
