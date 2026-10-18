# tests/unit/test_gap_fixes_20261014.py — 第五十九批：**缺口集中修复**的回归（G1/G8/G10/G12/G13）
"""用户令（2026-10-14）：「发现的问题要及时修复好，并更新相关文档！」——本批把前几轮"只登记未修"的缺口逐条落地。

| 缺口 | PRD/依据 | 原实现 | 本批 |
|---|---|---|---|
| **G12** | §3.3「系统每周自动整理一份"待评估超过 7 天"的孩子名单**提醒馆员跟进**」 | 只返回一个计数（名单落不到任何人手上） | 逐人写管理端待办（scene=admin.pending_evaluation），ISO 周去重，转正/退会即**自动审结** |
| **G1** | §3.6「差额记为"待结清"，**系统持续提醒馆员**」 | 只有"随时可见 + 操作时被拦"（PRD 该行 2026-10-13 已如实标注） | 逐人写待办（scene=admin.deposit_settle）+ 新任务 `deposit_settle_remind`，结清即自动审结 |
| **G13** | 场景原句「状态变更被拒绝并**记录违规尝试**」（PRD 无明文） | 只抛异常，无留痕 | 转移矩阵拦截 + 状态守卫拒绝都写审计（action=member.transition_rejected），**独立会话**（拒绝路径主事务必然回滚） |
| **G8** | §5.2「确认借出。系统同时**自动创建这条书的"阅读记录"**」 | 只有"首次播报才建进度行" | 借出即建（唯一索引兜底）+ 书后补音频时分母自愈 |
| **G10** | 总榜=历史荣誉 | 「未入会但有词账」的异常数据也会上榜 | 总榜过滤 `member_status == none`（过期/退会的历史照旧保留） |

写在同一文件的原因：它们是**同一轮的"发现即修"批次**，逐条对照可读；每条各自独立用例、互不依赖。
"""

from datetime import datetime, timedelta
from decimal import Decimal

from fastapi.testclient import TestClient


def _h(client, username="admin"):
    r = client.post("/api/admin/login", json={"username": username, "password": "dmkwords123"})
    return {"Authorization": f"Bearer {r.json()['token']}"}


def _family(client, h, phone, child_name="孩子"):
    p = client.post(
        "/api/admin/members/parents", json={"name": "家长", "phone": phone}, headers=h
    ).json()
    c = client.post(
        f"/api/admin/members/parents/{p['id']}/children", json={"name": child_name}, headers=h
    ).json()
    return p, c


def _buy(client, h, child_id, order_type):
    o = client.post(
        "/api/admin/orders", json={"child_id": child_id, "order_type": order_type}, headers=h
    ).json()
    client.post(
        f"/api/admin/orders/{o['id']}/confirm-payment", json={"pay_method": "scan"}, headers=h
    )
    return o


def _observation_child(client, h, phone, name="观察孩"):
    _p, c = _family(client, h, phone, name)
    _buy(client, h, c["id"], "observation_fee")
    return c


def _set_child(**fields):
    from backend.database import get_session
    from backend.domain.identity.models import Child

    with get_session() as db:
        db.query(Child).filter(Child.id == fields.pop("child_id")).update(fields)
        db.commit()


def _inbox(client, h) -> list[dict]:
    return client.get(
        "/api/admin/admin-notifications", params={"page_size": 100}, headers=h
    ).json()["items"]


def _counts(client, h) -> dict:
    return client.get("/api/admin/todo-counts", headers=h).json()


def _run_task(client, h, name) -> dict:
    r = client.post(f"/api/admin/tasks/{name}/run", headers=h)
    assert r.status_code == 200, r.text
    return r.json()


def _row(client, h, *, title: str, child_id: int) -> dict:
    rows = [
        n
        for n in _inbox(client, h)
        if n["title"] == title and n["ref_type"] == "child" and n["ref_id"] == str(child_id)
    ]
    assert rows, f"应有「{title}」待办：{[n['title'] for n in _inbox(client, h)]}"
    return rows[0]


