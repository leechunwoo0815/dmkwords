# tests/unit/test_audit5_p1_batch4_20261018.py — 第五轮大审查·P1 批量（子批 4：押金 refunded 复位写口）
"""P1-1：押金 `refunded` 复位只挂在"观察期费"路径 → 走**年费**或**转让**重新入会的孩子永远缴不了押金。

根因：`reset_refunded_on_rejoin`（`billing/service.py`）唯一调用点在
`order_service._open_membership`（**观察期费专用**）；年费分支（`TYPE_FORMAL`）与
转让受让方（`transfer_service` 直接 `target.member_status = FORMAL`）都没调 →
押金停在 `refunded`，而四条缴款入口（在线/管理端建单/补缴/小程序补缴）全拒 `refunded`，
`deposits.child_id` 又是单列唯一（建不出第二行）→ **有效会员却没有任何押金缴纳入口**。

修法：把复位提升为**会员态写口**的统一钩子——`DepositService.on_member_status_changed`
在 `none/expired/withdrawn → observation/formal` 时复位；三处写口（`order_service._transition_member`、
`identity/service._transition`、`transfer_service` 受让方）统一调用。

两条用例逐条**去修复即红**（变异自证见 docs/09 第七十五批）：
  1. 年费重新入会：退会（押金 `refunded`）→ 买年费 → 押金**复位**、可再缴（修前 422）；
  2. 转让受让方：受让方曾退会（押金 `refunded`）→ 转让审核通过 → 押金**复位**、可再缴（修前 422）。
"""

from __future__ import annotations

from fastapi.testclient import TestClient

from tests.unit.test_audit4_p2_batch4_20261017 import _transfer_ready
from tests.unit.test_p1_audit_20261015 import _db
from tests.unit.test_r313_guards import _withdrawn_child


def _settle_deposit_refund(client: TestClient, h: dict, child_id: int) -> None:
    """执行退会结算生成的**押金退款单** → 押金账户落到 `refunded`（钱已退给家长）。

    只做「会员费退款」还不够：退会结算会额外生成一张 `kind=deposit` 的退款单，
    押金要等这张单执行完才是 `refunded`（实测：仅执行会员费退款后押金停在 `refunding`）。
    """
    from backend.domain.identity.models import RefundRequest

    with _db() as db:
        rr = (
            db.query(RefundRequest)
            .filter(
                RefundRequest.child_id == child_id,
                RefundRequest.kind == RefundRequest.KIND_DEPOSIT,
                RefundRequest.is_deleted == 0,
            )
            .order_by(RefundRequest.id.desc())
            .first()
        )
    assert rr is not None, "退会结算应生成押金退款单"
    client.post(
        f"/api/admin/refund-requests/{rr.id}/review",
        json={"approve": True, "remark": "同意"},
        headers=h,
    )
    ex = client.post(
        f"/api/admin/refund-requests/{rr.id}/execute",
        json={"success": True, "remark": "押金已退"},
        headers=h,
    )
    assert ex.status_code == 200, ex.text


def _h(client: TestClient) -> dict:
    r = client.post("/api/admin/login", json={"username": "admin", "password": "dmkwords123"})
    assert r.status_code == 200, r.text
    return {"Authorization": f"Bearer {r.json()['token']}"}


def _deposit_row(child_id: int):
    from backend.domain.billing.models import Deposit

    with _db() as db:
        return (
            db.query(Deposit).filter(Deposit.child_id == child_id, Deposit.is_deleted == 0).first()
        )


def _deposit_status(client: TestClient, h: dict, child_id: int) -> str:
    r = client.get(f"/api/admin/deposits/children/{child_id}", headers=h)
    assert r.status_code == 200, r.text
    return r.json()["status"]


