# tests/unit/test_p2_audit_20261016.py — 第三轮大审查 P2 批次（2026-10-16，第六十三轮）
"""逐条对应 `docs/审查报告-全量-2026-10-15.md` §二（P2）与本轮派生的 P1 #9：

- **#9（P1）** `GET /api/miniapp/transfers/conditions` 无归属校验 → 可枚举他人 child_id
- **#15** 扫码还书硬编码 `condition="normal"` → 静默清掉刚登记的维护/遗失标记
- **#16** `override_reason` 只判真值不 strip → 全空格串可绕"必须填原因"的闸（留痕空白）
- **#17** `borrow()` 关预约收尾是快照读 + 无条件覆盖写 → 可把并发已取消的预约覆盖回 checked_out
- **#18** 到期提醒补发窗口重叠 → 当天到期的记录同时命中"0 天"与"1 天"两个节点，发两条
- **#19** 积分人工调整允许负数（与 PRD §7.2「不扣/非负」矛盾）
- **#22** 退款回调找不到退款单时先解引用 None → 404 分支是死代码、异常被 200 兜底吞掉
- **#23** 生产自检两处静默放行（SECRET_KEY 只比默认字面量；APP_ENV 拼错 + DEBUG 整段跳过）
- **#24** 阅读圈 `circle/posts` 的 child_id 无归属校验 → 可探测他人孩子 liked_by_me
- **#25** 转让撤销 / 家长删帖的越权与不存在可区分（违反铁律 128）
- **#26** `/observation-images` 裸 `startswith` → 改用项目单一口径 `is_within`
- **#28** conftest 清理清单漏 `payment_reconciliations`

每条修复都做「去修复即红」的变异自证（红线 147）。
"""

from __future__ import annotations

from datetime import date, datetime, timedelta

from fastapi.testclient import TestClient
from sqlalchemy import text

from tests.unit.test_p1_audit_20261015 import _db, _family


def _book(client, h, title, copies=1):
    from tests.unit.helpers import force_book_on

    r = client.post(
        "/api/admin/books",
        json={"isbn": None, "title": title, "word_count": 100, "copy_count": copies},
        headers=h,
    )
    assert r.status_code == 200, r.text
    book_id = r.json()["id"]
    force_book_on(client, h, book_id)
    return book_id, [
        c["id"] for c in client.get(f"/api/admin/books/{book_id}/copies", headers=h).json()
    ]


def _pay_deposit(client, h, child_id: int) -> None:
    """缴押金（借书闸要求押金在账；`_family` 只到观察期会员）。"""
    do = client.post(f"/api/admin/deposits/children/{child_id}/orders", headers=h)
    assert do.status_code == 200, do.text
    r = client.post(
        f"/api/admin/orders/{do.json()['order_id']}/confirm-payment",
        json={"pay_method": "scan"},
        headers=h,
    )
    assert r.status_code == 200, r.text


def _set_isbn(book_id: int, isbn: str) -> None:
    from backend.domain.catalog.models import Book

    with _db() as db:
        db.query(Book).filter(Book.id == book_id).update({"isbn": isbn})
        db.commit()


# ---------- #9 transfers/conditions 归属校验 ----------


def test_transfer_conditions_requires_ownership(client: TestClient):
    """别人的 child_id → 与"不存在"同回应（422「孩子不存在」），不再回条件清单。

    RED（还原修复：直接 `svc._child(任意 id)`）：返回 200 + 条件清单 → 会员态/会期/在借预约/
    未结清赔偿/冻结态对任何登录家长可见（枚举 child_id 即可逐个读）。
    """
    h = _h = {"Authorization": ""}
    r = client.post("/api/admin/login", json={"username": "admin", "password": "dmkwords123"})
    h = {"Authorization": f"Bearer {r.json()['token']}"}
    _p1, c1, _mini1 = _family(client, h, "13981600001", "甲家孩")
    _p2, c2, mini2 = _family(client, h, "13981600002", "乙家孩")

    own = client.get(
        f"/api/miniapp/transfers/conditions?source_child_id={c2['id']}&target_child_id={c2['id']}",
        headers=mini2,
    )
    assert own.status_code == 200, own.text
    assert "conditions" in own.json()

    foreign = client.get(
        f"/api/miniapp/transfers/conditions?source_child_id={c1['id']}&target_child_id={c2['id']}",
        headers=mini2,
    )
    assert foreign.status_code in (404, 422), (
        f"越权读他人孩子必须被拒，实 {foreign.status_code} {foreign.text[:120]}"
    )
    ghost = client.get(
        "/api/miniapp/transfers/conditions?source_child_id=999999&target_child_id=999999",
        headers=mini2,
    )
    assert ghost.status_code == foreign.status_code, "越权与不存在必须同回应（铁律 128）"
    assert ghost.json()["detail"] == foreign.json()["detail"], (ghost.json(), foreign.json())


