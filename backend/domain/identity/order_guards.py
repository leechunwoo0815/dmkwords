# backend/domain/identity/order_guards.py — 订单侧收款资格闸门（从 order_service 抽出）
"""为什么单开：`order_service.py` 触及架构关 god-file 上限（>800 行）。

两处都是"收款前必须成立"的资格判据：
① 99 元首单资格（P0-A：闸门必须在**结算链路**里，写在某个入口等于没写）；
② 冻结期（退会/转让审核中）禁止收款（2026-10-12 拍板 P9；2026-10-15 大审查 P1 #5 起
   同样挂进结算链路——否则冻结前拿到的支付参数能在冻结期付进来）。
以 mixin 挂回 `OrderService`，`self.db` 引用原样成立。
"""

from __future__ import annotations

from sqlalchemy import func
from sqlalchemy.orm import Session

from backend.common.exceptions import ConflictError, ValidationError
from backend.domain.identity.models import Child, Order, Parent


def assert_no_inflight_deposit_order(db: Session, child_id: int) -> None:
    """押金/补缴**在途单闸**（全系统唯一判据）：同一孩子已有待支付的押金类订单即拒。

    **第八轮审查 P2-1（2026-10-19）**：这道闸原先只写在家长端在线建单
    （`payment_service.create_online_order`），而管理端造单（`OrderService.create` 的 deposit 分支）
    与押金/补缴两个入口（`billing.create_deposit_order` / `create_supplement_order`）都没有。
    后果：双击 / 两个窗口就能留下两笔 pending 单，两笔都能付——第二笔在
    `on_deposit_order_paid` 抛"押金已缴纳" → 回调兜底 200 + 资金异常待办 =
    **钱收了、押金不动、只能人工退款**。押金是担保账户：宁可少收，不许错收。

    实现要点：**先锁 Child 行**再"查在途 + 建单"。原先在线路径的检查是纯读（TOCTOU），
    两个并发请求各自读到"无在途"照样穿透；锁序 Child → Order 与结算链路（`prepare_settle`）一致。
    锁的是裸列 `Child.id`（不加载实体），与 `assert_first_activity_eligible` 同款，无需
    `populate_existing`。
    """
    db.query(Child.id).filter(Child.id == child_id).with_for_update().first()
    inflight = (
        db.query(func.count(Order.id))
        .filter(
            Order.child_id == child_id,
            Order.order_type.in_((Order.TYPE_DEPOSIT, Order.TYPE_DEPOSIT_SUPPLEMENT)),
            Order.status.in_((Order.STATUS_PENDING_PAYMENT, Order.STATUS_PENDING_MANUAL)),
            Order.is_deleted == 0,
        )
        .scalar()
    )
    if inflight:
        raise ValidationError("该孩子已有一笔待支付的押金订单，请先完成支付或取消后再下单")


