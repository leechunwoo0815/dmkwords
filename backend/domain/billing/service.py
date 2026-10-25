# backend/domain/billing/service.py — 押金与赔偿（R-312）
"""押金全流程：缴纳（订单确认联动）→ 赔偿扣除 → 补缴 → 退会退款（WM10 接）。金额 Decimal。"""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal

from sqlalchemy import or_
from sqlalchemy.orm import Session

from backend.common.config_service import ConfigService
from backend.common.exceptions import NotFoundError, ValidationError
from backend.common.sql_utils import escape_like
from backend.domain.billing.models import Deposit, DepositLedger
from backend.domain.catalog.audit_events import publish_audit
from backend.domain.identity.models import Child, Order


class DepositService:
    def __init__(self, db: Session):
        self.db = db

    def _get_or_create(self, child_id: int) -> Deposit:
        dep = (
            self.db.query(Deposit)
            .filter(Deposit.child_id == child_id, Deposit.is_deleted == 0)
            .first()
        )
        if not dep:
            dep = Deposit(child_id=child_id, amount=Decimal("0"))
            self.db.add(dep)
            self.db.flush()
        return dep

    def _ledger(
        self, dep: Deposit, entry_type: str, amount: Decimal, reason: str, admin=None, copy_id=None
    ) -> None:
        self.db.add(
            DepositLedger(
                deposit_id=dep.id,
                entry_type=entry_type,
                amount=amount,
                balance_after=dep.available_amount,
                reason=reason,
                related_copy_id=copy_id,
                operator_id=admin.id if admin else None,
            )
        )

    # ---------- 缴纳（订单确认收款联动；identity.service 在 confirm 时调用） ----------
    def on_deposit_order_paid(self, admin, order: Order) -> Deposit:
        """押金订单（deposit / deposit_supplement）支付成功联动。"""
        child = self.db.query(Child).filter(Child.id == order.child_id).first()
        if not child:
            raise NotFoundError("孩子不存在")
        dep = (
            self.db.query(Deposit)
            .filter(Deposit.child_id == child.id, Deposit.is_deleted == 0)
            .with_for_update()
            .populate_existing()  # P1-F2 双保险：幂等检查基于锁定读（双确认时后到者阻塞后看到已缴）
            .first()
        )
        if dep is None:
            dep = Deposit(child_id=child.id, amount=Decimal("0"))  # 对齐 _get_or_create 建卡分支
            self.db.add(dep)
            self.db.flush()
        standard = Decimal(ConfigService(self.db).get_value("deposit_amount"))

        if order.order_type == Order.TYPE_DEPOSIT:
            # P2-1（2026-10-17 第六十九批·大审查）：原判据是"当前状态 == paid"——双击建出的
            # 第二笔押金单在中间发生过赔偿扣除/部分退款后状态已不是 paid → 盖章通过，
            # `amount/available_amount` 被**覆盖回标准额**（deducted_amount 不回落 → 台账三笔
            # 无法回推、家庭多付）。判据改为"本押金账户**是否已有本金**"（amount > 0）：
            # 已缴过就拒绝重复缴纳，不管中间发生过什么。
            if dep.amount > 0 or dep.status == Deposit.STATUS_PAID:
                raise ValidationError("押金已缴纳，请勿重复")
            # 金额取**订单实收**（`order.amount`），不取"确认时刻的当前配置"：建单后改配置时，
            # 原实现让 `dep.amount = standard` 而台账记 `order.amount` → 实收 ≠ 入账。
            received = (order.amount or Decimal("0")).quantize(Decimal("0.01"))
            dep.amount = received
            dep.available_amount = received
            dep.status = Deposit.STATUS_PAID
            self._ledger(dep, DepositLedger.ENTRY_PAY, received, "押金缴纳", admin)
        else:  # deposit_supplement 补缴（R-312：补至全额；E-20260909-03：金额含待结清）
            if dep.status not in (Deposit.STATUS_PARTIALLY_DEDUCTED, Deposit.STATUS_FULLY_DEDUCTED):
                raise ValidationError("押金当前无需补缴")
            # E-20260909-03（用户裁决「扣多少补多少」）：拆分入账——先恢复可用余额
            # 至标准额，余下部分结清待结清。原实现直接 available=标准额 且 unpaid 清零，
            # 超扣场景下待结清分文未收即豁免（扣 2000 只收 1200 的真金漏洞）。
            refill = min(order.amount, max(Decimal("0"), standard - dep.available_amount)).quantize(
                Decimal("0.01")
            )
            settle = (order.amount - refill).quantize(Decimal("0.01"))
            if settle > (dep.unpaid_balance or Decimal("0")):
                # 订单创建后押金状态已变（待结清缩小/标准额下调）——金额对不上，
                # 拒绝防漏收；调用方取消旧单按新状态重建即可
                raise ValidationError("押金状态已变化，补缴金额与当前差额不符，请重新创建补缴订单")
            dep.available_amount = (dep.available_amount + refill).quantize(Decimal("0.01"))
            dep.deducted_amount = (
                dep.deducted_amount - refill if dep.deducted_amount >= refill else Decimal("0")
            )
            dep.supplemented_total = (dep.supplemented_total + order.amount).quantize(
                Decimal("0.01")
            )
            dep.unpaid_balance = ((dep.unpaid_balance or Decimal("0")) - settle).quantize(
                Decimal("0.01")
            )
            if dep.unpaid_balance > 0:
                dep.status = Deposit.STATUS_FULLY_DEDUCTED
            elif dep.available_amount < standard:
                dep.status = Deposit.STATUS_PARTIALLY_DEDUCTED
            else:
                dep.status = Deposit.STATUS_PAID
            reason = "押金补缴至全额" if settle <= 0 else f"押金补缴至全额（含结清待结清 {settle}）"
            self._ledger(dep, DepositLedger.ENTRY_SUPPLEMENT, order.amount, reason, admin)

        publish_audit(
            self.db,
            admin=admin,
            action="deposit.pay",
            target_type="deposit",
            target_id=str(dep.id),
            detail={"amount": str(order.amount), "child": child.name},
            reason="押金收款确认",
        )
        from backend.common.events import DepositPaidEvent, event_bus

        event_bus.publish(
            DepositPaidEvent(
                child_id=child.id,
                deposit_id=dep.id,
                amount=order.amount,
                # 第八轮 P2-11：带上"这一笔缴款"的业务身份。补缴时 dep.id 与首缴相同，
                # 通知侧只用 deposit_id 会把第二次缴款当重复吞掉（详见事件字段注释）。
                order_id=order.id,
                is_supplement=order.order_type == Order.TYPE_DEPOSIT_SUPPLEMENT,
            ),
            db=self.db,
        )
        return dep

    #: 重新入会判定（P1-1，2026-10-18 第七十五批）：从"未入会 / 已过期 / 已退会"
    #: 进入"观察期 / 正式"——与 `Child.ALLOWED_TRANSITIONS` 的重新入会边（R-301）逐条对齐。
    REJOIN_FROM = (Child.MEMBER_NONE, Child.MEMBER_EXPIRED, Child.MEMBER_WITHDRAWN)
    REJOIN_TO = (Child.MEMBER_OBSERVATION, Child.MEMBER_FORMAL)

    def on_member_status_changed(self, child, old_status: str, new_status: str) -> None:
        """**会员态写口**的统一钩子：重新入会 → 复位已退还的押金账户（P1-1）。

        为什么放在这里：`reset_refunded_on_rejoin` 原先只有一个调用点（观察期费开通
        `_open_membership`），走**年费**或**转让**重新入会的孩子押金永远停在 `refunded`
        ——而四条缴款入口全拒 `refunded`、`deposits.child_id` 又是单列唯一 → 有效会员却
        **没有任何押金缴纳入口**（借书只能人工放行＝无担保出借）。
        现在由 `identity/member_status.write_member_status`（会员状态唯一写口）统一调用，
        任何入会路径都绕不过去。
        """
        if new_status in self.REJOIN_TO and old_status in self.REJOIN_FROM:
            self.reset_refunded_on_rejoin(child.id)

    def reset_refunded_on_rejoin(self, child_id: int) -> None:
        """重新入会时把**已退还**的押金账户归零复位为「未缴」（第四轮大审查 P1-2，2026-10-17）。

        为什么必须有：退会/转让结算会把押金置 `refunded`（钱已退给家长），而 `refunded` 此前是
        **终态**——四条缴款入口（在线缴 / 管理端造单 / 补缴 / 小程序补缴）全部以「押金状态不是 unpaid」
        为由拒绝，`deposits.child_id` 又是单列唯一（建不出第二行）→ 孩子成了"有效会员但永远缴不了押金"，
        借书被 `borrow_gate` 拒、只能馆员人工放行=无担保出借。

        `WITHDRAWN → OBSERVATION/FORMAL` 是 R-301 明文的重新入会边，缴费即入会；入会成功时把押金
        账户按「重新缴押金」的语义归零（金额/可用/已扣/待结清全 0），**历史流水（`deposit_ledgers`）
        保留**——押金台账是资金事实，只记不改；新一笔缴纳会再落一条 `pay` 行。
        """
        from backend.domain.billing.models import Deposit

        dep = (
            self.db.query(Deposit)
            .filter(Deposit.child_id == child_id, Deposit.is_deleted == 0)
            .first()
        )
        if dep is None or dep.status != Deposit.STATUS_REFUNDED:
            return
        dep.status = Deposit.STATUS_UNPAID
        dep.amount = Decimal("0")
        dep.available_amount = Decimal("0")
        dep.deducted_amount = Decimal("0")
        dep.unpaid_balance = Decimal("0")

    def restore_after_refund_rejected(self, child_id: int) -> None:
        """押金退款单被**拒绝/撤销**（钱没出去）→ 押金账户从 `refunding` 回到退款前的状态。

        第四轮大审查（2026-10-17，PRD §3.6 同日增补）：此前 `refunding` **没有回退边**——馆员拒绝或
        家长撤销押金退款单后，退会单推 rejected、孩子解锁，而押金永久停在 `refunding`：预约硬拦、
        借书只能人工放行、赔偿扣除 422、补缴/建单全拒（孩子是会员却什么都做不了，唯一出路是再提一次退会）。

        状态按余额重算，规则与 `on_deposit_order_paid` 的补缴分支**同一套**（不另立判据）：
        有未结清 → fully_deducted；可用额低于标准额 → partially_deducted；否则 → paid。
        """
        from backend.domain.billing.models import Deposit

        dep = (
            self.db.query(Deposit)
            .filter(Deposit.child_id == child_id, Deposit.is_deleted == 0)
            .with_for_update()
            .populate_existing()
            .first()
        )
        if dep is None or dep.status != Deposit.STATUS_REFUNDING:
            return
        standard = dep.amount or Decimal("0")
        if (dep.unpaid_balance or Decimal("0")) > 0:
            dep.status = Deposit.STATUS_FULLY_DEDUCTED
        elif dep.available_amount < standard:
            dep.status = Deposit.STATUS_PARTIALLY_DEDUCTED
        else:
            dep.status = Deposit.STATUS_PAID

    def create_deposit_order(self, admin, child_id: int) -> Order:
        """创建押金订单（1200，金额从配置读）。"""
        child = self.db.query(Child).filter(Child.id == child_id, Child.is_deleted == 0).first()
        if not child:
            raise NotFoundError("孩子不存在")
        self._assert_collectable(child)  # P9：冻结期禁收款
        dep = self._get_or_create(child.id)
        # 2026-10-12 审查 P2（T4）：原先只拦"已缴清"——押金处于"部分扣除/退款中"时再造一张
        # 全额押金单，结算会把可用余额**覆盖**成标准额（已扣除的记录仍在，台账自相矛盾，
        # 家庭多付一笔且退会只退标准额）。与家长端建单闸门同款：非"未缴"一律拒。
        if dep.status != Deposit.STATUS_UNPAID:
            raise ValidationError(
                f"押金当前状态为 {dep.status}，无需重复缴纳（补缴请走押金补缴入口）"
            )
        # 第八轮审查 P2-1：在途单闸（全系统唯一判据，见 order_guards）——原先只有家长端在线建单有
        from backend.domain.identity.order_guards import assert_no_inflight_deposit_order

        assert_no_inflight_deposit_order(self.db, child.id)
        amount = Decimal(ConfigService(self.db).get_value("deposit_amount"))
        import uuid
        from datetime import datetime

        order = Order(
            order_no=f"DMK{datetime.now():%Y%m%d%H%M%S}{uuid.uuid4().hex[:6].upper()}",
            order_type=Order.TYPE_DEPOSIT,
            parent_id=child.parent_id,
            child_id=child.id,
            amount=amount,
            status=Order.STATUS_PENDING_MANUAL,
        )
        self.db.add(order)
        self.db.flush()
        # 第十轮 W1-3（P3，2026-10-25）：**补审计留痕**。同域的补缴单（`create_supplement_order`）
        # 一直写 `deposit.supplement_order`，而"馆员给孩子建全额押金单"这条路
        # （`POST /api/admin/deposits/children/{id}/orders`）只 add/flush/commit：资金类动作
        # 没有 actor/时间/原因可回溯（`admin` 参数原先甚至未被使用）。与补缴单同款写法。
        from backend.domain.catalog.audit_events import publish_audit

        publish_audit(
            self.db,
            admin=admin,
            action="deposit.create_order",
            target_type="deposit",
            target_id=str(dep.id),
            detail={"amount": str(amount), "order_no": order.order_no, "child_id": child.id},
            reason="押金单创建",
        )
        self.db.commit()
        return order

    def _assert_collectable(self, child) -> None:
        """P9（2026-10-12 拍板）：冻结期禁止押金补缴（与确认收款/发起支付同一口径）。"""
        if child is not None and child.operation_locked:
            raise ValidationError(
                "该孩子正在退会/权益转让审核流程中，冻结期间不能缴押金——"
                "如确需先收欠款，请先撤销退会申请"
            )

    def create_supplement_order(self, admin, child_id: int) -> Order:
        """补缴订单：金额 = 恢复全额（标准额−可用余额）+ 待结清。

        E-20260909-03（用户裁决「扣多少补多少」）：原公式只算「标准额−可用余额」，
        超扣场景待结清永远收不回（支付侧还清零）——扣 2000 只补 1200 的真金漏洞。
        """
        child = self.db.query(Child).filter(Child.id == child_id, Child.is_deleted == 0).first()
        if not child:
            raise NotFoundError("孩子不存在")
        self._assert_collectable(child)  # P9：冻结期禁收款（补缴同样属于收款）
        dep = self._get_or_create(child.id)
        standard = Decimal(ConfigService(self.db).get_value("deposit_amount"))
        # E-20260909-03（扣多少补多少）：恢复全额 + 待结清（干净场景恰好 = 赔偿总额）
        diff = (standard - dep.available_amount + (dep.unpaid_balance or Decimal("0"))).quantize(
            Decimal("0.01")
        )
        if diff <= 0:
            raise ValidationError("押金余额充足，无需补缴")
        if dep.status not in (Deposit.STATUS_PARTIALLY_DEDUCTED, Deposit.STATUS_FULLY_DEDUCTED):
            raise ValidationError("押金当前无需补缴")
        # 第八轮审查 P2-1：补缴入口同样要有在途单闸（原先只在家长端在线建单）
        from backend.domain.identity.order_guards import assert_no_inflight_deposit_order

        assert_no_inflight_deposit_order(self.db, child.id)
        import uuid
        from datetime import datetime

        order = Order(
            order_no=f"DMK{datetime.now():%Y%m%d%H%M%S}{uuid.uuid4().hex[:6].upper()}",
            order_type=Order.TYPE_DEPOSIT_SUPPLEMENT,
            parent_id=child.parent_id,
            child_id=child.id,
            amount=diff,
            status=Order.STATUS_PENDING_MANUAL,
        )
        self.db.add(order)
        self.db.flush()
        publish_audit(
            self.db,
            admin=admin,
            action="deposit.supplement_order",
            target_type="deposit",
            target_id=str(dep.id),
            detail={"diff": str(diff)},
            reason="补缴订单创建",
        )
        self.db.commit()
        return order

    # ---------- 赔偿扣除（WM5 还书标记遗失时调用） ----------
    def deduct_for_compensation(
        self, admin, child_id: int, amount: Decimal, reason: str, copy_id: int | None = None
    ) -> Deposit:
        if amount <= 0:  # P0-F4 防御层：负数会反向增加可用余额（多退真钱）
            raise ValidationError("赔偿金额必须大于 0")
        # P1-F3：锁定读——扣款基于当前真实余额计算，双管理员并发扣款串行化
        # （无锁时 B 读旧快照覆盖写 → 账实永久漂移）
        dep = (
            self.db.query(Deposit)
            .filter(Deposit.child_id == child_id, Deposit.is_deleted == 0)
            .with_for_update()
            .populate_existing()
            .first()
        )
        if dep is None:
            dep = Deposit(child_id=child_id, amount=Decimal("0"))
            self.db.add(dep)
            self.db.flush()
        if dep.status not in (Deposit.STATUS_PAID, Deposit.STATUS_PARTIALLY_DEDUCTED):
            raise ValidationError("押金未缴纳或状态异常，无法扣除（请先线下协商处理）")
        deduct = min(amount, dep.available_amount)
        over = (amount - deduct).quantize(Decimal("0.01"))
        dep.available_amount = (dep.available_amount - deduct).quantize(Decimal("0.01"))
        dep.deducted_amount = (dep.deducted_amount + deduct).quantize(Decimal("0.01"))
        if over > 0:
            dep.unpaid_balance = (dep.unpaid_balance + over).quantize(Decimal("0.01"))
            dep.status = Deposit.STATUS_FULLY_DEDUCTED
        else:
            dep.status = (
                Deposit.STATUS_FULLY_DEDUCTED
                if dep.available_amount == 0
                else Deposit.STATUS_PARTIALLY_DEDUCTED
            )
        # P2-2（2026-10-17 第六十九批·大审查）：台账 `amount` 记**实际扣除额**（与退款行
        # "amount=实退额"同口径）——原实现记"索赔全额"，超扣场景（押金 1200、赔偿 2000）
        # 会落 amount=2000 / balance_after=0 → 用台账回推余额得 −800，与"台账即资金事实"冲突。
        # 索赔全额与转待结清部分写进 reason，人工追溯不丢信息。
        ledger_reason = reason
        if over > 0:
            ledger_reason = f"{reason}（索赔 {amount}，实扣 {deduct}，转待结清 {over}）"
        self._ledger(dep, DepositLedger.ENTRY_DEDUCT, deduct, ledger_reason, admin, copy_id)
        publish_audit(
            self.db,
            admin=admin,
            action="deposit.deduct",
            target_type="deposit",
            target_id=str(dep.id),
            detail={
                "amount": str(amount),
                "deducted": str(deduct),
                "unpaid": str(dep.unpaid_balance),
            },
            reason=reason,
        )
        self.db.flush()
        return dep

    # ---------- 查询 ----------
    def get_by_child(self, child_id: int) -> tuple[Deposit | None, list[DepositLedger]]:
        dep = (
            self.db.query(Deposit)
            .filter(Deposit.child_id == child_id, Deposit.is_deleted == 0)
            .first()
        )
        ledgers = []
        if dep:
            ledgers = (
                self.db.query(DepositLedger)
                .filter(DepositLedger.deposit_id == dep.id, DepositLedger.is_deleted == 0)
                .order_by(DepositLedger.id.desc())
                .all()
            )
        return dep, ledgers

    def my_deposit_view(self, child_id: int) -> dict:
        """家长端押金页视图（A-1/T6 下沉；R-312 差额补缴口径）。"""
        from decimal import Decimal

        from backend.common.config_service import ConfigService

        def _money(v: object) -> str:
            """金额统一两位小数字符串（P2-14，第九轮审查）。

            原先直接 `str(Decimal)`：`unpaid_balance` 为 NULL 时落成 `"0"`，而"无押金档"分支
            硬编码 `"0.00"` → 同一个"零"两种写法，小程序按 `!== '0.00'` 判零 → 已结清的孩子
            看到「未结清赔偿 ￥0」。金额输出**单一口径**：一律 quantize 到分。
            """
            d = v if isinstance(v, Decimal) else Decimal(str(v or "0"))
            return str(d.quantize(Decimal("0.01")))

        standard = Decimal(ConfigService(self.db).get_value("deposit_amount"))
        dep, ledgers = self.get_by_child(child_id)
        if not dep:
            return {
                "child_id": child_id,
                "status": "unpaid",
                "standard_amount": str(standard),
                "available_amount": "0.00",
                "deducted_amount": "0.00",
                "unpaid_balance": "0.00",
                "need_supplement": False,
                "ledger": [],
            }
        return {
            "child_id": child_id,
            "status": dep.status,
            "standard_amount": _money(standard),
            "available_amount": _money(dep.available_amount),
            "deducted_amount": _money(dep.deducted_amount),
            "unpaid_balance": _money(dep.unpaid_balance),
            # P2-14：差额也按分算（同一函数里 `available_amount < standard` 的 Decimal 比较
            # 本就正确，这里只是把输出统一到两位小数）
            "need_supplement": dep.status
            in (Deposit.STATUS_PARTIALLY_DEDUCTED, Deposit.STATUS_FULLY_DEDUCTED)
            and dep.available_amount < standard,
            "ledger": [
                {
                    "entry_type": r.entry_type,
                    "amount": _money(r.amount),
                    "balance_after": _money(r.balance_after),
                    "reason": r.reason,
                    "created_at": str(r.create_time),
                }
                for r in ledgers[:20]
            ],
        }

    def unpaid_settle_weekly(self) -> int:
        """待结清每周提醒（PRD §3.6）：押金扣到 0 仍不足的部分记为"待结清"，**系统持续提醒馆员**。

        2026-10-14（第五十九批 G1）：原实现只有"管理端恒显 + 操作时被拦"，**主动提醒通道未做**
        （PRD §3.6 该行 2026-10-13 已如实标注缺口）。现在逐人写管理端待办（`admin.deposit_settle`），
        显示态由 `todo_service` 按**未结清余额**推导（补缴结清即自动审结），`dedup_key` 取 **ISO 周**
        → "持续提醒" = 每周一次、不刷屏。返回待结清人数（任务看板 processed 口径）。
        """
        from backend.common.admin_notification_models import AdminNotification
        from backend.common.admin_notifications import AdminNotifyService

        rows = (
            self.db.query(Deposit).filter(Deposit.is_deleted == 0, Deposit.unpaid_balance > 0).all()
        )
        iso_year, iso_week, _ = datetime.now().isocalendar()
        for dep in rows:
            child = self.db.query(Child).filter(Child.id == dep.child_id).first()
            name = child.name if child else f"孩子#{dep.child_id}"
            AdminNotifyService(self.db).send(
                scene=AdminNotification.SCENE_DEPOSIT_SETTLE,
                title="待结清跟进",
                content=(
                    f"{name} 有未结清赔偿款 {dep.unpaid_balance} 元，请跟进补缴"
                    f"（补缴结清后本条自动审结）。"
                ),
                ref_type=AdminNotification.REF_CHILD,
                ref_id=dep.child_id,
                applicant_name=name,
                amount=dep.unpaid_balance,
                dedup_key=f"week:{iso_year}-{iso_week}",
            )
        if rows:
            self.db.commit()
        return len(rows)

    def list_deposits(self, page: int, page_size: int, status: str | None, keyword: str | None):

        # 第八轮 P2-12：这里原先 `outerjoin(Order, child_id + TYPE_DEPOSIT + STATUS_PAID)`——
        # 孩子**重新入会**后会有第二笔已付押金单，join 产出多行 → 管理端列表同一孩子
        # 重复出现、`total` 虚高（分页错位）。那个 Order 调用方从未使用（响应体只有押金与
        # 孩子）→ 直接去掉 join：`deposits.child_id` 唯一 + `children.id` 是主键 = 每孩子恒一行。
        q = (
            self.db.query(Deposit, Child)
            .join(Child, Deposit.child_id == Child.id)
            .filter(Deposit.is_deleted == 0)
        )
        if status:
            q = q.filter(Deposit.status == status)
        if keyword:
            like = f"%{escape_like(keyword)}%"
            q = q.filter(
                or_(Child.name.like(like, escape="\\"), Child.english_name.like(like, escape="\\"))
            )  # func.or_ 在 MySQL 生成非法 SQL（E-20260830 族）
        total = q.count()
        rows = q.order_by(Deposit.id.desc()).offset((page - 1) * page_size).limit(page_size).all()
        return rows, total

    def ensure_child(self, child_id: int) -> None:
        """孩子存在性校验（W2-D5，2026-10-25）：router 不许碰 ORM，存在性判定放服务层。

        为什么要有：`GET /deposits/children/{id}` 原先把"孩子不存在"和"存在但没押金账户"
        都回 `200 + null`，与同族端点（404）口径不一；判定逻辑放这里，router 只负责翻成 HTTP。
        """
        exists = self.db.query(Child.id).filter(Child.id == child_id, Child.is_deleted == 0).first()
        if not exists:
            raise NotFoundError("孩子不存在")

    def get_child_deposit_summary(self, child_id: int) -> tuple[Deposit | None, str]:
        dep, _ = self.get_by_child(child_id)
        child = self.db.query(Child).filter(Child.id == child_id).first()
        return dep, (child.name if child else "")

    def deduct(
        self, admin, child_id: int, amount: Decimal, reason: str, copy_id: int | None = None
    ):
        dep = self.deduct_for_compensation(admin, child_id, amount, reason, copy_id)
        child = self.db.query(Child).filter(Child.id == child_id).first()
        self.db.commit()
        return dep, (child.name if child else "")
