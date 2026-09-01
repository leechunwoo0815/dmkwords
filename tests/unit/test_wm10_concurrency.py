# tests/unit/test_wm10_concurrency.py — P1 资金并发行锁（session_pair 基建复用）
"""并发缺陷：无行锁的"读→校验→写"在双事务下快照覆盖写。
有效并发红的结构：B 先建旧快照（普通读，REPEATABLE READ）→ A 推进并提交 →
B 走被测 service（expire_all 模拟生产新请求语义——锁定读读最新已提交、
普通读走旧快照，两实现在此正确分流）：
- 无锁实现：B 读旧快照校验通过 → 覆盖写（双写/覆盖先写/物理双借）= RED
- 锁定读实现：SELECT FOR UPDATE 总读最新已提交 → 校验失败拒绝 = GREEN
跨事务读 A 提交数据需刷新快照（先例：test_wm13_admin_notify.py）。"""

from datetime import datetime, timedelta
from decimal import Decimal

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import func

from backend.common.exceptions import ConflictError, NotFoundError, ValidationError
from tests.unit.helpers import force_book_on


def _h(client, username="admin"):
    r = client.post("/api/admin/login", json={"username": username, "password": "dmkwords123"})
    return {"Authorization": f"Bearer {r.json()['token']}"}


def _family(client, h, phone, name="孩"):
    p = client.post(
        "/api/admin/members/parents", json={"name": "并发家长", "phone": phone}, headers=h
    ).json()
    c = client.post(
        f"/api/admin/members/parents/{p['id']}/children", json={"name": name}, headers=h
    ).json()
    mini = {
        "Authorization": f"Bearer {client.post('/api/miniapp/login', json={'phone': phone, 'code': '1234'}).json()['token']}"
    }
    return p, c, mini


def _pay(client, h, child_id, order_type):
    o = client.post(
        "/api/admin/orders", json={"child_id": child_id, "order_type": order_type}, headers=h
    ).json()
    client.post(
        f"/api/admin/orders/{o['id']}/confirm-payment", json={"pay_method": "scan"}, headers=h
    )
    return o


def _pay_deposit(client, h, child_id):
    do = client.post(f"/api/admin/deposits/children/{child_id}/orders", headers=h).json()
    client.post(
        f"/api/admin/orders/{do['order_id']}/confirm-payment",
        json={"pay_method": "scan"},
        headers=h,
    )


def _paid_child(client, h, phone, name="孩"):
    p = client.post(
        "/api/admin/members/parents", json={"name": "并发家长", "phone": phone}, headers=h
    ).json()
    return client.post(
        f"/api/admin/members/parents/{p['id']}/children", json={"name": name}, headers=h
    ).json()


def _book_with_copies(client, h, title, copies=3) -> int:
    book_id = client.post(
        "/api/admin/books",
        json={"isbn": None, "title": title, "word_count": 100, "copy_count": copies},
        headers=h,
    ).json()["id"]
    force_book_on(client, h, book_id)
    return book_id


def _db():
    from backend.database import get_session

    return get_session()


# ---------- P1-F1：退款 execute/review 行锁 ----------


def _approved_refund(client, h, mini, c, order) -> dict:
    rr = client.post(
        "/api/miniapp/refund-requests",
        json={"child_id": c["id"], "order_id": order["id"], "reason": "并发退款"},
        headers=mini,
    ).json()
    assert (
        client.post(
            f"/api/admin/refund-requests/{rr['id']}/review",
            json={"approve": True, "remark": "同意"},
            headers=h,
        ).status_code
        == 200
    )
    return rr


