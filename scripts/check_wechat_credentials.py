# scripts/check_wechat_credentials.py — 微信凭证自检（AppID/Secret/商户四件套/证书）
"""用法（详见 `docs/21` §2.4）：

    python -m scripts.check_wechat_credentials                # 离线：格式/文件/证书有效期/回调 URL
    python -m scripts.check_wechat_credentials --online        # 追加：AppID+Secret 是否真的有效
    python -m scripts.check_wechat_credentials --probe-pay     # 追加：AppID 与商户号是否已绑定

**纪律：只输出结论与微信错误码，绝不打印任何密钥内容**（密钥只用于本地校验或签名）。
退出码：0 = 关键项全过（WARN 不影响退出码）；1 = 有 FAIL。
"""

from __future__ import annotations

import argparse
import json
import pathlib
import re
import sys
import uuid
from datetime import UTC, datetime

ROOT = pathlib.Path(__file__).resolve().parent.parent

RESULTS: list[tuple[str, bool, bool]] = []  # (label, ok, critical)


def _check(label: str, ok: bool, detail: str = "", *, critical: bool = True) -> None:
    RESULTS.append((label, bool(ok), critical))
    mark = "OK  " if ok else ("FAIL" if critical else "WARN")
    print(f"[{mark}] {label}" + (f" — {detail}" if detail else ""))


def _cert_expiry(cert) -> datetime:
    """取证书到期时间（兼容 cryptography 新旧属性名）。"""
    dt = getattr(cert, "not_valid_after_utc", None)
    if dt is not None:
        return dt
    return cert.not_valid_after.replace(tzinfo=UTC)


# ---------- ① 离线：配置格式与文件 ----------


def check_static(settings) -> None:
    appid = (settings.WECHAT_APP_ID or "").strip()
    _check(
        "WECHAT_APP_ID 格式（wx + 16 位十六进制）",
        bool(re.fullmatch(r"wx[0-9a-fA-F]{16}", appid)),
        appid or "未配置",
    )
    secret = (settings.WECHAT_APP_SECRET or "").strip()
    _check(
        "WECHAT_APP_SECRET 已配置",
        len(secret) >= 16,
        f"长度 {len(secret)}（不回显）" if secret else "未配置（微信一键登录会失败）",
    )
    _check("APP_ENV", True, settings.APP_ENV, critical=False)
    if settings.APP_ENV.strip().lower() != "production" and settings.DEBUG:
        print("[note] 开发态（DEBUG=true 且 APP_ENV≠production）：生产硬校验按设计跳过")

    provider = (settings.PAYMENT_PROVIDER or "").strip().lower()
    _check("PAYMENT_PROVIDER 合法（mock/wechat）", provider in ("mock", "wechat"), provider)
    if provider == "mock":
        _check(
            "线上支付通道",
            True,
            "mock（开发/演示：下单即时到账）——生产必须换 wechat 或关 PAYMENT_ENABLED",
            critical=False,
        )
        return
    check_payment_bundle(settings)


