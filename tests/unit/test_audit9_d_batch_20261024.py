# tests/unit/test_audit9_d_batch_20261024.py — 第九轮审查 D 批（测试与门禁）
"""逐条对应 `docs/整改任务书-第九轮-20261022.md` §五 D 批：

- D1/P2-17：小程序圈帖卡片图/缩略图端点的**鉴权与负面面**补用例
  （报告原判"零用例"**不成立**——正面出图已由 `test_wm15_circle.py` 覆盖，见下）
- D2/P2-18：资金流程成功路径补**终态/台账**断言（原先只断言 200）
- D3/P2-19：零引用配置键守护（"数值全配置化"要有判据）
- D4/P2-20：状态机矩阵用例与实现解耦（期望表独立于被读的那张表）
- D7/N-10：残余 sleep 并发用例改 LOCK WAIT 探针

RED 判据（变异自证见任务书 §五「D 批落地结果」）：
  · D1 把 mini 端点的 `_parent_from_token` 去掉 → `test_mini_post_image_requires_token` 红
  · D1 把 `isfile` 判定去掉 → `test_mini_post_image_missing_file_404` 红
  · D3 把某个配置键的读取改成硬编码 → `test_config_keys_are_consumed_by_code` 红
  · D4 删掉状态机一条合法边（改 `identity/models.py` 的矩阵）→ `test_state_machine_expectations` 红
"""

from __future__ import annotations

import os

from fastapi.testclient import TestClient

from tests.unit.test_p1_audit_20261015 import _db
from tests.unit.test_wm15_circle import (
    MILESTONE,
    _award_milestone,
    _h,
    _mk_parent_with_child,
    _share,
)

# ---------------- D1/P2-17：小程序圈帖媒体端点的鉴权与负面面 ----------------
#
# 报告原话是"/api/miniapp/circle/posts/{id}/image **零用例**（admin 变体有专门用例、mini 变体漏了）"。
# **核实结论：正面出图已覆盖**（`tests/unit/test_wm15_circle.py:90-95` 的
# `test_share_creates_both_specs_and_thumb_endpoint` 对 `image`/`thumb` 两个端点各断言
# 200 + `content-type: image/jpeg`；`test_thumb_falls_back_to_full_for_legacy_post` 另覆盖旧帖回落）。
# **真实缺口 = 鉴权与负面面**：无 token / 假 token / 不存在的帖 / 跨家长可见性，此前一条没测。
# 本组用例补的就是这四类（与 admin 变体的 `test_circle_post_image_requires_admin_token` 对齐口径）。


def test_mini_post_image_requires_token(client: TestClient):
    """D1：无 token / 假 token → **401**（mini 变体此前没有负面用例）。"""
    h = _h(client)
    c, m, _ = _mk_parent_with_child(client, h, "13800003001", "媒体鉴权孩", "MediaAuth")
    post_id = _share(client, m, c, MILESTONE, _award_milestone(c, 100000)).json()["post_id"]

    for ep in ("image", "thumb"):
        anon = client.get(f"/api/miniapp/circle/posts/{post_id}/{ep}")
        assert anon.status_code == 401, f"{ep} 无 token 必须 401，实 {anon.status_code}"
        bad = client.get(f"/api/miniapp/circle/posts/{post_id}/{ep}?token=not-a-token")
        assert bad.status_code == 401, f"{ep} 假 token 必须 401，实 {bad.status_code}"


