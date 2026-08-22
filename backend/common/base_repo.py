# backend/common/base_repo.py
"""
[What] 通用 CRUD 数据访问层基类
[Why] 覆盖 80% 的 CRUD 场景，消除 Service 直接写 self.db.query() 的问题
[How] SQLAlchemy 泛型 Repository

架构意图：
  - BaseRepository[T] 提供通用 get/list/create/update/soft_delete
  - Service 层通过 Repository 访问数据，不再直接写 SQL
  - 复杂查询（聚合、多表 join）允许在 Service 中直接写查询
  - 事务边界在 Service 层：repo 只做 flush()，service 调用 commit()

约束：
  - 所有单表 CRUD 必须走 BaseRepository
  - BaseRepository 自动过滤 is_deleted=0
  - get_by_id_or_raise() 配合统一异常体系使用
  - 子类可以覆盖或扩展方法，但不允许删除基类方法
"""

from typing import Generic, TypeVar

from sqlalchemy.orm import Session

from backend.common.base_model import BaseModel

T = TypeVar("T", bound=BaseModel)


class BaseRepository(Generic[T]):
    """
    通用数据访问层

    使用方式：
      class OrderService:
          def __init__(self, db: Session):
              self.order_repo = BaseRepository(db, Order)
              self.child_repo = BaseRepository(db, Child)

          def get_order(self, order_id: int) -> Order:
              return self.order_repo.get_by_id_or_raise(order_id)

    复杂查询示例（在 Service 中直接写）：
      def get_leaderboard(self, period: str):
          return self.db.query(
              Child.id, Child.name, func.sum(BorrowRecord.word_count)
          ).join(BorrowRecord).group_by(Child.id).all()
    """

    def __init__(self, db: Session, model_class: type[T]):
        self.db = db
        self.model = model_class

    def get_by_id(self, id: int) -> T | None:
        """根据 ID 查询，自动过滤软删除"""
        return (
            self.db.query(self.model)
            .filter(
                self.model.id == id,
                self.model.is_deleted == 0,
            )
            .first()
        )

    def get_by_id_or_raise(self, id: int) -> T:
        """根据 ID 查询，不存在则抛出 NotFoundError"""
        obj = self.get_by_id(id)
        if not obj:
            from backend.common.exceptions import NotFoundError

            raise NotFoundError(f"{self.model.__name__}(id={id}) 不存在")
        return obj

    def get_by_field(self, field: str, value) -> T | None:
        """根据单个字段查询，自动过滤软删除"""
        if not hasattr(self.model, field):
            raise AttributeError(f"{self.model.__name__} 没有字段 {field}")
        return (
            self.db.query(self.model)
            .filter(
                getattr(self.model, field) == value,
                self.model.is_deleted == 0,
            )
            .first()
        )

    def list_all(self, offset: int = 0, limit: int = 100, **filters) -> list[T]:
        """列出所有记录，支持过滤条件"""
        q = self.db.query(self.model).filter(self.model.is_deleted == 0)
        for key, value in filters.items():
            if hasattr(self.model, key):
                q = q.filter(getattr(self.model, key) == value)
        return q.offset(offset).limit(limit).all()

    def count(self, **filters) -> int:
        """计数，支持过滤条件"""
        q = self.db.query(self.model).filter(self.model.is_deleted == 0)
        for key, value in filters.items():
            if hasattr(self.model, key):
                q = q.filter(getattr(self.model, key) == value)
        return q.count()

    def exists(self, **filters) -> bool:
        """判断记录是否存在"""
        return self.count(**filters) > 0

    def create(self, obj: T) -> T:
        """创建记录（flush 但不 commit）"""
        self.db.add(obj)
        self.db.flush()
        return obj

    def update(self, obj: T) -> T:
        """更新记录（flush 但不 commit）"""
        self.db.flush()
        return obj

    def soft_delete(self, id: int) -> None:
        """软删除记录"""
        obj = self.get_by_id(id)
        if obj:
            obj.soft_delete()
            self.db.flush()

    def bulk_create(self, objs: list[T]) -> list[T]:
        """批量创建"""
        self.db.add_all(objs)
        self.db.flush()
        return objs
