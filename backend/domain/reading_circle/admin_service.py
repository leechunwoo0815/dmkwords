# backend/domain/reading_circle/admin_service.py — 阅读圈管理端服务（WM14-A）
"""运营主战场（围绕"让家长感到被看见"）：
- 帖子管理（筛选：类型含「全部」/时间段/家长孩子关键词——客服定位）；
- 行内馆长赞（巡场动线 30 秒；特殊文案通知）；
- 置顶互斥（同时最多 1 条）+ 删除必填原因（审计留痕）；
- 未赞徽标数据（今日新帖 admin_liked=0 计数）+ 运营概览。
"""

from __future__ import annotations

from datetime import datetime, timedelta

from sqlalchemy import and_, func, or_, update
from sqlalchemy.orm import Session

from backend.common.exceptions import NotFoundError, ValidationError
from backend.domain.catalog.audit_events import publish_audit
from backend.domain.identity.models import Child, Parent
from backend.domain.reading_circle.card_engine import CARD_TYPE_LABELS
from backend.domain.reading_circle.models import CirclePost
from backend.domain.reading_circle.service import CircleService, _display_name, _parent_display


class AdminCircleService(CircleService):
    """管理端阅读圈服务（继承复用 _post_view/_notify_liked——行为同源）。"""

    # ---------- 帖子管理 ----------

    def list_posts(
        self,
        page: int = 1,
        page_size: int = 20,
        *,
        card_type: str | None = None,
        start: datetime | None = None,
        end: datetime | None = None,
        keyword: str | None = None,
    ) -> dict:
        """管理端帖子列表（时间倒序；类型下拉「全部」= card_type 空）。"""
        from backend.domain.identity.models import Parent as ParentModel

        q = (
            self.db.query(CirclePost, Child, ParentModel)
            .join(Child, CirclePost.child_id == Child.id)
            .join(ParentModel, CirclePost.parent_id == ParentModel.id)
            .filter(CirclePost.is_deleted == 0)
        )
        if card_type:
            q = q.filter(CirclePost.card_type == card_type)
        if start:
            q = q.filter(CirclePost.created_at >= start)
        if end:
            q = q.filter(CirclePost.created_at < end)
        if keyword:
            like = f"%{keyword}%"
            q = q.filter(
                (Child.name.like(like))
                | (ParentModel.name.like(like))
                | (ParentModel.display_name.like(like))
                | (Child.english_name.like(like))
            )
        total = q.count()
        rows = (
            q.order_by(CirclePost.created_at.desc(), CirclePost.id.desc())
            .offset((page - 1) * page_size)
            .limit(page_size)
            .all()
        )
        items = [self._admin_post_view(p) for p, _, _ in rows]
        return {
            "items": items,
            "total": total,
            "page": page,
            "page_size": page_size,
            "has_next": page * page_size < total,
        }

    def _admin_post_view(self, post: CirclePost) -> dict:
        child = self.db.query(Child).filter(Child.id == post.child_id).first()
        parent = self.db.query(Parent).filter(Parent.id == post.parent_id).first()
        return {
            "id": post.id,
            "parent_name": _parent_display(parent) if parent else "",
            "child_name": _display_name(child) if child else "",
            "child_cn_name": child.name if child else "",
            "card_type": post.card_type,
            "card_type_label": CARD_TYPE_LABELS.get(post.card_type, post.card_type),
            "image_url": f"/api/admin/circle/posts/{post.id}/image",
            "like_count": post.like_count,
            "admin_liked": bool(post.admin_liked),
            "is_pinned": bool(post.is_pinned),
            "created_at": post.created_at.strftime("%Y-%m-%d %H:%M") if post.created_at else "",
        }

    # ---------- 馆长赞 ----------

    def admin_like(self, admin, post_id: int) -> dict:
        """行内一键馆长赞：admin_liked=1 + like_count 原子 +1 + 特殊文案通知。"""
        post = (
            self.db.query(CirclePost)
            .filter(CirclePost.id == post_id, CirclePost.is_deleted == 0)
            .first()
        )
        if not post:
            raise NotFoundError("帖子不存在")
        if not post.admin_liked:
            post.admin_liked = 1
            self.db.execute(
                update(CirclePost)
                .where(CirclePost.id == post_id)
                .values(like_count=CirclePost.like_count + 1)
            )
            self.db.flush()
            # 馆长赞特殊文案（同事务）
            self._notify_liked(post, liker_name="", dedup_key="admin", admin=True)
            publish_audit(
                self.db,
                admin=admin,
                action="circle.admin_like",
                target_type="circle_post",
                target_id=str(post_id),
                detail={"like_count": post.like_count + 1},
                reason="馆长赞",
            )
            self.db.commit()
        fresh = self.db.query(CirclePost).filter(CirclePost.id == post_id).first()
        return {"post_id": post_id, "admin_liked": True, "like_count": fresh.like_count}

    def admin_unlike(self, admin, post_id: int) -> dict:
        """取消馆长赞：标记清零 + like_count 原子 -1（0 下限）。"""
        post = (
            self.db.query(CirclePost)
            .filter(CirclePost.id == post_id, CirclePost.is_deleted == 0)
            .first()
        )
        if not post:
            raise NotFoundError("帖子不存在")
        if post.admin_liked:
            post.admin_liked = 0
            self.db.execute(
                update(CirclePost)
                .where(CirclePost.id == post_id, CirclePost.like_count > 0)
                .values(like_count=CirclePost.like_count - 1)
            )
            publish_audit(
                self.db,
                admin=admin,
                action="circle.admin_unlike",
                target_type="circle_post",
                target_id=str(post_id),
                detail={},
                reason="取消馆长赞",
            )
            self.db.commit()
        fresh = self.db.query(CirclePost).filter(CirclePost.id == post_id).first()
        return {"post_id": post_id, "admin_liked": False, "like_count": fresh.like_count}

    # ---------- 置顶 ----------

    def pin(self, admin, post_id: int) -> dict:
        """置顶（互斥：置顶新帖自动取消旧置顶帖——同时最多 1 条）。"""
        post = (
            self.db.query(CirclePost)
            .filter(CirclePost.id == post_id, CirclePost.is_deleted == 0)
            .first()
        )
        if not post:
            raise NotFoundError("帖子不存在")
        if not post.is_pinned:
            # 互斥（Q8 ①）：FOR UPDATE 行锁串行化并发置顶——先锁住现有置顶行再改写，
            # 避免并发下产生双置顶（MySQL 无部分索引，靠行锁 + list_posts 自愈双保险）
            locked = (
                self.db.query(CirclePost)
                .filter(
                    CirclePost.is_pinned == 1,
                    CirclePost.is_deleted == 0,
                    CirclePost.id != post_id,
                )
                .with_for_update()
                .all()
            )
            for row in locked:
                row.is_pinned = 0
            post.is_pinned = 1
            publish_audit(
                self.db,
                admin=admin,
                action="circle.pin",
                target_type="circle_post",
                target_id=str(post_id),
                detail={},
                reason="置顶帖子",
            )
            self.db.commit()
        return {"post_id": post_id, "is_pinned": True}

    def unpin(self, admin, post_id: int) -> dict:
        """取消置顶。"""
        post = (
            self.db.query(CirclePost)
            .filter(CirclePost.id == post_id, CirclePost.is_deleted == 0)
            .first()
        )
        if not post:
            raise NotFoundError("帖子不存在")
        if post.is_pinned:
            post.is_pinned = 0
            publish_audit(
                self.db,
                admin=admin,
                action="circle.unpin",
                target_type="circle_post",
                target_id=str(post_id),
                detail={},
                reason="取消置顶",
            )
            self.db.commit()
        return {"post_id": post_id, "is_pinned": False}

    # ---------- 删除 ----------

    def delete_post(self, admin, post_id: int, reason: str) -> dict:
        """超管删任意帖（必填原因 + 审计留痕）。"""
        if not reason or not reason.strip():
            raise ValidationError("必须填写删除原因（审计留痕）")
        post = (
            self.db.query(CirclePost)
            .filter(CirclePost.id == post_id, CirclePost.is_deleted == 0)
            .first()
        )
        if not post:
            raise NotFoundError("帖子不存在")
        post.is_pinned = 0
        post.soft_delete()
        publish_audit(
            self.db,
            admin=admin,
            action="circle.delete_post",
            target_type="circle_post",
            target_id=str(post_id),
            detail={"card_type": post.card_type, "child_id": post.child_id},
            reason=reason.strip(),
        )
        self.db.commit()
        return {"id": post_id, "deleted": True}

    # ---------- 未赞徽标 + 运营概览 ----------

    def unliked_count(self) -> int:
        """今日新帖中 admin_liked=0 计数（冷启动：馆长对每条新帖手动点赞）。"""
        today_start = datetime.combine(datetime.now().date(), datetime.min.time())
        return (
            self.db.query(func.count(CirclePost.id))
            .filter(
                CirclePost.created_at >= today_start,
                CirclePost.admin_liked == 0,
                CirclePost.is_deleted == 0,
            )
            .scalar()
            or 0
        )

    def overview(self) -> dict:
        """运营概览：本周新帖/分享家长数/点赞总数/馆长赞覆盖率/类型分布。"""
        now = datetime.now()
        today = now.date()
        week_start = datetime.combine(today - timedelta(days=today.weekday()), datetime.min.time())
        base = self.db.query(CirclePost).filter(
            CirclePost.created_at >= week_start, CirclePost.is_deleted == 0
        )
        week_new_posts = base.count()
        sharing_parents = (
            self.db.query(func.count(func.distinct(CirclePost.parent_id)))
            .filter(CirclePost.created_at >= week_start, CirclePost.is_deleted == 0)
            .scalar()
            or 0
        )
        total_posts = (
            self.db.query(func.count(CirclePost.id)).filter(CirclePost.is_deleted == 0).scalar()
            or 0
        )
        admin_liked_posts = (
            self.db.query(func.count(CirclePost.id))
            .filter(CirclePost.admin_liked == 1, CirclePost.is_deleted == 0)
            .scalar()
            or 0
        )
        total_likes = (
            self.db.query(func.coalesce(func.sum(CirclePost.like_count), 0))
            .filter(CirclePost.is_deleted == 0)
            .scalar()
        )
        # 类型分布（全量口径——反哺模板迭代）
        dist_rows = (
            self.db.query(CirclePost.card_type, func.count(CirclePost.id))
            .filter(CirclePost.is_deleted == 0)
            .group_by(CirclePost.card_type)
            .all()
        )
        return {
            "week_new_posts": week_new_posts,
            "sharing_parents": sharing_parents,
            "total_likes": int(total_likes or 0),
            "admin_liked_coverage": round(100 * admin_liked_posts / total_posts, 1)
            if total_posts
            else 0.0,
            "card_type_distribution": {
                (CARD_TYPE_LABELS.get(t, t) if t else t): int(c) for t, c in dist_rows
            },
        }