def test_execute_concurrent_no_double_write(client: TestClient, session_pair):
    """B 旧快照 approved；A 已完整执行（refunded + 押金退款单 1 笔）；B 再 execute——
    锁定读 → 读到 refunded → 422 拒绝；无锁 → 覆盖写重走 success 分支双写（RED）。"""
    h = _h(client)
    p, c, mini = _family(client, h, "13800002301", "并发退孩")
    order = _pay(client, h, c["id"], "observation_fee")
    _pay_deposit(client, h, c["id"])
    rid = _approved_refund(client, h, mini, c, order)["id"]

    s1, s2 = session_pair
    from backend.domain.identity.models import RefundRequest

    stale = s2.query(RefundRequest).filter(RefundRequest.id == rid).first()
    assert stale.status == "approved"  # B 旧快照
    # A 走 HTTP 完整执行成功
    r_a = client.post(
        f"/api/admin/refund-requests/{rid}/execute",
        json={"success": True, "remark": "先到者执行"},
        headers=h,
    )
    assert r_a.status_code == 200, r_a.text
    s2.expire_all()
    from backend.domain.identity.wm10_service import RefundService

    with pytest.raises(ValidationError):
        RefundService(s2).execute(
            type("A", (), {"id": 1, "display_name": "超管"})(), rid, True, "并发执行"
        )
    s2.rollback()
    s1.rollback()
    with _db() as db:
        db.commit()
        dep_rr = (
            db.query(RefundRequest)
            .filter(RefundRequest.kind == RefundRequest.KIND_DEPOSIT, RefundRequest.is_deleted == 0)
            .all()
        )
        assert len(dep_rr) == 1, f"押金退款单应 1 笔，实 {len(dep_rr)}（双写）"
        req = db.query(RefundRequest).filter(RefundRequest.id == rid).first()
        assert req.status == RefundRequest.STATUS_REFUNDED


def test_review_concurrent_reject_cannot_overwrite_approve(client: TestClient, session_pair):
    """B 旧快照 pending；A 已 approve 提交；B 走 review reject——
    锁定读 → approved 非 pending → 422（先写不被覆盖）；无锁 → 覆盖为 rejected（RED）。"""
    h = _h(client)
    p, c, mini = _family(client, h, "13800002302", "审核并发孩")
    order = _pay(client, h, c["id"], "observation_fee")
    rid = _approved_refund(client, h, mini, c, order)["id"]

    s1, s2 = session_pair
    from backend.domain.identity.models import RefundRequest
    from backend.domain.identity.wm10_service import RefundService

    # 重建"B 快照早于 approve"场景：置回 pending → B 快照 → A approve 提交 → B reject
    with _db() as db:
        req = db.query(RefundRequest).filter(RefundRequest.id == rid).first()
        req.status = RefundRequest.STATUS_PENDING
        db.commit()
    stale = s2.query(RefundRequest).filter(RefundRequest.id == rid).first()
    assert stale.status == "pending"
    a = s1.query(RefundRequest).filter(RefundRequest.id == rid).with_for_update().first()
    a.status = RefundRequest.STATUS_APPROVED
    a.reviewed_by = 1
    s1.commit()
    s2.expire_all()
    with pytest.raises(ValidationError):
        RefundService(s2).review(
            type("A", (), {"id": 1, "display_name": "超管"})(), rid, False, "并发拒绝"
        )
    s2.rollback()
    with _db() as db:
        db.commit()
        req = db.query(RefundRequest).filter(RefundRequest.id == rid).first()
        assert req.status == RefundRequest.STATUS_APPROVED, f"approve 被覆盖：{req.status}"


def test_execute_double_submit_rejected(client: TestClient):
    """串行防重回归：已 refunded 的单再 execute → 422（状态机守卫）。"""
    h = _h(client)
    p, c, mini = _family(client, h, "13800002303", "防重孩")
    order = _pay(client, h, c["id"], "observation_fee")
    rr = client.post(
        "/api/miniapp/refund-requests",
        json={"child_id": c["id"], "order_id": order["id"], "reason": "防重"},
        headers=mini,
    ).json()
    client.post(
        f"/api/admin/refund-requests/{rr['id']}/review",
        json={"approve": True, "remark": "同意"},
        headers=h,
    )
    r1 = client.post(
        f"/api/admin/refund-requests/{rr['id']}/execute",
        json={"success": True, "remark": "第一次"},
        headers=h,
    )
    assert r1.status_code == 200, r1.text
    r2 = client.post(
        f"/api/admin/refund-requests/{rr['id']}/execute",
        json={"success": True, "remark": "第二次"},
        headers=h,
    )
    assert r2.status_code == 422, f"重复执行未拦: {r2.status_code}"


