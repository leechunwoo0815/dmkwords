# tests/unit/test_audit4_p2_batch5_20261017.py — 第四轮大审查·P2 批量（子批 5：资金两连）回归
"""第六十九批（子批 5）：P2-1 押金结算覆盖写 + 金额取当前配置 · P2-2 追责台账记实际扣除额。

每条用例都是报告里那条缺陷的复现，逐条**去修复即红**（变异自证见 docs/09 第六十九轮）。
"""

from decimal import Decimal

from fastapi.testclient import TestClient

from tests.unit.test_wm10_concurrency import _family, _h


def _member_child(client, h, phone: str, name: str = "押金孩"):
    _p, c, _mini = _family(client, h, phone, name=name)
    o = client.post(
        "/api/admin/orders",
        json={"child_id": c["id"], "order_type": "observation_fee"},
        headers=h,
    ).json()
    client.post(
        f"/api/admin/orders/{o['id']}/confirm-payment", json={"pay_method": "scan"}, headers=h
    )
    return c


def _deposit_order(client, h, child_id: int) -> dict:
    return client.post(f"/api/admin/deposits/children/{child_id}/orders", headers=h).json()


def _confirm(client, h, order_id: int):
    return client.post(
        f"/api/admin/orders/{order_id}/confirm-payment", json={"pay_method": "scan"}, headers=h
    )


def _deposit(client, h, child_id: int) -> dict:
    return client.get(f"/api/admin/deposits/children/{child_id}", headers=h).json()


def _ledgers(client, h, child_id: int) -> list[dict]:
    return client.get(f"/api/admin/deposits/children/{child_id}/ledgers", headers=h).json()


# ---------- P2-1：双击建出的第二笔押金单不许覆盖回标准额 ----------


def test_duplicate_deposit_order_rejected_after_deduction(client: TestClient):
    """两笔押金单 + 中间扣一次赔偿：确认第二笔必须 422，余额保持扣除后的值。

    RED（还原修复：闸只拦 paid 状态）：第二笔确认通过 → available 被覆盖回标准额，
    台账（pay×2 − deduct）与余额对不上（家庭多付）。
    """
    h = _h(client)
    c = _member_child(client, h, "13800008901")
    o1 = _deposit_order(client, h, c["id"])
    o2 = _deposit_order(client, h, c["id"])  # 双击/重试建出的第二笔
    assert o1["order_id"] != o2["order_id"]
    assert _confirm(client, h, o1["order_id"]).status_code == 200

    # 中间发生一次赔偿扣除（状态从 paid 变 partially_deducted）
    ded = client.post(
        f"/api/admin/deposits/children/{c['id']}/deduct",
        json={"amount": "200", "reason": "书页撕坏赔偿"},
        headers=h,
    )
    assert ded.status_code == 200, ded.text
    assert _deposit(client, h, c["id"])["available_amount"] == "1000.00"

    r2 = _confirm(client, h, o2["order_id"])
    assert r2.status_code == 422, (
        f"重复缴纳必须拒绝（不许覆盖回标准额）：{r2.status_code} {r2.text}"
    )
    assert "押金已缴纳" in r2.json()["detail"]

    dep = _deposit(client, h, c["id"])
    assert dep["available_amount"] == "1000.00", f"余额被覆盖回标准额 = 家庭多付：{dep}"
    assert dep["deducted_amount"] == "200.00"
    assert dep["status"] == "partially_deducted"

    # 台账可回推余额：pay − deduct = available（"台账即资金事实"）
    led = _ledgers(client, h, c["id"])
    pay = sum(Decimal(x["amount"]) for x in led if x["entry_type"] == "pay")
    deduct = sum(Decimal(x["amount"]) for x in led if x["entry_type"] == "deduct")
    assert pay - deduct == Decimal(dep["available_amount"]) == Decimal("1000.00"), (
        pay,
        deduct,
        dep,
    )


