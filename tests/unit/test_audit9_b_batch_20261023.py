# tests/unit/test_audit9_b_batch_20261023.py — 第九轮审查 B 批（P2 资金与状态机）
"""逐条对应 `docs/整改任务书-第九轮-20261022.md` §三 B 批：

- B2/P2-4：点赞复活计数幂等 + 点赞/查词的唯一冲突兜底（原先并发双击 → 裸 500 / 双增不自愈）
- B3/P2-5：跨域直写登记制（检查器「自证是活的」+ 真实仓零未登记项）
- B4/P2-6：会员状态唯一写口（直赋判红 + 检查器活性）
- B5/N-2：进步榜取孩子必须**一条**批量 SQL（原先循环内逐人 SELECT）
- B6/P2-1：未建档手机号短信紧档限流（**不是**硬闸——登录兜底通道不能被堵死）
- B7/P2-2 残余：还书善后失败不得把"已还书"变成 5xx，且必须留审计
- B8：微信回写失败落死信（原先只一行 warning，运营看不见）
"""

from datetime import datetime, timedelta
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import event, text
from sqlalchemy.exc import IntegrityError

from backend.database import engine
from tests.unit.test_wm14_circle import _seed_book
from tests.unit.test_wm15_circle import (
    MILESTONE,
    _award_milestone,
    _db,
    _h,
    _mk_parent_with_child,
    _share,
)


def _cfg(db, key: str, value: str) -> None:
    db.execute(
        text("UPDATE system_configs SET config_value=:v WHERE config_key=:k"),
        {"v": value, "k": key},
    )
    db.commit()


# ---------------- B2/P2-4：点赞幂等与复活计数 ----------------


def test_like_twice_keeps_count_at_one(client: TestClient):
    """B2：同一孩子重复点赞**幂等**（计数不叠加）。"""
    from backend.domain.reading_circle.models import CirclePost

    h = _h(client)
    c1, m1, _ = _mk_parent_with_child(client, h, "13800001901", "被赞孩B", "OwnerB")
    c2, m2, _ = _mk_parent_with_child(client, h, "13800001902", "点赞孩B", "LikerB")
    post_id = _share(client, m1, c1, MILESTONE, _award_milestone(c1, 100000)).json()["post_id"]

    for _ in range(3):
        r = client.post(
            f"/api/miniapp/circle/posts/{post_id}/like", json={"child_id": c2}, headers=m2
        )
        assert r.status_code == 200, r.text
    with _db() as db:
        assert db.query(CirclePost.like_count).filter(CirclePost.id == post_id).scalar() == 1


def test_like_after_unlike_revives_with_single_increment(client: TestClient):
    """B2：点赞 → 取消 → 再赞 = **+1 不是 +2**（复活路径的条件 UPDATE 判据）。"""
    from backend.domain.reading_circle.models import CircleLike, CirclePost

    h = _h(client)
    c1, m1, _ = _mk_parent_with_child(client, h, "13800001903", "被赞孩C", "OwnerC")
    c2, m2, _ = _mk_parent_with_child(client, h, "13800001904", "点赞孩C", "LikerC")
    post_id = _share(client, m1, c1, MILESTONE, _award_milestone(c1, 100000)).json()["post_id"]

    assert (
        client.post(
            f"/api/miniapp/circle/posts/{post_id}/like", json={"child_id": c2}, headers=m2
        ).status_code
        == 200
    )
    assert (
        client.delete(
            f"/api/miniapp/circle/posts/{post_id}/like?child_id={c2}", headers=m2
        ).status_code
        == 200
    )
    assert (
        client.post(
            f"/api/miniapp/circle/posts/{post_id}/like", json={"child_id": c2}, headers=m2
        ).status_code
        == 200
    )
    with _db() as db:
        assert db.query(CirclePost.like_count).filter(CirclePost.id == post_id).scalar() == 1, (
            "复活只能 +1（RED＝复活路径读改写导致双增且不自愈）"
        )
        rows = (
            db.query(CircleLike)
            .filter(CircleLike.post_id == post_id, CircleLike.child_id == c2)
            .all()
        )
        assert len(rows) == 1 and rows[0].is_deleted == 0