def check_payment_bundle(settings) -> None:
    """支付四件套 + 证书文件 + 回调 URL（离线可查的全部）。"""
    from cryptography.hazmat.primitives import serialization
    from cryptography.x509 import load_pem_x509_certificate

    required = (
        "WECHAT_MCH_ID",
        "WECHAT_API_KEY_V3",
        "WECHAT_CERT_SERIAL_NO",
        "WECHAT_PRIVATE_KEY_PATH",
        "WECHAT_PLATFORM_CERT_PATH",
        "WECHAT_PAY_NOTIFY_URL",
        "WECHAT_REFUND_NOTIFY_URL",
    )
    for key in required:
        val = (getattr(settings, key) or "").strip()
        detail = "已配置" if val else "缺失（生产环境会拒启）"
        if key == "WECHAT_API_KEY_V3" and val:
            detail = f"长度 {len(val)}（应为 32；不回显）"
        if key in ("WECHAT_PRIVATE_KEY_PATH", "WECHAT_PLATFORM_CERT_PATH") and val:
            detail = val
        _check(f"{key} 已配置", bool(val), detail)

    mch = (settings.WECHAT_MCH_ID or "").strip()
    _check(
        "WECHAT_MCH_ID 格式（8-10 位数字）", bool(re.fullmatch(r"\d{8,10}", mch)), mch or "未配置"
    )

    key_path = pathlib.Path((settings.WECHAT_PRIVATE_KEY_PATH or "").strip() or "/nonexistent")
    if key_path.is_file():
        try:
            serialization.load_pem_private_key(key_path.read_bytes(), password=None)
            _check(
                "商户私钥可解析（PEM、无密码）",
                True,
                f"{key_path.name}（{key_path.stat().st_size} 字节）",
            )
        except Exception as exc:  # 私钥有密码 / 不是 PEM / 文件损坏
            _check("商户私钥可解析（PEM、无密码）", False, type(exc).__name__)
    else:
        _check("商户私钥文件存在", False, str(key_path))

    # 商户证书（apiclient_cert.pem）：只在与私钥同目录时顺手核对序列号（不是启动必需项）
    cert_path = key_path.with_name("apiclient_cert.pem")
    if cert_path.is_file():
        cert = load_pem_x509_certificate(cert_path.read_bytes())
        serial = format(cert.serial_number, "X").upper()
        expect = (settings.WECHAT_CERT_SERIAL_NO or "").strip().upper()
        _check(
            "商户证书序列号与 .env 一致",
            serial == expect,
            f"证书 {serial} / .env {expect or '空'}",
        )
        days = (_cert_expiry(cert) - datetime.now(UTC)).days
        _check("商户证书未过期", days > 0, f"剩余 {days} 天")
    else:
        _check(
            "商户证书 apiclient_cert.pem（可选：用于核对序列号）",
            True,
            "同目录未找到——跳过（不影响启动）",
            critical=False,
        )

    plat_path = pathlib.Path((settings.WECHAT_PLATFORM_CERT_PATH or "").strip() or "/nonexistent")
    if plat_path.is_file():
        cert = load_pem_x509_certificate(plat_path.read_bytes())
        days = (_cert_expiry(cert) - datetime.now(UTC)).days
        _check("平台证书存在且未过期", days > 0, f"剩余 {days} 天")
        if 0 < days < 30:
            _check(
                "平台证书剩余 >30 天",
                False,
                f"仅剩 {days} 天——去管理端任务看板跑 wechat_cert_refresh",
                critical=False,
            )
    else:
        _check(
            "平台证书已首刷（任务 wechat_cert_refresh）",
            False,
            "文件不存在——部署后跑一次首刷任务即可（回调验签依赖它）",
        )

    for key in ("WECHAT_PAY_NOTIFY_URL", "WECHAT_REFUND_NOTIFY_URL"):
        url = (getattr(settings, key) or "").strip()
        _check(f"{key} 为 https 正式域名", url.startswith("https://"), url or "空")


# ---------- ② 在线：AppID + Secret 是否有效 ----------


def check_online(settings) -> None:
    import httpx

    appid = (settings.WECHAT_APP_ID or "").strip()
    secret = (settings.WECHAT_APP_SECRET or "").strip()
    if not (appid and secret):
        _check("在线自检（需 AppID + AppSecret）", False, "未配置，跳过", critical=False)
        return

    # access_token：能拿到就说明 AppID+Secret 有效（顺便验证服务器出口网络）
    try:
        from backend.integrations.wechat.service import WeChatService

        token = WeChatService().get_access_token()
        _check("access_token 获取（验证 AppID+Secret）", bool(token), "已获取（不回显）")
    except Exception as exc:
        _check("access_token 获取（验证 AppID+Secret）", False, str(exc)[:120])

    # code2session 探针：故意用一个假 code——凭据有效时微信回 40029（code 无效），
    # 凭据无效时回 40013（appid 无效）/40125（secret 无效）。这样不用真 code 也能验凭据。
    try:
        resp = httpx.get(
            "https://api.weixin.qq.com/sns/jscode2session",
            params={
                "appid": appid,
                "secret": secret,
                "js_code": "dmkwords_credential_probe",
                "grant_type": "authorization_code",
            },
            timeout=10,
        )
        data = resp.json()
        errcode = int(data.get("errcode", 0) or 0)
        if errcode == 40029:
            _check("code2session 探针", True, "凭据有效（40029 = 假 code 无效，属预期）")
        elif errcode in (40013, 40125):
            _check(
                "code2session 探针", False, f"凭据无效：errcode {errcode}（核对 AppID/AppSecret）"
            )
        else:
            _check("code2session 探针", errcode == 0, f"errcode {errcode}")
    except Exception as exc:
        _check("code2session 探针", False, f"请求失败：{type(exc).__name__}")


# ---------- ③ 探测：AppID 与商户号是否已绑定 ----------


