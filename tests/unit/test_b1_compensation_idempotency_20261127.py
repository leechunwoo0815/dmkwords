# tests/unit/test_b1_compensation_idempotency_20261127.py — B1-5 赔偿登记幂等（2026-11-26 双审查）
"""问题：押金赔偿扣款 `deduct_for_compensation` 只有**押金行锁**防并发，挡不住**同一请求重放**
（双击提交 / 超时重试 / 前端重发）——那会实打实扣两次钱，而且台账上两笔都"合法"。

修法：`deposit_ledgers.client_request_id` 幂等键（唯一索引，MySQL 允许多个 NULL）：
  · 带 key 的重放 → 直接返回当前押金，不再扣款；
  · 并发同 key → 唯一索引只放行一个，后到者按"重放"处理（不 500、不二次扣款）；
  · 不带 key → 保持原行为（历史调用方零改动），但 UI 应尽快带上 key。

判据（去修复即红）：同一 key 连打两次，可用余额只减一次、台账只有一行。
"""

from __future__ import annotations

from decimal import Decimal

from backend.domain.billing.models import Deposit, DepositLedger


def _child_with_deposit(client, admin_headers, phone: str) -> int:
    """建家长/孩子 → 押金单 → 确认收款（押金 1200 全额到账）。"""
    p = client.post(
        "/api/admin/members/parents",
        json={"name": "赔偿家长", "phone": phone},
        headers=admin_headers,
    ).json()
    c = client.post(
        f"/api/admin/members/parents/{p['id']}/children",
        json={"name": "赔偿孩"},
        headers=admin_headers,
    ).json()
    d = client.post(f"/api/admin/deposits/children/{c['id']}/orders", headers=admin_headers)
    assert d.status_code == 200, d.text
    order_id = d.json().get("id") or d.json()["order_id"]
    pay = client.post(
        f"/api/admin/orders/{order_id}/confirm-payment",
        json={"pay_method": "scan"},
        headers=admin_headers,
    )
    assert pay.status_code == 200, pay.text
    return c["id"]


def _deduct(client, admin_headers, child_id: int, *, key: str | None, amount: str = "200.00"):
    payload = {"amount": amount, "reason": "图书污损赔偿"}
    if key is not None:
        payload["client_request_id"] = key
    return client.post(
        f"/api/admin/deposits/children/{child_id}/deduct", json=payload, headers=admin_headers
    )


def test_same_key_does_not_deduct_twice(client, admin_headers, db):
    """同一幂等键重放 → 只扣一次（这是 B1-5 的核心判据）。"""
    child_id = _child_with_deposit(client, admin_headers, "13800009901")

    first = _deduct(client, admin_headers, child_id, key="comp-key-1")
    assert first.status_code == 200, first.text
    after_first = Decimal(first.json()["available_amount"])
    assert after_first == Decimal("1000.00"), first.json()

    second = _deduct(client, admin_headers, child_id, key="comp-key-1")
    assert second.status_code == 200, second.text
    assert Decimal(second.json()["available_amount"]) == after_first, (
        f"同键重放又扣了一次：{after_first} -> {second.json()['available_amount']}"
    )

    db.expire_all()
    dep = db.query(Deposit).filter(Deposit.child_id == child_id).first()
    assert dep.deducted_amount == Decimal("200.00"), dep.deducted_amount
    rows = db.query(DepositLedger).filter(DepositLedger.deposit_id == dep.id).all()
    deducts = [r for r in rows if r.entry_type == DepositLedger.ENTRY_DEDUCT]
    assert len(deducts) == 1, (
        f"台账不该出现两笔扣款：{[(r.amount, r.client_request_id) for r in deducts]}"
    )
    assert deducts[0].client_request_id == "comp-key-1"


def test_different_keys_deduct_separately(client, admin_headers, db):
    """对照组：不同键是两次真实扣款（别把幂等做成"一个孩子只能扣一次"）。"""
    child_id = _child_with_deposit(client, admin_headers, "13800009902")
    assert _deduct(client, admin_headers, child_id, key="comp-key-2a").status_code == 200
    second = _deduct(client, admin_headers, child_id, key="comp-key-2b")
    assert second.status_code == 200, second.text
    assert Decimal(second.json()["available_amount"]) == Decimal("800.00"), second.json()[
        "available_amount"
    ]


def test_without_key_keeps_old_behaviour(client, admin_headers, db):
    """不带键仍按原行为逐次扣款（历史调用方零改动），并把这条局限写清在用例里。"""
    child_id = _child_with_deposit(client, admin_headers, "13800009903")
    assert _deduct(client, admin_headers, child_id, key=None, amount="100.00").status_code == 200
    twice = _deduct(client, admin_headers, child_id, key=None, amount="100.00")
    assert twice.status_code == 200, twice.text
    assert Decimal(twice.json()["available_amount"]) == Decimal("1000.00"), twice.json()[
        "available_amount"
    ]


def test_key_reuse_returns_current_state_not_error(client, admin_headers):
    """重放不是错误：返回当前押金状态（前端重试无需特殊处理，也不会重复扣）。"""
    child_id = _child_with_deposit(client, admin_headers, "13800009904")
    _deduct(client, admin_headers, child_id, key="comp-key-4")
    replay = _deduct(client, admin_headers, child_id, key="comp-key-4")
    assert replay.status_code == 200
    body = replay.json()
    assert body["child_id"] == child_id
    assert Decimal(body["available_amount"]) == Decimal("1000.00")
