# tests/unit/test_p0_batch4.py — 第四批（26 号任务包 T40-T47）
"""T41（H-1）：退款执行六项复核机器化（R-310）——execute 翻状态前查未清项，
不过 422 不翻状态（M2 裁定语义）；「人工放行:」冒号前缀+超管豁免+审计
manual_override 布尔字段（Q5 批复）。会员资格类（observation/formal）与押金
（KIND_DEPOSIT·担保语义 Q4 批复）触发；活动费/自定义不触发。"""

from datetime import date, datetime, timedelta
from decimal import Decimal
from pathlib import Path

from fastapi.testclient import TestClient

from tests.unit.test_wm10_concurrency import _family, _h, _pay
from tests.unit.test_wm13_admin_inbox import _db

REFUND_APPLY_URL = "/api/miniapp/refund-requests"


def _member_refund_to_approved(client, h, mini, child, order_id, reason="退款"):
    """会员费退款单推进到 approved，返回 rr id。"""
    rr = client.post(
        REFUND_APPLY_URL,
        json={"child_id": child["id"], "order_id": order_id, "reason": reason},
        headers=mini,
    ).json()
    r = client.post(
        f"/api/admin/refund-requests/{rr['id']}/review",
        json={"approve": True, "remark": "同意"},
        headers=h,
    )
    assert r.status_code == 200, r.text
    return rr["id"]


def _mk_overdue(ctx_db_flag=True, child_id=0, overdue=True):
    """直造借阅记录（preconditions 查询只按 child_id+status+due_at，无 JOIN）。"""
    from backend.database import get_session
    from backend.domain.circulation.models import BorrowRecord

    with get_session() as db:
        db.add(
            BorrowRecord(
                child_id=child_id,
                book_id=0,
                copy_id=0,
                status=BorrowRecord.STATUS_ACTIVE,
                due_at=datetime.now() - timedelta(days=3)
                if overdue
                else datetime.now() + timedelta(days=3),
            )
        )
        db.commit()


def test_t41_member_refund_blocked_by_overdue(client: TestClient):
    """修复前：有逾期孩退款执行直接 200（六项零实现）= RED。"""
    h = _h(client)
    p, c, mini = _family(client, h, "13981050001", "逾期孩")
    o = _pay(client, h, c["id"], "observation_fee")
    rid = _member_refund_to_approved(client, h, mini, c, o["id"])
    _mk_overdue(child_id=c["id"], overdue=True)
    r = client.post(
        f"/api/admin/refund-requests/{rid}/execute",
        json={"success": True, "remark": "打款"},
        headers=h,
    )
    assert r.status_code == 422, f"有逾期应 422 拦截，实 {r.status_code} {r.text[:120]} = RED"
    assert "逾期" in r.json()["detail"], r.text
    # 不翻状态（M2 语义：校验失败不流转）——仍 approved 可再执行
    from backend.domain.identity.models import RefundRequest

    with _db() as db:
        row = db.query(RefundRequest).filter(RefundRequest.id == rid).first()
        assert row.status == RefundRequest.STATUS_APPROVED, f"应保持 approved，实 {row.status}"


def test_t41_passes_after_overdue_cleared(client: TestClient):
    """清逾期后执行通过。"""
    h = _h(client)
    p, c, mini = _family(client, h, "13981050002", "清欠孩")
    o = _pay(client, h, c["id"], "observation_fee")
    rid = _member_refund_to_approved(client, h, mini, c, o["id"])
    _mk_overdue(child_id=c["id"], overdue=True)
    # 还书（状态置 returned）
    from backend.database import get_session
    from backend.domain.circulation.models import BorrowRecord

    with get_session() as db:
        db.query(BorrowRecord).filter(BorrowRecord.child_id == c["id"]).update(
            {"status": BorrowRecord.STATUS_RETURNED}
        )
        db.commit()
    r = client.post(
        f"/api/admin/refund-requests/{rid}/execute",
        json={"success": True, "remark": "打款"},
        headers=h,
    )
    assert r.status_code == 200, r.text


