# backend/domain/identity/payment_reconcile_service.py — 每日资金对账（WM12-B，WM12-C 加固）
"""对账只**报**不改（口径 docs/09 WM12-B §二.5）——自动改账是下一个事故的来源。

两条腿：
1. **本地一致性审计**（不依赖微信，永远能跑）——六类差异：
   L1 订单主状态与退款终态冲突（已支付却有已退款单 / 已退款却查无退款单）；
   L2 悬挂退款（`processing` 超过 24h：网关受理了但结果没回来；含"未知态"未收口）；
   L3 线上退款链路不完整（有商户退款单号，原订单却缺微信支付单号）；
   L4 僵尸单残余（超时未支付单仍在：清理任务可能没跑）；
   L5 订单退款合计超付（同一订单活跃退款单合计 > 订单金额，审查 P0-1 兜底）；
   L6 押金退款合计超付（同一孩子活跃押金退款合计 > 已缴押金，审查 P0-1 兜底）。
2. **微信账单比对**（真通道）——下载当日对账单（SHA256 校验）后逐行比：
   账单有我库无 / 我库有账单无 / 金额不符 / 退款金额或状态不符。
   `skipped` 是**白名单**（审查 P0-2）：只有"未开通道"与"当日无账单（NO_BILL_EXIST）"两种；
   哈希校验失败、缺哈希、缺列、解析失败一律 `failed`（文件不可信必须有人看见）。

性能纪律（审查 P2-9）：本地审计全部走 JOIN / GROUP BY 聚合 + 有界 limit，
不做"全表取回 Python 里循环"（订单量大时那是 O(n) 内存 + N+1 查询）。
"""

from __future__ import annotations

import csv
import io
import json
import logging
from datetime import date, datetime, time, timedelta
from decimal import Decimal, InvalidOperation

from sqlalchemy import and_, func, or_
from sqlalchemy.orm import Session

from backend.common.async_utils import run_coro
from backend.domain.identity.models import Order, RefundRequest
from backend.domain.identity.payment_models import PaymentReconciliation

logger = logging.getLogger(__name__)

#: 悬挂退款阈值（小时）：超过它就该有人去看一眼——网关受理了但结果一直没回来
STUCK_REFUND_HOURS = 24

#: 本地审计单类差异的返回上限（有界查询：异常通常个位数，真到上限说明系统性问题，先看这些）
AUDIT_ROW_LIMIT = 2000

#: `detail` 列的落库预算（UTF-8 字节）。MySQL TEXT 上限 64KB——**2026-10-08 十万级压测实锤**：
#: 1100 条差异的 JSON 有 176KB，`INSERT` 直接 `DataError 1406`，**整轮对账一笔都写不进去**
#: （系统性事故时反而没有留痕）。故落库只存样本 + 分类计数 + 截断标记；真实总数在 `diff_count` 列。
DETAIL_BYTE_BUDGET = 32_000

#: 微信账单 CSV 的必需列（缺列说明格式变了，必须报警而不是静默少比几列）
BILL_REQUIRED_COLUMNS = ("商户订单号", "交易状态", "订单金额")

