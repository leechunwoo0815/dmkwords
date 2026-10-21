# tests/unit/test_audit8_d2_notify_pool_20261020.py — 第八轮大审查 D 批（下）P2-42
"""微信订阅推送：**并发有界**（线程池）+ **回写不再与未提交事务竞态**。

实测根因（不是静态推断）：通知行由**发起方事务**插入，而微信回执常在提交前就回来 →
对**未提交行**发 UPDATE，InnoDB **不是"匹配 0 行"，而是等锁**——探针实测
`1205 Lock wait timeout exceeded` 要等满 **50.15 秒**才返回（期间白占一条连接池连接），
回写线程随后只记一条 warning → `wechat_status` **永久停在 `queued`**。
千条提醒 = 千个这样的线程 + 千条连接（原先**每条通知一个 daemon 线程，无上限**）。

修法：① 推送走**有界线程池**（`PUSH_WORKERS` 个 worker + `PUSH_QUEUE_MAX` 积压上限，
满了如实记 `failed` + 真实原因）；② 回写改**轮询可见性**（快照读不阻塞、不加锁，
每次轮询后把连接还回池里），行提交后再 UPDATE。

RED（变异自证，三条都实测过）：
- `_writeback` 改回"直接 UPDATE" → 用例 1 的连接占用断言红（回写全程占着连接）；
- 改回 `threading.Thread(...).start()` → 用例 3 的并发上界断言红（40 并发 > 8）；
- 去掉积压上限判定 → 用例 4 红（队列已满仍报"已入队"）。
"""

from __future__ import annotations

import logging
import threading
import time

from fastapi.testclient import TestClient

SCENE_DUE = "borrow.due_remind"  # 已映射模板的高价值场景（订阅推送真路径）


def _enable_channel(monkeypatch, enabled: bool = True) -> None:
    """打开/关闭订阅总开关（`precheck_subscribe` 读的就是这个单例）。"""
    from backend.config import get_settings

    monkeypatch.setattr(get_settings(), "WECHAT_SUBSCRIBE_ENABLED", enabled, raising=False)


def _template(monkeypatch, value: str = "tmpl-test") -> None:
    """给"还书提醒"场景配一个模板 ID（否则 precheck 停在"模板未配置"）。"""
    from backend.integrations.wechat import subscribe as sub

    monkeypatch.setattr(sub.SubscribeTemplate, "RETURN_REMIND", value, raising=False)


# ---------- ① 回写与未提交事务：等可见，但不占连接 ----------


def test_writeback_waits_for_visibility_without_holding_connection(client: TestClient):
    """行未提交时：回写**不许**占着连接等锁；提交后必须在数秒内落到 sent。

    RED（变异）：把 `_writeback` 改回"直接 UPDATE" → 该语句会在未提交行上等锁 50 秒，
    采样期间连接一直被占（`blocked` 逼近 20/20）→ 本用例红。
    """
    from backend.common.notification_models import Notification
    from backend.common.notifications import NotificationService
    from backend.database import engine
    from tests.unit.test_wm10_concurrency import _db, _family, _h

    _p, c, _mini = _family(client, _h(client), "13800008791")
    db = _db()
    try:
        assert NotificationService(db).send(
            parent_id=c["parent_id"],
            scene=SCENE_DUE,
            title="借阅到期提醒",
            content="《测试》将于 3 天后到期",
            category=Notification.CATEGORY_BORROW,
            child_id=c["id"],
            openid="openid-stub",
        ), "首次写入应成功"
        nid = (
            db.query(Notification.id).filter(Notification.scene == SCENE_DUE).scalar()
        )  # 同事务可见（未提交）
        assert nid, "同事务内应能读到刚插入的行"

        baseline = engine.pool.checkedout()
        worker = threading.Thread(
            target=NotificationService._writeback, args=(nid, True, ""), daemon=True
        )
        worker.start()

        blocked = 0  # 采样：有多少次采样时"连接被多占了一条"
        for _ in range(20):
            time.sleep(0.1)
            if engine.pool.checkedout() > baseline:
                blocked += 1
        assert worker.is_alive(), "回写应仍在等行可见（而不是已异常退出）"
        assert blocked <= 8, (
            f"回写在等可见期间不应占着连接（20 次采样里 {blocked} 次多占 1 条）——"
            "RED=直接 UPDATE 未提交行 → InnoDB 等锁 50 秒，白占连接池"
        )

        db.commit()  # 事务提交 → 行可见
        worker.join(timeout=10)
        assert not worker.is_alive(), "行提交后回写应在数秒内完成（不是卡在 50 秒等锁）"
        db.expire_all()
        row = db.query(Notification).filter(Notification.id == nid).first()
        assert row.wechat_status == Notification.WECHAT_SENT, (
            f"回写必须落到 sent（修前永久停在 queued）：{row.wechat_status}"
        )
    finally:
        db.close()


def test_writeback_gives_up_honestly_when_row_never_committed(monkeypatch, caplog):
    """行始终不提交（事务回滚）→ 到点**如实放弃**并留痕，不许无限等、不许静默。"""
    from backend.common import notifications as notif

    monkeypatch.setattr(notif, "WRITEBACK_VISIBLE_TIMEOUT_SECONDS", 0.6)
    monkeypatch.setattr(notif, "WRITEBACK_POLL_SECONDS", 0.1)
    with caplog.at_level(logging.WARNING, logger="backend.common.notifications"):
        started = time.monotonic()
        notif.NotificationService._writeback(10**9, True, "")  # 不存在的行
        elapsed = time.monotonic() - started

    assert elapsed < 5, f"超时必须如实放弃（不许无限等）：{elapsed:.1f}s"
    assert any("未提交" in str(r.message) for r in caplog.records), (
        f"超时要留下可定位的告警（不是静默返回）：{[r.message for r in caplog.records]}"
    )


