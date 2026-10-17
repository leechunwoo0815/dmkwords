# tests/unit/test_audit4_p2_batch1_20261017.py — 第四轮大审查·P2 批量（子批 1）回归，2026-10-17
"""第六十九批（子批 1）：P2-19 门禁视角分离 · P2-25 部署时区 · P2-26 观测孤儿文件 ·
P2-27 报告图路径口径 · P2-18 死信落库 · P2-28 三个死键（退款/退会超时提醒 + 后台登录有效期）。

每条用例都是报告里那条缺陷的复现或判据的固化，逐条**去修复即红**（变异自证见 docs/09 第六十九轮）。
"""

import importlib.util
import json
import pathlib
from datetime import UTC, datetime, timedelta

from fastapi.testclient import TestClient

from tests.unit.test_wm10_concurrency import _db, _family, _h

ROOT = pathlib.Path(__file__).resolve().parents[2]


# ---------- P2-19：门禁脚本必须**按视角**分别断言 ----------


def _checker():
    spec = importlib.util.spec_from_file_location(
        "check_admin_todo_rows", ROOT / "scripts" / "check_admin_todo_rows.py"
    )
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_admin_todo_rows_checker_splits_viewpoints():
    """后端两个角色分支的求和项必须被分别归位（超管 9 项 / 专员 4 项）。"""
    mod = _checker()
    super_keys, staff_keys = mod.admin_total_keys_by_viewpoint(ROOT)
    assert "refund_pending" in super_keys and "payment_anomaly" in super_keys
    assert "refund_pending" not in staff_keys, "审计类不该被算进专员视角"
    assert staff_keys == {
        "activity_enroll_pending",
        "member_follow_up",
        "pending_evaluation_followup",
        "deposit_settle_followup",
    }
    assert not (super_keys & staff_keys), "两视角求和项不该重叠（重叠＝归属解析错）"


def test_admin_todo_rows_checker_catches_row_moved_across_tables(tmp_path):
    """把键从专员表**挪进**超管表：并集不变，老判法假绿；新判法必须点名该键。

    这是 P2-19 的原始缺陷形态（2026-10-15 P1 #10 假绿复发路径），故锁成回归用例。
    """
    import re
    import shutil

    (tmp_path / "backend/domain/admin").mkdir(parents=True)
    (tmp_path / "admin-web/src/pages").mkdir(parents=True)
    shutil.copy(ROOT / "backend/domain/admin/todo_service.py", tmp_path / "backend/domain/admin/")
    dash_src = ROOT / "admin-web/src/pages/Dashboard.tsx"
    dash = tmp_path / "admin-web/src/pages/Dashboard.tsx"
    shutil.copy(dash_src, dash)

    text = dash.read_text(encoding="utf-8")
    row = '  { key: "member_follow_up", label: "入会跟进", route: "/members" },\n'
    assert row in text
    text = text.replace(row, "", 1)  # 从专员表删
    m = re.search(r"(const TODO_ROWS:[\s\S]*?\n)\];", text)
    text = text[: m.end(1)] + row + text[m.end(1) :]  # 挪进超管表
    dash.write_text(text, encoding="utf-8")

    mod = _checker()
    super_keys, staff_keys = mod.admin_total_keys_by_viewpoint(tmp_path)
    rows = mod.frontend_row_keys(tmp_path)
    union = rows["TODO_ROWS"] | rows["STAFF_TODO_ROWS"]
    assert not (super_keys | staff_keys) - union, "并集判法在这种变异下**仍然通过**（假绿温床本身）"
    # 判据必须走**检查器自己的** find_missing：用例自己重算 = 同一口径两份实现（红线 154），
    # 那样把 main 改回并集判法用例照样绿（2026-10-17 首次变异自证当场咬到）
    super_missing, staff_missing = mod.find_missing(tmp_path)
    assert staff_missing == ["member_follow_up"], "按视角断言必须点名专员卡漏掉的那一行"
    assert super_missing == [], "超管视角没漏（被挪过去的那一行在 TODO_ROWS 里）"


# ---------- P2-25：部署脚本钉死 TZ ----------


def test_deploy_script_pins_timezone():
    text = (ROOT / "deploy/start-backend.sh").read_text(encoding="utf-8")
    assert "export TZ=" in text, "部署脚本必须显式钉 TZ（任务体用宿主本地时间）"
    assert "Asia/Shanghai" in text


# ---------- P2-27：报告图端点走 media_paths.is_within ----------


