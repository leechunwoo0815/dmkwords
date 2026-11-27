# tests/unit/test_b1_refund_withdrawal_concurrency_20261127.py — B1-1/B1-2 并发判据（第一百八十八批）
"""双审查 P1-1（退款并发重复出款）与 P1-2（退会缺 Child 锁 + 活跃唯一约束）的**真实双会话**判据。

统一修法：全局锁序 `Child → Order → RefundRequest → Withdrawal → Deposit`
（`backend/domain/identity/lock_order.py`）+ 活跃退会条件唯一约束（生成列 + 唯一索引）。

本文件四组用例，各自**去修复即红**（红在哪写在每个用例的 docstring 里）：
  A. 同一订单两笔 `failed` 退款单并发重试 → 合计不超订单金额（B1-1，**钱**的判据）；
  B. 会员费退款申请 × 家长退会申请并发 → 恰一张活跃退会单（B1-2 应用层，判别信号=锁等待形状；
     库级防线见 D，它会让"只去掉锁"的变异结果相同，故 B 单列）；
  C. 聚合推进锁退会行（B1-2）；
  D. 绕过应用层直插第二张活跃退会单 → 唯一索引拦下（B1-2 数据库层）；
  E. 索引真实性 + 生成列表达式与模型常量**防漂移**（照活动报名 P2-9 的规矩）。

为什么不用 `time.sleep` 编排：见 `tests/unit/helpers.lock_wait_query` 的说明——sleep 是猜时序，
`lock_wait_query` 把"对方真的在等锁"变成可判定事实。
"""

from __future__ import annotations

import re
import threading

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError

from backend.common.exceptions import ConflictError, ValidationError
from backend.database import SessionLocal, get_session
from backend.domain.identity.models import (
    ACTIVE_WITHDRAWAL_KEY_SQL,
    Child,
    RefundRequest,
    WithdrawalRequest,
)
from tests.unit.helpers import assert_locked_read, lock_wait_query
from tests.unit.test_wm10_concurrency import _family, _h

#: 假超管（execute 只在 `needs_check` 分支读 `role`，这里给全免得将来分支变化踩 AttributeError）
_ADMIN = type("A", (), {"id": 1, "display_name": "超管", "role": "superadmin"})()


def _run(fn, out: dict, key: str) -> None:
    """线程包装：结果/异常落 dict，绝不静默（同 test_b1_b5_concurrency 的写法）。"""
    try:
        out[key] = ("ok", fn())
    except Exception as exc:  # noqa: BLE001 — 并发用例必须如实记录失败
        out[key] = ("exc", exc)


def _custom_paid_order(client: TestClient, h: dict, child_id: int, amount: str = "500.00") -> dict:
    """造一张已收款的自定义订单（非会员费/活动费 → 退款申请不会联动建退会单）。"""
    o = client.post(
        "/api/admin/orders",
        json={
            "child_id": child_id,
            "order_type": "custom",
            "amount": amount,
            "remark": "B1-1 并发退款探针",
        },
        headers=h,
    )
    assert o.status_code == 200, o.text
    order = o.json()
    r = client.post(
        f"/api/admin/orders/{order['id']}/confirm-payment",
        json={"pay_method": "scan"},
        headers=h,
    )
    assert r.status_code == 200, r.text
    return order


def _failed_refund(
    client: TestClient, h: dict, mini: dict, child_id: int, order_id: int, why: str
) -> dict:
    """走**真实链路**造一笔 `failed` 退款单：家长申请 → 超管通过 → 执行判失败。"""
    rr = client.post(
        "/api/miniapp/refund-requests",
        json={"child_id": child_id, "order_id": order_id, "reason": why},
        headers=mini,
    )
    assert rr.status_code == 200, rr.text
    req = rr.json()
    r = client.post(
        f"/api/admin/refund-requests/{req['id']}/review",
        json={"approve": True, "remark": "同意"},
        headers=h,
    )
    assert r.status_code == 200, r.text
    r = client.post(
        f"/api/admin/refund-requests/{req['id']}/execute",
        json={"success": False, "remark": "首次执行失败（打款凭证缺失）"},
        headers=h,
    )
    assert r.status_code == 200, r.text
    return req


# ---------------------------------------------------------------- A. B1-1 钱