def test_like_insert_conflict_returns_idempotent(client: TestClient):
    """B2：并发双击撞 `uq_circle_like` → **幂等返回**而不是裸 500（模拟 flush 抛 IntegrityError）。"""
    from backend.domain.identity.models import Child, Parent
    from backend.domain.reading_circle.models import CirclePost
    from backend.domain.reading_circle.service import CircleService

    h = _h(client)
    c1, m1, _ = _mk_parent_with_child(client, h, "13800001905", "被赞孩D", "OwnerD")
    c2, m2, _ = _mk_parent_with_child(client, h, "13800001906", "点赞孩D", "LikerD")
    post_id = _share(client, m1, c1, MILESTONE, _award_milestone(c1, 100000)).json()["post_id"]

    with _db() as db:
        post = db.query(CirclePost).filter(CirclePost.id == post_id).first()
        child = db.query(Child).filter(Child.id == c2).first()
        parent = db.query(Parent).filter(Parent.id == child.parent_id).first()
        assert post is not None and child is not None and parent is not None
        real_flush = db.flush

        def _boom(*a, **k):
            raise IntegrityError("INSERT", {}, Exception("uq_circle_like"))

        with patch.object(db, "flush", _boom):
            out = CircleService(db).like(parent, post_id, child)
        assert out["liked"] is True, out
        assert isinstance(out["like_count"], int)
        # 收尾：会话仍可用（回滚过），且**没有**多出点赞行
        db.flush = real_flush
        from backend.domain.reading_circle.models import CircleLike

        assert db.query(CircleLike).filter(CircleLike.post_id == post_id).count() == 0


def test_vocabulary_lookup_commit_conflict_is_idempotent(client: TestClient):
    """B2：查词收录撞 `uq_vocab_child_word` → 幂等返回（recorded=False），不 500。"""
    from backend.domain.identity.models import Child, Parent
    from backend.domain.reading.models import DictionaryWord
    from backend.domain.reading.vocabulary_service import VocabularyService

    h = _h(client)
    c1, m1, _ = _mk_parent_with_child(client, h, "13800001907", "查词孩", "VocabKid")
    with _db() as db:
        word = db.query(DictionaryWord).filter(DictionaryWord.is_deleted == 0).first()
        assert word is not None, "演示库应有词典行"
        child = db.query(Child).filter(Child.id == c1).first()
        parent = db.query(Parent).filter(Parent.id == child.parent_id).first()
        with patch.object(
            db, "commit", side_effect=IntegrityError("INSERT", {}, Exception("uq_vocab_child_word"))
        ):
            out = VocabularyService(db).lookup(parent, word.word, None)
        assert out["word"] == word.word
        assert out["recorded"] is False, "冲突时应按『已收录』返回而不是 500"


# ---------------- B5/N-2：进步榜 N+1 ----------------