def test_t41_manual_override_with_audit(client: TestClient):
    """「人工放行:」前缀+超管 → 跳过复核+审计含 manual_override。"""
    h = _h(client)
    p, c, mini = _family(client, h, "13981050003", "放行孩")
    o = _pay(client, h, c["id"], "observation_fee")
    rid = _member_refund_to_approved(client, h, mini, c, o["id"])
    _mk_overdue(child_id=c["id"], overdue=True)
    r = client.post(
        f"/api/admin/refund-requests/{rid}/execute",
        json={"success": True, "remark": "人工放行: 家长当面结清承诺"},
        headers=h,
    )
    assert r.status_code == 200, r.text
    with _db() as db:
        from backend.domain.admin.models import AuditLog

        row = (
            db.query(AuditLog)
            .filter(
                AuditLog.action == "refund.execute",
                AuditLog.target_id == str(rid),
                AuditLog.is_deleted == 0,
            )
            .order_by(AuditLog.id.desc())
            .first()
        )
        assert row is not None, "执行审计应存在"
        import json

        detail = json.loads(row.detail) if isinstance(row.detail, str) else row.detail
        assert detail.get("manual_override") is True, f"审计应含 manual_override：{detail}"


def test_t41_activity_order_not_checked(client: TestClient):
    """活动费单不触发复核（退款不动会员态）。"""
    from datetime import timedelta

    h = _h(client)
    p, c, mini = _family(client, h, "13981050004", "活动款孩")
    act = client.post(
        "/api/admin/activities",
        json={
            "title": "复核豁免活动",
            "activity_type": "book_club",
            "start_at": (datetime.now() + timedelta(hours=72)).isoformat(),
            "location": "馆内",
            "max_quota": 5,
            "fee": 60,
            "description": "T41",
            "member_only": False,
        },
        headers=h,
    ).json()
    e = client.post(
        f"/api/miniapp/activities/{act['id']}/enroll", json={"child_id": c["id"]}, headers=mini
    ).json()
    client.post(
        f"/api/admin/orders/{e['order_id']}/confirm-payment", json={"pay_method": "scan"}, headers=h
    )
    _mk_overdue(child_id=c["id"], overdue=True)
    rr = client.post(
        REFUND_APPLY_URL,
        json={"child_id": c["id"], "order_id": e["order_id"], "reason": "不参加了"},
        headers=mini,
    ).json()
    assert (
        client.post(
            f"/api/admin/refund-requests/{rr['id']}/review",
            json={"approve": True, "remark": "同意"},
            headers=h,
        ).status_code
        == 200
    )
    r = client.post(
        f"/api/admin/refund-requests/{rr['id']}/execute",
        json={"success": True, "remark": "打款"},
        headers=h,
    )
    assert r.status_code == 200, f"活动费退款不触发复核，实 {r.status_code} {r.text[:120]}"


def test_t41_deposit_checked(client: TestClient):
    """押金单触发复核（担保语义：未还书退押金=担保落空，Q4 批复）。"""
    h = _h(client)
    p, c, mini = _family(client, h, "13981050005", "押金孩")
    do = client.post(f"/api/admin/deposits/children/{c['id']}/orders", headers=h).json()
    client.post(
        f"/api/admin/orders/{do['order_id']}/confirm-payment",
        json={"pay_method": "scan"},
        headers=h,
    )
    _mk_overdue(child_id=c["id"], overdue=True)
    # 造 approved 押金退款单（复核在 execute 层，直达）
    from backend.database import get_session
    from backend.domain.identity.models import RefundRequest

    with get_session() as db:
        from backend.domain.billing.models import Deposit

        dep = db.query(Deposit).filter(Deposit.child_id == c["id"]).first()
        rr = RefundRequest(
            kind=RefundRequest.KIND_DEPOSIT,
            child_id=c["id"],
            deposit_id=dep.id,
            amount=Decimal("200"),
            reason="退会退押金",
            status=RefundRequest.STATUS_APPROVED,
        )
        db.add(rr)
        db.flush()
        rid = rr.id
        db.commit()
    r = client.post(
        f"/api/admin/refund-requests/{rid}/execute",
        json={"success": True, "remark": "打款"},
        headers=h,
    )
    assert r.status_code == 422, f"押金退款未还书应 422，实 {r.status_code} {r.text[:120]} = RED"


# ---------- T43：书架角标批量接口（U2——一次 IN 查询禁 N+1） ----------