def test_formal_fee_rejoin_resets_refunded_deposit(client: TestClient):
    """年费重新入会 → 押金账户复位为未缴 → 可再次缴纳（修前：`refunded` 死锁，建单 422）。"""
    h = _h(client)
    c, _mini = _withdrawn_child(client, h, "13800007711", "年费重入会孩")
    _settle_deposit_refund(client, h, c["id"])

    dep = _deposit_row(c["id"])
    assert dep is not None and dep.status == "refunded", "前置：退会后押金应为 refunded"
    assert (
        client.post(f"/api/admin/deposits/children/{c['id']}/orders", headers=h).status_code == 422
    ), "前置：refunded 状态下建押金单应被拒"

    o = client.post(
        "/api/admin/orders", json={"child_id": c["id"], "order_type": "formal_fee"}, headers=h
    ).json()
    r = client.post(
        f"/api/admin/orders/{o['id']}/confirm-payment", json={"pay_method": "scan"}, headers=h
    )
    assert r.status_code == 200, r.text

    assert _deposit_status(client, h, c["id"]) == "unpaid", (
        "年费重新入会后押金应复位为 unpaid——RED=复位只挂在观察期费路径（`_open_membership`）"
    )
    again = client.post(f"/api/admin/deposits/children/{c['id']}/orders", headers=h)
    assert again.status_code == 200, f"复位后必须能再缴押金：{again.status_code} {again.text}"


def test_transfer_target_rejoin_resets_refunded_deposit(client: TestClient):
    """转让受让方曾退会（押金 refunded）→ 审核通过后押金复位、可再缴（修前 422 死锁）。"""
    h = _h(client)
    p, src, tgt, mini = _transfer_ready(client, h, "13800007712")

    # 让受让方先入会（年费 + 押金）再退会 → 押金置 refunded
    o = client.post(
        "/api/admin/orders", json={"child_id": tgt["id"], "order_type": "formal_fee"}, headers=h
    ).json()
    client.post(
        f"/api/admin/orders/{o['id']}/confirm-payment", json={"pay_method": "scan"}, headers=h
    )
    d = client.post(f"/api/admin/deposits/children/{tgt['id']}/orders", headers=h).json()
    client.post(
        f"/api/admin/orders/{d['order_id']}/confirm-payment", json={"pay_method": "scan"}, headers=h
    )
    rr = client.post(
        "/api/miniapp/refund-requests",
        json={"child_id": tgt["id"], "order_id": o["id"], "reason": "不学了"},
        headers=mini,
    ).json()
    client.post(
        f"/api/admin/refund-requests/{rr['id']}/review",
        json={"approve": True, "remark": "同意"},
        headers=h,
    )
    client.post(
        f"/api/admin/refund-requests/{rr['id']}/execute",
        json={"success": True, "remark": "已退"},
        headers=h,
    )
    _settle_deposit_refund(client, h, tgt["id"])
    dep = _deposit_row(tgt["id"])
    assert dep is not None and dep.status == "refunded", "前置：受让方退会后押金应为 refunded"
    assert (
        client.post(f"/api/admin/deposits/children/{tgt['id']}/orders", headers=h).status_code
        == 422
    ), "前置：refunded 状态下建押金单应被拒"

    applied = client.post(
        "/api/miniapp/transfers",
        json={"source_child_id": src["id"], "target_child_id": tgt["id"]},
        headers=mini,
    )
    assert applied.status_code == 200, applied.text
    rev = client.post(
        f"/api/admin/transfers/{applied.json()['id']}/review",
        json={"approve": True, "remark": "同意转让"},
        headers=h,
    )
    assert rev.status_code == 200, rev.text

    assert _deposit_status(client, h, tgt["id"]) == "unpaid", (
        "受让方接权益后押金应复位为 unpaid——RED=转让直接写 `member_status = FORMAL` 绕过写口"
    )
    again = client.post(f"/api/admin/deposits/children/{tgt['id']}/orders", headers=h)
    assert again.status_code == 200, f"复位后必须能再缴押金：{again.status_code} {again.text}"
