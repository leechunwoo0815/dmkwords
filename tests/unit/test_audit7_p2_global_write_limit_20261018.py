# tests/unit/test_audit7_p2_global_write_limit_20261018.py — 第七轮 P2-6 全局写限流
"""红测试（中间件未挂载/口径写错时必须红）：

1. 默认预算：同一 token 连发 61 次写请求 → 前 60 次 422，**第 61 次 429**（含 `detail` + `Retry-After`）；
2. 身份分桶：token A 打满不影响 token B（未登录按 IP——`_client_ip` 口径）；
3. 路径类分桶：打满 A 路径不影响 B 路径；
4. **读方法不计数**：连发 120 次 GET 无 429；
5. **回调豁免**：连发 61 次 `POST /api/pay/wechat/notify` 无 429（微信会重试，限流 = 丢单）；
6. 更紧预算：导入类第 7 次 429（6/min）、上传类第 21 次 429（20/min）；
7. **路径归一化**：换资源 id 不换桶（否则换 id 就能无限刷）；
8. 配置置 `0` 关闸。

注意：本文件用"参数校验失败（422）"的请求造连发——**便宜且不落库**，但走的是完整
HTTP 栈（中间件在路由之前，422 同样计数）。
"""

from fastapi.testclient import TestClient

#: `RATE_LIMIT_WRITE_PER_MINUTE` 默认值（配置改默认值必须同步改这里）
DEFAULT_LIMIT = 60
#: 导入类更紧预算（代码内置）
IMPORT_LIMIT = 6
#: 上传类更紧预算（代码内置）
UPLOAD_LIMIT = 20

#: 故意非法的书目创建体：FastAPI 参数校验 422，不落库
_BAD_BOOK = {"isbn": "x"}


def _empty_xlsx() -> bytes:
    """表头行 + 零数据行的合法 xlsx：走真导入处理器、不建书（比空字节省一次 500）。"""
    import io

    from openpyxl import Workbook

    wb = Workbook()
    wb.active.append(["ISBN", "书名", "作者", "AR", "词数", "主题", "年级", "副本数"])
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


_XLSX = _empty_xlsx()


def _post(client: TestClient, path: str, headers: dict | None = None, **kw):
    return client.post(path, json=_BAD_BOOK, headers=headers or {}, **kw)


def _codes(client: TestClient, path: str, headers: dict, times: int) -> list[int]:
    return [_post(client, path, headers).status_code for _ in range(times)]


def test_write_burst_is_blocked_at_default_budget(client: TestClient, admin_headers: dict):
    """① 默认预算：前 60 次 422（走到路由校验），第 61 次 429（中间件拦在路由之前）。"""
    codes = _codes(client, "/api/admin/books", admin_headers, DEFAULT_LIMIT)
    assert set(codes) == {422}, f"前 {DEFAULT_LIMIT} 次应为参数校验 422，实得 {set(codes)}"

    r = _post(client, "/api/admin/books", admin_headers)
    assert r.status_code == 429, (
        f"第 {DEFAULT_LIMIT + 1} 次应 429，实 {r.status_code} {r.text[:120]}"
    )
    # 响应与 business_exception_handler 同形（两端前端按 detail 弹提示，零前端改动）
    assert "请求过于频繁" in r.json()["detail"], r.text[:200]
    assert r.headers.get("Retry-After") == "60", r.headers


def test_budget_is_bucketed_by_identity(
    client: TestClient, admin_headers: dict, staff_headers: dict
):
    """② 身份分桶：token A 打满后，token B 不受影响（否则一人被刷、全馆停摆）。"""
    assert _codes(client, "/api/admin/books", admin_headers, DEFAULT_LIMIT) == [422] * DEFAULT_LIMIT
    assert _post(client, "/api/admin/books", admin_headers).status_code == 429

    r = _post(client, "/api/admin/books", staff_headers)
    assert r.status_code == 422, f"另一身份的首次写请求不应被 429，实 {r.status_code}"


def test_budget_is_bucketed_by_path_class(client: TestClient, admin_headers: dict):
    """③ 路径类分桶：打满 A 路径不影响 B 路径。"""
    assert _codes(client, "/api/admin/books", admin_headers, DEFAULT_LIMIT) == [422] * DEFAULT_LIMIT
    assert _post(client, "/api/admin/books", admin_headers).status_code == 429

    r = _post(client, "/api/admin/circulation/borrow", admin_headers)
    assert r.status_code != 429, f"另一路径类不应被 429，实 {r.status_code}"


def test_read_requests_are_never_limited(client: TestClient, admin_headers: dict):
    """④ 读方法不计数：连发 120 次 GET（> 默认预算）无 429——媒体流/大屏轮询不受影响。"""
    codes = {client.get("/api/admin/books", headers=admin_headers).status_code for _ in range(120)}
    assert 429 not in codes, f"GET 不该被限流，实得 {codes}"


