# backend/domain/identity/miniapp_router.py — 小程序退款/退会/转让/评估报告（WM10）
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from backend.common.base_schema import BaseSchema
from backend.database import get_db
from backend.domain.identity import refund_queries
from backend.domain.identity.auth import child_of_parent, get_current_parent
from backend.domain.identity.observation_service import ObservationReportService
from backend.domain.identity.transfer_service import TransferService
from backend.domain.identity.wm10_service import RefundService, WithdrawalService
from backend.middleware.rate_limit import rate_limit

router = APIRouter(tags=["identity-miniapp"])


class RefundApplyRequest(BaseSchema):
    child_id: int
    order_id: int
    reason: str


class WithdrawalApplyRequest(BaseSchema):
    child_id: int
    reason: str


class TransferApplyRequest(BaseSchema):
    source_child_id: int
    target_child_id: int


class ParentProfileRequest(BaseSchema):
    display_name: str = ""


class ChildAvatarRequest(BaseSchema):
    avatar: str = ""


# ---------- 孩子列表刷新（2026-09-16：修「点赞头像 vs 我的页头像不一致」） ----------
@router.get("/children")
def my_children(auth: Any = Depends(get_current_parent)):
    """家长名下孩子列表（**与登录载荷同源**：`identity.auth.children_payload`）。

    为什么需要它：小程序把登录返回的孩子列表缓存在本地，而阅读圈点赞墙/排行榜走服务端
    实时数据。**后台改了孩子头像（或会员到期）后本地快照永远不同步** → 用户看到
    「点赞的头像跟我的页面的头像不匹配」。本端点供小程序刷新本地缓存（app 前台时拉一次）。
    """
    from backend.domain.identity.auth import children_payload

    parent, db = auth
    return {"children": children_payload(db, parent.id)}


# ---------- 孩子头像（WM15-R3：系统内置头像库，家长自助选择） ----------
@router.put("/children/{child_id}/avatar")
def update_child_avatar(
    child_id: int,
    body: ChildAvatarRequest,
    auth: Any = Depends(get_current_parent),
):
    """家长给孩子选内置头像（只允许该字段；空串=清空回默认）。白名单校验在 service。"""
    from backend.domain.identity.service import ChildService

    parent, db = auth
    child_of_parent(db, parent.id, child_id)  # 归属红线：只能改自己孩子的
    ChildService(db).update_avatar(child_id, body.avatar)
    return {"child_id": child_id, "avatar": body.avatar or ""}


# ---------- 家长资料（WM14-B：展示称呼，双署名/被赞通知取它） ----------
@router.put("/parent/profile")
def update_parent_profile(
    body: ParentProfileRequest,
    auth: Any = Depends(get_current_parent),
):
    """只允许改展示称呼（display_name）；空串=回退真实姓名。"""
    from backend.domain.identity.service import ParentService

    parent, db = auth
    return ParentService(db).update_display_name(parent, body.display_name)


# ---------- 订单（家长视角，退款申请用） ----------
@router.get("/orders")
def my_orders(child_id: int, auth: Any = Depends(get_current_parent)):
    """家长视角订单列表（A-1/T6 下沉：逻辑在 OrderService.my_orders）。"""
    from backend.domain.identity.order_service import OrderService

    parent, db = auth
    child_of_parent(db, parent.id, child_id)
    return OrderService(db).my_orders(child_id, parent.id)


# ---------- 线上支付（WM12-A：下单 / 价格 / 发起支付） ----------
class OnlineOrderRequest(BaseSchema):
    child_id: int
    order_type: str


@router.get("/payment/plans")
def payment_plans(child_id: int, auth: Any = Depends(get_current_parent)):
    """购买页价格（配置下发，前端零硬编码金额）；`payment_enabled=false` 时前端不渲染支付入口。"""
    from backend.domain.identity.payment_service import PaymentService

    parent, db = auth
    child = child_of_parent(db, parent.id, child_id)
    return PaymentService(db).plans(parent, child)


@router.post("/orders")
def create_online_order(body: OnlineOrderRequest, auth: Any = Depends(get_current_parent)):
    """家长端在线下单（观察期费/年费/99 元首场/押金）→ 订单进待支付，随后调 `pay` 拉起微信支付。

    只建单不收钱：金额一律服务端按配置重算（`OrderService.price_member_order` 单一来源）。
    """
    from backend.domain.identity.payment_service import PaymentService

    parent, db = auth
    child = child_of_parent(db, parent.id, body.child_id)
    order = PaymentService(db).create_online_order(parent, child, body.order_type)
    return {
        "id": order.id,
        "order_no": order.order_no,
        "order_type": order.order_type,
        "amount": str(order.amount),
        "status": order.status,
    }


