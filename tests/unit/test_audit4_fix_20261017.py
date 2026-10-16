# tests/unit/test_audit4_fix_20261017.py — 第四轮大审查·第一批修复回归（2026-10-17）
"""第六十六批：P0-1（活动取消 expire_all）+ P1-1（观察期费资格）+ P1-2（押金复位/回退边）
+ P1-3（赔偿待办幂等键）+ P1-5（到期提醒去重键）+ P2-14（评估报告上传去重键）。

每条用例都是**报告里那条缺陷的复现**（第四轮大审查亲验探针转正），逐条"去修复即红"。
"""

import io
from datetime import date, datetime, timedelta

from fastapi.testclient import TestClient

from tests.unit.test_wm10_concurrency import _db, _h


def _parent_child(
    client, h, phone="13800008301", name="回归孩", member=True, deposit=False, deposit_paid=None
):
    p = client.post(
        "/api/admin/members/parents", json={"name": "回归家长", "phone": phone}, headers=h
    ).json()
    c = client.post(
        f"/api/admin/members/parents/{p['id']}/children", json={"name": name}, headers=h
    ).json()
    if member:
        o = client.post(
            "/api/admin/orders",
            json={"child_id": c["id"], "order_type": "observation_fee"},
            headers=h,
        ).json()
        client.post(
            f"/api/admin/orders/{o['id']}/confirm-payment", json={"pay_method": "scan"}, headers=h
        )
    if deposit or deposit_paid:
        d = client.post(f"/api/admin/deposits/children/{c['id']}/orders", headers=h).json()
        client.post(
            f"/api/admin/orders/{d['order_id']}/confirm-payment",
            json={"pay_method": "scan"},
            headers=h,
        )
    m = client.post("/api/miniapp/login", json={"phone": phone, "code": "1234"}).json()
    return p, c, {"Authorization": f"Bearer {m['token']}"}


def _book(client, h, title, copies=1):
    b = client.post(
        "/api/admin/books",
        json={"isbn": None, "title": title, "word_count": 1000, "copy_count": copies},
        headers=h,
    )
    assert b.status_code == 200, b.text
    book = b.json()
    return book, client.get(f"/api/admin/books/{book['id']}/copies", headers=h).json()


def _deposit_status(child_id: int) -> str:
    from backend.domain.billing.models import Deposit

    with _db() as db:
        return db.query(Deposit).filter(Deposit.child_id == child_id).first().status


def _compensation_todos() -> int:
    from backend.common.admin_notification_models import AdminNotification

    with _db() as db:
        return (
            db.query(AdminNotification)
            .filter(AdminNotification.scene == "admin.copy_damage_compensation")
            .count()
        )


# ---------- P0-1：活动取消必须真的落库（活动/报名/订单三处） ----------


def test_activity_cancel_persists_activity_and_enrollment(client: TestClient):
    h = _h(client)
    _p, c, mini = _parent_child(client, h, deposit=True)
    a = client.post(
        "/api/admin/activities",
        json={
            "title": "取消回归活动",
            "start_at": (datetime.now() + timedelta(days=7)).isoformat(),
            "max_quota": 10,
            "fee": "99.00",
        },
        headers=h,
    ).json()
    e = client.post(
        f"/api/miniapp/activities/{a['id']}/enroll", json={"child_id": c["id"]}, headers=mini
    ).json()
    body = client.post(f"/api/admin/activities/{a['id']}/cancel", headers=h)
    assert body.status_code == 200, body.text
    assert body.json()["cancelled"] == 1 and body.json()["orders_voided"] == 1, body.json()

    from backend.domain.activity.models import Activity, ActivityEnrollment
    from backend.domain.identity.models import Order

    with _db() as db:
        act = db.query(Activity).filter(Activity.id == a["id"]).first()
        enr = (
            db.query(ActivityEnrollment)
            .filter(ActivityEnrollment.id == e["enrollment"]["id"])
            .first()
        )
        order = db.query(Order).filter(Order.id == e["order_id"]).first()
        assert act.status == Activity.STATUS_CANCELLED, "接口说取消了，活动却还是 published"
        assert enr.status == ActivityEnrollment.STATUS_CANCELLED, "报名没落库（仍 pending_payment）"
        assert order.status == Order.STATUS_CANCELLED
    # 已取消的活动不能再报名（P0 的实际后果：一个"已取消"的活动还在卖票）
    again = client.post(
        f"/api/miniapp/activities/{a['id']}/enroll", json={"child_id": c["id"]}, headers=mini
    )
    assert again.status_code == 422, again.text


