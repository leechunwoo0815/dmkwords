# tests/unit/test_review11_r11b_20261026.py — 第十一轮整改 R11-b（第一百五十一批）
"""R11-b 五条资金/库存正确性的机器判据（审查报告 §二/§十）：

- **R11-05**：预约不校验"该孩子已在借同一本书" ⇒ 白约（锁副本 72h + 占额度，核销必失败）。
- **F1**：退会结算的押金项被**上一周期**的历史 REFUNDED 退款单永久屏蔽（重新入会后押金滞留）。
- **F2**：超管代退无冻结期闸、退款完成会把审核中的孩子直接写成 withdrawn 且锁未清。
- **B1**：取消赞 / 馆长赞是"读—判—写"，并发双击把 like_count 扣 2（永不自愈）。
- **B4**：内容寻址文件按"该帖软删"直接删，不查其它活跃帖是否共享同一路径。

RED（变异可证伪）：逐条见各用例 docstring。
"""

from __future__ import annotations

from datetime import datetime, timedelta
from decimal import Decimal

import pytest
from fastapi.testclient import TestClient

from backend.common.exceptions import ConflictError
from backend.database import SessionLocal, get_session
from backend.domain.admin.models import AdminUser
from backend.domain.billing.models import Deposit, DepositLedger
from backend.domain.circulation.models import BorrowRecord
from backend.domain.identity.models import (
    Child,
    Order,
    Parent,
    RefundRequest,
    WithdrawalRequest,
)
from backend.domain.identity.order_service import OrderService
from backend.domain.identity.wm10_service import RefundService
from backend.domain.identity.wm10_withdrawal_service import WithdrawalService
from backend.domain.reading.models import Reservation
from backend.domain.reading_circle import card_render
from backend.domain.reading_circle.admin_service import (
    AdminCircleService,
    CircleImageCleanupService,
)
from backend.domain.reading_circle.models import CircleLike, CirclePost
from backend.domain.reading_circle.service import CircleService
from tests.unit.test_wm10_concurrency import (
    _book_with_copies,
    _family,
    _h,
    _pay,
    _pay_deposit,
)


def _child_with_deposit(db, *, status=Child.MEMBER_FORMAL, locked=0, phone="13982026100"):
    parent = Parent(name="R11B家长", phone=phone)
    db.add(parent)
    db.flush()
    child = Child(parent_id=parent.id, name="R11B孩", member_status=status)
    child.operation_locked = locked
    db.add(child)
    db.flush()
    dep = Deposit(
        child_id=child.id,
        amount=Decimal("1200"),
        available_amount=Decimal("1200"),
        status=Deposit.STATUS_PAID,
    )
    db.add(dep)
    db.flush()
    return parent, child, dep


# ---------------- R11-05：预约查在借 ----------------


def test_reservation_rejects_book_already_on_loan(client: TestClient):
    """RED（删 create 里的 borrowed 判据）：接口 200 并落一条 active 预约（白约）。"""
    from backend.domain.catalog.models import BookCopy

    h = _h(client)
    _p, c, mini = _family(client, h, "13982026001", "在借禁约孩")
    _pay(client, h, c["id"], "observation_fee")
    _pay_deposit(client, h, c["id"])
    bid = _book_with_copies(client, h, "R11B 在借禁约书", 2)
    with get_session() as db:
        copy_id = db.query(BookCopy).filter(BookCopy.book_id == bid).first().id
    br = client.post(
        "/api/admin/circulation/borrow",
        json={"child_id": c["id"], "copy_id": copy_id},
        headers=h,
    )
    assert br.status_code == 200, br.text

    r = client.post(
        "/api/miniapp/reservations",
        json={"child_id": c["id"], "book_id": bid},
        headers=mini,
    )
    assert r.status_code == 409, (
        f"同一本书已在借，预约必须拒绝；实 {r.status_code} {r.text[:120]}"
        "（RED=服务端放行，锁一册副本 72h 后核销必失败）"
    )
    with get_session() as db:
        active = (
            db.query(Reservation)
            .filter(
                Reservation.child_id == c["id"],
                Reservation.book_id == bid,
                Reservation.status == Reservation.STATUS_ACTIVE,
                Reservation.is_deleted == 0,
            )
            .count()
        )
    assert active == 0


