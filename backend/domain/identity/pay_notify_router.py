# backend/domain/identity/pay_notify_router.py — 微信支付回调入口（无登录态，凭平台证书验签）
"""为什么单开一个 Router 而不是挂进 identity：这条路径**不能用家长登录态**
（微信服务器不带我们的 token），它的身份凭证是 `Wechatpay-Signature` 头 + 平台证书验签。

两个"不得不这样写"的地方：
1. **必须拿原始报文字节**（`await request.body()`）——签名是对原始串算的，先解析成 dict
   再序列化回去，空白/键序一变签就不过。所以端点是 `async def`（只有它才能 await body）。
2. 拿到字节后**丢回线程池**执行同步 Service（`run_in_threadpool`）——项目里 Service 一律同步
   （审查 P1-2 的教训：同步重活跑在事件循环里会卡死整个进程），这里保持同一纪律。

应答约定（与普通业务端点不同）：微信只认它的规则——**HTTP 200 才停止重试**。
所以处理结果由 Service 给出 `(状态码, 响应体)`，Router 只负责照搬，不加工。
"""

from __future__ import annotations

from functools import partial

from fastapi import APIRouter, Depends, Header, Request
from fastapi.responses import JSONResponse
from sqlalchemy.orm import Session
from starlette.concurrency import run_in_threadpool

from backend.database import get_db

router = APIRouter(tags=["payment"])


@router.post("/wechat/notify")
async def wechat_pay_notify(
    request: Request,
    wechatpay_signature: str = Header("", alias="Wechatpay-Signature"),
    wechatpay_timestamp: str = Header("", alias="Wechatpay-Timestamp"),
    wechatpay_nonce: str = Header("", alias="Wechatpay-Nonce"),
    db: Session = Depends(get_db),
):
    """微信支付结果通知：验签 → 解密 → 金额比对 → 流水号查重 → 幂等入账。

    处理细则（三层守卫，模式手册 P3）见 `backend/domain/identity/payment_service.py`。
    """
    from backend.domain.identity.payment_service import PaymentService

    raw = await request.body()
    handler = partial(
        PaymentService(db).handle_notify,
        body=raw.decode("utf-8", errors="replace"),
        signature=wechatpay_signature,
        timestamp=wechatpay_timestamp,
        nonce=wechatpay_nonce,
    )
    status_code, body = await run_in_threadpool(handler)
    return JSONResponse(status_code=status_code, content=body)
