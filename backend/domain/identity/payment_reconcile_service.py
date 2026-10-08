# backend/domain/identity/payment_reconcile_service.py — 每日资金对账（WM12-B）
"""对账只**报**不改（口径 docs/09 WM12-B §二.5）——自动改账是下一个事故的来源。

两条腿：
1. **本地一致性审计**（不依赖微信，永远能跑）——四类差异：
   L1 订单主状态与退款终态冲突（已支付却有已退款单 / 已退款却查无退款单）；
   L2 悬挂退款（`processing` 超过 24h：网关受理了但结果没回来）；
   L3 线上退款链路不完整（有商户退款单号，原订单却没有微信支付单号）；
   L4 僵尸单残余（超时未支付单仍在：清理任务可能没跑）。
2. **微信账单比对**（真通道）——下载当日对账单（SHA256 校验）后逐行比：
   账单有我库无 / 我库有账单无 / 金额不符 / 退款状态不符。
   缺凭据、当日无账单 → 记 `skipped`（**不把任务跑红**：每天一条假失败没人会再看这条告警）。
"""

from __future__ import annotations

import json
import logging
from datetime import date, datetime, time, timedelta
from decimal import Decimal, InvalidOperation

from sqlalchemy import func, or_
from sqlalchemy.orm import Session

from backend.common.async_utils import run_coro
from backend.domain.identity.models import Order, RefundRequest
from backend.domain.identity.payment_models import PaymentReconciliation

logger = logging.getLogger(__name__)

#: 悬挂退款阈值（小时）：超过它就该有人去看一眼——网关受理了但结果一直没回来
STUCK_REFUND_HOURS = 24

#: 微信账单 CSV 的必需列（缺列说明格式变了，必须报警而不是静默少比几列）
BILL_REQUIRED_COLUMNS = ("商户订单号", "交易状态", "订单金额")