def test_t43_quiz_status_batch(client: TestClient):
    """修复前：接口不存在 = RED。3 书三态一次返回（行为断言）。"""

    from backend.database import get_session
    from backend.domain.catalog.models import Book
    from backend.domain.growth.models import QuizAttempt, WordsLedger
    from backend.domain.reading.models import ReadingProgress

    h = _h(client)
    p, c, mini = _family(client, h, "13981060001", "角标孩")
    with get_session() as db:
        books = []
        for i in range(3):
            b = Book(title=f"角标书{i}", isbn=f"97810980000{i:02d}", is_deleted=0)
            db.add(b)
            books.append(b)
        db.flush()
        b1, b2, b3 = [b.id for b in books]
        # b1：passed（finished+词账）；b2：available（finished 无词账）；b3：locked
        db.add(ReadingProgress(child_id=c["id"], book_id=b1, finished=1))
        db.add(ReadingProgress(child_id=c["id"], book_id=b2, finished=1))
        db.add(WordsLedger(child_id=c["id"], book_id=b1, word_count=10))
        db.add(
            QuizAttempt(
                child_id=c["id"],
                book_id=b1,
                score=9,
                total_questions=10,
            )
        )
        db.commit()

    r = client.get(
        "/api/miniapp/quiz/status-batch",
        params={"child_id": c["id"], "book_ids": f"{b1},{b2},{b3}"},
        headers=mini,
    )
    assert r.status_code == 200, r.text
    data = {x["book_id"]: x for x in r.json()["items"]}
    assert data[b1]["status"] == "passed", f"实 {data}"
    assert data[b1]["best_percent"] == 90
    assert data[b2]["status"] == "available"
    assert data[b3]["status"] == "locked"


# ---------- T45：FEAT-082 活动封面+详情/编辑+轮播 ----------


def _png_bytes():
    """最小 PNG（1x1）。"""
    import struct
    import zlib

    def chunk(typ, data):
        c = struct.pack(">I", len(data)) + typ + data
        return c + struct.pack(">I", zlib.crc32(typ + data) & 0xFFFFFFFF)

    ihdr = struct.pack(">IIBBBBB", 1, 1, 8, 2, 0, 0, 0)
    idat = zlib.compress(b"\x00\xff\x00\x00")
    return b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", ihdr) + chunk(b"IDAT", idat) + chunk(b"IEND", b"")


def test_t45_activity_detail_and_update(client: TestClient):
    """修复前：详情/编辑端点不存在 = RED。规则：PUBLISHED+未开始可编辑；
    activity_type 禁改（schema forbid=422）；名额下限 Q7（低于活跃报名数 422，等于放行）。"""
    from datetime import timedelta

    h = _h(client)
    p, c, mini = _family(client, h, "13981070001", "编辑孩")
    act = client.post(
        "/api/admin/activities",
        json={
            "title": "编辑前活动",
            "activity_type": "book_club",
            "start_at": (datetime.now() + timedelta(hours=72)).isoformat(),
            "location": "馆内一层",
            "max_quota": 5,
            "fee": 60,
            "description": "T45",
            "member_only": False,
        },
        headers=h,
    ).json()
    e = client.post(
        f"/api/miniapp/activities/{act['id']}/enroll", json={"child_id": c["id"]}, headers=mini
    ).json()
    client.post(
        f"/api/admin/orders/{e['order_id']}/confirm-payment", json={"pay_method": "scan"}, headers=h
    )

    # 详情（含报名统计）
    d = client.get(f"/api/admin/activities/{act['id']}", headers=h)
    assert d.status_code == 200, f"详情端点应 200，实 {d.status_code} = RED"
    assert d.json()["enrolled_count"] == 1, f"报名统计应 1，实 {d.json()}"

    # 正常编辑（title/location/max_quota 等于活跃数=1 放行）
    u = client.put(
        f"/api/admin/activities/{act['id']}",
        json={"title": "编辑后活动", "location": "馆内二层", "max_quota": 1},
        headers=h,
    )
    assert u.status_code == 200, f"编辑应 200，实 {u.status_code} {u.text[:120]} = RED"
    assert (
        client.get(f"/api/admin/activities/{act['id']}", headers=h).json()["title"] == "编辑后活动"
    )

    # activity_type 禁改（extra=forbid → 422）
    u2 = client.put(
        f"/api/admin/activities/{act['id']}",
        json={"title": "x", "activity_type": "parent_child"},
        headers=h,
    )
    assert u2.status_code == 422, f"activity_type 禁改应 422，实 {u2.status_code} = RED"

    # 名额下限（缩到低于活跃报名数）
    u3 = client.put(f"/api/admin/activities/{act['id']}", json={"max_quota": 0}, headers=h)
    assert u3.status_code == 422, f"名额低于已报名应 422，实 {u3.status_code} = RED"


