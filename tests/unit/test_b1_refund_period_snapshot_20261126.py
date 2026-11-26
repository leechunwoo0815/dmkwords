# tests/unit/test_b1_refund_period_snapshot_20261126.py — B1-3 退款期限按订单快照冻结（2026-11-26 双审查）
"""问题（审查② 补充 P1-5）：`refund_rules.refundable_amount` 的分母直接读**当前配置**
（`observation_period_days` / `formal_period_days`）。于是把观察期从 30 天改成 60 天，
**历史订单**的退款比例会跟着变——"当时按 30 天卖、按 30 天退"的账再也还原不了，
而且对账只能看到"金额不对"，看不到为什么。

修法：下单时把期限冻进 `orders.period_days`（B1-3 迁移 `c3d9a1f7b2e4`），退款分母读快照；
历史订单（快照上线前）留 NULL，退回读当前配置（已知局限，见迁移注释）。

判据（去修复即红）：
  1. 建单时写入快照（观察期/年费=当时代码读到的配置；其它类型为 None）；
  2. **改配置后**，历史订单的可退金额仍按原快照算（这正是原实现会算错的地方）。
"""

from __future__ import annotations

from datetime import datetime, timedelta
from decimal import Decimal

from backend.domain.identity import refund_rules
from backend.domain.identity.models import Order


def _set_config(client, admin_headers, key: str, value: str) -> None:
    r = client.put(
        f"/api/admin/configs/{key}",
        json={"value": value, "reason": "B1-3 用例：改期限口径"},
        headers=admin_headers,
    )
    # 值没变时接口按设计返回 422「新值与当前值相同，无需修改」——用例里重复设同一值属正常
    if r.status_code != 200:
        assert "无需修改" in r.text, r.text


def test_order_freezes_period_at_creation(client, admin_headers, db):
    """建单即冻结：观察期单的 `period_days` 等于下单时的配置值。"""
    _set_config(client, admin_headers, "observation_period_days", "30")
    p = client.post(
        "/api/admin/members/parents",
        json={"name": "期限家长", "phone": "13800008801"},
        headers=admin_headers,
    ).json()
    c = client.post(
        f"/api/admin/members/parents/{p['id']}/children",
        json={"name": "期限孩"},
        headers=admin_headers,
    ).json()
    o = client.post(
        "/api/admin/orders",
        json={"child_id": c["id"], "order_type": "observation_fee"},
        headers=admin_headers,
    )
    assert o.status_code == 200, o.text
    row = db.query(Order).filter(Order.id == o.json()["id"]).first()
    assert row is not None and row.period_days == 30, f"建单未冻结期限：{row.period_days}"


def test_refund_uses_snapshot_after_config_change(client, admin_headers, db, monkeypatch):
    """**核心判据**：改配置不得影响历史订单的可退金额。

    观察期 3000 元、已用 10 天：
      · 按 30 天口径 → 余 20 天 → 2000.00（下单时谈好的口径）
      · 按 60 天口径 → 余 50 天 → 2500.00（配置改后"跟上"是错的行为）
    """
    _set_config(client, admin_headers, "observation_period_days", "30")
    p = client.post(
        "/api/admin/members/parents",
        json={"name": "口径家长", "phone": "13800008802"},
        headers=admin_headers,
    ).json()
    c = client.post(
        f"/api/admin/members/parents/{p['id']}/children",
        json={"name": "口径孩"},
        headers=admin_headers,
    ).json()
    created = client.post(
        "/api/admin/orders",
        json={"child_id": c["id"], "order_type": "observation_fee"},
        headers=admin_headers,
    ).json()

    row = db.query(Order).filter(Order.id == created["id"]).first()
    row.status = Order.STATUS_PAID
    row.paid_at = datetime.now() - timedelta(days=10)
    row.amount = Decimal("3000.00")
    db.commit()

    # 冻住"现在"：`days_used` 按整日截断，用例跑着跑着跨过整日边界就会今天差一天（实测抖过一次）。
    # 只 patch 口径模块里的 datetime，业务代码其余部分不受影响。
    fixed_now = datetime(2026, 11, 27, 12, 0, 0)

    class _FrozenDatetime(datetime):
        @classmethod
        def now(cls, tz=None):  # noqa: ARG003 - 签名对齐 datetime.now
            return fixed_now

    monkeypatch.setattr(refund_rules, "datetime", _FrozenDatetime)
    row.paid_at = fixed_now - timedelta(days=10)
    db.commit()

    before = refund_rules.refundable_amount(db, row)
    # 断言"按比例"而不是硬编码金额：`days_used` 按整日截断（10.0001 天与 9.9999 天差一天），
    # 硬编码 2000.00 会随秒级时序抖动。这里只要求它是按 30 天口径折算出来的部分退款。
    assert Decimal("0") < before < Decimal("3000.00"), before
    expected_if_30_days = Decimal("2000.00")
    assert abs(before - expected_if_30_days) <= Decimal("100.00"), (
        f"基准值应接近 30 天口径的 {expected_if_30_days}，实际 {before}（差一天 = 100 元）"
    )

    # 事后把口径改成 60 天（真实运营会这么干：活动期延长观察期）
    _set_config(client, admin_headers, "observation_period_days", "60")

    db.expire_all()
    row = db.query(Order).filter(Order.id == created["id"]).first()
    after = refund_rules.refundable_amount(db, row)
    # 核心不变式：改配置**不得**改变历史订单的可退金额（修前会从 ~2000 跳到 ~2500）
    assert after == before, (
        f"改配置后历史订单的可退金额必须仍按快照算：改前 {before} / 改后 {after}"
        "（修前分母跟着配置漂，会变成按 60 天口径的金额）"
    )
    # 对照组：配置确实改了（否则本条断言证明不了什么）
    assert refund_rules.period_days(db, Order.TYPE_OBSERVATION) == 60


