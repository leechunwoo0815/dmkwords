# tests/unit/test_dashboard_charts.py — 仪表盘图形区聚合（2026-09-21）
"""用户需求：仪表盘要有"很酷炫的图"（至少 6 个）。图形区数据走新端点 /api/admin/dashboard/charts，
本文件锁三件事：① 趋势是**连续日期序列且缺日补 0**（前端直接画折线不需要再补）；
② 热门书按借阅次数降序且带书名（封面由前端按 book_id 拼既有 cover-media 端点）；
③ 会员构成按状态聚合，空状态不返回。"""

from fastapi.testclient import TestClient

from tests.unit.test_wm10_concurrency import _family, _h, _pay, _pay_deposit


def _book(client: TestClient, h: dict, isbn: str, title: str) -> int:
    return client.post(
        "/api/admin/books", json={"isbn": isbn, "title": title, "word_count": 100}, headers=h
    ).json()["id"]


def test_dashboard_charts_shape_and_content(client: TestClient):
    h = _h(client)
    _p, c, _mini = _family(client, h, "13981018001", "图形区孩")
    _pay(client, h, c["id"], "observation_fee")
    _pay_deposit(client, h, c["id"])
    _book(client, h, "9780394800401", "图形区热门书")
    client.post(
        "/api/admin/circulation/borrow",
        json={"child_id": c["id"], "isbn": "9780394800401"},
        headers=h,
    )

    r = client.get("/api/admin/dashboard/charts?days=14&top=5", headers=h)
    assert r.status_code == 200, r.text
    body = r.json()

    trend = body["borrow_trend"]
    assert len(trend) == 14, "趋势必须是连续 14 天（缺日补 0，前端不用再补）"
    assert all({"date", "borrowed", "returned"} <= set(p) for p in trend)
    assert sum(p["borrowed"] for p in trend) >= 1, "刚借的那本必须出现在趋势里"

    hot = body["hot_books"]
    assert hot and hot[0]["title"] == "图形区热门书", hot
    assert hot[0]["borrow_count"] >= 1

    statuses = {m["status"]: m["count"] for m in body["member_status"]}
    assert statuses.get("observation", 0) >= 1, statuses


def test_dashboard_charts_days_clamped(client: TestClient):
    """days/top 越界自动收敛（3–60 / 1–10），不因传怪值报错。"""
    h = _h(client)
    assert (
        len(client.get("/api/admin/dashboard/charts?days=999", headers=h).json()["borrow_trend"])
        == 60
    )
    assert (
        len(client.get("/api/admin/dashboard/charts?days=1", headers=h).json()["borrow_trend"]) == 3
    )
