# tests/unit/test_audit7_p2_todo_wall_scan_20261018.py — 第七轮 P2-2：待办墙读取面收窄
"""造量 5000+ 行断言"**加载量有上界，且待处理一条不丢**"。

背景（第七轮 P2-2 的另一半）：子批 A 只加了索引，加载量没限——`list_inbox` 全表未删行读入 +
Python 排序切片；徽标 `todo_counts` 超管分支整表；`_scene_pending_count` 整场景。
修法用一条**已存在的精确判据**收窄候选集：`resolve_many` 的 T20c 覆盖层保证
**已销项（`handled_at` 非空）必非 pending** → pending 集合 ⊆ 未销项集合。

本文件的核心断言是**"喂给 resolver 的行数"**（= 真实加载量，即审查口径的"扫描行数"）——
用 spy 包 `resolve_many` 记录每次调用行数：任何一处退回"全量加载"都会让上界断言变红。

RED：
  · 去掉 `recent` 的 `.limit(TODO_SCAN_LIMIT)` → ① 行数爆表；
  · 去掉"未销项并集" → ① 老 pending 消失、② 未销项被窗口截断；
  · 去掉徽标/场景计数的 `handled_at IS NULL` → ③ 行数爆表。
"""

from __future__ import annotations

from datetime import datetime, timedelta

from fastapi.testclient import TestClient
from sqlalchemy import insert, text

from backend.domain.admin.todo_service import (
    TODO_OPEN_LIMIT,
    TODO_SCAN_LIMIT,
    AdminTodoService,
)
from tests.unit.test_p1_audit_20261015 import _db
from tests.unit.test_wm10_concurrency import _family

#: 造量：已审结历史（handled_at 非空）——窗口只取最近 TODO_SCAN_LIMIT 条
CLOSED = 5000
#: 未销项造量（②）：必须一条不落地可见，即使超过窗口
OPEN_MANY = 2100
#: ① 单次 resolve 调用行数上界 = 窗口 + 少量未销项
MAX_RESOLVE_ROWS = TODO_SCAN_LIMIT + 50
#: ③ 徽标路径上界：该用例里的未销项只有 2 条，已审结行一律不该进 resolver
MAX_RESOLVE_ROWS_COUNTS = 20


class _Spy:
    """记录 `resolve_many` 每次收到的行数（= 真实加载量）。"""

    def __init__(self, monkeypatch):
        self.sizes: list[int] = []
        orig = AdminTodoService.resolve_many
        spy = self  # 闭包里的 self 是 service 实例，不是 spy——显式取名避免踩错

        def _resolve(service, notifications):  # noqa: ANN001
            spy.sizes.append(len(notifications))
            return orig(service, notifications)

        monkeypatch.setattr(AdminTodoService, "resolve_many", _resolve)

    @property
    def max_rows(self) -> int:
        return max(self.sizes) if self.sizes else 0


def _closed_rows(n: int, *, scene: str, start_minutes_ago: int = 10) -> list[dict]:
    """已审结历史行（handled_at 非空 → 永不 pending）。"""
    now = datetime.now()
    return [
        {
            "scene": scene,
            "title": f"历史待办 {i}",
            "content": "closed",
            "ref_type": "child",
            "ref_id": str(100000 + i),
            "dedup_key": f"closed:{scene}:{i}",
            "is_deleted": 0,
            "created_at": now - timedelta(minutes=start_minutes_ago + i),
            "handled_at": now - timedelta(minutes=1),
        }
        for i in range(n)
    ]


def _open_rows(n: int, *, scene: str, start_minutes_ago: int = 1) -> list[dict]:
    """未销项行（handled_at 空）。scene 不在 NOTICE_SCENES → 进默认待办名单。"""
    now = datetime.now()
    return [
        {
            "scene": scene,
            "title": f"未销项 {i}",
            "content": "open",
            "ref_type": "child",
            "ref_id": str(200000 + i),
            "dedup_key": f"open:{scene}:{i}",
            "is_deleted": 0,
            "created_at": now - timedelta(minutes=start_minutes_ago + i),
            "handled_at": None,
        }
        for i in range(n)
    ]


def _insert(db, rows: list[dict]) -> None:
    from backend.common.admin_notification_models import AdminNotification

    db.execute(insert(AdminNotification), rows)
    db.commit()


def _inbox(client: TestClient, h: dict, **params) -> dict:
    query = {"page": 1, "page_size": 20, **params}
    r = client.get("/api/admin/admin-notifications", params=query, headers=h)
    assert r.status_code == 200, r.text
    return r.json()


