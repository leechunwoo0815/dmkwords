# backend/middleware/security_headers.py — 安全响应头（第八轮审查 P2-17）
"""一处下发全站安全响应头（API 与媒体都经这里；SPA 的 HTML 由 nginx 同款下发）。

**要防什么**（P2-17 实测✓：全站零安全头，nginx 模板也没有）：
  · **点击劫持**：管理端 token 存 localStorage，页面被塞进第三方 iframe 后，
    透明覆盖层可以诱导超管点「禁用员工/导出全量」——`X-Frame-Options` / `frame-ancestors`
    是唯一能拦住的层（同源策略管不了"被别的站嵌"）。
  · **MIME 嗅探**：上传的图片/音频若被嗅探成 HTML，配合媒体端点就是存储型 XSS——
    `X-Content-Type-Options: nosniff`。
  · **Referer 泄漏**：媒体 URL 带 `?token=`（30 天管理员凭证），外链跳转会把整条 URL
    放进 Referer 送给第三方站 → `Referrer-Policy: no-referrer`。
  · **协议降级**：首访 http 被劫持（`Strict-Transport-Security`，仅 https 响应上发——
    明文 HTTP 上发 HSTS 是无效指令，浏览器只认 TLS 通道上的）。
  · **能力滥用**：本项目不用摄像头/麦克风/定位，`Permissions-Policy` 显式关掉。

CSP 口径：**API/媒体响应一律 `default-src 'none'`**（它们是 JSON/图片/音频，不需要加载
任何子资源——这是最严的策略，零误伤）。SPA 的 HTML 需要更宽的策略（外链字体 + 打包脚本），
由 nginx 模板下发（`deploy/nginx/dmkwords.conf.template`，与这里同口径注释互指）。

**已有则覆盖**（与 media_cache 的"已有则不覆盖"相反）：安全头是**下限**，端点自己写漏了
也必须被补上；真要放宽（如某端点要允许被嵌）得改这里，不允许逐端点偷偷放宽。
"""

from __future__ import annotations

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request

#: 恒发的安全头（API/媒体/探针一律适用）
SECURITY_HEADERS = {
    "X-Content-Type-Options": "nosniff",
    "X-Frame-Options": "DENY",
    "Content-Security-Policy": "default-src 'none'; frame-ancestors 'none'; base-uri 'none'",
    "Referrer-Policy": "no-referrer",
    "Permissions-Policy": "geolocation=(), camera=(), microphone=()",
    "Cross-Origin-Opener-Policy": "same-origin",
}

#: 仅在 https 响应上发（明文上发无效，且会污染本地 http 开发）
HSTS_VALUE = "max-age=31536000; includeSubDomains"


def is_https(request: Request) -> bool:
    """请求是不是"浏览器看到的 https"——反代后面看 `X-Forwarded-Proto`（nginx 覆盖式写入）。"""
    if request.url.scheme == "https":
        return True
    return request.headers.get("x-forwarded-proto", "").lower() == "https"


class SecurityHeadersMiddleware(BaseHTTPMiddleware):
    """给每个响应补安全头（含 4xx/5xx —— 错误页同样会被嵌）。"""

    async def dispatch(self, request: Request, call_next):
        response = await call_next(request)
        for key, value in SECURITY_HEADERS.items():
            response.headers[key] = value
        if is_https(request):
            response.headers["Strict-Transport-Security"] = HSTS_VALUE
        return response