# ==================== G12：待评估每周名单 → 待办 ====================


def test_g12_pending_evaluation_weekly_creates_todo(client: TestClient):
    """PRD §3.3：超过 7 天未评估的孩子要进"名单提醒馆员"——现在是**待办墙上的实名待办**。"""
    h = _h(client)
    c = _observation_child(client, h, "13800002011")
    _set_child(
        child_id=c["id"],
        member_status="pending_evaluation",
        pending_evaluation_at=datetime.now() - timedelta(days=45),
    )

    res = _run_task(client, h, "pending_evaluation_weekly")
    assert res["processed"] >= 1, res

    row = _row(client, h, title="待评估跟进", child_id=c["id"])
    assert row["effective_status"] == "pending", row
    # 文案里的天数按"现在 − pending_evaluation_at"算（P2-13 第六十九批：时钟落库，不再挂 update_time）：DATETIME 秒级四舍五入会让"整 45 天"落成 44 天
    # （红线 117：造「整 N 天」要留舍入余量）→ 断言"超过阈值"而不是钉死 45
    import re

    m = re.search(r"待评估已 (\d+) 天", row["content"])
    assert m and int(m.group(1)) >= 7, row
    assert "转正" in row["content"], row
    counts = _counts(client, h)
    assert counts["pending_evaluation_followup"] == 1, counts
    assert counts["admin_total"] >= 1, counts


def test_g12_todo_auto_resolves_after_promotion(client: TestClient):
    """闭环：馆员办转正（年费收款）→ 该条**自动审结**、计数归零（不靠人工销项）。"""
    h = _h(client)
    c = _observation_child(client, h, "13800002012")
    _set_child(
        child_id=c["id"],
        member_status="pending_evaluation",
        pending_evaluation_at=datetime.now() - timedelta(days=10),
    )
    _run_task(client, h, "pending_evaluation_weekly")
    row_id = _row(client, h, title="待评估跟进", child_id=c["id"])["id"]
    assert _counts(client, h)["pending_evaluation_followup"] == 1

    _buy(client, h, c["id"], "formal_fee")  # 待评估 → formal

    row = next(n for n in _inbox(client, h) if n["id"] == row_id)
    assert row["effective_status"] == "done", row
    assert _counts(client, h)["pending_evaluation_followup"] == 0


def _week_keys(client, h, child_id: int) -> list[str]:
    """该孩子「待评估跟进」待办的去重键（按库里的真实值取，不猜）。"""
    from backend.common.admin_notification_models import AdminNotification
    from backend.database import get_session

    with get_session() as db:
        return sorted(
            r.dedup_key
            for r in db.query(AdminNotification)
            .filter(
                AdminNotification.scene == AdminNotification.SCENE_PENDING_EVALUATION,
                AdminNotification.ref_id == str(child_id),
                AdminNotification.is_deleted == 0,
            )
            .all()
        )


def _freeze_service_now(monkeypatch, when: datetime) -> None:
    """把 `identity/service.py` 里的 `datetime.now()` 冻结到 `when`（只影响该模块）。"""
    import backend.domain.identity.service as svc

    class _Frozen(datetime):
        @classmethod
        def now(cls, tz=None):  # noqa: ANN001
            return when

    monkeypatch.setattr(svc, "datetime", _Frozen)