def test_checkout_rejects_when_same_book_on_loan(client: TestClient, monkeypatch):
    """RED（删 checkout 里的 borrowed 预检）：核销先改状态再被 borrow 顶回，预约仍是 active。"""
    h = _h(client)
    _p, c, mini = _family(client, h, "13982026002", "核销在借孩")
    _pay(client, h, c["id"], "observation_fee")
    _pay_deposit(client, h, c["id"])
    bid = _book_with_copies(client, h, "R11B 核销在借书", 2)
    rr = client.post(
        "/api/miniapp/reservations", json={"child_id": c["id"], "book_id": bid}, headers=mini
    )
    assert rr.status_code == 200, rr.text
    rid = rr.json()["id"]
    # 造"历史预约 + 已在借同书"的现场（新 create 已拦，这里模拟存量/并发窗口）
    with get_session() as db:
        res = db.query(Reservation).filter(Reservation.id == rid).first()
        db.add(
            BorrowRecord(
                child_id=c["id"],
                copy_id=res.copy_id,
                book_id=bid,
                due_at=datetime.now() + timedelta(days=14),
                status=BorrowRecord.STATUS_ACTIVE,
            )
        )
        db.commit()

    # 预检必须在**进入 borrow 之前**拦住：借书层也会顶回"同书未还"，但那时状态已改、
    # 事务只能整体回滚（旧行为），抓不到"白占副本"这个中间态。故用哨兵证明 borrow 未被调用。
    from backend.domain.circulation.service import CirculationService

    def _must_not_borrow(*_a, **_k):
        raise AssertionError("checkout 应在调用 borrow 之前拒绝（预检缺失）")

    monkeypatch.setattr(CirculationService, "borrow", _must_not_borrow)
    ck = client.post(f"/api/admin/reservations/{rid}/checkout", headers=h)
    assert ck.status_code == 409, ck.text
    with get_session() as db:
        status = db.query(Reservation.status).filter(Reservation.id == rid).scalar()
    assert status == Reservation.STATUS_ACTIVE, (
        f"核销被拒后预约必须保持 active（白占副本才能被人工处理）；实 {status}"
    )


# ---------------- F1：押金退款单只认本周期 ----------------


def test_settlement_deposit_ignores_previous_cycle_refund(db):
    """RED（去掉 cycle_start 过滤）：上一周期 REFUNDED 押金单把本周期押金项永久屏蔽。"""
    _p, child, dep = _child_with_deposit(db)
    now = datetime.now()
    db.add(
        DepositLedger(
            deposit_id=dep.id,
            entry_type=DepositLedger.ENTRY_PAY,
            amount=Decimal("1200"),
            balance_after=Decimal("1200"),
            created_at=now - timedelta(days=60),
        )
    )
    old = RefundRequest(
        kind=RefundRequest.KIND_DEPOSIT,
        deposit_id=dep.id,
        child_id=child.id,
        amount=Decimal("1200"),
        status=RefundRequest.STATUS_REFUNDED,
        reason="上一周期退会",
    )
    old.create_time = now - timedelta(days=30)
    db.add(old)
    # 重新入会后新缴一笔（周期起点）
    db.add(
        DepositLedger(
            deposit_id=dep.id,
            entry_type=DepositLedger.ENTRY_PAY,
            amount=Decimal("1200"),
            balance_after=Decimal("1200"),
            created_at=now - timedelta(days=1),
        )
    )
    db.commit()

    items = WithdrawalService(db)._settle_items(child)
    dep_items = [it for it in items if it["kind"] == RefundRequest.KIND_DEPOSIT]
    assert dep_items, (
        "本周期押金仍在账上，结算必须包含押金项；"
        "RED=历史 REFUNDED 单把它永久屏蔽（钱静默滞留、押金无单独退款入口）"
    )
    assert dep_items[0]["amount"] == Decimal("1200.00")


def test_settlement_deposit_blocked_by_current_cycle_refund(db):
    """负向对照：同一周期内的在办/已退押金单仍必须阻止重复结算（不能把闸整个拆掉）。"""
    _p, child, dep = _child_with_deposit(db, phone="13982026101")
    now = datetime.now()
    db.add(
        DepositLedger(
            deposit_id=dep.id,
            entry_type=DepositLedger.ENTRY_PAY,
            amount=Decimal("1200"),
            balance_after=Decimal("1200"),
            created_at=now - timedelta(days=10),
        )
    )
    current = RefundRequest(
        kind=RefundRequest.KIND_DEPOSIT,
        deposit_id=dep.id,
        child_id=child.id,
        amount=Decimal("1200"),
        status=RefundRequest.STATUS_PENDING,
        reason="本周期在办",
    )
    current.create_time = now - timedelta(hours=1)
    db.add(current)
    db.commit()

    items = WithdrawalService(db)._settle_items(child)
    assert not [it for it in items if it["kind"] == RefundRequest.KIND_DEPOSIT]