@router.post("/orders/{order_id}/pay", dependencies=[Depends(rate_limit(10, 60))])
def pay_order(order_id: int, auth: Any = Depends(get_current_parent)):
    """发起线上支付（三段式：先落状态→调网关→锁内复核）。

    返回 `pay_params` 供小程序 `wx.requestPayment` 拉起；mock 通道 `instant_paid=true`
    （已即时到账，端上不用再拉起）。重复点按安全：幂等键=订单号（`already_paid=true`）。
    """
    from backend.domain.identity.payment_service import PaymentService

    parent, db = auth
    return PaymentService(db).prepay(parent, order_id)


# ---------- WM11 消息中心（家长端站内消息） ----------


class ReadNotificationsRequest(BaseSchema):
    ids: list[int] = []
    all: bool = False


@router.get("/notifications")
def my_notifications(
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    category: str | None = Query(None),
    scene: str | None = Query(None, description="按场景过滤（fix34 R0：如 circle.liked）"),
    auth: Any = Depends(get_current_parent),
):
    """家长端消息中心（A-1/T6 下沉：逻辑在 NotificationService.list_mine）。"""
    from backend.common.notifications import NotificationService

    parent, db = auth
    from backend.domain.identity.service import attach_actor_profiles

    data = NotificationService(db).list_mine(parent.id, page, page_size, category, scene)
    return attach_actor_profiles(db, data)  # fix34 R0：补点赞者头像/等级（业务域装饰）


@router.post("/notifications/read")
def mark_notifications_read(
    body: ReadNotificationsRequest, auth: Any = Depends(get_current_parent)
):
    """标记已读（A-1/T6 下沉：逻辑在 NotificationService.mark_read）。"""
    from backend.common.notifications import NotificationService

    parent, db = auth
    marked = NotificationService(db).mark_read(parent.id, body.ids, body.all)
    return {"ok": True, "marked": marked}


# ---------- 退款 ----------
@router.get("/refund-preview")
def refund_preview(child_id: int, order_id: int, auth: Any = Depends(get_current_parent)):
    parent, db = auth
    child = child_of_parent(db, parent.id, child_id)
    return RefundService(db).preview(child, order_id)


@router.post("/refund-requests")
def refund_apply(body: RefundApplyRequest, auth: Any = Depends(get_current_parent)):
    parent, db = auth
    child = child_of_parent(db, parent.id, body.child_id)
    req = RefundService(db).apply(child, body.order_id, body.reason)
    return {"id": req.id, "status": req.status, "amount": str(req.amount)}


@router.get("/refund-requests")
def refund_list(child_id: int, auth: Any = Depends(get_current_parent)):
    parent, db = auth
    child = child_of_parent(db, parent.id, child_id)
    return refund_queries.my_list(db, child)


class RefundCancelRequest(BaseSchema):
    child_id: int


@router.post("/refund-requests/{request_id}/cancel")
def refund_cancel(
    request_id: int, body: RefundCancelRequest, auth: Any = Depends(get_current_parent)
):
    """家长撤销待审核退款申请（BDD：cancelled、订单恢复；联动撤销退会申请）。"""
    parent, db = auth
    child = child_of_parent(db, parent.id, body.child_id)
    req = RefundService(db).cancel(child, request_id)
    return {"id": req.id, "status": req.status}


# ---------- 退会 ----------
@router.post("/withdrawals")
def withdrawal_apply(body: WithdrawalApplyRequest, auth: Any = Depends(get_current_parent)):
    parent, db = auth
    child = child_of_parent(db, parent.id, body.child_id)
    return WithdrawalService(db).apply(child, body.reason)


@router.get("/withdrawals")
def withdrawal_list(child_id: int, auth: Any = Depends(get_current_parent)):
    parent, db = auth
    child = child_of_parent(db, parent.id, child_id)
    return WithdrawalService(db).my_list(child)


