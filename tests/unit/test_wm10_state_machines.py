# tests/unit/test_wm10_state_machines.py — 退款 7 态 / 退会 6 态 / 联动链专项（docs/10 P0）
"""覆盖：R-309 会员费退款联动退会；R-308 execute 失败回 pending_settle + 重试；
家长撤销（退款 pending / 退会 applying）；R-305 转让退会记录与年费不退留痕；
99 元资格 refund_status 口径（WM3-03）。"""

from fastapi.testclient import TestClient


def _h(client, username="admin"):
    r = client.post("/api/admin/login", json={"username": username, "password": "dmkwords123"})
    return {"Authorization": f"Bearer {r.json()['token']}"}


def _family(client, h, phone, name="孩"):
    p = client.post(
        "/api/admin/members/parents", json={"name": "家长", "phone": phone}, headers=h
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


def _db():
    from backend.database import get_session

    return get_session()


def test_member_refund_links_withdrawal_r309(client: TestClient):
    """R-309/R-310：会员费退款申请同时创建退会申请并锁定；执行成功 →
    child withdrawn(user_refund) + 押金退款自动发起 + 退会 completed。"""
    h = _h(client)
    p, c, mini = _family(client, h, "13800001301", "联动退会孩")
    order = _pay(client, h, c["id"], "formal_fee")
    _pay_deposit(client, h, c["id"])
    # 申请 → 联动创建退会 + 锁定
    r = client.post(
        "/api/miniapp/refund-requests",
        json={"child_id": c["id"], "order_id": order["id"], "reason": "不学了"},
        headers=mini,
    )
    assert r.status_code == 200, r.text
    from backend.domain.identity.models import Child, WithdrawalRequest

    with _db() as db:
        ch = db.query(Child).filter(Child.id == c["id"]).first()
        assert ch.operation_locked == 1
        w = (
            db.query(WithdrawalRequest)
            .filter(WithdrawalRequest.child_id == c["id"])
            .order_by(WithdrawalRequest.id.desc())
            .first()
        )
        assert w is not None and w.source == "refund_linked"
        assert w.status == "applying"
    # 退会流程进行中（联动锁定）→ 不可再单独发起退会
    wdup = client.post(
        "/api/miniapp/withdrawals", json={"child_id": c["id"], "reason": "重复"}, headers=mini
    )
    assert wdup.status_code == 422
    assert "冻结" in wdup.json()["detail"]
    # 审核 → 执行成功 → withdrawn + user_refund + 押金单自动发起 + 退会 completed
    rid = r.json()["id"]
    client.post(
        f"/api/admin/refund-requests/{rid}/review",
        json={"approve": True, "remark": "同意"},
        headers=h,
    )
    ex = client.post(
        f"/api/admin/refund-requests/{rid}/execute",
        json={"success": True, "remark": "原路退回完成"},
        headers=h,
    )
    assert ex.status_code == 200, ex.text
    with _db() as db:
        ch = db.query(Child).filter(Child.id == c["id"]).first()
        assert ch.member_status == "withdrawn"
        assert ch.withdraw_reason == "user_refund"
        assert ch.operation_locked == 0
        w = (
            db.query(WithdrawalRequest)
            .filter(WithdrawalRequest.child_id == c["id"])
            .order_by(WithdrawalRequest.id.desc())
            .first()
        )
        assert w.status == "completed"
    # 押金退款自动发起（pending）
    pend = client.get("/api/admin/refund-requests?status=pending", headers=h).json()
    dep_req = [x for x in pend if x["kind"] == "deposit" and x["child_id"] == c["id"]]
    assert len(dep_req) == 1
    assert float(dep_req[0]["amount"]) == 1200


def test_refund_execute_fail_then_retry(client: TestClient):
    """R-308：execute 失败 → failed + 订单 refund_status=failed + 联动退会回 pending_settle；
    重试成功 → refunded + 退会 completed。"""
    h = _h(client)
    p, c, mini = _family(client, h, "13800001302", "失败重试孩")
    order = _pay(client, h, c["id"], "formal_fee")
    r = client.post(
        "/api/miniapp/refund-requests",
        json={"child_id": c["id"], "order_id": order["id"], "reason": "渠道问题"},
        headers=mini,
    )
    rid = r.json()["id"]
    client.post(
        f"/api/admin/refund-requests/{rid}/review",
        json={"approve": True, "remark": "同意"},
        headers=h,
    )
    # 失败（必须填原因）
    f1 = client.post(
        f"/api/admin/refund-requests/{rid}/execute",
        json={"success": False, "remark": ""},
        headers=h,
    )
    assert f1.status_code == 422
    f2 = client.post(
        f"/api/admin/refund-requests/{rid}/execute",
        json={"success": False, "remark": "银行卡信息有误，打款退回"},
        headers=h,
    )
    assert f2.status_code == 200
    assert f2.json()["status"] == "failed"
    from backend.domain.identity.models import Order, WithdrawalRequest

    with _db() as db:
        o = db.query(Order).filter(Order.id == order["id"]).first()
        assert o.status == "paid"  # 订单未变 refunded
        assert o.refund_status == "failed"
        w = (
            db.query(WithdrawalRequest)
            .filter(WithdrawalRequest.child_id == c["id"])
            .order_by(WithdrawalRequest.id.desc())
            .first()
        )
        assert w.status == "pending_settle"  # 失败回待结算
    # 重试成功
    ex = client.post(
        f"/api/admin/refund-requests/{rid}/execute",
        json={"success": True, "remark": "更正卡号后打款成功"},
        headers=h,
    )
    assert ex.status_code == 200
    assert ex.json()["status"] == "refunded"
    with _db() as db:
        o = db.query(Order).filter(Order.id == order["id"]).first()
        assert o.status == "refunded"
        assert o.refund_status == "refunded"
        w = (
            db.query(WithdrawalRequest)
            .filter(WithdrawalRequest.child_id == c["id"])
            .order_by(WithdrawalRequest.id.desc())
            .first()
        )
        assert w.status == "completed"


def test_parent_cancel_refund_and_withdrawal(client: TestClient):
    """家长撤销：退款 pending → cancelled（订单恢复、联动退会撤销解锁）；
    退会 applying → cancelled + 解锁。"""
    h = _h(client)
    p, c, mini = _family(client, h, "13800001303", "撤销孩")
    order = _pay(client, h, c["id"], "formal_fee")
    # 撤销退款申请（联动退会）
    r = client.post(
        "/api/miniapp/refund-requests",
        json={"child_id": c["id"], "order_id": order["id"], "reason": "先试试"},
        headers=mini,
    )
    rid = r.json()["id"]
    cx = client.post(
        f"/api/miniapp/refund-requests/{rid}/cancel", json={"child_id": c["id"]}, headers=mini
    )
    assert cx.status_code == 200
    assert cx.json()["status"] == "cancelled"
    from backend.domain.identity.models import Child, Order, WithdrawalRequest

    with _db() as db:
        o = db.query(Order).filter(Order.id == order["id"]).first()
        assert o.status == "paid" and o.refund_status == ""
        ch = db.query(Child).filter(Child.id == c["id"]).first()
        assert ch.operation_locked == 0
        w = (
            db.query(WithdrawalRequest)
            .filter(WithdrawalRequest.child_id == c["id"])
            .order_by(WithdrawalRequest.id.desc())
            .first()
        )
        assert w.status == "cancelled"
    # 撤销退会申请
    _pay_deposit(client, h, c["id"])
    w2 = client.post(
        "/api/miniapp/withdrawals", json={"child_id": c["id"], "reason": "再想想"}, headers=mini
    )
    assert w2.status_code == 200
    cx2 = client.post(
        f"/api/miniapp/withdrawals/{w2.json()['id']}/cancel",
        json={"child_id": c["id"]},
        headers=mini,
    )
    assert cx2.status_code == 200
    assert cx2.json()["status"] == "cancelled"
    with _db() as db:
        ch = db.query(Child).filter(Child.id == c["id"]).first()
        assert ch.operation_locked == 0
        assert ch.member_status == "formal"  # 会员资格保留


def test_transfer_generates_withdrawal_record_and_audit(client: TestClient):
    """R-305（WM10-04）：转让通过 → 转出方 withdraw_reason=membership_transfer +
    WithdrawalRequest 记录 + 年费不退款独立审计。"""
    h = _h(client)
    # 直接构造（两名孩子）
    pp = client.post(
        "/api/admin/members/parents", json={"name": "家长", "phone": "13800001304"}, headers=h
    ).json()
    src = client.post(
        f"/api/admin/members/parents/{pp['id']}/children", json={"name": "转出"}, headers=h
    ).json()
    tgt = client.post(
        f"/api/admin/members/parents/{pp['id']}/children", json={"name": "受让"}, headers=h
    ).json()
    mini = {
        "Authorization": f"Bearer {client.post('/api/miniapp/login', json={'phone': '13800001304', 'code': '1234'}).json()['token']}"
    }
    _pay(client, h, src["id"], "formal_fee")
    r = client.post(
        "/api/miniapp/transfers",
        json={"source_child_id": src["id"], "target_child_id": tgt["id"]},
        headers=mini,
    )
    assert r.status_code == 200, r.text
    ok = client.post(
        f"/api/admin/transfers/{r.json()['id']}/review",
        json={"approve": True, "remark": "同意"},
        headers=h,
    )
    assert ok.status_code == 200, ok.text
    from backend.domain.identity.models import Child, WithdrawalRequest

    with _db() as db:
        s = db.query(Child).filter(Child.id == src["id"]).first()
        assert s.member_status == "withdrawn"
        assert s.withdraw_reason == "membership_transfer"
        w = (
            db.query(WithdrawalRequest)
            .filter(WithdrawalRequest.child_id == src["id"])
            .order_by(WithdrawalRequest.id.desc())
            .first()
        )
        assert w is not None
        assert w.source == "transfer_linked"
        assert w.status == "completed"  # 无押金 → 直接完成
    # 年费不退款独立留痕
    logs = client.get(
        "/api/admin/audit-logs", params={"action": "transfer.annual_fee_no_refund"}, headers=h
    ).json()
    assert logs["total"] >= 1


def test_first_activity_refund_release_quota(client: TestClient):
    """WM3-03（R-321 refund_status 口径）：99 元退款 refunded 后可再购。"""
    h = _h(client)
    p, c, mini = _family(client, h, "13800001305", "九九孩")
    order = _pay(client, h, c["id"], "first_activity_fee")
    # 已购 → 重复购买拒绝
    dup = client.post(
        "/api/admin/orders",
        json={"child_id": c["id"], "order_type": "first_activity_fee"},
        headers=h,
    )
    assert dup.status_code == 409
    # 退款全流程
    rr = client.post(
        "/api/miniapp/refund-requests",
        json={"child_id": c["id"], "order_id": order["id"], "reason": "没时间参加"},
        headers=mini,
    )
    rid = rr.json()["id"]
    client.post(
        f"/api/admin/refund-requests/{rid}/review",
        json={"approve": True, "remark": "同意"},
        headers=h,
    )
    ex = client.post(
        f"/api/admin/refund-requests/{rid}/execute",
        json={"success": True, "remark": "已全额退"},
        headers=h,
    )
    assert ex.status_code == 200
    # refunded → 资格释放 → 可再购
    again = client.post(
        "/api/admin/orders",
        json={"child_id": c["id"], "order_type": "first_activity_fee"},
        headers=h,
    )
    assert again.status_code == 200, again.text


def test_direct_withdrawal_apply_list_review(client: TestClient):
    """直接退会 e2e（X1 链路锁）：miniapp 申请 → admin_list(applying) 可见 →
    super admin review 通过 → pending_settle + 押金退款单生成。"""
    h = _h(client)
    p, c, mini = _family(client, h, "13800001305", "直接退会孩")
    _pay(client, h, c["id"], "formal_fee")
    _pay_deposit(client, h, c["id"])

    # 1. 家长小程序申请退会（source=normal）
    w = client.post(
        "/api/miniapp/withdrawals", json={"child_id": c["id"], "reason": "搬家了"}, headers=mini
    )
    assert w.status_code == 200, w.text
    wid = w.json()["id"]
    assert w.json()["status"] == "applying"

    # 2. 管理端 applying 列表可见（前端退会 tab 操作列的判断依据）
    lst = client.get("/api/admin/withdrawals", params={"status": "applying"}, headers=h)
    assert lst.status_code == 200, lst.text
    row = next(x for x in lst.json() if x["id"] == wid)
    assert row["status"] == "applying" and row["child_name"] == "直接退会孩"

    # 3. super admin 审核通过 → 结算 + 押金退款单（无使用全额退）
    rv = client.post(
        f"/api/admin/withdrawals/{wid}/review",
        json={"approve": True, "remark": "同意"},
        headers=h,
    )
    assert rv.status_code == 200, rv.text
    # R-311：approve 同步结算，有退款单 → refunding（pending_settle 为事务内中间态）
    assert rv.json()["status"] == "refunding"

    from backend.domain.identity.models import RefundRequest

    with _db() as db:
        rf = (
            db.query(RefundRequest)
            .filter(RefundRequest.withdrawal_id == wid)
            .all()
        )
        kinds = {x.kind for x in rf}
        # 年费刚付全额可退（比例>0）+ 押金可用余额，两张单并存
        assert rf and kinds == {"order", "deposit"}, kinds
        assert sum(x.amount for x in rf) > 0
