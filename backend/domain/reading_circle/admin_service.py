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
from backend.common.sql_utils import escape_like
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
            like = f"%{escape_like(keyword)}%"
            q = q.filter(
                (Child.name.like(like, escape="\\"))
                | (ParentModel.name.like(like, escape="\\"))
                | (ParentModel.display_name.like(like, escape="\\"))
                | (Child.english_name.like(like, escape="\\"))
            )
        total = q.count()
        rows = (
            q.order_by(CirclePost.created_at.desc(), CirclePost.id.desc())
            .offset((page - 1) * page_size)
            .limit(page_size)
            .all()
        )
        # P3 性能族（2026-10-22 第一百一十五批）：JOIN 出来的 Child/Parent **直接用**——
        # 原先写 `for p, _, _ in rows` 丢弃、再由 `_admin_post_view` 逐帖各查一次
        # （page_size=100 时 201 条 SQL）。
        items = [self._admin_post_view(p, child=ch, parent=pa) for p, ch, pa in rows]
        return {
            "items": items,
            "total": total,
            "page": page,
            "page_size": page_size,
            "has_next": page * page_size < total,
        }

    def _admin_post_view(self, post: CirclePost, child=None, parent=None) -> dict:
        """单帖视图。列表路径**传入已 JOIN 的 child/parent**（零额外查询）；
        单帖路径（detail 等）不传 → 回落到按 id 查（保持原语义）。"""
        if child is None:
            child = self.db.query(Child).filter(Child.id == post.child_id).first()
        if parent is None:
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

    def _set_admin_liked(self, post_id: int, *, liked: bool) -> bool:
        """原子切换馆长赞（R11-b/B1，2026-10-26）。

        liked=True：仅当 `admin_liked=0` 时置 1 并 +1；liked=False：仅当 `admin_liked=1` 时置 0 并 -1
        （0 下限）。返回是否真的发生了切换——并发双击时后到者拿到 rowcount=0，不再重复加减。
        """
        if liked:
            return bool(
                self.db.execute(
                    update(CirclePost)
                    .where(CirclePost.id == post_id, CirclePost.admin_liked == 0)
                    .values(admin_liked=1, like_count=CirclePost.like_count + 1)
                ).rowcount
            )
        changed = self.db.execute(
            update(CirclePost)
            .where(CirclePost.id == post_id, CirclePost.admin_liked == 1)
            .values(admin_liked=0)
        ).rowcount
        if changed:
            self.db.execute(
                update(CirclePost)
                .where(CirclePost.id == post_id, CirclePost.like_count > 0)
                .values(like_count=CirclePost.like_count - 1)
            )
        return bool(changed)

    def admin_like(self, admin, post_id: int) -> dict:
        """行内一键馆长赞：admin_liked=1 + like_count 原子 +1 + 特殊文案通知。"""
        post = (
            self.db.query(CirclePost)
            .filter(CirclePost.id == post_id, CirclePost.is_deleted == 0)
            .first()
        )
        if not post:
            raise NotFoundError("帖子不存在")
        # R11-b/B1：条件 UPDATE 原子切换——并发双击只有第一个请求 rowcount=1（旧的
        # "读 admin_liked → 判 → 写"会让两个请求都 +1，计数永不自愈）。
        if self._set_admin_liked(post_id, liked=True):
            self.db.flush()
            # 馆长赞特殊文案（同事务）
            self._notify_liked(post, liker_name="", dedup_key="admin", admin=True)
            fresh_count = (
                self.db.query(CirclePost.like_count).filter(CirclePost.id == post_id).scalar()
            )
            publish_audit(
                self.db,
                admin=admin,
                action="circle.admin_like",
                target_type="circle_post",
                target_id=str(post_id),
                detail={"like_count": fresh_count},
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
        if self._set_admin_liked(post_id, liked=False):
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
                # B-16（P3 门禁族 2026-10-22 扩面时抓到）：锁实体必须链 populate_existing，
                # 否则命中行若已在 session 身份映射里，拿到的是**旧对象**（锁白加）。
                .populate_existing()
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

    def _referenced_elsewhere(self, rel: str) -> bool:
        """该内容寻址文件是否仍被**其它活跃帖**引用（R11-b/B4，2026-10-26）。

        生成图按内容指纹落盘（同内容同名，`file_storage.save_generated_media`）——两个帖子
        可以共享同一路径。只按"本行软删"就删文件会把活跃帖的图打碎，故删前必须问一句。
        """
        return (
            self.db.query(CirclePost.id)
            .filter(
                CirclePost.is_deleted == 0,
                or_(CirclePost.image_path == rel, CirclePost.thumb_path == rel),
            )
            .first()
            is not None
        )

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
                # R11-b/B4：有其它活跃帖引用同一文件时**只清本行指针，不删文件**。
                if self._referenced_elsewhere(rel):
                    setattr(row, attr, "")
                    continue
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
