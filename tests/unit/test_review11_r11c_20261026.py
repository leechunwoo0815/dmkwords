# tests/unit/test_review11_r11c_20261026.py — 第十一轮整改 R11-c1（第一百五十二批）
"""R11-c1 口径/契约/公共层小项的机器判据（审查报告 §十：F3/B7/B8/B9/B10/G2/G5/G6/G7/G8/G9/C1-C5）。

B5（突破卡新高校验）、G3（三处正确率口径）、G4（N+1/全表加载）体量较大，留 R11-c2。
"""

from __future__ import annotations

import re
from datetime import datetime, timedelta
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.exc import IntegrityError

from backend.common.admin_notification_models import AdminNotification
from backend.common.admin_notifications import AdminNotifyService
from backend.common.exceptions import ValidationError
from backend.common.notification_models import Notification
from backend.common.sql_utils import add_with_unique_fallback
from backend.database import get_session
from backend.domain.activity.models import Activity, ActivityEnrollment
from backend.domain.activity.service import ActivityService
from backend.domain.admin.models import AdminUser, AuditLog
from backend.domain.catalog.models import BookCopy
from backend.domain.circulation.models import BorrowRecord
from backend.domain.circulation.service import CirculationService
from backend.domain.growth.report_service import ReportService
from backend.domain.identity.models import Child, Parent
from backend.domain.reading.vocabulary_service import FavoriteService
from tests.unit.test_wm10_concurrency import (
    _book_with_copies,
    _family,
    _h,
    _pay,
    _pay_deposit,
)


def _overdue_copy(client, h, phone, title):
    _p, c, _mini = _family(client, h, phone, title)
    _pay(client, h, c["id"], "observation_fee")
    _pay_deposit(client, h, c["id"])
    bid = _book_with_copies(client, h, f"{title}书", 1)
    with get_session() as db:
        copy = db.query(BookCopy).filter(BookCopy.book_id == bid).first()
        copy_id, code = copy.id, copy.copy_code
    r = client.post(
        "/api/admin/circulation/borrow", json={"child_id": c["id"], "copy_id": copy_id}, headers=h
    )
    assert r.status_code == 200, r.text
    with get_session() as db:
        rec = (
            db.query(BorrowRecord)
            .filter(
                BorrowRecord.copy_id == copy_id, BorrowRecord.status == BorrowRecord.STATUS_ACTIVE
            )
            .first()
        )
        rec.due_at = datetime.now() - timedelta(days=2)
        db.commit()
    return copy_id, code


# ---------------- F3 / B7 / B8 ----------------


def test_deposit_ledgers_missing_child_404(client: TestClient):
    """RED（缺 ensure_child）：不存在的孩子回 200 []，与兄弟端点 404 口径不一。"""
    r = client.get("/api/admin/deposits/children/999999/ledgers", headers=_h(client))
    assert r.status_code == 404, r.text


def test_overdue_list_returns_copy_code(client: TestClient):
    """RED（未 join BookCopy）：契约字段 copy_code 恒为空串。"""
    h = _h(client)
    _copy_id, code = _overdue_copy(client, h, "13982026301", "逾期码孩")
    r = client.get("/api/admin/circulation/overdue", headers=h)
    assert r.status_code == 200
    assert r.json()[0]["copy_code"] == code, "催还名单必须带副本码（契约字段不能恒空）"


def test_overdue_truncation_uses_raw_count(client: TestClient, monkeypatch):
    """RED（按剔除后的条数判截断）：并发还书剔除后 loaded<limit ⇒ 误报未截断。"""
    h = _h(client)
    for i in range(2):
        _overdue_copy(client, h, f"1398202631{i}", f"逾期截断{i}")
    monkeypatch.setattr(CirculationService, "OVERDUE_LIST_LIMIT", 1)
    with get_session() as db:
        svc = CirculationService(db)
        assert len(svc.overdue_list()) == 1
        assert svc.overdue_truncated is True, "原始取数 2 ≥ 上限 1 即已截断（与剔除后的条数无关）"

    # 并发剔除把 kept 减到 0 时，响应头仍必须按**原始取数**报截断
    def fake_overdue(self):
        self.overdue_truncated = True
        return []

    monkeypatch.setattr(CirculationService, "overdue_list", fake_overdue)
    r = client.get("/api/admin/circulation/overdue", headers=h)
    assert r.json() == []
    assert r.headers.get("x-list-truncated") == "true", (
        "修复前按 len(out)=0 < limit 判 → false（漏告知还有更多）"
    )


# ---------------- B9 / B10 ----------------