# ---------- P1-F2：confirm_payment 行锁 + 押金幂等双保险 ----------


def test_confirm_payment_concurrent_idempotent(client: TestClient, session_pair):
    """B 旧快照 pending_manual；A 已确认（paid + ENTRY_PAY 1 笔）提交；B 再 confirm——
    锁定读 → paid → 422；无锁 → 覆盖写双 ENTRY_PAY 台账（RED）。"""
    h = _h(client)
    p, c, mini = _family(client, h, "13800002304", "确认并发孩")
    client.post(
        "/api/admin/orders", json={"child_id": c["id"], "order_type": "observation_fee"}, headers=h
    )
    do = client.post(f"/api/admin/deposits/children/{c['id']}/orders", headers=h).json()
    s1, s2 = session_pair
    from backend.domain.identity.models import Order

    stale = s2.query(Order).filter(Order.id == do["order_id"]).first()
    assert stale.status == "pending_manual_confirm"  # B 旧快照
    r_a = client.post(
        f"/api/admin/orders/{do['order_id']}/confirm-payment",
        json={"pay_method": "scan", "remark": "先到者"},
        headers=h,
    )
    assert r_a.status_code == 200, r_a.text
    s2.expire_all()
    from backend.domain.identity.service import OrderService

    with pytest.raises(ValidationError):
        OrderService(s2).confirm_payment(
            type("A", (), {"id": 1, "display_name": "超管"})(),
            do["order_id"],
            type("R", (), {"pay_method": "scan", "remark": "并发确认"})(),
        )
    s2.rollback()
    from backend.domain.billing.models import Deposit, DepositLedger

    with _db() as db:
        db.commit()
        ledgers = db.query(DepositLedger).filter(DepositLedger.entry_type == "pay").all()
        assert len(ledgers) == 1, f"ENTRY_PAY 台账应 1 笔，实 {len(ledgers)}（双写）"
        dep = db.query(Deposit).filter(Deposit.child_id == c["id"]).first()
        assert float(dep.available_amount) == 1200


# ---------- P1-F3：押金 deduct 锁定读 ----------


def test_deduct_concurrent_no_balance_drift(client: TestClient, session_pair):
    """余额 1200：B 旧快照；A 已扣 1100 提交；B 再扣 800——
    锁定读：读最新余额 100 → 扣 100 + over 700 挂未付（终态 available=0）；
    无锁：按旧快照 1200 扣 800 → 覆盖写 400（A 的 1100 被吞，账实漂移，RED）。"""
    h = _h(client)
    c = _paid_child(client, h, "13800002305", "并发扣款孩")
    _pay_deposit(client, h, c["id"])
    s1, s2 = session_pair
    from backend.domain.billing.models import Deposit

    stale = s2.query(Deposit).filter(Deposit.child_id == c["id"]).first()
    assert float(stale.available_amount) == 1200  # B 旧快照
    r_a = client.post(
        f"/api/admin/deposits/children/{c['id']}/deduct",
        json={"amount": "1100", "reason": "先到者扣款"},
        headers=h,
    )
    assert r_a.status_code == 200, r_a.text
    s2.expire_all()
    from backend.domain.billing.service import DepositService

    try:
        DepositService(s2).deduct_for_compensation(
            type("A", (), {"id": 1, "display_name": "超管"})(), c["id"], Decimal("800"), "并发扣款"
        )
        s2.commit()
    except ValidationError:
        s2.rollback()
    with _db() as db:
        db.commit()
        dep = db.query(Deposit).filter(Deposit.child_id == c["id"]).first()
        # 终态（卡片口径）：B 基于锁定读的最新余额 100 扣款 → available=0、
        # deducted=1200（1100+100）、over 700 挂 unpaid_balance 待付；
        # 无锁实现：available=400（A 的 1100 被吞）= RED
        assert float(dep.available_amount) == 0, f"余额应 0，实 {dep.available_amount}（覆盖写）"
        assert float(dep.deducted_amount) == 1200, f"deducted 应 1200，实 {dep.deducted_amount}"
        assert float(dep.unpaid_balance) == 700, f"unpaid 应 700，实 {dep.unpaid_balance}"


