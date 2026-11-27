# tests/unit/test_b5_6_wechat_integration_20261127.py — 微信集成的契约级单测（B5-6）
"""审查② P2「测试纵深」：`backend/integrations/wechat/` 966 行，此前只有 `code_to_session`
与手机号取号被登录用例间接覆盖；**支付 V3 的签名/验签/解密/金额换算几乎零直接判据**。

为什么这块必须有独立单测：它全是"错了也不会立刻报错"的代码——
  · 签名拼错一个 `\n` → 上线后所有下单/退款被微信拒（4xx），但本地 mock 通道永远绿；
  · 回调验签漏 ±300s 窗口 → 重放攻击面；
  · `amount.refund` 分→元少除 100 → 对账差 100 倍（F1/F2 元分混淆同族）。

用例全部**不联网**：RSA 密钥/自签证书在用例内现生成，`httpx.AsyncClient` 用假客户端替换，
AES-GCM 用真算法做加解密往返。三条纪律：
  ① 签名要用**公钥真验一遍**（只断言"有 signature 字段"等于没测）；
  ② 失败分支必须覆盖（5xx/429 = 结果未知 vs 4xx = 明确拒绝，两者语义不同，见 pay_v3 注释）；
  ③ 元/分换算逐字比对（`Decimal("1.50")`，不用浮点近似）。
"""

from __future__ import annotations

import base64
import json
import time
from datetime import datetime, timedelta
from decimal import Decimal

import pytest
from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding, rsa
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.x509.oid import NameOID

from backend.common.exceptions import ForbiddenError, PaymentError, ValidationError
from backend.common.gateways.payment.types import (
    PaymentOrderRequest,
    PaymentRefundRequest,
)
from backend.config import get_settings
from backend.integrations.wechat import pay_v3

MCH = "1900000109"
APPID = "wxaabbccddeeff0011"
SERIAL = "SN1234567890"
KEY_V3 = "dev-api-key-v3-32bytes-padding!!"  # AES-256 需要 32 字节


# ---------------------------------------------------------------- 夹具