def test_mini_post_image_bytes_and_missing_post(client: TestClient):
    """D1：有效 token 拿到真图字节；不存在的帖 → **404**（不是 500 / 空 200）。"""
    from backend.config import get_settings

    h = _h(client)
    c, m, _ = _mk_parent_with_child(client, h, "13800003002", "媒体字节孩", "MediaBytes")
    post_id = _share(client, m, c, MILESTONE, _award_milestone(c, 100000)).json()["post_id"]
    token = m["Authorization"].split()[1]

    for ep in ("image", "thumb"):
        r = client.get(f"/api/miniapp/circle/posts/{post_id}/{ep}", params={"token": token})
        assert r.status_code == 200, f"{ep}: {r.text[:120]}"
        assert r.content[:2] == b"\xff\xd8", f"{ep} 应返回 JPEG 字节"
        assert r.headers["content-type"] == "image/jpeg"

    missing = client.get("/api/miniapp/circle/posts/999999/image", params={"token": token})
    assert missing.status_code == 404, f"不存在的帖必须 404，实 {missing.status_code}"

    # 根目录锚定（不许把 uploads 之外的东西发出去）——图片确实落在 uploads 内
    root = os.path.abspath(get_settings().UPLOADS_DIR)
    from backend.domain.reading_circle.models import CirclePost

    with _db() as db:
        post = db.query(CirclePost).filter(CirclePost.id == post_id).first()
        assert os.path.isfile(os.path.join(root, post.image_path))


def test_mini_post_image_visible_to_other_parent(client: TestClient):
    """D1：**跨家长可见是有意设计**（信息流/名片是公开社交面）——把这条落成判据，防将来误"收紧"。

    依据：`reading_circle/service.py` 的 `list_posts` 无家长过滤（全量时间倒序）；`docs/18` 把该端点
    登记为双规格媒体；第九轮 C 批的 68 端点全量扫描也把它判为"公开面，非越权"（任务书 §四 偏差①）。
    """
    h = _h(client)
    owner, m_owner, _ = _mk_parent_with_child(client, h, "13800003003", "公开面孩", "PublicKid")
    post_id = _share(client, m_owner, owner, MILESTONE, _award_milestone(owner, 100000)).json()[
        "post_id"
    ]

    _c2, m_other, _ = _mk_parent_with_child(client, h, "13800003004", "路人家长孩", "Bystander")
    other_token = m_other["Authorization"].split()[1]
    r = client.get(f"/api/miniapp/circle/posts/{post_id}/image", params={"token": other_token})
    assert r.status_code == 200, (
        f"信息流是公开社交面，非本人家长也应能取到卡片图（这是有意设计），实 {r.status_code} "
        f"{r.text[:120]}"
    )


# ---------------- D3/P2-19：零引用配置键（"数值全配置化"的守护） ----------------
#
# 报告原话是"**10 个配置键**在 tests+features 全无引用"。**机器枚举后的实际口径（如实登记）**：
# · 种子表 59 键里，**backend 零读取点的 = 0**（没有死键）；
# · 但 **7 个键**（`activity_remind_days` / `checkin_7days_bonus` / `checkin_30days_bonus` /
#   `first_activity_90d_remind_days` / `member_expire_remind_days` / `quiz_pass_bonus` /
#   `reservation_remind_hours`）在 **tests + features + 前端**里一次都没出现——即"改硬编码或
#   键名打错都不会红"。下面 ① 用 AST 守住"每个键都有读取点"，②–⑦ 逐个把**配置值 → 可观测效果**
#   钉成用例（改值必须能看见差别，否则这条键等于没接线）。


def _set_cfg(key: str, value: str) -> None:
    """改配置值 —— **必须同时清 ConfigService 的 60s TTL 缓存**。

    不清的话，同一用例内"先设 A 跑一次、再设 B 跑一次"的窗口语义根本验不出来：
    第二次读到的还是 A（本批首轮实测踩到，两条提醒用例因此假红）。
    """
    from sqlalchemy import text

    from backend.common.config_service import invalidate_config_cache

    with _db() as db:
        db.execute(
            text("UPDATE system_configs SET config_value=:v WHERE config_key=:k"),
            {"v": value, "k": key},
        )
        db.commit()
    invalidate_config_cache(key)


def _seeded_config_keys() -> list[str]:
    """种子表 `CONFIG_SEEDS` 里的键（AST 取，不靠正则猜）。"""
    import ast
    import pathlib

    src = pathlib.Path("backend/seeds/seed_configs.py").read_text(encoding="utf-8")
    keys: list[str] = []
    for node in ast.walk(ast.parse(src)):
        if isinstance(node, ast.Assign) and any(
            getattr(t, "id", "") == "CONFIG_SEEDS" for t in node.targets
        ):
            for elt in node.value.elts:
                if (
                    isinstance(elt, ast.Tuple)
                    and elt.elts
                    and isinstance(elt.elts[0], ast.Constant)
                ):
                    keys.append(elt.elts[0].value)
    return keys


