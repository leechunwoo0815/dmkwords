# tests/unit/test_audit4_p2_batch2_20261017.py — 第四轮大审查·P2 批量（子批 2）回归，2026-10-17
"""第六十九批（子批 2）：P2-16 测验分母 · P2-17 看板「本周新增会员」 · P2-21 任务看板手动触发
可见性 · P2-22 上传响应 copy_count · P2-23 图形区首帧假 0。

每条用例都是报告里那条缺陷的复现或判据的固化，逐条**去修复即红**（变异自证见 docs/09 第六十九轮）。
"""

import pathlib
from datetime import datetime, timedelta

from fastapi.testclient import TestClient

from tests.unit.test_wm10_concurrency import _family, _h

ROOT = pathlib.Path(__file__).resolve().parents[2]


# ---------- P2-17：看板「本周新增会员」= 本周入会的会员（不含未入会建档） ----------


def test_dashboard_member_new_week_excludes_non_member(client: TestClient):
    h = _h(client)
    # ① 只建档、不入会
    _p1, c_none, _mini1 = _family(client, h, "13800008601")
    # ② 建档 + 缴观察期费入会
    _p2, c_member, _mini2 = _family(client, h, "13800008602")
    o = client.post(
        "/api/admin/orders",
        json={"child_id": c_member["id"], "order_type": "observation_fee"},
        headers=h,
    ).json()
    client.post(
        f"/api/admin/orders/{o['id']}/confirm-payment", json={"pay_method": "scan"}, headers=h
    )

    ov = client.get("/api/admin/dashboard", headers=h).json()
    assert ov["member_new_week"] == 1, (
        f"未入会建档不该算新增会员（原先按 create_time 数，同屏 member_total 已排除 none）："
        f"{ov['member_new_week']}"
    )
    # 同屏两个数不再互斥：新增会员必然 ≤ 会员总数（原口径可能新增 > 总数）
    assert ov["member_new_week"] <= ov["member_total"]
    assert c_none["id"] != c_member["id"]


# ---------- P2-16：成长档案「最高分」分母 = 该书当前启用题数 ----------


def _finish_book(client, h, child_id: int, book_id: int, phone: str) -> None:
    """给书上音频 + 让该孩子完播（时间回拨模拟真实流逝）→ 解锁测验。"""
    import io

    mp3 = b"\xff\xfb\x90\x00" + b"\x00" * 125000
    client.post(
        f"/api/admin/books/{book_id}/audio",
        files={"file": ("a.mp3", io.BytesIO(mp3), "audio/mpeg")},
        headers=h,
    )
    from backend.database import get_session
    from backend.domain.catalog.models import Book as BookModel
    from backend.domain.reading.models import ReadingProgress

    with get_session() as db:
        b = db.query(BookModel).filter(BookModel.id == book_id).first()
        b.audio_duration_seconds = 600
        db.commit()
    mini = {
        "Authorization": (
            "Bearer "
            + client.post("/api/miniapp/login", json={"phone": phone, "code": "1234"}).json()[
                "token"
            ]
        )
    }
    client.post(
        "/api/miniapp/reading/progress",
        json={"child_id": child_id, "book_id": book_id, "position": 10, "session_start": 0},
        headers=mini,
    )
    with get_session() as db:
        prog = (
            db.query(ReadingProgress)
            .filter(ReadingProgress.child_id == child_id, ReadingProgress.book_id == book_id)
            .first()
        )
        prog.last_report_at = datetime.now() - timedelta(seconds=590)
        db.commit()
    r = client.post(
        "/api/miniapp/reading/progress",
        json={"child_id": child_id, "book_id": book_id, "position": 600, "session_start": 10},
        headers=mini,
    )
    assert r.status_code == 200, r.text


def _member_child_with_book(client, h, phone: str, title: str, n_questions: int):
    """在会孩子 + 带 n 道题（答案全 A）的新书 → (child, book)。测验题量按配置截断，故
    题库题数与卷面题数可能是两个数——这正是 P2-16 判据要区分的地方。"""
    _p, c, _mini = _family(client, h, phone)
    o = client.post(
        "/api/admin/orders",
        json={"child_id": c["id"], "order_type": "observation_fee"},
        headers=h,
    ).json()
    client.post(
        f"/api/admin/orders/{o['id']}/confirm-payment", json={"pay_method": "scan"}, headers=h
    )
    book = client.post(
        "/api/admin/books",
        json={"isbn": None, "title": title, "word_count": 500},
        headers=h,
    ).json()
    for i in range(n_questions):
        r = client.post(
            f"/api/admin/books/{book['id']}/questions",
            json={
                "question_type": "single",
                "question_text": f"Q{i + 1}?",
                "options": ["A", "B", "C", "D"],
                "answer": "A",
            },
            headers=h,
        )
        assert r.status_code in (200, 201), r.text
    return c, book


