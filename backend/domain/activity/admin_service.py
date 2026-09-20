# backend/domain/activity/admin_service.py — 管理端活动服务（T45 god file 拆分）
"""AdminActivityService：管理端详情/编辑/封面上传/轮播（继承 ActivityService
复用 _activity_view/_quota_used——行为零变化，纯文件拆分合规 800 行限）。"""

from __future__ import annotations

import json
import os
from datetime import datetime

from sqlalchemy import func

from backend.common.exceptions import NotFoundError, ValidationError
from backend.domain.activity import detail_blocks
from backend.domain.activity.models import Activity, ActivityEnrollment
from backend.domain.activity.schemas import (
    BLOCK_IMAGE,
    BLOCK_PARAGRAPH,
    BLOCK_TYPES,
    DETAIL_IMAGE_DIR,
    MAX_IMAGES,
)
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
        # 只读详情（领导视角）：把"这个活动什么时候建的"也摆出来，方便回溯
        v["created_at"] = str(a.create_time) if a.create_time else None
        # 图文编辑器的**可写形态**：原始块（image 块只有相对路径，URL 由前端按 token 拼）
        v["detail_blocks"] = self.raw_detail_blocks(a)

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

        from backend.common.file_storage import read_image_policy, save_activity_cover_jpg

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
        rel = save_activity_cover_jpg(a.id, data, ext, read_image_policy(self.db, "activity_cover"))
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

    # ---------- 图文详情（2026-09-20 客户需求「像公众号一样」）----------

    def update_detail_blocks(self, admin, activity_id: int, blocks) -> Activity:
        """图文详情全量覆盖写（顺序即展示顺序）。

        **守卫与 `update()` 刻意不同**：图文是纯展示字段，活动**开始后/结束后都要能写**
        （活动前写招募图文，活动后补往期回顾），否则运营只能在活动开始前一次性写完。
        但**时间/名额/费用仍走 `update()` 的旧守卫**——资金与名额安全不放宽（任务包红线 R2）。
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
        if a.status == Activity.STATUS_CANCELLED:
            raise ValidationError("活动已取消，不可编辑")
        parsed = [b.model_dump(exclude_none=True) for b in blocks]
        self._validate_detail_blocks(a, parsed)
        old_paths = self._detail_image_paths(a)
        a.detail_blocks = json.dumps(parsed, ensure_ascii=False)
        publish_audit(
            self.db,
            admin=admin,
            action="activity.detail_blocks",
            target_type="activity",
            target_id=str(a.id),
            detail={
                "blocks": len(parsed),
                "images": sum(1 for b in parsed if b.get("type") == BLOCK_IMAGE),
            },
            reason="图文详情编辑",
        )
        self.db.commit()
        # 提交之后再删文件：避免事务回滚了文件却已经删了（写库与文件不同步的经典坑）
        new_paths = {b["path"] for b in parsed if b.get("type") == BLOCK_IMAGE and b.get("path")}
        orphans = [p for p in old_paths if p not in new_paths]
        if orphans:
            from backend.common.file_storage import remove_activity_detail_images

            remove_activity_detail_images(orphans)
        return a

    def upload_detail_image(self, admin, activity_id: int, data: bytes, filename: str) -> dict:
        """上传一张图文配图（守卫同 `update_detail_blocks`：取消的活动才拦）。"""
        from backend.common.file_storage import read_image_policy, save_activity_detail_image

        a = (
            self.db.query(Activity)
            .filter(Activity.id == activity_id, Activity.is_deleted == 0)
            .first()
        )
        if not a:
            raise NotFoundError("活动不存在")
        if a.status == Activity.STATUS_CANCELLED:
            raise ValidationError("活动已取消，不可编辑")
        ext = os.path.splitext(filename or "")[1]
        if not ext:
            raise ValidationError("配图文件缺少扩展名")
        rel = save_activity_detail_image(
            a.id, data, ext, read_image_policy(self.db, "activity_detail")
        )
        try:
            publish_audit(
                self.db,
                admin=admin,
                action="activity.detail_image",
                target_type="activity",
                target_id=str(a.id),
                detail={"path": rel},
                reason="图文配图上传",
            )
            self.db.commit()
        except Exception:
            from backend.common.file_storage import remove_activity_detail_images

            remove_activity_detail_images([rel])
            raise
        from backend.common.file_utils import activity_detail_image_url

        return {"path": rel, "url": activity_detail_image_url(a.id, rel)}

    @staticmethod
    def raw_detail_blocks(a: Activity) -> list[dict]:
        """原始图文块（管理端编辑器用）——委托 `detail_blocks.raw_blocks`，三处解析只留一份。"""
        return detail_blocks.raw_blocks(a)

    @staticmethod
    def _detail_image_paths(a: Activity) -> set[str]:
        """当前图文里的配图路径集合（用于算出"被移出"的图）。"""
        return detail_blocks.image_paths(a)

    @staticmethod
    def _validate_detail_blocks(a: Activity, blocks: list[dict]) -> None:
        """块级校验：类型合法 / 段落非空 / 配图归属本活动且文件真实存在 / 张数上限。"""
        from backend.common.file_storage import _uploads_root  # noqa: PLC2701 — 同包内部根目录

        images = [b for b in blocks if b.get("type") == BLOCK_IMAGE]
        if len(images) > MAX_IMAGES:
            raise ValidationError(f"配图最多 {MAX_IMAGES} 张（当前 {len(images)} 张）")
        for i, b in enumerate(blocks, 1):
            t = b.get("type")
            if t not in BLOCK_TYPES:
                raise ValidationError(f"第 {i} 块类型不支持：{t}")
            if t == BLOCK_PARAGRAPH:
                if not str(b.get("text") or "").strip():
                    raise ValidationError(f"第 {i} 段正文不能为空")
                continue
            path = str(b.get("path") or "").replace(os.sep, "/")
            if not path.startswith(DETAIL_IMAGE_DIR):
                raise ValidationError(f"第 {i} 张配图路径非法")
            # 归属校验：文件名前缀 = 活动 id（既防越权引用别家图，也让"删除被移出的图"绝对安全）
            if not os.path.basename(path).startswith(f"{a.id}_"):
                raise ValidationError(f"第 {i} 张配图不属于本活动")
            if not os.path.isfile(os.path.join(_uploads_root(), path)):
                raise ValidationError(f"第 {i} 张配图文件不存在，请重新上传")

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
