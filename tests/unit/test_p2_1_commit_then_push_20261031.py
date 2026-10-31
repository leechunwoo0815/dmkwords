# tests/unit/test_p2_1_commit_then_push_20261031.py — P2-1 通知「提交后推微信」判据
"""大审查 P2-1（docs/27）：send() 原先在 INSERT 后立即 `_wechat_push`，而调用方
commit() 在其后——主事务回滚/提交失败时，微信已告知「付款成功/退款到账」，
站内信与业务状态却回滚消失。

修法（docs/09 第一百五十批）：send() 只入队（session.info），提交成功后由
`after_commit` 监听器统一 dispatch；回滚由 `after_rollback` 丢弃。

判据（桩替换 push_subscribe_message 计数，不打真微信；生产开关本就关闭）：
① 回滚 → 推送 0 次 + 通知行不存在；② 提交 → 恰好 1 次且回滚后新提交不重放旧队列；
③ 推送抛异常 → 业务提交不受影响、状态如实落 failed；④ 初始状态条件写
（仅当 none）不覆盖微信回执终态；⑤ 队列里的死条目（行不存在）不推送。
"""

from __future__ import annotations

from backend.common import notifications as notif
from backend.common.notification_models import Notification
from backend.common.notifications import NotificationService
from backend.database import SessionLocal

# 无 FK 约束（notification_models.parent_id 是普通整型列），不需要真实家长行；
# 场景串唯一，避免与其他用例的去重键撞车；conftest 每测试 TRUNCATE notifications。
_PARENT_ID = 987654001
_SCENE_ROLLBACK = "p21.rollback"
_SCENE_ROLLBACK_LATER = "p21.rollback.later"
_SCENE_COMMIT = "p21.commit"
_SCENE_FAILURE = "p21.failure"
_SCENE_CONDITIONAL = "p21.conditional"
_SCENE_SAVEPOINT = "p21.savepoint"
_SCENE_SAVEPOINT_RB = "p21.savepoint.rb"
_SCENE_SAVEPOINT_GHOST = "p21.savepoint.ghost"


def _stub_push(monkeypatch, calls: list, *, queued: bool, reason: str, hint: str | None) -> None:
    """桩掉订阅模块的推送入口（记录调用参数；不打真微信）。"""

    def fake_push(openid, title, content, scene="", on_result=None):
        calls.append((openid, title, content, scene, on_result))
        return queued, reason, hint

    monkeypatch.setattr("backend.integrations.wechat.subscribe.push_subscribe_message", fake_push)


def _send(db, scene: str) -> bool:
    return NotificationService(db).send(
        parent_id=_PARENT_ID,
        scene=scene,
        title="付款成功",
        content="订单 A 已收款",
        category=Notification.CATEGORY_MONEY,
        openid="openid-stub",
    )


def _row(db, scene: str) -> Notification | None:
    return db.query(Notification).filter(Notification.scene == scene).first()


def test_rollback_never_pushes_and_later_commit_replays_nothing(monkeypatch):
    """① 事务回滚 → 推送 0 次、通知行不存在；同 session 后续提交不重放旧队列。"""
    calls: list = []
    _stub_push(monkeypatch, calls, queued=False, reason="通道未启用", hint="skipped")
    db = SessionLocal()
    try:
        assert _send(db, _SCENE_ROLLBACK) is True
        assert calls == [], "send() 本身不得触发推送（P2-1：提交后才推）"
        db.rollback()
        assert calls == [], "回滚后不得推送（P2-1 核心语义：回滚永不推送）"

        fresh = SessionLocal()
        try:
            assert _row(fresh, _SCENE_ROLLBACK) is None, "回滚的 INSERT 不应落库"
        finally:
            fresh.close()

        # 回滚后同一 session 再发一条并提交 → 只推新这条（旧队列已被 after_rollback 丢弃）
        assert _send(db, _SCENE_ROLLBACK_LATER) is True
        db.commit()
        assert len(calls) == 1, f"应只推送提交后的新通知，实际 {len(calls)} 次"
        assert calls[0][3] == _SCENE_ROLLBACK_LATER
    finally:
        db.close()


def test_commit_pushes_exactly_once_with_carry_through(monkeypatch):
    """② 提交成功 → 推送恰好 1 次，openid/scene 原样透传，回执回调链保留。"""
    calls: list = []
    _stub_push(monkeypatch, calls, queued=True, reason="已提交微信（模板 X）", hint=None)
    db = SessionLocal()
    try:
        assert _send(db, _SCENE_COMMIT) is True
        assert calls == []
        db.commit()
        assert len(calls) == 1, f"提交后应恰好推送 1 次，实际 {len(calls)} 次"
        openid, title, content, scene, on_result = calls[0]
        assert openid == "openid-stub"
        assert title == "付款成功" and content == "订单 A 已收款"
        assert scene == _SCENE_COMMIT
        assert on_result is not None, "回执回写链（on_result → _writeback）必须保留"

        # 再次 commit（无新通知）→ 不重复推送
        db.commit()
        assert len(calls) == 1

        row = _row(db, _SCENE_COMMIT)
        assert row is not None
        assert row.wechat_status == Notification.WECHAT_QUEUED, row.wechat_status
        assert row.wechat_error == "已提交微信，等待回执"
    finally:
        db.close()