def test_progress_board_uses_single_child_query(client: TestClient):
    """B5：进步榜取孩子信息 = **一条**批量 SQL（RED＝循环内逐人 `query(Child)`）。"""
    from backend.domain.growth.board_service import LeaderboardService
    from backend.domain.identity.models import Child

    h = _h(client)
    # 词账有 (child_id, book_id) 唯一索引 → 本周/上周各挂**不同书**
    book_now = _seed_book(client, h, "9788400001999", 500)
    book_last = _seed_book(client, h, "9788400001998", 50)

    this_monday = datetime.now() - timedelta(days=datetime.now().weekday())
    kids = []
    for i in range(3):
        cid, _mini, _pid = _mk_parent_with_child(
            client, h, f"1380000192{i}", f"进步孩{i}", f"Up{i}"
        )
        kids.append(cid)
        with _db() as db:
            from backend.domain.growth.models import WordsLedger

            db.add(
                WordsLedger(
                    child_id=cid, book_id=book_now, word_count=500, created_at=datetime.now()
                )
            )
            db.add(
                WordsLedger(
                    child_id=cid,
                    book_id=book_last,
                    word_count=50,
                    created_at=this_monday - timedelta(days=3),
                )
            )
            db.commit()

    sqls: list[str] = []

    def _before(conn, cursor, statement, params, context, executemany):  # noqa: ANN001
        sqls.append(" ".join(statement.split()))

    with _db() as db:  # viewer 先取好：自身那条 LIMIT 1 不进统计窗口
        viewer = db.query(Child).filter(Child.id == kids[0]).first()

    event.listen(engine, "before_cursor_execute", _before)
    try:
        with _db() as db:
            out = LeaderboardService(db).board(viewer, "progress")
    finally:
        event.remove(engine, "before_cursor_execute", _before)

    entries = out["entries"]
    assert len(entries) >= 3, f"夹具应造出 ≥3 人上榜，实得 {len(entries)}（不足则本判据空转）"
    # 只统计"按 id 取孩子"这一类（排除榜单自身的两条聚合 JOIN 查询）
    kid_queries = [q for q in sqls if "FROM children" in q and "JOIN" not in q]
    assert len(kid_queries) == 1, (
        f"取孩子必须只查一次批量 SQL（RED＝逐人 SELECT），实得 {len(kid_queries)} 条"
    )
    assert "IN (" in kid_queries[0], f"必须是批量 IN 查询，实得：{kid_queries[0][:160]}"


# ---------------- B6/P2-1：短信分档 ----------------


def test_sms_unregistered_phone_hits_tighter_cap(client: TestClient, db):
    """B6：未建档手机号按紧档日上限（默认 3；本用例压到 2 触发）。"""
    from backend.common.exceptions import ConflictError
    from backend.domain.identity.sms_service import SmsCodeService

    _cfg(db, "sms_send_interval_seconds", "0")  # 关掉间隔闸，本用例只验日上限档位
    _cfg(db, "sms_daily_limit", "10")
    _cfg(db, "sms_daily_limit_unregistered", "2")
    svc = SmsCodeService(db)
    phone = "13900000001"  # 演示库未建档
    sent, blocked = 0, 0
    for _ in range(4):
        try:
            svc.send(phone, client_ip="203.0.113.201")
            sent += 1
        except ConflictError as exc:
            blocked += 1
            assert "上限" in str(exc)
    assert sent == 2 and blocked == 2, f"未建档号应只放行 2 条（紧档），实得 sent={sent}"


def test_sms_registered_phone_uses_normal_cap(client: TestClient, db):
    """B6 反向样本：**已建档**家长照旧用普通档（紧档只针对未建档号）。"""
    from backend.common.exceptions import ConflictError
    from backend.domain.identity.sms_service import SmsCodeService

    h = _h(client)
    phone = "13900000002"
    _p, _mini, _pid = None, None, None
    client.post(
        "/api/admin/members/parents", json={"name": "已建档家长", "phone": phone}, headers=h
    )
    _cfg(db, "sms_send_interval_seconds", "0")
    _cfg(db, "sms_daily_limit", "10")
    _cfg(db, "sms_daily_limit_unregistered", "2")
    svc = SmsCodeService(db)
    sent = 0
    for _ in range(4):
        try:
            svc.send(phone, client_ip="203.0.113.202")
            sent += 1
        except ConflictError:
            break
    assert sent == 4, f"已建档号应按普通档放行 4 条，实得 {sent}"


# ---------------- B7/P2-2 残余：善后兜底 ----------------