# ---------- ② 推送并发有界 ----------


def test_push_threads_bounded_by_pool_workers(monkeypatch):
    """40 条同时推送 → **并发恰好等于 worker 数**（不是 40 个线程）。

    RED（变异）：改回 `threading.Thread(target=..., daemon=True).start()` →
    40 条各自起线程 → `live["max"]` 变 40 → 本用例红。
    """
    from backend.integrations.wechat import subscribe as sub

    _enable_channel(monkeypatch)
    _template(monkeypatch)

    gate = threading.Event()
    lock = threading.Lock()
    live = {"n": 0, "max": 0, "done": 0}

    async def fake_send(openid: str, template_id: str, data: dict, page: str = "") -> dict:
        with lock:
            live["n"] += 1
            live["max"] = max(live["max"], live["n"])
        gate.wait(timeout=20)  # 卡住：让并发数停在峰值上可观测
        with lock:
            live["n"] -= 1
            live["done"] += 1
        return {"errcode": 0}

    monkeypatch.setattr(sub.WeChatSubscribe, "send", staticmethod(fake_send))

    total = 40
    try:
        queued = [
            sub.push_subscribe_message(f"openid-{i}", "借阅到期提醒", "内容", SCENE_DUE)[0]
            for i in range(total)
        ]
        assert all(queued), f"40 条都应入队（积压上限 {sub.PUSH_QUEUE_MAX}）：{queued}"
        deadline = time.monotonic() + 15
        while time.monotonic() < deadline and live["max"] < sub.PUSH_WORKERS:
            time.sleep(0.05)
        assert live["max"] == sub.PUSH_WORKERS, (
            f"并发峰值应恰好等于 worker 数 {sub.PUSH_WORKERS}（实 {live['max']}）"
        )
    finally:
        gate.set()  # 无论断言成败都要放行，别把 worker 卡死影响后续用例
    deadline = time.monotonic() + 30
    while time.monotonic() < deadline and live["done"] < total:
        time.sleep(0.05)
    assert live["done"] == total, (
        f"全部任务都应跑完（worker 里必须释放积压名额，否则队列越用越小）：{live}"
    )
    assert live["max"] <= sub.PUSH_WORKERS, f"并发上界被突破：{live}"


def test_queue_overflow_records_failed_with_real_reason(monkeypatch):
    """积压满 → `failed` + **真实原因**（既不静默丢弃，也不伪装成"已入队/已跳过"）。

    RED（变异）：去掉 `_PUSH_BACKLOG.acquire` 判定 → 仍返回 True → 本用例红。
    """
    from backend.integrations.wechat import subscribe as sub

    _enable_channel(monkeypatch)
    _template(monkeypatch)

    full = threading.BoundedSemaphore(1)
    full.acquire()
    monkeypatch.setattr(sub, "_PUSH_BACKLOG", full)  # 队列已满（不真占 512 个名额）

    queued, reason, hint = sub.push_subscribe_message("openid-x", "借阅到期提醒", "内容", SCENE_DUE)
    assert queued is False, "队列已满不得报'已入队'"
    assert hint == "failed", f"队列满是**发送失败**（不是主动跳过）：{hint}"
    assert "队列已满" in reason and str(sub.PUSH_QUEUE_MAX) in reason, reason


# ---------- ③ 状态与原因只有一处口径 ----------


def test_skip_status_and_reason_come_from_shared_precheck(client: TestClient, monkeypatch):
    """落库的状态/原因必须**逐字等于** `precheck_subscribe` 的返回（单一事实源）。

    修前 `NotificationService._wechat_push` 自己另抄了一份"通道未启用/无 openid"判定
    （同一口径两处实现）。现在两边同源：本用例比对的就是"两边确实同源"这一行为事实。
    """
    from backend.common.notification_models import Notification
    from backend.common.notifications import NotificationService
    from backend.integrations.wechat.subscribe import precheck_subscribe
    from tests.unit.test_wm10_concurrency import _db, _family, _h

    _enable_channel(monkeypatch, False)
    ok, reason, hint = precheck_subscribe("openid-stub", SCENE_DUE)
    assert (ok, hint) == (False, "skipped"), (ok, reason, hint)

    _p, c, _mini = _family(client, _h(client), "13800008792")
    db = _db()
    try:
        NotificationService(db).send(
            parent_id=c["parent_id"],
            scene=SCENE_DUE,
            title="借阅到期提醒",
            content="内容",
            category=Notification.CATEGORY_BORROW,
            child_id=c["id"],
            openid="openid-stub",
        )
        db.commit()
        row = db.query(Notification).filter(Notification.scene == SCENE_DUE).first()
        assert row is not None
        assert row.wechat_status == hint, f"状态应取自共享判定（{hint}），实 {row.wechat_status}"
        assert row.wechat_error == reason, (
            f"原因应取自共享判定（{reason!r}），实 {row.wechat_error!r}——"
            "RED=调用方另抄一份判定，改一处漏一处"
        )
    finally:
        db.close()
