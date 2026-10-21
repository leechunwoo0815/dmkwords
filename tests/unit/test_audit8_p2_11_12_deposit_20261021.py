# tests/unit/test_audit8_p2_11_12_deposit_20261021.py — 第八轮 P2-11 / P2-12（第一百零九批）
"""第八轮大审查 P2 面最后两条（押金），见 `docs/审查报告-全量-第八轮-20261019.md`。

- **P2-11**：押金到账通知**不传去重键**（默认 `"1"` = 同一家长 × 场景 × 对象**终身一条**）
  且文案写死「押金补缴成功」：
  ① 首次缴纳也显示"补缴"（文案错）；
  ② 同一押金行**补缴**时 `deposit_id` 不变（`deposits.child_id` 单列唯一）→ 第二次缴款的
  通知与首缴的 `(parent, scene, ref_type, ref_id, dedup_key)` 五元组完全相同 → 被唯一索引
  当重复**静默吞掉**（家长永远收不到补缴到账）。
  修复口径：去重键 = **付款订单 id**（一笔订单一条通知；补缴是另一笔订单 → 键不同），
  文案按 `is_supplement` 分「押金缴纳成功 / 押金补缴成功」。
- **P2-12**：管理端押金列表 `outerjoin(Order, 已付押金单)` → 孩子**重新入会**后有第二笔
  已付押金单时 join 产出多行 → 同一孩子重复出现、`total` 虚高（分页错位）。
  该 Order 调用方（router）从未使用（响应体只有押金与孩子）→ 直接去掉 join。

每条都做过「去修复即红」的变异自证（红线 147）：失败形态写在各自 docstring 的 RED 段。
"""

from __future__ import annotations

from decimal import Decimal

from fastapi.testclient import TestClient

from tests.unit.test_wm9_activity import _h

SCENE = "money.deposit_paid"


def _db():
    from backend.database import get_session

    return get_session()


def _family(client, h, phone, name):
    """家长 + 孩子（不缴观察期费——押金缴纳不要求会员态，判据面更窄）。"""
    p = client.post(
        "/api/admin/members/parents", json={"name": "家长", "phone": phone}, headers=h
    ).json()
    c = client.post(
        f"/api/admin/members/parents/{p['id']}/children", json={"name": name}, headers=h
    ).json()
    return p, c


def _pay_deposit(client, h, child_id) -> int:
    """建押金单 + 确认收款；返回订单 id（通知去重键的业务身份）。"""
    o = client.post(f"/api/admin/deposits/children/{child_id}/orders", headers=h).json()
    r = client.post(
        f"/api/admin/orders/{o['order_id']}/confirm-payment", json={"pay_method": "scan"}, headers=h
    )
    assert r.status_code == 200, r.text
    return o["order_id"]


def _notif_rows(parent_id: int) -> list[tuple[str, str, str]]:
    """(title, content, dedup_key) 三元组，按 id 升序 = 发送先后。"""
    from backend.common.notification_models import Notification

    with _db() as db:
        rows = (
            db.query(Notification.title, Notification.content, Notification.dedup_key)
            .filter(
                Notification.parent_id == parent_id,
                Notification.scene == SCENE,
                Notification.is_deleted == 0,
            )
            .order_by(Notification.id)
            .all()
        )
    return [tuple(r) for r in rows]


def _insert_second_paid_deposit_order(child_id: int) -> int:
    """直落库造第二笔**已付**押金单（重新入会后的历史态；接口层已封：非 unpaid 一律拒）。"""
    from backend.domain.identity.models import Child, Order
    from backend.domain.identity.order_service import OrderService

    with _db() as db:
        child = db.get(Child, child_id)
        o = Order(
            order_no=OrderService.new_order_no(),
            order_type=Order.TYPE_DEPOSIT,
            parent_id=child.parent_id,
            child_id=child.id,
            amount=Decimal("1200.00"),
            status=Order.STATUS_PAID,
            remark="探针：重新入会后第二笔已付押金单",
        )
        db.add(o)
        db.commit()
        return o.id


# ---------- P2-11 ----------


def test_first_deposit_notification_wording_and_key(client: TestClient):
    """P2-11①：**首次缴纳**的到账通知 = 「押金缴纳成功」，去重键带付款订单号。

    RED（还原修复：文案写死"押金补缴成功" + 不传 dedup_key）：
      `title == "押金缴纳成功"` 与 `dedup_key == f"pay-order-{oid}"` 两条同时失败。
    """
    h = _h(client)
    p, c = _family(client, h, "13900002011", "首缴文案孩")
    oid = _pay_deposit(client, h, c["id"])

    rows = _notif_rows(p["id"])
    assert len(rows) == 1, rows
    title, content, dedup_key = rows[0]
    assert title == "押金缴纳成功", title
    assert "补缴" not in content, content
    assert dedup_key == f"pay-order-{oid}", dedup_key


