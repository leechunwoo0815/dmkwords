# backend/domain/growth/models.py — 测验/词数/积分/等级/里程碑
from datetime import datetime

from sqlalchemy import Column, DateTime, Index, Integer, SmallInteger, String, Text

from backend.common.base_model import BaseModel


class QuizAttempt(BaseModel):
    """测验提交记录（快照保真：提交时的题目+选项+答案+作答）。"""

    __tablename__ = "quiz_attempts"

    child_id = Column(Integer, nullable=False, index=True)
    book_id = Column(Integer, nullable=False, index=True)
    score = Column(Integer, nullable=False, comment="答对题数")
    total_questions = Column(Integer, nullable=False, comment="总题数")
    passed = Column(SmallInteger, nullable=False, default=0, comment="1=及格（≥及格线）")
    snapshot = Column(Text, nullable=False, default="[]", comment="题目快照 JSON（改题不影响历史）")
    submitted_at = Column(DateTime, nullable=False, default=datetime.now)


class WordsLedger(BaseModel):
    """有效词数入账（红线：学生×书目终身唯一，永不回收）。"""

    __tablename__ = "words_ledgers"
    __table_args__ = (Index("uq_words_child_book", "child_id", "book_id", unique=True),)

    child_id = Column(Integer, nullable=False, index=True)
    book_id = Column(Integer, nullable=False)
    word_count = Column(Integer, nullable=False, comment="该书总词数")
    source = Column(String(20), nullable=False, default="quiz", comment="来源（quiz=测验通过）")
    created_at = Column(DateTime, nullable=False, default=datetime.now)


class PointLedger(BaseModel):
    """积分明细（只加不减不转赠；零头池在 ChildGrowthState）。"""

    __tablename__ = "point_ledgers"

    child_id = Column(Integer, nullable=False, index=True)
    points = Column(Integer, nullable=False, comment="本笔积分（正数）")
    reason_type = Column(
        String(30),
        nullable=False,
        comment="words_convert/quiz_first_pass/quiz_full_marks/checkin_7/checkin_30/manual_adjust",
    )
    detail = Column(String(200), nullable=False, default="", comment="说明（书名/周期等）")
    related_id = Column(Integer, nullable=True, comment="关联对象ID（quiz 奖励=book_id）")
    operator_id = Column(Integer, nullable=True, comment="操作管理员（人工调整时）")
    created_at = Column(DateTime, nullable=False, default=datetime.now)


class ChildGrowthState(BaseModel):
    """孩子成长汇总（词数/本数/积分/等级/零头池；等级只升不降）。"""

    __tablename__ = "child_growth_states"
    __table_args__ = (Index("uq_growth_child", "child_id", "is_deleted", unique=True),)

    child_id = Column(Integer, nullable=False)
    words_total = Column(Integer, nullable=False, default=0, comment="累计有效词数")
    books_total = Column(Integer, nullable=False, default=0, comment="累计读完本数")
    points_total = Column(Integer, nullable=False, default=0, comment="累计积分")
    words_remainder = Column(
        Integer, nullable=False, default=0, comment="零头池（不满 100 词的余数）"
    )
    level = Column(String(1), nullable=False, default="A", comment="当前等级 A-Z（只升不降）")


class MilestoneAward(BaseModel):
    """里程碑达成记录（永不回收）。"""

    __tablename__ = "milestone_awards"
    __table_args__ = (Index("uq_milestone_child_node", "child_id", "node_words", unique=True),)

    child_id = Column(Integer, nullable=False, index=True)
    node_words = Column(Integer, nullable=False, comment="节点词数（如 100000）")
    awarded_at = Column(DateTime, nullable=False, default=datetime.now)


class CheckinStreakRecord(BaseModel):
    """打卡周期发奖记录（7/30 天周期防重复发）。"""

    __tablename__ = "checkin_streak_awards"
    __table_args__ = (Index("uq_streak_award", "child_id", "cycle_type", "cycle_no", unique=True),)

    child_id = Column(Integer, nullable=False)
    cycle_type = Column(String(10), nullable=False, comment="days7/days30")
    cycle_no = Column(Integer, nullable=False, comment="第几个周期（streak/N）")
    streak_at = Column(Integer, nullable=False, comment="达成时的连续天数")
    awarded_at = Column(DateTime, nullable=False, default=datetime.now)
