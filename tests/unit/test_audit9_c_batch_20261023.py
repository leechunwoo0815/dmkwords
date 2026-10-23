# tests/unit/test_audit9_c_batch_20261023.py — 第九轮审查 C 批（P2 端点与前端 + 我方 P3 族）
"""逐条对应 `docs/整改任务书-第九轮-20261022.md` §三 C 批：

- C1/P2-7：转让申请**越权与不存在同回应**（原先"别人的孩子"422 ≠ "不存在"404 → 可枚举 child_id）
- C2/P2-8/9/10：三个"无界参数 → 500"改 422（往期活动 limit/offset · 押金补缴裸 dict · 书城 page）
- C4-①：借阅台逾期名单 **GET 不写库**（状态推进只由 `overdue_mark` 任务负责）
- C4-②：五处列表硬上限经 P2-44 响应头如实告知（points / passed-books / favorites / overdue / parents）
- C4-③：凭证图与活动封面缺 `isfile` 守卫（文件丢失 → 500 而非 404）
- C4-④：名片海报按**内容指纹**命名（同内容再取不重绘不写盘；内容变了出新图并清旧版）

变异自证（7 组全部打红后逐字节还原，记录见任务书 §四「落地结果」）：
  · M1 C1 把 `apply` 的归属分支拆回独立 `ValidationError` → `test_transfer_apply_*` 红
  · M2/M3 C2 去掉 `Query(ge=)` 或退回裸 `dict` → `test_unbounded_params_*` 红
  · M4 C4-① 把只读探针换回"写回库" → `test_overdue_list_is_read_only` 红
  · M5 C4-② 去掉 `stamp_list_meta` → `test_list_limit_headers_stamped` 红
  · M6 C4-③ 去掉 `isfile` → `test_missing_voucher_file_is_404` 红
  · M7 C4-④ 去掉指纹提前返回 → `test_poster_second_get_does_not_rerender` 红
"""

from __future__ import annotations

import glob
import os

from fastapi.testclient import TestClient

from backend.config import get_settings
from tests.unit.test_audit8_a1_overdue_notify_20261019 import _make_overdue
from tests.unit.test_p1_audit_20261015 import _db
from tests.unit.test_wm11_tasks import _borrow, _full_member, _h
from tests.unit.test_wm15_circle import MILESTONE, _award_milestone, _mk_parent_with_child, _share

# ---------------- C1/P2-7：越权与不存在同回应 ----------------


def test_transfer_apply_ownership_and_missing_indistinguishable(client: TestClient):
    """C1：**别人的孩子**与**不存在的孩子**必须同状态码 + 同文案（否则可枚举 child_id）。"""
    h = _h(client)
    own, m_own, _ = _mk_parent_with_child(client, h, "13800002901", "转让自有孩", "MineT")
    other, _m_other, _ = _mk_parent_with_child(client, h, "13800002902", "转让他家孩", "TheirsT")

    miss = client.post(
        "/api/miniapp/transfers",
        json={"source_child_id": 990001, "target_child_id": 990002},
        headers=m_own,
    )
    foreign = client.post(
        "/api/miniapp/transfers",
        json={"source_child_id": other, "target_child_id": own},
        headers=m_own,
    )
    mixed = client.post(
        "/api/miniapp/transfers",
        json={"source_child_id": other, "target_child_id": 990002},
        headers=m_own,
    )

    for name, r in (("不存在", miss), ("别人的孩子", foreign), ("混合", mixed)):
        assert r.status_code == 404, f"{name}：应 404，实 {r.status_code} {r.text[:80]}"
        assert r.json() == {"detail": "孩子不存在"}, f"{name}：文案泄漏了归属差异 {r.json()}"
    assert miss.json() == foreign.json() == mixed.json()


# ---------------- C2/P2-8/9/10：无界参数 → 422（＋ 同族 sweep） ----------------