@pytest.fixture(scope="module")
def keys():
    """现生成商户密钥 + 自签平台证书（不联网、不依赖仓库里的 PEM）。"""
    merchant_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    platform_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    subject = issuer = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "dmk-test")])
    cert = (
        x509.CertificateBuilder()
        .subject_name(subject)
        .issuer_name(issuer)
        .public_key(platform_key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(datetime.utcnow() - timedelta(days=1))
        .not_valid_after(datetime.utcnow() + timedelta(days=365))
        .sign(platform_key, hashes.SHA256())
    )
    return {
        "merchant_key": merchant_key,
        "platform_key": platform_key,
        "cert_pem": cert.public_bytes(serialization.Encoding.PEM),
        "merchant_pem": merchant_key.private_bytes(
            serialization.Encoding.PEM,
            serialization.PrivateFormat.PKCS8,
            serialization.NoEncryption(),
        ),
        "platform_public": platform_key.public_key(),
        # 鉴权头/支付参数由**商户私钥**签名 → 只能用商户公钥验；平台公钥只用于回调验签
        "merchant_public": merchant_key.public_key(),
    }


@pytest.fixture
def gateway(keys, tmp_path, monkeypatch):
    """带真实密钥/证书的网关实例（settings 打桩，不读真实 .env）。"""
    key_file = tmp_path / "apiclient_key.pem"
    key_file.write_bytes(keys["merchant_pem"])
    cert_file = tmp_path / "platform_cert.pem"
    cert_file.write_bytes(keys["cert_pem"])

    s = get_settings()
    monkeypatch.setattr(s, "WECHAT_APP_ID", APPID)
    monkeypatch.setattr(s, "WECHAT_MCH_ID", MCH)
    monkeypatch.setattr(s, "WECHAT_API_KEY_V3", KEY_V3)
    monkeypatch.setattr(s, "WECHAT_CERT_SERIAL_NO", SERIAL)
    monkeypatch.setattr(s, "WECHAT_PRIVATE_KEY_PATH", str(key_file))
    monkeypatch.setattr(s, "WECHAT_PLATFORM_CERT_PATH", str(cert_file))
    monkeypatch.setattr(s, "WECHAT_PAY_NOTIFY_URL", "https://example.test/api/pay/wechat/notify")
    return pay_v3.WeChatPayV3()


class _Resp:
    def __init__(
        self,
        status_code: int = 200,
        payload: object = None,
        *,
        text: str = "",
        content: bytes = b"",
    ):
        self.status_code = status_code
        self._payload = payload
        self.text = text or (json.dumps(payload) if payload is not None else "")
        self.content = content

    def json(self):
        if self._payload is None:
            raise ValueError("no json body")
        return self._payload


class _FakeClient:
    """替换 httpx.AsyncClient：按调用顺序返回预置响应。"""

    def __init__(self, responses: list[_Resp]):
        self._responses = list(responses)
        self.calls: list[tuple[str, str, dict]] = []

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    def _next(self, method: str, url: str, kwargs: dict):
        self.calls.append((method, url, kwargs))
        return self._responses.pop(0)

    async def post(self, url, **kwargs):
        return self._next("POST", url, kwargs)

    async def get(self, url, **kwargs):
        return self._next("GET", url, kwargs)


def _patch_http(monkeypatch, responses: list[_Resp]) -> _FakeClient:
    client = _FakeClient(responses)
    monkeypatch.setattr(pay_v3.httpx, "AsyncClient", lambda **kwargs: client)
    return client


# ---------------------------------------------------------------- 签名 / 鉴权头


def test_sign_requires_private_key(monkeypatch):
    """商户私钥未配置时签名必须显式报错（不许静默生成空签名）。"""
    s = get_settings()
    monkeypatch.setattr(s, "WECHAT_PRIVATE_KEY_PATH", "/nonexistent/key.pem")
    gw = pay_v3.WeChatPayV3()
    with pytest.raises(RuntimeError, match="私钥"):
        gw._sign("x")


def test_auth_header_signature_covers_all_five_lines(gateway, keys):
    """V3 的签名串是 `METHOD\\nURL\\n时间戳\\n随机串\\nbody\\n`——少一个换行微信就拒。

    判据用**平台公钥真验一遍**：只断言"头里有 signature=" 等于没测（字符串里可能有但签名是错的）。
    """
    header = gateway._build_auth_header("POST", "/v3/pay/transactions/jsapi", '{"a":1}')
    assert header.startswith("WECHATPAY2-SHA256-RSA2048 ")
    for part in (
        f'mchid="{MCH}"',
        f'serial_no="{SERIAL}"',
        "nonce_str=",
        "timestamp=",
        "signature=",
    ):
        assert part in header, f"鉴权头缺 {part}：{header}"

    fields = dict(
        piece.split("=", 1) for piece in header[len("WECHATPAY2-SHA256-RSA2048 ") :].split(",")
    )
    message = (
        f"POST\n/v3/pay/transactions/jsapi\n{fields['timestamp'].strip(chr(34))}\n"
        f'{fields["nonce_str"].strip(chr(34))}\n{{"a":1}}\n'
    )
    keys["merchant_public"].verify(
        base64.b64decode(fields["signature"].strip('"')),
        message.encode(),
        padding.PKCS1v15(),
        hashes.SHA256(),
    )


def test_miniapp_pay_params_signature_covers_appid_and_package(gateway, keys):
    """小程序支付参数签名串是 `appid\\n时间戳\\n随机串\\nprepay_id=xxx\\n`（与下单签名不同！）。"""
    params = gateway._build_miniapp_pay_params("wx201410272009395522657a690389285100")
    assert set(params) == {"timeStamp", "nonceStr", "package", "signType", "paySign"}
    assert params["signType"] == "RSA"
    assert params["package"] == "prepay_id=wx201410272009395522657a690389285100"
    message = f"{APPID}\n{params['timeStamp']}\n{params['nonceStr']}\n{params['package']}\n"
    keys["merchant_public"].verify(
        base64.b64decode(params["paySign"]),
        message.encode(),
        padding.PKCS1v15(),
        hashes.SHA256(),
    )


# ---------------------------------------------------------------- 下单


@pytest.mark.asyncio
async def test_create_jsapi_order_success_returns_pay_params(gateway, monkeypatch):
    client = _patch_http(monkeypatch, [_Resp(200, {"prepay_id": "wx_prepay_1"})])
    params = await gateway.create_jsapi_order("openid-1", "ORD-1", 199, "会员费")
    assert params["package"] == "prepay_id=wx_prepay_1"
    method, url, kwargs = client.calls[0]
    assert method == "POST" and url == "/v3/pay/transactions/jsapi"
    body = json.loads(kwargs["content"])
    assert body["amount"] == {"total": 199, "currency": "CNY"}, "金额必须是整数分"
    assert body["payer"] == {"openid": "openid-1"}
    assert body["notify_url"].endswith("/api/pay/wechat/notify")


@pytest.mark.asyncio
async def test_create_jsapi_order_error_message_and_non_json_are_both_reported(
    gateway, monkeypatch
):
    _patch_http(monkeypatch, [_Resp(400, {"message": "参数错误"})])
    with pytest.raises(PaymentError, match="参数错误"):
        await gateway.create_jsapi_order("o", "ORD-1", 1, "x")

    _patch_http(monkeypatch, [_Resp(500, None, text="boom" * 100)])
    with pytest.raises(PaymentError, match="HTTP 500"):
        await gateway.create_jsapi_order("o", "ORD-1", 1, "x")


@pytest.mark.asyncio
async def test_create_order_wraps_payment_error_into_failure_response(gateway, monkeypatch):
    """ABC 契约：create_order 不抛异常，失败走 success=False（上层按它落 failed）。"""
    _patch_http(monkeypatch, [_Resp(400, {"message": "商户号不匹配"})])
    resp = await gateway.create_order(
        PaymentOrderRequest(
            out_trade_no="ORD-1", amount=Decimal("1.00"), openid="o", description="x"
        )
    )
    assert resp.success is False
    assert "商户号不匹配" in (resp.error_message or "")


# ---------------------------------------------------------------- 回调验签 / 解密


@pytest.mark.asyncio
async def test_verify_callback_without_platform_cert_raises(monkeypatch):
    s = get_settings()
    monkeypatch.setattr(s, "WECHAT_PLATFORM_CERT_PATH", "/nonexistent/cert.pem")
    monkeypatch.setattr(s, "WECHAT_PRIVATE_KEY_PATH", "/nonexistent/key.pem")
    gw = pay_v3.WeChatPayV3()
    with pytest.raises(RuntimeError, match="平台证书"):
        await gw.verify_callback_signature("{}", "sig", str(int(time.time())), "n")


@pytest.mark.asyncio
async def test_verify_callback_timestamp_window_and_types(gateway, keys):
    """±300 秒防重放：过期/未来时间戳与非数字时间戳一律 False（不是抛异常）。"""
    body, nonce = '{"id":"evt"}', "nonce1"
    raw = f"{int(time.time())}\n{nonce}\n{body}\n"
    good_sig = base64.b64encode(
        keys["platform_key"].sign(raw.encode(), padding.PKCS1v15(), hashes.SHA256())
    ).decode()

    assert await gateway.verify_callback_signature(body, good_sig, str(int(time.time())), nonce)
    # 过期（301 秒前）
    old_ts = str(int(time.time()) - 301)
    old_raw = f"{old_ts}\n{nonce}\n{body}\n"
    old_sig = base64.b64encode(
        keys["platform_key"].sign(old_raw.encode(), padding.PKCS1v15(), hashes.SHA256())
    ).decode()
    assert await gateway.verify_callback_signature(body, old_sig, old_ts, nonce) is False
    # 非数字时间戳
    assert await gateway.verify_callback_signature(body, good_sig, "not-a-number", nonce) is False
    # 签名错误
    assert (
        await gateway.verify_callback_signature(body, good_sig[:-8], str(int(time.time())), nonce)
        is False
    )


@pytest.mark.asyncio
async def test_decrypt_callback_parses_amounts_and_merchant_fields(gateway):
    """AES-GCM 真加解密往返：元/分换算 + B1-7 的 mchid/appid 抽取。"""
    payload = {
        "out_trade_no": "ORD-9",
        "transaction_id": "txn-9",
        "trade_state": "SUCCESS",
        "amount": {"total": 101, "refund": 55},
        "mchid": MCH,
        "appid": APPID,
    }
    nonce, ad = "abcdefghijkl", "transaction"
    ciphertext = AESGCM(KEY_V3.encode()).encrypt(
        nonce.encode(), json.dumps(payload).encode(), ad.encode()
    )
    data = await gateway.decrypt_callback_data(base64.b64encode(ciphertext).decode(), nonce, ad)
    assert data.out_trade_no == "ORD-9"
    assert data.amount == Decimal("1.01"), "分→元必须逐分精确（浮点会差 1 分）"
    assert data.refund_amount == Decimal("0.55"), "退款金额读 amount.refund 而不是 total"
    assert data.mchid == MCH and data.appid == APPID
    assert json.loads(data.raw_body)["transaction_id"] == "txn-9"


# ---------------------------------------------------------------- 退款 / 查单


@pytest.mark.asyncio
async def test_refund_requires_out_refund_no(gateway):
    with pytest.raises(PaymentError, match="out_refund_no"):
        await gateway.refund(
            PaymentRefundRequest(out_trade_no="ORD-1", refund_amount=100, total_amount=500)
        )


@pytest.mark.asyncio
@pytest.mark.parametrize("status", [500, 502, 429])
async def test_refund_unknown_result_raises_instead_of_failed(gateway, monkeypatch, status):
    """5xx/429 = 微信侧**结果未知**（禁止换单号重试）；4xx 才是明确拒绝。"""
    _patch_http(monkeypatch, [_Resp(status, {"message": "server error"})])
    with pytest.raises(PaymentError, match="结果未知"):
        await gateway.refund(
            PaymentRefundRequest(
                out_trade_no="ORD-1", out_refund_no="RF-1", refund_amount=100, total_amount=500
            )
        )


@pytest.mark.asyncio
async def test_refund_4xx_is_explicit_failure_and_200_carries_state(gateway, monkeypatch):
    _patch_http(monkeypatch, [_Resp(400, {"message": "余额不足"})])
    resp = await gateway.refund(
        PaymentRefundRequest(
            out_trade_no="ORD-1", out_refund_no="RF-1", refund_amount=100, total_amount=500
        )
    )
    assert resp.success is False and "余额不足" in (resp.error_message or "")

    _patch_http(monkeypatch, [_Resp(200, {"refund_id": "r-1", "status": "PROCESSING"})])
    ok = await gateway.refund(
        PaymentRefundRequest(
            out_trade_no="ORD-1", out_refund_no="RF-1", refund_amount=100, total_amount=500
        )
    )
    assert ok.success is True and ok.state == "PROCESSING"
    assert ok.refund_id == "r-1"


@pytest.mark.asyncio
async def test_query_refund_found_flag_and_cent_to_yuan(gateway, monkeypatch):
    """查单金额：微信 `amount.refund` 是分，必须 /100 再交给业务层逐分比对（B1-4）。"""
    _patch_http(monkeypatch, [_Resp(404, {"code": "RESOURCE_NOT_EXISTS"})])
    miss = await gateway.query_refund("RF-404")
    assert miss.found is False and "未受理" in (miss.error_message or "")

    _patch_http(
        monkeypatch,
        [_Resp(200, {"status": "SUCCESS", "refund_id": "r-2", "amount": {"refund": 150}})],
    )
    hit = await gateway.query_refund("RF-1")
    assert hit.found is True and hit.state == "SUCCESS"
    assert hit.refund_amount == Decimal("1.50")

    # 字段缺失 → None（业务层据此拒绝落终态），但不得抛异常
    _patch_http(monkeypatch, [_Resp(200, {"status": "SUCCESS", "refund_id": "r-3"})])
    none_amount = await gateway.query_refund("RF-2")
    assert none_amount.found is True and none_amount.refund_amount is None


@pytest.mark.asyncio
async def test_query_refund_non_200_raises(gateway, monkeypatch):
    _patch_http(monkeypatch, [_Resp(500, {"message": "internal"})])
    with pytest.raises(PaymentError, match="退款查单失败"):
        await gateway.query_refund("RF-1")


# ---------------------------------------------------------------- 对账单


@pytest.mark.asyncio
async def test_download_bill_verifies_hash_fail_closed(gateway, monkeypatch):
    """对账是"钱对不对"的最后一道防线：没哈希/哈希不符都必须拒收（不许照解压使用）。"""
    body = b"col1,col2\n1,2\n"
    import hashlib

    good = hashlib.sha256(body).hexdigest().upper()

    # ① 正常：hash 对得上，明文 CSV（非 gzip）也能解
    _patch_http(
        monkeypatch,
        [
            _Resp(
                200,
                {"download_url": "https://wx.test/bill", "hash_value": good, "hash_type": "SHA256"},
            ),
            _Resp(200, None, content=body),
        ],
    )
    assert "col1" in await gateway.download_bill("2026-11-26")

    # ② 哈希不符
    _patch_http(
        monkeypatch,
        [
            _Resp(
                200,
                {
                    "download_url": "https://wx.test/bill",
                    "hash_value": "DEADBEEF",
                    "hash_type": "SHA256",
                },
            ),
            _Resp(200, None, content=body),
        ],
    )
    with pytest.raises(PaymentError, match="哈希校验失败"):
        await gateway.download_bill("2026-11-26")

    # ③ 没给哈希 → 拒收
    _patch_http(
        monkeypatch,
        [
            _Resp(200, {"download_url": "https://wx.test/bill", "hash_type": "MD5"}),
            _Resp(200, None, content=body),
        ],
    )
    with pytest.raises(PaymentError, match="缺少可用哈希"):
        await gateway.download_bill("2026-11-26")


# ---------------------------------------------------------------- 证书轮换


def test_persist_platform_cert_writes_file_and_resets_gateway_cache(gateway, keys):
    """只落盘不清 `get_payment_gateway()` 缓存 = 进程不重启就继续用旧证书验签。"""
    calls: list[int] = []
    import backend.integrations.payment as payment_mod

    original = getattr(payment_mod, "reset_payment_gateway_cache", None)
    if original is not None:
        payment_mod.reset_payment_gateway_cache = lambda: calls.append(1)  # type: ignore[assignment]
    try:
        new_cert = x509.load_pem_x509_certificate(keys["cert_pem"])
        gateway._persist_platform_cert(keys["cert_pem"], new_cert)
        assert gateway.platform_cert is new_cert
        written = get_settings().WECHAT_PLATFORM_CERT_PATH
        with open(written, "rb") as fh:
            assert fh.read() == keys["cert_pem"]
        assert calls == [1], "落盘后必须清网关单例缓存（否则旧证书继续验签）"
    finally:
        if original is not None:
            payment_mod.reset_payment_gateway_cache = original  # type: ignore[assignment]


# ---------------------------------------------------------------- access_token 缓存


def test_access_token_is_cached_and_expires(monkeypatch):
    """缓存 110 分钟（expires_in − 10 分钟）——每次调用都打微信接口会被限频。"""
    from backend.integrations.wechat import service as wx_service

    monkeypatch.setattr(wx_service.WeChatService, "_token", None)
    monkeypatch.setattr(wx_service.WeChatService, "_token_expires_at", None)
    s = get_settings()
    monkeypatch.setattr(s, "WECHAT_APP_ID", APPID)
    monkeypatch.setattr(s, "WECHAT_APP_SECRET", "secret")

    calls: list[dict] = []

    class _R:
        def json(self):
            return {"access_token": "tok-1", "expires_in": 7200}

    monkeypatch.setattr(wx_service.httpx, "get", lambda url, **kw: calls.append(kw) or _R())
    svc = wx_service.WeChatService()
    assert svc.get_access_token() == "tok-1"
    assert svc.get_access_token() == "tok-1"
    assert len(calls) == 1, "第二次必须命中缓存"

    # 过期后重新取（`self._token = ...` 写的是**实例**属性，所以这里必须打在实例上；
    # 打在类上不生效——这是本用例第一版实测失败的坑）
    monkeypatch.setattr(svc, "_token_expires_at", datetime.now() - timedelta(seconds=1))
    assert svc.get_access_token() == "tok-1"
    assert len(calls) == 2


def test_access_token_without_credentials_returns_none(monkeypatch):
    from backend.integrations.wechat import service as wx_service

    monkeypatch.setattr(wx_service.WeChatService, "_token", None)
    monkeypatch.setattr(wx_service.WeChatService, "_token_expires_at", None)
    s = get_settings()
    monkeypatch.setattr(s, "WECHAT_APP_ID", "")
    monkeypatch.setattr(s, "WECHAT_APP_SECRET", "")
    assert wx_service.WeChatService().get_access_token() is None


def test_access_token_network_exception_returns_none(monkeypatch):
    from backend.integrations.wechat import service as wx_service

    monkeypatch.setattr(wx_service.WeChatService, "_token", None)
    monkeypatch.setattr(wx_service.WeChatService, "_token_expires_at", None)
    s = get_settings()
    monkeypatch.setattr(s, "WECHAT_APP_ID", APPID)
    monkeypatch.setattr(s, "WECHAT_APP_SECRET", "secret")

    def _boom(*a, **kw):
        raise OSError("network down")

    monkeypatch.setattr(wx_service.httpx, "get", _boom)
    assert wx_service.WeChatService().get_access_token() is None


# ---------------------------------------------------------------- code2session / 手机号


class _AsyncClientResp:
    def __init__(self, payload: dict):
        self._payload = payload

    def json(self):
        return self._payload


class _AsyncClient:
    def __init__(self, payload: dict):
        self._payload = payload

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    async def get(self, url, **kwargs):
        return _AsyncClientResp(self._payload)

    async def post(self, url, **kwargs):
        return _AsyncClientResp(self._payload)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("payload", "exc"),
    [
        ({"errcode": 40029, "errmsg": "invalid code"}, ValidationError),
        ({"errcode": 40163, "errmsg": "code been used"}, ValidationError),
        ({"errcode": 40013, "errmsg": "invalid appid"}, ForbiddenError),
        ({"errcode": 40125, "errmsg": "invalid secret"}, ForbiddenError),
        ({"errcode": 45011, "errmsg": "freq limit"}, ValidationError),
    ],
)
async def test_code_to_session_error_mapping(monkeypatch, payload, exc):
    """错误码映射是"用户看到什么提示"的唯一依据：40013/40125 = 配置错（运维），其余 = 登录失败。"""
    from backend.integrations.wechat import auth as wx_auth

    monkeypatch.setattr(wx_auth.httpx, "AsyncClient", lambda **kw: _AsyncClient(payload))
    with pytest.raises(exc):
        await wx_auth.WeChatAuth.code_to_session("code-1")


