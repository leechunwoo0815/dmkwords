# backend/domain/identity/service.py — 家长/孩子/会员开通（OrderService 已拆 order_service.py）
"""事务纪律：Service 统一 commit；金额全程 Decimal；审计走 EventBus（publish_audit）。"""

from __future__ import annotations

from datetime import date, datetime, timedelta

from sqlalchemy import func, or_
from sqlalchemy.orm import Session

from backend.common.exceptions import ConflictError, NotFoundError, ValidationError
from backend.common.notification_models import Notification
from backend.common.notifications import SCENE_MEMBER_EXPIRE_REMIND, NotificationService
from backend.domain.catalog.audit_events import publish_audit
from backend.domain.identity.models import Child, Order, Parent


class ParentService:
    def __init__(self, db: Session):
        self.db = db

    def create(self, admin, req) -> Parent:
        if (
            self.db.query(func.count(Parent.id))
            .filter(Parent.phone == req.phone, Parent.is_deleted == 0)
            .scalar()
        ):
            raise ConflictError(f"手机号 {req.phone} 已存在家长账号")
        parent = Parent(name=req.name, phone=req.phone, remark=req.remark)
        self.db.add(parent)
        self.db.flush()
        publish_audit(
            self.db,
            admin=admin,
            action="parent.create",
            target_type="parent",
            target_id=str(parent.id),
            detail={"name": req.name, "phone": req.phone},
        )
        self.db.commit()
        return parent

    def get(self, parent_id: int) -> Parent:
        parent = (
            self.db.query(Parent).filter(Parent.id == parent_id, Parent.is_deleted == 0).first()
        )
        if not parent:
            raise NotFoundError("家长不存在")
        return parent

    def list_children(self, parent_id: int) -> list[Child]:
        return (
            self.db.query(Child)
            .filter(Child.parent_id == parent_id, Child.is_deleted == 0)
            .order_by(Child.id)
            .all()
        )

    def search(self, keyword: str | None = None, limit: int = 20) -> list[Parent]:
        """家长搜索（W1 建档选择器：姓名/手机号模糊匹配）。"""
        q = self.db.query(Parent).filter(Parent.is_deleted == 0)
        if keyword:
            like = f"%{keyword}%"
            q = q.filter(or_(Parent.name.like(like), Parent.phone.like(like)))
        return q.order_by(Parent.id.desc()).limit(limit).all()

    # ---- WM3-B1 家长编辑/删除（订单守卫）----
    @staticmethod
    def has_orders(db: Session, parent_id: int) -> bool:
        """名下任一孩子存在未删订单（任意状态含 cancelled）→ 守卫口径（用户拍板）。"""
        return (
            db.query(func.count(Order.id))
            .filter(Order.parent_id == parent_id, Order.is_deleted == 0)
            .scalar()
        ) > 0

    @staticmethod
    def children_count(db: Session, parent_ids: list[int]) -> dict[int, int]:
        if not parent_ids:
            return {}
        rows = (
            db.query(Child.parent_id, func.count(Child.id))
            .filter(Child.parent_id.in_(parent_ids), Child.is_deleted == 0)
            .group_by(Child.parent_id)
            .all()
        )
        return dict(rows)

    def update(self, admin, parent_id: int, req) -> Parent:
        parent = self.get(parent_id)
        if self.has_orders(self.db, parent_id):
            raise ConflictError("该家长名下已创建订单，禁止修改")
        changed = []
        if req.name is not None:
            parent.name = req.name
            changed.append("name")
        if req.phone is not None and req.phone != parent.phone:
            dup = (
                self.db.query(func.count(Parent.id))
                .filter(Parent.phone == req.phone, Parent.is_deleted == 0, Parent.id != parent_id)
                .scalar()
            )
            if dup:
                raise ValidationError(f"手机号 {req.phone} 已存在家长账号")
            parent.phone = req.phone
            changed.append("phone")
        if req.remark is not None:
            parent.remark = req.remark
            changed.append("remark")
        if not changed:
            raise ValidationError("没有可更新的字段")
        publish_audit(
            self.db,
            admin=admin,
            action="parent.update",
            target_type="parent",
            target_id=str(parent.id),
            detail={"fields": changed, "phone": parent.phone},
            reason="手机号即小程序登录标识，修改后家长需用新号登录",
        )
        self.db.commit()
        return parent

    def delete(self, admin, parent_id: int) -> None:
        if self.has_orders(self.db, parent_id):
            raise ConflictError("该家长名下已创建订单，禁止删除")
        parent = self.get(parent_id)
        parent.is_deleted = 1
        # 名下未删孩子一并软删（孤儿档案必须清；守卫已确保这些孩子无订单）
        self.db.query(Child).filter(Child.parent_id == parent_id, Child.is_deleted == 0).update(
            {"is_deleted": 1}
        )
        publish_audit(
            self.db,
            admin=admin,
            action="parent.delete",
            target_type="parent",
            target_id=str(parent.id),
            detail={"name": parent.name, "phone": parent.phone},
        )
        self.db.commit()

    def list_page(
        self, page: int, page_size: int, keyword: str | None = None
    ) -> tuple[list[tuple[Parent, int, bool]], int]:
        """家长管理 tab 分页（含 children_count / has_orders）。"""
        q = self.db.query(Parent).filter(Parent.is_deleted == 0)
        if keyword:
            like = f"%{keyword}%"
            q = q.filter(or_(Parent.name.like(like), Parent.phone.like(like)))
        total = q.count()
        q = q.order_by(Parent.id.desc())
        parents = q.offset((page - 1) * page_size).limit(page_size).all()
        counts = self.children_count(self.db, [p.id for p in parents])
        out = [(p, counts.get(p.id, 0), self.has_orders(self.db, p.id)) for p in parents]
        return out, total


