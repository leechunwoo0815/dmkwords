# tests/unit/test_wm10_transfer_refund.py — 退款/退会/转让/评估报告（真实链路）
import io
from datetime import datetime, timedelta

from fastapi.testclient import TestClient


def _h(client, username="admin"):
    r = client.post("/api/admin/login", json={"username": username, "password": "dmkwords123"})
    return {"Authorization": f"Bearer {r.json()['token']}"}


def _mk_parent_with_children(client, h, phone, names, formal=True):
    p = client.post(
        "/api/admin/members/parents", json={"name": "家长", "phone": phone}, headers=h
    ).json()
    out = []
    for n in names:
        c = client.post(
            f"/api/admin/members/parents/{p['id']}/children", json={"name": n}, headers=h
        ).json()
        out.append(c)
    r = client.post("/api/miniapp/login", json={"phone": phone, "code": "1234"})
    mini = {"Authorization": f"Bearer {r.json()['token']}"}
    return p, out, mini


def _pay(client, h, child_id, order_type):
    o = client.post(
        "/api/admin/orders", json={"child_id": child_id, "order_type": order_type}, headers=h
    ).json()
    client.post(
        f"/api/admin/orders/{o['id']}/confirm-payment", json={"pay_method": "scan"}, headers=h
    )
    return o


def _mk_book(client, h, isbn):
    return client.post(
        "/api/admin/books",
        json={
            "isbn": isbn,
            "title": f"B{isbn[-3:]}",
            "word_count": 1000,
        },
        headers=h,
    ).json()


def test_refund_preview_apply_review_chain(client: TestClient):
    h = _h(client)
    p, [c], mini = _mk_parent_with_children(client, h, "13800001001", ["退款孩"])
    o = _pay(client, h, c["id"], "formal_fee")
    # 预览：服务端算可退金额（刚付款 → 接近全额）
    pv = client.get(
        f"/api/miniapp/refund-preview?child_id={c['id']}&order_id={o['id']}", headers=mini
    ).json()
    assert pv["order_type"] == "formal_fee"
    assert float(pv["refundable_amount"]) > 5000  # 6000 刚付完接近全额
    assert "比例" in pv["rule"]
    # staff01 不能审
    hs = _h(client, "staff01")
    denied = client.get("/api/admin/refund-requests", headers=hs)
    assert denied.status_code == 403
    # 申请
    r = client.post(
        "/api/miniapp/refund-requests",
        json={
            "child_id": c["id"],
            "order_id": o["id"],
            "reason": "孩子时间不够",
        },
        headers=mini,
    )
    assert r.status_code == 200, r.text
    rid = r.json()["id"]
    # 同一订单重复申请被拒
    dup = client.post(
        "/api/miniapp/refund-requests",
        json={
            "child_id": c["id"],
            "order_id": o["id"],
            "reason": "再来一次",
        },
        headers=mini,
    )
    assert dup.status_code == 409
    # 拒绝（必须填原因）
    rej = client.post(
        f"/api/admin/refund-requests/{rid}/review", json={"approve": False, "remark": ""}, headers=h
    )
    assert rej.status_code == 422
    rej2 = client.post(
        f"/api/admin/refund-requests/{rid}/review",
        json={
            "approve": False,
            "remark": "使用已超半年，按店规不退",
        },
        headers=h,
    )
    assert rej2.status_code == 200
    # 家长可见拒绝原因，可再次申请
    mine = client.get(f"/api/miniapp/refund-requests?child_id={c['id']}", headers=mini).json()
    assert mine[0]["status"] == "rejected"
    assert "店规" in mine[0]["review_remark"]
    r2 = client.post(
        "/api/miniapp/refund-requests",
        json={
            "child_id": c["id"],
            "order_id": o["id"],
            "reason": "再申请",
        },
        headers=mini,
    )
    assert r2.status_code == 200
    # 通过（R-308 两步）：approve → approved（订单仍 paid，退款链路 approved）
    ok = client.post(
        f"/api/admin/refund-requests/{r2.json()['id']}/review",
        json={
            "approve": True,
            "remark": "同意退",
        },
        headers=h,
    )
    assert ok.status_code == 200
    assert ok.json()["status"] == "approved"
    # 执行退款（登记凭证）→ refunded + 订单 refunded
    ex = client.post(
        f"/api/admin/refund-requests/{r2.json()['id']}/execute",
        json={"success": True, "remark": "微信原路退回，凭证 20260824-001"},
        headers=h,
    )
    assert ex.status_code == 200, ex.text
    assert ex.json()["status"] == "refunded"
    from backend.database import get_session
    from backend.domain.identity.models import Order

    db = get_session()
    order = db.query(Order).filter(Order.id == o["id"]).first()
    assert order.status == "refunded"
    assert order.refund_status == "refunded"
    db.close()