# ---------- P1-F5：还书 record 锁 + overdue_mark 状态守卫 ----------


def test_return_book_concurrent_no_double_return(client: TestClient, session_pair):
    """B 旧快照（active）；A 已还书提交（returned）；B 再还同 copy——
    锁定读：record（FOR UPDATE）读最新 → 无进行中借阅 → 404；
    无锁：B 读旧快照 active → 覆盖写双还 + 双事件（RED）。"""
    h = _h(client)
    p, c, mini = _family(client, h, "13800002306", "并发还孩")
    _pay(client, h, c["id"], "observation_fee")
    _pay_deposit(client, h, c["id"])
    book_id = _book_with_copies(client, h, "并发还书")
    from backend.domain.catalog.models import BookCopy

    with _db() as db:
        copy_id = db.query(BookCopy).filter(BookCopy.book_id == book_id).first().id
    br = client.post(
        "/api/admin/circulation/borrow", json={"child_id": c["id"], "copy_id": copy_id}, headers=h
    ).json()
    assert br.get("id"), f"借书失败: {br}"
    s1, s2 = session_pair
    from backend.domain.circulation.models import BorrowRecord

    stale = (
        s2.query(BorrowRecord)
        .filter(BorrowRecord.copy_id == copy_id, BorrowRecord.status == BorrowRecord.STATUS_ACTIVE)
        .first()
    )
    assert stale is not None  # B 旧快照：进行中
    r_a = client.post(
        "/api/admin/circulation/return", json={"copy_id": copy_id, "condition": "normal"}, headers=h
    )
    assert r_a.status_code == 200, r_a.text
    s2.expire_all()
    from backend.domain.circulation.service import CirculationService

    with pytest.raises((ValidationError, NotFoundError)):
        CirculationService(s2).return_book(
            type("A", (), {"id": 1, "display_name": "超管"})(), copy_id, "normal"
        )
    s2.rollback()
    with _db() as db:
        db.commit()
        records = (
            db.query(BorrowRecord)
            .filter(BorrowRecord.copy_id == copy_id, BorrowRecord.is_deleted == 0)
            .all()
        )
        returned = [r for r in records if r.status in ("returned", "lost")]
        assert len(returned) == 1, f"还书记录应 1 条，实 {len(returned)}（双还）"


def test_overdue_mark_does_not_overwrite_returned(client: TestClient, session_pair):
    """overdue_mark 与还书并发：已 returned 的记录不被覆盖回 overdue（状态守卫）。"""
    h = _h(client)
    p, c, mini = _family(client, h, "13800002307", "逾期并发孩")
    _pay(client, h, c["id"], "observation_fee")
    _pay_deposit(client, h, c["id"])
    book_id = _book_with_copies(client, h, "逾期并发书")
    from backend.domain.catalog.models import BookCopy
    from backend.domain.circulation.models import BorrowRecord

    with _db() as db:
        copy_id = db.query(BookCopy).filter(BookCopy.book_id == book_id).first().id
    client.post(
        "/api/admin/circulation/borrow", json={"child_id": c["id"], "copy_id": copy_id}, headers=h
    )
    with _db() as db:
        rec = (
            db.query(BorrowRecord)
            .filter(
                BorrowRecord.copy_id == copy_id,
                BorrowRecord.status == BorrowRecord.STATUS_ACTIVE,
            )
            .first()
        )
        rec.due_at = datetime.now() - timedelta(days=3)
        db.commit()
    s1, s2 = session_pair
    stale = (
        s2.query(BorrowRecord)
        .filter(BorrowRecord.copy_id == copy_id, BorrowRecord.status == BorrowRecord.STATUS_ACTIVE)
        .first()
    )
    assert stale is not None  # B 扫描快照：ACTIVE
    r_a = client.post(
        "/api/admin/circulation/return", json={"copy_id": copy_id, "condition": "normal"}, headers=h
    )
    assert r_a.status_code == 200, r_a.text
    from backend.domain.circulation.service import CirculationService

    CirculationService(s2).overdue_mark()  # B 旧扫描结果：状态守卫应跳过已 returned
    s2.commit()
    with _db() as db:
        db.commit()
        rec = db.query(BorrowRecord).filter(BorrowRecord.copy_id == copy_id).first()
        assert rec.status == BorrowRecord.STATUS_RETURNED, (
            f"已还记录被 overdue_mark 覆盖：{rec.status}"
        )


