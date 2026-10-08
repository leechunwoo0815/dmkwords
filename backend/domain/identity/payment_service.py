# backend/domain/identity/payment_service.py — 微信线上支付（WM12-A）
"""收款侧闭环：下单 → 预支付（三段式）→ 回调三件套（验签/金额/幂等）→ 自动结算。

三个**单一来源**（都是踩过坑才立的）：
- 定价：`OrderService.price_member_order`（管理端造单与家长端下单同一函数，二孩折扣/99 元资格不漂移）
- 结算：`OrderService._settle_paid`（人工确认收款与线上回调同一函数：会员开通/押金记账/报名转正）
- 金额单位：`yuan_to_cents()`（网关契约是【分】，库内是 Decimal 元）

回调三层守卫照 `docs/billing-circulation模式手册.md` P3：
① 入口：trade_state 消费（非 SUCCESS 不入账）+ 金额比对 + 流水号查重
② 执行：锁内状态守卫（退款中/已退款忽略，防资金状态反转）
③ 幂等：已 paid 的重复回调直接 200（**不抛错**——抛错会触发微信更猛烈的重试风暴，旧项目 L2-008）

三段式照 P2：先落库提交 → 事务外调网关 → 锁内复核，跨网络调用绝不抱在 DB 事务里。
"""

from __future__ import annotations

import json
import logging
import threading
import time
import types
import uuid
from decimal import Decimal, InvalidOperation
from typing import Any

from sqlalchemy import func
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from backend.common.async_utils import run_coro
from backend.common.exceptions import (
    ConflictError,
    ForbiddenError,
    NotFoundError,
    PaymentError,
    ValidationError,
)
from backend.common.gateways.payment.types import PaymentOrderRequest, yuan_to_cents
from backend.config import get_settings
from backend.domain.catalog.audit_events import publish_audit
from backend.domain.identity.models import Child, Order, Parent

logger = logging.getLogger(__name__)

#: 同一订单并发 `prepay` 的进程内串行化（WM12-C 审查 P2-10）：小程序双击/重试会让同一订单
#: 刷出多个预支付单。锁 + 5 秒内复用同一份 `pay_params`（同一 prepay_id 重复拉起支付没有副作用）。
#: 多 worker 部署下极端并发仍可能重复下单，但商户订单号是微信侧的**交易**幂等键（同一单号只会
#: 成交一笔），资金安全由回调幂等 + 对账兜底（docs/09 WM12-C §四）。
_PREPAY_LOCKS: dict[int, threading.Lock] = {}
_PREPAY_LOCKS_GUARD = threading.Lock()
_PREPAY_CACHE: dict[int, tuple[float, dict]] = {}
_PREPAY_CACHE_TTL = 5.0
_PREPAY_CACHE_MAX_AGE = 60.0


def _order_prepay_lock(order_id: int) -> threading.Lock:
    with _PREPAY_LOCKS_GUARD:
        lock = _PREPAY_LOCKS.get(order_id)
        if lock is None:
            lock = _PREPAY_LOCKS[order_id] = threading.Lock()
        return lock


def _cached_prepay(order_id: int) -> dict | None:
    with _PREPAY_LOCKS_GUARD:
        hit = _PREPAY_CACHE.get(order_id)
    if hit and (time.monotonic() - hit[0]) < _PREPAY_CACHE_TTL:
        return dict(hit[1])
    return None


def _remember_prepay(order_id: int, result: dict) -> None:
    """只缓存"等用户付款"的结果（有 `pay_params`）：即时到账的结果第二次点应按"已支付"答复。"""
    if not result.get("pay_params"):
        return
    now = time.monotonic()
    with _PREPAY_LOCKS_GUARD:
        for key in [k for k, (ts, _) in _PREPAY_CACHE.items() if now - ts > _PREPAY_CACHE_MAX_AGE]:
            _PREPAY_CACHE.pop(key, None)
        _PREPAY_CACHE[order_id] = (now, dict(result))