def test_favorite_add_survives_unique_conflict(db, monkeypatch):
    """RED（不捕获 IntegrityError）：并发双击收藏 → 唯一索引冲突裸 500。"""
    parent = Parent(name="收藏家长", phone="13982026320")
    db.add(parent)
    db.flush()
    child = Child(parent_id=parent.id, name="收藏孩")
    db.add(child)
    db.flush()
    from backend.domain.catalog.models import Book

    book = Book(title="收藏书", word_count=100)
    db.add(book)
    db.flush()

    calls = {"n": 0}
    real_commit = db.commit

    def flaky_commit():
        calls["n"] += 1
        if calls["n"] == 1:
            db.rollback()
            raise IntegrityError("INSERT", {}, Exception("Duplicate entry 'x' for key 'uq'"))
        return real_commit()

    monkeypatch.setattr(db, "commit", flaky_commit)
    out = FavoriteService(db).add(child, book.id)
    assert out["book_id"] == book.id


def test_lookup_rejects_missing_book_id(client: TestClient):
    """RED（不校验 book_id）：不存在的书也能落库并计入"来源书目"。"""
    from backend.domain.reading.models import DictionaryWord

    h = _h(client)
    _p, c, mini = _family(client, h, "13982026321", "查词孩")
    _pay(client, h, c["id"], "observation_fee")
    _pay_deposit(client, h, c["id"])
    word = "probezz"
    with get_session() as db:
        db.add(DictionaryWord(word=word, phonetic="", definition="d", translation="t"))
        db.commit()
    try:
        r = client.get(
            f"/api/miniapp/vocabulary/lookup?word={word}&child_id={c['id']}&book_id=999999",
            headers=mini,
        )
        assert r.status_code == 404, r.text
    finally:
        with get_session() as db:
            db.query(DictionaryWord).filter(DictionaryWord.word == word).delete()
            db.commit()


# ---------------- G2 / G5 / G6 / G7 / G8 / G9 ----------------


def test_copy_status_audit_target_is_book_copy(client: TestClient):
    """RED（_audit 固定 target_type=book）：副本审计挂到书目 id 上，审计页指错对象。"""
    h = _h(client)
    bid = _book_with_copies(client, h, "副本审计书", 1)
    with get_session() as db:
        copy_id = db.query(BookCopy).filter(BookCopy.book_id == bid).first().id
    r = client.put(
        f"/api/admin/copies/{copy_id}/status",
        json={"status": "maintenance", "reason": "R11-c 判据"},
        headers=h,
    )
    assert r.status_code == 200, r.text
    with get_session() as db:
        row = (
            db.query(AuditLog)
            .filter(AuditLog.action == "copy.status", AuditLog.target_id == str(copy_id))
            .first()
        )
    assert row is not None
    assert row.target_type == "book_copy", (
        f"副本动作的 target_type 必须是 book_copy，实 {row.target_type}"
    )


def test_cancel_activity_notifies_only_affected_families(db):
    """RED（按全部报名家庭发通知）：已取消/已退的家庭也收到"已付费用将进入退款审核"。"""
    admin = db.query(AdminUser).first()
    act = Activity(
        title="取消通知活动",
        activity_type="book_club",
        start_at=datetime.now() + timedelta(days=3),
        max_quota=10,
        status=Activity.STATUS_PUBLISHED,
    )
    db.add(act)
    db.flush()
    p_a = Parent(name="A家长", phone="13982026330")
    p_b = Parent(name="B家长", phone="13982026331")
    db.add_all([p_a, p_b])
    db.flush()
    c_a = Child(parent_id=p_a.id, name="A孩")
    c_b = Child(parent_id=p_b.id, name="B孩")
    db.add_all([c_a, c_b])
    db.flush()
    db.add_all(
        [
            ActivityEnrollment(
                activity_id=act.id,
                child_id=c_a.id,
                status=ActivityEnrollment.STATUS_ENROLLED,
                ticket_code="R11C-A-1",
            ),
            ActivityEnrollment(
                activity_id=act.id,
                child_id=c_b.id,
                status=ActivityEnrollment.STATUS_CANCELLED,
                ticket_code="R11C-B-1",
            ),
        ]
    )
    db.commit()

    ActivityService(db).cancel_activity(admin, act.id)
    db.commit()
    a_notes = db.query(Notification).filter(Notification.parent_id == p_a.id).all()
    b_notes = db.query(Notification).filter(Notification.parent_id == p_b.id).all()
    assert len(a_notes) == 1 and "无需退款" in a_notes[0].content
    assert b_notes == [], "早已取消报名的家庭不该收到活动取消退款承诺"


def test_upcoming_includes_activity_started_five_hours_ago(db):
    """RED（近期窗口 2h）：活动开始后 2–24h 两个 tab 都不显示。"""
    act = Activity(
        title="进行中活动",
        activity_type="book_club",
        start_at=datetime.now() - timedelta(hours=5),
        max_quota=10,
        status=Activity.STATUS_PUBLISHED,
    )
    db.add(act)
    db.commit()
    ids = [row["id"] for row in ActivityService(db).list_upcoming()]
    assert act.id in ids