# ---------- #15 扫码还书不得静默清掉维护/遗失标记 ----------


def test_scan_return_keeps_damage_mark(client: TestClient):
    """副本已报损（maintenance）后扫码还书 → 副本仍是 maintenance，且**不重复**写赔偿待办。

    RED（还原修复：`return_book(..., "normal")`）：副本被改回 available（标记静默消失）。
    """
    from backend.common.admin_notification_models import AdminNotification
    from backend.domain.catalog.models import BookCopy

    h = {
        "Authorization": "Bearer "
        + client.post(
            "/api/admin/login", json={"username": "admin", "password": "dmkwords123"}
        ).json()["token"]
    }
    _p, c, _mini = _family(client, h, "13981600003", "扫码还损孩")
    _pay_deposit(client, h, c["id"])
    book_id, copies = _book(client, h, "扫码还损样例")
    _set_isbn(book_id, "9788160000031")
    borrow = client.post(
        "/api/admin/circulation/borrow",
        json={"child_id": c["id"], "copy_id": copies[0]},
        headers=h,
    )
    assert borrow.status_code == 200, borrow.text
    # 报损登记（不动在借记录）→ 副本变 maintenance，写一条赔偿待办
    dmg = client.post(
        f"/api/admin/copies/{copies[0]}/report-damage",
        json={"kind": "damage", "reason": "封面撕裂"},
        headers=h,
    )
    assert dmg.status_code == 200, dmg.text
    with _db() as db:
        assert (
            db.query(BookCopy.status).filter(BookCopy.id == copies[0]).scalar()
            == BookCopy.STATUS_MAINTENANCE
        )
        todo_before = (
            db.query(AdminNotification)
            .filter(
                AdminNotification.scene == AdminNotification.SCENE_COPY_DAMAGE_COMPENSATION,
                AdminNotification.is_deleted == 0,
            )
            .count()
        )

    # 家长把书还回来：扫码（同一 ISBN 第二次 = 还书）
    scan = client.post(
        "/api/admin/circulation/scan",
        json={"child_id": c["id"], "code": "9788160000031"},
        headers=h,
    )
    assert scan.status_code == 200, scan.text
    assert scan.json()["action"] == "return", scan.json()
    with _db() as db:
        copy_status = db.query(BookCopy.status).filter(BookCopy.id == copies[0]).scalar()
        todo_after = (
            db.query(AdminNotification)
            .filter(
                AdminNotification.scene == AdminNotification.SCENE_COPY_DAMAGE_COMPENSATION,
                AdminNotification.is_deleted == 0,
            )
            .count()
        )
    assert copy_status == BookCopy.STATUS_MAINTENANCE, (
        f"还书不得把已报损副本改回在馆（实 {copy_status}）（RED=硬编码 normal 静默清标记）"
    )
    assert todo_after == todo_before, (
        f"同一次损失不该写第二条赔偿待办：{todo_before} → {todo_after}"
    )


# ---------- #16 全空格 override_reason ----------


