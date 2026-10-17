# tests/unit/test_audit4_fix2_20261017.py — 第四轮大审查·第二批修复回归（2026-10-17）
"""第六十七批：P1-4（逾期续借回 ACTIVE）+ P1-9/P2-7（副本状态收窄）+ P2-8（CAS 写）
+ P1-7（调度器锁互斥）+ P1-8（小程序退出撤销时序）+ P1-10（管理端报损入口）。

每条都是报告里那条缺陷的复现（亲验探针转正），逐条"去修复即红"。
"""

import shutil
import subprocess
import threading
from datetime import datetime, timedelta
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from tests.unit.test_wm10_concurrency import _db, _h

ROOT = Path(__file__).resolve().parents[2]


def _parent_child(client, h, phone="13800008401", name="二批孩", member=True, deposit=False):
    p = client.post(
        "/api/admin/members/parents", json={"name": "二批家长", "phone": phone}, headers=h
    ).json()
    c = client.post(
        f"/api/admin/members/parents/{p['id']}/children", json={"name": name}, headers=h
    ).json()
    if member:
        o = client.post(
            "/api/admin/orders",
            json={"child_id": c["id"], "order_type": "observation_fee"},
            headers=h,
        ).json()
        client.post(
            f"/api/admin/orders/{o['id']}/confirm-payment", json={"pay_method": "scan"}, headers=h
        )
    if deposit:
        d = client.post(f"/api/admin/deposits/children/{c['id']}/orders", headers=h).json()
        client.post(
            f"/api/admin/orders/{d['order_id']}/confirm-payment",
            json={"pay_method": "scan"},
            headers=h,
        )
    m = client.post("/api/miniapp/login", json={"phone": phone, "code": "1234"}).json()
    return p, c, {"Authorization": f"Bearer {m['token']}"}


def _book(client, h, title, copies=1):
    b = client.post(
        "/api/admin/books",
        json={"isbn": None, "title": title, "word_count": 1000, "copy_count": copies},
        headers=h,
    )
    assert b.status_code == 200, b.text
    book = b.json()
    return book, client.get(f"/api/admin/books/{book['id']}/copies", headers=h).json()


# ---------- P1-4：逾期人工续借后状态回 ACTIVE，提醒链恢复 ----------


def test_overdue_renew_sets_status_active_and_reminder_resumes(client: TestClient):
    h = _h(client)
    _p, c, _mini = _parent_child(client, h, deposit=True)
    _book_row, cp = _book(client, h, "逾期续借回归书")
    rec = client.post(
        "/api/admin/circulation/borrow",
        json={"child_id": c["id"], "copy_id": cp[0]["id"]},
        headers=h,
    ).json()
    from backend.domain.circulation.models import BorrowRecord

    with _db() as db:  # 造真逾期态（状态由逾期标记任务落库，不能只拨 due_at）
        db.query(BorrowRecord).filter(BorrowRecord.id == rec["id"]).update(
            {"due_at": datetime.now() - timedelta(days=3), "status": "overdue"}
        )
        db.commit()
    # 续借天数改 1：新到期日 = 明天，正好落在「1 天」提醒窗口里，用来证明提醒链真的恢复
    cfg = client.put(
        "/api/admin/configs/renew_days",
        json={"value": "1", "reason": "回归：验证续借后到期提醒恢复"},
        headers=h,
    )
    assert cfg.status_code == 200, cfg.text
    r = client.post(
        "/api/admin/circulation/renew",
        json={"record_id": rec["id"], "reason": "回归：逾期人工续借"},
        headers=h,
    )
    assert r.status_code == 200, r.text
    assert r.json()["status"] == "active", "续借后必须回到借出中（否则提醒/逾期标记永久跳过它）"
    with _db() as db:
        row = db.query(BorrowRecord).filter(BorrowRecord.id == rec["id"]).first()
        assert row.status == "active" and row.due_at > datetime.now(), (row.status, row.due_at)
        from backend.domain.circulation.service import CirculationService

        sent = CirculationService(db).book_due_remind()
    assert sent >= 1, "续借后到期提醒必须重新发得出去（原先状态卡 overdue → 永久静默）"


# ---------- P1-9 / P2-7：副本状态收窄到运维安全子集 ----------