def test_wechat_notify_callback_is_exempt(client: TestClient):
    """⑤ 回调豁免：微信会重试，限流会丢单——连发 61 次（> 预算）必须无 429。"""
    codes = [
        client.post("/api/pay/wechat/notify", json={}).status_code for _ in range(DEFAULT_LIMIT + 1)
    ]
    assert 429 not in codes, f"回调端点不该被限流，实得 {set(codes)}"
    # 证明请求真的走到了处理器（而不是被别处挡掉）：响应稳定且是业务错误
    assert len(set(codes)) == 1, f"回调响应应稳定，实得 {set(codes)}"


def test_health_prefix_is_exempt(client: TestClient):
    """⑤b 健康检查前缀豁免（探针高频）。/health 只有 GET → 写方法应为 405，不是 429。"""
    codes = {client.post("/health").status_code for _ in range(DEFAULT_LIMIT + 1)}
    assert codes == {405}, f"/health 的写请求应 405，实得 {codes}"


def test_import_and_upload_paths_have_tighter_budgets(client: TestClient, admin_headers: dict):
    """⑥ 更紧预算：导入 6/min、上传 20/min（按"单次请求的资源开销"收紧）。"""
    import_codes = [
        client.post(
            "/api/admin/books/import",
            headers=admin_headers,
            files={"file": ("empty.xlsx", _XLSX, "application/vnd.ms-excel")},
        ).status_code
        for _ in range(IMPORT_LIMIT)
    ]
    assert 429 not in import_codes, f"前 {IMPORT_LIMIT} 次导入不该 429，实得 {import_codes}"
    r7 = client.post(
        "/api/admin/books/import",
        headers=admin_headers,
        files={"file": ("empty.xlsx", _XLSX, "application/vnd.ms-excel")},
    )
    assert r7.status_code == 429, f"第 {IMPORT_LIMIT + 1} 次导入应 429，实 {r7.status_code}"

    # 上传类（另一路径类，预算独立）：20 次不拦、第 21 次拦
    cover_codes = [
        client.post(
            f"/api/admin/books/{9000 + i}/cover",
            headers=admin_headers,
            files={"file": ("c.jpg", b"not-an-image", "image/jpeg")},
        ).status_code
        for i in range(UPLOAD_LIMIT)
    ]
    assert 429 not in cover_codes, f"前 {UPLOAD_LIMIT} 次上传不该 429，实得 {cover_codes}"
    r21 = client.post(
        "/api/admin/books/9099/cover",
        headers=admin_headers,
        files={"file": ("c.jpg", b"not-an-image", "image/jpeg")},
    )
    assert r21.status_code == 429, f"第 {UPLOAD_LIMIT + 1} 次上传应 429，实 {r21.status_code}"


def test_path_params_share_one_bucket(client: TestClient, admin_headers: dict):
    """⑦ 路径归一化：`/books/{id}/cover` 是一个桶——**换 id 换桶 = 限流形同虚设**。"""
    codes = [
        client.post(
            f"/api/admin/books/{9100 + i}/cover",
            headers=admin_headers,
            files={"file": ("c.jpg", b"not-an-image", "image/jpeg")},
        ).status_code
        for i in range(UPLOAD_LIMIT)
    ]
    assert 429 not in codes, f"前 {UPLOAD_LIMIT} 次（20 个不同 id）不该 429，实得 {codes}"

    r = client.post(
        "/api/admin/books/9199/cover",  # 又是一个新 id：仍应落在同一个桶
        headers=admin_headers,
        files={"file": ("c.jpg", b"not-an-image", "image/jpeg")},
    )
    assert r.status_code == 429, f"换 id 不该换桶，实 {r.status_code}"


def test_zero_config_disables_global_limit(client: TestClient, admin_headers: dict, monkeypatch):
    """⑧ 配置置 0 = 关闸（压测/排障用；生产禁）。"""
    from backend.config import get_settings

    monkeypatch.setattr(get_settings(), "RATE_LIMIT_WRITE_PER_MINUTE", 0)
    codes = _codes(client, "/api/admin/books", admin_headers, DEFAULT_LIMIT + 1)
    assert 429 not in codes, f"关闸后不该出现 429，实得 {set(codes)}"


def test_stale_buckets_are_swept_but_fresh_kept():
    """⑨ 惰性清扫：窗口外的陈旧桶回收（键数有界），**在用的桶不许误清**。"""
    import time

    from backend.middleware.rate_limit import RateLimiter

    limiter = RateLimiter()
    stale = time.time() - RateLimiter._STALE_AFTER - 10
    for i in range(3):
        limiter._requests[f"old:{i}"] = [stale]
    limiter._requests["fresh:1"] = [time.time()]

    for _ in range(RateLimiter._SWEEP_EVERY):
        limiter.check("fresh:1", 10**9, 60)

    assert list(limiter._requests) == ["fresh:1"], (
        f"陈旧桶应被回收、在用桶应保留，实得 {list(limiter._requests)}"
    )