def test_non_member_orders_have_no_period_snapshot(client, admin_headers, db):
    """活动费/自定义单不吃期限口径：快照为 None（免得日后有人误读这一列）。"""
    p = client.post(
        "/api/admin/members/parents",
        json={"name": "活动家长", "phone": "13800008803"},
        headers=admin_headers,
    ).json()
    c = client.post(
        f"/api/admin/members/parents/{p['id']}/children",
        json={"name": "活动孩"},
        headers=admin_headers,
    ).json()
    o = client.post(
        "/api/admin/orders",
        json={
            "child_id": c["id"],
            "order_type": "custom",
            "amount": "88.00",
            "remark": "B1-3 用例：自定义单",
        },
        headers=admin_headers,
    )
    assert o.status_code == 200, o.text
    row = db.query(Order).filter(Order.id == o.json()["id"]).first()
    assert row.period_days is None, row.period_days


def test_legacy_order_without_snapshot_falls_back_to_config(db):
    """历史订单（快照列之前创建）period_days 为 NULL → 退回当前配置，不许 500。"""
    order = Order(
        order_no="DMKLEGACY0001",
        order_type=Order.TYPE_OBSERVATION,
        parent_id=1,
        child_id=1,
        amount=Decimal("3000.00"),
        status=Order.STATUS_PAID,
        paid_at=datetime.now() - timedelta(days=10),
        period_days=None,
    )
    db.add(order)
    db.commit()
    amount = refund_rules.refundable_amount(db, order)
    assert amount > 0, amount


def test_membership_length_uses_snapshot_not_current_config(client, admin_headers, db):
    """开通时长与退款分母必须同一口径：下单(30) → 改配置(60) → 确认收款 → 仍按 30 天开通。

    修前：`_open_membership` 读当前配置 → 孩子按 60 天开通，而退款分母是订单快照 30 天，
    "卖 30 退 60"或反之必然有一边撒谎。
    """
    from backend.domain.identity.models import Child

    _set_config(client, admin_headers, "observation_period_days", "30")
    p = client.post(
        "/api/admin/members/parents",
        json={"name": "开通家长", "phone": "13800008804"},
        headers=admin_headers,
    ).json()
    c = client.post(
        f"/api/admin/members/parents/{p['id']}/children",
        json={"name": "开通孩"},
        headers=admin_headers,
    ).json()
    created = client.post(
        "/api/admin/orders",
        json={"child_id": c["id"], "order_type": "observation_fee"},
        headers=admin_headers,
    ).json()

    # 下单之后、收款之前改口径（真实运营会发生：活动期调整）
    _set_config(client, admin_headers, "observation_period_days", "60")

    r = client.post(
        f"/api/admin/orders/{created['id']}/confirm-payment",
        json={"pay_method": "scan"},
        headers=admin_headers,
    )
    assert r.status_code == 200, r.text

    db.expire_all()
    child = db.query(Child).filter(Child.id == c["id"]).first()
    assert child.member_status == Child.MEMBER_OBSERVATION
    span = (child.member_expire - child.member_start).days
    assert span == 30, f"开通时长必须按订单快照 30 天，实际 {span} 天（修前是 60）"