def test_b1_two_failed_refund_retries_cannot_both_pay(client: TestClient):
    """同一订单两笔 `failed` 退款单并发重试 → **DB 终态金额合计不超订单金额**。

    这是 B1-1 的真实入口：`failed` 刻意**不计入**累计闸（否则合法重试会被拦死），
    所以两笔失败单互为盲区。修复前：两笔都过闸、都落 `refunded` → 订单 500 出款 1000。

    编排（可判定，不猜时序）：holder 会话持 **Child 行锁** 并把自己那笔改成 `processing`
    但**不提交** —— 这正是"另一笔已过闸、已翻在途、还没提交"的真实窗口。修复后第二笔必须
    等在这把 Child 行锁上（锁序第一级），释放后按**锁定读**看到在途的 500 → 累计超额 → 拒绝。
    """
    h = _h(client)
    _p, c, mini = _family(client, h, "13800002901", "退款串行孩")
    order = _custom_paid_order(client, h, c["id"])
    rr1 = _failed_refund(client, h, mini, c["id"], order["id"], "第一笔失败退款")
    rr2 = _failed_refund(client, h, mini, c["id"], order["id"], "第二笔失败退款")

    with get_session() as db:
        statuses = {
            r.id: r.status
            for r in db.query(RefundRequest).filter(RefundRequest.order_id == order["id"]).all()
        }
    assert statuses == {rr1["id"]: "failed", rr2["id"]: "failed"}, (
        f"前置条件不成立：两笔都应是 failed（重复出款的前提），实际 {statuses}"
    )

    holder = SessionLocal()
    out: dict = {}
    t1 = threading.Thread(target=lambda: None)
    try:
        # 在途那一笔：持 Child 行锁（修复后的串行化点）+ 状态翻在途但**不提交**
        holder.execute(text("SELECT id FROM children WHERE id = :i FOR UPDATE"), {"i": c["id"]})
        holder.execute(
            text("UPDATE refund_requests SET status = 'processing' WHERE id = :i"),
            {"i": rr2["id"]},
        )

        def retry_other() -> dict:
            db1 = SessionLocal()
            try:
                from backend.domain.identity.wm10_service import RefundService

                return RefundService(db1).execute(_ADMIN, rr1["id"], True, "并发重试")
            finally:
                db1.rollback()
                db1.close()

        t1 = threading.Thread(target=_run, args=(retry_other, out, "retry"), daemon=True)
        t1.start()
        blocked = lock_wait_query(timeout=10)
        assert_locked_read(blocked, "第二笔重试必须在 Child 行锁上等待（同一订单退款的串行化点）")

        holder.commit()  # 放行：在途那笔（processing）正式提交
        t1.join(timeout=25)
        assert not t1.is_alive(), "重试线程未在时限内结束（锁未释放？）"

        kind, payload = out.get("retry", ("missing", None))
        assert kind == "exc", f"在途那笔已占满订单金额，第二笔必须被累计闸拒绝；实际 {out}"
        assert isinstance(payload, ValidationError), f"应是累计闸 422，实际 {payload!r}"

        with get_session() as db:
            paid = db.execute(
                text(
                    "SELECT COALESCE(SUM(amount), 0) FROM refund_requests "
                    "WHERE order_id = :o AND is_deleted = 0 "
                    "AND status IN ('processing', 'refunded')"
                ),
                {"o": order["id"]},
            ).scalar()
            order_amount = db.execute(
                text("SELECT amount FROM orders WHERE id = :i"), {"i": order["id"]}
            ).scalar()
            first = db.query(RefundRequest).filter(RefundRequest.id == rr1["id"]).first()
        assert paid <= order_amount, (
            f"同一订单的已退+在途合计 {paid} 超过订单金额 {order_amount}——重复出款"
        )
        assert first.status == RefundRequest.STATUS_FAILED, (
            f"被拦下的那笔应保持 failed（可人工再处理），实际 {first.status}"
        )
    finally:
        holder.rollback()
        holder.close()
        t1.join(timeout=5)


# ---------------------------------------------------------------- B. B1-2 应用层串行化


