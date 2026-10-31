# tests/unit/test_admin_auth_coverage_20261031.py — 管理端鉴权覆盖常驻判据（R-6，复审 docs/29）
"""「137 个 admin 端点零漏挂鉴权」此前是一次性人工扫描的结论，仓库里没有可回归的判据——
下一批新增端点时没人能保证这条结论还成立。本判据把那次扫描固化：

对 app 实际挂载的全部 `/api/admin` 路由断言：
- 依赖树里含声明式鉴权（`get_current_admin` / `perm_checker`（require_perm 的内层）/
  `super_admin_checker`（require_super_admin 的内层）/ query-token 变体），或
- 命中 `IN_BODY_AUTH_ALLOWLIST`——**函数体内鉴权**的白名单（公开登录 + 媒体下行端点，
  每条必须写理由）；白名单是双向的：未登记的无鉴权路由判红，白名单条目指向的路由消失也判红
  （防止白名单烂尾）。

变异：摘掉任一 admin 端点的鉴权依赖 → 判红（复审核验过的手法）。
"""

from __future__ import annotations

from fastapi.routing import APIRoute

from backend.main import app

#: 声明式鉴权依赖的内层函数名（require_perm/require_super_admin 返回闭包，
#: FastAPI Depend 记录的是闭包 `__name__`；query-token 变体同口径）。
AUTH_DEP_NAMES = {
    "get_current_admin",  # backend/middleware/admin_auth.py
    "perm_checker",  # require_perm(...) 内层（admin_rbac.py）
    "super_admin_checker",  # require_super_admin(...) 内层
    "admin_query_checker",  # require_perm_or_query_token(...) 内层
    "admin_from_query_token",  # 双通道直接入口
}

#: 函数体内鉴权白名单（公开端点 + 媒体下行端点）。**每条必须写理由**；新增无鉴权端点
#: 要么补声明式依赖、要么进这张表并说明为什么体内鉴权（如 `<img>` 无法带头）。
IN_BODY_AUTH_ALLOWLIST: dict[tuple[str, str], str] = {
    ("POST", "/api/admin/login"): "公开登录端点（唯一入口；带 5 次/60s 限流与假哈希防时序探测）",
    (
        "GET",
        "/api/admin/books/{book_id}/cover-media",
    ): "封面媒体下行（体内 authorize_media：双通道+type+gen 撤销）",
    ("GET", "/api/admin/books/{book_id}/audio-media"): "音频媒体下行（同上）",
    (
        "GET",
        "/api/admin/uploads/{path:path}",
    ): "上传文件下发（require_perm_or_query_token + 五重路径校验）",
    (
        "GET",
        "/api/admin/members/orders/{order_id}/voucher-image",
    ): "收款凭证图（体内 assert_voucher_viewer：凭证鉴权）",
    (
        "GET",
        "/api/admin/activities/{activity_id}/detail-image",
    ): "活动详情图（体内 authorize_media）",
    ("GET", "/api/admin/activities/{activity_id}/cover-media"): "活动封面（体内 authorize_media）",
    ("GET", "/api/admin/circle/posts/{post_id}/image"): "阅读圈卡片图（体内 authorize_media）",
}


def _iter_routes(routes, prefix=""):
    for r in routes:
        if isinstance(r, APIRoute):
            yield prefix + r.path, r
        elif hasattr(r, "original_router"):
            # 本仓 FastAPI 把 include_router 保留为 _IncludedRouter（不摊平）：
            # 前缀在 include_context 上，真实路由在 original_router 里
            p = prefix + (getattr(r.include_context, "prefix", "") or "")
            yield from _iter_routes(r.original_router.routes, p)
        elif hasattr(r, "routes"):
            yield from _iter_routes(r.routes, prefix)


def _dep_names(dep) -> set[str]:
    names = {getattr(dep.call, "__name__", "")}
    for sub in dep.dependencies or []:
        names |= _dep_names(sub)
    return names


def _admin_routes() -> list[tuple[str, str, object]]:
    out = []
    for full, r in _iter_routes(app.routes):
        if full.startswith("/api/admin"):
            for method in sorted(r.methods):
                if method in ("HEAD", "OPTIONS"):
                    continue
                out.append((method, full, r))
    return out


def test_admin_routes_every_endpoint_has_auth_or_allowlisted():
    """全部 /api/admin 端点必须有声明式鉴权，或命中体内鉴权白名单（R-6）。"""
    routes = _admin_routes()
    # 防呆：装配方式变化导致扫不到路由时，判红而不是静默通过（0 覆盖 ≠ 全覆盖）
    assert len(routes) >= 100, f"admin 路由扫描到 {len(routes)} 条，装配/扫描疑似失效"

    offenders = []
    for method, path, r in routes:
        names: set[str] = set()
        for d in r.dependant.dependencies:
            names |= _dep_names(d)
        if names & AUTH_DEP_NAMES:
            continue
        if (method, path) in IN_BODY_AUTH_ALLOWLIST:
            continue
        offenders.append((method, path, sorted(n for n in names if n)))
    assert not offenders, (
        "以下 admin 端点既无声明式鉴权依赖、也不在体内鉴权白名单（新增端点请挂 "
        f"require_perm/get_current_admin，或说明理由进 IN_BODY_AUTH_ALLOWLIST）：{offenders}"
    )


def test_admin_auth_allowlist_stays_accurate():
    """白名单反向对账：每条白名单必须仍指向一个真实存在的无声明式鉴权路由（防烂尾）。"""
    routes = {(m, p) for m, p, _ in _admin_routes()}
    stale = []
    for key in IN_BODY_AUTH_ALLOWLIST:
        if key not in routes:
            stale.append(key)
    assert not stale, f"白名单条目指向的路由已不存在或已改为声明式鉴权，请删除条目：{stale}"