# ---------- P1-F4：预约释放/取消 锁序（Reservation → copy） ----------


def _reserved(client, h, mini, c, book_id):
    r = client.post(
        "/api/miniapp/reservations", json={"child_id": c["id"], "book_id": book_id}, headers=mini
    )
    assert r.status_code == 200, r.text
    rid = r.json()["id"]
    from backend.domain.reading.models import Reservation

    with _db() as db:
        res = db.query(Reservation).filter(Reservation.id == rid).first()
        return rid, res.copy_id


def test_expire_due_concurrent_no_phantom_available(client: TestClient, session_pair):
    """B 旧快照（active + 已过期）；A 已提交核销终态（checked_out + borrowed +
    活跃借阅）；B 跑 expire_due——锁定读：读到 checked_out → 跳过（copy 保持
    borrowed）；无锁：覆盖写 expired + available → 活跃借阅下副本可被再借（物理双借，RED）。
    A 的核销终态用 DB 模拟（并发对手已提交的既成事实），B 走真实 service。"""
    h = _h(client)
    p, c, mini = _family(client, h, "13800002308", "预约并发孩")
    _pay(client, h, c["id"], "observation_fee")
    _pay_deposit(client, h, c["id"])
    book_id = _book_with_copies(client, h, "预约并发书")
    rid, copy_id = _reserved(client, h, mini, c, book_id)
    # 先拨过期（B 快照里必须已含 expires_at 过去，due 扫描才命中）
    from backend.domain.reading.models import Reservation

    with _db() as db:
        res0 = db.query(Reservation).filter(Reservation.id == rid).first()
        res0.expires_at = datetime.now() - timedelta(hours=1)
        db.commit()
    s1, s2 = session_pair
    stale = s2.query(Reservation).filter(Reservation.id == rid).first()
    assert stale.status == "active"  # B 旧快照（active + 已过期）
    # A 的核销终态（已提交的既成事实）
    from backend.domain.catalog.models import BookCopy
    from backend.domain.circulation.models import BorrowRecord

    with _db() as db:
        res = db.query(Reservation).filter(Reservation.id == rid).first()
        res.status = Reservation.STATUS_CHECKED_OUT
        copy = db.query(BookCopy).filter(BookCopy.id == copy_id).first()
        copy.status = BookCopy.STATUS_BORROWED
        db.add(
            BorrowRecord(
                child_id=c["id"],
                book_id=book_id,
                copy_id=copy_id,
                status=BorrowRecord.STATUS_ACTIVE,
                due_at=datetime.now() + timedelta(days=14),
            )
        )
        db.commit()
    s2.expire_all()
    from backend.domain.reading.service import ReservationService

    ReservationService(s2).expire_due()  # 旧快照扫描命中 → 修复后逐条锁定读跳过
    s2.commit()
    with _db() as db:
        db.commit()
        res = db.query(Reservation).filter(Reservation.id == rid).first()
        assert res.status == "checked_out", f"核销态被释放覆盖：{res.status}"
        copy = db.query(BookCopy).filter(BookCopy.id == copy_id).first()
        assert copy.status == BookCopy.STATUS_BORROWED, (
            f"副本被改回 available（物理双借窗口）：{copy.status}"
        )
        rec = (
            db.query(BorrowRecord)
            .filter(
                BorrowRecord.copy_id == copy_id,
                BorrowRecord.status == BorrowRecord.STATUS_ACTIVE,
                BorrowRecord.is_deleted == 0,
            )
            .first()
        )
        assert rec is not None, "活跃借阅记录丢失"