def test_t45_cover_upload_and_carousel(client: TestClient):
    """封面上传→cover-media→miniapp 封面/轮播全链。"""
    from datetime import timedelta

    h = _h(client)
    p, c, mini = _family(client, h, "13981070002", "轮播孩")
    act = client.post(
        "/api/admin/activities",
        json={
            "title": "轮播活动",
            "activity_type": "book_club",
            "start_at": (datetime.now() + timedelta(hours=72)).isoformat(),
            "location": "馆内",
            "max_quota": 5,
            "fee": 0,
            "description": "轮播",
            "member_only": False,
        },
        headers=h,
    ).json()
    up = client.post(
        f"/api/admin/activities/{act['id']}/cover",
        files={"file": ("c.png", _png_bytes(), "image/png")},
        headers=h,
    )
    assert up.status_code == 200, f"封面上传应 200，实 {up.status_code} {up.text[:120]} = RED"
    from backend.database import get_session
    from backend.domain.activity.models import Activity

    with get_session() as db:
        a = db.query(Activity).filter(Activity.id == act["id"]).first()
        assert a.cover_path, "cover_path 应落库 = RED"
    # 管理端查看（Bearer）
    m = client.get(f"/api/admin/activities/{act['id']}/cover-media", headers=h)
    assert m.status_code == 200, f"cover-media 应 200，实 {m.status_code} = RED"
    # miniapp 轮播（有封面 PUBLISHED 未开始 ≤5）
    cr = client.get("/api/miniapp/activities/carousel", headers=mini)
    assert cr.status_code == 200, f"轮播端点应 200，实 {cr.status_code} = RED"
    items = cr.json()["items"]
    assert any(x["id"] == act["id"] for x in items), f"轮播应含封面活动，实 {items} = RED"
    assert all(x.get("cover_url") for x in items), "轮播项应带 cover_url"
    # miniapp 封面公开端点
    cv = client.get(f"/api/miniapp/activities/{act['id']}/cover", headers=mini)
    assert cv.status_code == 200, f"miniapp 封面应 200，实 {cv.status_code} = RED"


# ---------- 插修 15 R2：C-13 音频会员门禁（guards AUDIO·403） ----------


def _set_member_state(child_id: int, status: str, expire_offset_days: int | None = None):
    from datetime import timedelta

    from backend.database import get_session
    from backend.domain.identity.models import Child

    with get_session() as db:
        c = db.query(Child).filter(Child.id == child_id).first()
        c.member_status = status
        c.member_expire = (
            date.today() + timedelta(days=expire_offset_days)
            if expire_offset_days is not None
            else None
        )
        db.commit()


def _mk_audio_file(rel: str):
    """造真实音频文件（audio 端点 isfile 校验需要）。"""
    import os

    from backend.config import get_settings

    full = os.path.join(get_settings().UPLOADS_DIR, rel)
    os.makedirs(os.path.dirname(full), exist_ok=True)
    if not os.path.isfile(full):
        Path(full).write_bytes(b"ID3fake-mp3")


def _mk_borrow_holding(child_id: int, book_id: int):
    from datetime import datetime, timedelta

    from backend.database import get_session
    from backend.domain.circulation.models import BorrowRecord

    with get_session() as db:
        db.add(
            BorrowRecord(
                child_id=child_id,
                book_id=book_id,
                copy_id=0,
                status=BorrowRecord.STATUS_ACTIVE,
                borrowed_at=datetime.now(),
                due_at=datetime.now() + timedelta(days=14),
            )
        )
        db.commit()


