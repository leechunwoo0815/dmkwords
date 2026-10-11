# backend/domain/identity/reconcile_bill.py — 微信账单的**退款侧**比对（从 payment_reconcile_service 抽出）
"""对账单逐行比对里"退款"相关的纯逻辑：反解退款单 / 预载 / 金额与状态比对 / 累计出款。

为什么单开一个模块：`payment_reconcile_service.py` 已到 god-file 上限（≤800 行），
而 A3 第五批（2026-10-11）给退款行加"批量预载"又要增行。抽出的这组函数不持有状态、
只吃 (db, row, ...)，与对账服务同域（identity），是最自然的切法。

口径不变（逐行差异的 kind 与文案保持原样）：批量预载只是把"逐行查库"换成"先查一次"。
"""

from __future__ import annotations

from decimal import Decimal, InvalidOperation

from sqlalchemy.orm import Session

from backend.domain.identity.models import RefundRequest


def resolve_refund(
    db: Session, out_refund_no: str, refunds: dict[str, RefundRequest] | None = None
) -> RefundRequest | None:
    """取退款单：有预载表就读表，否则单查（直接调用单测的路径不受批量改造影响）。"""
    if refunds is not None:
        return refunds.get(out_refund_no)
    from backend.domain.identity import refund_online

    return refund_online.find_by_out_refund_no(db, out_refund_no)


def preload_refunds(db: Session, rows: list[dict]) -> dict[str, RefundRequest]:
    """一次性反解账单里出现的全部商户退款单号（A3 第五批）。

    返回 `{out_refund_no: RefundRequest}`；解析不出 id 或库里没有的号**不进表**——
    行内照旧走 `bill_refund_only` 差异分支（口径不变）。

    为什么必须批量：原先逐行 3 条 SQL（反解 / 累计出款 / 累计超限各查一次），
    8 万行账单（含 1 万退款行）实测 **30,001 条 SQL / 26.8s**。
    """
    from backend.domain.identity import refund_online

    wanted: dict[int, list[str]] = {}
    for row in rows:
        no = (row.get("商户退款单号") or "").strip()
        rid = refund_online.refund_id_of(no)
        if rid is not None:
            wanted.setdefault(rid, []).append(no)
    if not wanted:
        return {}
    found = (
        db.query(RefundRequest)
        .filter(RefundRequest.id.in_(list(wanted)), RefundRequest.is_deleted == 0)
        .all()
    )
    by_id = {r.id: r for r in found}
    out: dict[str, RefundRequest] = {}
    for rid, nos in wanted.items():
        req = by_id.get(rid)
        if req is not None:
            for no in nos:
                out[no] = req
    return out


def amount_eq(bill_amount: str | None, local: Decimal) -> bool:
    if bill_amount is None or str(bill_amount).strip() == "":
        return False
    try:
        return Decimal(str(bill_amount).replace(",", "").strip()) == Decimal(local).quantize(
            Decimal("0.01")
        )
    except (InvalidOperation, ValueError):
        return False


def parse_amount(bill_amount: str | None) -> Decimal | None:
    """账单金额列 → Decimal（不可解析返回 None，由调用方决定报警还是跳过）。"""
    if bill_amount is None or str(bill_amount).strip() == "":
        return None
    try:
        return Decimal(str(bill_amount).replace(",", "").strip()).quantize(Decimal("0.01"))
    except (InvalidOperation, ValueError):
        return None


def diff_refund_row(
    db: Session,
    row: dict,
    out_refund_no: str,
    refunds: dict[str, RefundRequest] | None = None,
) -> list[dict]:
    req = resolve_refund(db, out_refund_no, refunds)
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
    elif not amount_eq(bill_amount, req.amount):
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


def accumulate_refund_outflow(
    db: Session,
    acc: dict[int, Decimal],
    out_refund_no: str,
    row: dict,
    refunds: dict[str, RefundRequest] | None = None,
) -> None:
    """按**退款单**（不是退款单号）累计账单里的出款金额。

    P0-C 纵深（2026-10-09 专家复核 + 亲验）：逐号对号时，同一退款单换号重提的两笔
    各自都能通过校验（两号反解同一个 req，金额与状态都对得上）——只有按退款单累计
    才看得出"一笔退款出了两次钱"。
    """
    req = resolve_refund(db, out_refund_no, refunds)
    if req is None:
        return  # 反解不到的单已由 diff_refund_row 报 bill_refund_only
    amount = parse_amount(row.get("退款金额"))
    if amount is None:
        return
    acc[req.id] = acc.get(req.id, Decimal("0")) + amount


def refund_outflow_diffs(
    db: Session, acc: dict[int, Decimal], refunds: dict[str, RefundRequest] | None = None
) -> list[dict]:
    """同一退款单的账单累计出款 > 退款单金额 → 报差异（人工核对是否二次出款）。"""
    out: list[dict] = []
    by_id = {r.id: r for r in refunds.values()} if refunds is not None else None
    for req_id, total in sorted(acc.items()):
        if by_id is not None:
            req = by_id.get(req_id)
        else:
            req = db.query(RefundRequest).filter(RefundRequest.id == req_id).first()
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