def test_push_exception_does_not_break_business_commit(monkeypatch):
    """③ 推送抛异常 → commit 正常返回、通知行落库、状态如实记 failed + 真实原因。"""

    def boom(*args, **kwargs):
        raise RuntimeError("微信网关不可达")

    monkeypatch.setattr("backend.integrations.wechat.subscribe.push_subscribe_message", boom)
    db = SessionLocal()
    try:
        assert _send(db, _SCENE_FAILURE) is True
        db.commit()  # 不得向调用方抛错（站内消息已提交，微信是尽力通道）
        row = _row(db, _SCENE_FAILURE)
        assert row is not None, "推送异常不得影响通知行落库"
        assert row.wechat_status == Notification.WECHAT_FAILED, row.wechat_status
        assert "微信网关不可达" in (row.wechat_error or "")
    finally:
        db.close()


def test_initial_status_write_never_overwrites_receipt(monkeypatch):
    """④ 初始状态是条件写（仅当 none）：回执终态（sent/failed）永不被 queued/skipped 覆盖。"""
    calls: list = []
    _stub_push(monkeypatch, calls, queued=True, reason="已提交微信", hint=None)
    db = SessionLocal()
    try:
        assert _send(db, _SCENE_CONDITIONAL) is True
        db.commit()
        row = _row(db, _SCENE_CONDITIONAL)
        assert row is not None
        nid = row.id
    finally:
        db.close()

    # 微信回执先到：真实结果落终态 sent（走生产同款 _writeback）
    NotificationService._writeback(nid, True, "")

    # 迟到的 dispatch 初始写（queued）不得覆盖 sent
    db2 = SessionLocal()
    try:
        notif._set_wechat_if_untouched(db2, nid, Notification.WECHAT_QUEUED, "已提交微信，等待回执")
        db2.commit()
        row = _row(db2, _SCENE_CONDITIONAL)
        assert row is not None
        assert row.wechat_status == Notification.WECHAT_SENT, (
            f"初始写不得覆盖回执终态，实际 {row.wechat_status}"
        )
        assert not row.wechat_error
    finally:
        db2.close()


def test_dispatch_skips_rows_missing_from_db(monkeypatch):
    """⑤ 队列死条目（savepoint 回滚掉的行）→ 存在性校验拦截，不推送。"""
    calls: list = []
    _stub_push(monkeypatch, calls, queued=False, reason="通道未启用", hint="skipped")
    notif._dispatch_pending([(987654321, "幽灵通知", "内容", "openid-stub", "p21.ghost")])
    assert calls == [], "行不存在的条目不得推送（幽灵推送防护）"


def test_savepoint_commit_then_outer_commit_pushes_exactly_once(monkeypatch):
    """⑥ R-1（复审 docs/29）：SAVEPOINT 的**提交**同样派发 after_commit——监听器必须
    用 `in_nested_transaction()` 识别并保留队列；外层 commit 后恰好推 1 次。

    变异（去守卫）：nested commit 时提前 pop+dispatch → 行未提交不可见 → 命中
    "不存在就跳过"、队列清空 → 外层 commit 后 0 次 → 本用例红。
    """
    calls: list = []
    _stub_push(monkeypatch, calls, queued=True, reason="已提交微信", hint=None)
    db = SessionLocal()
    try:
        assert _send(db, _SCENE_SAVEPOINT) is True
        assert calls == []
        with db.begin_nested():
            pass  # SAVEPOINT 正常释放（R-1 场景：after_commit 在此派发一次）
        assert calls == [], "SAVEPOINT 提交不得触发推送（R-1 守卫缺失）"
        db.commit()
        assert len(calls) == 1, f"外层提交后应恰好推送 1 次，实际 {len(calls)} 次"
        row = _row(db, _SCENE_SAVEPOINT)
        assert row is not None and row.wechat_status == Notification.WECHAT_QUEUED
    finally:
        db.close()


def test_savepoint_rollback_keeps_surviving_entries(monkeypatch):
    """⑦ R-1 另一半：SAVEPOINT **回滚**同样派发 after_rollback（实测）——不得清队。
    savepoint 前入队的条目随外层提交正常推送（清队=误丢幸存通知）。"""
    calls: list = []
    _stub_push(monkeypatch, calls, queued=False, reason="通道未启用", hint="skipped")
    db = SessionLocal()
    try:
        assert _send(db, _SCENE_SAVEPOINT_RB) is True  # savepoint 之前入队
        try:
            with db.begin_nested():
                raise RuntimeError("savepoint 内业务失败")
        except RuntimeError:
            pass
        db.commit()
        assert len(calls) == 1, f"幸存条目应随外层提交推送 1 次，实际 {len(calls)} 次"
    finally:
        db.close()


def test_savepoint_rolled_back_entry_never_pushes(monkeypatch):
    """⑧ savepoint 内 send、savepoint 回滚 → 行已不存在；外层 commit 时存在性校验
    拦截，幽灵推送为 0（after_rollback 不清队 + dispatch 存在性校验配合）。"""
    calls: list = []
    _stub_push(monkeypatch, calls, queued=False, reason="通道未启用", hint="skipped")
    db = SessionLocal()
    try:
        try:
            with db.begin_nested():
                assert _send(db, _SCENE_SAVEPOINT_GHOST) is True
                raise RuntimeError("savepoint 内业务失败")
        except RuntimeError:
            pass
        db.commit()
        assert calls == [], "已随 savepoint 回滚的通知不得推送（幽灵推送防护）"
        fresh = SessionLocal()
        try:
            assert _row(fresh, _SCENE_SAVEPOINT_GHOST) is None
        finally:
            fresh.close()
    finally:
        db.close()