def _is_request_validation(r) -> bool:
    """是否被 Pydantic 请求校验拦下（FastAPI 的 detail 是**列表**；本仓业务 422 是字符串）。"""
    return r.status_code == 422 and isinstance(r.json().get("detail"), list)


def test_unbounded_params_now_422(client: TestClient):
    """C2：四个"无界入参"原先直落 500（`limit=-1` → MySQL 报错；裸 dict 的 `int("abc")`）。"""
    h = _h(client)
    _c, m, _ = _mk_parent_with_child(client, h, "13800002903", "参数孩", "ParamKid")

    assert _is_request_validation(
        client.get("/api/miniapp/activities/past", params={"limit": -1}, headers=m)
    ), "往期活动 limit=-1 必须 422"
    assert _is_request_validation(
        client.get("/api/miniapp/activities/past", params={"offset": -1}, headers=m)
    ), "往期活动 offset=-1 必须 422"
    assert _is_request_validation(
        client.get("/api/miniapp/activities/past", params={"limit": 100000}, headers=m)
    ), "往期活动 limit 上限必须生效"

    r_supp = client.post(
        "/api/miniapp/deposits/supplement-orders", json={"child_id": "abc"}, headers=m
    )
    assert _is_request_validation(r_supp), f"补缴 child_id 非数字必须 422，实 {r_supp.status_code}"

    assert _is_request_validation(
        client.get("/api/miniapp/books", params={"page": -1}, headers=m)
    ), "书城 page=-1 必须 422"

    # 注：`/api/admin/dashboard/charts` 的 `days/top` **不属本族、本批未改**——服务层
    # `DashboardChartsService.charts` 首行就 clamp（days 3..60 / top 1..10），守护用例是
    # `test_dashboard_charts_days_clamped`（判据="越界自动收敛、不因传怪值报错"）。本批 sweep
    # 一度给它加了 `Query(ge/le)`，C 级全量门禁**实证冲突**（该用例红）→ 已撤销，见任务书 §四 偏差②。


def test_unbounded_params_valid_values_still_work(client: TestClient):
    """C2 反向守护：合法入参一个都不能被新边界误伤（含合法 int 的补缴请求体）。"""
    h = _h(client)
    c, m, _ = _mk_parent_with_child(client, h, "13800002904", "参数反例孩", "ParamOk")

    past = client.get("/api/miniapp/activities/past", params={"limit": 30}, headers=m)
    assert past.status_code == 200, past.text
    # P2-44：上限经响应头告知（本批顺带补的 C4-② 同源改动）
    assert past.headers.get("X-List-Limit") == "30"

    books = client.get("/api/miniapp/books", params={"page": 1}, headers=m)
    assert books.status_code == 200, books.text

    supp = client.post("/api/miniapp/deposits/supplement-orders", json={"child_id": c}, headers=m)
    assert not _is_request_validation(supp), f"合法 int child_id 不该被请求校验拦，实 {supp.text}"

    # 管理端图形区不在本族（服务层 clamp，见上一用例注释）：合法值照常 200
    charts = client.get("/api/admin/dashboard/charts", params={"days": 14, "top": 5}, headers=h)
    assert charts.status_code == 200, charts.text


# ---------------- C4-①：逾期名单 GET 不写库 ----------------


def test_overdue_list_is_read_only(client: TestClient):
    """C4-①：`GET /circulation/overdue` 只读——状态推进只由 `overdue_mark` 任务负责。"""
    from backend.domain.circulation.models import BorrowRecord
    from backend.domain.circulation.service import CirculationService

    h = _h(client)
    _p, c, book = _full_member(client, h, phone="13800002910")
    rec = _borrow(client, h, c["id"], book["isbn"])
    _make_overdue(rec["id"])

    resp = client.get("/api/admin/circulation/overdue", headers=h)
    assert resp.status_code == 200, resp.text
    assert any(o["record_id"] == rec["id"] for o in resp.json()), "超期在借必须出现在名单里"
    assert resp.headers.get("X-List-Limit") == str(CirculationService.OVERDUE_LIST_LIMIT)

    with _db() as db:
        assert db.get(BorrowRecord, rec["id"]).status == BorrowRecord.STATUS_ACTIVE, (
            "GET 不得写库：查询逾期名单把状态改成 OVERDUE 就是 GET 里的写入（第九轮 P3）"
        )


