# backend/domain/identity/service.py — 家长/孩子/会员开通（OrderService 已拆 order_service.py）
"""事务纪律：Service 统一 commit；金额全程 Decimal；审计走 EventBus（publish_audit）。"""

from __future__ import annotations

from datetime import date, datetime, timedelta

from sqlalchemy import func, or_
from sqlalchemy.orm import Session

from backend.common.exceptions import ConflictError, NotFoundError, ValidationError
from backend.common.notification_models import Notification
from backend.common.notifications import SCENE_MEMBER_EXPIRE_REMIND, NotificationService
from backend.common.sql_utils import escape_like
from backend.domain.catalog.audit_events import publish_audit
from backend.domain.identity.models import Child, Order, Parent


def attach_actor_profiles(db: Session, data: dict) -> dict:
    """给通知列表补「行为主体」的头像/等级（fix34 R0：阅读圈顶部"谁赞了你"要显头像）。

    为什么不在 `common/notifications`：架构关禁止 common 依赖业务域（等级在 ChildGrowthState、
    头像在 Child，都是业务域数据）——通知**表**在 common，**业务装饰**归各域自己补。
    ref_type=child 时 ref_id = 行为孩子 id（点赞者）→ 批查一次，禁逐条。
    """
    from backend.domain.growth.service import levels_map
    from backend.domain.identity.models import Child

    items = data.get("items") or []
    # 馆长赞的展示名固定为「馆长」——与"有没有孩子行为主体"无关，必须在下面
    # `if not ids: return` 之前处理（首版写在后面 → 通知里只有馆长赞时 actor_name 缺失）
    for i in items:
        if i.get("ref_type") == "circle_admin":
            i["actor_name"] = "馆长"
    ids = [
        int(i["ref_id"])
        for i in items
        if i.get("ref_type") == "child" and str(i.get("ref_id") or "").isdigit()
    ]
    if not ids:
        return data
    avatars = {
        cid: av
        for cid, av in db.query(Child.id, Child.avatar)
        .filter(Child.id.in_(ids), Child.is_deleted == 0)
        .all()
    }
    lv = levels_map(db, ids)
    keys = set(ids)
    names = {
        cid: (en or f"小朋友{cid:03d}")
        for cid, en in db.query(Child.id, Child.english_name).filter(Child.id.in_(ids)).all()
    }
    for i in items:
        # **只对 ref_type=child 的项做孩子维度查找**（fix34e5）：馆长赞的 ref_id 是
        # "帖子 id"，若恰与某个孩子 id 相同，会被误配上那个孩子的头像/等级
        # —— 演示现场"帖子 1 / 孩子 1"就能撞上，通知里就会冒出别人的头像。
        # 展示名（fix34e4）同样只在这里给：馆长已在上面统一置为「馆长」。
        if i.get("ref_type") != "child":
            # 形状统一：非孩子主体（馆长赞）显式给空值——前端凭 ref_type 换金光馆长资产，
            # 不靠"字段缺失"来判断（字段缺失型契约会逼前端写防御代码）
            i["actor_avatar"] = None
            i["actor_level"] = None
            continue
        key = int(i["ref_id"]) if str(i.get("ref_id") or "").isdigit() else 0
        if key in keys:
            i["actor_name"] = names.get(key, "")
            i["actor_avatar"] = avatars.get(key)
            i["actor_level"] = lv.get(key, "A")
    return data