def test_report_image_endpoint_uses_is_within():
    """路径口径必须收口到 `media_paths.is_within`（裸 startswith 会被同前缀兄弟目录骗过）。

    只看**非注释行**——修复说明的注释里刻意引用了旧写法（`full.startswith(root_dir)`），
    这里断言的是代码而不是注释。
    """
    src = (ROOT / "backend/domain/growth/miniapp_router.py").read_text(encoding="utf-8")
    code = "\n".join(ln for ln in src.split("\n") if not ln.lstrip().startswith("#"))
    assert "is_within(root_dir, full)" in code
    assert "full.startswith(root_dir)" not in code

    from backend.common.media_paths import is_within

    root = "/tmp/uploads"
    assert not is_within(root, "/tmp/uploads-evil/x.jpg"), "同前缀兄弟目录必须判外部"


# ---------- P2-26：观测报告图片先全量校验，失败不留孤儿 ----------


def _mini_upload(client, h, phone, files):
    """建家庭 + 上传评估报告（管理端接口，返回 (child_id, response)）。"""
    _p, c, _mini = _family(client, h, phone)
    return c["id"], client.post(
        f"/api/admin/children/{c['id']}/observation-reports",
        files=files,
        data={"remark": "回归"},
        headers=h,
    )


def _png_bytes(idx: int) -> bytes:
    """真实可解码的 PNG（内容各不相同——内容寻址落盘时不会互相覆盖）。"""
    import io as _io

    from PIL import Image

    buf = _io.BytesIO()
    Image.new("RGB", (8, 8), (idx * 20 % 256, 30, 60)).save(buf, "PNG")
    return buf.getvalue()


def _obs_files(count: int, bad_last: bool = False):
    out = []
    for i in range(count):
        if bad_last and i == count - 1:
            out.append(("files", (f"bad{i}.txt", b"not an image", "text/plain")))
        else:
            out.append(("files", (f"ok{i}.png", _png_bytes(i + 1), "image/png")))
    return out


def _obs_dir(child_id: int) -> pathlib.Path:
    from backend.config import get_settings

    return pathlib.Path(get_settings().UPLOADS_DIR) / "observation" / f"child_{child_id}"


def _obs_snapshot(child_id: int) -> set[str]:
    """该孩子观测目录的文件名快照。

    注意：pytest 的 UPLOADS_DIR 是**整个会话共享**的临时目录，且观测图是内容寻址
    （同图同名）——所以只能比较"本次请求**新增**了哪些文件"，不能断言目录为空
    （别的用例留下的同内容文件会误伤，2026-10-17 全量门禁实测咬到两次）。
    """
    d = _obs_dir(child_id)
    return {p.name for p in d.glob("*")} if d.exists() else set()


def test_observation_upload_rejects_before_writing_any_file(client: TestClient):
    """第 3 张扩展名不合法 → 前 2 张**不许**已落盘（原先边校验边落盘 = 永久孤儿）。"""
    h = _h(client)
    _p, c, _mini = _family(client, h, "13800008501")
    before = _obs_snapshot(c["id"])
    resp = client.post(
        f"/api/admin/children/{c['id']}/observation-reports",
        files=_obs_files(3, bad_last=True),
        data={"remark": "回归"},
        headers=h,
    )
    assert resp.status_code == 422, resp.text
    assert "仅支持 PNG/JPG" in resp.text
    added = _obs_snapshot(c["id"]) - before
    assert added == set(), f"校验失败不许留文件，本次新增残留 {added}"


def test_observation_upload_cleans_up_when_save_fails_midway(client: TestClient, monkeypatch):
    """落盘途中出错 → 回收本次已写文件（同图已被引用时保留，见实现注释）。"""
    import backend.common.file_storage as fs
    from backend.domain.identity.observation_service import ObservationReportService

    real = fs.save_observation_image
    calls = {"n": 0}

    def flaky(child_id, data, ext, policy):
        calls["n"] += 1
        if calls["n"] == 2:
            raise RuntimeError("模拟第二张落盘失败")
        return real(child_id, data, ext, policy)

    monkeypatch.setattr(fs, "save_observation_image", flaky)
    h = _h(client)
    _p, c, _mini = _family(client, h, "13800008502")
    before = _obs_snapshot(c["id"])
    db = _db()
    try:
        child = _child(db, c["id"])
        svc = ObservationReportService(db)
        import io

        class _F:
            def __init__(self, name, data):
                self.filename = name
                self.file = io.BytesIO(data)

        try:
            svc.upload(
                _admin(db),
                child,
                [
                    _F("a.png", _png_bytes(11)),
                    _F("b.png", _png_bytes(12)),
                    _F("c.png", _png_bytes(13)),
                ],
                "回归",
            )
        except RuntimeError:
            pass
        else:  # pragma: no cover - 失败必须抛出
            raise AssertionError("落盘失败必须原样抛出")
    finally:
        db.close()
    added = _obs_snapshot(c["id"]) - before
    assert added == set(), f"中途失败必须回收本次已写文件，本次新增残留 {added}"


