# backend/domain/identity/order_guards.py — 订单侧收款资格闸门（从 order_service 抽出）
"""为什么单开：`order_service.py` 触及架构关 god-file 上限（>800 行）。

两处都是"收款前必须成立"的资格判据：
① 99 元首单资格（P0-A：闸门必须在**结算链路**里，写在某个入口等于没写）；
② 冻结期（退会/转让审核中）禁止收款（2026-10-12 拍板 P9）。
以 mixin 挂回 `OrderService`，`self.db` 引用原样成立。
"""

from __future__ import annotations

from sqlalchemy import func

from backend.common.exceptions import ConflictError, ValidationError
from backend.domain.identity.models import Child, Order, Parent


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

    def _assert_child_collectable(self, order: Order) -> None:
        """2026-10-12 拍板（P9）：**冻结期（退会/转让审核中）禁止一切收款**。

        冻结前已存在的订单（待确认收款/待支付/押金补缴）此前仍能被收钱——最后钱会走退会结算
        退掉，但中间出现"会员被重新开通、押金被缴进来、退会单还挂着"的自相矛盾账。
        确有欠款要先收的，先撤销退会申请。
        """
        if not order.child_id:
            return
        child = self.db.query(Child).filter(Child.id == order.child_id).first()
        if child is not None and child.operation_locked:
            raise ValidationError(
                "该孩子正在退会/权益转让审核流程中，冻结期间不能收款——"
                "如确需先收欠款，请先撤销退会申请"
            )
