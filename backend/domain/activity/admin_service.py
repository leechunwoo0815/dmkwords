# backend/domain/activity/admin_service.py — 管理端活动服务（T45 god file 拆分）
"""AdminActivityService：管理端详情/编辑/封面上传/轮播（继承 ActivityService
复用 _activity_view/_quota_used——行为零变化，纯文件拆分合规 800 行限）。"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import func

from backend.common.exceptions import NotFoundError, ValidationError
from backend.domain.activity.models import Activity, ActivityEnrollment
from backend.domain.activity.service import ActivityService
from backend.domain.catalog.audit_events import publish_audit


class AdminActivityService(ActivityService):
    def get_detail(self, activity_id: int) -> dict:
        """T45：管理端活动详情（含报名统计——enrolled/pending/checked_in 计数）。"""
        a = (
            self.db.query(Activity)
            .filter(Activity.id == activity_id, Activity.is_deleted == 0)
            .first()
        )
        if not a:
            raise NotFoundError("活动不存在")
        v = self._activity_view(a, with_quota=True)

        v["enrolled_count"] = (
            self.db.query(func.count(ActivityEnrollment.id))
            .filter(
                ActivityEnrollment.activity_id == a.id,
                ActivityEnrollment.status == ActivityEnrollment.STATUS_ENROLLED,
                ActivityEnrollment.is_deleted == 0,
            )
            .scalar()
        )
        v["pending_count"] = (
            self.db.query(func.count(ActivityEnrollment.id))
            .filter(
                ActivityEnrollment.activity_id == a.id,
                ActivityEnrollment.status == ActivityEnrollment.STATUS_PENDING_PAYMENT,
                ActivityEnrollment.is_deleted == 0,
            )
            .scalar()
        )
        v["checked_in_count"] = (
            self.db.query(func.count(ActivityEnrollment.id))
            .filter(
                ActivityEnrollment.activity_id == a.id,
                ActivityEnrollment.status == ActivityEnrollment.STATUS_CHECKED_IN,
                ActivityEnrollment.is_deleted == 0,
            )
            .scalar()
        )
        return v

    def update(self, admin, activity_id: int, req) -> Activity:
        """T45（FEAT-082·Q7/Q8 批复口径）：仅 PUBLISHED 且未开始可编辑。

        - activity_type 禁改（schema extra=forbid → 422 显式拒绝）
        - 名额下限：new_max_quota < 当前 ACTIVE 占位 → 422（等于放行——Q7）
        - fee/member_only 修改只影响新报名（已报名不追溯——注释锚定）
        """
        a = (
            self.db.query(Activity)
            .filter(Activity.id == activity_id, Activity.is_deleted == 0)
            .with_for_update()
            .populate_existing()
            .first()
        )
        if not a:
            raise NotFoundError("活动不存在")
        if a.status != Activity.STATUS_PUBLISHED:
            raise ValidationError("活动已取消，不可编辑")
        if a.start_at <= datetime.now():
            raise ValidationError("活动已开始，不可编辑")
        data = req.model_dump(exclude_unset=True)
        if "max_quota" in data and data["max_quota"] is not None:
            used = self._quota_used(a.id)
            if data["max_quota"] < used:
                raise ValidationError(f"名额不能低于已报名人数（当前 {used} 人）")
        for k, v in data.items():
            if hasattr(a, k) and k != "id":
                setattr(a, k, v)
        publish_audit(
            self.db,
            admin=admin,
            action="activity.update",
            target_type="activity",
            target_id=str(a.id),
            detail={"fields": sorted(data.keys())},
            reason="活动编辑",
        )
        self.db.commit()
        return a

    def upload_cover(self, admin, activity_id: int, data: bytes, filename: str) -> Activity:
        """T45：活动封面上传（R-316 同款通道；失败删文件防孤儿）。"""
        import os as _os

        from backend.common.file_storage import save_activity_cover_jpg

        a = (
            self.db.query(Activity)
            .filter(Activity.id == activity_id, Activity.is_deleted == 0)
            .first()
        )
        if not a:
            raise NotFoundError("活动不存在")
        ext = _os.path.splitext(filename or "")[1]
        if not ext:
            raise ValidationError("封面文件缺少扩展名")
        rel = save_activity_cover_jpg(a.id, data, ext)
        try:
            a.cover_path = rel
            publish_audit(
                self.db,
                admin=admin,
                action="activity.cover",
                target_type="activity",
                target_id=str(a.id),
                detail={"path": rel},
                reason="封面上传",
            )
            self.db.commit()
        except Exception:
            from backend.common.file_storage import remove_book_media

            remove_book_media(rel, None)
            raise
        return a

    def carousel(self) -> list[dict]:
        """T45：首页轮播位（有封面+PUBLISHED+未开始，最多 5 条）。"""
        rows = (
            self.db.query(Activity)
            .filter(
                Activity.is_deleted == 0,
                Activity.status == Activity.STATUS_PUBLISHED,
                Activity.cover_path.isnot(None),
                Activity.start_at >= datetime.now(),
            )
            .order_by(Activity.start_at)
            .limit(5)
            .all()
        )
        return [self._activity_view(a) for a in rows]

    def get_cover_path(self, activity_id: int) -> str | None:
        """T45：封面相对路径（cover-media/miniapp cover 端点共用——ORM 不进 Router）。"""
        a = (
            self.db.query(Activity)
            .filter(Activity.id == activity_id, Activity.is_deleted == 0)
            .first()
        )
        if not a:
            raise NotFoundError("活动不存在")
        return a.cover_path
