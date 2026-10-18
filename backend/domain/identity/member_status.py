# backend/domain/identity/member_status.py — 会员状态唯一写口（P1-1，2026-10-18 第七十五批）
"""为什么单开：会员状态有**多个入口**（订单结算 `OrderService._transition_member`、
身份域 `IdentityService._transition`、权益转让受让方），而"写状态"这件事必须**只定义一次**
——否则每加一个入口就会漏掉一类连带动作（本项目已有先例：押金复位只挂在观察期费路径，
年费/转让重新入会的孩子永久缴不了押金）。

本模块 = 状态写入 + 连带动作（押金账户复位）的唯一实现；各入口只调用 `write_member_status`。
状态合法性仍由调用方用 `Child.can_transition` 把关（部分入口要先写违规留痕再抛错）。
"""

from __future__ import annotations

from sqlalchemy.orm import Session

from backend.domain.identity.models import Child


def write_member_status(db: Session, child: Child, new_status: str) -> None:
    """写会员状态（含待评估时钟）+ 触发连带动作（重新入会 → 复位已退还押金）。

    `old_status` 必须在写之前取——写后就读不到了。
    """
    from backend.domain.billing.service import DepositService

    old_status = child.member_status
    child.set_member_status(new_status)
    DepositService(db).on_member_status_changed(child, old_status, new_status)
