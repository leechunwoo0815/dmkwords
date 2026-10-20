# tests/unit/test_audit8_a8_enroll_unique_20261020.py — 第八轮审查 A 批：P2-9 活动报名活跃态唯一
"""**缺陷（第八轮审查 P2-9）**：索引 `uq_enroll_active_child` **名字像唯一索引、实际
`unique=False`**（`activity/models.py` 与建表迁移 `c9d3a6b8e2f1` 都写死了非唯一）。
PRD §9.2「同一活动同一孩子只能有一个有效报名」于是只剩应用层"活动行锁 + 先查后插"：
漏锁的写口（**管理端造活动单不在活动行锁内**）并发即可造出同一孩子两条活跃报名并可叠出超卖；
家长侧 `cancel` / `apply_refund` 走普通读，"先读状态再改"并发下后写覆盖前写
（典型：**已签到者仍能拿到全额退款**）。

**修复**（第九十三批，2026-10-20）：
1. **库级条件唯一**：生成列 `active_uniq_key`（活跃四态 → `活动id-孩子id`，其余 NULL）+
   同名**真唯一**索引——MySQL 无部分索引，用生成列做条件唯一；取消/退款/软删后可**重新报名**；
2. **管理端造活动单**纳入活动行锁（名额闸 + 重复闸都在锁内）；
3. 家长侧 `_my_enrollment` 与整场取消的报名列表改**锁定读**；
4. 两个插入点接 `add_with_unique_fallback`（纵深防御：漏锁时给 409/422 而非 500）。

**判据（并发三条可判定，不用 `sleep` 猜时序）**：让先到者持行锁不提交，再发起第二个请求，
轮询 `information_schema.innodb_trx` 直到 `trx_state='LOCK WAIT'`，并断言**被阻塞的语句以
`SELECT` 开头**——修复前普通读不阻塞，只有 `UPDATE` 会等锁，前缀断言即红（红线 171 族）。
"""

import re
import threading
import time

from fastapi.testclient import TestClient
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError

from backend.database import get_session
from backend.domain.activity.models import ACTIVE_UNIQ_KEY_SQL, ActivityEnrollment
from backend.domain.identity.models import RefundRequest
from tests.unit.test_audit4_p2_batch3_20261017 import _activity, _enrolled_paid, _member_child
from tests.unit.test_wm11_tasks import _h

DUP_ENROLL_MSG = "该孩子已报名此活动（不能重复报名）"
ADMIN_DUP_MSG = "该孩子已有此活动的有效报名（待收款/已报名/退款待审），不可重复创建费用订单"


def _lock_wait_query(timeout: float = 8.0, interval: float = 0.25) -> str:
    """等 LOCK WAIT 出现，返回**被阻塞的那条语句**（空串 = 超时未见）。

    为什么返回语句文本而不是布尔（A 批 A7 踩坑，如实记）：只判"有没有 LOCK WAIT"**不判别**
    ——修复前写的 `UPDATE` 同样会等行锁，照样命中。真正判别的是**阻塞发生在读还是写**。
    轮询间隔 0.25s：0.05s 的紧轮询会把被测线程挤住，导致根本看不到 LOCK WAIT（实测假红）。
    """
    deadline = time.time() + timeout
    while time.time() < deadline:
        with get_session() as db:
            row = db.execute(
                text(
                    "SELECT trx_query FROM information_schema.innodb_trx "
                    "WHERE trx_state = 'LOCK WAIT' LIMIT 1"
                )
            ).scalar()
        if row:
            return str(row)
        time.sleep(interval)
    return ""


def _resp(r) -> dict:
    body = r.json() if r.headers.get("content-type", "").startswith("application/json") else r.text
    return {"code": r.status_code, "body": body}


def _free_enroll(client: TestClient, h, phone: str, title: str) -> tuple[dict, dict, dict]:
    """建孩子 + 免费活动 + 报名成功，返回 (child, mini, activity)。"""
    c, mini = _member_child(client, h, phone)
    act = _activity(client, h, hours_later=72, fee=0, title=title)
    r = client.post(
        f"/api/miniapp/activities/{act['id']}/enroll",
        json={"child_id": c["id"]},
        headers=mini,
    )
    assert r.status_code == 200, r.text
    return c, mini, act


# ---------------------------------------------------------------- ① schema 真实性