class OrderGuardMixin:
    """收款资格闸门（宿主类提供 `db`）。"""

    def assert_first_activity_eligible(self, order: Order) -> None:
        """99 元首场资格复查（R-321 每账号一次）——**全系统唯一判据**。

        三入口共用：家长 prepay 预检、线上回调结算、管理端确认/造单（都经 `_settle_paid`）。
        先锁 `parents` 行：只锁本单护不住同一账号的兄弟单（两事务各自计数都见"无已付"→都结算）。
        口径：**已退款的首场单不占名额**（与 prepay 预检同源），改口径只改这一处。
        """
        self.db.query(Parent.id).filter(Parent.id == order.parent_id).with_for_update().first()
        paid_exists = (
            self.db.query(func.count(Order.id))
            .filter(
                Order.parent_id == order.parent_id,
                Order.order_type == Order.TYPE_FIRST_ACTIVITY,
                Order.status == Order.STATUS_PAID,
                Order.refund_status != Order.REFUND_STATUS_REFUNDED,
                Order.id != order.id,
                Order.is_deleted == 0,
            )
            .scalar()
        )
        if paid_exists:
            raise ConflictError("该账号已购买过首场亲子活动（每账号仅一次）")

    def _assert_child_collectable(self, order: Order, child: Child | None = None) -> None:
        """2026-10-12 拍板（P9）：**冻结期（退会/转让审核中）禁止一切收款**。

        冻结前已存在的订单（待确认收款/待支付/押金补缴）此前仍能被收钱——最后钱会走退会结算
        退掉，但中间出现"会员被重新开通、押金被缴进来、退会单还挂着"的自相矛盾账。
        确有欠款要先收的，先撤销退会申请。

        2026-10-15（大审查 P1 #5）：本判据同时挂在**结算链路内**（`_settle_paid`），否则
        "冻结前已拿到的微信支付参数"能在冻结期付进来照常入账；那条路径传 `child=` 已锁定的行
        （结算前刚做了 `with_for_update` 锁定读），避免同一事务里再空读一次旧值。
        """
        if not order.child_id:
            return
        if child is None:
            child = self.db.query(Child).filter(Child.id == order.child_id).first()
        if child is not None and child.operation_locked:
            raise ValidationError(
                "该孩子正在退会/权益转让审核流程中，冻结期间不能收款——"
                "如确需先收欠款，请先撤销退会申请"
            )

    def assert_refund_allowed(self, order: Order) -> None:
        """退款受理的判据（P1 #4，2026-10-15；R11-b 增冻结期闸）——**与家长侧同源，超管不得绕过**：

        ① `refund_status=transferred`（已随权益转让处置）：同一笔年费既转给受让方又退款 = 重复得利
           （家长侧判据在 `wm10_service.apply_refund`）；
        ② 活动退款矩阵：已签到 / 活动已开始 / 报名已取消或已退 → 不可退
           （判据本体 `refund_rules.activity_refund_block`，三条链共用）；
        ③ **冻结期闸（R-309，R11-b）**：`operation_locked`（退会/转让审核中）不得单笔退款——
           退款执行会把孩子直接写成 withdrawn，而审核结果未定、解锁唯一出口是退会流程。
        """
        from backend.domain.identity import refund_rules

        if order.refund_status == Order.REFUND_STATUS_TRANSFERRED:
            raise ValidationError("该订单已随权益转让处置，不再受理退款申请")
        child = self.db.query(Child).filter(Child.id == order.child_id).first()
        if child is not None and child.operation_locked:
            raise ConflictError("孩子正在退会/转让审核流程中，会员费退款请走退会申请或等待审核完成")
        if order.order_type in (Order.TYPE_ACTIVITY, Order.TYPE_FIRST_ACTIVITY):
            block = refund_rules.activity_refund_block(self.db, order)
            if block:
                raise ValidationError(block)

    def prepare_settle(self, order: Order) -> Child | None:
        """**结算前置三件**（`_settle_paid` 的第一段）：99 元资格 → 冻结期闸门 → 锁定孩子行。

        为什么全部收在这里（2026-10-15 大审查 P1 #5/#6）：这三件事都必须发生在"钱动之前、
        且覆盖全部收款入口"的位置，而入口有四条（管理端确认/造单、家长 prepay、线上回调）——
        写在任一入口都等于漏三条。`_settle_paid` 是唯一共同收口点。

        ① **99 元首场资格**（P0-A 同款教训）：每账号一次，复查必须在结算链路内。
        ② **冻结期闸门（两层）**：
           - 先普通读早退——冻结标志在**申请创建时**就已提交（退会 apply / 转让 create），
             普通读足以判定；放在拿行锁之前还避开一个锁序成环：转让/退会审核是
             `Child → Order`（审核要 UPDATE 年费单为 transferred），结算若先持 Order 再等 Child
             就成环；
           - 再锁内复核——挡住"申请正在提交、结算抢在前面"的竞态（申请方在同一行锁内写标志，
             本事务的锁定读是**当前读**，等锁拿到后必然看到已提交的最新值）。
           原先只有建单/发起支付/确认收款三处查，**线上回调链不查**：冻结前拿到的微信支付参数
           （约 2 小时有效）在审核期间付进来照常入账（押金这笔钱不在已生成的结算清单里，可能永不退）。
           回调侧异常由 `handle_notify` 兜底成「回滚 + 资金异常待办 + 200」——钱已收，必须让人看见。
        ③ **锁定孩子主体行**（P1 #6）：订单行锁只护得住"这一单"，同一孩子两笔会员单并发收款
           （两个管理员同时确认 / 两笔回调同时到）会各按旧到期日算 `base+365` 覆盖写（两笔钱只加
           一次时长）。锁定读 + `populate_existing` 后整段结算复用这一份 child。
        """
        # W1-2：结算前置里加矩阵判定（原本 _settle_paid 直赋 paid，取消/支付并发即可越权结算）
        assert_settle_transition(order)
        if order.order_type == Order.TYPE_FIRST_ACTIVITY:
            self.assert_first_activity_eligible(order)
        self._assert_child_collectable(order)
        locked_child = (
            self.db.query(Child)
            .filter(Child.id == order.child_id)
            .with_for_update()
            .populate_existing()
            .first()
            if order.child_id
            else None
        )
        self._assert_child_collectable(order, child=locked_child)
        return locked_child


def assert_settle_transition(order: Order) -> None:
    """结算前的**订单转移矩阵**判定（第十轮 W1-2，2026-10-25）：不合法即抛，无返回值。

    为什么放在这里：`prepare_settle` 已经收着"钱动之前"的全部前置（99 元资格 / 冻结期 /
    锁孩子行），矩阵判定属于同一族。原先 `_settle_paid` 无条件直赋 `paid`，而
    `Order.ALLOWED_TRANSITIONS[STATUS_CANCELLED] = set()`——取消与支付并发时（家长点了取消 /
    超时单被清，同时回调或 mock 即时通道到达）`cancelled` 单会被结算成 `paid`：钱入账、
    会员/押金/报名联动全跑，而矩阵认为这条边不存在。
    """
    if order.status == Order.STATUS_PAID:
        raise ConflictError("订单已结算为已支付，不能重复结算（调用方应先判 status 幂等）")
    if not order.can_transition(Order.STATUS_PAID):
        raise ConflictError(f"订单当前状态为 {order.status}，不能结算为已支付（需人工处理）")