def _mini_headers(client, phone: str) -> dict:
    r = client.post("/api/miniapp/login", json={"phone": phone, "code": "1234"})
    return {"Authorization": f"Bearer {r.json()['token']}"}


def test_quiz_overview_returns_total_questions(client: TestClient):
    """3 题的书（题库 < 配置题量）：分母是 3（原先前端硬编码 5 → 显示 `3/5` 假未通过）。"""
    h = _h(client)
    p_phone = "13800008611"
    c, book = _member_child_with_book(client, h, p_phone, "三题书", 3)
    _finish_book(client, h, c["id"], book["id"], p_phone)
    mini = _mini_headers(client, p_phone)

    q = client.get(f"/api/miniapp/quiz/{book['id']}?child_id={c['id']}", headers=mini).json()
    assert q["unlocked"] is True and len(q["questions"]) == 3
    sub = client.post(
        f"/api/miniapp/quiz/{book['id']}/submit",
        json={"child_id": c["id"], "answers": ["A", "A", "A"]},
        headers=mini,
    )
    assert sub.status_code == 200, sub.text
    assert sub.json()["score"] == 3

    growth = client.get(f"/api/admin/children/{c['id']}/growth", headers=h).json()
    row = next(r for r in growth["quiz_overview"] if r["book_id"] == book["id"])
    assert row["total_questions"] == 3, f"分母必须是卷面题量：{row}"
    assert row["best_score"] == 3

    # 前端用后端给的分母渲染（不许再出现硬编码 5）
    page = (ROOT / "admin-web/src/pages/GrowthManage.tsx").read_text(encoding="utf-8")
    assert "r.total_questions" in page
    assert "? 5 :" not in page


def test_quiz_overview_denominator_follows_attempt_snapshot(client: TestClient):
    """题量配置=8 时：卷面 8 题，分母必须是 8（旧的前端硬编码 5 会显示 `8/5`/`5/5` 假满分）。

    注意分母来源是**那次作答的题量快照**（`QuizAttempt.total_questions`）——服务端出题按
    `min(题库启用题数, quiz_questions_per_book)` 截断，题库题数与卷面题数并不相等。
    """
    h = _h(client)
    p_phone = "13800008612"
    c, book = _member_child_with_book(client, h, p_phone, "八题书", 8)
    client.put(
        "/api/admin/configs/quiz_questions_per_book",
        json={"value": "8", "reason": "P2-16 回归：题量改 8"},
        headers=h,
    )
    try:
        _finish_book(client, h, c["id"], book["id"], p_phone)
        mini = _mini_headers(client, p_phone)
        q = client.get(f"/api/miniapp/quiz/{book['id']}?child_id={c['id']}", headers=mini).json()
        assert len(q["questions"]) == 8, "配置 8 → 卷面 8 题"
        sub = client.post(
            f"/api/miniapp/quiz/{book['id']}/submit",
            json={"child_id": c["id"], "answers": ["A"] * 8},
            headers=mini,
        )
        assert sub.status_code == 200, sub.text
        growth = client.get(f"/api/admin/children/{c['id']}/growth", headers=h).json()
        row = next(r for r in growth["quiz_overview"] if r["book_id"] == book["id"])
        assert row["total_questions"] == 8, f"分母必须是卷面题量快照：{row}"
        assert row["best_score"] == 8
    finally:
        client.put(
            "/api/admin/configs/quiz_questions_per_book",
            json={"value": "5", "reason": "P2-16 回归：题量复原"},
            headers=h,
        )


# ---------- P2-21：任务看板手动触发按角色可见（判据与服务层强制点同源） ----------


def test_task_specs_manual_allowed_by_role(client: TestClient):
    h_super = _h(client, "admin")
    h_staff = _h(client, "staff01")
    super_items = client.get("/api/admin/tasks", headers=h_super).json()["items"]
    staff_items = client.get("/api/admin/tasks", headers=h_staff).json()["items"]
    assert super_items and staff_items

    super_by = {s["name"]: s for s in super_items}
    staff_by = {s["name"]: s for s in staff_items}
    assert all(s["manual_allowed"] is True for s in super_items), "超管全部可触发"
    # 资金/系统组对专员必须为 False（此前 UI 全量露出 → 点了才吃 403）
    blocked = [n for n, s in staff_by.items() if s["group"] in ("资金", "系统")]
    assert blocked, "样本里应有资金/系统组任务"
    assert all(staff_by[n]["manual_allowed"] is False for n in blocked)
    allowed = [n for n, s in staff_by.items() if s["group"] not in ("资金", "系统")]
    assert allowed and all(staff_by[n]["manual_allowed"] is True for n in allowed)
    # 提示不许放宽真正的闸：专员真去触发 → 仍 403
    money_task = sorted(blocked)[0]
    assert client.post(f"/api/admin/tasks/{money_task}/run", headers=h_staff).status_code == 403
    assert super_by[money_task]["manual_allowed"] is True

    # 前端必须消费该字段（不许自己再写一套角色判断）
    page = (ROOT / "admin-web/src/pages/TaskBoard.tsx").read_text(encoding="utf-8")
    assert "manual_allowed === false" in page and "disabled" in page