def test_unique_index_is_real_and_covers_exactly_active_statuses(client: TestClient):
    """索引必须**真的是唯一索引**，且覆盖的状态集合 == `ACTIVE_STATUSES`。

    RED（还原修复：改回 `unique=False` 的复合索引）：第一条断言即红。
    第二条是**漂移闸**：DDL 里写死的状态字面量必须与模型常量逐字一致——将来加状态
    （如 `waitlist`）只改一处会红，不会静默放过。
    """
    with get_session() as db:
        rows = db.execute(
            text(
                "SELECT COLUMN_NAME, NON_UNIQUE FROM information_schema.STATISTICS "
                "WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = 'activity_enrollments' "
                "AND INDEX_NAME = 'uq_enroll_active_child'"
            )
        ).fetchall()
        assert rows, "uq_enroll_active_child 索引不存在"
        assert len(rows) == 1 and rows[0][0] == "active_uniq_key", (
            f"唯一约束必须落在生成列 active_uniq_key 上，实际 {rows}"
        )
        assert rows[0][1] == 0, "uq_enroll_active_child 必须是**真唯一索引**（NON_UNIQUE=0）"

        expr = db.execute(
            text(
                "SELECT GENERATION_EXPRESSION FROM information_schema.COLUMNS "
                "WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = 'activity_enrollments' "
                "AND COLUMN_NAME = 'active_uniq_key'"
            )
        ).scalar()
        assert expr, "生成列 active_uniq_key 不存在"

    # MySQL 回显时把字面量写成 `_utf8mb4\'pending_payment\'`（反斜杠转义引号），先去掉反斜杠
    ddl_statuses = set(re.findall(r"'([a-z_]+)'", str(expr).replace("\\", "")))
    assert ddl_statuses == set(ActivityEnrollment.ACTIVE_STATUSES), (
        f"生成列里的状态集合 {sorted(ddl_statuses)} 与 ACTIVE_STATUSES "
        f"{sorted(ActivityEnrollment.ACTIVE_STATUSES)} 不一致（改一处忘另一处）"
    )

    def _norm(sql: str) -> str:
        """归一：MySQL 回显会加反引号、`_utf8mb4` 前缀、`\\'` 转义，并**给每个操作数补一层括号**
        （`(is_deleted = 0) and (status in (...))`）——括号只是回显噪声，去掉后两侧才可比。"""
        s = str(sql).lower().replace("`", "").replace("_utf8mb4", "").replace("\\", "")
        return re.sub(r"[\s()]+", "", s)

    assert _norm(ACTIVE_UNIQ_KEY_SQL) == _norm(expr), (
        f"模型里的 ACTIVE_UNIQ_KEY_SQL 与库内实际表达式不一致：\n"
        f"  模型 {_norm(ACTIVE_UNIQ_KEY_SQL)}\n  库内 {_norm(expr)}"
    )


def test_db_rejects_second_active_enrollment(client: TestClient):
    """**绕过应用层**直插第二条活跃报名 → 库级唯一约束必须拦下（1062）。

    RED（还原修复：索引非唯一）：直插成功 → 断言失败。
    """
    h = _h(client)
    c, _mini, act = _free_enroll(client, h, "13800003701", "唯一约束探针A")
    with get_session() as db:
        dup = ActivityEnrollment(
            activity_id=act["id"],
            child_id=c["id"],
            order_id=None,
            ticket_code="TKPROBE_P29A",
            status=ActivityEnrollment.STATUS_ENROLLED,
        )
        db.add(dup)
        try:
            db.flush()
        except IntegrityError as e:
            assert "uq_enroll_active_child" in str(e.orig), f"应撞唯一索引：{e.orig}"
            db.rollback()
        else:
            db.rollback()
            raise AssertionError("第二条活跃报名竟然插进去了——唯一约束没生效")


# ---------------------------------------------------------------- ② 对照组：不许修过头


