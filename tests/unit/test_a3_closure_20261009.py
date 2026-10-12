# tests/unit/test_a3_closure_20261009.py — 上线前闭环 A3（2026-10-09，第一批 4 项）
"""A3 组第一批（性能/运维里的"小改动高价值"四条）：

① 家长缺 Authorization 头 → **401**（原先 FastAPI 参数校验拦成 422，与"未登录"语义不符）
② 管理端登录**时序不可观测**：未知用户名也要跑一次 PBKDF2（原先短路返回 → 响应时间暴露账号是否存在）
③ 借阅到期提醒**补发窗口**：停机一天后仍能发出（原先按节点日精确匹配 → 永久漏发），且不重复发
④ `renew_limit` 配置**活着**（原先写死 >= 1，运营改配置无效）

未做（如实）：三处 N+1、计数类 check-then-act、孤儿文件清理、admin-web 代码分割、
`detail` 全量 CSV 导出、任务留痕可观测性——见交接 v58 §三 与 `docs/审查报告-全量-2026-10-09.md` §四。
"""

from __future__ import annotations

from datetime import date, datetime, timedelta

from sqlalchemy import text

from tests.unit.test_wm10_concurrency import _db, _family, _h

# ---------- ① 401 语义 ----------


def test_missing_auth_header_returns_401(client):
    r = client.get("/api/miniapp/children")
    assert r.status_code == 401, f"缺头应为 401（未登录语义），实 {r.status_code}"
    assert "登录" in r.json()["detail"]


def test_empty_bearer_returns_401(client):
    r = client.get("/api/miniapp/children", headers={"Authorization": "Bearer "})
    assert r.status_code == 401


# ---------- ② 登录时序 ----------


def test_admin_login_runs_password_hash_for_unknown_user(client, monkeypatch):
    """未知用户名也必须跑一次 PBKDF2（等价耗时），否则可枚举账号。"""
    from backend.domain.admin import service as admin_service

    calls: list[str] = []
    real = admin_service.verify_password

    def counting(plain: str, hashed: str) -> bool:
        calls.append(hashed)
        return real(plain, hashed)

    monkeypatch.setattr(admin_service, "verify_password", counting)
    r = client.post("/api/admin/login", json={"username": "__no_such_user__", "password": "x"})
    assert r.status_code == 401
    assert len(calls) == 1, "未知用户名路径必须仍做一次等价校验（防时序侧信道）"


# ---------- ③ 到期提醒补发窗口 ----------


def _borrow_record(child_id: int, due_at: datetime, status: str = "active") -> int:
    from backend.domain.catalog.models import Book
    from backend.domain.circulation.models import BorrowRecord

    with _db() as db:
        book = db.query(Book).first()
        rec = BorrowRecord(
            child_id=child_id,
            book_id=book.id if book else 1,
            copy_id=1,
            status=status,
            borrowed_at=datetime.now() - timedelta(days=10),
            due_at=due_at,
            renew_used=0,
        )
        db.add(rec)
        db.flush()
        rid = rec.id
        db.commit()
        return rid


def _reminders_for(child_id: int) -> list[str]:
    with _db() as db:
        rows = db.execute(
            text(
                "SELECT content FROM notifications WHERE child_id=:c AND ref_type='borrow_record' "
                "ORDER BY id"
            ),
            {"c": child_id},
        ).fetchall()
        return [r[0] for r in rows]


def test_book_due_reminder_catches_up_after_downtime(client):
    """节点日昨天已过（停机一天）→ 今天仍要发出，文案按**实际剩余天数**算，且不重复。"""
    h = _h(client)
    _p, child, _mini = _family(client, h, "13981037301", name="小明")
    # 真实补发场景：节点表只有"1 天后"，而这本书**昨天就该命中 1 天节点**（昨天它=1 天后），
    # 但任务昨天没跑（停机）→ 今天它已经"今天到期"。精确匹配的旧实现永久漏发；
    # 带 1 天补发窗口的新实现今天仍发出，文案按实际天数说"今天"。
    with _db() as db:
        db.execute(
            text("UPDATE system_configs SET config_value='1' WHERE config_key='due_remind_days'")
        )
        db.commit()
    _borrow_record(child["id"], datetime.now() + timedelta(hours=2))

    from backend.domain.circulation.service import CirculationService

    with _db() as db:
        sent = CirculationService(db).book_due_remind()
    assert sent == 1, "补发窗口应让该记录被发出（原先精确匹配节点日会漏）"
    contents = _reminders_for(child["id"])
    assert len(contents) == 1 and "今天" in contents[0], contents

    with _db() as db:
        again = CirculationService(db).book_due_remind()
    assert again == 0 and len(_reminders_for(child["id"])) == 1, "补发不得重复发（幂等键=节点值）"


# ---------- ④ renew_limit 配置活着 ----------


def test_renew_limit_reads_config(client):
    """`renew_limit` 改成 2 后，续借一次不再被"限 1 次"拦（配置真的生效）。"""
    h = _h(client)
    _p, child, _mini = _family(client, h, "13981037302", name="小明")
    rec_id = _borrow_record(child["id"], datetime.now() + timedelta(days=5))
    with _db() as db:
        db.execute(
            text("UPDATE system_configs SET config_value='2' WHERE config_key='renew_limit'")
        )
        db.commit()
        from backend.common.config_service import invalidate_config_cache

        invalidate_config_cache()

    from backend.domain.circulation.service import CirculationService
    from backend.domain.identity.models import Child

    with _db() as db:
        kid = db.query(Child).filter(Child.id == child["id"]).first()
        kid.member_status = "formal"
        kid.member_start = date.today()
        kid.member_expire = date.today() + timedelta(days=200)
        db.commit()

    admin = type("A", (), {"id": 0, "display_name": "A3", "role": "superadmin"})()
    with _db() as db:
        CirculationService(db).renew(admin, rec_id)  # 第一次：允许
    with _db() as db:
        try:
            CirculationService(db).renew(admin, rec_id)  # 第二次：仍允许（配置=2）
        except Exception as exc:  # noqa: BLE001
            raise AssertionError(f"renew_limit=2 时第二次续借不该被拒：{exc}") from exc
    with _db() as db:
        row = db.execute(
            text("SELECT renew_used, due_at FROM borrow_records WHERE id=:i"), {"i": rec_id}
        ).fetchone()
    assert row[0] == 2, "两次续借都应记账"
