# tests/unit/test_a3b3_ops_20261011.py — A3 第三批：全量明细导出 + 任务失败兜底（2026-10-11）
"""两条"运维可追溯"能力的回归：

① **对账差异全量明细 CSV**（专家裁决④配套）：`detail` 落库只存 32KB 样本 + 分类计数
   （TEXT 64KB 上限，10 万级实测 176KB 写不进去）→ 全量明细靠 `/payments/reconciliations/export.csv`
   **重算本地审计**并全量导出；本用例断言"导出条数 ≥ 落库样本条数"且表头/权限正确。
② **任务失败文件兜底**（P2-15）：同一 DB 故障会让"失败日志"也写不进库（看板无记录）→
   现在额外落一份可 grep 的标记文件（`TASK_FAILURE_LOG` 可覆盖路径）。
"""

from __future__ import annotations

import csv
import io
import os

from sqlalchemy import text

from tests.unit.test_wm10_concurrency import _h

# ---------- ① 全量明细导出 ----------


def test_reconcile_export_csv_returns_all_diffs(client, db):
    """导出 CSV：表头齐全、条数 ≥ 落库样本条数（落库有 32KB 截断，导出是全量）。"""
    h = _h(client)
    # 造一条本地差异（已支付订单挂着 refunded 的退款单 → paid_order_with_refunded_request）
    from tests.unit.test_wm12_refund_reconcile import (
        _apply_refund,
        _approve,
        _execute,
        _online_paid_order,
    )

    _p, child, mh, order = _online_paid_order(client, db, phone="13981037501")
    applied = _apply_refund(client, mh, order, child["id"])
    _approve(client, h, applied["id"])
    _execute(client, h, applied["id"], remark="原路退")
    # 人为制造"已退款订单仍挂着活跃退款单"的差异形状：把订单退回 paid
    db.execute(text("UPDATE orders SET status='paid' WHERE id=:i"), {"i": order.id})
    db.commit()

    run = client.post("/api/admin/payments/reconcile", json={}, headers=h)
    assert run.status_code == 200, run.text
    local = next(r for r in run.json()["reports"] if r["source"] == "local")
    stored = len(local["detail"])

    resp = client.get("/api/admin/payments/reconciliations/export.csv", headers=h)
    assert resp.status_code == 200, resp.text
    assert resp.headers["content-type"].startswith("text/csv")
    assert "attachment" in resp.headers.get("content-disposition", "")
    rows = list(csv.reader(io.StringIO(resp.text)))
    assert rows[0] == ["bill_date", "kind", "ref", "message"]
    assert len(rows) - 1 >= stored, f"导出 {len(rows) - 1} 条 < 落库样本 {stored} 条"


def test_reconcile_export_requires_read_permission(client, db):
    """导出是读语义（dashboard.view）：专员可导、未登录 401。"""
    staff = client.post(
        "/api/admin/login", json={"username": "staff01", "password": "dmkwords123"}
    ).json()
    r = client.get(
        "/api/admin/payments/reconciliations/export.csv",
        headers={"Authorization": f"Bearer {staff['token']}"},
    )
    assert r.status_code == 200, r.text
    assert client.get("/api/admin/payments/reconciliations/export.csv").status_code == 401


# ---------- ② 任务失败文件兜底 ----------


def test_task_failure_marker_written_when_db_log_fails(client, db, monkeypatch, tmp_path):
    """DB 写不进失败日志时，必须落文件标记（否则看板+库都无痕）。"""
    from backend.tasks import registry

    marker = tmp_path / "task-failures.log"
    monkeypatch.setenv("TASK_FAILURE_LOG", str(marker))

    def boom(self, **kwargs):  # noqa: ARG001
        raise RuntimeError("A3 模拟任务爆炸")

    # 让任务本体炸 → 再让"写失败日志"也炸（模拟同一 DB 故障）
    spec = registry.TASKS["media_census"]
    monkeypatch.setattr(registry.TaskSpec, "run", boom, raising=False)
    monkeypatch.setattr(spec, "run", boom)

    class _DeadSession:
        def add(self, *a, **k):
            raise RuntimeError("DB 故障：写不进")

        def commit(self):
            raise RuntimeError("DB 故障：写不进")

        def rollback(self):
            pass

        def close(self):
            pass

    import backend.database as database

    monkeypatch.setattr(database, "get_session", lambda: _DeadSession())
    out = registry.run_task("media_census")
    assert out["status"] == "failed"
    assert marker.is_file(), "DB 写不进时必须有文件兜底"
    assert "media_census" in marker.read_text(encoding="utf-8")
    assert os.environ.get("TASK_FAILURE_LOG") == str(marker)
