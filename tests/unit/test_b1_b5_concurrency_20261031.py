# tests/unit/test_b1_b5_concurrency_20261031.py — B1–B5 并发线索复现判据（第一百五十一批）
"""docs/27 §二 待复核线索的亲验判据（B1/B3 修复判据 + B2 环实证）。

- **B1**：通知写入撞 InnoDB 死锁（1213）时 `send_event` 不得吞——1213 已回滚**整个事务**，
  吞掉后调用方 `commit()` 静默成功，此前 flush 过的业务写入全部丢失（「订单写成功」的假象）。
  构造：T1 持业务行锁；T2 占住通知去重键后等待 T1 的业务行锁；T1 的通知 INSERT 成为
  死锁闭环语句。InnoDB victim 由 undo 行数差控制（T2 先改 20 行 > T1 的 1 行）。
- **B3**：`activity_auto_finish` 扫描（MVCC 普通读）与写入之间，`cancel_activity`（锁活动行）
  提交 cancelled → 盲写把 cancelled 覆盖成 finished（丢失更新，无需死锁，完全确定性）。
- **B2**：cancel（活动→报名→订单）× confirm（订单→报名）锁序环的真实死锁实证——
  断言「恰一方收到 1213」（InnoDB 自动解环，victim 不定，故只断言恰一方）。
  修不修的裁定见 docs/09 第一百五十一批 / docs/27 §五（三条失败路径全部可见，不统一锁序）。
- **B4/B5 无判据**（结论条目，读码定论）：B4 误解锁窗口被 apply 的 dup 检查（覆盖双方孩子的
  任意 pending 转让）+ reqA 状态翻转与 _unlock_both 同事务 + apply 先锁 Child 后查 三重关闭
  （触发条件不存在）；B5 唯一键缺 is_deleted 但全仓无软删写口。证据链落 docs/09 与 docs/27。
"""

from __future__ import annotations

import threading

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text
from sqlalchemy.dialects.mysql import insert as mysql_insert
from sqlalchemy.exc import OperationalError

from backend.common.events import DomainEvent
from backend.common.notification_models import Notification
from backend.common.notifications import NotificationService
from backend.database import SessionLocal
from tests.unit.helpers import lock_wait_query
from tests.unit.test_wm9_activity import _h, _mk_activity, _mk_child
from tests.unit.test_wm10_concurrency import _family


def _run(fn, out: dict, key: str) -> None:
    """线程包装：结果/异常落 dict，绝不静默。"""
    try:
        out[key] = ("ok", fn())
    except Exception as exc:  # noqa: BLE001 — 并发用例必须如实记录失败
        out[key] = ("exc", exc)


def _is_deadlock(exc: object) -> bool:
    orig = getattr(exc, "orig", None)
    args = getattr(orig, "args", None)
    return bool(args) and args[0] == 1213


# ---------- B1：通知死锁必须可见（re-raise），不得吞 ----------


class _B1Event(DomainEvent):
    event_type = "b1.test"


