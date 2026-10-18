# tests/unit/test_audit4_p2_batch3_20261017.py — 第四轮大审查·P2 批量（子批 3）回归，2026-10-17
"""第六十九批（子批 3）：P2-4 活动退款关闸单一来源 · P2-5 90 天催办看会员态 · P2-9 续听卡借阅行
                        定时 · P2-10 收藏夹软删书目 · P2-11 checkins 入参上界 · P2-13 待评估时钟 · P2-15 书架角标解锁口径 ·
P2-24 微信推送状态可信。

每条用例都是报告里那条缺陷的复现或判据的固化，逐条**去修复即红**（变异自证见 docs/09 第六十九轮）。
"""

from datetime import date, datetime, timedelta
from decimal import Decimal

from fastapi.testclient import TestClient

from tests.unit.test_wm10_concurrency import _family, _h


def _member_child(client, h, phone: str, name: str = "孩"):
    """建家庭 + 缴观察期费入会（观察期态）。"""
    _p, c, mini = _family(client, h, phone, name=name)
    o = client.post(
        "/api/admin/orders",
        json={"child_id": c["id"], "order_type": "observation_fee"},
        headers=h,
    ).json()
    client.post(
        f"/api/admin/orders/{o['id']}/confirm-payment", json={"pay_method": "scan"}, headers=h
    )
    # 借书链路要求"会员 + 押金已缴"（P1 口径），回归续听卡/借阅的行也必须先缴押金
    d = client.post(f"/api/admin/deposits/children/{c['id']}/orders", headers=h).json()
    client.post(
        f"/api/admin/orders/{d['order_id']}/confirm-payment",
        json={"pay_method": "scan"},
        headers=h,
    )
    return c, mini


def _book(client, h, title: str, copies: int = 2) -> int:
    from tests.unit.helpers import force_book_on

    r = client.post(
        "/api/admin/books",
        json={"isbn": None, "title": title, "word_count": 200, "copy_count": copies},
        headers=h,
    )
    book_id = r.json()["id"]
    force_book_on(client, h, book_id)
    return book_id


# ---------- P2-4：活动退款关闸（开始前 N 小时关线上退款）两个入口同一判据 ----------


def _activity(client, h, hours_later: float, fee: int = 50, title: str = "关闸活动"):
    r = client.post(
        "/api/admin/activities",
        json={
            "title": title,
            "activity_type": "book_club",
            "start_at": (datetime.now() + timedelta(hours=hours_later)).isoformat(),
            "location": "馆内一层",
            "max_quota": 5,
            "fee": fee,
            "description": "关闸回归",
            "member_only": False,
        },
        headers=h,
    )
    assert r.status_code == 200, r.text
    return r.json()


def _enrolled_paid(client, h, mini, child_id: int, activity_id: int) -> dict:
    e = client.post(
        f"/api/miniapp/activities/{activity_id}/enroll",
        json={"child_id": child_id},
        headers=mini,
    ).json()
    assert e["order_id"], e
    client.post(
        f"/api/admin/orders/{e['order_id']}/confirm-payment", json={"pay_method": "scan"}, headers=h
    )
    return e


def test_activity_refund_cutoff_blocks_generic_entry(client: TestClient):
    """通用订单退款入口（「我的订单」）也必须过"开始前 2 小时关闸"。

    RED（还原修复：判据只写在活动专属入口）：`POST /api/miniapp/refund-requests` 会 **200 建单**
    ——同一笔退款两个入口两套规则（活动开始前 1 小时仍可自助退）。
    """
    h = _h(client)
    c, mini = _member_child(client, h, "13800008701")
    act_soon = _activity(client, h, hours_later=1, title="一小时后开始")
    e = _enrolled_paid(client, h, mini, c["id"], act_soon["id"])

    r = client.post(
        "/api/miniapp/refund-requests",
        json={"child_id": c["id"], "order_id": e["order_id"], "reason": "关闸回归"},
        headers=mini,
    )
    assert r.status_code == 422, f"关闸内不许建退款单：{r.status_code} {r.text}"
    assert "线上退款已关闭" in r.json()["detail"]

    # 活动专属入口同样被拦（判据同源；文案的小时数取配置值）
    r2 = client.post(
        f"/api/miniapp/enrollments/{e['enrollment']['id']}/refund-apply",
        json={"child_id": c["id"]},
        headers=mini,
    )
    assert r2.status_code == 422 and "线上退款已关闭" in r2.json()["detail"]