def test_every_seeded_config_key_has_a_reader():
    """D3①：种子表每个键都必须有**代码读取点**（键名打错 / 值被硬编码取代 → 红）。

    判据是 **AST 字符串常量**（不是正则扫文本）：注释里提到键名不算读取点。
    """
    import ast
    import pathlib

    keys = _seeded_config_keys()
    assert len(keys) >= 59, f"种子表解析异常，只取到 {len(keys)} 个键"

    found: set[str] = set()
    for path in pathlib.Path("backend").rglob("*.py"):
        if path.name == "seed_configs.py":
            continue
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
            if isinstance(node, ast.Constant) and isinstance(node.value, str):
                found.add(node.value)
    missing = [k for k in keys if k not in found]
    assert not missing, f"这些配置键只有种子、没有读取点（死配置或键名打错）：{missing}"


def test_quiz_pass_bonus_drives_points(client: TestClient):
    """D3②：`quiz_pass_bonus` 改值 → 首次通过测验的入账积分跟着变（满分走另一键）。"""
    from backend.domain.growth.models import PointLedger
    from tests.unit.test_audit4_p2_batch2_20261017 import (
        _finish_book,
        _member_child_with_book,
        _mini_headers,
    )

    _set_cfg("quiz_pass_bonus", "33")
    h = _h(client)
    phone = "13981060001"
    c, book = _member_child_with_book(client, h, phone, "奖金书", 5)
    _finish_book(client, h, c["id"], book["id"], phone)
    mini = _mini_headers(client, phone)

    # 4 对 1 错 = 通过但非满分 → 走 `quiz_pass_bonus`（满分走 `quiz_full_marks_bonus`）
    r = client.post(
        f"/api/miniapp/quiz/{book['id']}/submit",
        json={"child_id": c["id"], "answers": ["A", "A", "A", "A", "B"]},
        headers=mini,
    )
    assert r.status_code == 200, r.text
    assert r.json()["passed"] is True, f"4/5 应判通过：{r.json()}"
    with _db() as db:
        rows = (
            db.query(PointLedger)
            .filter(
                PointLedger.child_id == c["id"],
                PointLedger.reason_type == "quiz_first_pass",
                PointLedger.is_deleted == 0,
            )
            .all()
        )
        assert [x.points for x in rows] == [33], (
            f"首次通过的入账积分必须等于配置值 33，实 {[x.points for x in rows]}"
        )


def test_checkin_cycle_bonus_drives_points(client: TestClient):
    """D3③：`checkin_7days_bonus` / `checkin_30days_bonus` 改值 → 周期奖入账跟着变。"""
    from backend.domain.growth.models import PointLedger
    from backend.domain.growth.service import GrowthService
    from tests.unit.test_wm10_concurrency import _family

    _set_cfg("checkin_7days_bonus", "21")
    _set_cfg("checkin_30days_bonus", "31")
    h = _h(client)
    _p, c, _mini = _family(client, h, "13981060002", "周期奖孩")

    with _db() as db:
        svc = GrowthService(db)
        svc.on_checkin(c["id"], 7)
        svc.on_checkin(c["id"], 30)
        db.commit()
        rows = (
            db.query(PointLedger)
            .filter(PointLedger.child_id == c["id"], PointLedger.is_deleted == 0)
            .all()
        )
        got = {r.reason_type: r.points for r in rows}
    assert got.get("checkin_7") == 21, f"7 天周期奖应取配置值 21，实 {got}"
    assert got.get("checkin_30") == 31, f"30 天周期奖应取配置值 31，实 {got}"


