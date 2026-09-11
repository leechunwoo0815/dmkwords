# backend/domain/reading_circle/service.py — 阅读圈家长端服务（WM14-A）
"""核心规则（FEAT-084 / PRD §7.5）：
- 权限红线：只能晒自己孩子的成就（伪造 ref_id → 422）；
- 防滥用：同一成就**同时至多一条活跃帖**（ix_circle_post_achievement 普通索引 +
  本文件 is_deleted=0 查重——删除即恢复晒权）+ 每日限晒 2 帖
  （配置 circle_daily_post_limit，按孩子计）+ 帖子不可编辑（card_data 快照冻结）；
- 点赞：一心一赞（post_id+parent_id 唯一，软删复活同 FavoriteService 先例），
  like_count 原子 UPDATE（禁读改写——并发红线），like 同事务发通知 scene=circle.liked；
- 删除权：家长删自己的帖（软删）；超管删任意帖必填原因（admin_service）。
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta

from sqlalchemy import func, update
from sqlalchemy.orm import Session

from backend.common.config_service import ConfigService
from backend.common.exceptions import NotFoundError, ValidationError
from backend.common.notification_models import Notification
from backend.common.notifications import SCENE_CIRCLE_LIKED, NotificationService
from backend.domain.identity.models import Child, Parent
from backend.domain.reading_circle import card_engine
from backend.domain.reading_circle.models import CircleLike, CirclePost


def _display_name(child: Child) -> str:
    """孩子英文名兜底（榜单口径 R-317/318）。"""
    return child.english_name or f"小朋友{child.id:03d}"


def _parent_display(parent: Parent) -> str:
    """家长署名（WM14-B/Q11 清偿）：display_name（如「Tommy妈妈」）优先，
    空则回退真实姓名。**三消费端必须走这里**：信息流署名 / 管理端列表 / 被赞通知文案
    ——新增显示字段枚举全部消费端（媒体消费点清单化同款纪律）。"""
    return (parent.display_name or "").strip() or parent.name


class CircleService:
    def __init__(self, db: Session):
        self.db = db

    # ---------- 可晒成就库 ----------

    def my_cards(self, child: Child) -> dict:
        """孩子全部可晒成就（已达成未晒 + 已晒分组）。"""
        achievements = card_engine.enumerate_cards(self.db, child)
        shared_rows = (
            self.db.query(CirclePost)
            .filter(CirclePost.child_id == child.id, CirclePost.is_deleted == 0)
            .all()
        )
        shared_keys = {(r.card_type, r.ref_id) for r in shared_rows}
        available, shared = [], []
        for c in achievements:
            c["label"] = card_engine.CARD_TYPE_LABELS[c["card_type"]]
            if (c["card_type"], c["ref_id"]) in shared_keys:
                shared.append(c)
            else:
                available.append(c)
        return {"available": available, "shared": shared}

    # ---------- 晒卡 ----------

    def create_post(self, parent: Parent, child: Child, card_type: str, ref_id: int) -> dict:
        # ① 成就归属（伪造 ref_id 晒他人孩子成就 → 422）
        card_data = card_engine.assemble_card_data(self.db, child, card_type, ref_id)

        # ② 同成就同时至多一条活跃帖（Q9 裁决：删除即恢复晒权——家长误删可重晒、
        # 超管删也不永久封死）。MySQL 无部分索引故走普通索引 + is_deleted=0 查重
        # （同 BorrowRecord 副本先例）；并发双击竞态可容忍：无资金风险、日限 2 帖
        # 封顶、超管可删。
        existing = (
            self.db.query(CirclePost)
            .filter(
                CirclePost.child_id == child.id,
                CirclePost.card_type == card_type,
                CirclePost.ref_id == ref_id,
                CirclePost.is_deleted == 0,
            )
            .first()
        )
        if existing:
            raise ValidationError("该成就已晒过（删除原帖后可重新晒）")

        # ③ 每日限晒（当日 0 点起按孩子计数）
        # 时区口径（Q7 裁决）：业务时区 = Asia/Shanghai，依赖部署时区锚定
        # （dev 本机与生产 compose TZ 均 +8:00，docs/20 §二）——此处用 naive 本地
        # 时间；跨零点窗口由 now().date() 天然满足；禁 datetime.utcnow()（混用
        # aware/naive 会重现 T20a 撞车）
        limit = int(ConfigService(self.db).get_value("circle_daily_post_limit"))
        today_start = datetime.combine(datetime.now().date(), datetime.min.time())
        today_count = (
            self.db.query(func.count(CirclePost.id))
            .filter(
                CirclePost.child_id == child.id,
                CirclePost.created_at >= today_start,
                CirclePost.is_deleted == 0,
            )
            .scalar()
            or 0
        )
        if today_count >= limit:
            raise ValidationError(f"今日已晒 {today_count} 帖，每日限晒 {limit} 帖，明天再来吧")

        # ④ 渲染卡片图 + 落帖（card_data 快照冻结）
        rendered = card_engine.render_card(card_data)  # WM15-R2：双规格（含字大图 + 无字缩略图）
        post = CirclePost(
            parent_id=parent.id,
            child_id=child.id,
            card_type=card_type,
            ref_id=ref_id,
            card_data=card_engine.card_data_json(card_data),
            image_path=rendered["image_path"],
            thumb_path=rendered["thumb_path"],
        )
        self.db.add(post)
        self.db.commit()
        return {
            "post_id": post.id,
            "card_type": card_type,
            "image_url": f"/api/miniapp/circle/posts/{post.id}/image",
        }

    # ---------- 信息流 ----------

    def list_posts(self, viewer: Parent, page: int = 1, page_size: int = 10) -> dict:
        """时间倒序真分页 + 置顶帖置首（一条）。"""
        base = self.db.query(CirclePost).filter(CirclePost.is_deleted == 0)
        total = base.count()

        # 置顶槽：取最新一条置顶帖（Q8 自愈——脏数据出现多条置顶时，其余置顶帖
        # 回落普通流按时间序显示，不再被 is_pinned==0 过滤静默丢帖）
        pinned_rows = (
            self.db.query(CirclePost)
            .filter(CirclePost.is_deleted == 0, CirclePost.is_pinned == 1)
            .order_by(CirclePost.created_at.desc(), CirclePost.id.desc())
            .all()
        )
        pinned = pinned_rows[0] if pinned_rows else None
        exclude_id = pinned.id if pinned else 0

        q = (
            self.db.query(CirclePost)
            .filter(CirclePost.is_deleted == 0, CirclePost.id != exclude_id)
            .order_by(CirclePost.created_at.desc(), CirclePost.id.desc())
        )
        pin_offset = 1 if pinned else 0
        if page == 1:
            rows = q.limit(max(0, page_size - pin_offset)).all()
            if pinned:
                rows = [pinned, *rows]
        else:
            rows = q.offset((page - 1) * page_size - pin_offset).limit(page_size).all()

        likers = self._likers_map([r.id for r in rows])
        items = [self._post_view(r, viewer.id, likers) for r in rows]
        return {
            "items": items,
            "total": total,
            "page": page,
            "page_size": page_size,
            "has_next": page * page_size < total,
            "banner": self._banner(),
        }

    def _banner(self) -> dict:
        """社区横幅（WM14-B）：本周全馆共读词数 + 在坚持的孩子数。

        计数同源：口径与周榜完全一致（LeaderboardService.period_entries——
        active_only 且在会、words>0），禁在此另写聚合（计数同源第 8 案预防）。
        """
        from backend.domain.growth.board_service import LeaderboardService

        today = datetime.now().date()
        monday = today - timedelta(days=today.weekday())
        entries = LeaderboardService(self.db).period_entries(
            datetime.combine(monday, datetime.min.time())
        )
        return {"words": sum(e["words"] for e in entries), "kids": len(entries)}

    def _likers_map(self, post_ids: list[int], limit: int = 8) -> dict[int, list]:
        """点赞头像墙（批查，禁 N+1）：只取有名义的赞（liker_child_id 为空的历史赞不入墙，
        仍计入 like_count）。每帖最多 limit 个。"""
        from backend.domain.reading_circle.models import CircleLike

        if not post_ids:
            return {}
        rows = (
            self.db.query(CircleLike.post_id, Child.id, Child.english_name, Child.avatar)
            .join(Child, Child.id == CircleLike.liker_child_id)
            .filter(
                CircleLike.post_id.in_(post_ids),
                CircleLike.is_deleted == 0,
                Child.is_deleted == 0,
            )
            .order_by(CircleLike.id.asc())
            .all()
        )
        out: dict[int, list] = {}
        for pid, cid, en, av in rows:
            bucket = out.setdefault(pid, [])
            if len(bucket) < limit:
                bucket.append({"child_id": cid, "name": en or f"小朋友{cid:03d}", "avatar": av})
        return out

    @staticmethod
    def _card_fields(post: CirclePost) -> tuple[str, str]:
        """从冻结的 card_data 快照取原生渲染文本（WM15-R1：文字脱离图片原生化）。"""
        try:
            d = json.loads(post.card_data or "{}")
        except (ValueError, TypeError):
            return "", ""
        return str(d.get("title", "")), str(d.get("value_label", ""))

    def _post_view(
        self, post: CirclePost, viewer_parent_id: int, likers: dict[int, list] | None = None
    ) -> dict:
        child = (
            self.db.query(Child).filter(Child.id == post.child_id, Child.is_deleted == 0).first()
        )
        parent = (
            self.db.query(Parent)
            .filter(Parent.id == post.parent_id, Parent.is_deleted == 0)
            .first()
        )
        liked = (
            self.db.query(func.count(CircleLike.id))
            .filter(
                CircleLike.post_id == post.id,
                CircleLike.parent_id == viewer_parent_id,
                CircleLike.is_deleted == 0,
            )
            .scalar()
        )
        title, subtitle = self._card_fields(post)
        return {
            "id": post.id,
            "child_id": post.child_id,
            "parent_name": _parent_display(parent) if parent else "",
            "child_name": _display_name(child) if child else "",
            "avatar": child.avatar if child else None,
            "card_type": post.card_type,
            "card_type_label": card_engine.CARD_TYPE_LABELS.get(post.card_type, post.card_type),
            "title": title,
            "subtitle": subtitle,
            "image_url": f"/api/miniapp/circle/posts/{post.id}/image",
            "thumb_url": f"/api/miniapp/circle/posts/{post.id}/thumb",
            "likers": (likers or {}).get(post.id, []),
            "like_count": post.like_count,
            "liked_by_me": bool(liked),
            "admin_liked": bool(post.admin_liked),
            "is_pinned": bool(post.is_pinned),
            "is_mine": post.parent_id == viewer_parent_id,
            "created_at": post.created_at.strftime("%Y-%m-%d %H:%M") if post.created_at else "",
        }

    # ---------- 点赞 ----------

    def like(self, parent: Parent, post_id: int, child_id: int | None = None) -> dict:
        post = (
            self.db.query(CirclePost)
            .filter(CirclePost.id == post_id, CirclePost.is_deleted == 0)
            .first()
        )
        if not post:
            raise NotFoundError("帖子不存在")
        # 一心一赞：软删行复活（同 FavoriteService B5 先例——唯一索引不含 is_deleted）
        existing = (
            self.db.query(CircleLike)
            .filter(CircleLike.post_id == post_id, CircleLike.parent_id == parent.id)
            .first()
        )
        # 展示名义快照（C3/C4）：记录点赞那一刻家长选中的孩子；未传则 NULL（老版本端兼容）
        liker_child = None
        if child_id:
            liker_child = (
                self.db.query(Child)
                .filter(Child.id == child_id, Child.parent_id == parent.id, Child.is_deleted == 0)
                .first()
            )
        if existing and existing.is_deleted == 0:
            return {"post_id": post_id, "like_count": post.like_count, "liked": True}
        if existing:
            existing.is_deleted = 0
            if liker_child and not existing.liker_child_id:
                existing.liker_child_id = liker_child.id
        else:
            self.db.add(
                CircleLike(
                    post_id=post_id,
                    parent_id=parent.id,
                    liker_child_id=liker_child.id if liker_child else None,
                )
            )
        # 原子 UPDATE（禁读改写——并发红线）
        self.db.execute(
            update(CirclePost)
            .where(CirclePost.id == post_id)
            .values(like_count=CirclePost.like_count + 1)
        )
        self.db.flush()
        # 被赞通知（同事务；自己赞自己不发）
        if post.parent_id != parent.id:
            # C3：文案切孩子名义（「Tommy 赞了你的成就」）；无名义则降级家长显示名（C4 兼容）
            liker_name = _display_name(liker_child) if liker_child else _parent_display(parent)
            self._notify_liked(
                post,
                liker_name=liker_name,
                dedup_key=f"parent:{parent.id}",
                liker_child_id=liker_child.id if liker_child else None,
            )
        self.db.commit()
        fresh = self.db.query(CirclePost.like_count).filter(CirclePost.id == post_id).scalar()
        return {"post_id": post_id, "like_count": fresh, "liked": True}

    def unlike(self, parent: Parent, post_id: int) -> dict:
        post = (
            self.db.query(CirclePost)
            .filter(CirclePost.id == post_id, CirclePost.is_deleted == 0)
            .first()
        )
        if not post:
            raise NotFoundError("帖子不存在")
        row = (
            self.db.query(CircleLike)
            .filter(
                CircleLike.post_id == post_id,
                CircleLike.parent_id == parent.id,
                CircleLike.is_deleted == 0,
            )
            .first()
        )
        if not row:
            return {"post_id": post_id, "like_count": post.like_count, "liked": False}
        row.is_deleted = 1
        # 原子 UPDATE -1（0 下限防负数）
        self.db.execute(
            update(CirclePost)
            .where(CirclePost.id == post_id, CirclePost.like_count > 0)
            .values(like_count=CirclePost.like_count - 1)
        )
        self.db.commit()
        fresh = self.db.query(CirclePost.like_count).filter(CirclePost.id == post_id).scalar()
        return {"post_id": post_id, "like_count": fresh, "liked": False}

    def _notify_liked(
        self,
        post: CirclePost,
        *,
        liker_name: str,
        dedup_key: str,
        admin: bool = False,
        liker_child_id: int | None = None,
    ) -> None:
        """被赞通知（scene=circle.liked；馆长赞特殊文案「馆长赞了 X 的成就」；
        dedup_key=点赞者（同帖同家长重赞不重复轰炸；馆长走 admin 键）。"""
        child = self.db.query(Child).filter(Child.id == post.child_id).first()
        child_name = _display_name(child) if child else ""
        if admin:
            content = f"馆长赞了 {child_name} 的成就"
            title = "馆长为你点赞"
        else:
            content = f"{liker_name} 赞了 {child_name} 的成就"
            title = "收到点赞"
        NotificationService(self.db).send(
            parent_id=post.parent_id,
            scene=SCENE_CIRCLE_LIKED,
            title=title,
            content=content,
            category=Notification.CATEGORY_OTHER,
            child_id=post.child_id,
            ref_type="child" if liker_child_id else "circle_post",
            ref_id=str(liker_child_id or post.id),
            dedup_key=dedup_key,
        )

    # ---------- 删除 ----------

    def delete_post(self, parent: Parent, post_id: int) -> dict:
        """家长删自己的帖（软删；帖子不可编辑+终身一晒口径保留）。"""
        post = (
            self.db.query(CirclePost)
            .filter(CirclePost.id == post_id, CirclePost.is_deleted == 0)
            .first()
        )
        if not post:
            raise NotFoundError("帖子不存在")
        if post.parent_id != parent.id:
            raise ValidationError("只能删除自己发布的帖子")
        post.is_pinned = 0  # 删帖联动取消置顶
        post.soft_delete()
        self.db.commit()
        return {"id": post_id, "deleted": True}
