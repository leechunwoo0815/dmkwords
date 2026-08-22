# backend/common/base_model.py
"""
[What] 所有业务模型的公共基类
[Why] 消除 id/create_time/update_time/is_deleted 四字段的 96 处重复定义
[How] SQLAlchemy 抽象基类 + TimestampMixin

架构意图：
  所有 model 继承 BaseModel 即可获得标准字段。
  新增模型只需 class X(BaseModel)，不再重复写字段定义。
  soft_delete() 方法统一软删除行为。

约束：
  - id 使用 BigInteger 主键（MySQL）
  - 所有查询默认过滤 is_deleted=0（BaseRepository 自动处理）
  - 软删除不物理删除数据
"""

from datetime import datetime

from sqlalchemy import BigInteger, Column, DateTime, SmallInteger

from backend.database import Base as SQLAlchemyBase

# 主键类型：MySQL BigInteger
BIG_PK = BigInteger()


class TimestampMixin:
    """
    时间戳混入：create_time / update_time / is_deleted

    为什么用 Mixin 而不是直接写在 BaseModel？
    因为未来可能有不需要软删除的模型（如 DictionaryWord），
    可以只继承 TimestampMixin 而不继承 BaseModel。

    时区口径（审查 P1-2 根治）：默认值使用应用侧 datetime.now()，
    与业务代码所有 datetime.now() 比较保持同一时钟口径；
    不用 func.now()（统一应用侧时间，避免 DB 时区歧义）。
    """

    create_time = Column(DateTime, default=datetime.now, comment="创建时间")
    update_time = Column(DateTime, default=datetime.now, onupdate=datetime.now, comment="更新时间")
    is_deleted = Column(SmallInteger, default=0, comment="软删除标记: 0=正常 1=已删除")


class BaseModel(SQLAlchemyBase, TimestampMixin):
    """
    所有业务模型的公共基类

    继承此类的模型自动拥有：
      - id: BigInteger 自增主键
      - create_time: 创建时间
      - update_time: 更新时间（自动更新）
      - is_deleted: 软删除标记

    使用方式：
      class MyModel(BaseModel):
          __tablename__ = "my_table"
          name = Column(String(100))

    注意：
      - __abstract__ = True，不会创建对应的数据库表
      - soft_delete() 只设置 is_deleted=1，不物理删除
    """

    __abstract__ = True

    id = Column(BIG_PK, primary_key=True, autoincrement=True, comment="主键")

    def soft_delete(self):
        """软删除：标记 is_deleted=1"""
        self.is_deleted = 1

    def __repr__(self):
        return f"<{self.__class__.__name__}(id={self.id})>"