def test_cancelled_then_reenroll_allowed(client: TestClient):
    """对照组（**防止修过头**）：取消后必须能重新报名，且拿到**新券码**、旧券行保留为 cancelled。

    RED（把唯一性写成"同活动同孩子永久唯一"）：重新报名撞唯一约束 → 500。
    """
    h = _h(client)
    c, mini, act = _free_enroll(client, h, "13800003702", "唯一约束探针B")
    with get_session() as db:
        first = (
            db.query(ActivityEnrollment)
            .filter(
                ActivityEnrollment.activity_id == act["id"],
                ActivityEnrollment.child_id == c["id"],
            )
            .one()
        )
        first_id, first_ticket = first.id, first.ticket_code

    r = client.post(
        f"/api/miniapp/enrollments/{first_id}/cancel",
        json={"child_id": c["id"]},
        headers=mini,
    )
    assert r.status_code == 200, r.text

    again = client.post(
        f"/api/miniapp/activities/{act['id']}/enroll",
        json={"child_id": c["id"]},
        headers=mini,
    )
    assert again.status_code == 200, f"取消后必须能重新报名：{again.status_code} {again.text}"
    assert again.json()["enrollment"]["ticket_code"] != first_ticket, "重新报名必须发新券码"

    with get_session() as db:
        rows = (
            db.query(ActivityEnrollment)
            .filter(
                ActivityEnrollment.activity_id == act["id"],
                ActivityEnrollment.child_id == c["id"],
            )
            .order_by(ActivityEnrollment.id)
            .all()
        )
        assert [x.status for x in rows] == ["cancelled", "enrolled"], (
            f"旧行应留 cancelled、新行 enrolled，实际 {[x.status for x in rows]}"
        )
        # 生成列取值精确（不是"非空即可"）：活跃态 = `活动id-孩子id`，非活跃 = NULL
        assert rows[0].active_uniq_key is None, f"cancelled 行不得占位：{rows[0].active_uniq_key!r}"
        assert rows[1].active_uniq_key == f"{act['id']}-{c['id']}", (
            f"活跃行键应为 `活动id-孩子id`，实际 {rows[1].active_uniq_key!r}"
        )


# ---------------------------------------------------------------- ③ 并发：管理端造单入活动行锁


def test_admin_activity_order_waits_for_activity_lock(client: TestClient, session_pair):
    """管理端造活动单必须**阻塞在活动行锁**上，并在拿到锁后读到最新状态。

    场景：先到者（s2）锁住活动行并置 cancelled 未提交 → 管理端造单必须等锁
    （被阻塞语句以 `SELECT` 开头）→ 先到者提交后，造单读到 cancelled → 422。

    RED（还原修复：`OrderService.create` 的活动读不加锁）：造单**立刻 200** 建出订单
    （超卖/重复报名的入口），LOCK WAIT 断言与 422 断言同时红。
    """
    h = _h(client)
    c, _mini = _member_child(client, h, "13800003703")
    act = _activity(client, h, hours_later=72, title="造单加锁探针")

    _s1, s2 = session_pair
    from backend.domain.activity.models import Activity

    locked = (
        s2.query(Activity)
        .filter(Activity.id == act["id"])
        .with_for_update()
        .populate_existing()
        .first()
    )
    assert locked is not None
    locked.status = Activity.STATUS_CANCELLED  # 模拟"整场取消进行中"（未提交）

    result: dict = {}

    def _create_order():
        result.update(
            _resp(
                client.post(
                    "/api/admin/orders",
                    json={
                        "child_id": c["id"],
                        "order_type": "activity_fee",
                        "activity_id": act["id"],
                    },
                    headers=h,
                )
            )
        )

    t = threading.Thread(target=_create_order)
    t.start()
    try:
        blocked_stmt = _lock_wait_query()
        assert blocked_stmt, "管理端造单没有等在任何锁上（说明活动读没加锁）"
        assert blocked_stmt.lstrip().upper().startswith("SELECT"), (
            f"被阻塞的必须是**锁定读**（活动行的 SELECT ... FOR UPDATE）；实际是 "
            f"{blocked_stmt[:60]!r}——说明读没加锁，闸门在锁外"
        )
    finally:
        s2.commit()
    t.join(timeout=10)
    assert not t.is_alive(), "造单线程 10s 未结束（疑似死锁）"

    assert result["code"] == 422, f"活动已取消后不得造单：{result}"
    assert result["body"]["detail"] == "活动已取消或结束，不能创建活动订单"
    with get_session() as db:
        n = db.execute(
            text(
                "SELECT COUNT(*) FROM activity_enrollments WHERE activity_id = :a AND child_id = :c"
            ),
            {"a": act["id"], "c": c["id"]},
        ).scalar()
        assert n == 0, "被拒的造单不得留下报名行（订单与报名同事务）"