def test_window_bounds_scan_and_keeps_old_pending(
    client: TestClient, admin_headers: dict, monkeypatch
):
    """① 5000 条已审结 + 1 条**很老的**未销项 pending：加载量有上界，老 pending 一条不丢。"""
    from backend.common.admin_notification_models import AdminNotification

    # 未入会孩子 → 会籍跟进（ref_type=child）显示态 = 待处理
    _p, child, _mini = _family(client, admin_headers, "13982000001", name="老待办孩")
    with _db() as db:
        _insert(db, _closed_rows(CLOSED, scene=AdminNotification.SCENE_MEMBER_FOLLOW_UP))
        db.execute(
            insert(AdminNotification),
            [
                {
                    "scene": AdminNotification.SCENE_MEMBER_FOLLOW_UP,
                    "title": "很久以前的入会跟进",
                    "content": "old open",
                    "ref_type": "child",
                    "ref_id": str(child["id"]),
                    "dedup_key": "old-open",
                    "is_deleted": 0,
                    # 比 5000 条历史都老 → 任何"只取最近 N 条"的窗口都会漏掉它
                    "created_at": datetime.now() - timedelta(days=365),
                    "handled_at": None,
                }
            ],
        )
        db.commit()

    spy = _Spy(monkeypatch)
    data = _inbox(client, admin_headers)

    assert spy.max_rows <= MAX_RESOLVE_ROWS, (
        f"加载量应 ≤ {MAX_RESOLVE_ROWS}（窗口 + 未销项），实得 {spy.max_rows}"
    )
    assert data["truncated"] is True, "有 5000 条历史、窗口 2000 → 必须如实置 truncated"
    assert data["total_all"] == CLOSED + 1, data["total_all"]

    pend = _inbox(client, admin_headers, status_filter="pending")
    assert pend["pending_count"] == 1, f"老 pending 丢了：{pend['pending_count']}"
    assert [i["title"] for i in pend["items"]] == ["很久以前的入会跟进"], pend["items"]
    assert pend["items"][0]["effective_status"] == "pending", pend["items"][0]


def test_open_rows_are_never_windowed(client: TestClient, admin_headers: dict, monkeypatch):
    """② 未销项 2100 条且**比 3000 条新历史都老**：窗口盖不到它们，并集必须一条不落。"""
    from backend.common.admin_notification_models import AdminNotification

    scene = AdminNotification.SCENE_MEMBER_FOLLOW_UP
    with _db() as db:
        _insert(db, _open_rows(OPEN_MANY, scene=scene, start_minutes_ago=60 * 24))  # 老
        _insert(db, _closed_rows(3000, scene=scene, start_minutes_ago=5))  # 新（占满窗口）

    spy = _Spy(monkeypatch)
    data = _inbox(client, admin_headers)

    assert data["total_all"] == OPEN_MANY + 3000, data["total_all"]
    assert spy.max_rows >= OPEN_MANY, (
        f"未销项必须全部进 resolver（应 ≥ {OPEN_MANY}），实得 {spy.max_rows}——被窗口截断了"
    )


def test_counts_path_skips_closed_rows(client: TestClient, admin_headers: dict, monkeypatch):
    """③ 徽标/场景计数：已审结行不进 resolver（**计数不变**，读取量随已销项下降）。"""
    from backend.common.admin_notification_models import AdminNotification
    from backend.domain.identity.models import Child

    _p, child, _mini = _family(client, admin_headers, "13982000002", name="待评估孩")
    with _db() as db:
        # 该孩子置为"待评估" → 场景 SCENE_PENDING_EVALUATION 的显示态 = 待处理
        db.query(Child).filter(Child.id == child["id"]).update(
            {"member_status": Child.MEMBER_PENDING_EVALUATION}
        )
        # 三个计数场景各造 2000 条**已审结**历史（handled_at 非空 → 必非 pending）
        for scene in (
            AdminNotification.SCENE_PENDING_EVALUATION,
            AdminNotification.SCENE_MEMBER_FOLLOW_UP,
            AdminNotification.SCENE_ACTIVITY_ENROLL_MANUAL,
        ):
            _insert(db, _closed_rows(2000, scene=scene))
        # 未销项只留 2 条：待评估（计 1）+ 报名（无此报名单 → 已审结，计 0）
        db.execute(
            insert(AdminNotification),
            [
                {
                    "scene": AdminNotification.SCENE_PENDING_EVALUATION,
                    "title": "待评估跟进",
                    "content": "open",
                    "ref_type": "child",
                    "ref_id": str(child["id"]),
                    "dedup_key": "open-pending-eval",
                    "is_deleted": 0,
                    "created_at": datetime.now() - timedelta(minutes=5),
                    "handled_at": None,
                },
                {
                    "scene": AdminNotification.SCENE_ACTIVITY_ENROLL_MANUAL,
                    "title": "报名待确认",
                    "content": "open",
                    "ref_type": "activity_enrollment",
                    "ref_id": "999999",
                    "dedup_key": "open-enroll",
                    "is_deleted": 0,
                    "created_at": datetime.now() - timedelta(minutes=6),
                    "handled_at": None,
                },
            ],
        )
        db.commit()

    spy = _Spy(monkeypatch)
    r = client.get("/api/admin/todo-counts", headers=admin_headers)
    assert r.status_code == 200, r.text
    counts = r.json()

    assert spy.max_rows <= MAX_RESOLVE_ROWS_COUNTS, (
        f"徽标路径应只喂未销项（≤ {MAX_RESOLVE_ROWS_COUNTS}），实得 {spy.max_rows}——退回全量加载了"
    )
    assert counts["pending_evaluation_followup"] == 1, counts["pending_evaluation_followup"]
    assert counts["activity_enroll_pending"] == 0, counts["activity_enroll_pending"]
    assert counts["member_follow_up"] == 0, counts["member_follow_up"]