@pytest.mark.asyncio
async def test_code_to_session_success_returns_payload(monkeypatch):
    from backend.integrations.wechat import auth as wx_auth

    payload = {"openid": "openid-1", "session_key": "sk", "errcode": 0}
    monkeypatch.setattr(wx_auth.httpx, "AsyncClient", lambda **kw: _AsyncClient(payload))
    assert await wx_auth.WeChatAuth.code_to_session("code-1") == payload


@pytest.mark.asyncio
async def test_get_phone_number_success_and_failure(monkeypatch):
    from backend.integrations.wechat import auth as wx_auth
    from backend.integrations.wechat import subscribe as wx_sub

    async def _token_ok():
        return {"access_token": "tok"}

    monkeypatch.setattr(wx_sub, "_get_access_token", _token_ok)
    monkeypatch.setattr(
        wx_auth.httpx,
        "AsyncClient",
        lambda **kw: _AsyncClient({"errcode": 0, "phone_info": {"phoneNumber": "13800000000"}}),
    )
    assert await wx_auth.WeChatAuth.get_phone_number("pcode") == "13800000000"

    monkeypatch.setattr(wx_auth.httpx, "AsyncClient", lambda **kw: _AsyncClient({"errcode": 40001}))
    assert await wx_auth.WeChatAuth.get_phone_number("pcode") is None


