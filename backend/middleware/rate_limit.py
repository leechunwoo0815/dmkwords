# backend/middleware/rate_limit.py
"""限流（两档）

① **逐端点装饰器** `rate_limit(...)`：登录/支付/验证码等敏感点，按"路径+IP"收紧；
② **全局写中间件** `WriteRateLimitMiddleware`（第七轮 P2-6）：全体写端点，按"路径类+身份"限流。

内存实现（单进程）。生产多副本时每副本独立计数，需要全局配额应换 Redis 方案。
"""

import hashlib
import logging
import re
import threading
import time

from fastapi import Request
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.responses import JSONResponse

from backend.common.exceptions import RateLimitError

logger = logging.getLogger(__name__)


class RateLimiter:
    """基于滑动窗口的内存限流器（**线程安全**）。

    第八轮审查 P1-4 ③：原先无锁——`rate_limit` 依赖跑在同步端点的线程池里、全局中间件
    跑在事件循环线程里，两处并发改同一个 dict → 实测（8 线程 × 6000 键）**7/8 抛
    `RuntimeError: dictionary changed size during iteration`**，即挂 `rate_limit` 的
    登录/支付端点会偶发 500。现在 check + 清扫整体持锁（临界区只有几次 list 操作，无 I/O）。
    """

    #: 惰性清扫间隔（每 N 次检查清一次）：全局限流把键空间从"6 个端点"扩到
    #: "全体身份 × 路径类"——不清理等于自己制造内存增长（第七轮 P2-6）。
    _SWEEP_EVERY = 512
    #: 桶的陈旧阈值（秒）：大于所有调用点窗口（现最大 3600），超过即可安全回收。
    _STALE_AFTER = 3600
    #: 桶数**硬上限**（内存兜底，第八轮审查 P1-4）：路径侧已由"只对真实路由建桶"收口，
    #: 但身份侧仍不可信（未认证者可换随机 token / 换源 IP 造桶）。到顶后**不再新建桶**，
    #: 新键一律并入单一溢出桶——**不做"淘汰最旧桶"**：淘汰等于给攻击者一条"清空我自己的
    #: 计数"的旁路（把别人打满的桶挤出去即可继续刷），有界内存不能拿限流有效性换。
    #: 量级估算：满桶（60 个时间戳）约 2KB，10k 桶最坏 ~20MB。
    #: 不变量：**常规桶 ≤ `_MAX_BUCKETS`，加溢出桶总计 ≤ `_MAX_BUCKETS + 1`**。
    _MAX_BUCKETS = 10_000
    #: 溢出桶键：到顶后新出现的键共用它（共用预算 = 攻击下新身份被整体降速，可接受）
    _OVERFLOW_KEY = "__overflow__"

    def __init__(self):
        # {key: [timestamp, ...]}
        self._requests: dict[str, list[float]] = {}
        self._checks = 0
        self._lock = threading.Lock()

    def check(self, key: str, max_requests: int, window_seconds: int) -> None:
        """检查是否超过频率限制，超过则抛 RateLimitError"""
        now = time.time()
        cutoff = now - window_seconds

        with self._lock:
            if key not in self._requests and len(self._requests) >= self._MAX_BUCKETS:
                key = self._OVERFLOW_KEY
            # 清理过期记录
            recent = [t for t in self._requests.get(key, ()) if t > cutoff]
            over = len(recent) >= max_requests
            if over:
                self._requests[key] = recent
            else:
                recent.append(now)
                self._requests[key] = recent
            self._sweep_locked(now)

        if over:
            logger.warning(
                "Rate limit hit: key=%s, max=%d, window=%ds",
                key,
                max_requests,
                window_seconds,
            )
            raise RateLimitError(f"请求过于频繁，请 {window_seconds} 秒后再试")

    def _sweep_locked(self, now: float) -> None:
        """惰性清扫：窗口外的陈旧桶整键回收（键数有界，内存不随历史身份/路径累积）。

        调用方须已持 `self._lock`（第七轮 P2-6 的清扫本身也是并发读写点：
        边遍历边被另一个线程删除 = `RuntimeError`）。
        """
        self._checks += 1
        if self._checks % self._SWEEP_EVERY:
            return
        stale = now - self._STALE_AFTER
        for key in [k for k, v in self._requests.items() if not v or max(v) < stale]:
            del self._requests[key]

    def bucket_count(self) -> int:
        """当前桶数（运维/用例可观测；正常应在 `_MAX_BUCKETS` 以内）。"""
        with self._lock:
            return len(self._requests)


# 全局限流器实例
_limiter = RateLimiter()