def test_withdrawal_flow_and_deposit_refund(client: TestClient):
    h = _h(client)
    p, [c], mini = _mk_parent_with_children(client, h, "13800001002", ["退会孩"])
    _pay(client, h, c["id"], "observation_fee")
    do = client.post(f"/api/admin/deposits/children/{c['id']}/orders", headers=h).json()
    client.post(
        f"/api/admin/orders/{do['order_id']}/confirm-payment",
        json={"pay_method": "scan"},
        headers=h,
    )
    # 借一本书 → 退会前提不满足
    _mk_book(client, h, "9781000000001")
    br = client.post(
        "/api/admin/circulation/borrow",
        json={
            "child_id": c["id"],
            "isbn": "9781000000001",
        },
        headers=h,
    )
    assert br.status_code == 200
    blocked = client.post(
        "/api/miniapp/withdrawals",
        json={
            "child_id": c["id"],
            "reason": "想退",
        },
        headers=mini,
    )
    assert blocked.status_code == 422
    assert "未归还" in blocked.json()["detail"]
    # 还书 → 可退
    client.post(
        "/api/admin/circulation/return",
        json={"copy_id": br.json()["copy_id"], "condition": "normal"},
        headers=h,
    )
    r = client.post(
        "/api/miniapp/withdrawals",
        json={
            "child_id": c["id"],
            "reason": "搬家了",
        },
        headers=mini,
    )
    assert r.status_code == 200, r.text
    wid = r.json()["id"]
    # 审核期间冻结：借书被拒
    frozen = client.post(
        "/api/admin/circulation/borrow",
        json={
            "child_id": c["id"],
            "isbn": "9781000000001",
        },
        headers=h,
    )
    assert frozen.status_code == 422
    assert "冻结" in frozen.json()["detail"]
    # admin 拒绝 → 解锁
    rej = client.post(
        f"/api/admin/withdrawals/{wid}/review",
        json={
            "approve": False,
            "remark": "再考虑下",
        },
        headers=h,
    )
    assert rej.status_code == 200
    borrow2 = client.post(
        "/api/admin/circulation/borrow",
        json={
            "child_id": c["id"],
            "isbn": "9781000000001",
        },
        headers=h,
    )
    assert borrow2.status_code == 200
    client.post(
        "/api/admin/circulation/return",
        json={"copy_id": borrow2.json()["copy_id"], "condition": "normal"},
        headers=h,
    )
    # 再申请 → 通过（R-311 六态）：审核通过 → 结算生成退款单（观察期费 + 押金 1200）→ refunding
    r2 = client.post(
        "/api/miniapp/withdrawals",
        json={
            "child_id": c["id"],
            "reason": "确定退",
        },
        headers=mini,
    )
    wid2 = r2.json()["id"]
    ok = client.post(
        f"/api/admin/withdrawals/{wid2}/review",
        json={
            "approve": True,
            "remark": "同意",
        },
        headers=h,
    )
    assert ok.status_code == 200
    assert ok.json()["status"] == "refunding"  # 结算单生成，进入退款中
    from backend.database import get_session
    from backend.domain.identity.models import Child, WithdrawalRequest

    db = get_session()
    w = db.query(WithdrawalRequest).filter(WithdrawalRequest.id == wid2).first()
    assert w.source == "normal"
    # 结算单：观察期费（按剩余天数）+ 押金 1200
    pend = client.get("/api/admin/refund-requests?status=pending", headers=h).json()
    dep_req = [x for x in pend if x["kind"] == "deposit" and x["child_id"] == c["id"]]
    obs_req = [
        x
        for x in pend
        if x["kind"] == "order"
        and x["child_id"] == c["id"]
        and x.get("order_type") == "observation_fee"
    ]
    assert len(dep_req) == 1
    assert float(dep_req[0]["amount"]) == 1200
    assert len(obs_req) == 1
    db.close()
    # 逐单审核 + 执行 → 全部 refunded → 退会 completed + withdrawn
    for rr in (obs_req[0], dep_req[0]):
        client.post(
            f"/api/admin/refund-requests/{rr['id']}/review",
            json={"approve": True, "remark": "同意"},
            headers=h,
        )
        ex = client.post(
            f"/api/admin/refund-requests/{rr['id']}/execute",
            json={"success": True, "remark": "线下已退，凭证留存"},
            headers=h,
        )
        assert ex.status_code == 200, ex.text
    db = get_session()
    ch = db.query(Child).filter(Child.id == c["id"]).first()
    assert ch.member_status == "withdrawn"
    assert ch.withdraw_reason == "user_withdrawal"
    assert ch.operation_locked == 0
    w2 = db.query(WithdrawalRequest).filter(WithdrawalRequest.id == wid2).first()
    assert w2.status == "completed"
    from backend.domain.billing.models import Deposit

    dep = db.query(Deposit).filter(Deposit.child_id == c["id"]).first()
    assert dep.status == "refunded"
    db.close()