@pytest.mark.asyncio
async def test_get_phone_number_without_token_returns_none(monkeypatch):
    from backend.integrations.wechat import auth as wx_auth
    from backend.integrations.wechat import subscribe as wx_sub

    async def _token_missing():
        return {}

    monkeypatch.setattr(wx_sub, "_get_access_token", _token_missing)
    assert await wx_auth.WeChatAuth.get_phone_number("pcode") is None


# ---------------------------------------------------------------- 覆盖率开关 / 夜间诊断（B5-6 第二半）


def test_branch_coverage_switch_is_wired_into_gate():
    """分支覆盖率是**换口径**：必须做成开关，不许直接替换 91% 的行覆盖率基线。"""
    from pathlib import Path

    gate = (Path(__file__).resolve().parent.parent.parent / "scripts/gate.sh").read_text(
        encoding="utf-8"
    )
    assert "COVERAGE_BRANCH" in gate, "门禁没有分支覆盖率开关"
    assert "--cov-branch" in gate
    # 默认必须是关（`${COVERAGE_BRANCH:-0}`），否则等于悄悄换了门禁口径
    assert 'if [ "${COVERAGE_BRANCH:-0}" = "1" ]' in gate


def test_monthly_branch_coverage_workflow_is_report_only():
    """月度诊断必须报告制（continue-on-error）——先积累数据，再定阈值。"""
    from pathlib import Path

    import yaml

    path = Path(__file__).resolve().parent.parent.parent / ".github/workflows/coverage-branch.yml"
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    triggers = data.get("on", data.get(True))
    assert triggers["schedule"][0]["cron"] == "0 4 1 * *", "必须按月跑（不是每次推送）"
    assert "workflow_dispatch" in triggers
    job = data["jobs"]["branch-coverage"]
    assert job["continue-on-error"] is True, "报告制：不许在阈值确定前拦交付"
    run_src = " ".join(s.get("run", "") for s in job["steps"])
    assert "--cov-branch" in run_src
    assert "-n 4 --dist load" in run_src, "必须并行（串行 90 分钟跑不完）"
    upload = [s for s in job["steps"] if "upload-artifact" in s.get("uses", "")]
    assert upload and upload[0].get("if") == "always()"