def test_b1_refund_apply_waits_on_child_lock_before_creating_withdrawal(client: TestClient):
    """会员费退款申请（R-309 联动建退会单）必须先在 **Child 行锁**上排队。

    修复前：`apply` 只锁订单行，`已有进行中退会` 的判重是普通读 → 与家长侧
    `WithdrawalService.apply`（锁 Child）互相看不见对方的未提交行 → **两张活跃退会单**
    → 两张押金退款单 → 押金退两遍。

    判别信号是**锁等待形状**：修复后阻塞在 `SELECT … FOR UPDATE`（Child 行锁）。
    （只去掉锁的变异在本用例里最终也会 409 —— 那时是库级唯一索引兜底，见用例 D；
    所以"应用层串行化"这件事只能靠锁等待来判，不能靠最终结果。）
    """
    h = _h(client)
    _p, c, mini = _family(client, h, "13800002902", "退会并发孩")
    order = client.post(
        "/api/admin/orders",
        json={"child_id": c["id"], "order_type": "observation_fee"},
        headers=h,
    ).json()
    r = client.post(
        f"/api/admin/orders/{order['id']}/confirm-payment",
        json={"pay_method": "scan"},
        headers=h,
    )
    assert r.status_code == 200, r.text

    holder = SessionLocal()
    out: dict = {}
    t1 = threading.Thread(target=lambda: None)
    try:
        # 模拟家长侧 `WithdrawalService.apply` 进行中：持 Child 行锁 + 自己那张退会单未提交
        holder.execute(text("SELECT id FROM children WHERE id = :i FOR UPDATE"), {"i": c["id"]})
        holder.add(
            WithdrawalRequest(
                child_id=c["id"],
                source=WithdrawalRequest.SOURCE_NORMAL,
                reason="家长侧并发退会申请",
            )
        )
        holder.flush()  # 未提交：模拟"家长侧已建单但事务未结束"

        def refund_apply() -> object:
            db1 = SessionLocal()
            try:
                from backend.domain.identity.wm10_service import RefundService

                child = db1.query(Child).filter(Child.id == c["id"]).first()
                return RefundService(db1).apply(child, order["id"], "会员费退款（并发探针）")
            finally:
                db1.rollback()
                db1.close()

        t1 = threading.Thread(target=_run, args=(refund_apply, out, "apply"), daemon=True)
        t1.start()
        blocked = lock_wait_query(timeout=10)
        assert_locked_read(blocked, "退款申请必须先在 Child 行锁上排队（与家长侧退会申请互斥）")

        holder.commit()
        t1.join(timeout=25)
        assert not t1.is_alive(), "退款申请线程未在时限内结束"

        kind, payload = out.get("apply", ("missing", None))
        assert kind == "exc" and isinstance(payload, ConflictError), (
            f"对方已有进行中的退会流程 → 必须 409，实际 {out}"
        )

        with get_session() as db:
            active = (
                db.query(WithdrawalRequest)
                .filter(
                    WithdrawalRequest.child_id == c["id"],
                    WithdrawalRequest.status.in_(WithdrawalRequest.ACTIVE_STATUSES),
                    WithdrawalRequest.is_deleted == 0,
                )
                .count()
            )
            refunds = (
                db.query(RefundRequest)
                .filter(RefundRequest.order_id == order["id"], RefundRequest.is_deleted == 0)
                .count()
            )
        assert active == 1, f"同一孩子必须只有一张活跃退会单，实际 {active} 张"
        assert refunds == 0, "被拒的退款申请不得留下退款单"
    finally:
        holder.rollback()
        holder.close()
        t1.join(timeout=5)


# ---------------------------------------------------------------- C. B1-2 聚合推进


def test_b1_advance_withdrawal_locks_withdrawal_row(client: TestClient):
    """聚合推进（`advance_withdrawal`）必须**锁定读**退会行。

    修复前是普通读 + 盲写：两笔结算退款单（会员费/押金）几乎同时落终态时，两边都可能读到
    `open_cnt > 0`（对方未提交）→ 退会永久卡 `refunding`、孩子永久冻结；或两边都判 0 → 双写。
    判别信号：修复后阻塞在 `SELECT … FOR UPDATE`（退会行锁）。
    """
    h = _h(client)
    _p, c, mini = _family(client, h, "13800002903", "聚合推进孩")
    order = _custom_paid_order(client, h, c["id"], "300.00")

    with get_session() as db:
        w = WithdrawalRequest(
            child_id=c["id"], source=WithdrawalRequest.SOURCE_NORMAL, reason="聚合推进探针"
        )
        w.status = WithdrawalRequest.STATUS_REFUNDING
        db.add(w)
        db.flush()
        rr = RefundRequest(
            kind=RefundRequest.KIND_ORDER,
            order_id=order["id"],
            withdrawal_id=w.id,
            child_id=c["id"],
            amount="300.00",
            reason="结算单",
        )
        rr.status = RefundRequest.STATUS_REFUNDED
        db.add(rr)
        db.flush()
        db.commit()  # `get_session()` 只是裸 Session（不像 fixture 那样自动提交）——别漏
        wid, rid = w.id, rr.id

    holder = SessionLocal()
    out: dict = {}
    t1 = threading.Thread(target=lambda: None)
    try:
        holder.execute(
            text("SELECT id FROM withdrawal_requests WHERE id = :i FOR UPDATE"), {"i": wid}
        )

        def advance() -> None:
            db1 = SessionLocal()
            try:
                from backend.domain.identity.wm10_withdrawal_service import advance_withdrawal

                advance_withdrawal(db1, rid)
                db1.commit()
            finally:
                db1.rollback()
                db1.close()

        t1 = threading.Thread(target=_run, args=(advance, out, "adv"), daemon=True)
        t1.start()
        blocked = lock_wait_query(timeout=10)
        assert_locked_read(
            blocked, "聚合推进必须锁定读退会行（否则并发双写 completed/卡 refunding）"
        )

        holder.commit()
        t1.join(timeout=25)
        assert not t1.is_alive(), "聚合推进线程未在时限内结束"
        assert out.get("adv", ("missing", None))[0] == "ok", out

        with get_session() as db:
            w2 = db.query(WithdrawalRequest).filter(WithdrawalRequest.id == wid).first()
            child = db.query(Child).filter(Child.id == c["id"]).first()
        assert w2.status == WithdrawalRequest.STATUS_COMPLETED, w2.status
        assert not child.operation_locked, "退会完成后孩子必须解锁"
    finally:
        holder.rollback()
        holder.close()
        t1.join(timeout=5)