def test_cancel_reservation_concurrent_no_phantom_available(client: TestClient, session_pair):
    """cancel 同款：B 旧快照 active；A 已核销提交；B（家长 token）cancel——
    锁定读 → 422（状态不可取消）；无锁 → copy 被改回 available（RED）。"""
    h = _h(client)
    p, c, mini = _family(client, h, "13800002309", "取消并发孩")
    _pay(client, h, c["id"], "observation_fee")
    _pay_deposit(client, h, c["id"])
    book_id = _book_with_copies(client, h, "取消并发书")
    rid, copy_id = _reserved(client, h, mini, c, book_id)
    # 先拨过期（A 核销前过期校验会用锁定读读最新值——先过期会让 A 被拒，
    # 因此 A 核销在拨过期之前完成：拨过期放在 A 核销后仅影响 B 的扫描视图）
    s1, s2 = session_pair
    from backend.domain.reading.models import Reservation

    stale = s2.query(Reservation).filter(Reservation.id == rid).first()
    assert stale.status == "active"  # B 旧快照
    # A 核销借出（HTTP，真实链路，预约未过期可核销）
    r_a = client.post(f"/api/admin/reservations/{rid}/checkout", headers=h)
    assert r_a.status_code == 200, r_a.text
    s2.expire_all()
    # B（家长 token 旧快照 active）取消：服务端锁定读最新（checked_out）→ 422
    r_b = client.post(
        f"/api/miniapp/reservations/{rid}/cancel", json={"child_id": c["id"]}, headers=mini
    )
    assert r_b.status_code == 422, f"核销后取消未拦: {r_b.status_code} {r_b.text[:100]}"
    from backend.domain.catalog.models import BookCopy

    with _db() as db:
        db.commit()
        copy = db.query(BookCopy).filter(BookCopy.id == copy_id).first()
        assert copy.status == BookCopy.STATUS_BORROWED, f"副本状态被破坏：{copy.status}"


def test_first_activity_double_confirm_rejected(client: TestClient):
    """P1-F6：两笔 pending 99 元单依次 confirm → 第二笔 ConflictError（R-321 每账号一次）。
    无锁内复查时两笔都能 paid（RED）。"""
    h = _h(client)
    p, c, mini = _family(client, h, "13800002310", "99元孩")
    o1 = client.post(
        "/api/admin/orders",
        json={"child_id": c["id"], "order_type": "first_activity_fee"},
        headers=h,
    ).json()
    o2 = client.post(
        "/api/admin/orders",
        json={"child_id": c["id"], "order_type": "first_activity_fee"},
        headers=h,
    ).json()
    r1 = client.post(
        f"/api/admin/orders/{o1['id']}/confirm-payment", json={"pay_method": "scan"}, headers=h
    )
    assert r1.status_code == 200, r1.text
    r2 = client.post(
        f"/api/admin/orders/{o2['id']}/confirm-payment", json={"pay_method": "scan"}, headers=h
    )
    assert r2.status_code == 409, f"第二笔 99 元未拦: {r2.status_code} {r2.text[:120]}"


def test_refund_apply_concurrent_single_request(client: TestClient, session_pair):
    """P1-F7：B 旧快照（订单 paid、无进行中申请）；A 已提交退款申请（pending）；
    B 再 apply 同一订单——Order 行锁后查重：B 阻塞后读到 pending → 409；
    无锁 → 两条 pending 僵尸单（RED）。"""
    h = _h(client)
    p, c, mini = _family(client, h, "13800002311", "申请并发孩")
    order = _pay(client, h, c["id"], "observation_fee")
    s1, s2 = session_pair
    from backend.domain.identity.models import Order, RefundRequest

    stale = s2.query(Order).filter(Order.id == order["id"]).first()
    assert stale.status == "paid"  # B 旧快照
    # A 提交退款申请（HTTP）
    r_a = client.post(
        "/api/miniapp/refund-requests",
        json={"child_id": c["id"], "order_id": order["id"], "reason": "先到申请"},
        headers=mini,
    )
    assert r_a.status_code == 200, r_a.text
    s2.expire_all()
    # B（旧快照）再申请：Order 锁后查重 → 409；无锁 → 双 pending
    from backend.domain.identity.models import Child
    from backend.domain.identity.wm10_service import RefundService

    # R-309：observation 订单的退款申请联动退会并冻结孩子——B 被
    # Order 行锁后查重（409）或冻结守卫（422）拦下均可，双申请被防即达标
    with pytest.raises((ConflictError, ValidationError)):
        RefundService(s2).apply(
            s2.query(Child).filter(Child.id == c["id"]).with_for_update().first(),
            order["id"],
            "并发申请",
        )
    s2.rollback()
    with _db() as db:
        db.commit()
        cnt = (
            db.query(func.count(RefundRequest.id))
            .filter(RefundRequest.order_id == order["id"], RefundRequest.is_deleted == 0)
            .scalar()
        )
        assert cnt == 1, f"退款申请应 1 条，实 {cnt}（僵尸单）"