# ---------- P1-1：观察期费资格（建单侧单一来源 + 发起支付前复核） ----------


def test_observation_fee_not_sellable_to_existing_member(client: TestClient):
    h = _h(client)
    _p, c, mini = _parent_child(client, h)  # 已是观察期会员
    plans = client.get(f"/api/miniapp/payment/plans?child_id={c['id']}", headers=mini).json()
    item = next(i for i in plans["items"] if i["order_type"] == "observation_fee")
    assert item["available"] is False, item
    assert "有效会员" in item["reason"], item
    r = client.post(
        "/api/miniapp/orders",
        json={"child_id": c["id"], "order_type": "observation_fee"},
        headers=mini,
    )
    assert r.status_code == 409, (r.status_code, r.text)
    # 管理端造单同源（同一定价函数）
    r2 = client.post(
        "/api/admin/orders",
        json={"child_id": c["id"], "order_type": "observation_fee"},
        headers=h,
    )
    assert r2.status_code in (409, 422), r2.text


def test_observation_fee_prepay_recheck_blocks_after_state_change(client: TestClient):
    """建单时孩子还没入会、支付前已经入会（另一笔单先结算/馆员人工开通）→ 必须在**调网关前**挡住。"""
    h = _h(client)
    p = client.post(
        "/api/admin/members/parents", json={"name": "待入会家长", "phone": "13800008302"}, headers=h
    ).json()
    c = client.post(
        f"/api/admin/members/parents/{p['id']}/children", json={"name": "待入会孩"}, headers=h
    ).json()
    login = client.post("/api/miniapp/login", json={"phone": "13800008302", "code": "1234"}).json()
    mini = {"Authorization": f"Bearer {login['token']}"}
    from backend.domain.identity.models import Parent

    with _db() as db:  # 线上支付要求微信一键登录家长（openid 闸排在本次复核之前）
        db.query(Parent).filter(Parent.id == p["id"]).update({"wechat_openid": "o_regression_1"})
        db.commit()
    order = client.post(
        "/api/miniapp/orders",
        json={"child_id": c["id"], "order_type": "observation_fee"},
        headers=mini,
    ).json()
    from backend.domain.identity.models import Child

    with _db() as db:  # 建单之后孩子已入会（模拟并发/人工开通）
        db.query(Child).filter(Child.id == c["id"]).update(
            {"member_status": "observation", "member_expire": date.today() + timedelta(days=25)}
        )
        db.commit()
    r = client.post(f"/api/miniapp/orders/{order['id']}/pay", headers=mini)
    assert r.status_code == 422, r.text
    assert "有效会员" in r.json()["detail"], r.text
    from backend.domain.identity.models import Order

    with _db() as db:  # 没走到网关：订单仍停在待支付、没有流水号
        row = db.query(Order).filter(Order.id == order["id"]).first()
        assert row.status == Order.STATUS_PENDING_PAYMENT and not row.transaction_id, row.status


# ---------- P1-2：押金复位（重新入会可再缴）与 refunding 回退 ----------