def check_probe_pay(settings) -> None:
    """发一次**注定失败**的预支付请求，靠微信的错误码判断绑定关系（不产生任何资金动作）。

    判读（依赖微信的校验顺序，真通道实测为准）：
    - `APPID_MCHID_NOT_MATCH` → **未绑定**（去商户平台「AppID 账号管理」关联）
    - `SIGN_ERROR` / 401 → 私钥与证书序列号不匹配（签名没过，绑定问题无从判断）
    - 其它参数类错误（如 openid 不存在）→ **绑定与签名都通了**，只是探针参数是假的
    """
    import httpx

    missing = [
        k
        for k in ("WECHAT_MCH_ID", "WECHAT_CERT_SERIAL_NO", "WECHAT_PRIVATE_KEY_PATH")
        if not (getattr(settings, k) or "").strip()
    ]
    if missing:
        _check("AppID↔商户号绑定探测", False, f"缺 {'/'.join(missing)}，跳过", critical=False)
        return
    try:
        from backend.integrations.wechat.pay_v3 import WeChatPayV3

        gateway = WeChatPayV3()
    except Exception as exc:
        _check("支付网关可实例化（私钥/平台证书）", False, str(exc)[:120])
        return

    url = "/v3/pay/transactions/jsapi"
    body = json.dumps(
        {
            "appid": (settings.WECHAT_APP_ID or "").strip(),
            "mchid": (settings.WECHAT_MCH_ID or "").strip(),
            "description": "dmkwords credential probe",
            "out_trade_no": f"DMKPROBE{uuid.uuid4().hex[:12].upper()}",
            "notify_url": (settings.WECHAT_PAY_NOTIFY_URL or "").strip()
            or "https://example.invalid/notify",
            "amount": {"total": 1, "currency": "CNY"},
            "payer": {"openid": "dmkwords_probe_openid"},
        },
        ensure_ascii=False,
    )
    try:
        # 复用生产签名逻辑（私钥 + 证书序列号）；这里只是"探针"，不落任何订单
        headers = {
            "Authorization": gateway._build_auth_header("POST", url, body),
            "Content-Type": "application/json",
            "Accept": "application/json",
        }
        resp = httpx.post(gateway.BASE_URL + url, content=body, headers=headers, timeout=10)
        try:
            data = resp.json()
        except Exception:
            data = {}
        code = str(data.get("code", "") or "")
        message = str(data.get("message", "") or "")[:120]
        if resp.status_code in (200, 202):
            _check("AppID↔商户号绑定探测", True, "预支付意外成功（参数竟被接受，需人工看一眼）")
        elif code == "APPID_MCHID_NOT_MATCH":
            _check("AppID↔商户号绑定探测", False, f"{code}：AppID 与商户号尚未绑定——{message}")
        elif code == "SIGN_ERROR" or resp.status_code == 401:
            _check(
                "AppID↔商户号绑定探测",
                False,
                f"签名未通过（{code or resp.status_code}）——核对证书序列号/私钥",
            )
        else:
            _check(
                "AppID↔商户号绑定探测",
                True,
                f"微信返回 {code or resp.status_code}（参数类错误=预期：绑定与签名都已通过）",
            )
    except Exception as exc:
        _check("AppID↔商户号绑定探测", False, f"请求失败：{type(exc).__name__}")


def main() -> int:
    parser = argparse.ArgumentParser(description="微信凭证自检（只输出结论与错误码，不打印密钥）")
    parser.add_argument("--online", action="store_true", help="追加：AppID+Secret 有效性检查")
    parser.add_argument("--probe-pay", action="store_true", help="追加：AppID↔商户号绑定探测")
    args = parser.parse_args()

    if str(ROOT) not in sys.path:
        sys.path.insert(0, str(ROOT))
    from backend.config import get_settings

    print("=== 微信凭证自检（docs/21 §2.4）===")
    check_static(get_settings())
    if args.online:
        print("--- 在线检查（会向微信发一次探针请求）---")
        check_online(get_settings())
    if args.probe_pay:
        print("--- 绑定探测（发一次注定失败的预支付请求，无资金动作）---")
        check_probe_pay(get_settings())

    failed = [label for label, ok, critical in RESULTS if critical and not ok]
    warned = [label for label, ok, critical in RESULTS if not critical and not ok]
    print(
        f"\n结论：{len(RESULTS)} 项检查｜FAIL {len(failed)}｜WARN {len(warned)}"
        + (f"\n  FAIL：{'; '.join(failed)}" if failed else "")
        + (f"\n  WARN：{'; '.join(warned)}" if warned else "")
    )
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
