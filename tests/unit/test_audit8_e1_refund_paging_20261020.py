# tests/unit/test_audit8_e1_refund_paging_20261020.py — 第八轮大审查 E 批 P1-7
"""退款中心三个台账：**服务端分页 + 总数**（原先只给最近 200 条，更早的待审**不可见且无提示**）。

缺陷回顾（P1-7）：`ORDER BY id DESC LIMIT 200` 且无分页参数、无 total；前端在这 200 条内做客户端
分页 → 积压超 200 条后**最老的 pending 被挤出窗口**，而侧边栏徽标仍显示"待审 N"
（"徽标说有、页面看不到"，资金待办被藏）。

修法：三个列表加 `page`/`page_size`（单页仍锁 200 上限）+ 响应头 `X-Total-Count`
（当前筛选下的**全部**行数）；`X-List-Truncated` 在有 total 时按"本页之后还有没有行"**精确**判定。
徽标另走 `GET /api/admin/refund-center/counts`（分页后本页行数算不出全量——那是"假 0"）。

RED（变异自证）：
- 去掉 offset（忽略 page 参数）→ 用例 1 红（第 2 页与第 1 页同内容）；
- 不写 `X-Total-Count` → 用例 1/2 红；
- counts 端点改成"只数本页"或删掉 → 用例 3 红。
"""

from __future__ import annotations

from fastapi.testclient import TestClient

from tests.unit.test_wm13_writeback import _apply_refund, _family, _h, _pay


def _refunds(client: TestClient, h: dict, n: int, prefix: str = "13991") -> list[dict]:
    out = []
    for i in range(n):
        p, c, mini = _family(client, h, f"{prefix}{i:06d}", f"分页孩{i}")
        o = _pay(client, h, c["id"], "observation_fee")
        out.append(_apply_refund(client, mini, c, o))
    return out


def test_refund_list_serverside_paging_and_total(client: TestClient):
    """3 条退款：第 1 页 2 条 + `X-Total-Count=3` + truncated=true；第 2 页 1 条 + truncated=false。"""
    h = _h(client)
    _refunds(client, h, 3)

    r1 = client.get("/api/admin/refund-requests?page=1&page_size=2", headers=h)
    assert r1.status_code == 200, r1.text
    page1 = r1.json()
    assert len(page1) == 2, f"第 1 页应 2 条（实 {len(page1)}）"
    assert r1.headers.get("X-Total-Count") == "3", (
        f"必须给当前筛选下的总数（实 {r1.headers.get('X-Total-Count')!r}）——"
        "RED=无 total，前端算不出还有几页，只能假装'这就是全部'"
    )
    assert r1.headers.get("X-List-Truncated") == "true", "本页之后还有行 → 必须标 true"

    r2 = client.get("/api/admin/refund-requests?page=2&page_size=2", headers=h)
    assert r2.status_code == 200, r2.text
    page2 = r2.json()
    assert len(page2) == 1, f"第 2 页应 1 条（实 {len(page2)}）"
    assert r2.headers.get("X-Total-Count") == "3"
    assert r2.headers.get("X-List-Truncated") == "false", "已是最后一页 → 不得再标 true"
    ids1 = {r["id"] for r in page1}
    ids2 = {r["id"] for r in page2}
    assert not (ids1 & ids2), f"两页不得重复（RED=忽略 page 参数）: {ids1} / {ids2}"


def test_page_size_is_capped(client: TestClient):
    """单页大小锁 200（防一次拉爆）；非法页码/页大小直接 422。"""
    h = _h(client)
    assert client.get("/api/admin/refund-requests?page_size=9999", headers=h).status_code == 422
    assert client.get("/api/admin/refund-requests?page=0", headers=h).status_code == 422


def test_withdrawal_and_transfer_lists_have_total(client: TestClient):
    """退会/转让台账同款：给 total + 分页参数生效（同族三处不能只修一处）。"""
    h = _h(client)
    p, c, mini = _family(client, h, "13992000001", "退会分页孩")
    _pay(client, h, c["id"], "formal_fee")
    r = client.post(
        "/api/miniapp/withdrawals", json={"child_id": c["id"], "reason": "分页测试"}, headers=mini
    )
    assert r.status_code == 200, r.text

    rw = client.get("/api/admin/withdrawals?page=1&page_size=1", headers=h)
    assert rw.status_code == 200, rw.text
    assert rw.headers.get("X-Total-Count") == "1", rw.headers.get("X-Total-Count")

    rt = client.get("/api/admin/transfers?page=1&page_size=1", headers=h)
    assert rt.status_code == 200, rt.text
    assert rt.headers.get("X-Total-Count") == "0", "无转让单时总数应为 0（不是缺头）"


def test_counts_endpoint_matches_list_and_todo_counts(client: TestClient):
    """徽标计数必须来自后端，且与 `todo-counts` **同源**（同一口径两处实现 = 迟早漂移）。"""
    h = _h(client)
    _refunds(client, h, 2, prefix="13993")

    counts = client.get("/api/admin/refund-center/counts", headers=h).json()
    assert counts["refund"]["pending"] == 2, (
        f"退款待审计数应为 2（实 {counts['refund']}）——RED=分页后用本页行数算徽标（假 0）"
    )
    assert set(counts) == {"refund", "withdrawal", "transfer"}, counts

    todo = client.get("/api/admin/todo-counts", headers=h).json()
    assert todo["refund_pending"] == counts["refund"]["pending"], (
        f"侧边栏待办徽标与退款中心徽标必须同源：{todo['refund_pending']} != "
        f"{counts['refund']['pending']}"
    )

    # 列表页大小压到 1：徽标仍是全量 2（这就是"分页不能拿本页算计数"的直接证据）
    one = client.get("/api/admin/refund-requests?page=1&page_size=1", headers=h)
    assert len(one.json()) == 1 and one.headers.get("X-Total-Count") == "2"
    counts2 = client.get("/api/admin/refund-center/counts", headers=h).json()
    assert counts2["refund"]["pending"] == 2, "本页 1 条不代表待审 1 条"