def test_copy_status_rejects_workflow_owned_states(client: TestClient):
    h = _h(client)
    _book_row, cp = _book(client, h, "副本状态收窄回归书")
    copy_id = cp[0]["id"]
    for bad in ("borrowed", "reserved"):
        r = client.put(
            f"/api/admin/copies/{copy_id}/status",
            json={"status": bad, "reason": "回归：试图裸写工作流状态"},
            headers=h,
        )
        assert r.status_code == 422, (bad, r.status_code, r.text)
        assert "流程" in r.json()["detail"], r.text
    # 运维安全子集仍然可用
    ok = client.put(
        f"/api/admin/copies/{copy_id}/status",
        json={"status": "maintenance", "reason": "回归：转维护"},
        headers=h,
    )
    assert ok.status_code == 200 and ok.json()["status"] == "maintenance", ok.text


def test_copy_status_release_lock_still_allowed(client: TestClient):
    """「释放锁定」（reserved→available）是合法运维动作，收窄后不能被误伤。"""
    h = _h(client)
    _p, c, mini = _parent_child(client, h, deposit=True)
    _book_row, cp = _book(client, h, "释放锁定回归书")
    copy_id = cp[0]["id"]
    res = client.post(
        "/api/miniapp/reservations",
        json={"child_id": c["id"], "book_id": _book_row["id"]},
        headers=mini,
    )
    assert res.status_code == 200, res.text
    from backend.domain.catalog.models import BookCopy

    with _db() as db:
        assert db.query(BookCopy).filter(BookCopy.id == copy_id).first().status == "reserved"
    rel = client.put(
        f"/api/admin/copies/{copy_id}/status",
        json={"status": "available", "reason": "回归：释放锁定"},
        headers=h,
    )
    assert rel.status_code == 200 and rel.json()["status"] == "available", rel.text


# ---------- P2-8：终局写是条件 UPDATE（并发改动 → 显式 409，不静默覆盖） ----------


def test_copy_status_cas_rejects_concurrent_change(client: TestClient, monkeypatch):
    """模拟"守卫读完之后、写入之前"另一个会话把副本借走（真并发的最坏时序）。"""
    h = _h(client)
    _book_row, cp = _book(client, h, "CAS 回归书")
    copy_id = cp[0]["id"]
    from backend.domain.catalog.models import BookCopy
    from backend.domain.catalog.service import BookService

    orig = BookService._active_borrow_of

    def racing(self, cid):  # noqa: ANN001
        with _db() as db:  # 并发会话：副本被借出（borrow 会持 copy 行锁并提交 borrowed）
            db.query(BookCopy).filter(BookCopy.id == cid).update({"status": "borrowed"})
            db.commit()
        return orig(self, cid)

    monkeypatch.setattr(BookService, "_active_borrow_of", racing)
    r = client.put(
        f"/api/admin/copies/{copy_id}/status",
        json={"status": "maintenance", "reason": "回归：并发覆盖"},
        headers=h,
    )
    assert r.status_code == 409, (r.status_code, r.text)
    assert "并发" in r.json()["detail"], r.text
    with _db() as db:  # 并发方写入的状态没被覆盖
        assert db.query(BookCopy).filter(BookCopy.id == copy_id).first().status == "borrowed"


def test_report_damage_cas_rejects_concurrent_change(client: TestClient, monkeypatch):
    """报损链同款 CAS：并发把副本借走之后，报损不能静默覆盖成维护/遗失。"""
    h = _h(client)
    _book_row, cp = _book(client, h, "报损 CAS 回归书")
    copy_id = cp[0]["id"]
    from backend.domain.catalog.models import BookCopy
    from backend.domain.catalog.service import BookService

    orig = BookService._active_borrow_of

    def racing(self, cid):  # noqa: ANN001
        with _db() as db:
            db.query(BookCopy).filter(BookCopy.id == cid).update({"status": "borrowed"})
            db.commit()
        return orig(self, cid)

    monkeypatch.setattr(BookService, "_active_borrow_of", racing)
    r = client.post(
        f"/api/admin/copies/{copy_id}/report-damage",
        json={"kind": "lost", "reason": "回归：并发覆盖"},
        headers=h,
    )
    assert r.status_code == 409, (r.status_code, r.text)
    with _db() as db:
        assert db.query(BookCopy).filter(BookCopy.id == copy_id).first().status == "borrowed"


# ---------- P1-7：调度器单实例锁的共享连接必须串行化 ----------