def test_g12_weekly_dedup_within_same_week(client: TestClient, monkeypatch):
    """同一周重复跑只留一条（ISO 周去重）；**下一周由时间推进驱动**再提醒一次。

    P2-3（第七轮，2026-10-18）：原用例的"跨周"是**手工插一条 `dedup_key="week:1999-01"`**——
    那验证的是"唯一索引容得下两个不同键"，**不是代码的周键推导**：把代码的键改成常量
    （`week:2000-01`）它照样绿（审查实测变异 M3 存活）。现在改为**冻结时钟到下一 ISO 周**再跑
    **同一条任务**，断言两条**真实推导出来的**周键——键值写死为断言目标，常量键必红。
    """
    h = _h(client)
    c = _observation_child(client, h, "13800002013")
    _set_child(
        child_id=c["id"],
        member_status="pending_evaluation",
        pending_evaluation_at=datetime.now() - timedelta(days=30),
    )
    _run_task(client, h, "pending_evaluation_weekly")
    _run_task(client, h, "pending_evaluation_weekly")
    y0, w0, _ = datetime.now().isocalendar()
    assert _week_keys(client, h, c["id"]) == [f"week:{y0}-{w0}"], (
        f"同一周只提醒一条，实得 {_week_keys(client, h, c['id'])}"
    )

    # 时间推进 14 天（必跨 ISO 周）→ 同一条任务再跑 → 必须**再**提醒一条，且键是新周
    later = datetime.now() + timedelta(days=14)
    y1, w1, _ = later.isocalendar()
    assert (y1, w1) != (y0, w0), "构造有误：推进 14 天必须换周"
    _freeze_service_now(monkeypatch, later)
    _run_task(client, h, "pending_evaluation_weekly")

    assert _week_keys(client, h, c["id"]) == [f"week:{y0}-{w0}", f"week:{y1}-{w1}"], (
        f"跨周应各提醒一条且键按真实周推导，实得 {_week_keys(client, h, c['id'])}"
    )
    assert (
        len(
            [
                n
                for n in _inbox(client, h)
                if n["title"] == "待评估跟进" and n["ref_id"] == str(c["id"])
            ]
        )
        == 2
    ), "不同周应能各提醒一条（列表侧同口径）"


# ==================== G1：待结清主动提醒 ====================


def _deposit_with_unpaid(client, h, phone, amount="300.00", name="欠款孩"):
    _p, c = _family(client, h, phone, name)
    do = client.post(f"/api/admin/deposits/children/{c['id']}/orders", headers=h).json()
    client.post(
        f"/api/admin/orders/{do['order_id']}/confirm-payment",
        json={"pay_method": "scan"},
        headers=h,
    )
    # 造数：赔偿超额扣光 → 差额记"待结清"（真实链由 deposit.feature 覆盖，此处只要那个态）
    from backend.database import get_session
    from backend.domain.billing.models import Deposit

    with get_session() as db:
        db.query(Deposit).filter(Deposit.child_id == c["id"]).update(
            {"unpaid_balance": Decimal(amount), "status": "fully_deducted", "available_amount": 0}
        )
        db.commit()
    return c


def test_g1_deposit_unpaid_creates_todo(client: TestClient):
    """PRD §3.6「系统持续提醒馆员」：待结清要主动出现在待办墙上（不只是"随时可见"）。"""
    h = _h(client)
    c = _deposit_with_unpaid(client, h, "13800002021")

    res = _run_task(client, h, "deposit_settle_remind")
    assert res["processed"] >= 1, res

    row = _row(client, h, title="待结清跟进", child_id=c["id"])
    assert row["effective_status"] == "pending", row
    assert "300" in row["content"], row
    counts = _counts(client, h)
    assert counts["deposit_settle_followup"] == 1, counts


def test_g1_todo_auto_resolves_after_settled(client: TestClient):
    """闭环：补缴结清（unpaid_balance 归 0）→ 该条自动审结、计数归零。"""
    h = _h(client)
    c = _deposit_with_unpaid(client, h, "13800002022")
    _run_task(client, h, "deposit_settle_remind")
    row_id = _row(client, h, title="待结清跟进", child_id=c["id"])["id"]

    from backend.database import get_session
    from backend.domain.billing.models import Deposit

    with get_session() as db:
        db.query(Deposit).filter(Deposit.child_id == c["id"]).update({"unpaid_balance": 0})
        db.commit()

    row = next(n for n in _inbox(client, h) if n["id"] == row_id)
    assert row["effective_status"] == "done", row
    assert _counts(client, h)["deposit_settle_followup"] == 0


