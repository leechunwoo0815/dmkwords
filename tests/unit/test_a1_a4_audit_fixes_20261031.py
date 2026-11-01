# tests/unit/test_a1_a4_audit_fixes_20261031.py — A-1～A-4 审查小修判据（第一百五十六批）
"""docs/27 §六 亲验 5 条中的 A-1/A-2/A-3（A-4 为注释+契约描述订正，判据=契约重导+grep）。

- A-1：生产配置校验必须拦 `CORS_ORIGINS="*"`（`*` 放任意第三方站 JS 读非凭证 API 响应）。
- A-2：逐端点 429 必须带 `Retry-After`（与全局写限流 429 同形）——RateLimitError 携带
  窗口，`business_exception_handler` 对 429 且带窗口的补头。
- A-3：报告图片端点与数据端点**同源**（unpaid 同拒）——"预检与下行流禁漂移"的报告域对齐。
"""

from __future__ import annotations

import asyncio

import pytest
from fastapi.testclient import TestClient

from backend.common.exceptions import RateLimitError
from tests.unit.test_audit8_b_batch_20261020 import _prod_settings
from tests.unit.test_wm9_activity import _h, _mk_child

# ---------- A-1：生产配置拦 CORS 通配符 ----------


def test_a1_prod_validation_rejects_cors_wildcard():
    with pytest.raises(RuntimeError) as ei:
        _prod_settings(CORS_ORIGINS="*").validate_production()
    assert "CORS_ORIGINS" in str(ei.value)
    # 对照组：合法域名清单不得再报 CORS（本机 venv 可能缺短信 SDK 等无关问题，
    # 所以只断言"错误里不含 CORS_ORIGINS"，不断言不抛）
    with pytest.raises(RuntimeError) as e2:
        _prod_settings(CORS_ORIGINS="https://admin.example.com").validate_production()
    assert "CORS_ORIGINS" not in str(e2.value)


# ---------- A-2：429 带 Retry-After（handler 单测 + 真端到端） ----------


def _run_handler(exc: RateLimitError):
    from backend.common.exceptions import business_exception_handler

    return asyncio.run(business_exception_handler(None, exc))


def test_a2_handler_adds_retry_after_when_window_present():
    resp = _run_handler(RateLimitError("请求过于频繁，请 60 秒后再试", retry_after=60))
    assert resp.status_code == 429
    assert resp.headers["retry-after"] == "60"


def test_a2_handler_omits_header_without_window():
    resp = _run_handler(RateLimitError())
    assert resp.status_code == 429
    assert "retry-after" not in resp.headers


def test_a2_per_endpoint_429_carries_retry_after(client: TestClient):
    """端到端：admin login 限流 5/60 → 第 6 次起 429 且带头（窗口 60）。"""
    for _ in range(5):
        client.post("/api/admin/login", json={"username": "admin", "password": "wrong"})
    r = client.post("/api/admin/login", json={"username": "admin", "password": "wrong"})
    assert r.status_code == 429, r.text
    assert r.headers.get("retry-after") == "60", dict(r.headers)


# ---------- A-3：报告图片端点与数据端点同源（unpaid 同拒） ----------


def _mini_token(client: TestClient, phone: str) -> str:
    r = client.post("/api/miniapp/login", json={"phone": phone, "code": "1234"})
    assert r.status_code == 200, r.text
    return r.json()["token"]


def test_a3_report_image_same_gate_as_data_endpoint(client: TestClient):
    """unpaid：数据端点与图片端点必须同结论（都 422「入会后可查看」）。

    变异（A-3）：摘掉 image 端点的 require_member_action → 图片端点 200 → 本用例红。
    """
    h = _h(client)
    c_unpaid, _mini = _mk_child(client, h, "13800156001", "A3未缴孩", member=False)
    token = _mini_token(client, "13800156001")

    r_data = client.get(
        f"/api/miniapp/reports/weekly?child_id={c_unpaid['id']}",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert r_data.status_code == 422, r_data.text
    r_img = client.get(f"/api/miniapp/reports/weekly/image?child_id={c_unpaid['id']}&token={token}")
    assert r_img.status_code == 422, (
        f"图片端点漂移（未拦 unpaid）：{r_img.status_code} {r_img.text[:120]}"
    )
    assert "入会后可查看" in r_img.json()["detail"]


def test_a3_report_image_still_serves_active_member(client: TestClient):
    """对照组：正式会员的图片端点保持可用（守卫不许误伤正常路径）。"""
    h = _h(client)
    c_member, _mini = _mk_child(client, h, "13800156002", "A3会员孩", member=True)
    token = _mini_token(client, "13800156002")
    r_data = client.get(
        f"/api/miniapp/reports/weekly?child_id={c_member['id']}",
        headers={"Authorization": f"Bearer {token}"},
    )
    r_img = client.get(f"/api/miniapp/reports/weekly/image?child_id={c_member['id']}&token={token}")
    # 同源断言：两端点结论必须一致（观察期口径由 REPORT_VIEW 矩阵决定，不允许漂移）
    assert (r_data.status_code == 200) == (r_img.status_code == 200), (
        f"两端点漂移：data={r_data.status_code} image={r_img.status_code}"
    )
    assert r_img.status_code == 200, r_img.text[:200]