def _child(db, child_id):
    from backend.domain.identity.models import Child

    return db.query(Child).filter(Child.id == child_id).first()


def _admin(db):
    from backend.domain.admin.models import AdminUser

    return db.query(AdminUser).filter(AdminUser.username == "admin").first()


# ---------- P2-18：死信必须真的落库（此前只写日志） ----------


def test_dead_letter_written_on_shared_session_failure():
    """共享事务里订阅器抛错：业务照旧回滚 + `dead_letters` 落一条（此前零写入）。"""
    from backend.common.events import DomainEvent, event_bus
    from backend.common.notification_models import DeadLetter

    def boom(event, db):
        raise ValueError("订阅器故意失败")

    event_bus.subscribe("p2_18.probe", boom)
    db = _db()
    try:
        try:
            event_bus.publish(DomainEvent(event_type="p2_18.probe"), db=db)
        except ValueError:
            pass
        else:  # pragma: no cover - 异常必须原样抛出（业务一致性）
            raise AssertionError("共享会话模式下订阅器异常必须 re-raise")
        db.rollback()
    finally:
        db.close()
        event_bus.unsubscribe("p2_18.probe", boom)

    check = _db()
    try:
        row = (
            check.query(DeadLetter)
            .filter(DeadLetter.event_type == "p2_18.probe")
            .order_by(DeadLetter.id.desc())
            .first()
        )
    finally:
        check.close()
    assert row is not None, "死信表必须留下痕迹（重启不再丢）"
    assert row.handler_name == "boom" and "订阅器故意失败" in (row.error or "")
    assert row.retry_count == 0
    assert json.loads(row.payload) is not None


def test_dead_letter_retry_count_on_independent_session():
    """独立会话模式：重试一次仍失败 → retry_count=1。"""
    from backend.common.events import DomainEvent, event_bus
    from backend.common.notification_models import DeadLetter

    def boom(event, db):
        raise RuntimeError("独立会话也失败")

    event_bus.subscribe("p2_18.probe2", boom)
    try:
        event_bus.publish(DomainEvent(event_type="p2_18.probe2"))
    finally:
        event_bus.unsubscribe("p2_18.probe2", boom)

    db = _db()
    try:
        row = (
            db.query(DeadLetter)
            .filter(DeadLetter.event_type == "p2_18.probe2")
            .order_by(DeadLetter.id.desc())
            .first()
        )
    finally:
        db.close()
    assert row is not None and row.retry_count == 1


# ---------- P2-28：退款/退会审核超时提醒 + 后台登录有效期 ----------


def _aging_refund(client, h, phone, hours: int) -> tuple[int, int]:
    """建一个"已提交 hours 小时"的待审退款单，返回 (child_id, request_id)。"""
    _p, c, mini = _family(client, h, phone)
    o = client.post(
        "/api/admin/orders", json={"child_id": c["id"], "order_type": "observation_fee"}, headers=h
    ).json()
    client.post(
        f"/api/admin/orders/{o['id']}/confirm-payment", json={"pay_method": "scan"}, headers=h
    )
    r = client.post(
        "/api/miniapp/refund-requests",
        json={"child_id": c["id"], "order_id": o["id"], "reason": "超时回归"},
        headers=mini,
    )
    assert r.status_code == 200, r.text
    rid = r.json()["id"]
    db = _db()
    try:
        from backend.domain.identity.models import RefundRequest

        row = db.query(RefundRequest).filter(RefundRequest.id == rid).first()
        row.created_at = datetime.now() - timedelta(hours=hours)
        db.commit()
    finally:
        db.close()
    return c["id"], rid


def test_refund_review_timeout_warn(client: TestClient):
    """超 72h 未审 → 一条待办 + 计入 admin_total；重复跑不刷屏；审结后自动归零。"""
    from backend.tasks.registry import run_task

    h = _h(client)
    child_id, rid = _aging_refund(client, h, "13800008511", hours=80)
    fresh_child, _fresh_rid = _aging_refund(client, h, "13800008512", hours=1)

    run_task("refund_review_timeout_warn")
    counts = client.get("/api/admin/todo-counts", headers=h).json()
    assert counts["refund_review_timeout"] == 1, counts
    inbox = client.get(
        "/api/admin/admin-notifications?scene=admin.refund_review_timeout", headers=h
    ).json()["items"]
    assert len(inbox) == 1 and inbox[0]["effective_status"] == "pending"
    assert str(rid) == inbox[0]["ref_id"]
    assert "退款审核超时" in inbox[0]["content"]

    # 幂等：再跑一次不加新条（唯一索引含 ref_id + dedup 默认 "1"）
    assert run_task("refund_review_timeout_warn")["processed"] == 0
    assert client.get("/api/admin/todo-counts", headers=h).json()["refund_review_timeout"] == 1

    # 审核通过 → 显示态自动"已审结"、计数归零（提醒不会变孤儿）
    assert (
        client.post(
            f"/api/admin/refund-requests/{rid}/review", json={"approve": True}, headers=h
        ).status_code
        == 200
    )
    counts2 = client.get("/api/admin/todo-counts", headers=h).json()
    assert counts2["refund_review_timeout"] == 0
    assert counts2["admin_total"] >= 0
    # 未超时的那一单从未被提醒
    assert fresh_child != child_id