def test_borrow_blank_override_reason_rejected(client: TestClient):
    """人工放行原因全空格 → 视为未填（拒），不得借出。

    RED（还原修复：不 strip）：`"   "` 为真值 → 押金未缴的闸被绕过，孩子借到书且留痕是空白。
    """
    h = {
        "Authorization": "Bearer "
        + client.post(
            "/api/admin/login", json={"username": "admin", "password": "dmkwords123"}
        ).json()["token"]
    }
    _p, c, _mini = _family(client, h, "13981600004", "空格放行孩")  # 会员但没缴押金
    book_id, copies = _book(client, h, "空格放行样例")
    r = client.post(
        "/api/admin/circulation/borrow",
        json={"child_id": c["id"], "copy_id": copies[0], "override_reason": "   "},
        headers=h,
    )
    assert r.status_code == 422, f"全空格原因不算原因：{r.status_code} {r.text[:120]}"
    assert "押金" in r.json()["detail"], r.json()
    # 对照：真的写了原因 → 放行成功（闸只拦空串，不拦合法放行）
    r2 = client.post(
        "/api/admin/circulation/borrow",
        json={
            "child_id": c["id"],
            "copy_id": copies[0],
            "override_reason": "家长现场承诺次日缴押金",
        },
        headers=h,
    )
    assert r2.status_code == 200, r2.text


# ---------- #17 借书关预约：不得覆盖并发已取消的预约 ----------


def test_borrow_does_not_overwrite_concurrently_cancelled_reservation(
    client: TestClient, session_pair
):
    """**并发**：家长取消（持行锁未提交）期间借书收尾不得把预约覆盖回 checked_out。

    RED（还原修复：普通读 + 无条件赋值）：s2 的普通读拿到旧值 active，随后 UPDATE 撞锁——
    等 s1 提交 cancelled 后 s2 的 UPDATE 落地，**取消被覆盖**（界面显示"已借出"，书却已回馆）。
    修复后：s2 的锁定读阻塞，s1 提交后当前读+状态守卫都不认这行 → 不再写。
    """
    import threading
    import time

    from backend.domain.circulation.service import CirculationService
    from backend.domain.reading.models import Reservation
    from tests.unit.test_g5_reservation_copy_exception_20261014 import (
        _admin_h,
        _member_child,
        _reserve,
    )

    h = _admin_h(client)
    child, mini = _member_child(client, h, "13981600005", "关预约并发孩")
    book_id, copies = _book(client, h, "关预约并发样例", copies=2)
    res = _reserve(client, mini, child["id"], book_id)
    rid = res["id"]

    s1, s2 = session_pair
    locked = s1.query(Reservation).filter(Reservation.id == rid).with_for_update().first()
    assert locked.status == Reservation.STATUS_ACTIVE
    locked.status = Reservation.STATUS_CANCELLED
    s1.flush()  # 持锁未提交：模拟"取消正在进行"

    out: dict = {}

    def _borrow():
        try:
            rec, _w = CirculationService(s2).borrow(
                type("A", (), {"id": 1, "display_name": "超管"})(), child["id"], copies[1], None
            )
            s2.commit()
            out["ok"] = rec.id
        except Exception as exc:  # noqa: BLE001
            s2.rollback()
            out["err"] = repr(exc)

    t = threading.Thread(target=_borrow)
    t.start()
    time.sleep(0.6)  # 让借书线程走到"关预约收尾"（修复后应阻塞在预约行锁上）
    s1.commit()  # 取消提交
    t.join(timeout=10)
    assert not t.is_alive(), f"借书线程未结束（疑似死锁）：{out}"
    with _db() as db:
        status = db.query(Reservation.status).filter(Reservation.id == rid).scalar()
    assert status == Reservation.STATUS_CANCELLED, (
        f"并发取消不得被借书收尾覆盖（实 {status}）（RED=快照读 + 无条件覆盖写）"
    )


# ---------- #18 到期提醒窗口重叠 ----------