class CircleImageCleanupService:
    """孤儿卡片图清理（Q13 二期挂账·WM14-B C3）。

    删帖超 RETENTION_DAYS 天的卡片图物理删除：只删文件、不删帖子行
    （软删行是审计/追溯依据）；删后 image_path/thumb_path 置空串，天然防重复清理（幂等）。
    WM15-B1：双规格后**两列两文件一起清**——否则缩略图会成永久孤儿（首版会漏）。
    安全：unlink 前用 abspath 校验落在 uploads/circle/ 内（路径穿越防御）。
    """

    RETENTION_DAYS = 30

    def __init__(self, db: Session):
        self.db = db

    def cleanup_orphan_images(self) -> int:
        import os

        from backend.config import get_settings

        cutoff = datetime.now() - timedelta(days=self.RETENTION_DAYS)
        rows = (
            self.db.query(CirclePost)
            .filter(
                CirclePost.is_deleted == 1,
                CirclePost.update_time < cutoff,
                or_(
                    and_(CirclePost.image_path.isnot(None), CirclePost.image_path != ""),
                    and_(CirclePost.thumb_path.isnot(None), CirclePost.thumb_path != ""),
                ),
            )
            .all()
        )
        if not rows:
            return 0
        root = os.path.abspath(get_settings().UPLOADS_DIR)
        circle_dir = os.path.join(root, "circle") + os.sep
        removed = 0
        for row in rows:
            blocked = False
            for attr in ("image_path", "thumb_path"):
                rel = getattr(row, attr) or ""
                if not rel:
                    continue
                full = os.path.abspath(os.path.join(root, rel))
                if full.startswith(circle_dir) and os.path.isfile(full):
                    try:
                        os.remove(full)
                    except OSError:
                        blocked = True  # 文件被占用：不置空，下次任务重试
                        continue
                setattr(row, attr, "")
            if not blocked:
                removed += 1
        self.db.commit()
        return removed