#: 账单里参与"已支付"比对的交易状态（其余状态不参与）
BILL_COUNTED_STATES = ("SUCCESS", "REFUND")


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
        diffs += self._audit_refund_overpaid()
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
        """L1：订单主状态与退款单终态互相矛盾（两个方向都查，都是钱的去向说不清）。

        JOIN 查询（审查 P2-9）：原先把"所有已退款退款单的 order_id"拉回 Python 再按它查订单，
        订单量一大就是无界 IN + O(n·m) 的 set 重建——现在两条 SQL 各自只回差异行。
        """
        out: list[dict] = []
        paid_but_refunded = (
            self.db.query(Order)
            .join(RefundRequest, RefundRequest.order_id == Order.id)
            .filter(
                RefundRequest.is_deleted == 0,
                RefundRequest.kind == RefundRequest.KIND_ORDER,
                RefundRequest.status == RefundRequest.STATUS_REFUNDED,
                Order.is_deleted == 0,
                Order.status == Order.STATUS_PAID,
            )
            .distinct()
            .limit(AUDIT_ROW_LIMIT)
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
            .outerjoin(
                RefundRequest,
                and_(
                    RefundRequest.order_id == Order.id,
                    RefundRequest.is_deleted == 0,
                    RefundRequest.kind == RefundRequest.KIND_ORDER,
                    RefundRequest.status == RefundRequest.STATUS_REFUNDED,
                ),
            )
            .filter(
                Order.is_deleted == 0,
                Order.status == Order.STATUS_REFUNDED,
                RefundRequest.id.is_(None),
            )
            .limit(AUDIT_ROW_LIMIT)
            .all()
        )
        out += [
            {
                "kind": "refunded_order_without_request",
                "ref": o.order_no,
                "message": "订单已退款，但查不到对应的已退款单（历史旁路或手工改库？）",
            }
            for o in refunded_orders
        ]
        return out

    def _audit_stuck_refunds(self) -> list[dict]:
        """L2：退款单卡在"执行中"超阈值——线上原路退款受理了但结果没回来（含未知态）。"""
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
            .order_by(RefundRequest.id.desc())
            .limit(AUDIT_ROW_LIMIT)
            .all()
        )
        return [
            {
                "kind": "stuck_refund",
                "ref": f"refund#{r.id}",
                "message": (
                    f"退款单卡在执行中超过 {STUCK_REFUND_HOURS}h（金额 {r.amount}，"
                    f"单号 {r.out_refund_no or '-'}"
                    + ("，**网关结果未知**" if r.gateway_unknown_at else "")
                    + "）——用退款中心的「查单」（POST /api/admin/refund-requests/{id}/query-gateway）"
                    "确认微信侧结果"
                ),
            }
            for r in rows
        ]

    def _audit_online_chain_gap(self) -> list[dict]:
        """L3：有商户退款单号（走过网关）却找不到原单的微信支付单号——链路不完整。

        一条 LEFT JOIN 只回差异行（审查 P2-9：原先是"全量退款单 + 逐行查订单"）。
        """
        rows = (
            self.db.query(RefundRequest, Order)
            .outerjoin(Order, Order.id == RefundRequest.order_id)
            .filter(
                RefundRequest.is_deleted == 0,
                RefundRequest.out_refund_no.isnot(None),
                RefundRequest.out_refund_no != "",
                RefundRequest.kind == RefundRequest.KIND_ORDER,
                RefundRequest.order_id.isnot(None),
                or_(
                    Order.id.is_(None),
                    Order.transaction_id.is_(None),
                    Order.transaction_id == "",
                ),
            )
            .limit(AUDIT_ROW_LIMIT)
            .all()
        )
        out: list[dict] = []
        for r, order in rows:
            if order is None:
                out.append(
                    {
                        "kind": "refund_order_missing",
                        "ref": f"refund#{r.id}",
                        "message": f"退款单关联的订单不存在（order_id={r.order_id}）",
                    }
                )
                continue
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

    def _audit_refund_overpaid(self) -> list[dict]:
        """L5/L6：**退款合计不得超付**（审查 P0-1 的对账兜底——重复出款的最后一道探测器）。

        "活跃退款单" = `approved`/`processing`/`refunded`（钱要出去或已经出去）；`pending` 只是
        申请、`rejected`/`cancelled` 不会出款，都不计入。
        两条都是 SQL 聚合（`GROUP BY … HAVING SUM(...) > 上限`，审查 P2-9），不拉全表。
        """
        active = (
            RefundRequest.STATUS_APPROVED,
            RefundRequest.STATUS_PROCESSING,
            RefundRequest.STATUS_REFUNDED,
        )
        out: list[dict] = []
        order_rows = (
            self.db.query(
                Order.order_no,
                Order.amount,
                func.sum(RefundRequest.amount).label("refund_total"),
                func.count(RefundRequest.id).label("refund_count"),
            )
            .join(RefundRequest, RefundRequest.order_id == Order.id)
            .filter(
                RefundRequest.is_deleted == 0,
                RefundRequest.kind == RefundRequest.KIND_ORDER,
                RefundRequest.status.in_(active),
                Order.is_deleted == 0,
            )
            .group_by(Order.id, Order.order_no, Order.amount)
            .having(func.sum(RefundRequest.amount) > Order.amount)
            .limit(AUDIT_ROW_LIMIT)
            .all()
        )
        for order_no, amount, total, count in order_rows:
            out.append(
                {
                    "kind": "refund_overpaid",
                    "ref": order_no,
                    "message": (
                        f"同一订单 {count} 笔活跃退款合计 {total} 超过订单金额 {amount}"
                        "——重复出款风险，请人工核对退款单号与微信侧记录"
                    ),
                }
            )
        deposit_rows = (
            self.db.query(
                RefundRequest.child_id,
                func.sum(RefundRequest.amount).label("refund_total"),
                func.count(RefundRequest.id).label("refund_count"),
            )
            .filter(
                RefundRequest.is_deleted == 0,
                RefundRequest.kind == RefundRequest.KIND_DEPOSIT,
                RefundRequest.status.in_(active),
            )
            .group_by(RefundRequest.child_id)
            .all()
        )
        if deposit_rows:
            child_ids = [row[0] for row in deposit_rows]
            paid_rows = (
                self.db.query(Order.child_id, func.sum(Order.amount).label("paid_total"))
                .filter(
                    Order.is_deleted == 0,
                    # 已退款的押金单也算"已缴"（否则退完一笔后上限归零，反手报一个假超付）
                    Order.status.in_([Order.STATUS_PAID, Order.STATUS_REFUNDED]),
                    Order.order_type.in_([Order.TYPE_DEPOSIT, Order.TYPE_DEPOSIT_SUPPLEMENT]),
                    Order.child_id.in_(child_ids),
                )
                .group_by(Order.child_id)
                .all()
            )
            paid = {cid: total for cid, total in paid_rows}
            for child_id, refund_total, count in deposit_rows:
                cap = paid.get(child_id, Decimal("0"))
                if refund_total > cap:
                    out.append(
                        {
                            "kind": "deposit_refund_overpaid",
                            "ref": f"child#{child_id}",
                            "message": (
                                f"该孩子 {count} 笔活跃押金退款合计 {refund_total} 超过已缴押金 "
                                f"{cap}——重复出款风险，请人工核对"
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
            .order_by(Order.id.desc())
            .limit(AUDIT_ROW_LIMIT)
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
            # WM12-C（审查 P0-2c）：`skipped` 是白名单——"未开通道"在上面两个早退分支，
            # 这里只剩"当日无账单"（微信 NO_BILL_EXIST）。**哈希校验失败/缺哈希/缺列/格式变化
            # 一律 failed**：把"文件不可信"记成"跳过"，运营会读成"没问题"（修复前正是如此）。
            status = (
                PaymentReconciliation.STATUS_SKIPPED
                if "NO_BILL_EXIST" in message
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
                note=(
                    f"当日无对账单（{message[:180]}）"
                    if status == PaymentReconciliation.STATUS_SKIPPED
                    else f"账单不可用/不可信，需人工看：{message[:180]}"
                ),
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
        """逐行比对微信账单与本地订单/退款。返回 (核对笔数, 差异列表)。

        WM12-C（审查 P0-2a）：**退款行按 `out_refund_no` 独立核对**，不依赖"当日已支付订单"集合——
        退款的账单日往往晚于支付日，原先跨日退款会走 `bill_only` 分支并 `continue`，
        既产生假差异、又让跨日退款的金额/状态**永远不被校验**。
        """
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
        #: P0-C 纵深：按退款单累计账单出款（同一退款单换号重提 → 累计值超退款单金额才算得出）
        refund_outflow: dict[int, Decimal] = {}
        checked = 0
        for row in rows:
            out_trade_no = (row.get("商户订单号") or "").strip()
            state = (row.get("交易状态") or "").strip().upper()
            if not out_trade_no:
                continue
            if state not in BILL_COUNTED_STATES:
                continue  # 未支付/已关闭等状态不参与"我们的已支付"比对
            refund_no = (row.get("商户退款单号") or "").strip()
            order = ours.get(out_trade_no)
            if order is None and refund_no:
                # 跨日退款：这行描述的是**往日那笔交易**的退款 → 只按商户退款单号核对退款侧
                checked += 1
                diffs += self._diff_refund_row(row, refund_no)
                self._accumulate_refund_outflow(refund_outflow, refund_no, row)
                continue
            if order is None:
                checked += 1
                bill_seen.add(out_trade_no)
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
            checked += 1
            bill_seen.add(out_trade_no)
            if not self._amount_eq(row.get("订单金额"), order.amount):
                diffs.append(
                    {
                        "kind": "amount_mismatch",
                        "ref": out_trade_no,
                        "message": f"金额不符：账单 {row.get('订单金额')} vs 本地 {order.amount}",
                    }
                )
            if refund_no:
                diffs += self._diff_refund_row(row, refund_no)
                self._accumulate_refund_outflow(refund_outflow, refund_no, row)
        for order_no in ours:
            if order_no not in bill_seen:
                diffs.append(
                    {
                        "kind": "local_only",
                        "ref": order_no,
                        "message": "本地当日已支付，但微信账单里没有（可能账期延迟或单号不符）",
                    }
                )
        diffs += self._refund_outflow_diffs(refund_outflow)
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
        bill_amount = (row.get("退款金额") or "").strip()
        if not bill_amount:
            # 有退款单号却没有退款金额 → 列名/格式变了：显式报出来，别伪装成"金额不符"
            out.append(
                {
                    "kind": "refund_field_missing",
                    "ref": out_refund_no,
                    "message": "账单退款行缺少「退款金额」列值（列名或格式可能已变，需核对样本）",
                }
            )
        elif not self._amount_eq(bill_amount, req.amount):
            out.append(
                {
                    "kind": "refund_amount_mismatch",
                    "ref": out_refund_no,
                    "message": f"退款金额不符：账单 {bill_amount} vs 本地 {req.amount}",
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
        """解析对账单 CSV：`csv` 模块逐行读 → 按列名取值 → 遇汇总行即止。

        WM12-C（审查 P0-2b）：早先 `ln.split(",")` 是朴素切分——商品名含逗号（如
        "绘本套装,上册,下册"）会让后面所有列**整体错位**（金额列读到费率列），
        既产生假差异、也可能"错位后碰巧相等"从而漏检。`csv` 模块按引号规则切，不会错列。
        顺带处理 BOM（微信账单可能带 utf-8 BOM）与单元格前缀反引号（Excel 文本标记）。

        格式变了一律抛错（宁可整轮 failed 也不要静默少比几列——那才是真的假绿）。
        """
        text = (csv_text or "").lstrip("\ufeff")
        rows = list(csv.reader(io.StringIO(text)))
        header_idx = None
        for i, cells in enumerate(rows):
            norm = [c.strip().lstrip("`") for c in cells]
            if "商户订单号" in norm and "交易状态" in norm:
                header_idx = i
                break
        if header_idx is None:
            raise ValueError("对账单缺少表头（商户订单号/交易状态）")
        header = [c.strip().lstrip("`") for c in rows[header_idx]]
        missing = [c for c in BILL_REQUIRED_COLUMNS if c not in header]
        if missing:
            raise ValueError(f"对账单缺少必需列：{'/'.join(missing)}")
        out: list[dict] = []
        for cells in rows[header_idx + 1 :]:
            if not cells:
                continue
            first = (cells[0] or "").strip().lstrip("`")
            if first.startswith("总交易单数") or first.startswith("总笔数"):
                break
            if len(cells) < len(header):
                raise ValueError(f"对账单行字段数不足：{','.join(cells)[:60]}")
            out.append(
                {header[i]: (cells[i] or "").strip().lstrip("`") for i in range(len(header))}
            )
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

    @staticmethod
    def _parse_amount(bill_amount: str | None) -> Decimal | None:
        """账单金额列 → Decimal（不可解析返回 None，由调用方决定报警还是跳过）。"""
        if bill_amount is None or str(bill_amount).strip() == "":
            return None
        try:
            return Decimal(str(bill_amount).replace(",", "").strip()).quantize(Decimal("0.01"))
        except (InvalidOperation, ValueError):
            return None

    def _accumulate_refund_outflow(
        self, acc: dict[int, Decimal], out_refund_no: str, row: dict
    ) -> None:
        """按**退款单**（不是退款单号）累计账单里的出款金额。

        P0-C 纵深（2026-10-09 专家复核 + 亲验）：逐号对号时，同一退款单换号重提的两笔
        各自都能通过校验（两号反解同一个 req，金额与状态都对得上）——只有按退款单累计
        才看得出"一笔退款出了两次钱"。
        """
        from backend.domain.identity import refund_online

        req = refund_online.find_by_out_refund_no(self.db, out_refund_no)
        if req is None:
            return  # 反解不到的单已由 _diff_refund_row 报 bill_refund_only
        amount = self._parse_amount(row.get("退款金额"))
        if amount is None:
            return
        acc[req.id] = acc.get(req.id, Decimal("0")) + amount

    def _refund_outflow_diffs(self, acc: dict[int, Decimal]) -> list[dict]:
        """同一退款单的账单累计出款 > 退款单金额 → 报差异（人工核对是否二次出款）。"""
        from backend.domain.identity.models import RefundRequest

        out: list[dict] = []
        for req_id, total in sorted(acc.items()):
            req = self.db.query(RefundRequest).filter(RefundRequest.id == req_id).first()
            if req is None:
                continue
            if total > req.amount:
                out.append(
                    {
                        "kind": "refund_overpaid_channel",
                        "ref": f"refund_request#{req_id}",
                        "message": (
                            f"同一退款单在微信账单累计出款 {total} 超过退款单金额 {req.amount}"
                            "——疑似换商户退款单号重提造成二次出款，请人工核对"
                        ),
                    }
                )
        return out

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
        # （早先靠"能否 parse 成 list"区分，结果把 skipped 的原因读丢了）。
        # WM12 压测加固：diffs 只落**样本**（按字节预算）+ 分类计数 + 截断标记——
        # 真实总数看 diff_count 列；不截断会让大差异日的 INSERT 超 TEXT 上限、整轮写不进去。
        sample, by_kind = self._sample_diffs(diffs)
        detail = json.dumps(
            {
                "diffs": sample,
                "diff_total": len(diffs),
                "diff_by_kind": by_kind,
                "truncated": len(sample) < len(diffs),
                "note": note,
            },
            ensure_ascii=False,
        )
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
    def _sample_diffs(diffs: list[dict]) -> tuple[list[dict], dict[str, int]]:
        """按字节预算取差异样本 + 全量分类计数（截断在 `_record` 里显式标记）。"""
        by_kind: dict[str, int] = {}
        sample: list[dict] = []
        used = 0
        for item in diffs:
            kind = item.get("kind") or "unknown"
            by_kind[kind] = by_kind.get(kind, 0) + 1
            size = len(json.dumps(item, ensure_ascii=False).encode())
            if used + size <= DETAIL_BYTE_BUDGET:
                sample.append(item)
                used += size
        return sample, by_kind

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
            "diff_total": payload.get("diff_total", len(detail)),
            "diff_by_kind": payload.get("diff_by_kind", {}),
            "truncated": bool(payload.get("truncated", False)),
            "note": payload.get("note", ""),
            "trigger": row.trigger,
            "finished_at": str(row.finished_at or ""),
        }