def test_manual_trigger_rule_single_source():
    """判据只有一份实现：`TaskAdminService.manual_trigger_allowed` 同时被 run() 与 specs 用。"""
    import backend.domain.admin.service as admin_service
    from backend.domain.admin.models import AdminUser
    from backend.domain.admin.service import TaskAdminService as Svc
    from backend.domain.admin.task_specs import TaskAdminService as SpecSvc

    assert Svc.manual_trigger_allowed("资金", AdminUser.ROLE_SUPER_ADMIN) is True
    assert Svc.manual_trigger_allowed("资金", AdminUser.ROLE_STAFF) is False
    assert Svc.manual_trigger_allowed("会员", AdminUser.ROLE_STAFF) is True
    # P2-21（第九轮 D 批）：原先这里是**源码文本计数**（`impl.count("SUPERADMIN_ONLY_TASK_GROUPS = ")
    # == 1` + 另一文件里 `not in`）——文本断言脆（改注释/换行即红）且**不证明行为**。
    # 改成模块级**属性**判定：常量必须存在于 task_specs、不得存在于旧实现处。
    # （两个入口的一致性由上面三条行为断言 + 本文件另一处 run 端点的 403/200 用例共同保证。）
    assert hasattr(SpecSvc, "SUPERADMIN_ONLY_TASK_GROUPS"), "分组常量应在 task_specs"
    assert not hasattr(admin_service, "SUPERADMIN_ONLY_TASK_GROUPS"), (
        "同一口径不许两份实现：旧实现里的分组常量必须删净"
    )


# ---------- P2-22：封面/音频上传响应的 copy_count 必须是真实在册数 ----------


def _jpeg_bytes() -> bytes:
    import io

    from PIL import Image

    buf = io.BytesIO()
    Image.new("RGB", (16, 16), (10, 20, 30)).save(buf, "JPEG")
    return buf.getvalue()


def test_upload_cover_returns_real_copy_count(client: TestClient):
    h = _h(client)
    book = client.post(
        "/api/admin/books",
        json={"isbn": None, "title": "上传响应回归", "word_count": 100, "copy_count": 3},
        headers=h,
    ).json()
    r = client.post(
        f"/api/admin/books/{book['id']}/cover",
        files={"file": ("c.jpg", _jpeg_bytes(), "image/jpeg")},
        headers=h,
    )
    assert r.status_code == 200, r.text
    assert r.json()["copy_count"] == 3, "上传响应不许写死 0（字段声明是「在册副本总数」）"

    # P2-21（第九轮 D 批）：原先这里数源码里 `_book_response_fresh(db, book)` 出现 5 次
    # （"1 处定义 + 4 处调用"）——源码计数是弱证据且会随重构漂移。
    # 改成**逐个端点实测**：四个出参点都必须回重算后的在册数。
    got = client.get(f"/api/admin/books/{book['id']}", headers=h).json()["copy_count"]
    assert got == 3, f"详情端点应在册数 3，实 {got}"
    upd = client.put(
        f"/api/admin/books/{book['id']}",
        json={"title": "上传响应回归·改", "word_count": 100},
        headers=h,
    )
    assert upd.status_code == 200, upd.text
    assert upd.json()["copy_count"] == 3, f"编辑端点应在册数 3，实 {upd.json()['copy_count']}"
    mp3 = b"\xff\xfb\x90\x00" + b"\x00" * 125000
    aud = client.post(
        f"/api/admin/books/{book['id']}/audio",
        files={"file": ("a.mp3", mp3, "audio/mpeg")},
        headers=h,
    )
    assert aud.status_code == 200, aud.text
    assert aud.json()["copy_count"] == 3, f"音频上传端点应在册数 3，实 {aud.json()['copy_count']}"


# ---------- P2-23：图形区首帧失败禁假 0 ----------


def test_dashboard_charts_first_frame_failure_not_fake_zero():
    src = (ROOT / "admin-web/src/components/DashboardCharts.tsx").read_text(encoding="utf-8")
    assert "setChartsFailed(true)" in src and "ChartsPlaceholder" in src
    assert "charts?.borrow_trend ?? []" not in src, "首帧失败不许走空数组（= 假 0）"
    assert "chart-empty" in src  # 空态仍保留给「确实没数据」的成功帧
    assert "图表加载失败" in src, "首帧失败必须如实说失败（投屏 U10：失败禁假 0）"
