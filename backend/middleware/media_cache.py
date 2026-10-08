# backend/middleware/media_cache.py — 媒体响应缓存头（单一口径，docs/15 §22.5）
"""内容寻址文件长缓存、其余协商缓存——**一处生效，13 个媒体下发端点全覆盖**。

为什么用中间件而不是逐端点写：媒体下发端点有十几处（审查 §三 A3 清单），逐处写必漏；
判据只看"响应类型是媒体 + URL 末段是不是机器生成的名字"，与文件存在哪无关。

口径（上线前审查 P2-7）：
  名字末段是**机器生成的令牌**（`{stem}_{内容指纹/随机 token}`）→ 文件写完就不再变
  （重传会生成新名字）→ `public, max-age=31536000, immutable`，CDN/端上长缓存安全；
  其余媒体 → `no-cache`，必须回源复验（ETag 协商）。后者含两类：**就地覆盖**的
  （如 `posters/poster_<child_id>.jpg`）与**无可判令牌**的（如评估报告图 `observation/child_N/<hex>.jpg`
  ——它是 `<16位hex>.jpg`、不带下划线，判不出就保守走协商缓存，别为它放宽规则误伤 posters）。
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
REVALIDATE = "no-cache"


class MediaCacheMiddleware(BaseHTTPMiddleware):
    """给媒体响应补 Cache-Control（已有则不覆盖——保留端点自己更精确的声明）。"""

    async def dispatch(self, request: Request, call_next):
        response = await call_next(request)
        content_type = response.headers.get("content-type", "")
        if response.status_code == 200 and content_type.startswith(MEDIA_CONTENT_TYPES):
            if "cache-control" not in response.headers:
                response.headers["Cache-Control"] = (
                    IMMUTABLE if GENERATED_TAG_RE.search(request.url.path) else REVALIDATE
                )
        return response
