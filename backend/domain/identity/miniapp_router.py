# backend/domain/identity/miniapp_router.py — 小程序退款/退会/转让/评估报告（WM10）
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, Query, Request, Response
from pydantic import Field
from sqlalchemy.orm import Session

from backend.common.base_schema import BaseSchema
from backend.common.list_meta import stamp_list_meta
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
@router.post("/logout")
def parent_logout(auth: Any = Depends(get_current_parent)):
    """退出登录 = **服务端撤销**（P1-11，2026-10-09）：token 代数 +1，旧 token 立即失效。

    为什么需要它：家长 token 原先 30 天有效且**不可撤销**（唯一出路是删家长档案=废号），
    而它还会出现在媒体 URL（`?token=`）里 → 访问日志/分享链接泄漏后 30 天内一直可用。
    现在"退出登录"是真撤销：本端与其它设备上的旧 token 全部 401（含 URL 里的）。
    """
    from backend.domain.identity.service import revoke_parent_tokens

    parent, db = auth
    revoke_parent_tokens(db, parent)
    return {"ok": True}


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
def my_orders(response: Response, child_id: int, auth: Any = Depends(get_current_parent)):
    """家长视角订单列表（A-1/T6 下沉：逻辑在 OrderService.my_orders）。

    P2-44：50 条硬上限经响应头如实告知（`X-List-Limit` / `X-List-Truncated`）。
    """
    from backend.domain.identity.order_service import OrderService

    parent, db = auth
    child_of_parent(db, parent.id, child_id)
    svc = OrderService(db)
    rows = svc.my_orders(child_id, parent.id)
    stamp_list_meta(response, loaded=len(rows), limit=svc.MY_ORDERS_LIMIT)
    return rows


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
    """转让前置条件核对（前端逐条展示差什么；仅限本家长名下的孩子）。"""
    parent, db = auth
    # P1 #9（2026-10-16，第三轮大审查）：**归属校验**此前缺失——直接 `svc._child(任意 id)`，任一登录
    # 家长即可枚举他人 child_id 并逐个读回：会员态/在会期、在借与预约、未结清赔偿、是否在退会/转让/
    # 冻结中（财务与流程状态外泄）。改用 `child_of_parent`（与其它小程序端点同一口径：
    # 越权与不存在同回应「孩子不存在」）。
    source = child_of_parent(db, parent.id, source_child_id)
    target = child_of_parent(db, parent.id, target_child_id)
    svc = TransferService(db)
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


@router.get("/login/options")
def login_options():
    """登录方式开关（公开端点，2026-10-12 拍板 P1）：小程序据此隐藏手机号入口。"""
    from backend.config import get_settings

    return {"sms_enabled": bool(get_settings().SMS_ENABLED), "wechat_enabled": True}


class MiniappConfigsResponse(BaseSchema):
    """小程序展示口径（第八轮 E 批剩余：前端口径改后端下发）。"""

    reservation_hours: int = Field(..., description="预约锁定时长（小时）")
    audio_finish_threshold_percent: int = Field(..., description="音频完播阈值（%）")
    observation_period_days: int = Field(..., description="观察期天数")
    transfer_review_timeout_hours: int = Field(..., description="转让审核时限（小时）")
    renew_limit: int = Field(..., description="每本书可续借次数")
    renew_days: int = Field(..., description="续借延长天数")
    sms_send_interval_seconds: int = Field(..., description="同号短信重发间隔（秒）")
    quiz_questions_per_book: int = Field(..., description="每本书测验题量")
    quiz_max_attempts: int = Field(..., description="每本书测验机会（次）")
    activity_refund_cutoff_hours: int = Field(..., description="活动退款时限（距开始小时）")


#: 下发的键 → 字段名（**唯一清单**：加一处口径只改这里 + 上面 schema）
MINIAPP_CONFIG_KEYS = {
    "reservation_hours": "reservation_hours",
    "audio_finish_threshold_percent": "audio_finish_threshold_percent",
    "observation_period_days": "observation_period_days",
    "transfer_review_timeout_hours": "transfer_review_timeout_hours",
    "renew_limit": "renew_limit",
    "renew_days": "renew_days",
    "sms_send_interval_seconds": "sms_send_interval_seconds",
    "quiz_questions_per_book": "quiz_questions_per_book",
    "quiz_max_attempts": "quiz_max_attempts",
    "activity_refund_cutoff_hours": "activity_refund_cutoff_hours",
}


@router.get("/configs", response_model=MiniappConfigsResponse)
def miniapp_configs(db: Session = Depends(get_db)) -> MiniappConfigsResponse:
    """业务口径下发（**公开端点**：登录页倒计时要用，不能要求先登录）。

    为什么要有（第八轮审查 E 批剩余）：小程序里 13 处硬编码业务数字（预约保留 72 小时、
    完播阈值 95%、观察期 30 天、续借 1 次…）与配置中心各写一份——运营改配置后文案不跟随；
    P2-73 更是三方不一致（种子/服务端 **90 秒**、小程序倒计时写 **60 秒**）：家长 60 秒后
    点发送必被服务端拒。本端点把**展示口径**集中下发（读的是配置中心同一份值，60 秒 TTL 缓存），
    前端不再自持副本；改配置 → 文案跟随。

    只下发**展示用**口径：不放服务端校验规则（那些仍由服务端在动作发生时判定，
    前端拿到的数字只是文案依据，不能当判据）。
    """
    from backend.common.config_service import ConfigService

    svc = ConfigService(db)
    return MiniappConfigsResponse(
        **{field: svc.get_int(key) for key, field in MINIAPP_CONFIG_KEYS.items()}
    )


@router.post("/sms/send", dependencies=[Depends(rate_limit(5, 60))])
def sms_send(body: SmsSendRequest, request: Request, db: Session = Depends(get_db)):
    """发短信验证码（登录兜底 / 微信绑定共用）。

    三道闸门在服务层：同号间隔、每日上限、校验失败次数；开发态 `SMS_PROVIDER=mock`
    验证码打在服务端日志（`[MockSms]`），不真发短信。
    """
    from backend.domain.identity.sms_service import SmsCodeService
    from backend.middleware.rate_limit import _client_ip

    # 2026-10-12 拍板（P2）：把来源 IP 交给服务层做"单 IP 日上限"（与限流同一 IP 口径）
    return SmsCodeService(db).send(body.phone, body.purpose, client_ip=_client_ip(request))
