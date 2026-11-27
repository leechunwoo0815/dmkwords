# backend/domain/activity/repository.py — activity 域数据访问（单表读）
from sqlalchemy.orm import Session

from backend.common.base_repo import BaseRepository
from backend.domain.activity.models import Activity, ActivityEnrollment


class ActivityRepository(BaseRepository[Activity]):
    def __init__(self, db: Session):
        super().__init__(db, Activity)

    def get_active(self, activity_id: int) -> Activity | None:
        """取未软删的活动（口径来自 `ActivityService._get`：`id` + `is_deleted == 0`）。

        不存在时返回 None，由服务层自己抛 `NotFoundError("活动不存在")`
        ——保持原调用点的异常文案与语义不变。
        """
        return self.get_by_id(activity_id)


class ActivityEnrollmentRepository(BaseRepository[ActivityEnrollment]):
    def __init__(self, db: Session):
        super().__init__(db, ActivityEnrollment)

    def list_active_by_child(self, child_id: int) -> list[ActivityEnrollment]:
        """孩子名下**活跃态**报名（口径来自 `ActivityService._my_enrollment_map`：
        `child_id` + `status in ACTIVE_STATUSES` + `is_deleted == 0`，不排序、不限量）。

        终态（refunded/cancelled）不占报名入口位——这是该方法原有的业务含义，迁移不改变。
        """
        return (
            self.db.query(ActivityEnrollment)
            .filter(
                ActivityEnrollment.child_id == child_id,
                ActivityEnrollment.status.in_(ActivityEnrollment.ACTIVE_STATUSES),
                ActivityEnrollment.is_deleted == 0,
            )
            .all()
        )