class PaymentReconcileService:
    """对账执行与报表查询（Service 统一 commit）。"""

    def __init__(self, db: Session):
        self.db = db

    # ---------- 对外 ----------

    def run(
        self,
        *,
        bill_date: date | None = None,
        with_wechat: bool = True,
        trigger: str = PaymentReconciliation.TRIGGER_MANUAL,
        actor=None,
    ) -> dict:
        """跑一轮对账：本地审计必跑；微信账单在真通道且给了日期时再跑。"""
        day = bill_date or date.today()
        checked, diffs = self._local_audit()
        reports = [
            self._record(
                source=PaymentReconciliation.SOURCE_LOCAL,
                day=day,
                checked=checked,
                diffs=diffs,
                trigger=trigger,
                actor=actor,
            )
        ]
        if with_wechat:
            reports.append(self._reconcile_with_wechat(day, trigger=trigger, actor=actor))
        return {
            "bill_date": str(day),
            "reports": reports,
            "diff_total": sum(r["diff_count"] for r in reports),
        }

    def latest(self, limit: int = 10) -> list[dict]:
        rows = (
            self.db.query(PaymentReconciliation)
            .filter(PaymentReconciliation.is_deleted == 0)
            .order_by(PaymentReconciliation.id.desc())
            .limit(limit)
            .all()
        )
        return [self._view(r) for r in rows]

    # ---------- ① 本地一致性审计 ----------

    def _local_audit(self) -> tuple[int, list[dict]]:
        diffs: list[dict] = []
        diffs += self._audit_status_conflict()
        diffs += self._audit_stuck_refunds()
        diffs += self._audit_online_chain_gap()
        diffs += self._audit_zombie_orders()
        checked = (
            self.db.query(func.count(Order.id)).filter(Order.is_deleted == 0).scalar() or 0
        ) + (
            self.db.query(func.count(RefundRequest.id))
            .filter(RefundRequest.is_deleted == 0)
            .scalar()
            or 0
        )
        return int(checked), diffs

    def _audit_status_conflict(self) -> list[dict]:
        """L1：订单主状态与退款单终态互相矛盾（两个方向都查，都是钱的去向说不清）。"""
        out: list[dict] = []
        refunded_ids = [
            r[0]
            for r in self.db.query(RefundRequest.order_id)
            .filter(
                RefundRequest.is_deleted == 0,
                RefundRequest.kind == RefundRequest.KIND_ORDER,
                RefundRequest.status == RefundRequest.STATUS_REFUNDED,
                RefundRequest.order_id.isnot(None),
            )
            .all()
        ]
        if refunded_ids:
            paid_but_refunded = (
                self.db.query(Order)
                .filter(
                    Order.id.in_(refunded_ids),
                    Order.is_deleted == 0,
                    Order.status == Order.STATUS_PAID,
                )
                .all()
            )
            out += [
                {
                    "kind": "paid_order_with_refunded_request",
                    "ref": o.order_no,
                    "message": f"订单仍为已支付，但已有退款单终态=已退款（订单 id={o.id}）",
                }
                for o in paid_but_refunded
            ]
        refunded_orders = (
            self.db.query(Order)
            .filter(Order.is_deleted == 0, Order.status == Order.STATUS_REFUNDED)
            .all()
        )
        for o in refunded_orders:
            if o.id in set(refunded_ids):
                continue
            out.append(
                {
                    "kind": "refunded_order_without_request",
                    "ref": o.order_no,
                    "message": "订单已退款，但查不到对应的已退款单（历史旁路或手工改库？）",
                }
            )
        return out

    def _audit_stuck_refunds(self) -> list[dict]:
        """L2：退款单卡在"执行中"超阈值——线上原路退款受理了但结果没回来。"""
        cutoff = datetime.now() - timedelta(hours=STUCK_REFUND_HOURS)
        rows = (
            self.db.query(RefundRequest)
            .filter(
                RefundRequest.is_deleted == 0,
                RefundRequest.status == RefundRequest.STATUS_PROCESSING,
                or_(
                    RefundRequest.reviewed_at.is_(None),
                    RefundRequest.reviewed_at < cutoff,
                ),
            )
            .all()
        )
        return [
            {
                "kind": "stuck_refund",
                "ref": f"refund#{r.id}",
                "message": (
                    f"退款单卡在执行中超过 {STUCK_REFUND_HOURS}h（金额 {r.amount}，"
                    f"单号 {r.out_refund_no or '-'}）——请到微信商户平台查该笔退款结果"
                ),
            }
            for r in rows
        ]

    def _audit_online_chain_gap(self) -> list[dict]:
        """L3：有商户退款单号（走过网关）却找不到原单的微信支付单号——链路不完整。"""
        rows = (
            self.db.query(RefundRequest)
            .filter(
                RefundRequest.is_deleted == 0,
                RefundRequest.out_refund_no.isnot(None),
                RefundRequest.out_refund_no != "",
                RefundRequest.kind == RefundRequest.KIND_ORDER,
                RefundRequest.order_id.isnot(None),
            )
            .all()
        )
        out: list[dict] = []
        for r in rows:
            order = self.db.query(Order).filter(Order.id == r.order_id).first()
            if order is None:
                out.append(
                    {
                        "kind": "refund_order_missing",
                        "ref": f"refund#{r.id}",
                        "message": f"退款单关联的订单不存在（order_id={r.order_id}）",
                    }
                )
                continue
            if not (order.transaction_id or "").strip():
                out.append(
                    {
                        "kind": "online_refund_without_transaction",
                        "ref": order.order_no,
                        "message": (
                            f"退款单 {r.out_refund_no} 已提交微信，但原订单缺微信支付单号"
                            "（对账时无法与账单勾对）"
                        ),
                    }
                )
        return out

    def _audit_zombie_orders(self) -> list[dict]:
        """L4：超时未支付单仍在（清理任务本该把它们取消，留着就说明任务没跑或卡了）。"""
        from backend.common.config_service import ConfigService

        hours = int(ConfigService(self.db).get_value("pending_payment_timeout_hours", "48"))
        cutoff = datetime.now() - timedelta(hours=hours)
        rows = (
            self.db.query(Order)
            .filter(
                Order.is_deleted == 0,
                Order.status.in_([Order.STATUS_PENDING_PAYMENT, Order.STATUS_PENDING_MANUAL]),
                Order.create_time < cutoff,
            )
            .all()
        )
        return [
            {
                "kind": "zombie_order",
                "ref": o.order_no,
                "message": (
                    f"待支付单超过 {hours}h 仍在（状态 {o.status}，金额 {o.amount}）"
                    "——检查「订单超时取消」任务"
                ),
            }
            for o in rows
        ]

    # ---------- ② 微信账单比对 ----------

    def _reconcile_with_wechat(self, day: date, *, trigger: str, actor=None) -> dict:
        from backend.common.exceptions import PaymentError
        from backend.config import get_settings
        from backend.integrations.payment import get_payment_gateway

        settings = get_settings()
        if not settings.PAYMENT_ENABLED:
            return self._record(
                source=PaymentReconciliation.SOURCE_WECHAT,
                day=day,
                checked=0,
                diffs=[],
                trigger=trigger,
                actor=actor,
                status=PaymentReconciliation.STATUS_SKIPPED,
                note="线上支付未开启（PAYMENT_ENABLED=false），无需比对",
            )
        if settings.PAYMENT_PROVIDER.strip().lower() != "wechat":
            return self._record(
                source=PaymentReconciliation.SOURCE_WECHAT,
                day=day,
                checked=0,
                diffs=[],
                trigger=trigger,
                actor=actor,
                status=PaymentReconciliation.STATUS_SKIPPED,
                note="当前为 mock 支付通道（未接微信），账单比对跳过",
            )
        try:
            csv_text = run_coro(get_payment_gateway().download_bill(day.isoformat(), "ALL"))
        except PaymentError as exc:
            message = str(exc)
            status = (
                PaymentReconciliation.STATUS_SKIPPED
                if "NO_BILL_EXIST" in message or "账单" in message
                else PaymentReconciliation.STATUS_FAILED
            )
            return self._record(
                source=PaymentReconciliation.SOURCE_WECHAT,
                day=day,
                checked=0,
                diffs=[],
                trigger=trigger,
                actor=actor,
                status=status,
                note=f"账单下载失败：{message[:180]}",
            )
        except Exception as exc:  # 网关/网络/解密任何异常都不该让整轮对账崩掉
            logger.error("微信账单下载异常: %s", exc, exc_info=True)
            return self._record(
                source=PaymentReconciliation.SOURCE_WECHAT,
                day=day,
                checked=0,
                diffs=[],
                trigger=trigger,
                actor=actor,
                status=PaymentReconciliation.STATUS_FAILED,
                note=f"账单下载异常：{exc}"[:180],
            )
        checked, diffs = self._diff_bill(day, csv_text)
        return self._record(
            source=PaymentReconciliation.SOURCE_WECHAT,
            day=day,
            checked=checked,
            diffs=diffs,
            trigger=trigger,
            actor=actor,
        )

    def _diff_bill(self, day: date, csv_text: str) -> tuple[int, list[dict]]:
        """逐行比对微信账单与本地订单/退款。返回 (核对笔数, 差异列表)。"""
        rows = self._parse_bill(csv_text)
        start = datetime.combine(day, time.min)
        end = start + timedelta(days=1)
        our_orders = (
            self.db.query(Order)
            .filter(
                Order.is_deleted == 0,
                Order.paid_at.isnot(None),
                Order.paid_at >= start,
                Order.paid_at < end,
                Order.status.in_([Order.STATUS_PAID, Order.STATUS_REFUNDED]),
            )
            .all()
        )
        ours = {o.order_no: o for o in our_orders}
        bill_seen: set[str] = set()
        diffs: list[dict] = []
        checked = 0
        for row in rows:
            out_trade_no = (row.get("商户订单号") or "").strip()
            state = (row.get("交易状态") or "").strip().upper()
            if not out_trade_no:
                continue
            if state not in ("SUCCESS", "REFUND"):
                continue  # 未支付/已关闭等状态不参与"我们的已支付"比对
            checked += 1
            bill_seen.add(out_trade_no)
            order = ours.get(out_trade_no)
            if order is None:
                diffs.append(
                    {
                        "kind": "bill_only",
                        "ref": out_trade_no,
                        "message": (
                            f"微信账单有此笔（{state}，金额 {row.get('订单金额', '')}），"
                            "本地查不到当日已支付订单"
                        ),
                    }
                )
                continue
            if not self._amount_eq(row.get("订单金额"), order.amount):
                diffs.append(
                    {
                        "kind": "amount_mismatch",
                        "ref": out_trade_no,
                        "message": f"金额不符：账单 {row.get('订单金额')} vs 本地 {order.amount}",
                    }
                )
            refund_no = (row.get("商户退款单号") or "").strip()
            if refund_no:
                diffs += self._diff_refund_row(row, refund_no)
        for order_no in ours:
            if order_no not in bill_seen:
                diffs.append(
                    {
                        "kind": "local_only",
                        "ref": order_no,
                        "message": "本地当日已支付，但微信账单里没有（可能账期延迟或单号不符）",
                    }
                )
        return checked, diffs

    def _diff_refund_row(self, row: dict, out_refund_no: str) -> list[dict]:
        from backend.domain.identity import refund_online

        req = refund_online.find_by_out_refund_no(self.db, out_refund_no)
        if req is None:
            return [
                {
                    "kind": "bill_refund_only",
                    "ref": out_refund_no,
                    "message": "微信账单有这笔退款，本地查不到对应退款单",
                }
            ]
        out: list[dict] = []
        if not self._amount_eq(row.get("退款金额"), req.amount):
            out.append(
                {
                    "kind": "refund_amount_mismatch",
                    "ref": out_refund_no,
                    "message": f"退款金额不符：账单 {row.get('退款金额')} vs 本地 {req.amount}",
                }
            )
        bill_state = (row.get("退款状态") or "").strip().upper()
        if bill_state == "SUCCESS" and req.status != RefundRequest.STATUS_REFUNDED:
            out.append(
                {
                    "kind": "refund_state_mismatch",
                    "ref": out_refund_no,
                    "message": f"微信已退款成功，本地退款单状态仍是 {req.status}（回调可能没到）",
                }
            )
        return out

    @staticmethod
    def _parse_bill(csv_text: str) -> list[dict]:
        """解析对账单 CSV：找表头行 → 按列名取值 → 遇汇总行即止。

        格式变了一律抛错（宁可整轮 failed 也不要静默少比几列——那才是真的假绿）。
        """
        lines = [ln for ln in (csv_text or "").splitlines() if ln.strip()]
        header_idx = next(
            (i for i, ln in enumerate(lines) if "商户订单号" in ln and "交易状态" in ln),
            None,
        )
        if header_idx is None:
            raise ValueError("对账单缺少表头（商户订单号/交易状态）")
        header = [c.strip() for c in lines[header_idx].split(",")]
        missing = [c for c in BILL_REQUIRED_COLUMNS if c not in header]
        if missing:
            raise ValueError(f"对账单缺少必需列：{'/'.join(missing)}")
        out: list[dict] = []
        for ln in lines[header_idx + 1 :]:
            if ln.startswith("总交易单数") or ln.startswith("总笔数"):
                break
            cells = ln.split(",")
            if len(cells) < len(header):
                raise ValueError(f"对账单行字段数不足：{ln[:60]}")
            out.append({header[i]: cells[i].strip().lstrip("`") for i in range(len(header))})
        return out

    @staticmethod
    def _amount_eq(bill_amount: str | None, local: Decimal) -> bool:
        if bill_amount is None or str(bill_amount).strip() == "":
            return False
        try:
            return Decimal(str(bill_amount).replace(",", "").strip()) == Decimal(local).quantize(
                Decimal("0.01")
            )
        except (InvalidOperation, ValueError):
            return False

    # ---------- 报表 ----------

    def _record(
        self,
        *,
        source: str,
        day: date,
        checked: int,
        diffs: list[dict],
        trigger: str,
        actor=None,
        status: str | None = None,
        note: str = "",
    ) -> dict:
        if status is None:
            status = PaymentReconciliation.STATUS_DIFF if diffs else PaymentReconciliation.STATUS_OK
        # detail 统一是 JSON 对象 {diffs, note}：既能放差异清单，也能放"为什么跳过"
        # （早先靠"能否 parse 成 list"区分，结果把 skipped 的原因读丢了）
        detail = json.dumps({"diffs": diffs, "note": note}, ensure_ascii=False)
        row = PaymentReconciliation(
            bill_date=day,
            source=source,
            status=status,
            checked_count=checked,
            diff_count=len(diffs),
            detail=detail,
            trigger=trigger,
            actor_id=getattr(actor, "id", None),
            finished_at=datetime.now(),
        )
        self.db.add(row)
        self.db.commit()
        return self._view(row)

    @staticmethod
    def _view(row: PaymentReconciliation) -> dict:
        payload: dict = {}
        try:
            parsed = json.loads(row.detail) if row.detail else {}
            payload = parsed if isinstance(parsed, dict) else {}
        except (TypeError, ValueError):
            payload = {}
        detail = payload.get("diffs") or []
        return {
            "id": row.id,
            "bill_date": str(row.bill_date),
            "source": row.source,
            "status": row.status,
            "checked_count": row.checked_count,
            "diff_count": row.diff_count,
            "detail": detail,
            "note": payload.get("note", ""),
            "trigger": row.trigger,
            "finished_at": str(row.finished_at or ""),
        }