@router.get("/withdrawals/{request_id}/settlement")
def withdrawal_settlement(request_id: int, child_id: int, auth: Any = Depends(get_current_parent)):
    """本次退会「能退哪些费用」（2026-09-15 用户需求）。

    家长端退会页只提退会申请；**审核通过后**由后端把自动排查出的可退费用明细
    展示给家长（数据来自审核时真实生成的退款单，不是估算）。
    """
    parent, db = auth
    child = child_of_parent(db, parent.id, child_id)
    return WithdrawalService(db).my_settlement(child, request_id)


class WithdrawalCancelRequest(BaseSchema):
    child_id: int


@router.post("/withdrawals/{request_id}/cancel")
def withdrawal_cancel(
    request_id: int, body: WithdrawalCancelRequest, auth: Any = Depends(get_current_parent)
):
    """家长撤销进行中的退会申请（applying → cancelled + 解锁）。"""
    parent, db = auth
    child = child_of_parent(db, parent.id, body.child_id)
    req = WithdrawalService(db).cancel(child, request_id)
    return {"id": req.id, "status": req.status}


# ---------- 权益转让 ----------
@router.post("/transfers")
def transfer_apply(body: TransferApplyRequest, auth: Any = Depends(get_current_parent)):
    parent, db = auth
    return TransferService(db).apply(parent, body.source_child_id, body.target_child_id)


@router.get("/transfers")
def transfer_list(auth: Any = Depends(get_current_parent)):
    parent, db = auth
    return TransferService(db).my_list(parent)


@router.get("/transfers/conditions")
def transfer_conditions(
    source_child_id: int, target_child_id: int, auth: Any = Depends(get_current_parent)
):
    """转让前置条件核对（前端逐条展示差什么）。"""
    parent, db = auth
    svc = TransferService(db)
    source = svc._child(source_child_id)
    target = svc._child(target_child_id)
    return {"conditions": svc.check_conditions(parent, source, target)}


@router.post("/transfers/{transfer_id}/cancel")
def transfer_cancel(transfer_id: int, auth: Any = Depends(get_current_parent)):
    parent, db = auth
    return TransferService(db).cancel(parent, transfer_id)


# ---------- 评估报告 ----------
@router.get("/observation-reports")
def observation_reports(child_id: int, auth: Any = Depends(get_current_parent)):
    parent, db = auth
    child_of_parent(db, parent.id, child_id)
    return ObservationReportService(db).list_for_child(child_id)


# ---------- 登录（2026-10-08 接线，审查 P0-1）：微信主通道 + 短信兜底 ----------


class WeChatLoginRequest(BaseSchema):
    code: str = ""  # wx.login 拿到的临时 code


class WeChatBindRequest(BaseSchema):
    bind_ticket: str = ""
    phone: str = ""
    code: str = ""


class SmsSendRequest(BaseSchema):
    phone: str = ""
    purpose: str = "login"  # login=登录兜底 / bind=微信首次绑手机号


@router.post("/login/wechat", dependencies=[Depends(rate_limit(10, 60))])
def login_wechat(body: WeChatLoginRequest, db: Session = Depends(get_db)):
    """微信一键登录（**主通道**）。

    openid 已绑家长 → 直接发登录态；未绑 → `{need_bind: true, bind_ticket}`，
    小程序接着走「手机号 + 短信码」的绑定（`/login/bind`）。家长档案仍由馆员到店建档创建。
    """
    from backend.domain.identity.auth import login_by_wechat

    return login_by_wechat(db, body.code)


@router.post("/login/bind", dependencies=[Depends(rate_limit(5, 60))])
def login_bind(body: WeChatBindRequest, db: Session = Depends(get_db)):
    """微信首次绑定手机号：绑定凭证 + 短信验证码 → 落 openid 并发登录态。"""
    from backend.domain.identity.auth import bind_wechat

    return bind_wechat(db, body.bind_ticket, body.phone, body.code)


@router.post("/sms/send", dependencies=[Depends(rate_limit(5, 60))])
def sms_send(body: SmsSendRequest, db: Session = Depends(get_db)):
    """发短信验证码（登录兜底 / 微信绑定共用）。

    三道闸门在服务层：同号间隔、每日上限、校验失败次数；开发态 `SMS_PROVIDER=mock`
    验证码打在服务端日志（`[MockSms]`），不真发短信。
    """
    from backend.domain.identity.sms_service import SmsCodeService

    return SmsCodeService(db).send(body.phone, body.purpose)
