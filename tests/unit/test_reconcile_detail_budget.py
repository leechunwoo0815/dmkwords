# tests/unit/test_reconcile_detail_budget.py — 大差异日的 detail 落库预算（十万级压测抓到的缺陷）
"""2026-10-08 十万级压测实录（`gate-runs/2026-10-08/reconcile-100k.log`）：

演练库造 10 万订单 + 8 千退款单后跑真实对账服务 → `detail` 的 JSON 176KB > MySQL TEXT 64KB →
`DataError 1406` → **整轮对账一笔都写不进去**（越是系统性事故、越没有留痕）。

修法（本测试固化）：`detail` 只落**样本**（按字节预算）+ **分类计数** + **截断标记**；
真实总数仍在 `diff_count` 列，分类计数是全量统计（不受截断影响）。
"""

from __future__ import annotations

from datetime import date

from backend.domain.identity.payment_models import PaymentReconciliation
from backend.domain.identity.payment_reconcile_service import (
    DETAIL_BYTE_BUDGET,
    PaymentReconcileService,
)


def _db():
    from backend.database import get_session

    return get_session()


def _diffs(n: int) -> list[dict]:
    return [
        {
            "kind": "status_conflict",
            "ref": f"DMKPERF{i:014d}",
            "message": f"订单仍为已支付，但已有退款单终态=已退款（订单 id={i}）",
        }
        for i in range(n)
    ]


def _record(diffs: list[dict]) -> dict:
    with _db() as db:
        report = PaymentReconcileService(db)._record(
            source="local",
            day=date.today(),
            checked=len(diffs),
            diffs=diffs,
            trigger=PaymentReconciliation.TRIGGER_MANUAL,
        )
        row = (
            db.query(PaymentReconciliation).filter(PaymentReconciliation.id == report["id"]).first()
        )
        report["_raw_bytes"] = len((row.detail or "").encode("utf-8"))
        report["_db_diff_count"] = row.diff_count
    return report


def test_large_diff_set_is_sampled_and_fully_counted():
    report = _record(_diffs(1500))
    # ① 落库体积在 TEXT 上限之内（压测炸的就是这里：176KB > 64KB）
    assert report["_raw_bytes"] <= 64 * 1024
    assert report["_raw_bytes"] <= DETAIL_BYTE_BUDGET + 4096  # 预算 + 包装字段余量
    # ② 真实总数与分类计数不丢（截断只影响样本）
    assert report["_db_diff_count"] == 1500
    assert report["diff_total"] == 1500
    assert report["diff_by_kind"] == {"status_conflict": 1500}
    # ③ 截断被显式标记，样本非空且小于全量
    assert report["truncated"] is True
    assert 0 < len(report["detail"]) < 1500


def test_small_diff_set_is_not_truncated():
    report = _record(_diffs(3))
    assert report["truncated"] is False
    assert len(report["detail"]) == 3
    assert report["diff_total"] == 3