def test_transfer_full_chain(client: TestClient):
    h = _h(client)
    p, [src, tgt], mini = _mk_parent_with_children(client, h, "13800001003", ["大孩", "二孩"])
    _pay(client, h, src["id"], "formal_fee")
    do = client.post(f"/api/admin/deposits/children/{src['id']}/orders", headers=h).json()
    client.post(
        f"/api/admin/orders/{do['order_id']}/confirm-payment",
        json={"pay_method": "scan"},
        headers=h,
    )
    # 条件核对：tgt none、src formal → 全过
    cond = client.get(
        f"/api/miniapp/transfers/conditions?source_child_id={src['id']}&target_child_id={tgt['id']}",
        headers=mini,
    ).json()
    failed = [c["name"] for c in cond["conditions"] if not c["ok"]]
    assert failed == [], failed
    # 借书后 → 条件失败且前端可见具体项
    _mk_book(client, h, "9781000000011")
    br = client.post(
        "/api/admin/circulation/borrow",
        json={
            "child_id": src["id"],
            "isbn": "9781000000011",
        },
        headers=h,
    )
    assert br.status_code == 200, br.text
    cond2 = client.get(
        f"/api/miniapp/transfers/conditions?source_child_id={src['id']}&target_child_id={tgt['id']}",
        headers=mini,
    ).json()
    failed2 = [c["name"] for c in cond2["conditions"] if not c["ok"]]
    assert any("归还" in f for f in failed2)
    r_fail = client.post(
        "/api/miniapp/transfers",
        json={
            "source_child_id": src["id"],
            "target_child_id": tgt["id"],
        },
        headers=mini,
    )
    assert r_fail.status_code == 422
    client.post(
        "/api/admin/circulation/return",
        json={"copy_id": br.json()["copy_id"], "condition": "normal"},
        headers=h,
    )
    # 正式发起 → 双方冻结
    r = client.post(
        "/api/miniapp/transfers",
        json={
            "source_child_id": src["id"],
            "target_child_id": tgt["id"],
        },
        headers=mini,
    )
    assert r.status_code == 200, r.text
    tid = r.json()["id"]
    frozen = client.post(
        "/api/admin/circulation/borrow",
        json={
            "child_id": tgt["id"],
            "isbn": "9781000000011",
        },
        headers=h,
    )
    assert frozen.status_code == 422
    # admin 通过 → 同事务六步
    ok = client.post(
        f"/api/admin/transfers/{tid}/review", json={"approve": True, "remark": "同意"}, headers=h
    )
    assert ok.status_code == 200, ok.text
    from backend.database import get_session
    from backend.domain.identity.models import Child

    db = get_session()
    s = db.query(Child).filter(Child.id == src["id"]).first()
    t = db.query(Child).filter(Child.id == tgt["id"]).first()
    assert s.member_status == "withdrawn"
    assert s.operation_locked == 0
    assert t.member_status == "formal"
    assert t.operation_locked == 0
    assert t.member_expire == s.member_expire  # 到期日继承
    db.close()
    # 押金退款自动生成（src 无押金 → 无申请；tgt 自己缴押金才能借）
    # tgt 借书需要自己押金
    no_dep = client.post(
        "/api/admin/circulation/borrow",
        json={
            "child_id": tgt["id"],
            "isbn": "9781000000011",
        },
        headers=h,
    )
    assert no_dep.status_code == 422  # 押金未缴纳


def test_transfer_with_deposit_auto_refund(client: TestClient):
    """转出方有押金 → 通过时自动发起押金退款申请。"""
    h = _h(client)
    p, [src, tgt], mini = _mk_parent_with_children(client, h, "13800001004", ["转出", "受让"])
    _pay(client, h, src["id"], "formal_fee")
    do = client.post(f"/api/admin/deposits/children/{src['id']}/orders", headers=h).json()
    client.post(
        f"/api/admin/orders/{do['order_id']}/confirm-payment",
        json={"pay_method": "scan"},
        headers=h,
    )
    r = client.post(
        "/api/miniapp/transfers",
        json={
            "source_child_id": src["id"],
            "target_child_id": tgt["id"],
        },
        headers=mini,
    )
    ok = client.post(
        f"/api/admin/transfers/{r.json()['id']}/review", json={"approve": True}, headers=h
    )
    assert ok.status_code == 200
    pend = client.get("/api/admin/refund-requests?status=pending", headers=h).json()
    dep_req = [x for x in pend if x["kind"] == "deposit" and x["child_id"] == src["id"]]
    assert len(dep_req) == 1