def test_settle_orphans_safe_swallows_failure_and_audits(client: TestClient):
    """B7：善后自身失败**不抛出**（否则"书已还"会变成家长端 5xx），且留审计行。"""
    from backend.domain.admin.models import AdminUser, AuditLog
    from backend.domain.reading import reservation_ops

    with _db() as db:
        admin = db.query(AdminUser).filter(AdminUser.username == "admin").first()
        assert admin is not None
        with patch.object(
            reservation_ops,
            "settle_orphans_after_copy_gone",
            side_effect=RuntimeError("boom（模拟善后内部失败）"),
        ):
            reservation_ops.settle_orphans_after_copy_gone_safe(
                db, copy_id=999999, admin=admin, condition="lost"
            )  # 不得抛出
        row = (
            db.query(AuditLog)
            .filter(AuditLog.action == "circulation.reservation_settle_failed")
            .order_by(AuditLog.id.desc())
            .first()
        )
    assert row is not None, "善后失败必须留审计（否则运营无从发现孤儿预约）"
    assert row.target_id == "999999"


# ---------------- B8：微信回写失败 → 死信 ----------------


def test_wechat_writeback_failure_records_dead_letter():
    """B8：回写失败要**可见**（落 `dead_letters`），而不只是一行 warning。"""
    from backend.common.notification_models import DeadLetter
    from backend.common.notifications import _record_writeback_dead_letter

    marker = "13900000099"
    _record_writeback_dead_letter(int(marker), RuntimeError("writeback boom"))
    with _db() as db:
        row = (
            db.query(DeadLetter)
            .filter(DeadLetter.event_type == "notification.wechat_writeback")
            .filter(DeadLetter.payload.like(f"%{marker}%"))
            .first()
        )
    assert row is not None, "微信回写失败必须落死信（看板可见）"
    assert "writeback boom" in (row.error or "")


# ---------------- B3/B4：新检查的「自证是活的」 ----------------


def test_cross_domain_check_is_live():
    """B3：把跨域直写检查改成空实现 → 架构关自证必须报失败。"""
    from tests.unit.test_audit8_c_gate_selftest_20261020 import _load

    mod = _load("verify_architecture")
    original = mod.check_cross_domain_writes
    try:
        mod.check_cross_domain_writes = lambda errors: None
        rc = mod.self_test()
    finally:
        mod.check_cross_domain_writes = original
    assert rc == 1, "跨域直写检查被改坏后架构关自证必须失败"


def test_member_status_write_port_check_is_live():
    """B4：把会员状态写口检查改成空实现 → 架构关自证必须报失败。"""
    from tests.unit.test_audit8_c_gate_selftest_20261020 import _load

    mod = _load("verify_architecture")
    original = mod.check_member_status_write_port
    try:
        mod.check_member_status_write_port = lambda errors: None
        rc = mod.self_test()
    finally:
        mod.check_member_status_write_port = original
    assert rc == 1, "会员状态写口检查被改坏后架构关自证必须失败"


def test_cross_domain_whitelist_entries_all_match_real_code():
    """B3：白名单每条都要**真的命中**当前代码（防"登记了但已销项"的僵尸条目）。"""
    from tests.unit.test_audit8_c_gate_selftest_20261020 import _load

    mod = _load("verify_architecture")
    found = mod._scan_cross_domain_writes(mod.BACKEND)
    stale = sorted(set(mod.CROSS_DOMAIN_WRITE_WHITELIST) - found)
    assert not stale, f"白名单僵尸条目（已不再命中）：{stale}"
    assert len(mod.CROSS_DOMAIN_WRITE_WHITELIST) == 13, (
        f"登记数变化了（现 {len(mod.CROSS_DOMAIN_WRITE_WHITELIST)}），请同步本用例与 LEDGER"
    )


@pytest.mark.parametrize("unused", [None])
def test_member_status_has_no_direct_assignment(unused):
    """B4：全仓**零**直赋 `member_status`（判据与架构关同源；变更即同步两处）。"""
    from tests.unit.test_audit8_c_gate_selftest_20261020 import _load

    mod = _load("verify_architecture")
    errors: list[str] = []
    mod.check_member_status_write_port(errors)
    assert errors == []