def test_deposit_refunded_reset_when_child_rejoins(client: TestClient):
    h = _h(client)
    _p, c, _mini = _parent_child(client, h, member=False)
    d = client.post(f"/api/admin/deposits/children/{c['id']}/orders", headers=h).json()
    client.post(
        f"/api/admin/orders/{d['order_id']}/confirm-payment", json={"pay_method": "scan"}, headers=h
    )
    # 退会结算后的终态（钱已退给家长）——此时孩子的押金账户是 `refunded`
    from backend.domain.billing.models import Deposit

    with _db() as db:
        db.query(Deposit).filter(Deposit.child_id == c["id"]).update(
            {"status": "refunded", "available_amount": 0}
        )
        db.commit()
    # 重新入会：缴观察期费 → 押金账户必须回到「未缴」
    r = client.post(
        "/api/admin/orders", json={"child_id": c["id"], "order_type": "observation_fee"}, headers=h
    ).json()
    client.post(
        f"/api/admin/orders/{r['id']}/confirm-payment", json={"pay_method": "scan"}, headers=h
    )
    assert _deposit_status(c["id"]) == "unpaid", "重新入会后押金仍是 refunded → 永远缴不了押金"
    again = client.post(f"/api/admin/deposits/children/{c['id']}/orders", headers=h)
    assert again.status_code == 200, again.text
    from backend.domain.billing.models import Deposit as D2

    client.post(
        f"/api/admin/orders/{again.json()['order_id']}/confirm-payment",
        json={"pay_method": "scan"},
        headers=h,
    )
    with _db() as db:
        dep = db.query(D2).filter(D2.child_id == c["id"]).first()
        assert dep.status == "paid" and dep.available_amount == dep.amount, (
            dep.status,
            dep.available_amount,
        )
        # 历史流水保留（押金台账只记不改）：pay → refund → pay 三条
        from backend.domain.billing.models import DepositLedger

        rows = (
            db.query(DepositLedger)
            .filter(DepositLedger.deposit_id == dep.id)
            .order_by(DepositLedger.id)
            .all()
        )
        # 复位不清流水：两次缴纳各一条 pay（本用例直改状态模拟退会，故没有 refund 行）
        assert [r.entry_type for r in rows] == ["pay", "pay"], [r.entry_type for r in rows]


def test_deposit_refunding_restored_when_refund_rejected(client: TestClient):
    """押金退款单被拒（钱没出去）→ 押金不能永久卡在 refunding。"""
    h = _h(client)
    _p, c, mini = _parent_child(client, h, deposit=True)
    w = client.post(
        "/api/miniapp/withdrawals", json={"child_id": c["id"], "reason": "回归：退会"}, headers=mini
    ).json()
    wid = w.get("id") or w.get("request_id")
    r = client.post(
        f"/api/admin/withdrawals/{wid}/review",
        json={"approve": True, "remark": "同意退会"},
        headers=h,
    )
    assert r.status_code == 200, r.text
    assert _deposit_status(c["id"]) == "refunding", _deposit_status(c["id"])
    from backend.domain.identity.models import RefundRequest

    with _db() as db:
        rr = (
            db.query(RefundRequest)
            .filter(
                RefundRequest.withdrawal_id == wid,
                RefundRequest.kind == RefundRequest.KIND_DEPOSIT,
                RefundRequest.is_deleted == 0,
            )
            .first()
        )
        assert rr is not None
        rid = rr.id
    rej = client.post(
        f"/api/admin/refund-requests/{rid}/review",
        json={"approve": False, "remark": "押金退款单被拒（回归）"},
        headers=h,
    )
    assert rej.status_code == 200, rej.text
    assert _deposit_status(c["id"]) == "paid", (
        "押金退款单被拒后卡在 refunding（钱没出去却借不了书）"
    )


# ---------- P1-3：赔偿待办按"事件"幂等（不同册/不同借阅各一条） ----------


def test_compensation_todo_per_damage_event(client: TestClient):
    h = _h(client)
    _p, c, _mini = _parent_child(client, h, deposit=True)
    books = [_book(client, h, f"赔偿回归书{i}") for i in range(2)]
    recs = []
    for _book_row, cp in books:
        rec = client.post(
            "/api/admin/circulation/borrow",
            json={"child_id": c["id"], "copy_id": cp[0]["id"]},
            headers=h,
        ).json()
        recs.append((rec, cp[0]["id"]))
    # 两条链各来一次：报损（book0）与还书带损（book1）
    r1 = client.post(
        f"/api/admin/copies/{recs[0][1]}/report-damage",
        json={"kind": "lost", "reason": "回归：遗失"},
        headers=h,
    )
    assert r1.status_code == 200, r1.text
    r2 = client.post(
        "/api/admin/circulation/return",
        json={"copy_id": recs[1][1], "condition": "maintenance"},
        headers=h,
    )
    assert r2.status_code == 200, r2.text
    assert _compensation_todos() == 2, "同一孩子的两笔损坏/遗失必须各留一条赔偿待办"


