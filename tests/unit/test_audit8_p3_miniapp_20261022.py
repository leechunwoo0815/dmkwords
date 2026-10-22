# tests/unit/test_audit8_p3_miniapp_20261022.py — 第八轮 P3 小程序族余 8 条（第一百一十六批）
"""**缺陷（第八轮审查 §3.4.2 小程序族挂账 8 条）**——逐条对应：

| # | 缺陷 | 判据（本文件） |
|---|---|---|
| 1 | 订单/活动/预约列表**静默截断**（后端响应头给了 `X-List-Truncated`，小程序 `request.js` 读不到头） | `test_js_request_layer_echos_headers_and_clears_img`（Node 桩件真跑 `withHeaders`/`listMeta`）+ 三个端点的 `X-List-*` 头 |
| 2 | 生词本整表回吐 + 整表 `setData` 无分页 | `test_vocabulary_list_is_paginated` / `test_vocabulary_summary_counts_all_pages` |
| 3 | 打卡只拉最近 90 条 → 翻月看不到更早记录 | `test_checkin_calendar_supports_date_range` / `..._today_and_streak_decoupled_from_range` |
| 4 | 预约页展示的状态徽标**不可达**（后端只回 active；docs/04 S35 要求显示"已核销"） | `test_reservation_list_mine_can_include_closed` / `test_reservation_list_mine_default_stays_active_only` |
| 5 | `<image>` 加载失败无兜底（全仓 `binderror` 出现 0 次） | `test_js_request_layer_echos_headers_and_clears_img` + 检查器 W5（`check_miniapp_wiring`，含注入自证） |
| 6 | 首页 onShow 请求扇出（`notifications` 与 tabBar 重复、续听两个请求串行） | **不另写用例**：纯小程序端结构，由 `check_miniapp_wiring` 的 W1（handler 必须存在）与人工核验 `docs/04` S226 兜底；如实登记"未做自动化行为断言" |
| 7 | 主包体积 | **无法判定项**（需真机/打包口径），见报告 §3.4.7，不写用例 |
| 8 | 真机未验项 | 见 `docs/04` §五十三（S220–S227），不写用例 |

**RED（还原修复后的失败形态，逐条实测）**：
- 预约：`include_closed` 分支删掉（仍 `status == ACTIVE`）→ 3 条里只剩 1 条 → 断言红；
- 打卡区间：`start_date/end_date` 过滤删掉 → 只回"最近 days 条" → 断言红；
- 打卡 streak 解耦：`today_row` 改回 `next((r for r in rows …))` → 翻历史月份时 `current_streak == 0` → 断言红；
- 生词本分页：`offset/limit` 删掉（回 `.all()`）→ 第 2 页返回 25 条 → 断言红；
- 统计五口径：`week_count/repeat_count` 删掉（回前端滤波）→ 键缺失 → 断言红；
- JS：`request.js` 里 `withHeaders` 分支删掉 → Node 桩件断言红（`undefined.headers`）。
"""

from __future__ import annotations

import json
import shutil
import subprocess
from datetime import date, datetime, timedelta
from pathlib import Path

import pytest
from fastapi import Response

from backend.database import SessionLocal

ROOT = Path(__file__).resolve().parents[2]


# ---------- 夹具 ----------


def _child(db, phone: str, name: str, member_status: str | None = None):
    """最小夹具。`member_status="observation"` = 有效会员（过 R-313 守卫，`VOCAB_VIEW` 要它）。"""
    from backend.domain.identity.models import Child, Parent

    p = Parent(name="小程序族家长", phone=phone)
    db.add(p)
    db.flush()
    c = Child(name=name, parent_id=p.id)
    if member_status:
        c.member_status = member_status
    db.add(c)
    db.commit()
    return p, c


_ISBN_SEQ = [7000]


def _book(db) -> int:
    from backend.domain.catalog.models import Book

    _ISBN_SEQ[0] += 1
    b = Book(
        title=f"小程序族书 {_ISBN_SEQ[0]}",
        isbn=f"978{_ISBN_SEQ[0]:010d}",
        word_count=1000,
        status=1,
    )
    db.add(b)
    db.commit()
    return b.id


# ---------- ①⑤ JS 工具层行为自检（Node 桩件真跑，不是源码字符串断言） ----------