class ChildService:
    def __init__(self, db: Session):
        self.db = db

    def create(self, admin, parent_id: int, req) -> Child:
        ParentService(self.db).get(parent_id)
        child = Child(
            parent_id=parent_id,
            name=req.name,
            english_name=req.english_name,
            gender=req.gender,
            birthday=req.birthday,
            grade=req.grade,
            member_status=Child.MEMBER_NONE,
        )
        self.db.add(child)
        self.db.flush()
        publish_audit(
            self.db,
            admin=admin,
            action="child.create",
            target_type="child",
            target_id=str(child.id),
            detail={"name": req.name, "parent_id": parent_id},
        )
        self.db.commit()
        return child

    def _transition(self, child: Child, new_status: str) -> None:
        if not child.can_transition(new_status):
            raise ValidationError(
                f"会员状态不允许从 {child.member_status} 变更为 {new_status}（转移矩阵拦截）"
            )
        child.member_status = new_status

    def _get_child(self, child_id: int) -> Child:
        child = self.db.query(Child).filter(Child.id == child_id, Child.is_deleted == 0).first()
        if not child:
            raise NotFoundError("孩子不存在")
        return child

    # ---- WM3-B1 孩子编辑/删除（订单守卫）----
    @staticmethod
    def has_orders(db: Session, child_id: int) -> bool:
        """该孩子存在未删订单（任意状态含 cancelled）→ 守卫口径（用户拍板）。"""
        return (
            db.query(func.count(Order.id))
            .filter(Order.child_id == child_id, Order.is_deleted == 0)
            .scalar()
        ) > 0

    def update_profile(
        self,
        admin,
        child_id: int,
        english_name: str | None = None,
        grade: str | None = None,
        ar_level: str | None = None,
        name: str | None = None,
        gender: int | None = None,
        birthday=None,
    ) -> Child:
        """维护孩子资料（C19 + WM3-B1 扩展：姓名/性别/生日全开）；AR 只升不降 + 订单守卫。"""
        child = self._get_child(child_id)
        # F7 守卫口径细化（用户拍板 2026-09-01）：身份字段（姓名/性别/生日）有订单
        # 禁改——学籍动态字段（英文名/年级/AR）放开（年级随学年变化，教学属性）。
        # 删除守卫不变（有订单禁删）；AR 只升不降逻辑保留。
        if self.has_orders(self.db, child_id) and any(
            f is not None for f in (name, gender, birthday)
        ):
            raise ConflictError("身份字段（姓名/性别/生日）已创建订单禁止修改，英文名/年级/AR 可改")
        changed = []
        if name is not None:
            child.name = name
            changed.append("name")
        if gender is not None:
            child.gender = gender
            changed.append("gender")
        if birthday is not None:
            child.birthday = birthday
            changed.append("birthday")
        if english_name is not None:
            child.english_name = english_name or None
            changed.append("english_name")
        if grade is not None:
            child.grade = grade
            changed.append("grade")
        if ar_level is not None:
            current = child.ar_level
            try:
                cur_v = float(current) if current is not None else -1.0
            except ValueError:
                cur_v = 0.0
            try:
                new_v = float(ar_level)
            except ValueError:
                raise ValidationError("AR 值必须是数字（如 3.5）") from None
            if new_v < 0:
                raise ValidationError("AR 值不能为负数")
            if current is None or new_v > cur_v:
                child.ar_level = f"{new_v:.1f}"
                changed.append("ar_level")
            else:
                raise ValidationError(f"AR 值只升不降（当前 {current}，提交 {ar_level}）")
        if not changed:
            raise ValidationError("没有可更新的字段")
        publish_audit(
            self.db,
            admin=admin,
            action="child.update_profile",
            target_type="child",
            target_id=str(child.id),
            detail={"fields": changed, "ar_level": child.ar_level, "grade": child.grade},
            reason="维护孩子资料（老师评估）",
        )
        self.db.commit()
        return child

    def delete(self, admin, child_id: int) -> None:
        """软删孩子档案（WM3-B1；订单守卫 409）。"""
        child = self._get_child(child_id)
        if self.has_orders(self.db, child_id):
            raise ConflictError("该孩子已创建订单，禁止删除")
        child.is_deleted = 1
        publish_audit(
            self.db,
            admin=admin,
            action="child.delete",
            target_type="child",
            target_id=str(child.id),
            detail={"name": child.name, "parent_id": child.parent_id},
        )
        self.db.commit()

    def mark_pending_evaluation(self, admin, child_id: int, reason: str) -> Child:
        """观察期 → 待评估（C13/决策 8：馆员手动标记留痕；到期自动转换任务在 WM11）。"""
        child = self._get_child(child_id)
        if child.member_status != Child.MEMBER_OBSERVATION:
            raise ValidationError(f"仅观察期孩子可标记待评估（当前状态 {child.member_status}）")
        self._transition(child, Child.MEMBER_PENDING_EVALUATION)
        self.db.flush()
        publish_audit(
            self.db,
            admin=admin,
            action="child.mark_pending_evaluation",
            target_type="child",
            target_id=str(child.id),
            detail={
                "child": child.name,
                "from": Child.MEMBER_OBSERVATION,
                "to": Child.MEMBER_PENDING_EVALUATION,
            },
            reason=reason or "观察期到期，标记待评估",
        )
        self.db.commit()
        return child

    def evaluate_approve(self, admin, child_id: int, reason: str) -> Order:
        """评估通过转正（C13/R-101-5）：转正必须支付正式年费 → 创建年费订单（二孩折扣
        沿用 OrderService 判定），收款确认后自动转正式会员。审计与订单同一事务提交。"""
        child = self._get_child(child_id)
        if child.member_status != Child.MEMBER_PENDING_EVALUATION:
            raise ValidationError(f"仅待评估孩子可评估通过转正（当前状态 {child.member_status}）")
        if child.operation_locked:
            raise ValidationError("孩子正在转让/退会审核流程中，不能办理转正")
        publish_audit(
            self.db,
            admin=admin,
            action="child.evaluate_approve",
            target_type="child",
            target_id=str(child.id),
            detail={"child": child.name, "next": "创建年费订单，收款确认后转正"},
            reason=reason or "评估通过，转正式会员",
        )
        from backend.domain.identity.schemas import OrderCreateRequest

        req = OrderCreateRequest(
            child_id=child_id,
            order_type=Order.TYPE_FORMAL,
            remark=reason or "评估通过转正",
        )
        return OrderService(self.db).create(admin, req)

    def list_children(self, page: int, page_size: int, keyword: str | None, status: str | None):
        q = self.db.query(Child).filter(Child.is_deleted == 0)
        if keyword:
            like = f"%{keyword}%"
            q = q.join(Parent, Child.parent_id == Parent.id).filter(
                or_(
                    Child.name.like(like),
                    Child.english_name.like(like),
                    Parent.phone.like(like),
                    Parent.name.like(like),
                )
            )
        else:
            q = q.join(Parent, Child.parent_id == Parent.id)
        if status:
            q = q.filter(Child.member_status == status)
        total = q.count()
        rows = (
            q.add_columns(Parent.name, Parent.phone)
            .order_by(Child.id.desc())
            .offset((page - 1) * page_size)
            .limit(page_size)
            .all()
        )
        return rows, total

    def expire_due_members(self) -> int:
        """D1 第 3 层（WM11 定时任务）：到期会员落库。

        - 正式会员 member_expire 已过 → expired（读时判定是拦截防线，落库是状态口径/榜单历史）
        - 观察期到期未评估 → pending_evaluation（C13 自动转换；馆员待评估名单）
        幂等：落库后状态不在查询范围，重复跑无副作用。不发家长通知（PRD §十无"已到期"通知项）。
        """
        today = date.today()
        due = (
            self.db.query(Child)
            .filter(
                Child.is_deleted == 0,
                Child.member_status.in_([Child.MEMBER_FORMAL, Child.MEMBER_OBSERVATION]),
                Child.member_expire.isnot(None),
                Child.member_expire < today,
            )
            .all()
        )
        changed = 0
        for child in due:
            if child.member_status == Child.MEMBER_FORMAL:
                self._transition(child, Child.MEMBER_EXPIRED)
            elif child.member_status == Child.MEMBER_OBSERVATION:
                self._transition(child, Child.MEMBER_PENDING_EVALUATION)
            changed += 1
        if changed:
            self.db.commit()
        return changed

    def pending_evaluation_weekly(self) -> int:
        """待评估每周名单（PRD §12 提醒行）：超过 N 天未评估的待评估孩子计数（馆员看板跟进）。"""
        from backend.common.config_service import ConfigService

        threshold = int(ConfigService(self.db).get_value("pending_evaluation_remind_days", "7"))
        cutoff = datetime.now() - timedelta(days=threshold)
        return (
            self.db.query(func.count(Child.id))
            .filter(
                Child.is_deleted == 0,
                Child.member_status == Child.MEMBER_PENDING_EVALUATION,
                Child.update_time < cutoff,
            )
            .scalar()
        )

    def member_expire_remind(self) -> int:
        """会员到期提醒（PRD §12：前 30/14/7 天 + 当天；每节点只发一次）。

        幂等：dedup_key=提醒节点值，唯一索引防重复。只提醒 formal 且未过期。
        """
        from backend.common.config_service import ConfigService

        notify_days = [
            int(x)
            for x in ConfigService(self.db)
            .get_value("member_expire_remind_days", "30,14,7,0")
            .split(",")
            if x.strip() != ""
        ]
        if not notify_days:
            return 0
        today = date.today()
        sent = 0
        for days in sorted(set(notify_days)):
            target = today + timedelta(days=days)
            children = (
                self.db.query(Child)
                .filter(
                    Child.is_deleted == 0,
                    Child.member_status == Child.MEMBER_FORMAL,
                    Child.member_expire.isnot(None),
                    Child.member_expire == target,
                )
                .all()
            )
            for child in children:
                parent = self.db.query(Parent).filter(Parent.id == child.parent_id).first()
                label = "今天" if days == 0 else f"{days} 天后"
                if NotificationService(self.db).send(
                    parent_id=child.parent_id,
                    scene=SCENE_MEMBER_EXPIRE_REMIND,
                    title="会员续费提醒",
                    content=(
                        f"孩子 {child.name} 的正式会员将在{label}（{target:%Y-%m-%d}）到期，"
                        f"请及时续费以免影响阅读。"
                    ),
                    category=Notification.CATEGORY_MEMBER,
                    child_id=child.id,
                    ref_type="child",
                    ref_id=str(child.id),
                    dedup_key=str(days),
                    openid=parent.wechat_openid if parent else None,
                ):
                    sent += 1
        if sent:
            self.db.commit()
        return sent


# god file 整改（WM3 插修1，876 行 > 800）：OrderService 拆出 order_service.py；
# re-export 保持既有引用路径（router/wm10_service/billing 等零改动）
from backend.domain.identity.order_service import (  # noqa: F401,E402
    OrderService,
)