# ---------------- C4-②：列表上限如实告知（P2-44） ----------------


def test_list_limit_headers_stamped(client: TestClient):
    """C4-②：五处列表的上限必须经响应头告知（原先静默截断 / 完全无上限）。"""
    from backend.domain.circulation.service import CirculationService
    from backend.domain.growth.passed_books_service import PassedBooksService
    from backend.domain.growth.service import GrowthService
    from backend.domain.identity.service import ParentService
    from backend.domain.reading.vocabulary_service import FavoriteService

    h = _h(client)
    c, m, _ = _mk_parent_with_child(client, h, "13800002911", "上限孩", "LimitKid")

    cases = (
        ("/api/miniapp/points", m, c, str(GrowthService.POINTS_LIST_LIMIT)),
        ("/api/miniapp/growth/passed-books", m, c, str(PassedBooksService.LIST_LIMIT)),
        ("/api/miniapp/favorites", m, c, str(FavoriteService.LIST_LIMIT)),
        ("/api/miniapp/activities/past", m, None, "30"),
    )
    for path, headers, child_id, expected in cases:
        params = {"child_id": child_id} if child_id else {}
        r = client.get(path, params=params, headers=headers)
        assert r.status_code == 200, f"{path}: {r.text[:120]}"
        assert r.headers.get("X-List-Limit") == expected, f"{path}: {dict(r.headers)}"
        assert r.headers.get("X-List-Truncated") in ("true", "false"), f"{path} 缺截断信号"

    parents = client.get("/api/admin/members/parents", headers=h)
    assert parents.status_code == 200, parents.text
    assert parents.headers.get("X-List-Limit") == str(ParentService.SEARCH_LIMIT)

    overdue = client.get("/api/admin/circulation/overdue", headers=h)
    assert overdue.status_code == 200, overdue.text
    assert overdue.headers.get("X-List-Limit") == str(CirculationService.OVERDUE_LIST_LIMIT)


# ---------------- C4-③：缺 isfile 守卫（文件丢失 → 404 而非 500） ----------------


def test_missing_voucher_file_is_404(client: TestClient):
    """C4-③：库里登记了凭证路径但磁盘文件已丢 → 404（原先 FileResponse 阶段 RuntimeError → 500）。"""
    from backend.domain.identity.models import Order

    h = _h(client)
    parent = client.post(
        "/api/admin/members/parents", json={"name": "C4 家长", "phone": "13986002901"}, headers=h
    ).json()
    child = client.post(
        f"/api/admin/members/parents/{parent['id']}/children", json={"name": "C4 孩"}, headers=h
    ).json()
    order = client.post(
        "/api/admin/orders",
        json={"child_id": child["id"], "order_type": "observation_fee"},
        headers=h,
    ).json()
    with _db() as db:
        # 路径合法（在 uploads 内、可过 is_within）但**文件不存在**
        db.query(Order).filter(Order.id == order["id"]).update(
            {"voucher_path": f"voucher/c4_missing_{order['id']}.jpg"}
        )
        db.commit()
    token = h["Authorization"].split()[1]
    r = client.get(
        f"/api/admin/members/orders/{order['id']}/voucher-image", params={"token": token}
    )
    assert r.status_code == 404, f"凭证文件不在磁盘上必须 404，实 {r.status_code} {r.text[:80]}"