def _client_ip(request: Request) -> str:
    """限流客户端 IP：**默认只信传输层**（`request.client.host`）。

    WM12-C（审查 P1-5）：原实现无条件取 `X-Forwarded-For` 首段——客户端自填
    `X-Forwarded-For: 10.9.1.x` 就能每个请求换一个限流桶（实测 20 个并发伪造 XFF 全部
    通过 `3/60` 的登录限流）。现在只有 `TRUSTED_PROXY_COUNT>0`（显式声明"我前面有几层
    可信代理"）才读 XFF，且取**右起第 N 段**：每层可信代理会在右侧追加它亲眼看到的上游
    地址，最右段来自最近的代理，客户端伪造的左侧段一律不采信（docs/20 §五/§八）。
    """
    from backend.config import get_settings

    trusted = int(getattr(get_settings(), "TRUSTED_PROXY_COUNT", 0) or 0)
    if trusted > 0:
        parts = [
            p.strip() for p in request.headers.get("x-forwarded-for", "").split(",") if p.strip()
        ]
        if len(parts) >= trusted:
            return parts[-trusted]
    return request.client.host if request.client else "unknown"


def rate_limit(max_requests: int = 10, window_seconds: int = 60):
    """限流依赖注入

    用法：
        @router.post("/login", dependencies=[Depends(rate_limit(5, 60))])
    """

    def _check(request: Request):
        key = f"{request.url.path}:{_client_ip(request)}"
        _limiter.check(key, max_requests, window_seconds)

    return _check


# ---------- 全局写限流（第七轮 P2-6） ----------

#: 写方法：只拦这四种。GET/HEAD/OPTIONS 是读——媒体流、大屏轮询、列表页不受影响。
_WRITE_METHODS = frozenset({"POST", "PUT", "PATCH", "DELETE"})

#: 豁免前缀（写方法才走到这里）：
#:   `/health`  健康检查（探针高频，非业务写）
#:   `/api/pay/` 微信支付/退款回调——**微信会重试**，限流会把回调挡在门外 = 丢单
#:   `/docs` `/redoc` `/openapi.json`  FastAPI 内建文档（非业务写；生产本就关闭）
_EXEMPT_PREFIXES = ("/health", "/api/pay/", "/docs", "/redoc", "/openapi.json")

#: 路径归一化：数字段 / 长十六进制段（资源 id、uuid、hex token）→ `{id}`。
#: 不归一化 = 换 id 就换桶（`/books/1/cover` 与 `/books/2/cover` 各算一份预算 = 形同虚设），
#: 且桶数随资源数无界增长（内存放大）。
_PATH_PARAM_RE = re.compile(r"/(?:\d+|[0-9a-fA-F]{16,})(?=/|$)")

#: 更紧的路径类预算 (前缀, 后缀, (max, window))：按"单次请求的资源开销"收紧——
#: 批量导入一次 1000 行吃内存、上传类单次落盘吃磁盘；其余写走配置默认值。
_WRITE_BUDGET_RULES: tuple[tuple[str, str, tuple[int, int]], ...] = (
    ("/api/admin/books/import", "", (6, 60)),
    ("/api/admin/books/", "/cover", (20, 60)),
    ("/api/admin/books/", "/audio", (20, 60)),
    ("/api/admin/activities/", "/cover", (20, 60)),
    ("/api/admin/activities/", "/detail-images", (20, 60)),
    ("/api/miniapp/children/", "/avatar", (20, 60)),
)


def normalize_path(path: str) -> str:
    """限流分桶用的路径类（资源 id 归一化成 `{id}`）。"""
    return _PATH_PARAM_RE.sub("/{id}", path)


# ---------- "这个路径真的存在吗"（第八轮审查 P1-4 ①） ----------

#: 未命中任何真实路由的写请求共用的路径类（所有 404 写请求挤一个桶）
UNROUTED_PATH = "__unrouted__"

_KNOWN_ROUTE_RE: re.Pattern[str] | None = None
_KNOWN_ROUTE_FAILED = False
_KNOWN_ROUTE_LOCK = threading.Lock()


def _route_to_regex(path: str) -> str:
    """契约路径模板 → 正则：`{param}` 一段非斜杠（写端点无 `{p:path}` 转换器；
    仅两条 GET 用，且 GET 不进本中间件）。"""
    return "/".join(
        "[^/]+" if seg.startswith("{") and seg.endswith("}") else re.escape(seg)
        for seg in path.split("/")
    )


def _known_route_re(app) -> re.Pattern[str] | None:
    """全部真实路由的合并正则（懒构建一次；进程内路由在 import 期就注册完了）。

    **为什么用 OpenAPI 契约面而不是 `app.routes`**：本版 FastAPI 的 `include_router`
    结果在 `app.routes` 里是 `_IncludedRouter` 包装（**无 `path` 属性**，只扫顶层会得到
    0 条业务路由）。契约面是本仓既有先例（`features/steps/account_steps.py` 枚举登录面同款）。

    构建失败（模型异常等）→ 返回 None，调用方**按"视为已路由"降级**（回到 P1-4 之前的行为，
    内存仍有 `_MAX_BUCKETS` 兜底），绝不因为一个防御性检查把全体写请求打成 500。
    """
    global _KNOWN_ROUTE_RE, _KNOWN_ROUTE_FAILED
    if _KNOWN_ROUTE_RE is not None or _KNOWN_ROUTE_FAILED:
        return _KNOWN_ROUTE_RE
    with _KNOWN_ROUTE_LOCK:
        if _KNOWN_ROUTE_RE is None and not _KNOWN_ROUTE_FAILED:
            try:
                alts = [_route_to_regex(p) for p in app.openapi()["paths"]]
                _KNOWN_ROUTE_RE = re.compile("^(?:" + "|".join(alts) + ")/?$")
            except Exception:  # noqa: BLE001 — 降级路径，任何异常都不该影响请求
                logger.exception("路由契约面构建失败——限流退回'全部路径视为已路由'")
                _KNOWN_ROUTE_FAILED = True
    return _KNOWN_ROUTE_RE