# ---------- P2-1（第二半）：金额取订单实收，不取确认时的当前配置 ----------


def test_deposit_amount_uses_order_amount_not_current_config(client: TestClient):
    """建单后改配置：`dep.amount` 与台账都必须等于**订单实收**（原先取当前配置 → 实收≠入账）。"""
    h = _h(client)
    c = _member_child(client, h, "13800008902")
    o = _deposit_order(client, h, c["id"])  # 按当前配置 1200 建单
    client.put(
        "/api/admin/configs/deposit_amount",
        json={"value": "1500", "reason": "P2-1 回归：建单后改配置"},
        headers=h,
    )
    try:
        assert _confirm(client, h, o["order_id"]).status_code == 200
        dep = _deposit(client, h, c["id"])
        assert dep["amount"] == "1200.00", f"本金必须=订单实收：{dep}"
        assert dep["available_amount"] == "1200.00"
        led = _ledgers(client, h, c["id"])
        pay_rows = [x for x in led if x["entry_type"] == "pay"]
        assert len(pay_rows) == 1 and Decimal(pay_rows[0]["amount"]) == Decimal("1200.00")
    finally:
        client.put(
            "/api/admin/configs/deposit_amount",
            json={"value": "1200", "reason": "P2-1 回归复原"},
            headers=h,
        )


# ---------- P2-2：追责台账记实际扣除额（超扣场景可回推） ----------


def test_deduct_ledger_records_actual_deduction(client: TestClient):
    """押金 1200、赔偿 2000：台账记 1200（实扣）+ 原因里保留索赔全额与待结清。

    RED（还原修复：台账记索赔全额）：amount=2000 / balance_after=0 → 回推余额 −800。
    """
    h = _h(client)
    c = _member_child(client, h, "13800008903")
    o = _deposit_order(client, h, c["id"])
    assert _confirm(client, h, o["order_id"]).status_code == 200

    r = client.post(
        f"/api/admin/deposits/children/{c['id']}/deduct",
        json={"amount": "2000", "reason": "高价书遗失赔偿"},
        headers=h,
    )
    assert r.status_code == 200, r.text
    dep = r.json()
    assert dep["available_amount"] == "0.00"
    assert Decimal(dep["unpaid_balance"]) == Decimal("800.00")

    led = _ledgers(client, h, c["id"])
    deduct_rows = [x for x in led if x["entry_type"] == "deduct"]
    assert len(deduct_rows) == 1
    row = deduct_rows[0]
    assert Decimal(row["amount"]) == Decimal("1200.00"), f"台账必须记实扣额：{row}"
    assert Decimal(row["balance_after"]) == Decimal("0.00")
    assert "索赔 2000" in row["reason"] and "转待结清 800" in row["reason"], row

    # 回推：pay − deduct == available（旧口径会得 −800）
    pay = sum(Decimal(x["amount"]) for x in led if x["entry_type"] == "pay")
    deduct = sum(Decimal(x["amount"]) for x in led if x["entry_type"] == "deduct")
    assert pay - deduct == Decimal(dep["available_amount"]) == Decimal("0.00")


def test_deduct_ledger_clean_case_unchanged(client: TestClient):
    """不超扣时（扣 200）台账 amount 仍是 200、reason 不加赘述（判据未放宽）。"""
    h = _h(client)
    c = _member_child(client, h, "13800008904")
    o = _deposit_order(client, h, c["id"])
    _confirm(client, h, o["order_id"])
    r = client.post(
        f"/api/admin/deposits/children/{c['id']}/deduct",
        json={"amount": "200", "reason": "轻微损坏"},
        headers=h,
    )
    assert r.status_code == 200, r.text
    led = _ledgers(client, h, c["id"])
    row = next(x for x in led if x["entry_type"] == "deduct")
    assert Decimal(row["amount"]) == Decimal("200.00")
    assert row["reason"] == "轻微损坏"