def test_due_remind_sends_once_for_due_today(client: TestClient):
    """今天到期的在借记录只发**一条**提醒（节点 0 与 1 的补发窗口不再重叠）。

    RED（还原修复：每节点各向前放宽 1 天）：due=today 同时落进节点 0 与节点 1 的窗口 → 两条。
    """
    from backend.common.notification_models import Notification
    from backend.domain.circulation.models import BorrowRecord
    from backend.domain.circulation.reminders import book_due_remind

    h = {
        "Authorization": "Bearer "
        + client.post(
            "/api/admin/login", json={"username": "admin", "password": "dmkwords123"}
        ).json()["token"]
    }
    _p, c, _mini = _family(client, h, "13981600006", "到期提醒孩")
    _pay_deposit(client, h, c["id"])
    book_id, copies = _book(client, h, "到期提醒样例")
    borrow = client.post(
        "/api/admin/circulation/borrow",
        json={"child_id": c["id"], "copy_id": copies[0]},
        headers=h,
    )
    assert borrow.status_code == 200, borrow.text
    with _db() as db:
        db.query(BorrowRecord).filter(BorrowRecord.id == borrow.json()["id"]).update(
            {"due_at": datetime.combine(date.today(), datetime.min.time()) + timedelta(hours=20)}
        )
        db.commit()
        sent = book_due_remind(db)
        rows = (
            db.query(Notification)
            .filter(Notification.scene == "borrow.due_remind", Notification.is_deleted == 0)
            .all()
        )
    assert sent == 1 and len(rows) == 1, (
        f"今天到期只应发一条（实 sent={sent}, rows={len(rows)}）"
        "（RED=0 天与 1 天两个节点窗口重叠，同一条记录发两条）"
    )


# ---------- #19 积分人工调整不得为负 ----------


def test_negative_points_adjust_rejected(client: TestClient):
    """负积分调整被拒（PRD §7.2：积分不扣、非负）；正数正常入账。"""
    h = {
        "Authorization": "Bearer "
        + client.post(
            "/api/admin/login", json={"username": "admin", "password": "dmkwords123"}
        ).json()["token"]
    }
    _p, c, _mini = _family(client, h, "13981600007", "积分调整孩")
    bad = client.post(
        f"/api/admin/children/{c['id']}/points/adjust",
        json={"points": -50, "reason": "手工扣分"},
        headers=h,
    )
    assert bad.status_code == 422, f"负数调整必须被拒：{bad.status_code} {bad.text[:100]}"
    ok = client.post(
        f"/api/admin/children/{c['id']}/points/adjust",
        json={"points": 50, "reason": "补偿加成"},
        headers=h,
    )
    assert ok.status_code == 200, ok.text


# ---------- #22 退款回调找不到退款单 ----------


def test_refund_notify_unknown_out_refund_no_404(client: TestClient):
    """未知商户退款单号 → 404（修复前 AttributeError 被兜底成 200「已记录待人工处理」）。

    走 `handle_notify` 本身（`simulate-refund-callback` 端点自己有前置校验，构造不出"单号格式对但查无此单"
    的入参）；报文形状与演练端点同款，mock 通道验签放行。
    """
    import json
    import time
    import uuid

    from backend.database import get_session
    from backend.domain.identity.payment_service import PaymentService

    payload = {
        "out_refund_no": "RF999999-1-1",
        "transaction_id": f"mock_refund_{uuid.uuid4().hex[:16]}",
        "refund_status": "SUCCESS",
        "refund_amount": 100,
    }
    body = json.dumps(
        {
            "id": uuid.uuid4().hex,
            "event_type": "REFUND.SUCCESS",
            "resource_type": "encrypt-resource",
            "resource": {
                "algorithm": "AEAD_AES_256_GCM",
                "ciphertext": json.dumps(payload),
                "associated_data": "refund",
                "nonce": uuid.uuid4().hex[:12],
            },
        },
        ensure_ascii=False,
    )
    with get_session() as db:
        status, resp = PaymentService(db).handle_notify(
            body=body, signature="mock_sign", timestamp=str(int(time.time())), nonce="mocknonce"
        )
    assert status == 404, (
        f"找不到退款单必须 404（RED=先解引用 None → 异常被 200 兜底）：{status} {resp}"
    )
    assert resp["message"] == "退款单不存在", resp


# ---------- #24 阅读圈 child_id 归属 ----------


