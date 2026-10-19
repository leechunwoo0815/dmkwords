# tests/unit/test_audit8_a6_inflight_deposit_order_20261019.py — 第八轮审查 A 批：P2-1 押金在途单闸
"""**缺陷（第八轮审查 P2-1）**：押金"在途单闸"原先只写在**家长端在线建单**
（`payment_service.create_online_order`），另外三个造单入口都没有：

| 入口 | 原先 | 后果 |
|---|---|---|
| 管理端押金单 `POST /deposits/children/{id}/orders` | 只判押金账户状态 | 双击两笔 pending，两笔都能付 |
| 管理端补缴单 `POST /deposits/children/{id}/supplement-orders` | 只判押金账户状态 | 同上 |
| 管理端通用造单 `POST /admin/orders`（`order_type=deposit`） | **连账户状态都不判** | 同上 |

第二笔成交时 `on_deposit_order_paid` 抛"押金已缴纳" → 回调兜底 200 + 资金异常待办 =
**钱收了、押金不动、只能人工退款**（押金是担保账户：宁可少收，不许错收）。

**修复**：判据收口到 `order_guards.assert_no_inflight_deposit_order()`（四个写口共用），
并**补上 Child 行锁**——原先在线路径的检查是纯读，两个并发请求各自读到"无在途"照样穿透。

**判据**：① 各入口第二笔 → 422 精确文案；② **对照组**：把第一笔取消后必须能再建（不许修过头）。
"""

from fastapi.testclient import TestClient

from backend.database import get_session
from backend.domain.identity.models import Order
from tests.unit.test_audit4_p2_batch3_20261017 import _member_child
from tests.unit.test_wm11_tasks import _h

BLOCK_MSG = "该孩子已有一笔待支付的押金订单，请先完成支付或取消后再下单"


def _member_without_deposit(client: TestClient, h, phone: str):
    """建家庭 + 缴观察期费（**不缴押金**，留出"未缴"态给押金建单用）。"""
    c, mini = _member_child(client, h, phone)
    with get_session() as db:
        # 把押金单/账户清掉，回到"从未缴过押金"的干净态（API 无"退押金"路径，直改造态）
        from backend.domain.billing.models import Deposit, DepositLedger

        dep = db.query(Deposit).filter(Deposit.child_id == c["id"]).first()
        if dep:
            db.query(DepositLedger).filter(DepositLedger.deposit_id == dep.id).delete()
            db.delete(dep)
        db.query(Order).filter(
            Order.child_id == c["id"], Order.order_type == Order.TYPE_DEPOSIT
        ).delete()
        db.commit()
    return c, mini


def test_admin_deposit_order_second_is_blocked(client: TestClient):
    """管理端押金造单：第二笔 422（RED：原先两笔都 200）。"""
    h = _h(client)
    c, _mini = _member_without_deposit(client, h, "13800003501")

    first = client.post(f"/api/admin/deposits/children/{c['id']}/orders", headers=h)
    assert first.status_code == 200, first.text
    second = client.post(f"/api/admin/deposits/children/{c['id']}/orders", headers=h)
    assert second.status_code == 422, f"在途单闸必须拦第二笔：{second.status_code} {second.text}"
    assert second.json()["detail"] == BLOCK_MSG


def test_admin_generic_order_deposit_second_is_blocked(client: TestClient):
    """管理端通用造单（`order_type=deposit`）：第二笔 422（RED：原先连账户状态都不判）。"""
    h = _h(client)
    c, _mini = _member_without_deposit(client, h, "13800003502")

    first = client.post(
        "/api/admin/orders", json={"child_id": c["id"], "order_type": "deposit"}, headers=h
    )
    assert first.status_code == 200, first.text
    second = client.post(
        "/api/admin/orders", json={"child_id": c["id"], "order_type": "deposit"}, headers=h
    )
    assert second.status_code == 422, f"{second.status_code} {second.text}"
    assert second.json()["detail"] == BLOCK_MSG


def test_online_deposit_order_second_is_blocked(client: TestClient):
    """家长端在线建单：第二笔 422（原行为保持，判据已收口到单点）。"""
    h = _h(client)
    c, mini = _member_without_deposit(client, h, "13800003503")

    first = client.post(
        "/api/miniapp/orders",
        json={"child_id": c["id"], "order_type": "deposit"},
        headers=mini,
    )
    assert first.status_code == 200, first.text
    second = client.post(
        "/api/miniapp/orders",
        json={"child_id": c["id"], "order_type": "deposit"},
        headers=mini,
    )
    assert second.status_code == 422, f"{second.status_code} {second.text}"
    assert second.json()["detail"] == BLOCK_MSG


def test_cancel_then_recreate_allowed(client: TestClient):
    """对照组（**防止修过头**）：取消第一笔后必须能再建（在途 = 待支付/待人工确认，不含已取消）。"""
    h = _h(client)
    c, _mini = _member_without_deposit(client, h, "13800003504")

    first = client.post(f"/api/admin/deposits/children/{c['id']}/orders", headers=h).json()
    cancel = client.post(f"/api/admin/orders/{first['order_id']}/cancel", headers=h)
    assert cancel.status_code == 200, cancel.text

    again = client.post(f"/api/admin/deposits/children/{c['id']}/orders", headers=h)
    assert again.status_code == 200, f"取消后应能重新建单：{again.status_code} {again.text}"