@pytest.mark.skipif(shutil.which("node") is None, reason="环境无 node")
def test_js_request_layer_echos_headers_and_clears_img():
    """`request.js` 的 `withHeaders`/`listMeta` 与 `img.js` 的 `onImgError` 真跑一遍。

    为什么用 Node 桩件（而不是断言源码里有某个词）：这两处是**行为约定**——
    ① 响应头必须真能传到调用方（列表截断信号的唯一读取口，丢了就退回"看起来是全部"）；
    ② 兜底处理器必须只清 `data-img-path` 指的那个字段，没有该属性时不许 setData。
    语法/命名型检查器证明不了这两点（P2-64 同款教训：源码字符串断言不算行为断言）。
    """
    r = subprocess.run(
        ["node", "scripts/miniapp_selftest.js"],
        cwd=ROOT,
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert r.returncode == 0, f"Node 行为自检失败：\n{r.stdout}\n{r.stderr}"
    assert "PASS" in r.stdout


# ---------- ② 生词本分页 ----------


def _vocab(db, child_id: int, n: int, *, old_days: int = 0, repeat_from: int = 10**9):
    """造 n 个生词；`old_days` 之前的词算"非本周"；第 `repeat_from` 个之后查过多次。"""
    from backend.domain.reading.models import Vocabulary

    created = datetime.now() - timedelta(days=old_days)
    for i in range(n):
        db.add(
            Vocabulary(
                child_id=child_id,
                word=f"pageword{i:03d}",
                lookup_count=3 if i >= repeat_from else 1,
                created_at=created,
            )
        )
    db.commit()


def test_vocabulary_list_is_paginated():
    """第 1 页只回 PAGE_SIZE 条、第 2 页从 PAGE_SIZE 起（RED：去掉 offset/limit 回 `.all()` → 25 条）。"""
    from backend.domain.reading.vocabulary_service import VocabularyService

    db = SessionLocal()
    try:
        _, child = _child(db, "13900009101", "生词孩")
        _vocab(db, child.id, 25)
        svc = VocabularyService(db)
        page1 = svc.list_words(child, page=1)
        page2 = svc.list_words(child, page=2)
        assert len(page1) == svc.PAGE_SIZE == 20
        assert len(page2) == 5
        assert {w["id"] for w in page1} & {w["id"] for w in page2} == set()  # 不重不漏
    finally:
        db.close()


def test_vocabulary_summary_counts_all_pages():
    """五口径在**分页之外**统计（RED：删掉 week_count/repeat_count → KeyError）。

    这正是"列表分页后前端 reduce 必漂移"的守护：25 个词里 21 个是 7 天前建的、
    10 个查过多次——数字必须与服务端口径一致，与"当前页有几条"无关。
    """
    from backend.domain.reading.vocabulary_service import VocabularyService

    db = SessionLocal()
    try:
        _, child = _child(db, "13900009102", "生词孩2")
        _vocab(db, child.id, 25, old_days=30, repeat_from=15)
        stats = VocabularyService(db).summary(child)
        assert stats["word_count"] == 25
        assert stats["week_count"] == 0  # 全部是 30 天前的
        assert stats["repeat_count"] == 10  # 15..24 共 10 个 lookup_count=3
    finally:
        db.close()


def test_vocabulary_endpoint_stamps_page_size_header():
    """端点按 P2-44 把每页上限如实写进响应头（前端据此判"还有没有下一页"）。"""
    from backend.domain.reading.miniapp_router import vocabulary_list

    db = SessionLocal()
    try:
        parent, child = _child(db, "13900009103", "生词孩3", member_status="observation")
        _vocab(db, child.id, 25)
        resp = Response()
        rows = vocabulary_list(
            response=resp, child_id=child.id, page=1, page_size=20, auth=(parent, db)
        )
        assert len(rows) == 20
        assert resp.headers["X-List-Limit"] == "20"
        assert resp.headers["X-List-Truncated"] == "true"
        resp2 = Response()
        vocabulary_list(response=resp2, child_id=child.id, page=2, page_size=20, auth=(parent, db))
        assert resp2.headers["X-List-Truncated"] == "false"  # 只有 5 条 = 最后一页
    finally:
        db.close()


# ---------- ③ 打卡日历区间 ----------


def _checkins(db, child_id: int, days_ago: list[int]):
    from backend.domain.reading.models import CheckIn

    bid = _book(db)
    for d in days_ago:
        db.add(
            CheckIn(
                child_id=child_id,
                book_id=bid,
                checkin_date=date.today() - timedelta(days=d),
                streak=1,
            )
        )
    db.commit()


def test_checkin_calendar_supports_date_range():
    """区间模式只回该日期段（RED：去掉区间过滤 → 回"最近 days 条"，翻历史月份看不到任何记录）。

    场景对齐真机：孩子**连着 61 天**都打过卡，另外 200 天前也打过一天。日历页往前翻到
    200 天前那个月：旧实现是「最近 60 **条**」（行数窗口，不是日期窗口），那条 200 天前的
    记录**不在窗口内** → 整月空白（家长以为那月没坚持）。区间模式按所看月份取数，才拿得到。
    """
    from backend.domain.reading.service import ReadingService

    db = SessionLocal()
    try:
        _, child = _child(db, "13900009111", "打卡孩")
        old_day = date.today() - timedelta(days=200)
        _checkins(db, child.id, [200] + list(range(0, 61)))
        svc = ReadingService(db)
        # 区间 = 200 天前那个自然月
        first = old_day.replace(day=1)
        last = (first + timedelta(days=31)).replace(day=1) - timedelta(days=1)
        res = svc.checkin_calendar(child, 60, first, last)
        assert str(old_day) in res["dates"]
        assert len(res["dates"]) == 1
        # 默认模式是"最近 60 条"（行数窗口）→ 62 条里最早的 2 条（含 200 天前那条）看不到，
        # 这正是原缺陷；区间模式不受它影响。
        res_default = svc.checkin_calendar(child, 60)
        assert len(res_default["dates"]) == 60
        assert str(old_day) not in res_default["dates"]
    finally:
        db.close()


def test_checkin_calendar_today_and_streak_decoupled_from_range():
    """`today_checked`/`current_streak` 与取数区间**解耦**（RED：改回在 rows 里找今天 → 历史月份下 streak=0）。

    为什么必须解耦：翻到历史月份时区间里没有今天，若还在 `rows` 里找今天的行，
    连续天数会被算成 0——"连续 0 天"会把家长吓一跳（禁假 0 同族）。
    """
    from backend.domain.reading.models import CheckIn
    from backend.domain.reading.service import ReadingService

    db = SessionLocal()
    try:
        _, child = _child(db, "13900009112", "打卡孩2")
        bid = _book(db)
        db.add(
            CheckIn(
                child_id=child.id,
                book_id=bid,
                checkin_date=date.today(),
                streak=9,
            )
        )
        db.commit()
        svc = ReadingService(db)
        past = date.today() - timedelta(days=200)
        res = svc.checkin_calendar(child, 60, past.replace(day=1), past)
        assert res["dates"] == []  # 那个月确实没打卡
        assert res["today_checked"] is True  # 但"今天打没打"照旧真实
        assert res["current_streak"] == 9  # 连续天数不许被区间带成 0
    finally:
        db.close()


def test_checkin_endpoint_rejects_reversed_and_oversized_range():
    """区间入参校验：倒序 / 跨度超一年一律 422（与 `days` 的 1..365 同一纪律）。"""
    from backend.common.exceptions import ValidationError
    from backend.domain.reading.miniapp_router import checkin_calendar

    db = SessionLocal()
    try:
        parent, child = _child(db, "13900009113", "打卡孩3")
        today = date.today()
        with pytest.raises(ValidationError):
            checkin_calendar(
                child_id=child.id,
                days=30,
                start_date=today,
                end_date=today - timedelta(days=3),
                auth=(parent, db),
            )
        with pytest.raises(ValidationError):
            checkin_calendar(
                child_id=child.id,
                days=30,
                start_date=today - timedelta(days=500),
                end_date=today,
                auth=(parent, db),
            )
    finally:
        db.close()


# ---------- ④ 预约列表含历史 ----------


def _reservations(db, child_id: int):
    from backend.domain.reading.models import Reservation

    bid = _book(db)
    out = {}
    for status in ("active", "checked_out", "expired", "cancelled", "exception"):
        r = Reservation(
            child_id=child_id,
            book_id=bid,
            copy_id=1,
            expires_at=datetime.now() + timedelta(hours=72),
            status=status,
        )
        db.add(r)
        db.flush()
        out[status] = r.id
    db.commit()
    return out


def test_reservation_list_mine_can_include_closed():
    """`include_closed=True` 回全部状态（RED：删掉分支 → 只剩 active 1 条）。

    这是 `docs/04` **S35** 的验收点：「家长端我的预约**显示已核销**」——
    原实现只回 active，核销后预约直接从列表消失（家长以为记录丢了），
    而页面里 `checked_out/expired/cancelled` 三支徽标**永远不可达**。
    """
    from backend.domain.reading.service import ReservationService

    db = SessionLocal()
    try:
        _, child = _child(db, "13900009121", "预约孩")
        _reservations(db, child.id)
        svc = ReservationService(db)
        rows = svc.list_mine(child, include_closed=True)
        assert {r["status"] for r in rows} == {
            "active",
            "checked_out",
            "expired",
            "cancelled",
            "exception",
        }
        assert len(rows) == 5
    finally:
        db.close()


def test_reservation_list_mine_default_stays_active_only():
    """默认口径**不变**：书架 / 首页 / 详情页三个既有消费端零影响（回归守护）。"""
    from backend.domain.reading.service import ReservationService

    db = SessionLocal()
    try:
        _, child = _child(db, "13900009122", "预约孩2")
        _reservations(db, child.id)
        rows = ReservationService(db).list_mine(child)
        assert [r["status"] for r in rows] == ["active"]
    finally:
        db.close()


def test_reservation_endpoint_stamps_limit_when_history_requested():
    """含历史时按 P2-44 把 50 条上限写进响应头；默认（只回 active）**不**写头（无上限）。"""
    from backend.domain.reading.miniapp_router import list_reservations

    db = SessionLocal()
    try:
        parent, child = _child(db, "13900009123", "预约孩3")
        _reservations(db, child.id)
        resp = Response()
        rows = list_reservations(
            response=resp, child_id=child.id, include_closed=True, auth=(parent, db)
        )
        assert len(rows) == 5
        assert resp.headers["X-List-Limit"] == "50"
        assert resp.headers["X-List-Truncated"] == "false"
        resp2 = Response()
        rows2 = list_reservations(
            response=resp2, child_id=child.id, include_closed=False, auth=(parent, db)
        )
        assert len(rows2) == 1
        assert "X-List-Truncated" not in resp2.headers
    finally:
        db.close()


# ---------- ① 订单截断信号（后端侧；前端读取由 Node 自检覆盖） ----------


def test_orders_endpoint_still_stamps_truncation_header():
    """家长订单 50 条硬上限：响应头必须如实给（前端 `withHeaders` 读的就是这两个头）。"""
    from backend.domain.identity.miniapp_router import my_orders

    db = SessionLocal()
    try:
        parent, child = _child(db, "13900009131", "订单孩")
        resp = Response()
        rows = my_orders(response=resp, child_id=child.id, auth=(parent, db))
        assert rows == []
        assert resp.headers["X-List-Limit"] == "50"
        assert resp.headers["X-List-Truncated"] == "false"  # 0 条 < 50 → 未截断
    finally:
        db.close()


# ---------- 契约快照守护（端点新增查询参数的机械兜底） ----------


def test_openapi_snapshot_covers_new_query_params():
    """三个端点的新增查询参数必须进了 OpenAPI 快照（前端 `pnpm gen:api` 跟着走）。

    为什么单列一条：改端点参数后**只重导快照、忘了 `gen:api`** 可以潜伏两批不被发现
    （第一百零六批实测事故），本批新增了 4 个参数，值得钉一条。
    """
    snap = json.loads((ROOT / "docs" / "api" / "openapi.json").read_text(encoding="utf-8"))
    want = {
        "/api/miniapp/checkins": {"days", "start_date", "end_date"},
        "/api/miniapp/reservations": {"include_closed"},
        "/api/miniapp/vocabulary": {"page", "page_size"},
    }
    for path, params in want.items():
        actual = {p["name"] for p in snap["paths"][path]["get"].get("parameters", [])}
        assert params <= actual, f"{path} 缺参数 {params - actual}（快照未重导？）"