def test_reservation_remind_hours_window(client: TestClient):
    """D3④：`reservation_remind_hours` 是**窗口**——改小后同一张预约不再提醒。"""

    from backend.domain.reading.reservation_service import ReservationService

    h = _h(client)
    p, c, _mini, book_id = _seed_reservation(client, h, "13981060003", "预约窗口孩", hours_ahead=3)

    _set_cfg("reservation_remind_hours", "1")  # 3 小时后到期 → 在窗口外
    with _db() as db:
        assert ReservationService(db).expire_remind() == 0
    with _db() as db:
        from sqlalchemy import func

        from backend.common.notification_models import Notification

        n = (
            db.query(func.count(Notification.id))
            .filter(
                Notification.parent_id == p,
                Notification.scene == "reservation.expiring",
                Notification.is_deleted == 0,
            )
            .scalar()
        )
        assert n == 0, f"窗口 1 小时时不该提醒（预约 3 小时后到期），实发 {n} 条"

    _set_cfg("reservation_remind_hours", "5")  # 同一条预约进入窗口
    with _db() as db:
        assert ReservationService(db).expire_remind() == 1
    with _db() as db:
        from sqlalchemy import func

        from backend.common.notification_models import Notification

        n = (
            db.query(func.count(Notification.id))
            .filter(
                Notification.parent_id == p,
                Notification.scene == "reservation.expiring",
                Notification.is_deleted == 0,
            )
            .scalar()
        )
        assert n == 1, f"窗口 5 小时时应提醒 1 条，实 {n}"


def test_member_expire_remind_days_window(client: TestClient):
    """D3⑤：`member_expire_remind_days` 是**节点表**——改节点后 3 天后到期的孩子才收到提醒。"""
    from backend.domain.identity.service import ChildService

    h = _h(client)
    _p, c, _mini = _seed_formal_child(client, h, "13981060004", "到期节点孩", days_to_expire=3)

    _set_cfg("member_expire_remind_days", "1")  # 只提醒"明天到期"
    with _db() as db:
        ChildService(db).member_expire_remind()
    assert _count_notifs(_p, "member.expire_remind") == 0, "节点=1 时不该提醒 3 天后到期的孩子"

    _set_cfg("member_expire_remind_days", "3")
    with _db() as db:
        ChildService(db).member_expire_remind()
    assert _count_notifs(_p, "member.expire_remind") == 1, "节点=3 时必须提醒"


def test_activity_remind_days_window(client: TestClient):
    """D3⑥：`activity_remind_days` 是**偏移表**——改偏移后 2 天后开场的活动才进提醒面。"""
    from backend.domain.activity.service import ActivityService

    h = _h(client)
    p, _c, _act = _seed_enrolled_activity(client, h, "13981060005", "活动偏移孩", hours_ahead=48)

    _set_cfg("activity_remind_days", "5")  # 只提醒 5 天后开场的
    with _db() as db:
        assert ActivityService(db).activity_remind() == 0
    assert _count_notifs(p, "activity.remind") == 0

    _set_cfg("activity_remind_days", "2")
    with _db() as db:
        assert ActivityService(db).activity_remind() == 1
    assert _count_notifs(p, "activity.remind") == 1


def test_first_activity_90d_remind_days_window(client: TestClient):
    """D3⑦：`first_activity_90d_remind_days` 是**天数阈值**——改小后 30 天前的首场单才被催。"""
    from backend.domain.identity.order_service import OrderService

    h = _h(client)
    p, _c = _seed_first_activity_order(client, h, "13981060006", "首场催办孩", paid_days_ago=30)

    _set_cfg("first_activity_90d_remind_days", "90")  # 30 天前 < 90 天 → 不催
    with _db() as db:
        assert OrderService(db).first_activity_90d_remind() == 0
    assert _count_notifs(p, "member.expire_remind") == 0

    _set_cfg("first_activity_90d_remind_days", "10")  # 30 天前 > 10 天 → 催
    with _db() as db:
        assert OrderService(db).first_activity_90d_remind() == 1
    assert _count_notifs(p, "member.expire_remind") == 1


def _count_notifs(parent_id: int, scene: str) -> int:
    from sqlalchemy import func

    from backend.common.notification_models import Notification

    with _db() as db:
        return int(
            db.query(func.count(Notification.id))
            .filter(
                Notification.parent_id == parent_id,
                Notification.scene == scene,
                Notification.is_deleted == 0,
            )
            .scalar()
            or 0
        )