def test_circle_posts_child_id_requires_ownership(client: TestClient):
    """传别人孩子的 child_id 查信息流 → 拒（越权与不存在同回应）。"""
    h = {
        "Authorization": "Bearer "
        + client.post(
            "/api/admin/login", json={"username": "admin", "password": "dmkwords123"}
        ).json()["token"]
    }
    _p1, c1, _mini1 = _family(client, h, "13981600008", "圈甲孩")
    _p2, c2, mini2 = _family(client, h, "13981600009", "圈乙孩")
    ok = client.get(f"/api/miniapp/circle/posts?child_id={c2['id']}", headers=mini2)
    assert ok.status_code == 200, ok.text
    bad = client.get(f"/api/miniapp/circle/posts?child_id={c1['id']}", headers=mini2)
    ghost = client.get("/api/miniapp/circle/posts?child_id=999999", headers=mini2)
    assert bad.status_code in (404, 422), f"越权 child_id 必须被拒：{bad.status_code}"
    assert bad.status_code == ghost.status_code and bad.json() == ghost.json(), (
        bad.json(),
        ghost.json(),
    )


# ---------- #25 越权与不存在同回应 ----------


def test_transfer_cancel_and_post_delete_indistinguishable(client: TestClient):
    """别人的转让申请 / 帖子：越权与不存在同回应（404），不再靠 422 区分出"存在但不是你的"。"""
    from backend.domain.identity.models import TransferRequest

    h = {
        "Authorization": "Bearer "
        + client.post(
            "/api/admin/login", json={"username": "admin", "password": "dmkwords123"}
        ).json()["token"]
    }
    _p1, c1, mini1 = _family(client, h, "13981600010", "甲户孩")
    _p2, c2, mini2 = _family(client, h, "13981600011", "乙户孩")
    # 甲户造一条转让申请（直接落库：本用例只考撤销的越权回应，不重造 12 项条件链）
    with _db() as db:
        row = TransferRequest(
            source_child_id=c1["id"],
            target_child_id=c1["id"],
            expires_at=datetime.now() + timedelta(hours=72),
        )
        db.add(row)
        db.commit()
        tid = row.id

    foreign = client.post(f"/api/miniapp/transfers/{tid}/cancel", headers=mini2)
    ghost = client.post("/api/miniapp/transfers/999999/cancel", headers=mini2)
    assert foreign.status_code == ghost.status_code == 404, (foreign.text, ghost.text)
    assert foreign.json() == ghost.json(), (foreign.json(), ghost.json())
    # 甲户自己撤 → 正常
    own = client.post(f"/api/miniapp/transfers/{tid}/cancel", headers=mini1)
    assert own.status_code == 200, own.text

    # 删帖：乙户的帖子由甲户删 → 404（与不存在同回应）
    # 直插帖子行：晒卡端点要求真实成就 ref_id（本用例只考"越权 ≠ 不存在"这一点）
    from backend.domain.reading_circle.models import CirclePost

    with _db() as db:
        post = CirclePost(
            parent_id=_p2["id"],
            child_id=c2["id"],
            card_type=CirclePost.CARD_FINISH_BOOK,
            ref_id=1,
            card_data="{}",
        )
        db.add(post)
        db.commit()
        pid = post.id
    bad = client.delete(f"/api/miniapp/circle/posts/{pid}", headers=mini1)
    ghost2 = client.delete("/api/miniapp/circle/posts/999999", headers=mini1)
    assert bad.status_code == ghost2.status_code == 404, (bad.text, ghost2.text)


# ---------- #23 生产自检不得被拼错的环境名整段跳过 ----------


def _prod_settings(**overrides):
    from backend.config import Settings

    base = dict(
        DEBUG=False,
        SECRET_KEY="x" * 32,
        DB_PASSWORD="pw",
        WECHAT_APP_ID="wx",
        WECHAT_APP_SECRET="sec",
        LOGIN_DEV_CODE="",
        CORS_ORIGINS="https://admin.example.com",
        SMS_ENABLED=True,
        SMS_PROVIDER="aliyun",
        SMS_APP_ID="app",
        SMS_APP_KEY="key",
        SMS_SIGN_NAME="sign",
        PAYMENT_ENABLED=True,
        PAYMENT_PROVIDER="wechat",
    )
    base.update(overrides)
    from backend import config as _cfg

    _cfg.sms_sdk_available = lambda provider: True  # type: ignore[assignment]
    return Settings(**base)


