# tests/unit/test_b1_refund_query_amount_20261126.py — B1-4 网关层：查单金额单位（元/分）
"""B1-4（2026-11-26 双审查）在网关层的判据：

微信退款查单 `/v3/refund/domestic/refunds/{out_refund_no}` 返回
`amount.refund`，**单位是分**；业务层 `RefundRequest.amount` 是**元**（Numeric(10,2)）。
不加换算就拿去比对，等于把 500 元当成 5 元（F1/F2 元分混淆同族的又一入口）——
"金额不符 → 拒落终态"这条判据会在**每一笔**上都误报，最后必然被人为关掉。

本用例只钉网关层解析：50000（分）→ 500.00（元）。缺 `amount` 字段 → None（不猜）。
"""

from __future__ import annotations

import asyncio
from decimal import Decimal

import pytest


class _FakeResponse:
    def __init__(self, payload: dict):
        self.status_code = 200
        self._payload = payload

    def json(self) -> dict:
        return self._payload


class _FakeAsyncClient:
    """够用的 httpx.AsyncClient 替身：只实现 query_refund 用到的那两个方法。"""

    def __init__(self, response: _FakeResponse, **_kwargs):
        self._response = response

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc_info):
        return False

    async def get(self, url, headers=None):  # noqa: ARG002 - 签名对齐 httpx
        return self._response


def _gateway(monkeypatch, payload: dict):
    """构造一个不加载证书的网关实例（只测解析，不碰签名/网络）。"""
    from backend.integrations.wechat import pay_v3

    gateway = object.__new__(pay_v3.WeChatPayV3)
    monkeypatch.setattr(
        gateway, "_build_auth_header", lambda *a, **kw: "WECHATPAY2-SHA256-RSA2048 stub"
    )
    monkeypatch.setattr(
        pay_v3.httpx,
        "AsyncClient",
        lambda **kwargs: _FakeAsyncClient(_FakeResponse(payload), **kwargs),
    )
    return gateway


CENTS_PAYLOAD = {
    "refund_id": "50000000wx_refund",
    "out_refund_no": "RF1-1-1",
    "status": "SUCCESS",
    "amount": {"total": 50000, "refund": 50000, "payer_total": 50000, "currency": "CNY"},
}


def test_query_refund_converts_cents_to_yuan(monkeypatch):
    """`amount.refund` 是分：50000 → 500.00 元（不换算就是 100 倍误差）。"""
    gateway = _gateway(monkeypatch, CENTS_PAYLOAD)
    result = asyncio.run(gateway.query_refund("RF1-1-1"))
    assert result.found is True
    assert result.state == "SUCCESS"
    assert result.refund_amount == Decimal("500.00"), result.refund_amount


def test_query_refund_partial_amount(monkeypatch):
    """部分退款：只退 10 元 → 1000 分 → 10.00 元（比对判据才有东西可比）。"""
    payload = {**CENTS_PAYLOAD, "amount": {"total": 50000, "refund": 1000, "currency": "CNY"}}
    gateway = _gateway(monkeypatch, payload)
    result = asyncio.run(gateway.query_refund("RF1-1-1"))
    assert result.refund_amount == Decimal("10.00")


def test_query_refund_without_amount_is_none(monkeypatch):
    """网关没给 amount → None（业务层据此跳过比对，不拿猜测落终态）。"""
    payload = {k: v for k, v in CENTS_PAYLOAD.items() if k != "amount"}
    gateway = _gateway(monkeypatch, payload)
    result = asyncio.run(gateway.query_refund("RF1-1-1"))
    assert result.refund_amount is None


def test_query_refund_not_found_still_has_no_amount(monkeypatch):
    """404（查无此单）语义不变：found=False，不带金额。"""
    from backend.integrations.wechat import pay_v3

    gateway = object.__new__(pay_v3.WeChatPayV3)
    monkeypatch.setattr(gateway, "_build_auth_header", lambda *a, **kw: "stub")

    class _NotFound(_FakeAsyncClient):
        def __init__(self, response, **kwargs):
            super().__init__(response, **kwargs)
            self._response.status_code = 404

    monkeypatch.setattr(
        pay_v3.httpx,
        "AsyncClient",
        lambda **kwargs: _NotFound(_FakeResponse({"code": "RESOURCE_NOT_EXISTS"}), **kwargs),
    )
    result = asyncio.run(gateway.query_refund("RF1-1-1"))
    assert result.found is False
    assert result.refund_amount is None


@pytest.mark.parametrize("bad", [None, "", "abc", {"refund": 1}])
def test_query_refund_malformed_amount_is_none(monkeypatch, bad):
    """脏数据不许把解析炸成 500（None = 不核对，人工介入）。"""
    payload = {**CENTS_PAYLOAD, "amount": {"refund": bad}}
    gateway = _gateway(monkeypatch, payload)
    result = asyncio.run(gateway.query_refund("RF1-1-1"))
    assert result.refund_amount is None