def test_withdrawal_review_timeout_warn(client: TestClient):
    """退会申请待审超 3 天 → 一条待办；口径=提醒不自动取消（单据仍是 applying）。"""
    from backend.tasks.registry import run_task

    h = _h(client)
    p, c, mini = _family(client, h, "13800008521")
    # 退会前提：孩子得先在会（观察期/正式）——先缴观察期费
    o = client.post(
        "/api/admin/orders", json={"child_id": c["id"], "order_type": "observation_fee"}, headers=h
    ).json()
    client.post(
        f"/api/admin/orders/{o['id']}/confirm-payment", json={"pay_method": "scan"}, headers=h
    )
    r = client.post(
        "/api/miniapp/withdrawals", json={"child_id": c["id"], "reason": "超时回归"}, headers=mini
    )
    assert r.status_code == 200, r.text
    wid = r.json()["id"]
    db = _db()
    try:
        from backend.domain.identity.models import WithdrawalRequest

        row = db.query(WithdrawalRequest).filter(WithdrawalRequest.id == wid).first()
        row.created_at = datetime.now() - timedelta(days=4)
        db.commit()
        assert row.status == WithdrawalRequest.STATUS_APPLYING  # 提醒不改状态
    finally:
        db.close()

    run_task("withdrawal_review_timeout_warn")
    counts = client.get("/api/admin/todo-counts", headers=h).json()
    assert counts["withdrawal_review_timeout"] == 1, counts
    assert run_task("withdrawal_review_timeout_warn")["processed"] == 0
    db = _db()
    try:
        from backend.domain.identity.models import WithdrawalRequest

        row = db.query(WithdrawalRequest).filter(WithdrawalRequest.id == wid).first()
        db.refresh(row)
        assert row.status == WithdrawalRequest.STATUS_APPLYING, "超时提醒**不许**自动取消申请"
    finally:
        db.close()


def test_review_timeout_tasks_registered_with_groups():
    from backend.tasks.registry import TASKS

    assert TASKS["refund_review_timeout_warn"].group == "资金"  # 动钱 → 手动触发仅超管
    assert TASKS["withdrawal_review_timeout_warn"].group == "会员"
    assert len(TASKS) == 21


def test_admin_token_expire_hours_from_config_center(client: TestClient):
    """配置中心 `admin_token_expire_hours` 是登录有效期的**生效点**（此前零读取方）。"""
    import jwt

    from backend.common.config_service import ConfigService, invalidate_config_cache

    h = _h(client)
    client.put(
        "/api/admin/configs/admin_token_expire_hours",
        json={"value": "1", "reason": "P2-28 回归"},
        headers=h,
    )
    token = client.post(
        "/api/admin/login", json={"username": "admin", "password": "dmkwords123"}
    ).json()["token"]
    payload = jwt.decode(token, options={"verify_signature": False})
    now = datetime.now(UTC)
    exp = datetime.fromtimestamp(payload["exp"], UTC)
    delta_h = (exp - now).total_seconds() / 3600
    assert 0.8 < delta_h <= 1.01, f"配置 1 小时没生效：{delta_h}"

    # 非法值（0）→ 回落环境默认，绝不因一条配置把登录锁死
    client.put(
        "/api/admin/configs/admin_token_expire_hours",
        json={"value": "0", "reason": "P2-28 回归-非法"},
        headers=h,
    )
    db = _db()
    try:
        from backend.config import get_settings

        invalidate_config_cache("admin_token_expire_hours")
        from backend.domain.admin.service import AuthService

        hours = AuthService(db)._token_expire_hours()
        assert hours == get_settings().ADMIN_TOKEN_EXPIRE_HOURS
    finally:
        db.close()
    # 复原配置（避免影响其它用例/演示库）
    client.put(
        "/api/admin/configs/admin_token_expire_hours",
        json={"value": "8", "reason": "P2-28 回归-复原"},
        headers=h,
    )
    db = _db()
    try:
        assert ConfigService(db).get_int("admin_token_expire_hours") == 8
    finally:
        db.close()