# ---------------------------------------------------------------- ④ 并发：家长侧锁定读


def test_parent_refund_waits_for_enrollment_lock_and_rejects_checked_in(
    client: TestClient, session_pair
):
    """家长申请退款必须**阻塞在报名行锁**上；先到者提交"已签到"后必须被拒（422）且不建退款单。

    这正是审查原文的"已签到者全额退款"：修复前家长侧普通读，读到的是 `enrolled` 旧快照
    → 200 建出退款单；修复后锁定读拿到 `checked_in` → 422。

    RED（还原修复：`_my_enrollment` 不加锁）：无 LOCK WAIT（断言红）+ 退款单被建出（断言红）。
    """
    h = _h(client)
    c, mini = _member_child(client, h, "13800003704")
    act = _activity(client, h, hours_later=72, title="签到退款探针")
    e = _enrolled_paid(client, h, mini, c["id"], act["id"])
    eid = e["enrollment"]["id"]

    _s1, s2 = session_pair
    locked = (
        s2.query(ActivityEnrollment)
        .filter(ActivityEnrollment.id == eid)
        .with_for_update()
        .populate_existing()
        .first()
    )
    assert locked is not None
    locked.status = ActivityEnrollment.STATUS_CHECKED_IN  # 模拟"馆员正在扫券签到"（未提交）

    result: dict = {}

    def _apply_refund():
        result.update(
            _resp(
                client.post(
                    f"/api/miniapp/enrollments/{eid}/refund-apply",
                    json={"child_id": c["id"]},
                    headers=mini,
                )
            )
        )

    t = threading.Thread(target=_apply_refund)
    t.start()
    try:
        blocked_stmt = _lock_wait_query()
        assert blocked_stmt, "退款申请没有等在任何锁上（说明家长侧读没加锁）"
        assert blocked_stmt.lstrip().upper().startswith("SELECT"), (
            f"被阻塞的必须是**锁定读**（报名行的 SELECT ... FOR UPDATE）；实际是 "
            f"{blocked_stmt[:60]!r}——说明读没加锁，状态判据读的是旧快照"
        )
    finally:
        s2.commit()
    t.join(timeout=10)
    assert not t.is_alive(), "退款线程 10s 未结束（疑似死锁）"

    assert result["code"] == 422, f"已签到不得退款：{result}"
    assert result["body"]["detail"] == "已签到，不能退款（人都来了，成本已发生）"
    with get_session() as db:
        n = (
            db.query(RefundRequest.id)
            .filter(
                RefundRequest.order_id == e["order_id"],
                RefundRequest.status == RefundRequest.STATUS_PENDING,
            )
            .count()
        )
        assert n == 0, "被拒的退款申请不得留下退款单（否则审核员一批准钱就出去了）"
        assert db.get(ActivityEnrollment, eid).status == ActivityEnrollment.STATUS_CHECKED_IN


# ---------------------------------------------------------------- ⑤ 并发：整场取消锁报名行


def test_cancel_activity_waits_for_enrollment_lock(client: TestClient, session_pair):
    """整场取消必须**阻塞在报名行锁**上（不能拿旧快照覆盖家长刚做的状态变更）。

    RED（还原修复：报名列表裸读）：不出现 LOCK WAIT 的 `SELECT`（被阻塞的是后面 commit 的
    `UPDATE`，前缀断言即红）。
    """
    h = _h(client)
    c, _mini, act = _free_enroll(client, h, "13800003705", "整场取消加锁探针")
    with get_session() as db:
        eid = (
            db.query(ActivityEnrollment.id)
            .filter(
                ActivityEnrollment.activity_id == act["id"],
                ActivityEnrollment.child_id == c["id"],
            )
            .scalar()
        )

    _s1, s2 = session_pair
    locked = (
        s2.query(ActivityEnrollment)
        .filter(ActivityEnrollment.id == eid)
        .with_for_update()
        .populate_existing()
        .first()
    )
    assert locked is not None
    locked.cancel_reason = "家长正在操作（未提交）"  # 模拟家长侧持锁事务进行中

    result: dict = {}

    def _cancel_activity():
        result.update(_resp(client.post(f"/api/admin/activities/{act['id']}/cancel", headers=h)))

    t = threading.Thread(target=_cancel_activity)
    t.start()
    try:
        blocked_stmt = _lock_wait_query()
        assert blocked_stmt, "整场取消没有等在任何锁上（说明报名列表没加锁）"
        assert blocked_stmt.lstrip().upper().startswith("SELECT"), (
            f"被阻塞的必须是**锁定读**（报名行的 SELECT ... FOR UPDATE）；实际是 "
            f"{blocked_stmt[:60]!r}——说明列表是裸读，家长的状态变更会被旧快照覆盖"
        )
    finally:
        s2.commit()
    t.join(timeout=10)
    assert not t.is_alive(), "取消线程 10s 未结束（疑似死锁）"

    assert result["code"] == 200, f"取消应成功：{result}"
    assert result["body"]["cancelled"] == 1, result
    with get_session() as db:
        assert db.get(ActivityEnrollment, eid).status == ActivityEnrollment.STATUS_CANCELLED, (
            "取消后报名应落 cancelled"
        )