def test_b1_deadlock_in_notify_handler_is_raised_not_swallowed(client: TestClient):
    """B1：send_event 撞 1213 → 必须抛出（业务可见失败）；吞掉 = commit 静默丢业务写入（RED）。"""
    h = _h(client)
    parent, child, _mini = _family(client, h, "13800151001", name="B1孩")
    _p2, child2, _mini2 = _family(client, h, "13800151002", name="B1孩二")

    t1_ready = threading.Event()
    t2_holds = threading.Event()
    out: dict = {}
    scene = "b1.ring"

    def t2_body() -> None:
        db2 = SessionLocal()
        try:
            # victim 体积：先改 20 行（undo 计数远大于 T1），InnoDB 选 T1 回滚
            for _ in range(20):
                db2.execute(
                    text("UPDATE children SET name = CONCAT(name, '.') WHERE id = :i"),
                    {"i": child2["id"]},
                )
            # 占住通知去重键（未提交）——T1 的 send 将在这把锁上等待并成为死锁闭环语句
            db2.execute(
                mysql_insert(Notification).values(
                    parent_id=parent["id"],
                    scene=scene,
                    category="资金",
                    title="T2 占键",
                    content="",
                    ref_type="",
                    ref_id="",
                    dedup_key="1",
                    wechat_status=Notification.WECHAT_NONE,
                    wechat_error="",
                )
            )
            t2_holds.set()
            # 请求 T1 持有的业务行锁 → 环闭合，InnoDB 选 T1（undo 少）回滚
            db2.execute(
                text("UPDATE children SET name = CONCAT(name, '!') WHERE id = :i"),
                {"i": child["id"]},
            )
            db2.commit()
        except Exception as exc:  # noqa: BLE001
            out["t2"] = ("exc", exc)
            db2.rollback()
        else:
            out["t2"] = ("ok", None)
        finally:
            db2.close()

    db1 = SessionLocal()
    t2 = threading.Thread(target=_run, args=(t2_body, out, "t2"), daemon=True)
    try:
        # 业务写入（未提交，持 C1 行锁）——死锁后它必须**不落库**且调用方看得见失败
        db1.execute(text("UPDATE children SET name = 'b1-busy' WHERE id = :i"), {"i": child["id"]})
        t1_ready.set()
        t2.start()
        assert t2_holds.wait(timeout=10), "T2 未在时限内占住去重键"
        # 可判定等待：T2 的业务行锁请求已进入 LOCK WAIT（非猜时序）
        lock_wait_query(timeout=8)

        def business_step() -> None:
            NotificationService(db1).send_event(
                _B1Event(),
                parent_id=parent["id"],
                scene=scene,
                title="付款成功",
                content="订单 A 已收款",
                category="资金",
                openid=None,
            )
            db1.commit()  # 吞掉异常的调用方会「照常提交成功」

        with pytest.raises(OperationalError) as ei:
            business_step()
        assert _is_deadlock(ei.value), f"应是 InnoDB 死锁 1213，实际 {ei.value!r}"
        db1.rollback()  # 死锁后事务已被服务端回滚；session 复位并保住一致性

        fresh = SessionLocal()
        try:
            name = fresh.execute(
                text("SELECT name FROM children WHERE id = :i"), {"i": child["id"]}
            ).scalar()
            assert name != "b1-busy", "回滚后业务写入不应存在"
            rows = fresh.execute(
                text("SELECT title FROM notifications WHERE scene = :s"), {"s": scene}
            ).fetchall()
            assert all(r[0] != "付款成功" for r in rows), "T1 的通知行不应落库"
        finally:
            fresh.close()
    finally:
        db1.close()  # 未提交即关闭 = 回滚，释放 T2 可能还在等的锁
        t2.join(timeout=20)
        assert not t2.is_alive(), "T2 未在时限内结束"
        assert out.get("t2", ("missing", None))[0] in ("ok", "exc"), out


# ---------- B3：auto_finish 不得把 cancelled 覆盖成 finished ----------


def test_b3_auto_finish_never_overwrites_cancelled(client: TestClient):
    """B3：扫描与写入之间活动被取消 → 终态必须是 cancelled（盲写覆盖 = RED）。"""
    from backend.domain.activity.models import Activity
    from backend.domain.activity.service import ActivityService
    from backend.domain.admin.models import AdminUser

    h = _h(client)
    act = _mk_activity(client, h, quota=5, fee=0, title="B3活动")
    # API 校验拒绝过去时间（"开始时间必须在未来"）——先用真实链路建活动，
    # 再直接改库把开始时间挪到 auto_finish 的扫描窗口（>24h 前），不影响被测并发窗口。
    from datetime import datetime, timedelta

    past = datetime.now() - timedelta(hours=30)
    with SessionLocal() as db_seed:
        db_seed.execute(
            text("UPDATE activities SET start_at = :s WHERE id = :i"),
            {"s": past, "i": act["id"]},
        )
        db_seed.commit()

    db_lock = SessionLocal()
    try:
        locked = (
            db_lock.query(Activity)
            .filter(Activity.id == act["id"], Activity.is_deleted == 0)
            .with_for_update()
            .populate_existing()
            .first()
        )
        assert locked is not None and locked.status == Activity.STATUS_PUBLISHED

        out: dict = {}

        def t1_body() -> int:
            db1 = SessionLocal()
            try:
                return ActivityService(db1).activity_auto_finish()
            finally:
                db1.close()

        t1 = threading.Thread(target=_run, args=(t1_body, out, "t1"), daemon=True)
        t1.start()
        # 可判定等待：T1 已在活动行锁上等待（修复后=重锁复核处；修复前=commit 的 UPDATE 处）
        stmt = lock_wait_query(timeout=10)
        assert stmt, "T1 未在时限内进入锁等待（编排失效）"

        # T2（同持锁 session）：真实取消流程提交 cancelled
        admin = db_lock.query(AdminUser).filter(AdminUser.username == "admin").first()
        assert admin is not None
        ActivityService(db_lock).cancel_activity(admin, act["id"])

        t1.join(timeout=25)
        assert not t1.is_alive(), "T1 未在时限内结束（锁未释放？）"

        fresh = SessionLocal()
        try:
            final = fresh.query(Activity.status).filter(Activity.id == act["id"]).scalar()
            assert final == Activity.STATUS_CANCELLED, (
                f"活动已被取消，auto_finish 不得覆盖成 finished，实际 {final}"
            )
        finally:
            fresh.close()
    finally:
        db_lock.rollback()
        db_lock.close()


