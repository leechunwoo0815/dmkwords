# tests/unit/test_audit4_fix3_20261017.py — 第四轮大审查·第三批：借阅/预约锁序专批（2026-10-17）
"""P1-6：`borrow()` 的取锁顺序必须与全局纪律一致 —— `Reservation → copy`。

为什么用"锁序探针"而不是"跑两次碰运气"的并发用例：
    环的形状是 A 持 copy 等 Reservation、B 持 Reservation 等 copy（1213）。要确定性地区分
    "A 先锁了 copy"与"A 先锁了 Reservation"，只需**先让 S1 占住预约行**，再看第三方会话能否
    在 1 秒内拿到 copy2：
      - 修复后：borrow 卡在预约行上，**还没摸到 copy2** → 探针立刻拿到 ✓
      - 旧顺序：borrow 先锁 copy2 → 探针等锁超时 ✗（恢复旧顺序的变异即红）
"""

import threading

from fastapi.testclient import TestClient
from sqlalchemy import text

from backend.database import SessionLocal
from backend.domain.admin.models import AdminUser
from backend.domain.circulation.service import CirculationService
from tests.unit.helpers import assert_locked_read, lock_wait_query
from tests.unit.test_wm10_concurrency import _db, _h


def _setup(client, h, phone="13800008501"):
    """会员 + 押金 + 一本书 2 副本 + 孩子对该书有 1 条 active 预约（copy1 被锁）。"""
    p = client.post(
        "/api/admin/members/parents", json={"name": "锁序家长", "phone": phone}, headers=h
    ).json()
    c = client.post(
        f"/api/admin/members/parents/{p['id']}/children", json={"name": "锁序孩"}, headers=h
    ).json()
    o = client.post(
        "/api/admin/orders", json={"child_id": c["id"], "order_type": "observation_fee"}, headers=h
    ).json()
    client.post(
        f"/api/admin/orders/{o['id']}/confirm-payment", json={"pay_method": "scan"}, headers=h
    )
    d = client.post(f"/api/admin/deposits/children/{c['id']}/orders", headers=h).json()
    client.post(
        f"/api/admin/orders/{d['order_id']}/confirm-payment", json={"pay_method": "scan"}, headers=h
    )
    b = client.post(
        "/api/admin/books",
        json={"isbn": None, "title": "锁序回归书", "word_count": 1000, "copy_count": 2},
        headers=h,
    ).json()
    mini = {
        "Authorization": (
            f"Bearer {client.post('/api/miniapp/login', json={'phone': phone, 'code': '1234'}).json()['token']}"
        )
    }
    res = client.post(
        "/api/miniapp/reservations", json={"child_id": c["id"], "book_id": b["id"]}, headers=mini
    )
    assert res.status_code == 200, res.text
    with _db() as db:
        rid = db.execute(
            text(
                "SELECT id FROM reservations WHERE child_id=:c AND book_id=:b "
                "AND status='active' AND is_deleted=0"
            ),
            {"c": c["id"], "b": b["id"]},
        ).scalar()
        copy2 = db.execute(
            text(
                "SELECT id FROM book_copies WHERE book_id=:b AND status='available' "
                "AND is_deleted=0 ORDER BY id LIMIT 1"
            ),
            {"b": b["id"]},
        ).scalar()
    assert rid and copy2, (rid, copy2)
    return c, copy2, rid


def test_borrow_locks_reservation_before_copy(client: TestClient):
    h = _h(client)
    c, copy2, rid = _setup(client, h)

    holder = SessionLocal()  # S1：占住预约行（模拟"漏斗/取消正在进行"）
    try:
        holder.execute(text("SELECT id FROM reservations WHERE id=:r FOR UPDATE"), {"r": rid})
        errors: list[str] = []
        done: list[int] = []

        def run_borrow() -> None:
            db = SessionLocal()  # 子线程独立会话（线程 A）
            try:
                admin = db.query(AdminUser).first()
                rec, _w = CirculationService(db).borrow(admin, c["id"], copy2, None)
                db.commit()
                done.append(rec.id)
            except Exception as exc:  # noqa: BLE001
                db.rollback()
                errors.append(f"{type(exc).__name__}: {exc}")
            finally:
                db.close()

        t = threading.Thread(target=run_borrow, daemon=True)
        t.start()
        assert_locked_read(lock_wait_query(), "借书收尾等的预约行锁定读")

        # S2：short-timeout 探针，尝试锁 copy2
        probe = SessionLocal()
        try:
            probe.execute(text("SET SESSION lock_wait_timeout = 1"))
            probe.execute(text("SELECT id FROM book_copies WHERE id=:c FOR UPDATE"), {"c": copy2})
            probe.rollback()
        except Exception as exc:  # noqa: BLE001
            probe.rollback()
            probe.close()
            holder.rollback()
            holder.close()
            t.join(5)
            raise AssertionError(
                f"锁序错误：borrow 在拿到预约行之前就锁住了副本行（探针 1 秒内拿不到 copy2）：{exc}"
            ) from exc
        finally:
            try:
                probe.close()
            except Exception:  # noqa: BLE001
                pass
    finally:
        try:
            holder.rollback()
            holder.close()
        except Exception:  # noqa: BLE001
            pass

    t.join(15)
    assert not errors, errors
    assert done, "借书线程没有完成"
    # 终态：预约关成 checked_out、副本借出
    with _db() as db:
        assert (
            db.execute(text("SELECT status FROM reservations WHERE id=:r"), {"r": rid}).scalar()
            == "checked_out"
        )
        assert (
            db.execute(text("SELECT status FROM book_copies WHERE id=:c"), {"c": copy2}).scalar()
            == "borrowed"
        )


def test_borrow_normal_path_with_reservation_still_works(client: TestClient):
    """锁序改动后的基本回归：有预约 → 借同书另一册 → 预约自动关（checked_out）。"""
    h = _h(client)
    c, copy2, rid = _setup(client, h, phone="13800008502")
    r = client.post(
        "/api/admin/circulation/borrow",
        json={"child_id": c["id"], "copy_id": copy2},
        headers=h,
    )
    assert r.status_code == 200, r.text
    with _db() as db:
        assert (
            db.execute(text("SELECT status FROM reservations WHERE id=:r"), {"r": rid}).scalar()
            == "checked_out"
        )