# ---------------------------------------------------------------- ⑥ 唯一冲突兜底（纵深防御）


def test_unique_fallback_returns_false_without_polluting_session(client: TestClient):
    """`add_with_unique_fallback` 是两处插入点的纵深防御：**冲突返回 False 且不污染事务**。

    诚实披露：主防线是活动行锁（③④⑤ 三条并发用例证明），本条只证明"漏锁时不会冒 500"。
    注意必须验证**非冲突插入真的落库**——若保存点里没 flush，这个兜底会退化成"永远 True"。
    """
    from backend.common.sql_utils import add_with_unique_fallback

    h = _h(client)
    c, _mini, act = _free_enroll(client, h, "13800003706", "兜底探针")

    with get_session() as db:
        conflict = ActivityEnrollment(
            activity_id=act["id"],
            child_id=c["id"],
            order_id=None,
            ticket_code="TKPROBE_P29F1",
            status=ActivityEnrollment.STATUS_ENROLLED,
        )
        assert add_with_unique_fallback(db, conflict) is False, "活跃重复必须返回 False"
        assert conflict.id is None, "冲突对象必须被逐出会话（不得留下半截身份）"

        # 冲突之后同一事务必须仍然可用（保存点回滚而非整事务报废）
        ok = ActivityEnrollment(
            activity_id=act["id"],
            child_id=c["id"],
            order_id=None,
            ticket_code="TKPROBE_P29F2",
            status=ActivityEnrollment.STATUS_CANCELLED,
        )
        assert add_with_unique_fallback(db, ok) is True, "非活跃（不占位）必须插入成功"
        assert ok.id is not None, "保存点内必须真的 flush 落库，否则兜底形同虚设"
        db.rollback()

    with get_session() as db:
        n = db.execute(
            text("SELECT COUNT(*) FROM activity_enrollments WHERE ticket_code LIKE 'TKPROBE_P29F%'")
        ).scalar()
        assert n == 0, "探针行必须已回滚（不留脏数据）"


# ---------------------------------------------------------------- ⑦ 回归：两个入口的重复闸


def test_admin_duplicate_activity_order_still_rejected(client: TestClient):
    """回归：已有活跃报名时管理端再造单 → 422（锁内 dup 检查的精确文案，未被本批改动放宽）。"""
    h = _h(client)
    c, mini = _member_child(client, h, "13800003707")
    act = _activity(client, h, hours_later=72, title="重复造单回归")
    _enrolled_paid(client, h, mini, c["id"], act["id"])

    r = client.post(
        "/api/admin/orders",
        json={"child_id": c["id"], "order_type": "activity_fee", "activity_id": act["id"]},
        headers=h,
    )
    assert r.status_code == 422, f"已有活跃报名必须拦下：{r.status_code} {r.text}"
    assert r.json()["detail"] == ADMIN_DUP_MSG

    # 对照组：家长端重复报名 → 409（两个入口两套状态码，各自文案精确）
    again = client.post(
        f"/api/miniapp/activities/{act['id']}/enroll",
        json={"child_id": c["id"]},
        headers=mini,
    )
    assert again.status_code == 409, f"家长端重复报名必须 409：{again.status_code} {again.text}"
    assert again.json()["detail"] == DUP_ENROLL_MSG