def test_prod_validation_not_skipped_by_env_typo():
    """APP_ENV 拼错（prod）+ DEBUG=true → 红线仍全量校验（原先整段静默跳过）。"""
    import pytest

    with pytest.raises(RuntimeError, match="SECRET_KEY"):
        _prod_settings(
            APP_ENV="prod", DEBUG=True, SECRET_KEY="change-in-production"
        ).validate_production()
    # 明确的本机环境 + DEBUG → 仍可跳过（开发/CI 不受影响）
    _prod_settings(
        APP_ENV="dev", DEBUG=True, SECRET_KEY="change-in-production"
    ).validate_production()


def test_weak_secret_key_rejected_in_production():
    """空串 / 短密钥在生产被拒（原先只比一个默认字面量）。"""
    import pytest

    with pytest.raises(RuntimeError, match="SECRET_KEY"):
        _prod_settings(APP_ENV="production", SECRET_KEY="").validate_production()
    with pytest.raises(RuntimeError, match="SECRET_KEY"):
        _prod_settings(APP_ENV="production", SECRET_KEY="short-key").validate_production()


# ---------- #28 conftest 清理清单 ----------


def test_conftest_clears_payment_reconciliations():
    """对账表必须在每测试清理清单里（否则对账用例读到上个用例的残留）。"""
    from tests.unit.conftest import ADMIN_TABLES

    assert "payment_reconciliations" in ADMIN_TABLES
    with _db() as db:
        # 能查通即表名正确（写错表名会在清理时炸，但那时已经晚了）
        assert db.execute(text("SELECT COUNT(*) FROM payment_reconciliations")).scalar() == 0


# ---------- #26 观察报告图片：路径守卫用项目单一口径 ----------


def test_observation_image_rejects_sibling_prefix_path(client: TestClient, monkeypatch):
    """`observation/../observation-evil/x.jpg` 必须被路径守卫拒（裸 startswith 会放行同前缀兄弟目录）。

    为什么要把 DB 归属校验打桩成"放行"：现在这条越界路径**下一道**（`image_owned_by`）会兜住，
    这正是审查说的"当前被兜住、属未来旁路"——本用例把下一道放行，专门钉住路径守卫本身
    （纵深防御：将来归属校验一变宽，这里必须还挡得住）。
    直接调端点函数：httpx/FastAPI 会把 URL 里的 `..` 规范化掉，走 HTTP 构造不出这个入参。
    """
    import os

    import pytest

    from backend.common.exceptions import NotFoundError
    from backend.config import get_settings
    from backend.domain.identity.observation_service import ObservationReportService
    from backend.domain.reading.miniapp_router import observation_image

    root = os.path.abspath(get_settings().UPLOADS_DIR)
    evil_dir = os.path.join(root, "observation-evil")
    os.makedirs(evil_dir, exist_ok=True)
    with open(os.path.join(evil_dir, "x.jpg"), "wb") as fh:
        fh.write(b"\xff\xd8\xff")  # 真建出这个文件：裸 startswith 会放行它

    monkeypatch.setattr(
        ObservationReportService, "image_owned_by", lambda *a, **k: True, raising=True
    )

    h = {
        "Authorization": "Bearer "
        + client.post(
            "/api/admin/login", json={"username": "admin", "password": "dmkwords123"}
        ).json()["token"]
    }
    _p, _c, mini = _family(client, h, "13981600012", "图片路径孩")
    token = mini["Authorization"].split(" ", 1)[1]

    from backend.database import get_session

    with get_session() as db:
        with pytest.raises(NotFoundError):
            observation_image(path="../observation-evil/x.jpg", token=token, db=db)


# ---------- #20 打卡周期奖：断签后重建的周期仍要发奖 ----------


