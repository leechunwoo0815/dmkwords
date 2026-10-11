# tests/unit/test_a3b5_reconcile_refund_sql_20261011.py — A3 第五批：账单退款行批量化（2026-10-11）
"""K1 核对现场实测挖出的第四处 N+1：`_diff_bill` 的退款行**逐行 3 条 SQL**。

实测（演练库 8 万行账单含 1 万退款行）：修复前 **30,001 条 SQL / 26.8s**；
本文件断言的是"退款行数增长不再让 SQL 条数增长"（强复现：回退源码即红）。

另：`refund_id_of` 是单号格式的唯一出处（`RF{id}-{attempts}-1`）。
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal

from sqlalchemy import event

from backend.domain.identity.models import RefundRequest
from backend.domain.identity.payment_reconcile_service import PaymentReconcileService

HEADER = (
    "交易时间,公众账号ID,商户号,微信订单号,商户订单号,交易状态,订单金额,"
    "商户退款单号,退款金额,退款状态"
)


def _count_refund_sql(fn) -> int:
    """只数 `FROM refund_requests` 的语句（本次改造的靶点；其它查询不参与判红）。"""
    from backend.database import engine

    stmts: list[str] = []

    def rec(conn, cursor, statement, parameters, context, executemany):  # noqa: ARG001
        if "refund_requests" in statement:
            stmts.append(statement)

    event.listen(engine, "before_cursor_execute", rec)
    try:
        fn()
    finally:
        event.remove(engine, "before_cursor_execute", rec)
    return len(stmts)


def _make_refunds(db, n: int, status: str = RefundRequest.STATUS_REFUNDED) -> list[RefundRequest]:
    """直接造 n 张退款单（真实单号格式 `RF{id}-1-1`，与 refund_online 生成规则同源）。"""
    reqs = []
    for _ in range(n):
        req = RefundRequest(
            kind=RefundRequest.KIND_ORDER,
            order_id=1,
            child_id=1,
            amount=Decimal("500.00"),
            reason="A3b5 造单（账单比对用）",
            status=status,
        )
        db.add(req)
        db.flush()
        req.out_refund_no = f"RF{req.id}-1-1"
        reqs.append(req)
    db.commit()
    return reqs


def _bill_with_refund_rows(reqs: list[RefundRequest]) -> str:
    rows = [
        f"2026-10-09 10:00:00,wxapp,1900,wx_txn_{i},DMKUNKNOWN{i:04d},REFUND,500.00,"
        f"{r.out_refund_no},500.00,SUCCESS"
        for i, r in enumerate(reqs)
    ]
    return "\n".join([HEADER, *rows, f"总交易单数,{len(rows)}"])


def test_bill_refund_rows_sql_is_constant(client, db):
    """退款行的 SQL 条数**与退款行数无关**（修复前 6 行 ≈19 条、12 行 ≈37 条，线性增长）。"""
    few = _make_refunds(db, 6)
    many = _make_refunds(db, 12)
    svc = PaymentReconcileService(db)

    sql_few = _count_refund_sql(
        lambda: svc._diff_bill(date(2026, 10, 9), _bill_with_refund_rows(few))
    )
    sql_many = _count_refund_sql(
        lambda: svc._diff_bill(date(2026, 10, 9), _bill_with_refund_rows(many))
    )

    assert sql_few == sql_many, (
        f"退款行数翻倍不该增加 SQL：6 行 {sql_few} 条 vs 12 行 {sql_many} 条"
    )
    assert sql_many <= 3, f"预载应只查一次退款单表，实测 {sql_many} 条"


def test_bill_refund_rows_semantics_unchanged(client, db):
    """批量预载不改判定：金额相符且本地已退款 → 无差异；本地还在执行中 → 报状态不符。"""
    ok = _make_refunds(db, 1, status=RefundRequest.STATUS_REFUNDED)[0]
    processing = _make_refunds(db, 1, status=RefundRequest.STATUS_PROCESSING)[0]
    svc = PaymentReconcileService(db)

    _checked, diffs_ok = svc._diff_bill(date(2026, 10, 9), _bill_with_refund_rows([ok]))
    assert diffs_ok == [], diffs_ok

    _checked2, diffs_bad = svc._diff_bill(date(2026, 10, 9), _bill_with_refund_rows([processing]))
    assert [d["kind"] for d in diffs_bad] == ["refund_state_mismatch"], diffs_bad


def test_bill_unknown_refund_no_still_reported(client, db):
    """反解不到的退款单号仍走 `bill_refund_only`（批量预载不得把它静默吞掉）。"""
    _make_refunds(db, 1)
    csv_text = "\n".join(
        [
            HEADER,
            "2026-10-09 10:00:00,wxapp,1900,wx_txn_9,DMKUNKNOWN0009,REFUND,500.00,RF999999-1-1,500.00,SUCCESS",
            "总交易单数,1",
        ]
    )
    _checked, diffs = PaymentReconcileService(db)._diff_bill(date(2026, 10, 9), csv_text)
    assert [d["kind"] for d in diffs] == ["bill_refund_only"], diffs


def test_refund_id_of_is_single_source_of_format():
    """单号格式的唯一出处：`RF{id}-{attempts}-1` 可解；演练种子那种 `RF-1-1-1` 不可解。"""
    from backend.domain.identity.refund_online import refund_id_of

    assert refund_id_of("RF123-2-1") == 123
    assert refund_id_of("RF1-1-1") == 1
    assert refund_id_of("RF-1-1-1") is None  # 演练种子格式：RF 后紧跟 `-` → 不是 id
    assert refund_id_of("") is None
    assert refund_id_of(None) is None
    assert refund_id_of("WX123-1-1") is None