def test_g1_task_registered(client: TestClient):
    """新任务必须在任务看板注册（否则定时器跑不到）。"""
    h = _h(client)
    specs = client.get("/api/admin/tasks", headers=h).json()
    names = {s["name"] for s in specs.get("items", specs if isinstance(specs, list) else [])}
    assert "deposit_settle_remind" in names, names


# ==================== G13：状态机拒绝留痕 ====================


def test_g13_guard_rejection_audited(client: TestClient):
    """HTTP 可达的非法尝试（none 态直接转正）→ 422 **且留痕**（审计表有 member.transition_rejected）。"""
    h = _h(client)
    _p, c = _family(client, h, "13800002031", "未入会孩")

    r = client.post(
        f"/api/admin/members/children/{c['id']}/evaluate-approve",
        json={"reason": "G13：非法转正探针"},
        headers=h,
    )
    assert r.status_code == 422, r.text

    logs = client.get(
        "/api/admin/audit-logs",
        params={"action": "member.transition_rejected", "page_size": 50},
        headers=h,
    ).json()
    assert logs["total"] == 1, logs
    row = logs["items"][0]
    assert row["target_id"] == str(c["id"]), row
    assert "仅待评估孩子可评估通过转正" in row["reason"], row


def test_g13_matrix_rejection_audited_service_level(client: TestClient):
    """转移矩阵拦截（HTTP 无路径可达的非法对，如 none→expired）→ 抛错**且留痕**。"""
    import pytest

    from backend.common.exceptions import ValidationError
    from backend.database import get_session
    from backend.domain.identity.models import Child
    from backend.domain.identity.order_service import OrderService

    h = _h(client)
    _p, c = _family(client, h, "13800002032", "矩阵孩")

    with get_session() as db:
        child = db.query(Child).filter(Child.id == c["id"]).first()
        with pytest.raises(ValidationError):
            OrderService(db)._transition_member(child, Child.MEMBER_EXPIRED)

    logs = client.get(
        "/api/admin/audit-logs",
        params={"action": "member.transition_rejected", "page_size": 50},
        headers=h,
    ).json()
    assert logs["total"] == 1, logs
    row = logs["items"][0]
    assert row["target_id"] == str(c["id"]), row
    assert "转移矩阵" in row["reason"], row
    assert "ALLOWED_TRANSITIONS" in (row["detail"] or ""), row


def test_g13_legal_transition_not_audited(client: TestClient):
    """合法流转**不留**"违规尝试"痕（避免留痕被噪声淹没）。"""
    h = _h(client)
    c = _observation_child(client, h, "13800002033")
    _buy(client, h, c["id"], "formal_fee")  # observation → formal（合法）

    logs = client.get(
        "/api/admin/audit-logs",
        params={"action": "member.transition_rejected", "page_size": 50},
        headers=h,
    ).json()
    assert logs["total"] == 0, logs


# ==================== G8：借出即建阅读记录 ====================


def _book_with_audio(client, h, isbn, title="G8 样例书", seconds=1800):
    from backend.database import get_session

    b = client.post(
        "/api/admin/books",
        json={"isbn": isbn, "title": title, "word_count": 1000, "copy_count": 1},
        headers=h,
    ).json()
    from tests.unit.helpers import ensure_book_on

    ensure_book_on(client, h, b["id"])
    from backend.domain.catalog.models import Book

    with get_session() as db:
        db.query(Book).filter(Book.id == b["id"]).update(
            {
                "audio_path": f"bdd/audio-{b['id']}.mp3",
                "audio_duration_seconds": seconds,
            }
        )
        db.commit()
    return b