# ---------------- F2：冻结期闸 + 退款完成不越权 ----------------


def test_super_admin_refund_blocked_while_review_locked(db):
    """RED（删 assert_refund_allowed 的冻结期闸）：审核中的孩子被单笔退款，随后被写成 withdrawn。"""
    admin = db.query(AdminUser).first()
    _p, child, _dep = _child_with_deposit(db, locked=1, phone="13982026102")
    order = Order(
        order_no="R11B-F2-0001",
        order_type=Order.TYPE_OBSERVATION,
        parent_id=child.parent_id,
        child_id=child.id,
        amount=Decimal("1000"),
        status=Order.STATUS_PAID,
    )
    db.add(order)
    db.commit()

    with pytest.raises(ConflictError, match="审核流程"):
        OrderService(db).refund_order(admin, order.id, "测试代退")
    assert db.query(RefundRequest).filter(RefundRequest.order_id == order.id).count() == 0, (
        "闸门必须在建单之前生效（不能先落 pending 再报错）"
    )


def test_unlinked_refund_does_not_withdraw_locked_child(db):
    """RED（删 _complete_refund_withdrawal 的冻结期守卫）：审核中的孩子被单笔退款直接写 withdrawn。"""
    _p, child, dep = _child_with_deposit(db, locked=1, phone="13982026103")
    order = Order(
        order_no="R11B-F2-0002",
        order_type=Order.TYPE_OBSERVATION,
        parent_id=child.parent_id,
        child_id=child.id,
        amount=Decimal("1000"),
        status=Order.STATUS_PAID,
    )
    req = RefundRequest(
        kind=RefundRequest.KIND_ORDER,
        order_id=order.id,
        child_id=child.id,
        amount=Decimal("1000"),
        status=RefundRequest.STATUS_PROCESSING,
        reason="冻结前提交的退款单",
    )
    db.add_all([order, req])
    db.commit()

    RefundService(db)._complete_refund_withdrawal(None, req, order)
    db.refresh(child)
    db.refresh(dep)
    assert child.member_status == Child.MEMBER_FORMAL, (
        "审核结果未定，单笔退款不得把会员状态写成 withdrawn（解锁唯一出口是退会流程）"
    )
    assert dep.status == Deposit.STATUS_PAID, "不得在审核中抢先发起押金退款"

    # 联动单（带 withdrawal_id）行为不变：仍完成退会并自动发起押金退款
    w = WithdrawalRequest(child_id=child.id, source=WithdrawalRequest.SOURCE_NORMAL)
    db.add(w)
    db.flush()
    req.withdrawal_id = w.id
    db.commit()
    RefundService(db)._complete_refund_withdrawal(None, req, order)
    # `SessionLocal(autoflush=False)`：方法内 flush 发生在置 REFUNDING **之前**，
    # 不补 flush 就 refresh 会把未落库的状态覆盖回旧值（铁律 20 同族）。
    db.flush()
    db.refresh(child)
    db.refresh(dep)
    assert child.member_status == Child.MEMBER_WITHDRAWN
    assert dep.status == Deposit.STATUS_REFUNDING


# ---------------- B1：取消赞 / 馆长赞并发 ----------------


def _post(db, *, like_count=0, admin_liked=0, phone="13982026104"):
    parent, child, _dep = _child_with_deposit(db, phone=phone)
    post = CirclePost(
        parent_id=parent.id,
        child_id=child.id,
        card_type=CirclePost.CARD_FINISH_BOOK,
        ref_id=1,
        card_data="{}",
        like_count=like_count,
        admin_liked=admin_liked,
    )
    db.add(post)
    db.flush()
    return parent, child, post


def test_unlike_consumes_like_row_only_once(db):
    """RED（把 _consume_like_row 还原成"置 row.is_deleted=1 后 return True"）：
    并发对手已提交时仍会再扣一次（like_count 双降）。"""
    parent, child, post = _post(db, like_count=1)
    like = CircleLike(post_id=post.id, parent_id=parent.id, child_id=child.id)
    db.add(like)
    db.flush()
    like_id = like.id
    db.commit()

    # 并发对手先提交"已取消"，本请求手里仍是旧对象——条件 UPDATE 必须拿到 0
    other = SessionLocal()
    try:
        other.query(CircleLike).filter(CircleLike.id == like_id).update({"is_deleted": 1})
        other.commit()
    finally:
        other.close()

    svc = CircleService(db)
    assert svc._consume_like_row(like_id) is False, "同一行只能被消费一次（后到者 rowcount=0）"

    # 正常路径仍是幂等：连点两次只扣 1
    db.query(CircleLike).filter(CircleLike.id == like_id).update({"is_deleted": 0})
    db.commit()
    svc.unlike(post.id, child)
    svc.unlike(post.id, child)
    db.refresh(post)
    assert post.like_count == 0