def test_activity_refund_outside_cutoff_still_works(client: TestClient):
    """未触碰关闸（3 天后开始）→ 通用入口照常受理（不收窄过头）。"""
    h = _h(client)
    c, mini = _member_child(client, h, "13800008702")
    act_later = _activity(client, h, hours_later=72, title="三天后开始")
    e = _enrolled_paid(client, h, mini, c["id"], act_later["id"])
    r = client.post(
        "/api/miniapp/refund-requests",
        json={"child_id": c["id"], "order_id": e["order_id"], "reason": "提前取消"},
        headers=mini,
    )
    assert r.status_code == 200, r.text


# ---------- P2-5：99 元 90 天催办必须看会员态 ----------


def test_first_activity_90d_remind_skips_members(client: TestClient):
    """已入会的家庭不再被催「可办理正式会员」（原先只看 99 元单付满 90 天）。"""
    from backend.database import get_session
    from tests.unit.test_wm10_concurrency import _db

    h = _h(client)
    # ① 只买 99 元首场、未入会 → 应被催
    _p1, c_open, _m1 = _family(client, h, "13800008711")
    # ② 买到 99 元首场后**又入了会** → 不该被催
    c_member, _m2 = _member_child(client, h, "13800008712")
    db = _db()
    try:
        from backend.domain.identity.models import Child as _Child
        from backend.domain.identity.models import Order

        for child_id in (c_open["id"], c_member["id"]):
            child = db.query(_Child).filter(_Child.id == child_id).first()
            db.add(
                Order(
                    order_no=f"REG-P2-5-{child_id}",
                    child_id=child_id,
                    parent_id=child.parent_id,
                    order_type=Order.TYPE_FIRST_ACTIVITY,
                    amount=Decimal("99.00"),
                    status=Order.STATUS_PAID,
                    pay_method="scan",
                    paid_at=datetime.now() - timedelta(days=100),
                    remark="P2-5 回归：99 元首单（100 天前）",
                )
            )
        db.commit()
    finally:
        db.close()

    from backend.tasks.registry import run_task

    run_task("first_activity_90d_remind")
    db = get_session()
    try:
        from backend.common.notification_models import Notification

        rows = (
            db.query(Notification)
            .filter(
                Notification.scene == "member.expire_remind",
                Notification.child_id.in_([c_open["id"], c_member["id"]]),
            )
            .all()
        )
        by_child = {r.child_id: r for r in rows}
    finally:
        db.close()
    assert c_open["id"] in by_child, "未入会的家庭应当收到转年费提醒"
    assert c_member["id"] not in by_child, "已入会的家庭不该被催「可办理正式会员」"


# ---------- P2-9：续听卡取"在借中"那一行借阅记录 ----------


def test_continue_listening_prefers_active_borrow_row(client: TestClient):
    """还了再借：续听卡必须带 due_at（原先可能取到 `returned` 那一行 → 退化成"可续听"）。"""
    from tests.unit.test_wm10_concurrency import _db

    h = _h(client)
    c, mini = _member_child(client, h, "13800008721")
    book_id = _book(client, h, "续听书", copies=2)
    copies = client.get(f"/api/admin/books/{book_id}/copies", headers=h).json()
    assert len(copies) == 2, copies
    cp1, cp2 = copies[0]["id"], copies[1]["id"]
    # 上进度要求书有音频（报告进度链路的硬前置）——直接落库设时长，省掉一次伪音频上传
    db = _db()
    try:
        from backend.domain.catalog.models import Book as _Book

        row = db.query(_Book).filter(_Book.id == book_id).first()
        row.audio_path = "book_audio/regression.mp3"
        row.audio_duration_seconds = 600
        db.commit()
    finally:
        db.close()

    # ① 借第一册 → 上进度（未读完）→ 还书（留一条 returned 借阅记录）
    r1 = client.post(
        "/api/admin/circulation/borrow",
        json={"child_id": c["id"], "copy_id": cp1},
        headers=h,
    )
    assert r1.status_code == 200, r1.text
    prog = client.post(
        "/api/miniapp/reading/progress",
        json={"child_id": c["id"], "book_id": book_id, "position": 10, "session_start": 0},
        headers=mini,
    )
    assert prog.status_code == 200, prog.text
    ret = client.post(
        "/api/admin/circulation/return",
        json={"copy_id": cp1, "condition": "normal"},
        headers=h,
    )
    assert ret.status_code == 200, ret.text

    # ② 再借第二册（在借中）——同一本书、同一孩子，两条借阅记录
    r2 = client.post(
        "/api/admin/circulation/borrow",
        json={"child_id": c["id"], "copy_id": cp2},
        headers=h,
    )
    assert r2.status_code == 200, r2.text

    card = client.get(f"/api/miniapp/continue-listening?child_id={c['id']}", headers=mini)
    assert card.status_code == 200, card.text
    body = card.json()
    assert body, "应有续听卡"
    # 接口只下发 {book, percent, last_position, due_at}——`due_at` 非空即"取到了在借那一行"
    # （若取到 returned 那行，in_borrow=False → due_at 恒为 None，正是 P2-9 的症状）
    assert body.get("due_at"), f"在借必须带到期日（原先进取到 returned 行会退化成可续听）：{body}"