def is_routed_path(app, path: str) -> bool:
    """该原始路径是否命中真实路由（契约面不可用时按 True 降级）。

    为什么中间件要判这个（P1-4 ①）：全局中间件在**路由匹配之前**执行，原先"是不是写方法"
    就建桶 → `POST /x-probe-<随机>` 每个随机路径各建一桶（`_STALE_AFTER=3600`、清扫每 512 次
    才跑一次）→ **未认证即可把内存打满**。现在未命中的写请求一律并入 `UNROUTED_PATH` 桶。
    """
    pattern = _known_route_re(app)
    return True if pattern is None else pattern.match(path) is not None


def _identity(request: Request) -> str:
    """限流身份：**优先登录态**——`Authorization` 里**凭证本体**的 sha1 摘要前 16 位
    （不把 token 原文留在内存键/日志里）；无头回落 `_client_ip`（只信传输层，口径同登录限流）。

    第八轮审查 P1-4 ②：原先对**整个头原文**做 sha1 —— `Bearer tok` / `bearer tok` /
    `BEARER tok` / `Bearer  tok`（多空格）各是一个桶（实测同一 token 5 种写法 = **5 个桶**），
    等于把一个身份的写入预算放大 5 倍（攻击者还能继续变体）。
    现在按 HTTP 语义归一：**空白折叠 + scheme 大小写不敏感**，取凭证本体再哈希。
    与两端取 token 的方式对齐（管理端 `HTTPBearer.credentials`、家长端
    `authorization.replace("Bearer ", "").strip()` 的目标都是"凭证本体"）。
    """
    parts = (request.headers.get("authorization") or "").split()
    if parts:
        cred = parts[1] if len(parts) > 1 else parts[0]
        return "tok:" + hashlib.sha1(cred.encode("utf-8")).hexdigest()[:16]
    return "ip:" + _client_ip(request)


def _write_budget(path: str) -> tuple[int, int]:
    """该写路径类的预算 (max_requests, window_seconds)：命中更紧规则用规则值，否则用配置默认。"""
    for prefix, suffix, budget in _WRITE_BUDGET_RULES:
        if path.startswith(prefix) and (not suffix or path.endswith(suffix)):
            return budget
    from backend.config import get_settings

    return int(getattr(get_settings(), "RATE_LIMIT_WRITE_PER_MINUTE", 60)), 60


class WriteRateLimitMiddleware(BaseHTTPMiddleware):
    """全局写限流：**一处生效，全体写端点自动覆盖**。

    为什么用中间件而不是逐端点挂装饰器：写端点 111 个且持续新增，逐处挂必漏
    （第七轮实测：110 个写端点只有 6 处挂了 `rate_limit`）。判据只看"是不是写方法 +
    路径类"，与路由定义无关，新增端点自动纳入。

    响应与 `business_exception_handler` 同形（`{"detail": ...}` + `Retry-After`）：
    这里**不能 raise** —— 本中间件在 ExceptionMiddleware 之外，raise 只会变成 500；
    同形还让两端前端（`request.js` / `client.ts`）按 `detail` 弹提示，**零前端改动**。
    """

    async def dispatch(self, request: Request, call_next):
        raw_path = request.url.path
        path = normalize_path(raw_path)
        if request.method not in _WRITE_METHODS or path.startswith(_EXEMPT_PREFIXES):
            return await call_next(request)
        # P1-4 ①：未命中真实路由的写请求（含扫描器/爆破的随机路径）共用兜底桶——
        # 否则"换个随机路径 = 换个新桶"，未认证就能把桶表撑爆。
        if not is_routed_path(request.app, raw_path):
            path = UNROUTED_PATH

        max_requests, window_seconds = _write_budget(path)
        if max_requests <= 0:  # 0 = 关闸（压测/排障；生产禁）
            return await call_next(request)

        key = f"write:{path}:{_identity(request)}"
        try:
            _limiter.check(key, max_requests, window_seconds)
        except RateLimitError as exc:
            return JSONResponse(
                status_code=exc.code,
                content={"detail": exc.message},
                headers={"Retry-After": str(window_seconds)},
            )
        return await call_next(request)