def test_checkin_cycle_award_survives_streak_reset(client: TestClient):
    """断签重数后，下一个 7 天周期仍要发奖（原 `streak // N` 口径：cycle_no 复用 → 终身只发一次）。

    RED（还原修复：cycle_no = streak // 7 + 按 cycle_no 查重）：断签后 streak 回到 7 → cycle_no 又是 1
    → 撞历史记录 → 不发奖（断言"必须发"会红）。
    """
    from datetime import datetime, timedelta

    from backend.domain.growth.models import CheckinStreakRecord
    from backend.domain.growth.service import GrowthService
    from backend.domain.identity.models import Child

    h = {
        "Authorization": "Bearer "
        + client.post(
            "/api/admin/login", json={"username": "admin", "password": "dmkwords123"}
        ).json()["token"]
    }
    _p, c, _mini = _family(client, h, "13981600013", "断签发奖孩")
    with _db() as db:
        db.query(Child).filter(Child.id == c["id"]).first()
        svc = GrowthService(db)
        first = svc.on_checkin(c["id"], 7)  # 第一个 7 天周期
        assert [a["type"] for a in first] == ["checkin_7"], first
        assert svc.on_checkin(c["id"], 7) == [], "同一次达成重复调用必须幂等"

        # 断签重建：老周期是**昨天**达成的（跨天后 streak 又从 0 数到 7）
        db.query(CheckinStreakRecord).filter(CheckinStreakRecord.child_id == c["id"]).update(
            {"awarded_at": datetime.now() - timedelta(days=1)}
        )
        db.commit()

        state = svc.ensure_state(c["id"])
        before = state.points_total
        again = svc.on_checkin(c["id"], 7)  # 新周期再次达成 7 天
        assert [a["type"] for a in again] == ["checkin_7"], (
            f"断签重建后的新周期必须发奖（实 {again}）"
            "（RED=cycle_no 复用为 1 → 撞历史记录，此后终身不发）"
        )
        assert state.points_total > before, "发奖必须落积分"
        rows = (
            db.query(CheckinStreakRecord)
            .filter(CheckinStreakRecord.child_id == c["id"])
            .order_by(CheckinStreakRecord.id)
            .all()
        )
        assert [r.cycle_no for r in rows] == [1, 2], (
            f"周期序号只增不复用：{[(r.cycle_no) for r in rows]}"
        )


# ---------- #21 周报/月报通知：按周期去重，不再终身一条 ----------


def test_report_notification_per_period(client: TestClient, monkeypatch):
    """同一孩子两条周期各一条报告通知（原 `dedup_key=kind` → 终身只有第一条）。

    RED（还原修复：dedup_key=kind）：换周期后第二次生成仍撞唯一索引 → 只剩 1 条。
    """
    from backend.common.notification_models import Notification
    from backend.domain.growth.report_service import ReportService

    h = {
        "Authorization": "Bearer "
        + client.post(
            "/api/admin/login", json={"username": "admin", "password": "dmkwords123"}
        ).json()["token"]
    }
    _p, c, _mini = _family(client, h, "13981600014", "报告通知孩")

    real = ReportService.period_range

    def _gen() -> int:
        r = client.post(f"/api/admin/children/{c['id']}/reports/weekly/generate", headers=h)
        assert r.status_code == 200, r.text
        with _db() as db:
            return (
                db.query(Notification)
                .filter(
                    Notification.scene == "report.generated",
                    Notification.is_deleted == 0,
                )
                .count()
            )

    monkeypatch.setattr(ReportService, "period_range", real)
    assert _gen() == 1, "第一份周报要有一条通知"
    assert _gen() == 1, "同一周期重复生成不重复发（幂等）"

    # 下一个周期（把周期区间往后挪一周）→ 必须有第二条通知
    def _next_period(self, kind: str):
        start, end, label = real(self, kind)
        return start + timedelta(days=7), end + timedelta(days=7), label + "（下一周）"

    monkeypatch.setattr(ReportService, "period_range", _next_period)
    assert _gen() == 2, "换周期必须再发一条（RED=dedup_key 只有 kind，终身只发第一条）"