def test_g8_borrow_creates_reading_record(client: TestClient):
    """PRD §5.2：确认借出即**自动创建阅读记录**（原先只有首次播报才建）。"""
    h = _h(client)
    c = _observation_child(client, h, "13800002041")
    do = client.post(f"/api/admin/deposits/children/{c['id']}/orders", headers=h).json()
    client.post(
        f"/api/admin/orders/{do['order_id']}/confirm-payment",
        json={"pay_method": "scan"},
        headers=h,
    )
    book = _book_with_audio(client, h, "9789100030001")
    copies = client.get(f"/api/admin/books/{book['id']}/copies", headers=h).json()

    r = client.post(
        "/api/admin/circulation/borrow",
        json={"child_id": c["id"], "copy_id": copies[0]["id"]},
        headers=h,
    )
    assert r.status_code == 200, r.text

    from backend.database import get_session
    from backend.domain.reading.models import ReadingProgress

    with get_session() as db:
        row = (
            db.query(ReadingProgress)
            .filter(
                ReadingProgress.child_id == c["id"],
                ReadingProgress.book_id == book["id"],
                ReadingProgress.is_deleted == 0,
            )
            .first()
        )
    assert row is not None, "借出即应有阅读记录（PRD §5.2）"
    assert row.total_seconds == 1800, row.total_seconds
    assert row.coverage_seconds == 0 and row.finished == 0, (row.coverage_seconds, row.finished)


def test_g8_progress_denominator_self_heals(client: TestClient):
    """借出时书还没音频（分母 0）→ 后补音频 → 首次播报把分母刷新成真实时长（自愈，幂等）。"""
    from backend.database import get_session
    from backend.domain.catalog.models import Book
    from backend.domain.reading.models import ReadingProgress

    h = _h(client)
    c = _observation_child(client, h, "13800002042")
    b = client.post(
        "/api/admin/books",
        json={"isbn": "9789100030002", "title": "G8 无音频书", "word_count": 1000, "copy_count": 1},
        headers=h,
    ).json()
    from tests.unit.helpers import ensure_book_on

    ensure_book_on(client, h, b["id"])

    from backend.domain.reading.service import ensure_progress_row

    with get_session() as db:
        ensure_progress_row(db, child_id=c["id"], book_id=b["id"], total_seconds=0)
        db.commit()
        row = (
            db.query(ReadingProgress)
            .filter(ReadingProgress.child_id == c["id"], ReadingProgress.book_id == b["id"])
            .first()
        )
        assert row.total_seconds == 0
        # 馆员后补音频
        db.query(Book).filter(Book.id == b["id"]).update(
            {"audio_path": f"bdd/audio-{b['id']}.mp3", "audio_duration_seconds": 600}
        )
        db.commit()

    login = client.post("/api/miniapp/login", json={"phone": "13800002042", "code": "1234"}).json()
    mini = {"Authorization": f"Bearer {login['token']}"}
    r = client.post(
        "/api/miniapp/reading/progress",
        json={"child_id": c["id"], "book_id": b["id"], "position": 10, "session_start": 0},
        headers=mini,
    )
    assert r.status_code == 200, r.text
    assert r.json()["total_seconds"] == 600, r.json()
    assert r.json()["coverage_percent"] > 0, r.json()


# ==================== G10：总榜过滤"未入会但有词账" ====================


def test_g10_total_board_excludes_unpaid_with_words(client: TestClient):
    """总榜是历史荣誉，但**「未入会却有词账」是异常数据**（正常流程不可达）→ 不上榜。"""
    from backend.database import get_session
    from backend.domain.growth.models import WordsLedger

    h = _h(client)
    # 观看者：有效会员（否则看不了榜）
    viewer = _observation_child(client, h, "13800002051", "看榜孩")
    # 异常孩子：none 状态却有词账
    _p, ghost = _family(client, h, "13800002052", "异常孩")
    with get_session() as db:
        db.add(
            WordsLedger(
                child_id=ghost["id"], book_id=1, word_count=9000, source="quiz", is_deleted=0
            )
        )
        db.commit()

    login = client.post("/api/miniapp/login", json={"phone": "13800002051", "code": "1234"}).json()
    mini = {"Authorization": f"Bearer {login['token']}"}
    board = client.get(
        "/api/miniapp/leaderboard",
        params={"period": "total", "child_id": viewer["id"]},
        headers=mini,
    ).json()
    ids = [e["child_id"] for e in board["entries"]]
    assert ghost["id"] not in ids, "未入会但有词账的异常数据不得进总榜"
