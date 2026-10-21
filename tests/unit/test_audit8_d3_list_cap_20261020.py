# tests/unit/test_audit8_d3_list_cap_20261020.py — 第八轮大审查 D 批（下）P2-44
"""硬上限**必须自报**：列表经响应头如实告知"上限 / 是否已达上限"，导出在文件里写明。

背景（P2-44）：退款/退会/转让/预约 200、家长订单 50、活动 200/50、导出 10000——
全是硬上限且**无任何提示**。管理员看到 200 行会当成"这就是全部"→ **待审单被漏看**；
导出的 Excel 拿去做台账/对账会静默漏行（与 P1-7 同族："看起来成功"的截断）。

为什么用响应头而不是把响应体包成 `{items,total}`：这些接口消费方遍布管理端与小程序，
改形状是破坏性变更（还要同步 OpenAPI 生成的 schema.d.ts）；响应头零破坏。
语义**不撒谎**：`X-List-Truncated=true` 只表示"返回行数已达上限，**可能**还有更多"。

RED（变异自证）：把 `stamp_list_meta` 调用去掉 → 头缺失 → 用例 1 红；
导出不写"注意"行 → 用例 3 红。
"""

from __future__ import annotations

from fastapi.testclient import TestClient

from tests.unit.test_wm10_concurrency import _db
from tests.unit.test_wm13_writeback import _apply_refund, _family, _h, _pay


def _one_refund(client: TestClient, h: dict, phone: str, name: str) -> dict:
    """造一条退款申请（真实链路：建档 → 观察期费 → 支付 → 家长申请退款）。"""
    p, c, mini = _family(client, h, phone, name)
    o = _pay(client, h, c["id"], "observation_fee")
    return _apply_refund(client, mini, c, o)


# ---------- ① 列表：上限自报（响应头） ----------


def test_refund_list_reports_cap_headers(client: TestClient, monkeypatch):
    """已达上限 → `X-List-Truncated: true`；未达上限 → `false`（不误报）。"""
    from backend.domain.identity import refund_queries

    h = _h(client)
    _one_refund(client, h, "13989000001", "上限孩1")
    _one_refund(client, h, "13989000002", "上限孩2")

    monkeypatch.setattr(refund_queries, "ADMIN_LIST_LIMIT", 1)  # 把上限压到 1 观测截断
    r = client.get("/api/admin/refund-requests", headers=h)
    assert r.status_code == 200, r.text
    assert len(r.json()) == 1, "上限压到 1 后只应返回 1 条"
    assert r.headers.get("X-List-Limit") == "1", (
        f"上限必须自报（实际 {r.headers.get('X-List-Limit')!r}）——"
        "RED=裸数组 + 硬上限，管理员以为这就是全部"
    )
    assert r.headers.get("X-List-Truncated") == "true", (
        f"已达上限必须如实标记（实际 {r.headers.get('X-List-Truncated')!r}）"
    )

    monkeypatch.setattr(refund_queries, "ADMIN_LIST_LIMIT", 50)  # 未达上限
    r2 = client.get("/api/admin/refund-requests", headers=h)
    assert r2.headers.get("X-List-Truncated") == "false", "未达上限不得误报截断"


def test_withdrawal_and_transfer_lists_report_cap_headers(client: TestClient, monkeypatch):
    """退会 / 转让台账同样自报上限（同族三处，不能只修一处）。"""
    from backend.domain.identity.transfer_service import TransferService
    from backend.domain.identity.wm10_withdrawal_service import WithdrawalService

    h = _h(client)
    monkeypatch.setattr(WithdrawalService, "ADMIN_LIST_LIMIT", 0)  # 0 = 任何返回都算"已达上限"
    monkeypatch.setattr(TransferService, "ADMIN_LIST_LIMIT", 0)

    for path in ("/api/admin/withdrawals", "/api/admin/transfers"):
        r = client.get(path, headers=h)
        assert r.status_code == 200, r.text
        assert r.headers.get("X-List-Limit") == "0", f"{path} 缺上限头"
        assert r.headers.get("X-List-Truncated") == "true", f"{path} 缺截断头"