def test_r2_audio_membership_guard(client: TestClient):
    """修复前：audio 端点只验家长 token 无会员守卫——退会/未缴费直链可听 = RED。"""

    h = _h(client)
    # 演示家庭（formal）+ 音频书（seed_demo_library 的 Brown Bear 有音频——直造一本书带音频）
    p, c, mini = _family(client, h, "13981080001", "音频孩")
    from backend.database import get_session
    from backend.domain.catalog.models import Book

    with get_session() as db:
        b = Book(
            title="音频测试书",
            isbn="9781099000001",
            is_deleted=0,
            audio_path="audio/test.mp3",
            audio_duration_seconds=100,
            status=1,
        )
        db.add(b)
        db.flush()
        bid = b.id
        db.commit()
    _mk_audio_file("audio/test.mp3")
    _set_member_state(c["id"], "formal", 30)
    _mk_borrow_holding(c["id"], bid)

    base = f"/api/miniapp/books/{bid}/audio"
    # 在册 → 200
    _set_member_state(c["id"], "formal", 30)
    r = client.get(base, params={"token": mini["Authorization"].split(" ")[1], "child_id": c["id"]})
    assert r.status_code == 200, f"在册应 200，实 {r.status_code} {r.text[:120]}"
    # 未缴费 → 403
    _set_member_state(c["id"], "none")
    r = client.get(base, params={"token": mini["Authorization"].split(" ")[1], "child_id": c["id"]})
    assert r.status_code == 403, f"未缴费应 403，实 {r.status_code} = RED"
    # 退会 → 403
    _set_member_state(c["id"], "withdrawn")
    r = client.get(base, params={"token": mini["Authorization"].split(" ")[1], "child_id": c["id"]})
    assert r.status_code == 403, f"退会应 403，实 {r.status_code} = RED"
    # 过期+非在借（换一本没借的书）→ 403
    _set_member_state(c["id"], "formal", -3)
    with get_session() as db:
        b2 = Book(
            title="音频对照书",
            isbn="9781099000002",
            is_deleted=0,
            audio_path="audio/test2.mp3",
            audio_duration_seconds=100,
            status=1,
        )
        db.add(b2)
        db.flush()
        bid2 = b2.id
        db.commit()
    _mk_audio_file("audio/test2.mp3")
    r = client.get(
        f"/api/miniapp/books/{bid2}/audio",
        params={"token": mini["Authorization"].split(" ")[1], "child_id": c["id"]},
    )
    assert r.status_code == 403, f"过期+非在借应 403，实 {r.status_code} = RED"
    # 过期+在借该书 → 200
    r = client.get(base, params={"token": mini["Authorization"].split(" ")[1], "child_id": c["id"]})
    assert r.status_code == 200, f"过期+在借应 200（仅手头在借允），实 {r.status_code}"


def test_r2_report_progress_guard_regression(client: TestClient):
    """report_progress 收口 guards（散落判定删除防漂移）：退会 → 403。"""

    h = _h(client)
    p, c, mini = _family(client, h, "13981080002", "进度孩")
    from backend.database import get_session
    from backend.domain.catalog.models import Book

    with get_session() as db:
        b = Book(
            title="进度测试书",
            isbn="9781099000003",
            is_deleted=0,
            audio_path="audio/t3.mp3",
            audio_duration_seconds=100,
            status=1,
        )
        db.add(b)
        db.flush()
        bid = b.id
        db.commit()
    _mk_audio_file("audio/t3.mp3")
    _set_member_state(c["id"], "withdrawn")
    r = client.post(
        "/api/miniapp/reading/progress",
        json={"child_id": c["id"], "book_id": bid, "position": 5},
        headers=mini,
    )
    assert r.status_code == 403, f"退会上报应 403，实 {r.status_code} {r.text[:120]}"


# ---------- 插修 16 R3：播放入口前置拦截（audio-permission 端点） ----------


def test_r3_audio_permission_endpoint(client: TestClient):
    """修复前：端点不存在 = RED。五态断言对齐 guards.AUDIO 矩阵（禁两端点漂移）。"""

    from backend.database import get_session
    from backend.domain.catalog.models import Book

    h = _h(client)
    p, c, mini = _family(client, h, "13981090001", "预检孩")
    with get_session() as db:
        b = Book(
            title="预检书",
            isbn="9781099500001",
            is_deleted=0,
            audio_path="audio/pre.mp3",
            audio_duration_seconds=100,
            status=1,
        )
        db.add(b)
        db.flush()
        bid = b.id
        db.commit()

    url = f"/api/miniapp/books/{bid}/audio-permission"
    # 在册 → allowed
    _set_member_state(c["id"], "formal", 30)
    r = client.get(url, params={"child_id": c["id"]}, headers=mini)
    assert r.status_code == 200, r.text
    assert r.json()["allowed"] is True and r.json()["reason"] == "ok", r.json()
    # 未缴费 → 禁（reason=unpaid）
    _set_member_state(c["id"], "none")
    r = client.get(url, params={"child_id": c["id"]}, headers=mini)
    assert r.json()["allowed"] is False and r.json()["reason"] == "unpaid", r.json()
    # 退会 → 禁（reason=withdrawn）
    _set_member_state(c["id"], "withdrawn")
    r = client.get(url, params={"child_id": c["id"]}, headers=mini)
    assert r.json()["allowed"] is False and r.json()["reason"] == "withdrawn", r.json()
    # 过期+非在借 → 禁（reason=expired）
    _set_member_state(c["id"], "formal", -3)
    r = client.get(url, params={"child_id": c["id"]}, headers=mini)
    assert r.json()["allowed"] is False and r.json()["reason"] == "expired", r.json()
    # 过期+在借该书 → 允
    _mk_borrow_holding(c["id"], bid)
    r = client.get(url, params={"child_id": c["id"]}, headers=mini)
    assert r.json()["allowed"] is True and r.json()["reason"] == "ok", r.json()
