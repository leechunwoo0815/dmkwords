# tests/unit/test_a2_closure_20261009.py — 上线前闭环 A2（2026-10-09，8 项）
"""A2 组（加固与体验，零依赖）逐条回归：

① 家长 token 可撤销（退出登录 = 服务端代数 +1，旧 token 401）
② 受保护目录不从通用上传端点下发（voucher/、observation/ 走各自鉴权端点）
③ 服务协议文案与实际一致（不再写"不提供在线支付"/只写短信登录）
④ 动态类名补样式（wd-{status} 三档、cat-{catKey} 八类）
⑤ 死代码清理 + `api.listBooks` 参数形状（首页推荐多拉了两倍多）
⑥ 转让超时**条件 UPDATE**：已被审核抢先的单不许被翻回 expired
⑦ 退款域 BDD 解封（5 场景走真实链路）
"""

from __future__ import annotations

import os
from datetime import datetime, timedelta
from pathlib import Path

from sqlalchemy import text

from backend.domain.identity.models import TransferRequest
from backend.domain.identity.transfer_service import TransferService
from tests.unit.test_wm12_payment import _family, _h

ROOT = Path(__file__).resolve().parents[2]


# ---------- ① token 撤销 ----------


def test_parent_token_revoked_after_logout(client, db):
    """退出登录必须**服务端撤销**：旧 token 立即 401，重新登录的新 token 可用。"""
    _parent, _child, mini = _family(client, phone="13800009951")
    assert client.get("/api/miniapp/children", headers=mini).status_code == 200

    assert client.post("/api/miniapp/logout", headers=mini).status_code == 200
    assert client.get("/api/miniapp/children", headers=mini).status_code == 401, (
        "撤销后旧 token 必须失效（原先 30 天不可撤销）"
    )

    relogin = client.post(
        "/api/miniapp/login", json={"phone": "13800009951", "code": "1234"}
    ).json()
    fresh = {"Authorization": f"Bearer {relogin['token']}"}
    assert client.get("/api/miniapp/children", headers=fresh).status_code == 200


# ---------- ② 受保护目录 ----------


def test_admin_uploads_refuses_protected_dirs(client):
    """受保护目录（voucher/ 等）不从通用上传端点下发；公开目录照常。"""
    from backend.config import get_settings

    root = get_settings().UPLOADS_DIR
    h = _h(client)
    made: list[str] = []
    try:
        for rel in ("voucher/__a2_probe.txt", "cover/__a2_probe.txt"):
            full = os.path.join(root, rel)
            os.makedirs(os.path.dirname(full), exist_ok=True)
            with open(full, "w", encoding="utf-8") as fh:
                fh.write("probe")
            made.append(full)
        assert client.get("/api/admin/uploads/voucher/__a2_probe.txt", headers=h).status_code == 404
        assert client.get("/api/admin/uploads/cover/__a2_probe.txt", headers=h).status_code == 200
    finally:
        for full in made:
            if os.path.isfile(full):
                os.remove(full)


# ---------- ③ 文案 ----------


def test_service_agreement_matches_reality():
    wxml = (ROOT / "miniapp/pages/agreement/service-agreement/service-agreement.wxml").read_text(
        encoding="utf-8"
    )
    assert "不提供在线支付" not in wxml, "文案不得再写死'不提供在线支付'（已接微信支付）"
    assert "微信一键登录" in wxml, "登录方式必须包含微信一键登录（主通道）"


# ---------- ④ 动态类名 ----------


def test_dynamic_class_variants_defined():
    wd = (ROOT / "miniapp/pages/order-pkg/withdrawal/withdrawal.wxss").read_text(encoding="utf-8")
    for name in ("wd-applying", "wd-pending_settle", "wd-refunding"):
        assert f".wd-status.{name}" in wd, f"{name} 必须有样式定义（模板用 wd-{{{{item.status}}}}）"
    msg = (ROOT / "miniapp/pages/order-pkg/messages/messages.wxss").read_text(encoding="utf-8")
    for key in ("money", "borrow", "reading", "activity", "report", "reserve", "other"):
        assert f".msg-cat-tag.cat-{key}" in msg, f"cat-{key} 必须有样式定义"


# ---------- ⑤ 死代码 + listBooks ----------


def test_dead_utils_removed_and_listbooks_call_fixed():
    for name in ("auth", "consent", "security", "storage", "subscribe"):
        assert not (ROOT / f"miniapp/utils/{name}.js").exists(), f"零引用 utils/{name}.js 应删除"
    index_js = (ROOT / "miniapp/pages/index/index.js").read_text(encoding="utf-8")
    assert "api.listBooks(''" not in index_js, "listBooks 必须按对象参数调用（原按位置传 → 多拉）"
    assert "page_size: 6" in index_js


# ---------- ⑥ 转让超时条件 UPDATE ----------


def test_transfer_expire_skips_row_approved_concurrently(client, db, monkeypatch):
    """A 行推进后（同一次扫描内）B 行已被审核批准 → B 不许被翻回 expired。"""
    h = _h(client)
    parent, child, _mh = _family(client, phone="13800009952")
    sibling = client.post(
        f"/api/admin/members/parents/{parent['id']}/children", json={"name": "受让孩"}, headers=h
    ).json()
    overdue = datetime.now() - timedelta(hours=2)
    ids = []
    for _ in range(2):
        tr = TransferRequest(
            source_child_id=child["id"],
            target_child_id=sibling["id"],
            status=TransferRequest.STATUS_PENDING,
            expires_at=overdue,
            review_remark="A2 超时并发",
        )
        db.add(tr)
        db.flush()
        ids.append(tr.id)
    db.commit()

    svc = TransferService(db)
    real_unlock = svc._unlock_both
    calls: list[int] = []

    def _unlock_then_approve_other(req):
        calls.append(req.id)
        real_unlock(req)
        if len(calls) == 1:
            # 模拟"审核抢先"：另一张单在同一次扫描过程中被批准（独立会话 + 提交）
            from backend.database import SessionLocal

            other = SessionLocal()
            try:
                other.execute(
                    text("UPDATE transfer_requests SET status='approved' WHERE id=:i"),
                    {"i": ids[1]},
                )
                other.commit()
            finally:
                other.close()

    monkeypatch.setattr(svc, "_unlock_both", _unlock_then_approve_other)
    expired = svc.expire_overdue()

    assert expired == 1, f"只应推进仍 pending 的那张（实推进 {expired}）"
    db.expire_all()  # 独立会话刚改过库：清 ORM 快照/身份映射，读库内事实
    rows = {
        r.id: r.status for r in db.query(TransferRequest).filter(TransferRequest.id.in_(ids)).all()
    }
    assert rows[ids[1]] == TransferRequest.STATUS_APPROVED, "已批准的单不得被超时扫描翻回"


# ---------- ⑦ BDD 解封 ----------


def test_refund_bdd_unfrozen():
    feature = (ROOT / "features/refund.feature").read_text(encoding="utf-8")
    assert "\n@draft\n功能" not in feature, "feature 级 @draft 必须移除（5 场景已实现）"
    for scenario in (
        "场景: 提交退款申请",
        "场景: 同一订单禁止重复申请",
        "场景: 待审核状态下家长可撤销",
        "场景: 审核拒绝后可再次申请",
        "场景: 审核通过执行原路退款",
    ):
        idx = feature.index(scenario)
        assert "@draft" not in feature[max(0, idx - 20) : idx], f"{scenario} 不应再标 draft"
    assert (ROOT / "features/steps/refund_steps.py").is_file()
