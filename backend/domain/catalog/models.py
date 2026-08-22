# backend/domain/catalog/models.py — 书目 / 实体副本 / 测验题目 / 音频
"""catalog 域模型（WM2 图书资产）。

表：
  books          书目（一种书；ISBN 唯一，无 ISBN 走内部编号）
  book_copies    实体副本（一本真实的纸质书，5 态 + reserved）
  quiz_questions 测验题目（一书多题备用，首期前 5 道）
"""

from sqlalchemy import Column, Index, Integer, SmallInteger, String, Text

from backend.common.base_model import BaseModel


class Book(BaseModel):
    """书目。"""

    __tablename__ = "books"

    STATUS_ON = 1  # 上架
    STATUS_OFF = 0  # 下架

    isbn = Column(
        String(20), unique=True, nullable=True, index=True, comment="ISBN（无 ISBN 为空）"
    )
    internal_code = Column(
        String(30), unique=True, nullable=True, comment="内部编号（无 ISBN 书目自动生成）"
    )
    title = Column(String(200), nullable=False, comment="书名")
    author = Column(String(100), nullable=False, default="", comment="作者")
    cover_path = Column(String(255), nullable=True, comment="封面存储路径（服务端统一转 JPG）")
    audio_path = Column(String(255), nullable=True, comment="音频 MP3 路径")
    audio_duration_seconds = Column(Integer, nullable=True, comment="音频总时长（秒）")
    word_count = Column(Integer, nullable=False, default=0, comment="总词数（有效词数入账依据）")
    ar_level = Column(String(10), nullable=True, comment="AR 值（可后补）")
    topic = Column(String(50), nullable=False, default="", comment="主题分类")
    grade = Column(String(50), nullable=False, default="", comment="适读年级")
    description = Column(Text, nullable=True, comment="简介")
    status = Column(SmallInteger, nullable=False, default=STATUS_ON, comment="1=上架 0=下架")

    @property
    def book_code(self) -> str:
        """对外标识：优先 ISBN，无 ISBN 用内部编号。"""
        return self.isbn or self.internal_code or ""


class BookCopy(BaseModel):
    """实体副本（一本纸质书）。"""

    __tablename__ = "book_copies"
    __table_args__ = (Index("uq_copy_code", "copy_code", "is_deleted", unique=True),)

    STATUS_AVAILABLE = "available"  # 在馆可借
    STATUS_RESERVED = "reserved"  # 预约锁定
    STATUS_BORROWED = "borrowed"  # 借出
    STATUS_MAINTENANCE = "maintenance"  # 维护中
    STATUS_LOST = "lost"  # 遗失

    # 允许的状态转移矩阵（红线 8：改状态机先画矩阵）
    ALLOWED_TRANSITIONS = {
        STATUS_AVAILABLE: {STATUS_RESERVED, STATUS_BORROWED, STATUS_MAINTENANCE, STATUS_LOST},
        STATUS_RESERVED: {STATUS_AVAILABLE, STATUS_BORROWED, STATUS_MAINTENANCE},
        STATUS_BORROWED: {STATUS_AVAILABLE, STATUS_MAINTENANCE, STATUS_LOST},
        STATUS_MAINTENANCE: {STATUS_AVAILABLE, STATUS_LOST},
        STATUS_LOST: {STATUS_AVAILABLE, STATUS_MAINTENANCE},  # 找回恢复
    }

    book_id = Column(Integer, nullable=False, index=True, comment="书目ID")
    copy_code = Column(String(40), nullable=False, comment="副本码（内部唯一）")
    status = Column(String(20), nullable=False, default=STATUS_AVAILABLE, comment="副本状态")

    def can_transition(self, new_status: str) -> bool:
        return new_status in self.ALLOWED_TRANSITIONS.get(self.status, set())


class QuizQuestion(BaseModel):
    """测验题目。提交测验时保存快照（WM7）；本题库可改可停用，不影响历史成绩。"""

    __tablename__ = "quiz_questions"

    TYPE_SINGLE = "single"  # 单选
    TYPE_BOOLEAN = "boolean"  # 判断

    book_id = Column(Integer, nullable=False, index=True, comment="书目ID")
    question_type = Column(String(10), nullable=False, default=TYPE_SINGLE, comment="题型")
    question_text = Column(Text, nullable=False, comment="题干")
    options = Column(Text, nullable=False, comment="选项 JSON 数组（单选4项；判断为 [对,错]）")
    answer = Column(String(200), nullable=False, comment="正确答案（单选=选项值；判断=对/错）")
    sort_order = Column(Integer, nullable=False, default=0, comment="排序（首期取前5）")
    is_active = Column(SmallInteger, nullable=False, default=1, comment="1=启用 0=停用")