class ParentService:
    def __init__(self, db: Session):
        self.db = db

    def create(self, admin, req) -> Parent:
        active = (
            self.db.query(Parent).filter(Parent.phone == req.phone, Parent.is_deleted == 0).first()
        )
        if active:
            raise ConflictError(f"手机号 {req.phone} 已存在家长账号")
        # P2-6（2026-10-17 第六十九批·大审查）：`parents.phone` 是**全局唯一索引（不含 is_deleted）**，
        # 而服务层查重只看未删行 → 软删家长后同手机号再建档必撞唯一索引（探针实测 1062 → 500），
        # 且没有恢复入口。按"同一手机号 = 同一个人"就地**复活**那一行（清软删 + 覆盖姓名/备注）；
        # 行锁保证两个馆员同时建档同一号码时串行（后到者看到已被复活 → ConflictError）。
        revived = (
            self.db.query(Parent)
            .filter(Parent.phone == req.phone, Parent.is_deleted != 0)
            .with_for_update()
            .populate_existing()  # B-16：锁定读必须刷新身份映射快照
            .first()
        )
        if revived is not None:
            revived.is_deleted = 0
            revived.name = req.name
            revived.remark = req.remark
            parent = revived
        else:
            parent = Parent(name=req.name, phone=req.phone, remark=req.remark)
            self.db.add(parent)
        self.db.flush()
        publish_audit(
            self.db,
            admin=admin,
            action="parent.create",
            target_type="parent",
            target_id=str(parent.id),
            detail={
                "name": req.name,
                "phone": req.phone,
                **({"revived_soft_deleted": True} if revived is not None else {}),
            },
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

    @staticmethod
    def validate_avatar(avatar: str | None) -> str | None:
        """WM15-R3：头像 id 白名单校验（防任意值注入；空=清空头像）。"""
        if avatar is None:
            return None
        v = (avatar or "").strip()
        if not v:
            return None
        from backend.domain.reading_circle.art import AVATAR_IDS

        if v not in AVATAR_IDS:
            raise ValidationError("头像不在可选范围内")
        return v

    def update_display_name(self, parent: Parent, display_name: str) -> dict:
        """家长自助改展示称呼（WM14-B Q11 清偿）：只允许该字段，空串=回退真实姓名。

        消费端三处（信息流署名 / 管理端列表 / 被赞通知）统一走
        reading_circle.service._parent_display（display_name or name）。
        """
        value = (display_name or "").strip()
        if len(value) > 20:
            raise ValidationError("称呼最多 20 个字")
        parent.display_name = value or None
        self.db.commit()
        return {"display_name": parent.display_name or "", "name": parent.name}

    def list_children(self, parent_id: int) -> list[Child]:
        return (
            self.db.query(Child)
            .filter(Child.parent_id == parent_id, Child.is_deleted == 0)
            .order_by(Child.id)
            .all()
        )

    #: 建档选择器远程搜索的条数上限（P2-44：上限经响应头如实告知）
    SEARCH_LIMIT = 20

    def search(self, keyword: str | None = None, limit: int = SEARCH_LIMIT) -> list[Parent]:
        """家长搜索（W1 建档选择器：姓名/手机号模糊匹配）。"""
        q = self.db.query(Parent).filter(Parent.is_deleted == 0)
        if keyword:
            like = f"%{escape_like(keyword)}%"
            q = q.filter(
                or_(Parent.name.like(like, escape="\\"), Parent.phone.like(like, escape="\\"))
            )
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
    def has_orders_map(db: Session, parent_ids: list[int]) -> set[int]:
        """**批量**版 has_orders（P3 性能族，2026-10-22 第一百一十五批）。

        为什么：列表页原先逐行调 `has_orders` → 每页 1+N 条 COUNT（page_size 上限 100）。
        这里一条 `GROUP BY parent_id` 出集合，列表处改 `p.id in set`。**单行守卫路径
        （编辑/删除家长）继续用 `has_orders`**——那里就是要立刻对。
        """
        if not parent_ids:
            return set()
        rows = (
            db.query(Order.parent_id)
            .filter(Order.parent_id.in_(parent_ids), Order.is_deleted == 0)
            .group_by(Order.parent_id)
            .all()
        )
        return {pid for (pid,) in rows}

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
            # 第八轮审查 P1-9（与第七轮 P1-4 软删书目同 ISBN 同族）：
            # `parents.phone` 是**全局唯一索引（不含 is_deleted）**，而查重只看未删行
            # → "改成一个属于**已删除档案**的号码"会穿过服务层，落库时撞唯一索引 = **HTTP 500**。
            # 改号是"账号换手"，目标号不能属于任何（含已删）档案：想用这个号请走建档流程
            # （`create` 对软删同号是**复活**那一行，见上）。查重必须含软删。
            taken = (
                self.db.query(Parent)
                .filter(Parent.phone == req.phone, Parent.id != parent_id)
                .first()
            )
            if taken is not None:
                if taken.is_deleted:
                    raise ValidationError(
                        f"手机号 {req.phone} 属于一个已删除的家长档案，不能改号占用"
                        "（如需恢复该档案，请在建档入口用此号码新建）"
                    )
                raise ValidationError(f"手机号 {req.phone} 已存在家长账号")
            # 2026-10-12 拍板（P3）：手机号是登录账号，**改号 = 档案换手** → 旧登录一律立刻失效。
            # 不撤的话旧号持有人最长 30 天还能看这个家庭的孩子/会员码/订单/评估报告（实测路径）。
            # 家长重新登录一次的成本远低于泄露代价。
            # P1-9：撤销动作内部 `commit()`（token_generation +1 = **强制下线**），
            # 原先写在查重**之前** → 改号失败但家长已被踢下线（实测 V7：500 且 gen 0→1）。
            # 现在只在全部校验通过后执行；并发下若仍撞唯一索引，转 422 而不是 500。
            from sqlalchemy.exc import IntegrityError

            from backend.domain.identity.service import revoke_parent_tokens

            parent.phone = req.phone
            try:
                revoke_parent_tokens(self.db, parent)
            except IntegrityError as e:
                self.db.rollback()
                raise ValidationError(f"手机号 {req.phone} 已被占用，请刷新后重试") from e
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
            like = f"%{escape_like(keyword)}%"
            q = q.filter(
                or_(Parent.name.like(like, escape="\\"), Parent.phone.like(like, escape="\\"))
            )
        total = q.count()
        q = q.order_by(Parent.id.desc())
        parents = q.offset((page - 1) * page_size).limit(page_size).all()
        counts = self.children_count(self.db, [p.id for p in parents])
        has_orders_set = self.has_orders_map(self.db, [p.id for p in parents])
        out = [(p, counts.get(p.id, 0), p.id in has_orders_set) for p in parents]
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
            avatar=ParentService.validate_avatar(getattr(req, "avatar", None)),
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
        # P2-13（2026-10-17 第六十九批·大审查）：状态写口收拢——顺带维护 `pending_evaluation_at`。
        # P1-1（2026-10-18 第七十五批）：改走 `member_status.write_member_status`，
        # 连带动作（重新入会 → 复位已退还押金）对所有入口生效。
        from backend.domain.identity.member_status import write_member_status

        write_member_status(self.db, child, new_status)

    def update_avatar(self, child_id: int, avatar: str) -> Child:
        """WM15-R3：家长自助设置孩子内置头像（白名单；空串=清空）。"""
        child = self._get_child(child_id)
        child.avatar = ParentService.validate_avatar(avatar)
        self.db.commit()
        return child

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
        avatar: str | None = None,
        clear_birthday: bool = False,
    ) -> Child:
        """维护孩子资料（C19 + WM3-B1 扩展：姓名/性别/生日全开）；AR 只升不降 + 订单守卫。"""
        child = self._get_child(child_id)
        # F7 守卫口径细化（用户拍板 2026-09-01）：身份字段（姓名/性别/生日）有订单
        # 禁改——学籍动态字段（英文名/年级/AR）放开（年级随学年变化，教学属性）。
        # 删除守卫不变（有订单禁删）；AR 只升不降逻辑保留。
        # B6（插修5）：守卫判"值变更"而非"字段提交"——前端全量表单原样回传
        # name/gender/birthday 时不得误拦（gender=0 合法值故保留 is not None 前置）。
        if self.has_orders(self.db, child_id) and any(
            f is not None and f != cur
            for f, cur in ((name, child.name), (gender, child.gender), (birthday, child.birthday))
        ):
            raise ConflictError("身份字段（姓名/性别/生日）已创建订单禁止修改，英文名/年级/AR 可改")
        changed = []
        if name is not None:
            child.name = name
            changed.append("name")
        if gender is not None:
            child.gender = gender
            changed.append("gender")
        if clear_birthday:
            # P2-60（2026-10-20 第八轮）：**显式清空生日**。原先只有 `is not None` 分支，
            # 前端无论送 null 还是省略都等于"不改" → 生日**永远清不掉**（表单里删了、保存后还在）。
            # 现在前端送显式 null，路由据 `model_fields_set` 传 clear_birthday=True 到这里。
            child.birthday = None
            changed.append("birthday")
        elif birthday is not None:
            child.birthday = birthday
            changed.append("birthday")
        if english_name is not None:
            child.english_name = english_name or None
            changed.append("english_name")
        if grade is not None:
            child.grade = grade
            changed.append("grade")
        if avatar is not None:
            # WM15-R3：头像 id 白名单（"__clear__" 语义：显式清空）
            child.avatar = None if avatar == "__clear__" else ParentService.validate_avatar(avatar)
            changed.append("avatar")
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
        """软删孩子档案（WM3-B1；订单守卫 409 + 冻结守卫 422）。"""
        child = self._get_child(child_id)
        # 2026-10-13（第五十一轮·BDD 转让解封亲验）：**冻结期禁删档案**。原实现只靠订单守卫，
        # 而**受让方**（none/withdrawn 状态）没有订单 → 冻结期间可被删档，转让单随即指向已删孩子
        # （PRD §3.5 第 3 条"两个孩子的相关操作暂时冻结"）。转出方本来就被订单守卫拦住，
        # 这里补上冻结判据，把"双方"都收口。
        if child.operation_locked:
            raise ValidationError("孩子正在转让/退会审核流程中，不能删除档案")
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

    @staticmethod
    def _audit_rejected_change(admin, child: Child, *, to: str, guard: str) -> None:
        """G13（2026-10-14 第五十九批）：被守卫拒绝的**状态变更尝试**留痕（独立会话）。

        纯状态守卫（不是 matrix 拦截）走这里；留痕必须独立于主事务——拒绝即抛异常，
        同事务写审计会随回滚一起丢（详见 `audit_events.publish_rejection_audit`）。
        """
        from backend.domain.catalog.audit_events import publish_rejection_audit

        publish_rejection_audit(
            actor=admin,
            action="member.transition_rejected",
            target_type="child",
            target_id=str(child.id),
            detail={"child": child.name, "from": child.member_status, "to": to, "guard": guard},
            reason=f"状态变更被拒：{guard}",
        )

    def mark_pending_evaluation(self, admin, child_id: int, reason: str) -> Child:
        """观察期 → 待评估（C13/决策 8：馆员手动标记留痕；到期自动转换任务在 WM11）。"""
        child = self._get_child(child_id)
        if child.member_status != Child.MEMBER_OBSERVATION:
            self._audit_rejected_change(
                admin,
                child,
                to=Child.MEMBER_PENDING_EVALUATION,
                guard="仅观察期孩子可标记待评估",
            )
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
            self._audit_rejected_change(
                admin,
                child,
                to=Child.MEMBER_FORMAL,
                guard="仅待评估孩子可评估通过转正",
            )
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
            like = f"%{escape_like(keyword)}%"
            q = q.join(Parent, Child.parent_id == Parent.id).filter(
                or_(
                    Child.name.like(like, escape="\\"),
                    Child.english_name.like(like, escape="\\"),
                    Parent.phone.like(like, escape="\\"),
                    Parent.name.like(like, escape="\\"),
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
            .with_for_update()
            .populate_existing()  # E-10/T19：锁定读刷新快照，防并发续费被旧快照覆盖
            .all()
        )
        changed = 0
        for child in due:
            # 锁后逐行复查谓词（RC 读最新已提交）：锁等待期间若已续费则不再推进
            if not (
                child.member_expire
                and child.member_expire < today
                and child.member_status in (Child.MEMBER_FORMAL, Child.MEMBER_OBSERVATION)
            ):
                continue
            if child.member_status == Child.MEMBER_FORMAL:
                self._transition(child, Child.MEMBER_EXPIRED)
            elif child.member_status == Child.MEMBER_OBSERVATION:
                self._transition(child, Child.MEMBER_PENDING_EVALUATION)
            changed += 1
        if changed:
            self.db.commit()
        return changed

    def pending_evaluation_weekly(self) -> int:
        """待评估每周名单（PRD §3.3）：超过 N 天未评估的孩子 → **管理端待办提醒馆员跟进**。

        2026-10-14（第五十九批 G12）：原实现只返回一个计数——PRD 原文要求"系统每周自动整理一份
        『待评估超过 7 天』的孩子名单**提醒馆员跟进**"，计数落不到任何人手上（缺口登记于第五十八轮）。
        现在逐人写待办（`admin.pending_evaluation`），显示态由 `todo_service` 按会员状态推导
        （转正/退会即自动审结），`dedup_key` 取 **ISO 周** → 同一周重复跑只留一条，下周再提醒一次。
        返回值仍是**名单人数**（任务看板 processed 口径不变）。
        """
        from backend.common.admin_notification_models import AdminNotification
        from backend.common.admin_notifications import AdminNotifyService
        from backend.common.config_service import ConfigService

        threshold = int(ConfigService(self.db).get_value("pending_evaluation_remind_days", "7"))
        now = datetime.now()
        cutoff = now - timedelta(days=threshold)
        # P2-13（2026-10-17 第六十九批·大审查）：判据从 `update_time` 改为**进入待评估的时刻**
        # （`pending_evaluation_at`）。原判据下，家长自助换头像这类无关更新会把时钟归零 →
        # 该孩子从每周名单与待办里消失（"待评估超过 N 天"永远凑不满 N 天）。
        rows = (
            self.db.query(Child)
            .filter(
                Child.is_deleted == 0,
                Child.member_status == Child.MEMBER_PENDING_EVALUATION,
                Child.pending_evaluation_at.isnot(None),
                Child.pending_evaluation_at < cutoff,
            )
            .all()
        )
        iso_year, iso_week, _ = now.isocalendar()
        for child in rows:
            days = max(threshold, (now - child.pending_evaluation_at).days)
            AdminNotifyService(self.db).send(
                scene=AdminNotification.SCENE_PENDING_EVALUATION,
                title="待评估跟进",
                content=f"{child.name} 待评估已 {days} 天，请及时跟进评估（转正或办理退会）。",
                ref_type=AdminNotification.REF_CHILD,
                ref_id=child.id,
                applicant_name=child.name,
                dedup_key=f"week:{iso_year}-{iso_week}",
            )
        if rows:
            self.db.commit()
        return len(rows)

    def member_expire_remind(self) -> int:
        """会员到期提醒（PRD §12：前 30/14/7 天 + 当天；每节点只发一次）。

        幂等：dedup_key=提醒节点值，唯一索引防重复。只提醒 formal 且未过期。

        P2-41（2026-10-20 第八轮）：**消 N+1 + 分块提交**——原先每个孩子各查一次
        `Parent`（N 条）+ 每条一次通知存在性 SELECT（第 2N 条），且整批一个事务。
        现在家长一次 `IN`、已发键一次 `IN` 预取，每 `BATCH_COMMIT_CHUNK` 条提交一次。
        """
        from backend.common.batch import commit_every, fetch_by_ids
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
        done = 0
        # P2-5（2026-10-09 审查）：补发窗口——原按"节点日"精确匹配（member_expire == target），
        # 停机一整天该节点永久漏发。现在向前放宽 1 天，文案按实际剩余天数算；
        # 幂等键仍是节点值 `str(days)` → 补发不重复。
        catchup = timedelta(days=1)
        for days in sorted(set(notify_days)):
            target = today + timedelta(days=days)
            children = (
                self.db.query(Child)
                .filter(
                    Child.is_deleted == 0,
                    Child.member_status == Child.MEMBER_FORMAL,
                    Child.member_expire.isnot(None),
                    Child.member_expire >= target - catchup,
                    Child.member_expire <= target,
                )
                .all()
            )
            if not children:
                continue
            # 批量上下文（P2-41）：家长一次 IN + 已发键一次 IN（原先是 2N 条 SELECT）
            parents = {
                p.id: p for p in fetch_by_ids(self.db, Parent, [c.parent_id for c in children])
            }
            already = NotificationService.existing_keys(
                self.db,
                scene=SCENE_MEMBER_EXPIRE_REMIND,
                ref_type="child",
                ref_ids=[c.id for c in children],
            )
            for child in children:
                done += 1
                if (str(child.id), f"{child.member_expire}-{days}", child.parent_id) in already:
                    continue  # 该节点该覆盖期已发过（本任务每小时跑一次）
                parent = parents.get(child.parent_id)
                actual_days = (child.member_expire - today).days
                label = (
                    "今天"
                    if actual_days == 0
                    else (f"{actual_days} 天后" if actual_days > 0 else "已过期")
                )
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
                    # 第四轮大审查 P1-5（2026-10-17）：键必须带**覆盖期**。原先是 `str(days)`
                    # （30/14/7/0），而 ref_id 是终身不变的 child_id → 家长续费一年后再次进入
                    # 同一窗口时命中上一周期那行 → **此后再也收不到任何到期提醒**（实测 1 → 0 条）。
                    # 口径与 `circulation/followups.py` 的 `renew:{member_expire}` 同款：
                    # 一"期"（同一个到期日）一个键，换期即新键。
                    dedup_key=f"{child.member_expire}-{days}",
                    openid=parent.wechat_openid if parent else None,
                ):
                    sent += 1
                commit_every(self.db, done)
        if sent:
            self.db.commit()
        return sent


# god file 整改（WM3 插修1，876 行 > 800）：OrderService 拆出 order_service.py；
# re-export 保持既有引用路径（router/wm10_service/billing 等零改动）
from backend.domain.identity.order_service import (  # noqa: F401,E402
    OrderService,
)


def revoke_parent_tokens(db, parent) -> None:
    """撤销该家长已签发的全部 token（P1-11，2026-10-09）。

    做法：`parents.token_generation + 1` —— token 载荷带 `gen`，代数不符即 401。
    放在 Service 层（Router 只调它）：架构关禁止 Router 出现 ORM 操作。
    """
    parent.token_generation = int(parent.token_generation or 0) + 1
    db.commit()