def test_compensation_todo_repeats_after_handled(client: TestClient):
    """处理完之后再出事要能再提醒（`ignore_handled`）：同一册报损 → 处理 → 修复后再报损。"""
    h = _h(client)
    _book_row, cp = _book(client, h, "重复报损回归书")
    r1 = client.post(
        f"/api/admin/copies/{cp[0]['id']}/report-damage",
        json={"kind": "damage", "reason": "回归：第一次损坏"},
        headers=h,
    )
    assert r1.status_code == 200, r1.text
    assert _compensation_todos() == 1
    from backend.common.admin_notification_models import AdminNotification

    with _db() as db:  # 馆员处理完这条待办
        db.query(AdminNotification).filter(
            AdminNotification.scene == "admin.copy_damage_compensation"
        ).update({"handled_at": datetime.now(), "handled_by": 1})
        db.commit()
    client.put(
        f"/api/admin/copies/{cp[0]['id']}/status",
        json={"status": "available", "reason": "修复归架"},
        headers=h,
    )
    r2 = client.post(
        f"/api/admin/copies/{cp[0]['id']}/report-damage",
        json={"kind": "damage", "reason": "回归：修好后又坏"},
        headers=h,
    )
    assert r2.status_code == 200, r2.text
    # 唯一索引 (scene, ref_type, ref_id, dedup_key) 不含 handled 状态 → 实现是**就地复活**那条行，
    # 行数仍 1，但必须重新回到"未处理"且带上这次的事由
    with _db() as db:
        row = (
            db.query(AdminNotification)
            .filter(AdminNotification.scene == "admin.copy_damage_compensation")
            .first()
        )
        assert _compensation_todos() == 1, "同一册同性质共用一条待办（复活而非新增）"
        assert row.handled_at is None, "已处理的旧待办必须被重新打开"
        assert "修好后又坏" in row.content, row.content


# ---------- P1-5：会员到期提醒跨会员周期仍要发 ----------


def test_member_expire_remind_survives_renewal_cycle(client: TestClient):
    h = _h(client)
    _p, c, _mini = _parent_child(client, h)
    from backend.domain.identity.models import Child
    from backend.domain.identity.service import ChildService

    with _db() as db:
        db.query(Child).filter(Child.id == c["id"]).update(
            {"member_status": "formal", "member_expire": date.today() + timedelta(days=30)}
        )
        db.commit()
        first = ChildService(db).member_expire_remind()
        # 下一个会员周期（另一个到期日，仍在同一提醒窗口内）：键必须跟着"期"变
        db.query(Child).filter(Child.id == c["id"]).update(
            {"member_expire": date.today() + timedelta(days=29)}
        )
        db.commit()
        second = ChildService(db).member_expire_remind()
    assert first >= 1, "第一周期就没发（前置不成立）"
    assert second >= 1, "续费后再到期必须重新提醒（原先键只用天数 → 终身静默）"


# ---------- P2-14：评估报告每次上传都要通知 ----------


def test_observation_report_upload_notifies_each_time(client: TestClient):
    h = _h(client)
    _p, c, mini = _parent_child(client, h)
    from tests.unit.test_wm3_voucher import _png_bytes

    img = _png_bytes()
    for remark in ("第一期评估", "第二期评估"):
        r = client.post(
            f"/api/admin/children/{c['id']}/observation-reports",
            data={"remark": remark},
            files=[("files", ("r.png", io.BytesIO(img), "image/png"))],
            headers=h,
        )
        assert r.status_code == 200, r.text
    msgs = client.get(
        "/api/miniapp/notifications", params={"child_id": c["id"]}, headers=mini
    ).json()
    items = msgs["items"] if isinstance(msgs, dict) else msgs
    uploaded = [m for m in items if "评估报告" in str(m.get("title", ""))]
    assert len(uploaded) == 2, [m.get("title") for m in items]