def test_missing_activity_cover_is_404(client: TestClient):
    """C4-③：活动封面路径在库里但文件已丢 → 404（同上）。"""
    from backend.domain.activity.models import Activity

    h = _h(client)
    act = client.post(
        "/api/admin/activities",
        json={
            "title": "C4 封面用例",
            # 远期哨兵（≥2050）：门禁 check_fake_assertions 判"时间炸弹"
            "start_at": "2050-01-01T10:00:00",
            "max_quota": 5,
            "fee": "0",
        },
        headers=h,
    ).json()
    with _db() as db:
        db.query(Activity).filter(Activity.id == act["id"]).update(
            {"cover_path": f"activity/c4_missing_{act['id']}.jpg"}
        )
        db.commit()
    r = client.get(f"/api/admin/activities/{act['id']}/cover-media", headers=h)
    assert r.status_code == 404, f"封面文件不在磁盘上必须 404，实 {r.status_code} {r.text[:80]}"


# ---------------- C4-④：海报内容指纹（同内容不重绘；内容变了出新图） ----------------


def _poster_files(child_id: int) -> list[str]:
    root = os.path.abspath(get_settings().UPLOADS_DIR)
    return sorted(glob.glob(os.path.join(root, "posters", f"poster_{child_id}_*.jpg")))


def test_poster_second_get_does_not_rerender(client: TestClient):
    """C4-④：同内容二次取图 → **不重绘、不覆盖写**（文件名带内容指纹，命中直接复用）。"""
    h = _h(client)
    c, m, _ = _mk_parent_with_child(client, h, "13800002920", "海报缓存孩", "CachePoster")
    token = m["Authorization"].split()[1]
    url = f"/api/miniapp/circle/children/{c}/poster"

    first = client.get(url, params={"token": token})
    assert first.status_code == 200, first.text
    files = _poster_files(c)
    assert len(files) == 1, f"该孩子应恰好 1 张海报，实 {files}"
    assert not os.path.isfile(
        os.path.join(os.path.abspath(get_settings().UPLOADS_DIR), "posters", f"poster_{c}.jpg")
    ), "旧的无指纹命名不该再产生（否则又是就地覆盖）"
    stat_before = os.stat(files[0])

    second = client.get(url, params={"token": token})
    assert second.status_code == 200 and second.content == first.content
    stat_after = os.stat(files[0])
    assert (stat_after.st_mtime_ns, stat_after.st_size) == (
        stat_before.st_mtime_ns,
        stat_before.st_size,
    ), "命中指纹还重绘/重写盘，就等于没缓存（第九轮 P3 未修）"


def test_poster_rerenders_and_prunes_when_content_changes(client: TestClient):
    """C4-④：内容变了（新勋章）→ 出新指纹文件，并清掉上一版（不堆积旧图）。"""
    h = _h(client)
    c, m, _ = _mk_parent_with_child(client, h, "13800002921", "海报换版孩", "PosterV2")
    token = m["Authorization"].split()[1]
    url = f"/api/miniapp/circle/children/{c}/poster"

    assert client.get(url, params={"token": token}).status_code == 200
    old = _poster_files(c)
    assert len(old) == 1, old

    _award_milestone(c, 100000)  # 勋章墙上多一枚 → 内容指纹必变
    assert client.get(url, params={"token": token}).status_code == 200

    new = _poster_files(c)
    assert len(new) == 1, f"旧版应被清掉、只剩新版（不堆积 700KB 旧图），实 {new}"
    assert new[0] != old[0], "内容变了必须出新指纹文件（否则家长永远拿到旧海报）"
    assert not os.path.isfile(old[0]), "旧版海报必须被清理"


def test_milestone_share_still_works_after_poster_change(client: TestClient):
    """C4-④ 邻居守卫：名片相关链路（晒卡）不受海报命名变化影响。"""
    h = _h(client)
    c, m, _ = _mk_parent_with_child(client, h, "13800002922", "晒卡回归孩", "PosterShare")
    r = _share(client, m, c, MILESTONE, _award_milestone(c, 100000))
    assert r.status_code == 200, r.text
