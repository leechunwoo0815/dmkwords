# backend/middleware/media_cache.py — 媒体响应缓存头（单一口径，docs/15 §22.5）
"""内容寻址文件长缓存、其余协商缓存——**一处生效，13 个媒体下发端点全覆盖**。

为什么用中间件而不是逐端点写：媒体下发端点有十几处（审查 §三 A3 清单），逐处写必漏；
判据只看"响应类型是媒体 + URL 末段是不是机器生成的名字"，与文件存在哪无关。

口径（上线前审查 P2-7）：
  名字末段是**机器生成的令牌**（`{stem}_{内容指纹/随机 token}`）→ 文件写完就不再变
  （重传会生成新名字）→ `max-age=31536000, immutable`，CDN/端上长缓存安全；
  其余媒体 → `no-cache`，必须回源复验（ETag 协商）。后者含两类：**URL 里没有令牌**的
  （海报 `/circle/children/{id}/poster`——它的内容指纹只在**落盘文件名**上，不进 URL；
  报告图 `reports/weekly_1.jpg`）与**判不出令牌**的（评估报告图 `observation/child_N/<hex>.jpg`
  ——它是 `<16位hex>.jpg`、不带下划线，判不出就保守走协商缓存，别为它放宽规则误伤别的）。

**带凭证的请求一律 `private`**（第八轮审查 P2-24）：
  `public` 的语义是"任何共享缓存都可以存下来给**别人**用"。而家长周报图
  （`/api/miniapp/reports/{kind}/image?child_id=N&token=…`）文件名同样带内容摘要 →
  原先命中长缓存分支，被标成 `public, immutable` **一年**：一旦前面挂了 CDN/反代，
  孩子的成长报告（含姓名、书目、统计）就被公共缓存持有——撤 token、撤孩子都撤不回它。
  判据取"请求是否带凭证"（Authorization 头或 `?token=`）：带凭证 = 响应是特定身份
  才能拿到的内容 → `private`（浏览器/端上仍可长缓存，共享缓存不得存）。

  2026-10-24 订正（第九轮 E 批）：本段原写"公开素材（如馆藏封面 `/api/miniapp/covers/{book_id}`
  无 token）不受影响，保持 `public`"——**与实况不符**：封面端点同样要求凭证（无 token 实测 401），
  同族的活动封面 / 圈帖图 / 名片海报亦全部 401。**现状：小程序侧没有任何匿名媒体端点**，
  因此 `public`（`IMMUTABLE`）分支当前无生产消费者，属**防御性保留**（将来若确有公开素材出口，
  不带凭证的请求会落到它，不需要再改中间件）；不要据此以为存在匿名素材面。
非媒体响应（JSON/HTML/导出）不动——它们的缓存策略归各自端点。
"""

from __future__ import annotations

import re

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request

#: 媒体响应类型前缀
MEDIA_CONTENT_TYPES = ("image/", "audio/")

#: 机器生成的文件名令牌：`_<8~32 位十六进制>.<ext>` 结尾
#: （`file_storage.content_tag` 默认 12 位、报告图 16 位、周报 10 位、随机 token_hex(6) 12 位）
GENERATED_TAG_RE = re.compile(r"_[0-9a-f]{8,32}\.[a-z0-9]{2,5}$")

IMMUTABLE = "public, max-age=31536000, immutable"
IMMUTABLE_PRIVATE = "private, max-age=31536000, immutable"
REVALIDATE = "no-cache"
REVALIDATE_PRIVATE = "private, no-cache"

#: 凭证出现在 query 里的键名（`<img>`/`<audio>` 带不了 Authorization 头，媒体走 query token）
AUTH_QUERY_KEYS = ("token",)


def request_has_credentials(request: Request) -> bool:
    """请求是否带凭证（Authorization 头或 `?token=`）——决定 `public` 还是 `private`。"""
    if request.headers.get("authorization", "").startswith("Bearer "):
        return True
    return any(key in request.query_params for key in AUTH_QUERY_KEYS)


class MediaCacheMiddleware(BaseHTTPMiddleware):
    """给媒体响应补 Cache-Control（已有则不覆盖——保留端点自己更精确的声明）。"""

    async def dispatch(self, request: Request, call_next):
        response = await call_next(request)
        content_type = response.headers.get("content-type", "")
        if response.status_code == 200 and content_type.startswith(MEDIA_CONTENT_TYPES):
            if "cache-control" not in response.headers:
                authed = request_has_credentials(request)
                if GENERATED_TAG_RE.search(request.url.path):
                    response.headers["Cache-Control"] = IMMUTABLE_PRIVATE if authed else IMMUTABLE
                else:
                    response.headers["Cache-Control"] = REVALIDATE_PRIVATE if authed else REVALIDATE
        return response