def test_union_deduplicates(client: TestClient, admin_headers: dict):
    """⑤ 两条候选集**重叠时不许重复计数**：窗口内的未销项会被两条查询同时命中。"""
    from backend.common.admin_notification_models import AdminNotification

    _p, child, _mini = _family(client, admin_headers, "13982000003", name="重叠孩")
    with _db() as db:
        rows = _open_rows(3, scene=AdminNotification.SCENE_MEMBER_FOLLOW_UP)
        for r in rows:
            r["ref_id"] = str(child["id"])  # 都指向"未入会"孩子 → 3 条都是待处理
        _insert(db, rows)

    data = _inbox(client, admin_headers)
    assert data["pending_count"] == 3, f"重叠行被重复计数：{data['pending_count']}"
    ids = [i["id"] for i in data["items"]]
    assert len(ids) == len(set(ids)), f"列表出现重复行：{ids}"
    assert data["total"] == 3, data["total"]


def test_open_backlog_over_cap_is_flagged(client: TestClient, admin_headers: dict, monkeypatch):
    """⑥ 未销项积压超上限（8000 > `TODO_OPEN_LIMIT`）：**不静默丢**——`truncated` 置真、
    且加载量仍被硬上界卡住（窗口 2000 + 未销项上限 5000 = 7000，而不是 8000 全量）。"""
    from backend.common.admin_notification_models import AdminNotification

    over = 8000
    with _db() as db:
        _insert(db, _open_rows(over, scene=AdminNotification.SCENE_MEMBER_FOLLOW_UP))

    spy = _Spy(monkeypatch)
    data = _inbox(client, admin_headers)

    assert data["truncated"] is True, "未销项触顶必须显形"
    assert data["total_all"] == over, data["total_all"]
    assert spy.max_rows <= TODO_SCAN_LIMIT + TODO_OPEN_LIMIT, (
        f"加载量应被硬上界卡住（≤ {TODO_SCAN_LIMIT + TODO_OPEN_LIMIT}），实得 {spy.max_rows}"
    )


def test_window_query_plan_uses_index_without_filesort(client: TestClient):
    """④ 窗口查询与未销项查询都走 `ix_admin_notif_deleted_created`、无 filesort。

    造量取 2 万行而不是 5 千：**优化器按"LIMIT 占表比例"选计划**——5 千行 + LIMIT 2000 时
    它合理地选全表扫（2000/5000 = 40%），2 万行 + LIMIT 2000（10%）才走索引序。生产累积是
    万级往上，故按 2 万行断言（实测本机：4 种形态全部 `key=ix_admin_notif_deleted_created`、无 filesort）。
    """
    from backend.common.admin_notification_models import AdminNotification

    with _db() as db:
        _insert(db, _closed_rows(20000, scene=AdminNotification.SCENE_MEMBER_FOLLOW_UP))
        plans = {}
        for name, sql in (
            (
                "recent",
                "SELECT * FROM admin_notifications WHERE is_deleted=0"
                f" ORDER BY created_at DESC, id DESC LIMIT {TODO_SCAN_LIMIT}",
            ),
            (
                "open",
                "SELECT * FROM admin_notifications WHERE is_deleted=0 AND handled_at IS NULL"
                " ORDER BY created_at ASC, id ASC LIMIT 5000",
            ),
        ):
            row = db.execute(text("EXPLAIN " + sql)).mappings().fetchall()[0]
            plans[name] = {"type": row["type"], "key": row["key"], "extra": row["Extra"]}

    assert plans["recent"]["key"] == "ix_admin_notif_deleted_created", plans["recent"]
    assert "filesort" not in plans["recent"]["extra"].lower(), plans["recent"]
    assert plans["open"]["key"] == "ix_admin_notif_deleted_created", plans["open"]
    assert "filesort" not in plans["open"]["extra"].lower(), plans["open"]