#: 支持"家长端在线下单"的订单类型（活动费/押金补缴由既有链路建单后走 pay，不在此列）
ONLINE_ORDER_TYPES = (
    Order.TYPE_FIRST_ACTIVITY,
    Order.TYPE_OBSERVATION,
    Order.TYPE_FORMAL,
    Order.TYPE_DEPOSIT,
)

#: 微信支付商品描述（≤127 字）
TYPE_LABELS = {
    Order.TYPE_FIRST_ACTIVITY: "首场亲子活动费",
    Order.TYPE_OBSERVATION: "观察期会员费",
    Order.TYPE_FORMAL: "正式会员年费",
    Order.TYPE_DEPOSIT: "借阅押金",
    Order.TYPE_DEPOSIT_SUPPLEMENT: "押金补缴",
    Order.TYPE_ACTIVITY: "活动报名费",
}

SYSTEM_ACTOR_NAME = "微信支付回调"


class PaymentService:
    """家长端线上收款的唯一入口（Service 统一 commit）。"""

    def __init__(self, db: Session):
        self.db = db

    # ---------- 开关与网关 ----------

    @staticmethod
    def enabled() -> bool:
        """线上支付开关（`PAYMENT_ENABLED`）：关掉=上线版本"纯人工收款"，小程序不出现支付入口。"""
        return bool(get_settings().PAYMENT_ENABLED)

    @staticmethod
    def _gateway():
        from backend.integrations.payment import get_payment_gateway

        return get_payment_gateway()

    def _ensure_enabled(self) -> None:
        if not self.enabled():
            raise ValidationError("当前未开放在线支付，请到店办理或联系馆员")

    # ---------- 展示价（配置下发，前端零硬编码） ----------

    def plans(self, parent: Parent, child: Child) -> dict:
        """购买页价格：与下单价同源（同一定价函数），前端只负责展示。"""
        from backend.domain.identity.order_service import OrderService

        svc = OrderService(self.db)
        items: list[dict] = []
        for order_type in ONLINE_ORDER_TYPES:
            item = {
                "order_type": order_type,
                "label": TYPE_LABELS.get(order_type, order_type),
                "amount": None,
                "available": True,
                "reason": "",
            }
            try:
                item["amount"] = str(svc.price_member_order(parent, child, order_type))
            except ConflictError as exc:
                # 99 元首场每账号一次：买过就置灰（不隐藏，家长要看得到"为什么不能买"）
                item["available"] = False
                item["reason"] = str(exc)
            items.append(item)
        return {
            "payment_enabled": self.enabled(),
            "provider": get_settings().PAYMENT_PROVIDER.strip().lower(),
            "child_id": child.id,
            # WM12-C（审查 P0-3，用户裁定）：线上支付**只允许微信一键登录**的家长——
            # 短信兜底/固定码登录拿不到 openid，前端据此隐藏支付入口（点了必 422）。
            "can_pay_online": bool((parent.wechat_openid or "").strip()),
            "items": items,
        }

    # ---------- 下单（在线单） ----------

    def create_online_order(self, parent: Parent, child: Child, order_type: str) -> Order:
        """家长端在线下单：状态直接进 `pending_payment`（管理端造单仍是待人工确认）。"""
        self._ensure_enabled()
        if order_type not in ONLINE_ORDER_TYPES:
            raise ValidationError("该订单类型不支持在线支付，请到店办理")
        if child.operation_locked:
            raise ValidationError("孩子正在转让/退会审核流程中，不能创建新订单")
        from backend.domain.identity.order_service import OrderService

        amount = OrderService(self.db).price_member_order(parent, child, order_type)
        order = Order(
            order_no=OrderService.new_order_no(),
            order_type=order_type,
            parent_id=parent.id,
            child_id=child.id,
            amount=amount,
            status=Order.STATUS_PENDING_PAYMENT,
            remark=f"小程序在线下单（{TYPE_LABELS.get(order_type, order_type)}）",
        )
        self.db.add(order)
        self.db.flush()
        publish_audit(
            self.db,
            admin=self._parent_actor(parent),
            action="order.create",
            target_type="order",
            target_id=order.order_no,
            detail={
                "type": order_type,
                "amount": str(amount),
                "child": child.name,
                "channel": "miniapp_online",
            },
        )
        self.db.commit()
        return order

    # ---------- 预支付（三段式） ----------

    def prepay(self, parent: Parent, order_id: int) -> dict:
        """发起线上支付：返回 `wx.requestPayment` 参数（mock 通道即时到账）。

        同单并发串行化（WM12-C 审查 P2-10）：小程序双击在前端已被忙标志挡住，这里是服务端兜底。
        """
        self._ensure_enabled()
        order = self._owned_order(parent, order_id)
        lock = _order_prepay_lock(order.id)
        if not lock.acquire(blocking=False):
            raise ConflictError("该订单正在发起支付，请稍候（请勿重复点击）")
        try:
            cached = _cached_prepay(order.id)
            if cached is not None:
                return cached
            result = self._prepay_once(parent, order)
            _remember_prepay(order.id, result)
            return result
        finally:
            lock.release()

    def _prepay_once(self, parent: Parent, order: Order) -> dict:
        """单次预支付（三段式）——调用方已持有该订单的进程内锁。"""
        if order.status == Order.STATUS_PAID:
            # 幂等：已支付单再点「去支付」→ 如实告知，不报错也不重复下单
            return self._pay_result(order, already_paid=True)
        if order.status not in (Order.STATUS_PENDING_PAYMENT, Order.STATUS_PENDING_MANUAL):
            raise ValidationError(f"订单状态 {order.status} 不可发起支付")
        openid = (parent.wechat_openid or "").strip()
        if not openid:
            # 口径 docs/09 WM12-C §二.4（用户裁定）：线上支付只允许微信一键登录的家长。
            # 小程序已按 `can_pay_online` 隐藏入口，这里是服务端兜底（老版本前端/直连 API）。
            raise ValidationError(
                "线上支付需微信一键登录：请用微信一键登录后再试（短信登录仅供查询订单）"
            )
        if order.order_type == Order.TYPE_FIRST_ACTIVITY:
            self._assert_first_activity_payable(order)

        # Phase 1：先落库（状态置线上待支付 + 提交），把锁放掉再谈网络
        if order.status != Order.STATUS_PENDING_PAYMENT:
            order.status = Order.STATUS_PENDING_PAYMENT
            self.db.commit()

        # Phase 2：事务外调网关（无任何 DB 锁；失败不回滚 Phase 1——模式手册 P2/F-030）
        gateway = self._gateway()
        try:
            resp = run_coro(
                gateway.create_order(
                    PaymentOrderRequest(
                        out_trade_no=order.order_no,
                        amount=Decimal(yuan_to_cents(order.amount)),
                        description=TYPE_LABELS.get(order.order_type, "会员费用"),
                        openid=openid,
                    )
                )
            )
        except Exception as exc:  # 网关异常：保持待支付，善后交给僵尸单清理（P4）
            logger.error("微信下单异常 order=%s: %s", order.order_no, exc, exc_info=True)
            raise PaymentError(f"发起支付失败：{exc}") from exc
        if not resp.success:
            raise PaymentError(resp.error_message or "微信下单失败，请稍后重试")

        # Phase 3：锁内复核（回调可能已经把它结掉了——回调赢了就直接认，避免状态反转）
        fresh = self._locked_by_order_no(order.order_no)
        if fresh.status == Order.STATUS_PAID:
            return self._pay_result(fresh, already_paid=True)

        if getattr(gateway, "supports_instant_payment", False):
            # mock/沙箱通道即时到账：走**与真实回调同一条结算链**
            self._settle_online(
                fresh,
                transaction_id=f"mock_txn_{uuid.uuid4().hex[:16]}",
                remark="mock 支付通道即时到账",
            )
            return self._pay_result(fresh, already_paid=False, instant_paid=True)

        return {
            **self._pay_result(fresh, already_paid=False),
            "pay_params": resp.pay_params,
        }

    # ---------- 支付回调（三件套） ----------

    def handle_notify(
        self, *, body: str, signature: str, timestamp: str, nonce: str
    ) -> tuple[int, dict]:
        """处理微信支付回调：返回 `(HTTP 状态码, 响应体)`。

        幂等/守卫顺序见模块 docstring；任何异常路径都不抛给 Router——
        回调必须**有明确应答**（200 停止重试 / 4xx-5xx 让微信重试），
        否则微信会一直重发到 24 小时结束。
        """
        if not self.enabled():
            return 503, {"code": "FAIL", "message": "线上支付未开启"}
        gateway = self._gateway()

        # ① 入口层：验签（生产走平台证书；未配证书 fail-closed 拒绝，绝不静默放行）
        try:
            verified = run_coro(
                gateway.verify_callback_signature(body, signature, timestamp, nonce)
            )
        except Exception as exc:
            logger.error("支付回调验签异常: %s", exc, exc_info=True)
            self._audit_anomaly("支付回调验签异常", reason=str(exc)[:200])
            return 401, {"code": "FAIL", "message": "验签不可用"}
        if not verified:
            self._audit_anomaly("支付回调验签不通过", reason="signature_invalid")
            return 401, {"code": "FAIL", "message": "签名验证失败"}

        envelope = self._parse_envelope(body)
        if envelope is None:
            self._audit_anomaly("支付回调报文无法解析", reason=body[:200])
            return 400, {"code": "FAIL", "message": "报文格式错误"}

        event_type = str(envelope.get("event_type") or "").upper()
        if event_type.startswith("REFUND"):
            return self._handle_refund_notify(gateway, envelope, event_type)
        if event_type and event_type != "TRANSACTION.SUCCESS":
            return 200, {"code": "SUCCESS", "message": "已忽略非支付成功事件"}

        data = self._decrypt(gateway, envelope)
        if data is None:
            self._audit_anomaly("支付回调解密失败", reason=event_type)
            return 400, {"code": "FAIL", "message": "回调数据无法解密"}

        if data.trade_state != "SUCCESS":
            # trade_state 消费：非 SUCCESS 不标记已支付
            self._audit_anomaly(
                f"支付回调状态非成功，忽略（{data.trade_state or '空'}）",
                reason=f"out_trade_no={data.out_trade_no}",
            )
            return 200, {"code": "SUCCESS", "message": "已忽略非成功状态"}

        order = (
            self.db.query(Order)
            .filter(Order.order_no == data.out_trade_no, Order.is_deleted == 0)
            .first()
        )
        if not order:
            self._audit_anomaly("支付回调找不到订单", reason=f"out_trade_no={data.out_trade_no}")
            return 404, {"code": "FAIL", "message": "订单不存在"}

        # ① 金额比对：回调金额 ≠ 订单金额 → 拒绝入账（落审计让差异可查）
        if data.amount is None or Decimal(data.amount).quantize(Decimal("0.01")) != Decimal(
            order.amount
        ).quantize(Decimal("0.01")):
            self._audit_anomaly(
                "支付回调金额与订单不符，拒绝入账",
                target_id=order.order_no,
                reason=f"回调={data.amount} 订单={order.amount}",
            )
            return 400, {"code": "FAIL", "message": "金额不符"}

        # ② 执行层：锁内重取 + 状态守卫
        locked = self._locked_by_order_no(data.out_trade_no)
        if locked.status == Order.STATUS_PAID:
            # ③ 幂等层：已入账的重复回调直接成功（不重复入账、不触发重试风暴）
            if locked.transaction_id and locked.transaction_id != data.transaction_id:
                self._audit_anomaly(
                    "订单已支付但回调流水号不同（需人工核对）",
                    target_id=locked.order_no,
                    reason=f"库内={locked.transaction_id} 回调={data.transaction_id}",
                )
            return 200, {"code": "SUCCESS", "message": "成功"}
        if locked.status == Order.STATUS_REFUNDED or locked.refund_status in (
            Order.REFUND_STATUS_PROCESSING,
            Order.REFUND_STATUS_REFUNDED,
        ):
            # ② 乱序守卫（P3 ②）：退款中/已退款的订单忽略支付回调，防资金状态反转
            self._audit_anomaly(
                "退款中/已退款订单收到支付回调，忽略（乱序守卫）",
                target_id=locked.order_no,
                reason=f"status={locked.status} refund_status={locked.refund_status}",
            )
            return 200, {"code": "SUCCESS", "message": "已忽略"}
        if locked.status not in (Order.STATUS_PENDING_PAYMENT, Order.STATUS_PENDING_MANUAL):
            # 已取消（含超时释放）却收到成功回调 = 迟到支付：钱已收，必须让人看见
            self._audit_anomaly(
                "已取消订单收到成功支付回调（迟到支付，需人工处理）",
                target_id=locked.order_no,
                reason=f"status={locked.status} transaction_id={data.transaction_id}",
            )
            return 200, {"code": "SUCCESS", "message": "已记录待人工处理"}

        # ① 流水号查重（WM12-C 审查 P2-15：从锁外移到锁内）——同一微信支付单号不得关联两笔订单。
        # 锁只能护住"这一单"，两个不同订单带同一流水号并发时仍可能同时过关，
        # 所以真正的兜底是 `orders.transaction_id` 唯一索引（uq_order_transaction）+ 下面的 IntegrityError。
        if data.transaction_id:
            dup = (
                self.db.query(func.count(Order.id))
                .filter(
                    Order.transaction_id == data.transaction_id,
                    Order.id != locked.id,
                    Order.is_deleted == 0,
                )
                .scalar()
            )
            if dup:
                self._audit_anomaly(
                    "支付流水号已被其他订单占用，拒绝入账",
                    target_id=locked.order_no,
                    reason=data.transaction_id,
                )
                return 409, {"code": "FAIL", "message": "支付单号重复"}

        try:
            self._settle_online(
                locked,
                transaction_id=data.transaction_id or "",
                remark=f"微信支付（{data.transaction_id or '无流水号'}）",
            )
        except ConflictError as exc:
            # 99 元首场资格：两笔待支付单被先后支付（钱都收了）→ 不入账也不能装作无事
            self._audit_anomaly(
                "首场活动费重复支付，需人工退款处理",
                target_id=locked.order_no,
                reason=str(exc)[:200],
            )
            return 200, {"code": "SUCCESS", "message": "已记录待人工处理"}
        except IntegrityError as exc:
            # WM12-C（审查 P2-15）：并发回调同时入账被唯一索引拦下（uq_order_transaction）——
            # 流水号已被别的订单用了 = 钱收了两笔只认一笔，必须留痕让人处理（不回 5xx 触发重试风暴）
            self.db.rollback()
            self._audit_anomaly(
                "支付回调并发入账被唯一索引拦截（流水号冲突，需人工核对）",
                target_id=locked.order_no,
                reason=f"transaction_id={data.transaction_id} err={str(exc.orig)[:120]}",
            )
            return 200, {"code": "SUCCESS", "message": "已记录待人工处理"}
        return 200, {"code": "SUCCESS", "message": "成功"}

    def _handle_refund_notify(self, gateway, envelope: dict, event_type: str) -> tuple[int, dict]:
        """退款结果通知（WM12-B）：`REFUND.SUCCESS` 落已退款 / `CLOSED|ABNORMAL` 记失败 / 其余保持执行中。

        幂等与乱序守卫在 `refund_online.finalize_gateway_result`（已退款单的重复/迟到失败通知不改状态）。
        """
        from backend.domain.identity import refund_online
        from backend.domain.identity.wm10_service import RefundService

        data = self._decrypt(gateway, envelope)
        if data is None:
            self._audit_anomaly("退款回调解密失败", reason=event_type)
            return 400, {"code": "FAIL", "message": "回调数据无法解密"}
        out_refund_no = (data.out_refund_no or "").strip()
        if not out_refund_no:
            self._audit_anomaly("退款回调缺商户退款单号", reason=event_type)
            return 400, {"code": "FAIL", "message": "缺少退款单号"}
        req = refund_online.find_by_out_refund_no(self.db, out_refund_no)
        if req is None:
            self._audit_anomaly("退款回调找不到退款单", reason=f"out_refund_no={out_refund_no}")
            return 404, {"code": "FAIL", "message": "退款单不存在"}

        finalize = RefundService(self.db)._finalize_refund
        state = (data.refund_status or "").upper()
        if state == "SUCCESS":
            # WM12-C（审查 P1-7）：退款回调必须与**申请金额**比对——"微信只退了 10 元、
            # 本地把整单记成已退款"这类不一致，原先完全没人发现（直接照抄回调的结果）。
            # 不符 → 不落终态 + 审计 + 400（让人工核对；照抄微信金额的代价是本地账实不符）。
            if data.refund_amount is None:
                self._audit_anomaly(
                    "退款回调未携带退款金额，无法核对（如实处置）",
                    target_id=str(req.id),
                    reason=f"out_refund_no={out_refund_no}",
                )
            elif Decimal(data.refund_amount).quantize(Decimal("0.01")) != Decimal(
                req.amount
            ).quantize(Decimal("0.01")):
                self._audit_anomaly(
                    "退款回调金额与申请金额不符，拒绝落终态",
                    target_id=str(req.id),
                    reason=(
                        f"回调={data.refund_amount} 申请={req.amount} out_refund_no={out_refund_no}"
                    ),
                )
                return 400, {"code": "FAIL", "message": "退款金额不符"}
            result = refund_online.finalize_gateway_result(
                self.db,
                req.id,
                success=True,
                remark=f"微信退款到账（{out_refund_no}）",
                finalize=finalize,
            )
            ignored = bool(result.get("ignored"))
            if not ignored:
                message = "已退款"
            elif result.get("reason") == "state_not_processable":
                message = "状态不可推进，已忽略并留痕"
            else:
                message = "重复通知已忽略"
            return 200, {"code": "SUCCESS", "message": message}
        if state in ("CLOSED", "ABNORMAL"):
            result = refund_online.finalize_gateway_result(
                self.db,
                req.id,
                success=False,
                remark=f"微信退款未成功（{state}，{out_refund_no}）",
                finalize=finalize,
            )
            ignored = bool(result.get("ignored"))
            if not ignored:
                message = "已记失败"
            elif result.get("reason") == "state_not_processable":
                message = "状态不可推进，已忽略并留痕"
            else:
                message = "重复通知已忽略"
            return 200, {"code": "SUCCESS", "message": message}
        # PROCESSING 等中间态：保持"执行中"，等下一次通知（不 commit 会丢审计，所以显式提交）
        self.db.commit()
        return 200, {"code": "SUCCESS", "message": "处理中，等待后续通知"}

    # ---------- 开发/验收通道 ----------

    def simulate_callback(
        self,
        admin,
        order_no: str,
        *,
        amount: str = "",
        trade_state: str = "SUCCESS",
        transaction_id: str = "",
    ) -> dict:
        """超管演练回调（**仅 mock 支付通道可用**）：重复回调/金额篡改/非成功状态。

        生产不可达：哪怕误开，`validate_production` 也会先因 mock 通道拒绝启动。
        走的是**与真实回调完全相同**的处理链（自己构造报文喂 `handle_notify`），
        所以它演练出来的结论对真实回调有效。
        """
        provider = get_settings().PAYMENT_PROVIDER.strip().lower()
        if provider != "mock":
            raise ForbiddenError("模拟回调仅在 mock 支付通道下可用（生产请以真机真实支付验证）")
        order = (
            self.db.query(Order).filter(Order.order_no == order_no, Order.is_deleted == 0).first()
        )
        if not order:
            raise NotFoundError("订单不存在")
        # WM12-C（审查 P3-19）：非法金额字符串原先抛 `InvalidOperation` → 500；
        # 演练端点也是业务端点，参数错必须 422 而不是"服务器错误"
        try:
            amount_value = Decimal(amount) if amount else Decimal(order.amount)
        except (InvalidOperation, ValueError) as exc:
            raise ValidationError("金额格式不正确（应如 500 或 500.00）") from exc
        payload: dict[str, Any] = {
            "out_trade_no": order_no,
            "transaction_id": transaction_id or f"mock_txn_{uuid.uuid4().hex[:16]}",
            "trade_state": trade_state,
            "amount": int(yuan_to_cents(amount_value)),
        }
        body = json.dumps(
            {
                "id": uuid.uuid4().hex,
                "event_type": "TRANSACTION.SUCCESS",
                "resource_type": "encrypt-resource",
                "resource": {
                    "algorithm": "AEAD_AES_256_GCM",
                    "ciphertext": json.dumps(payload),
                    "associated_data": "transaction",
                    "nonce": uuid.uuid4().hex[:12],
                },
            },
            ensure_ascii=False,
        )
        status, resp = self.handle_notify(
            body=body, signature="mock_sign", timestamp=str(int(time.time())), nonce="mocknonce"
        )
        self._audit_anomaly(
            "超管触发支付回调演练",
            target_id=order_no,
            reason=f"trade_state={trade_state} http={status} resp={resp.get('message', '')}",
            admin=admin,
        )
        return {"http_status": status, "response": resp, "order_no": order_no}

    def simulate_refund_callback(
        self,
        admin,
        out_refund_no: str,
        *,
        refund_status: str = "SUCCESS",
        amount: str = "",
    ) -> dict:
        """超管演练退款结果通知（**仅 mock 支付通道可用**）——验收用：重复通知/迟到失败不改状态。

        与支付演练同理：走的是**与真实退款回调完全相同**的处理链（自己构造报文喂 `handle_notify`）。
        `amount` 留空 = 用退款单申请金额（正常路径）；传金额可演练"退款金额不符被拒"（审查 P1-7）。
        """
        provider = get_settings().PAYMENT_PROVIDER.strip().lower()
        if provider != "mock":
            raise ForbiddenError("模拟回调仅在 mock 支付通道可用（生产请以真机真实退款验证）")
        from backend.domain.identity import refund_online

        req = refund_online.find_by_out_refund_no(self.db, out_refund_no)
        if req is None:
            raise NotFoundError("退款单不存在（单号格式 RF{退款单id}-{次数}-{笔}）")
        try:
            amount_value = Decimal(amount) if amount else Decimal(req.amount)
        except (InvalidOperation, ValueError) as exc:
            raise ValidationError("金额格式不正确（应如 99 或 99.00）") from exc
        payload = {
            "out_refund_no": out_refund_no,
            "transaction_id": f"mock_refund_{uuid.uuid4().hex[:16]}",
            "refund_status": refund_status,
            # 与微信 `amount.refund` 同语义（单位：分）
            "refund_amount": int(yuan_to_cents(amount_value)),
        }
        body = json.dumps(
            {
                "id": uuid.uuid4().hex,
                "event_type": f"REFUND.{refund_status.upper()}",
                "resource_type": "encrypt-resource",
                "resource": {
                    "algorithm": "AEAD_AES_256_GCM",
                    "ciphertext": json.dumps(payload),
                    "associated_data": "refund",
                    "nonce": uuid.uuid4().hex[:12],
                },
            },
            ensure_ascii=False,
        )
        status, resp = self.handle_notify(
            body=body, signature="mock_sign", timestamp=str(int(time.time())), nonce="mocknonce"
        )
        self._audit_anomaly(
            "超管触发退款回调演练",
            target_id=str(req.id),
            reason=f"refund_status={refund_status} http={status} resp={resp.get('message', '')}",
            admin=admin,
        )
        return {
            "http_status": status,
            "response": resp,
            "out_refund_no": out_refund_no,
            "refund_request_id": req.id,
        }

    # ---------- 内部 ----------

    def _settle_online(self, order: Order, *, transaction_id: str, remark: str) -> Order:
        """线上收款结算（复用人工确认的 `_settle_paid` 单一链路）+ 提交。"""
        from backend.domain.identity.order_service import OrderService

        OrderService(self.db)._settle_paid(
            order,
            actor=self._system_actor(),
            pay_method="wechat",
            remark=remark,
            payer_id=None,  # 线上支付没有经办管理员
            action="order.payment_callback",
            reason=remark,
            transaction_id=transaction_id or None,
        )
        self.db.commit()
        return order

    @staticmethod
    def _system_actor():
        return types.SimpleNamespace(id=0, display_name=SYSTEM_ACTOR_NAME)

    @staticmethod
    def _parent_actor(parent: Parent):
        return types.SimpleNamespace(id=0, display_name=f"家长(小程序) parent={parent.id}")

    def _pay_result(self, order: Order, *, already_paid: bool, instant_paid: bool = False) -> dict:
        return {
            "order_no": order.order_no,
            "order_id": order.id,
            "amount": str(order.amount),
            "status": order.status,
            "already_paid": already_paid,
            "instant_paid": instant_paid,
            "pay_params": {},
        }

    def _owned_order(self, parent: Parent, order_id: int) -> Order:
        """归属红线：只能对自己的孩子/家长级订单发起支付（越权一律 404）。"""
        order = self.db.query(Order).filter(Order.id == order_id, Order.is_deleted == 0).first()
        if not order or order.parent_id != parent.id:
            raise NotFoundError("订单不存在")
        return order

    def _locked_by_order_no(self, order_no: str) -> Order:
        order = (
            self.db.query(Order)
            .filter(Order.order_no == order_no, Order.is_deleted == 0)
            .with_for_update()
            .populate_existing()
            .first()
        )
        if not order:
            raise NotFoundError("订单不存在")
        return order

    def _assert_first_activity_payable(self, order: Order) -> None:
        """99 元首场发起支付前的锁内复查（R-321 每账号一次；真正入账时还会再查一次）。"""
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

    @staticmethod
    def _parse_envelope(body: str) -> dict | None:
        try:
            envelope = json.loads(body)
        except (TypeError, ValueError):
            return None
        return envelope if isinstance(envelope, dict) else None

    @staticmethod
    def _decrypt(gateway, envelope: dict):
        resource = envelope.get("resource") or {}
        ciphertext = str(resource.get("ciphertext") or "")
        if not ciphertext:
            return None
        try:
            return run_coro(
                gateway.decrypt_callback_data(
                    ciphertext,
                    str(resource.get("nonce") or ""),
                    str(resource.get("associated_data") or ""),
                )
            )
        except Exception as exc:
            logger.error("支付回调解密失败: %s", exc, exc_info=True)
            return None

    def _audit_anomaly(
        self,
        message: str,
        *,
        target_id: str = "",
        reason: str = "",
        admin=None,
    ) -> None:
        """异常/演练留痕（**不抛错**：回调路径的任何异常都不能变成"无应答"）。

        资金相关异常必须留痕，否则出问题时只能看到"钱少了"而查不到经过。
        """
        logger.warning("支付异常留痕: %s | %s", message, reason)
        publish_audit(
            self.db,
            admin=admin or self._system_actor(),
            action="payment.anomaly",
            target_type="order",
            target_id=target_id or "-",
            detail={"message": message},
            reason=reason,
        )
        self.db.commit()