def test_transfer_timeout_expired(client: TestClient):
    h = _h(client)
    p, [src, tgt], mini = _mk_parent_with_children(
        client, h, "13800001005", ["超时转出", "超时受让"]
    )
    _pay(client, h, src["id"], "formal_fee")
    r = client.post(
        "/api/miniapp/transfers",
        json={
            "source_child_id": src["id"],
            "target_child_id": tgt["id"],
        },
        headers=mini,
    )
    tid = r.json()["id"]
    # 配置超时 1 分钟 + 把 expires_at 拨到过去 → 列表访问触发惰性过期
    client.put(
        "/api/admin/configs/transfer_review_timeout_hours",
        json={"value": "1", "reason": "测试"},
        headers=h,
    )
    from backend.database import get_session
    from backend.domain.identity.models import TransferRequest

    db = get_session()
    tr = db.query(TransferRequest).filter(TransferRequest.id == tid).first()
    tr.expires_at = datetime.now() - timedelta(minutes=1)
    db.commit()
    db.close()
    lst = client.get("/api/miniapp/transfers", headers=mini).json()
    mine = [x for x in lst if x["id"] == tid]
    assert mine[0]["status"] == "expired"
    # 双方解锁
    from backend.domain.identity.models import Child

    db = get_session()
    for cid in (src["id"], tgt["id"]):
        ch = db.query(Child).filter(Child.id == cid).first()
        assert ch.operation_locked == 0
    db.close()
    # 过期后审核被拒
    late = client.post(f"/api/admin/transfers/{tid}/review", json={"approve": True}, headers=h)
    assert late.status_code == 422


def test_transfer_reject_and_cancel(client: TestClient):
    h = _h(client)
    # 拒绝
    p, [src, tgt], mini = _mk_parent_with_children(client, h, "13800001006", ["拒转出", "拒受让"])
    _pay(client, h, src["id"], "formal_fee")
    r = client.post(
        "/api/miniapp/transfers",
        json={
            "source_child_id": src["id"],
            "target_child_id": tgt["id"],
        },
        headers=mini,
    )
    rej = client.post(
        f"/api/admin/transfers/{r.json()['id']}/review",
        json={
            "approve": False,
            "remark": "不符合条件",
        },
        headers=h,
    )
    assert rej.status_code == 200
    from backend.database import get_session
    from backend.domain.identity.models import Child

    db = get_session()
    s = db.query(Child).filter(Child.id == src["id"]).first()
    assert s.member_status == "formal" and s.operation_locked == 0
    db.close()
    # 家长撤销
    p2, [src2, tgt2], mini2 = _mk_parent_with_children(
        client, h, "13800001007", ["撤转出", "撤受让"]
    )
    _pay(client, h, src2["id"], "formal_fee")
    r2 = client.post(
        "/api/miniapp/transfers",
        json={
            "source_child_id": src2["id"],
            "target_child_id": tgt2["id"],
        },
        headers=mini2,
    )
    cx = client.post(f"/api/miniapp/transfers/{r2.json()['id']}/cancel", headers=mini2)
    assert cx.status_code == 200
    assert cx.json()["status"] == "cancelled"


def test_observation_report_upload_and_view(client: TestClient):
    h = _h(client)
    p, [c], mini = _mk_parent_with_children(client, h, "13800001008", ["观察孩"])
    _pay(client, h, c["id"], "observation_fee")
    # 上传 2 张图
    png = b"\x89PNG\r\n\x1a\n" + b"\x00" * 100
    r = client.post(
        f"/api/admin/children/{c['id']}/observation-reports",
        data={"remark": "第一阶段评估：听力优秀"},
        files=[
            ("files", ("r1.png", io.BytesIO(png), "image/png")),
            ("files", ("r2.png", io.BytesIO(png), "image/png")),
        ],
        headers=h,
    )
    assert r.status_code == 200, r.text
    assert len(r.json()["images"]) == 2
    # 家长端可见
    lst = client.get(f"/api/miniapp/observation-reports?child_id={c['id']}", headers=mini).json()
    assert len(lst) == 1
    assert lst[0]["remark"] == "第一阶段评估：听力优秀"
    assert len(lst[0]["images"]) == 2
    # 非图片被拒
    r2 = client.post(
        f"/api/admin/children/{c['id']}/observation-reports",
        data={"remark": ""},
        files=[("files", ("bad.txt", io.BytesIO(b"xxx"), "text/plain"))],
        headers=h,
    )
    assert r2.status_code == 422