# ---------- P2-10：收藏夹不展示已软删书目 ----------


def test_favorites_hide_soft_deleted_book(client: TestClient):
    h = _h(client)
    c, mini = _member_child(client, h, "13800008731")
    book_id = _book(client, h, "将被软删的书")
    r = client.post(
        "/api/miniapp/favorites", json={"child_id": c["id"], "book_id": book_id}, headers=mini
    )
    assert r.status_code == 200, r.text
    assert len(client.get(f"/api/miniapp/favorites?child_id={c['id']}", headers=mini).json()) == 1

    assert client.delete(f"/api/admin/books/{book_id}", headers=h).status_code == 200
    left = client.get(f"/api/miniapp/favorites?child_id={c['id']}", headers=mini).json()
    assert left == [], f"软删书目不该留在收藏夹（点进去 404）：{left}"


# ---------- P2-11：checkins 入参上下界 ----------


def test_checkins_days_bounds(client: TestClient):
    h = _h(client)
    c, mini = _member_child(client, h, "13800008741")
    assert (
        client.get(f"/api/miniapp/checkins?child_id={c['id']}&days=-1", headers=mini).status_code
        == 422
    )
    assert (
        client.get(
            f"/api/miniapp/checkins?child_id={c['id']}&days=1000000000", headers=mini
        ).status_code
        == 422
    )
    ok = client.get(f"/api/miniapp/checkins?child_id={c['id']}&days=30", headers=mini)
    assert ok.status_code == 200, ok.text


# ---------- P2-13：待评估时钟 = 进入待评估的时刻（不随无关字段更新归零） ----------


def test_pending_evaluation_clock_survives_unrelated_update(client: TestClient):
    """家长自助换头像（"任意字段更新"）不许把待评估时钟归零。

    RED（还原修复：判据挂 `update_time`）：换完头像 update_time=now → 该孩子从周名单消失。
    """
    from tests.unit.test_wm10_concurrency import _db

    h = _h(client)
    c, mini = _member_child(client, h, "13800008751")
    db = _db()
    try:
        from backend.domain.identity.models import Child

        row = db.query(Child).filter(Child.id == c["id"]).first()
        row.member_expire = date.today() - timedelta(days=1)  # 观察期已过
        db.commit()
    finally:
        db.close()

    from backend.tasks.registry import run_task

    run_task("member_expire_check")
    db = _db()
    try:
        from backend.domain.identity.models import Child

        row = db.query(Child).filter(Child.id == c["id"]).first()
        db.refresh(row)
        assert row.member_status == Child.MEMBER_PENDING_EVALUATION
        assert row.pending_evaluation_at is not None, "进入待评估必须记时刻"
        # 把时钟拨到 10 天前（模拟"已经待评估 10 天"）
        row.pending_evaluation_at = datetime.now() - timedelta(days=10)
        db.commit()
    finally:
        db.close()

    # 家长自助换头像（无关字段更新 → update_time 被刷新）
    avatar = client.get("/api/miniapp/avatars", headers=mini)
    if avatar.status_code == 200 and avatar.json():
        first = avatar.json()[0]
        value = first if isinstance(first, str) else first.get("id") or first.get("value")
        r = client.put(
            f"/api/miniapp/children/{c['id']}/avatar", json={"avatar": value}, headers=mini
        )
        assert r.status_code == 200, r.text

    client.put(
        "/api/admin/configs/pending_evaluation_remind_days",
        json={"value": "1", "reason": "P2-13 回归"},
        headers=h,
    )
    try:
        run_task("pending_evaluation_weekly")
        db = _db()
        try:
            from backend.common.admin_notification_models import AdminNotification

            rows = (
                db.query(AdminNotification)
                .filter(
                    AdminNotification.scene == AdminNotification.SCENE_PENDING_EVALUATION,
                    AdminNotification.ref_id == str(c["id"]),
                )
                .all()
            )
        finally:
            db.close()
        assert rows, "换头像不该把待评估孩子从周名单里抹掉（时钟挂 update_time 的旧症状）"
    finally:
        client.put(
            "/api/admin/configs/pending_evaluation_remind_days",
            json={"value": "7", "reason": "P2-13 回归复原"},
            headers=h,
        )


# ---------- P2-15：书架角标解锁判据与详情页一致（有词账即已解锁） ----------