# ---------------------------------------------------------------- D/E. 库级防线


def test_b1_db_rejects_second_active_withdrawal(client: TestClient):
    """**绕过应用层**直插第二张活跃退会单 → 库级唯一索引必须拦下（1062）。

    RED（删掉迁移 `e7c4d1a9b3f6`）：直插成功 → 断言失败。
    """
    _h_ = _h(client)
    _p, c, _mini = _family(client, _h_, "13800002904", "唯一约束探针孩")
    with get_session() as db:
        first = WithdrawalRequest(child_id=c["id"], source="normal", reason="第一张活跃单")
        db.add(first)
        db.flush()
        dup = WithdrawalRequest(child_id=c["id"], source="refund_linked", reason="第二张活跃单")
        db.add(dup)
        with pytest.raises(IntegrityError) as ei:
            db.flush()
        assert "uq_withdrawal_active_child" in str(ei.value.orig), f"应撞唯一索引：{ei.value.orig}"
        db.rollback()

    # 终态不占位：同一孩子退会结束后可以重新申请（新单）
    with get_session() as db:
        w = WithdrawalRequest(child_id=c["id"], source="normal", reason="已结束的单")
        db.add(w)
        db.flush()
        assert w.active_uniq_key == c["id"], f"活跃单必须占位：{w.active_uniq_key!r}"
        w.status = WithdrawalRequest.STATUS_CANCELLED
        db.flush()
        assert w.active_uniq_key is None, f"非活跃单不得占位：{w.active_uniq_key!r}"
        again = WithdrawalRequest(child_id=c["id"], source="normal", reason="重新申请")
        db.add(again)
        db.flush()  # 不抛 = 不占位
        assert again.id


def test_b1_unique_index_is_real_and_expression_matches_model(client: TestClient):
    """索引必须是**真唯一索引**且落在生成列上；表达式里的状态集合与模型常量一致（防漂移）。

    RED（把索引改回非唯一 / 改了 `ACTIVE_STATUSES` 却没改 DDL）：分别两条断言红。
    """
    with get_session() as db:
        rows = db.execute(
            text(
                "SELECT COLUMN_NAME, NON_UNIQUE FROM information_schema.STATISTICS "
                "WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = 'withdrawal_requests' "
                "AND INDEX_NAME = 'uq_withdrawal_active_child'"
            )
        ).fetchall()
        assert rows, "uq_withdrawal_active_child 索引不存在"
        assert len(rows) == 1 and rows[0][0] == "active_uniq_key", (
            f"唯一约束必须落在生成列 active_uniq_key 上，实际 {rows}"
        )
        assert rows[0][1] == 0, "uq_withdrawal_active_child 必须是**真唯一索引**（NON_UNIQUE=0）"
        expr = db.execute(
            text(
                "SELECT GENERATION_EXPRESSION FROM information_schema.COLUMNS "
                "WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = 'withdrawal_requests' "
                "AND COLUMN_NAME = 'active_uniq_key'"
            )
        ).scalar()
        assert expr, "生成列 active_uniq_key 不存在"

    # MySQL 回显把字面量写成 `_utf8mb4\'applying\'`（反斜杠转义引号），先去掉反斜杠再抽状态
    ddl_statuses = set(re.findall(r"'([a-z_]+)'", str(expr).replace("\\", "")))
    assert ddl_statuses == set(WithdrawalRequest.ACTIVE_STATUSES), (
        f"生成列里的状态集合 {sorted(ddl_statuses)} 与 ACTIVE_STATUSES "
        f"{sorted(WithdrawalRequest.ACTIVE_STATUSES)} 不一致（改一处忘另一处）"
    )

    def _norm(sql: str) -> str:
        """归一：MySQL 回显会加反引号、`_utf8mb4` 前缀、`\\'` 转义，并给每个操作数补一层括号。"""
        s = str(sql).lower().replace("`", "").replace("_utf8mb4", "").replace("\\", "")
        return re.sub(r"[\s()]+", "", s)

    assert _norm(ACTIVE_WITHDRAWAL_KEY_SQL) == _norm(expr), (
        f"模型里的 ACTIVE_WITHDRAWAL_KEY_SQL 与库内实际表达式不一致：\n"
        f"  模型 {_norm(ACTIVE_WITHDRAWAL_KEY_SQL)}\n  库内 {_norm(expr)}"
    )