def test_supplement_deposit_notification_not_swallowed(client: TestClient):
    """P2-11②：赔偿后**补缴**必须再发一条（同一押金行 deposit_id 不变 → 旧实现被吞）。

    RED（还原修复：不传 dedup_key）：第二次 `send` 的五元组与首缴完全相同
      （同一 parent/scene/ref_type/deposit_id + 默认键 "1"）→ 唯一索引吞掉 →
      通知数停在 1，`len(rows) == 2` 失败（家长永远收不到补缴到账）。
    """
    h = _h(client)
    p, c = _family(client, h, "13900002012", "补缴不吞孩")
    first_oid = _pay_deposit(client, h, c["id"])

    # 赔偿 68 → 押金转「部分扣除」（补缴入口的前置状态）
    r = client.post(
        f"/api/admin/deposits/children/{c['id']}/deduct",
        json={"amount": "68.00", "reason": "探针：赔偿"},
        headers=h,
    )
    assert r.status_code == 200, r.text
    sup = client.post(f"/api/admin/deposits/children/{c['id']}/supplement-orders", headers=h).json()
    r = client.post(
        f"/api/admin/orders/{sup['order_id']}/confirm-payment",
        json={"pay_method": "scan"},
        headers=h,
    )
    assert r.status_code == 200, r.text

    rows = _notif_rows(p["id"])
    assert len(rows) == 2, rows  # ← 旧实现这里只有 1 条（补缴通知被当重复吞掉）
    assert rows[0][2] == f"pay-order-{first_oid}", rows[0]
    title, _content, dedup_key = rows[1]
    assert title == "押金补缴成功", title
    assert dedup_key == f"pay-order-{sup['order_id']}", dedup_key
    assert dedup_key != rows[0][2], "首缴与补缴的去重键必须不同（否则补缴必被吞）"


def test_same_order_replay_stays_single_notification(client: TestClient):
    """P2-11③：**同一笔订单**重复投递事件仍只写一条（业务键不许把幂等改没）。

    判据直接打在事件处理器上（幂等键的语义面）：同一 order_id 投两次 → 1 条；
    换 order_id → 2 条。
    RED（还原修复）：两次投递都落在默认键 "1" 上 → 换 order_id 也只有 1 条，
      `len(rows) == 2` 失败。
    """
    from backend.common.events import DepositPaidEvent
    from backend.tasks.notify_handlers import _on_deposit_paid

    h = _h(client)
    p, c = _family(client, h, "13900002013", "幂等探针孩")

    with _db() as db:
        for oid, is_sup in ((901, False), (901, False), (902, True)):
            _on_deposit_paid(
                DepositPaidEvent(
                    child_id=c["id"],
                    deposit_id=12345,  # 同一押金行（补缴时真实值也不变）
                    amount=Decimal("1200.00"),
                    order_id=oid,
                    is_supplement=is_sup,
                ),
                db,
            )
        db.commit()

    rows = _notif_rows(p["id"])
    assert len(rows) == 2, rows
    assert [r[2] for r in rows] == ["pay-order-901", "pay-order-902"]
    assert [r[0] for r in rows] == ["押金缴纳成功", "押金补缴成功"]


# ---------- P2-12 ----------


def test_deposit_list_one_row_per_child(client: TestClient):
    """P2-12：第二笔已付押金单（重新入会）后，列表**同一孩子仍只有一行**，total 不虚高。

    RED（还原修复：`outerjoin(Order, child_id + TYPE_DEPOSIT + STATUS_PAID)`）：
      该孩子产出 2 行 → `total == 2`、`items` 两条同 child_id，两条断言都失败。
    """
    h = _h(client)
    p, c = _family(client, h, "13900002014", "重复行探针孩")
    _pay_deposit(client, h, c["id"])
    _insert_second_paid_deposit_order(c["id"])

    # 前置条件自证：库里**确有**两笔已付押金单（否则本用例会因构造失败而假绿）
    from backend.domain.identity.models import Order

    with _db() as db:
        paid = (
            db.query(Order)
            .filter(
                Order.child_id == c["id"],
                Order.order_type == Order.TYPE_DEPOSIT,
                Order.status == Order.STATUS_PAID,
                Order.is_deleted == 0,
            )
            .count()
        )
    assert paid == 2, f"探针构造失败：已付押金单 {paid} 笔"

    r = client.get("/api/admin/deposits", params={"keyword": "重复行探针孩"}, headers=h)
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["total"] == 1, body
    assert [x["child_id"] for x in body["items"]] == [c["id"]], body