def test_quiz_endpoints_404_for_missing_book(client: TestClient):
    """RED（不查 Book）：可给不存在的书建孤儿题，列表还回 200 []。"""
    h = _h(client)
    body = {
        "question_type": "boolean",
        "question_text": "测试题",
        "options": ["对", "错"],
        "answer": "对",
    }
    r = client.post("/api/admin/books/999999/questions", json=body, headers=h)
    assert r.status_code == 404, r.text
    r2 = client.get("/api/admin/books/999999/questions", headers=h)
    assert r2.status_code == 404, r2.text


def test_dashboard_accepts_fresh_flag(client: TestClient):
    """RED（docstring 承诺但参数不存在）：?fresh=1 无法强制刷新。"""
    r = client.get("/api/admin/dashboard?fresh=1", headers=_h(client))
    assert r.status_code == 200, r.text


def test_weekly_report_label_is_seven_days(db):
    """RED（label 直接用排他上界）：周报显示成 8 个日历日。"""
    _start, _end, label = ReportService(db).period_range("weekly")
    m = re.fullmatch(r"(\d{4})年(\d{2})月(\d{2})日 - (\d{2})月(\d{2})日", label)
    assert m, label
    y, mo, d, mo2, d2 = map(int, m.groups())
    first = datetime(y, mo, d)
    last = datetime(y, mo2, d2)
    assert (last - first).days == 6, f"周报应显示 7 个日历日，实 {label}"


# ---------------- C1-C5 ----------------


def test_413_carries_security_headers(client: TestClient):
    """RED（BodyLimit 直接发 413）：最大的请求反而 0 个安全头。"""
    r = client.post(
        "/api/pay/wechat/notify",
        content=b"x" * (70 * 1024),
        headers={"content-type": "application/json"},
    )
    assert r.status_code == 413, r.text
    for key in (
        "x-content-type-options",
        "x-frame-options",
        "content-security-policy",
        "referrer-policy",
        "permissions-policy",
        "cross-origin-opener-policy",
    ):
        assert key in {k.lower() for k in r.headers}, f"413 缺安全头 {key}"


def test_truncated_mp3_xing_returns_zero():
    """RED（struct.unpack 未捕获）：截断 Xing 头抛 struct.error → 500。"""
    from backend.common.file_storage import _mp3_duration

    data = b"\xff\xfb\x90\x64" + b"Xing"  # 合法帧头 + Xing 后不足 8 字节
    assert _mp3_duration(data) == 0


def test_activity_detail_image_failure_leaves_no_file(tmp_path, monkeypatch):
    """RED（先 open("wb") 再 normalize）：校验失败留 0 字节孤儿，且目录在"绝不清理"名单。"""
    import backend.common.file_storage as fs

    monkeypatch.setattr(fs, "get_settings", lambda: SimpleNamespace(UPLOADS_DIR=str(tmp_path)))
    policy = fs.ImagePolicy(
        max_edge=1200, quality=85, max_input_bytes=1024 * 1024, max_output_bytes=200 * 1024
    )
    with pytest.raises(ValidationError):
        fs.save_activity_detail_image(1, b"not-an-image", ".jpg", policy)
    leftovers = (
        list((tmp_path / "activity_detail").glob("*"))
        if (tmp_path / "activity_detail").exists()
        else []
    )
    assert leftovers == [], f"校验失败不得留文件：{leftovers}"


def test_notification_revive_clears_extra(db):
    """RED（复活不清 extra）：旧处理说明留在了新待办上。"""
    db.add(
        AdminNotification(
            scene="admin.test_revive",
            title="旧标题",
            content="旧内容",
            ref_type="probe",
            ref_id="r11c",
            dedup_key="d1",
            handled_at=datetime.now(),
            handled_by=1,
            extra='{"old": 1}',
        )
    )
    db.commit()
    AdminNotifyService(db).send(
        scene="admin.test_revive",
        title="新标题",
        content="新内容",
        ref_type="probe",
        ref_id="r11c",
        dedup_key="d1",
        ignore_handled=True,
    )
    db.commit()
    row = db.query(AdminNotification).filter(AdminNotification.scene == "admin.test_revive").first()
    assert row.handled_at is None
    assert row.extra is None, "复活必须清掉上一轮的处理说明（extra）"


def test_unique_fallback_only_swallows_duplicate(db, monkeypatch):
    """RED（宽捕获）：NOT NULL 违约被误判成"并发重复"，调用方给出错误业务提示。"""

    # 非重复类 IntegrityError 必须照抛（用 begin_nested 注入一个非 1062 的冲突）
    def raise_not_null():
        raise IntegrityError("INSERT", {}, Exception("Column 'name' cannot be null"))

    monkeypatch.setattr(db, "begin_nested", raise_not_null)
    with pytest.raises(IntegrityError, match="cannot be null"):
        add_with_unique_fallback(db, Parent(name="x", phone="13982026340"))
    monkeypatch.undo()
    # 真正的唯一键冲突仍然静默返回 False
    db.add(Parent(name="重复家长", phone="13982026341"))
    db.commit()
    assert add_with_unique_fallback(db, Parent(name="重复家长2", phone="13982026341")) is False