def test_quiz_batch_unlocked_when_words_ledger_exists(client: TestClient):
    """有词账、无完播行的书：角标必须 `passed`（原先 `locked`，与详情页 PASSED 自相矛盾）。"""
    from tests.unit.test_wm10_concurrency import _db

    h = _h(client)
    c, _mini = _member_child(client, h, "13800008761")
    book_id = _book(client, h, "只有词账的书")
    db = _db()
    try:
        from backend.domain.growth.models import WordsLedger

        db.add(
            WordsLedger(
                child_id=c["id"],
                book_id=book_id,
                word_count=200,
                source="quiz_pass",
                is_deleted=0,
            )
        )
        db.commit()
    finally:
        db.close()
    # 词账已存在（passed 判据）；无 ReadingProgress 完播行（旧判据会判 locked）
    r = client.get(
        f"/api/miniapp/quiz/status-batch?child_id={c['id']}&book_ids={book_id}", headers=_mini
    )
    assert r.status_code == 200, r.text
    item = r.json()["items"][0]
    assert item["status"] == "passed", f"有词账即已解锁（与 get_quiz 同口径）：{item}"


# ---------- P2-24：微信推送状态可信（已提交≠已送达；跳过原因如实） ----------


def test_wechat_status_skipped_with_real_reason(client: TestClient, monkeypatch):
    """标题未命中模板映射 → `skipped` + 真实原因（原先写 `failed` + 假原因"额度不足/用户拒收"）。"""
    from backend.common import notifications as notif
    from backend.common.notification_models import Notification
    from tests.unit.test_wm10_concurrency import _db

    settings = notif.get_settings()
    monkeypatch.setattr(settings, "WECHAT_SUBSCRIBE_ENABLED", True, raising=False)

    def fake_push(openid, title, content, scene="", on_result=None):
        return False, "标题未命中订阅模板映射（随便一个标题）——仅站内消息"

    monkeypatch.setattr("backend.integrations.wechat.subscribe.push_subscribe_message", fake_push)

    _p, c, _mini = _family(client, _h(client), "13800008771")
    db = _db()
    try:
        svc = notif.NotificationService(db)
        svc.send(
            parent_id=c["parent_id"],
            scene="regression.p2_24",
            title="随便一个标题",
            content="内容",
            category=Notification.CATEGORY_OTHER,
            openid="openid-stub",
        )
        db.commit()
        row = (
            db.query(Notification)
            .filter(Notification.scene == "regression.p2_24")
            .order_by(Notification.id.desc())
            .first()
        )
        assert row is not None
        assert row.wechat_status == Notification.WECHAT_SKIPPED, row.wechat_status
        assert "未命中订阅模板映射" in (row.wechat_error or "")
        assert "额度不足" not in (row.wechat_error or ""), "不许再写假原因"
    finally:
        db.close()


def test_wechat_status_queued_then_written_back(client: TestClient, monkeypatch):
    """入队 ≠ 送达：先 `queued`，拿到回执后回写 sent/failed。"""
    from backend.common import notifications as notif
    from backend.common.notification_models import Notification
    from tests.unit.test_wm10_concurrency import _db

    settings = notif.get_settings()
    monkeypatch.setattr(settings, "WECHAT_SUBSCRIBE_ENABLED", True, raising=False)
    captured: dict = {}

    def fake_push(openid, title, content, scene="", on_result=None):
        captured["on_result"] = on_result
        return True, "已提交微信（模板 MEMBER_EXPIRE_REMIND）"

    monkeypatch.setattr("backend.integrations.wechat.subscribe.push_subscribe_message", fake_push)

    _p, c, _mini = _family(client, _h(client), "13800008772")
    db = _db()
    try:
        notif.NotificationService(db).send(
            parent_id=c["parent_id"],
            scene="regression.p2_24b",
            title="会员续费提醒",
            content="内容",
            category=Notification.CATEGORY_MEMBER,
            openid="openid-stub",
        )
        db.commit()
        row = (
            db.query(Notification)
            .filter(Notification.scene == "regression.p2_24b")
            .order_by(Notification.id.desc())
            .first()
        )
        assert row.wechat_status == Notification.WECHAT_QUEUED, "入队只能写 queued（不是 sent）"
        nid = row.id
    finally:
        db.close()

    # 后台线程拿到真实结果后回写（这里直接调回调，确定性）
    assert captured.get("on_result") is not None
    captured["on_result"](False, "微信订阅发送失败: 额度不足")
    db = _db()
    try:
        row = db.query(Notification).filter(Notification.id == nid).first()
        db.refresh(row)
        assert row.wechat_status == Notification.WECHAT_FAILED
        assert "额度不足" in (row.wechat_error or "")
    finally:
        db.close()

    captured["on_result"](True, "")
    db = _db()
    try:
        row = db.query(Notification).filter(Notification.id == nid).first()
        db.refresh(row)
        assert row.wechat_status == Notification.WECHAT_SENT and not row.wechat_error
    finally:
        db.close()
