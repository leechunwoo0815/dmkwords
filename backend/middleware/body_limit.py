# backend/middleware/body_limit.py — 请求体上限（第八轮审查 P2-19）
"""应用层也要有 body 闸——**不能只靠 nginx 的 `client_max_body_size`**。

**要防什么**（P2-19 实测✓）：nginx 侧是 200m，而**回环直连后端（127.0.0.1:8002）没有 nginx**；
更关键的是 `/api/pay/wechat/notify` 是**无登录态**端点（微信服务器不带我们的 token，凭平台证书
验签），它的实现必须 `await request.body()` 拿原始报文字节（签名对原始串算）——
**未认证 + 全量读进内存 + 无上限** = 一条 curl 就能把单进程 uvicorn 的内存打满
（本项目单进程、无 workers，整站一起不可用）。

上限分三档（**按"这个端点正常能有多大"给，不搞一刀切**）：
  · 无凭证回调（`/api/pay/**`）：**64KB**——微信支付/退款通知是几 KB 的 JSON；
  · 上传类（`multipart/form-data`）：**200MB**——与 nginx `client_max_body_size 200m` 同口径
    （音频/大图；UploadFile 会落临时文件，不是全量驻留内存）；
  · 其余 JSON 业务请求：**8MB**——远超任何正常业务载荷（最大的活动图文详情也就几十 KB）。

两层拦截：
  ① `Content-Length` 声明超限 → **不读 body 直接 413**（正常客户端都走这条；上传类只走这条）；
  ② 非上传类请求**整包读进来（边读边计数）再重放给下游** → `Transfer-Encoding: chunked`
     这种"声明不了长度、可以无限流"的形态也拦得住。

**实测踩过的两个坑（写下来防复发）**：
  · 第一版在 `receive` 里抛异常想让上层转 413——Starlette 的 `BaseHTTPMiddleware` 把 receive
    包在 anyio task group 里，异常以 **ExceptionGroup 逃逸**（TestClient 直接抛给调用方，
    生产上是连接被打断而不是 413）。判据是"**在下游开始响应之前返回 413**"，必须在中间件层造响应。
  · 第二版用 `BaseHTTPMiddleware` + `request._receive = 重放`——**下游收不到重放的 body**：
    下游的 Request 是拿 `scope + 原始 receive` 新建的，patch 中间件自己那个 Request 对象
    影响不到它（实测：登录接口 422 "Field required"，body 被中间件读走后就没了）。
    所以这里是**纯 ASGI 中间件**（自己拿 receive/send，重放的是真正传给下游的那个 receive）。

**已知残留（如实标注）**：`multipart` + chunked（无 Content-Length）组合只有 nginx 的 200m 兜着——
该形态需要先登录（上传端点全部要 `*.manage` 权限），且 Starlette 对 multipart 是**落临时文件**
不是驻留内存（填磁盘不填内存）；真要拦得往 Starlette 的 multipart 解析层加计数，成本与风险
不成正比，留档在 docs/09 第一百零五批。
"""

from __future__ import annotations

import json

#: 无凭证回调端点的前缀（`/api/pay/wechat/notify` 等）——报文很小，给硬上限
CALLBACK_PATH_PREFIXES = ("/api/pay/",)
CALLBACK_MAX_BODY_BYTES = 64 * 1024
#: 上传类（multipart）上限，与 nginx client_max_body_size 200m 同口径
UPLOAD_MAX_BODY_BYTES = 200 * 1024 * 1024
#: 其余 JSON 业务请求上限
DEFAULT_MAX_BODY_BYTES = 8 * 1024 * 1024


def limit_for(path: str, content_type: str) -> int:
    """按路径/内容类型取上限（**判定只在这一处**）。"""
    if any(path.startswith(p) for p in CALLBACK_PATH_PREFIXES):
        return CALLBACK_MAX_BODY_BYTES
    if content_type.startswith("multipart/form-data"):
        return UPLOAD_MAX_BODY_BYTES
    return DEFAULT_MAX_BODY_BYTES


async def _send_json(send, status: int, detail: str) -> None:
    # R11-c/C1：413 由本中间件**直接发**（绕开内层 SecurityHeadersMiddleware），
    # 必须在这里补齐同一份安全头（否则最大的请求反而拿到 0 头）。
    from backend.middleware.security_headers import SECURITY_HEADERS

    payload = json.dumps({"detail": detail}, ensure_ascii=False).encode()
    headers = [
        (b"content-type", b"application/json"),
        (b"content-length", str(len(payload)).encode()),
    ]
    headers += [(k.lower().encode(), v.encode()) for k, v in SECURITY_HEADERS.items()]
    await send(
        {
            "type": "http.response.start",
            "status": status,
            "headers": headers,
        }
    )
    await send({"type": "http.response.body", "body": payload})


class BodySizeLimitMiddleware:
    """请求体上限（纯 ASGI：声明超限直接 413；无声明则整包读 + 计数后重放）。"""

    def __init__(self, app) -> None:
        self.app = app

    async def __call__(self, scope, receive, send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        headers = {k.lower(): v for k, v in scope.get("headers", [])}
        declared = headers.get(b"content-length", b"").decode("latin-1")
        content_type = headers.get(b"content-type", b"").decode("latin-1")
        limit = limit_for(scope.get("path", ""), content_type)
        # 声明超限：**所有方法**都拦（这条不需要读 body，零成本）
        if declared.isdigit() and int(declared) > limit:
            await _send_json(send, 413, f"请求体过大（上限 {limit // 1024}KB）")
            return
        # 无 body 的方法到此为止（**实测踩过**：给 GET 也走下面的重放路径会把响应流截断，
        # 见 `_replay` 的说明）；GET 带 body 由声明闸 + nginx 200m 兜住。
        if scope.get("method", "") not in ("POST", "PUT", "PATCH", "DELETE"):
            await self.app(scope, receive, send)
            return
        if content_type.startswith("multipart/form-data"):
            # 上传类：只走声明闸（body 由 Starlette 落临时文件，不在这里整包读）
            await self.app(scope, receive, send)
            return

        body = b""
        while True:
            message = await receive()
            if message["type"] == "http.disconnect":
                await _send_json(send, 400, "请求中断")
                return
            body += message.get("body", b"")
            if len(body) > limit:
                await _send_json(send, 413, f"请求体过大（上限 {limit // 1024}KB）")
                return
            if not message.get("more_body"):
                break

        replayed = False

        async def _replay():
            """重放一次完整 body；**之后交还给原始 receive**。

            为什么不"第二次就返回 `http.disconnect`"：那是**伪造客户端断开**——
            Starlette 的响应流（FileResponse/StreamingResponse）会在后台监听 receive，
            收到 disconnect 就取消发送 → **响应被截断**（实测：导入模板下载拿到半截文件，
            测试报 `BadZipFile: File is not a zip file`）。交还给原始 receive 才是正确语义：
            真有断开由它如实上报。
            """
            nonlocal replayed
            if not replayed:
                replayed = True
                return {"type": "http.request", "body": body, "more_body": False}
            return await receive()

        await self.app(scope, _replay, send)