def test_admin_like_unlike_is_single_switch(db):
    """RED（去掉 admin_liked 条件）：连点两次馆长赞把计数 +2、取消两次 -2。"""
    _p, _c, post = _post(db, phone="13982026105")
    svc = AdminCircleService(db)

    assert svc._set_admin_liked(post.id, liked=True) is True
    db.commit()
    assert svc._set_admin_liked(post.id, liked=True) is False, "已在赞态时不得再 +1"
    db.refresh(post)
    assert post.admin_liked == 1 and post.like_count == 1

    assert svc._set_admin_liked(post.id, liked=False) is True
    db.commit()
    assert svc._set_admin_liked(post.id, liked=False) is False, "已取消时不得再 -1"
    db.refresh(post)
    assert post.admin_liked == 0 and post.like_count == 0


# ---------------- B4：内容寻址文件共享 ----------------


def test_cleanup_keeps_file_shared_with_active_post(db, tmp_path, monkeypatch):
    """RED（删 _referenced_elsewhere 守卫）：软删帖清理把活跃帖共享的图删掉（碎图）。"""
    import backend.config as config_mod

    monkeypatch.setattr(
        config_mod, "get_settings", lambda: type("S", (), {"UPLOADS_DIR": str(tmp_path)})()
    )
    circle = tmp_path / "circle"
    circle.mkdir(parents=True)
    shared = circle / "r11b_shared.jpg"
    shared.write_bytes(b"img")

    parent, child, _dep = _child_with_deposit(db, phone="13982026106")
    now = datetime.now()
    dead = CirclePost(
        parent_id=parent.id,
        child_id=child.id,
        card_type=CirclePost.CARD_FINISH_BOOK,
        ref_id=2,
        card_data="{}",
        image_path="circle/r11b_shared.jpg",
    )
    dead.is_deleted = 1
    dead.update_time = now - timedelta(days=31)
    alive = CirclePost(
        parent_id=parent.id,
        child_id=child.id,
        card_type=CirclePost.CARD_FINISH_BOOK,
        ref_id=3,
        card_data="{}",
        image_path="circle/r11b_shared.jpg",
    )
    db.add_all([dead, alive])
    db.commit()

    CircleImageCleanupService(db).cleanup_orphan_images()
    assert shared.is_file(), (
        "文件仍被活跃帖引用，清理任务不得删除（RED=按单帖软删就 os.remove，活跃帖碎图）"
    )
    db.refresh(dead)
    assert dead.image_path == "", "本行指针应被清空（不再引用该文件）"

    # 活跃帖也软删并过期后，下一次清理才允许删文件
    alive.is_deleted = 1
    alive.update_time = now - timedelta(days=31)
    db.commit()
    CircleImageCleanupService(db).cleanup_orphan_images()
    assert not shared.exists()


def test_ensure_thumbs_keeps_file_shared_with_active_post(db, monkeypatch):
    """RED（删 ensure_circle_thumbs 的 shared 检查）：重渲一帖时删掉另一活跃帖共享的旧缩略图。"""
    import os

    shared_rel = "circle/shared_thumb_r11b.jpg"
    new_rel = "circle/new_thumb_r11b.jpg"
    root = card_render._uploads_root()
    os.makedirs(os.path.join(root, "circle"), exist_ok=True)
    full = os.path.join(root, shared_rel)
    with open(full, "wb") as fh:
        fh.write(b"thumb")

    parent, child, _dep = _child_with_deposit(db, phone="13982026107")
    a = CirclePost(
        parent_id=parent.id,
        child_id=child.id,
        card_type=CirclePost.CARD_FINISH_BOOK,
        ref_id=4,
        card_data="{}",
        thumb_path=shared_rel,
    )
    b = CirclePost(
        parent_id=parent.id,
        child_id=child.id,
        card_type=CirclePost.CARD_FINISH_BOOK,
        ref_id=5,
        card_data="{}",
        thumb_path=shared_rel,
    )
    db.add_all([a, b])
    db.commit()

    monkeypatch.setattr(card_render, "render_thumb", lambda *a, **k: new_rel)
    card_render.ensure_circle_thumbs(db)
    assert os.path.isfile(full), (
        "另一活跃帖仍引用该缩略图，重渲时不得删除共享文件（RED=按旧路径直接 os.remove）"
    )