def test_reservation_and_activity_lists_report_cap_headers(client: TestClient, monkeypatch):
    """预约 / 管理端活动列表同样自报上限。"""
    from backend.domain.activity.service import ActivityService
    from backend.domain.reading.service import ReservationAdminService

    h = _h(client)
    monkeypatch.setattr(ReservationAdminService, "ADMIN_LIST_LIMIT", 0)
    monkeypatch.setattr(ActivityService, "ADMIN_LIST_LIMIT", 0)

    for path in ("/api/admin/reservations", "/api/admin/activities"):
        r = client.get(path, headers=h)
        assert r.status_code == 200, r.text
        assert r.headers.get("X-List-Limit") == "0", f"{path} 缺上限头"
        assert r.headers.get("X-List-Truncated") == "true", f"{path} 缺截断头"


def test_miniapp_order_list_reports_cap_headers(client: TestClient, monkeypatch):
    """家长端订单列表（50 条上限）同样自报。"""
    from backend.domain.identity.order_service import OrderService

    h = _h(client)
    _p, c, mini = _family(client, h, "13989000003", "订单上限孩")
    monkeypatch.setattr(OrderService, "MY_ORDERS_LIMIT", 0)

    r = client.get(f"/api/miniapp/orders?child_id={c['id']}", headers=mini)
    assert r.status_code == 200, r.text
    assert r.headers.get("X-List-Limit") == "0"
    assert r.headers.get("X-List-Truncated") == "true"


# ---------- ② 导出：上限写在文件里 ----------


def _sheet_text(content: bytes) -> str:
    from io import BytesIO

    from openpyxl import load_workbook

    ws = load_workbook(BytesIO(content)).active
    return "\n".join(
        " ".join(str(c) for c in row if c is not None) for row in ws.iter_rows(values_only=True)
    )


def test_notification_export_states_the_cap(client: TestClient, monkeypatch):
    """超过上限时导出文件**首行写明**共多少条、只导了多少条（不再静默截断）。"""
    from backend.common.notification_models import Notification
    from backend.common.notifications import NotificationService
    from backend.domain.admin import service as admin_service

    h = _h(client)
    _p, c, _mini = _family(client, h, "13989000004", "导出上限孩")
    db = _db()
    try:
        for i in range(2):
            NotificationService(db).send(
                parent_id=c["parent_id"],
                scene=f"export.cap.{i}",
                title="导出上限测试",
                content="内容",
                category=Notification.CATEGORY_OTHER,
            )
        db.commit()
    finally:
        db.close()

    monkeypatch.setattr(admin_service, "NOTIFICATION_EXPORT_LIMIT", 1)
    r = client.get("/api/admin/notifications/export", headers=h)
    assert r.status_code == 200, r.text
    text = _sheet_text(r.content)
    assert "注意" in text and "共 2 条" in text, (
        f"超上限时导出文件必须自报（共 N 条 / 仅导 M 条）：\n{text[:200]}"
    )


def test_audit_export_states_the_cap(client: TestClient, monkeypatch):
    """审计导出同款：超上限时首行写明（**恰好等于上限**不算超，不得误报）。"""
    from backend.domain.admin import service as admin_service

    h = _h(client)
    _h(client)  # 再登录一次 → 第二条审计行（上限压到 1 才有"被截断"这个事实）
    monkeypatch.setattr(admin_service, "AUDIT_EXPORT_LIMIT", 1)
    r = client.get("/api/admin/audit-logs/export", headers=h)
    assert r.status_code == 200, r.text
    text = _sheet_text(r.content)
    assert "注意" in text and "仅导出最新" in text, f"审计导出超上限时必须自报：\n{text[:200]}"