def test_execute_advance_failure_rolls_back_all(client: TestClient, session_pair, monkeypatch):
    """P1-F9：execute 主流程与 _advance_withdrawal 同事务——聚合推进抛异常时
    主流程整体回滚（退款单不落 refunded 终态）；修复前双 commit 半提交（RED）。"""
    h = _h(client)
    p, c, mini = _family(client, h, "13800002312", "事务边界孩")
    order = _pay(client, h, c["id"], "formal_fee")  # formal 触发联动退会链
    rr = client.post(
        "/api/miniapp/refund-requests",
        json={"child_id": c["id"], "order_id": order["id"], "reason": "事务边界"},
        headers=mini,
    ).json()
    client.post(
        f"/api/admin/refund-requests/{rr['id']}/review",
        json={"approve": True, "remark": "同意"},
        headers=h,
    )
    s1, s2 = session_pair
    from backend.domain.identity.wm10_service import RefundService

    def _boom(db, request_id):
        raise RuntimeError("模拟推进崩溃")

    monkeypatch.setattr(RefundService, "_advance_withdrawal", _boom)
    with pytest.raises(RuntimeError):
        RefundService(s2).execute(
            type("A", (), {"id": 1, "display_name": "超管"})(), rr["id"], True, "执行"
        )
    s2.rollback()
    with _db() as db:
        db.commit()
        from backend.domain.identity.models import RefundRequest as RR

        req = db.query(RR).filter(RR.id == rr["id"]).first()
        # 方案 A：同事务 → 推进失败整体回滚，退款单不落 refunded 终态
        assert req.status != "refunded", f"推进崩溃后主流程半提交：{req.status}（事务分裂未修）"


def test_checkout_res_lock_reads_fresh_state(session_pair):
    """顺带-1（P1 审查遗留）：checkout 锁定读必须 populate_existing。

    结构（P1-F4 探针同款，identity map 语义实证）：
    A 普通读预约行（identity map 载入旧快照）→ B 推进 status=expired 并提交 →
    A 锁定读：无 populate_existing 时返回已加载实例保留旧值（守卫读到 active 误核销）；
    有 populate_existing 强制行数据刷新（读到 expired → 守卫拒绝）。
    """
    from datetime import datetime, timedelta

    from backend.domain.reading.models import Reservation

    s1, s2 = session_pair
    res = Reservation(
        child_id=1,
        book_id=1,
        copy_id=1,
        expires_at=datetime.now() + timedelta(hours=72),
        status=Reservation.STATUS_ACTIVE,
    )
    s1.add(res)
    s1.commit()
    rid = res.id

    # A：普通读（载入 identity map 旧快照 active）——模拟 checkout _res_pre
    pre = s1.query(Reservation).filter(Reservation.id == rid).first()
    assert pre.status == Reservation.STATUS_ACTIVE

    # B：推进 expired 并提交（模拟并发释放/取消后状态推进）
    s2.query(Reservation).filter(Reservation.id == rid).with_for_update().first()
    s2.query(Reservation).filter(Reservation.id == rid).update(
        {"status": Reservation.STATUS_EXPIRED}
    )
    s2.commit()

    # A：锁定读 + populate_existing（checkout 修复后路径）→ 必须读到新值
    locked = (
        s1.query(Reservation)
        .filter(Reservation.id == rid)
        .with_for_update()
        .populate_existing()
        .first()
    )
    assert locked.status == Reservation.STATUS_EXPIRED, (
        "锁定读未刷新 identity map 旧值——守卫将读到 active 误核销（顺带-1 RED 语义）"
    )
    s1.rollback()