# ---------- B2：cancel × confirm 锁序环死锁实证（结论条目，不改码） ----------


def test_b2_cancel_and_confirm_lock_ring_deadlocks(client: TestClient):
    """B2：cancel（活动→报名→订单）× confirm（订单→报名）并发 → InnoDB 死锁真实发生。

    断言「恰一方 1213」（victim 不定）。三条失败路径的可见性结论见 docs/09 第一百五十一批：
    cancel 方 500（馆员可见）、confirm 方 500（可见）、回调路径 handle_notify 兜底
    rollback + anomaly 待办 + 200「已记录待人工处理」（有留痕，非静默）。
    """
    from backend.domain.activity.service import ActivityService
    from backend.domain.admin.models import AdminUser

    h = _h(client)
    c, mini = _mk_child(client, h, "13800154001", "B2孩")
    act = _mk_activity(client, h, quota=5, fee=50, title="B2活动")
    resp = client.post(
        f"/api/miniapp/activities/{act['id']}/enroll", json={"child_id": c["id"]}, headers=mini
    )
    assert resp.status_code == 200, resp.text
    e = resp.json()
    enrollment_id = e["enrollment"]["id"] if isinstance(e.get("enrollment"), dict) else e["id"]
    order_id = e["order_id"]

    db2 = SessionLocal()
    out: dict = {}
    t1 = threading.Thread(target=lambda: None)  # 占位，finally 里 join 安全
    try:
        # T2（confirm 链第一步）：锁订单行（真实 confirm_payment 的入口守卫同款）
        locked_order = db2.execute(
            text("SELECT id FROM orders WHERE id = :i FOR UPDATE"), {"i": order_id}
        ).scalar()
        assert locked_order == order_id

        def t1_body() -> dict:
            db1 = SessionLocal()
            try:
                admin = db1.query(AdminUser).filter(AdminUser.username == "admin").first()
                return ActivityService(db1).cancel_activity(admin, act["id"])
            finally:
                db1.rollback()  # 受害者：复位死锁事务；胜者：cancel_activity 已内部 commit，回滚为空操作
                db1.close()

        t1 = threading.Thread(target=_run, args=(t1_body, out, "t1"), daemon=True)
        t1.start()
        # 可判定等待：T1 已锁报名行、其订单作废 UPDATE 正等 T2 的订单锁
        assert lock_wait_query(timeout=10), "T1 未在时限内进入锁等待（编排失效）"

        # T2（confirm 链第二步）：写报名行 → 等 T1 的报名锁 → 环闭合
        try:
            db2.execute(
                text("UPDATE activity_enrollments SET cancel_reason = 'b2-probe' WHERE id = :i"),
                {"i": enrollment_id},
            )
            db2.commit()
            out["t2"] = ("ok", None)
        except Exception as exc:  # noqa: BLE001
            db2.rollback()
            out["t2"] = ("exc", exc)

        t1.join(timeout=25)
        assert not t1.is_alive(), "T1 未在时限内结束"
        t1_kind = out.get("t1", ("missing", None))[0]
        t2_kind = out.get("t2", ("missing", None))[0]
        assert {t1_kind, t2_kind} == {"ok", "exc"}, f"应恰一方成功一方失败：{out}"
        failed = out["t1"][1] if t1_kind == "exc" else out["t2"][1]
        assert _is_deadlock(failed), f"失败方应是 InnoDB 死锁 1213：{failed!r}"
    finally:
        db2.rollback()
        db2.close()
        t1.join(timeout=5)
