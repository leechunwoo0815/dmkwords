# backend/domain/reading/models.py — 播放进度 / 打卡 / 预约
from datetime import datetime

from sqlalchemy import Column, Date, DateTime, Index, Integer, String, Text

from backend.common.base_model import BaseModel


class ReadingProgress(BaseModel):
    """播放进度（区间并集，完播判定 ≥95% 防刷核心）。

    intervals: JSON 数组 [[start_sec, end_sec], ...] 已合并排序区间。
    finished 首次达成时触发：时长记录 + 当日打卡 + Quiz 解锁（WM7）。
    """

    __tablename__ = "reading_progress"
    __table_args__ = (  # 学生×书目唯一（进程行）
        Column("child_id", Integer, nullable=False, index=True),
    )

    book_id = Column(Integer, nullable=False, index=True)
    intervals = Column(Text, nullable=False, default="[]", comment="已合并播放区间 JSON")
    coverage_seconds = Column(Integer, nullable=False, default=0, comment="累计覆盖秒数（并集）")
    total_seconds = Column(Integer, nullable=False, default=0, comment="音频总时长")
    finished = Column(Integer, nullable=False, default=0, comment="1=已完播")
    finished_at = Column(DateTime, nullable=True)
    last_position = Column(Integer, nullable=False, default=0, comment="上次播放位置（断点续播）")
    last_report_at = Column(
        DateTime, nullable=True, comment="上次上报时间（防刷：服务端墙上时钟基准）"
    )
    reading_minutes = Column(
        Integer, nullable=False, default=0, comment="阅读时长（=音频原始时长，首次完播记一次）"
    )


class CheckIn(BaseModel):
    """每日打卡（当天首次完播即打卡；与 Quiz 无关）。"""

    __tablename__ = "checkins"
    __table_args__ = (
        Column("child_id", Integer, nullable=False, index=True),
        Column("checkin_date", Date, nullable=False),
    )

    book_id = Column(Integer, nullable=False, comment="触发打卡的书")
    created_at = Column(DateTime, nullable=False, default=datetime.now)
    streak = Column(Integer, nullable=False, default=1, comment="打卡时的连续天数")


class Reservation(BaseModel):
    """预约（72h 锁定，占借阅额度）。"""

    __tablename__ = "reservations"

    STATUS_ACTIVE = "active"  # 锁定中
    STATUS_EXPIRED = "expired"  # 超时释放
    STATUS_CANCELLED = "cancelled"  # 家长取消
    STATUS_CHECKED_OUT = "checked_out"  # 核销转借阅
    STATUS_EXCEPTION = "exception"  # 副本异常

    child_id = Column(Integer, nullable=False, index=True)
    book_id = Column(Integer, nullable=False, index=True)
    copy_id = Column(Integer, nullable=False)
    expires_at = Column(DateTime, nullable=False, comment="锁定截止（72h）")
    status = Column(String(20), nullable=False, default=STATUS_ACTIVE, index=True)


class DictionaryWord(BaseModel):
    """词典（FEAT-055：精确查询；全量 ECDICT 后续导入，结构已对齐）。"""

    __tablename__ = "dictionary_words"

    word = Column(String(64), nullable=False, index=True, comment="词条（小写）")
    phonetic = Column(String(128), nullable=True, comment="音标")
    definition = Column(Text, nullable=True, comment="英文释义")
    translation = Column(Text, nullable=True, comment="中文翻译")


class Vocabulary(BaseModel):
    """生词本（查词自动收录；同词唯一；记来源书目）。"""

    __tablename__ = "vocabularies"
    __table_args__ = (Index("uq_vocab_child_word", "child_id", "word", unique=True),)

    child_id = Column(Integer, nullable=False, index=True)
    word = Column(String(64), nullable=False, comment="单词")
    book_id = Column(Integer, nullable=True, comment="来源书目（查词时正在听的书）")
    created_at = Column(DateTime, nullable=False, default=datetime.now)


class Favorite(BaseModel):
    """收藏夹（想读清单；纯标记不占额度；任何状态可用）。"""

    __tablename__ = "favorites"
    __table_args__ = (Index("uq_fav_child_book", "child_id", "book_id", unique=True),)

    child_id = Column(Integer, nullable=False, index=True)
    book_id = Column(Integer, nullable=False, comment="书目")
    created_at = Column(DateTime, nullable=False, default=datetime.now)