def test_scheduler_lock_is_thread_safe():
    """8 线程 × 30 次核锁：全部为真且零异常（此前同一 Connection 并发使用 → 协议错乱）。"""
    from backend.tasks import registry as registry_mod

    orig_name = registry_mod.SCHEDULER_LOCK_NAME
    registry_mod.SCHEDULER_LOCK_NAME = "dmkwords:test:audit4-fix2"
    registry_mod._lock_conn = None
    try:
        assert registry_mod._acquire_scheduler_lock(), "测试锁应能获取（库是通的）"
        errors: list[str] = []
        results: list[bool] = []

        def worker():
            for _ in range(30):
                try:
                    results.append(registry_mod._holds_scheduler_lock())
                except Exception as exc:  # noqa: BLE001
                    errors.append(f"{type(exc).__name__}: {str(exc)[:80]}")
                    return

        threads = [threading.Thread(target=worker) for _ in range(8)]
        [t.start() for t in threads]
        [t.join() for t in threads]
        assert not errors, errors[:3]
        assert len(results) == 8 * 30 and all(results), (len(results), results.count(False))
    finally:
        registry_mod.release_scheduler_lock()
        registry_mod.SCHEDULER_LOCK_NAME = orig_name


# ---------- P1-8：小程序退出登录必须"带 token 发撤销请求" ----------


LOGOUT_HARNESS = r"""
const path = require('path');
const Module = require('module');

const seen = [];
let appState = { token: 'T0KEN-BEFORE-LOGOUT', userInfo: {}, currentChild: {} };

global.wx = {
  getStorageSync: (k) => (k === 'token' ? appState.token : ''),
  removeStorageSync: (k) => { if (k === 'token') appState.token = ''; },
  getNetworkType: (opts) => setTimeout(() => opts.success({ networkType: 'wifi' }), 5),
  request: (opts) => {
    seen.push(opts.header && opts.header.Authorization ? opts.header.Authorization : null);
    setTimeout(() => opts.success({ statusCode: 200, data: { ok: true } }), 5);
  },
  showToast: () => {}, hideLoading: () => {}, showLoading: () => {},
  reLaunch: () => {},
};
global.getApp = () => ({ globalData: {
  baseURL: 'http://test', token: appState.token, userInfo: appState.userInfo,
  currentChild: appState.currentChild,
} });
// getApp() 每次返回**同一个** globalData 对象，才能观察到清理
const app = { globalData: { baseURL: 'http://test', token: appState.token } };
global.getApp = () => app;
global.getCurrentPages = () => [];

const session = require(path.join(process.argv[2], 'miniapp', 'utils', 'session.js'));
const api = require(path.join(process.argv[2], 'miniapp', 'utils', 'api.js'));

session.logout();
setTimeout(() => {
  console.log(JSON.stringify({
    authHeaders: seen,
    tokenAfter: app.globalData.token,
    apiHasLogout: typeof api.logout === 'function',
  }));
}, 60);
"""


def test_miniapp_logout_sends_token_before_clearing(tmp_path: Path):
    """P1-8：撤销请求必须**带 token 发出**（原先先清本地 token → 请求 401 → 服务端撤销从不执行）。"""
    node = shutil.which("node")
    if not node:
        pytest.skip("node 不可用（CI/本机缺 node）")
    harness = tmp_path / "logout_harness.js"
    harness.write_text(LOGOUT_HARNESS, encoding="utf-8")
    proc = subprocess.run(
        [node, str(harness), str(ROOT)],
        capture_output=True,
        text=True,
        timeout=60,
        cwd=str(ROOT),
    )
    assert proc.returncode == 0, proc.stderr[-500:]
    import json

    out = json.loads(proc.stdout.strip().splitlines()[-1])
    assert out["apiHasLogout"] is True
    assert out["authHeaders"] == ["Bearer T0KEN-BEFORE-LOGOUT"], out["authHeaders"]
    assert out["tokenAfter"] == "", "清本地必须发生在撤销请求之后"


# ---------- P1-10：管理端「报损·遗失」入口（静态接线 + tsc 双保险） ----------


def test_admin_web_report_damage_entry_wired():
    api_src = (ROOT / "admin-web/src/api/catalog.ts").read_text(encoding="utf-8")
    page_src = (ROOT / "admin-web/src/pages/BookDetail.tsx").read_text(encoding="utf-8")
    assert "export function apiReportCopyDamage(" in api_src
    assert "/report-damage" in api_src
    assert "apiReportCopyDamage" in page_src and "报损·遗失" in page_src