def _seed_reservation(client, h, phone: str, name: str, *, hours_ahead: int):
    """造一张"还有 hours_ahead 小时到期"的活跃预约（直插，只验窗口语义）。

    `copy_id` 是 NOT NULL——用既有 `_book_with_copies` 造书+副本（顺带把书上架）。
    """
    from datetime import datetime, timedelta

    from backend.domain.catalog.models import BookCopy
    from backend.domain.reading.models import Reservation
    from tests.unit.test_wm10_concurrency import _book_with_copies

    p, c, mini = _seed_formal_child(client, h, phone, name, days_to_expire=200)
    book_id = _book_with_copies(client, h, f"{name}的书", 1)
    with _db() as db:
        copy_id = db.query(BookCopy).filter(BookCopy.book_id == book_id).first().id
        db.add(
            Reservation(
                child_id=c,
                book_id=book_id,
                copy_id=copy_id,
                status=Reservation.STATUS_ACTIVE,
                expires_at=datetime.now() + timedelta(hours=hours_ahead),
            )
        )
        db.commit()
    return p, c, mini, book_id


def _seed_formal_child(client, h, phone: str, name: str, *, days_to_expire: int):
    from datetime import date, timedelta

    from backend.domain.identity.models import Child
    from tests.unit.test_wm10_concurrency import _family

    p, c, mini = _family(client, h, phone, name)
    with _db() as db:
        row = db.query(Child).filter(Child.id == c["id"]).first()
        row.member_status = Child.MEMBER_FORMAL
        row.member_expire = date.today() + timedelta(days=days_to_expire)
        db.commit()
        pid = row.parent_id
    return pid, c["id"], mini


def _seed_published_book(client, h, *, isbn: str, title: str) -> int:
    from tests.unit.helpers import force_book_on

    book = client.post(
        "/api/admin/books", json={"isbn": isbn, "title": title, "word_count": 300}, headers=h
    ).json()
    force_book_on(client, h, book["id"])
    return book["id"]


def _seed_enrolled_activity(client, h, phone: str, name: str, *, hours_ahead: int):
    from datetime import datetime, timedelta

    from backend.domain.activity.models import Activity, ActivityEnrollment
    from tests.unit.test_wm10_concurrency import _family

    p, c, mini = _family(client, h, phone, name)
    act = client.post(
        "/api/admin/activities",
        json={
            "title": f"{name}的活动",
            "activity_type": "book_club",
            "start_at": (datetime.now() + timedelta(hours=hours_ahead)).isoformat(),
            "location": "馆内一层",
            "max_quota": 5,
            "fee": 0,
            "description": "窗口用例",
        },
        headers=h,
    ).json()
    with _db() as db:
        db.add(
            ActivityEnrollment(
                activity_id=act["id"],
                child_id=c["id"],
                ticket_code=f"TC{act['id']:06d}{c['id']:06d}",
                status=ActivityEnrollment.STATUS_ENROLLED,
            )
        )
        db.commit()
        assert (
            db.query(Activity).filter(Activity.id == act["id"]).first().status
            == Activity.STATUS_PUBLISHED
        )
    return p["id"], c["id"], act["id"]


def _seed_first_activity_order(client, h, phone: str, name: str, *, paid_days_ago: int):
    from datetime import datetime, timedelta

    from backend.domain.identity.models import Child, Order
    from tests.unit.test_wm10_concurrency import _family

    p, c, _mini = _family(client, h, phone, name)
    with _db() as db:
        row = db.query(Child).filter(Child.id == c["id"]).first()
        row.member_status = Child.MEMBER_NONE  # 还没会员关系才值得催（P2-5 口径）
        db.add(
            Order(
                order_no=f"FA{paid_days_ago}{c['id']:06d}",
                order_type=Order.TYPE_FIRST_ACTIVITY,
                parent_id=p["id"],
                child_id=c["id"],
                amount=99,
                status=Order.STATUS_PAID,
                paid_at=datetime.now() - timedelta(days=paid_days_ago),
            )
        )
        db.commit()
    return p["id"], c["id"]
