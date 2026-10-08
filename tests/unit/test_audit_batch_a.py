# tests/unit/test_audit_batch_a.py — 上线前审查"批次 A"的固化断言
"""审查（docs/专家审查-上线前-2026-09-24.md）发现 → 已修 → 这里钉成机械断言。

覆盖：uploads 路径口径 is_within / 事件总线独立会话 / 任务手动触发权限分级 /
书城 page_size 上限 / 会员期限配置同源 / 媒体 Cache-Control。
每条都对应报告里一个 ID；改坏就红。
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from decimal import Decimal

from fastapi.testclient import TestClient

from backend.common.events import DomainEvent, event_bus
from backend.common.media_paths import is_within
from backend.config import get_settings


def _h(client, username="admin"):
    r = client.post("/api/admin/login", json={"username": username, "password": "dmkwords123"})
    return {"Authorization": f"Bearer {r.json()['token']}"}


def _parent_token(client, h, phone="13800007101"):
    """建家长+孩子并换小程序 token（书城等 miniapp 端点需要家长身份）。"""
    p = client.post(
        "/api/admin/members/parents", json={"name": "批次A家长", "phone": phone}, headers=h
    )
    assert p.status_code == 200, p.text
    c = client.post(
        f"/api/admin/members/parents/{p.json()['id']}/children",
        json={"name": "批次A孩"},
        headers=h,
    )
    assert c.status_code == 200, c.text
    r = client.post("/api/miniapp/login", json={"phone": phone, "code": "1234"})
    assert r.status_code == 200, r.text
    return {"Authorization": f"Bearer {r.json()['token']}"}


# ---------------- 路径口径（报告 P3-1） ----------------


def test_is_within_rejects_sibling_prefix_dir(tmp_path):
    """`uploads-evil/` 与 `uploads` 同前缀——裸 startswith 会放行，is_within 必须拦住。"""
    root = str(tmp_path / "uploads")
    os.makedirs(root)
    assert is_within(root, os.path.join(root, "cover", "a.jpg")) is True
    assert is_within(root, os.path.join(root, "a.jpg")) is True
    assert is_within(root, str(tmp_path / "uploads-evil" / "a.jpg")) is False
    assert is_within(root, root) is False  # 根目录本身不是文件
    assert is_within(root, os.path.join(root, "..", "outside.jpg")) is False


# ---------------- 事件总线独立会话（报告 P1-1） ----------------


def test_event_bus_independent_session_publish():
    """`publish(db=None)`（定时任务场景）必须真能跑——原 `get_session()()` 直接 TypeError。"""
    seen: list[str] = []

    @dataclass
    class _Probe(DomainEvent):
        # 必须 @dataclass：父类 dataclass 生成的 __init__ 会把 event_type 显式赋成 ""，
        # 非 dataclass 子类的类属性会被实例属性遮蔽（实测踩过）
        event_type: str = "audit_batch_a.probe"

    def _handler(event, db) -> None:
        assert db is not None, "独立会话模式下 handler 必须拿到 session"
        seen.append(event.event_type)

    event_bus.subscribe("audit_batch_a.probe", _handler)
    event_bus.publish(_Probe())  # 不传 db → 独立 session 分支
    assert seen == ["audit_batch_a.probe"]


# ---------------- 任务手动触发权限分级（报告 P1-5） ----------------


def test_task_trigger_superadmin_only_for_fund_and_system(client: TestClient):
    staff = _h(client, "staff01")
    admin = _h(client)

    # 系统组（媒体体检）与资金组（订单超时取消）：专员 403、超管 200
    for task in ("media_census", "order_timeout_cancel"):
        r_staff = client.post(f"/api/admin/tasks/{task}/run", headers=staff)
        assert r_staff.status_code == 403, f"{task}: {r_staff.status_code} {r_staff.text}"
        assert "超级管理员" in r_staff.json()["detail"]
        assert client.post(f"/api/admin/tasks/{task}/run", headers=admin).status_code == 200

    # 日常运维组（会员）：专员仍可触发
    assert client.post("/api/admin/tasks/member_expire_check/run", headers=staff).status_code == 200


# ---------------- 书城 page_size 上限（报告 P2-2） ----------------


def test_books_page_size_capped(client: TestClient):
    admin = _h(client)
    mini = _parent_token(client, admin)
    ok = client.get("/api/miniapp/books?page=1&page_size=100", headers=mini)
    assert ok.status_code == 200, ok.text
    over = client.get("/api/miniapp/books?page=1&page_size=101", headers=mini)
    assert over.status_code == 422, over.text


# ---------------- 会员期限配置同源（报告 P2-3） ----------------


def test_membership_period_comes_from_config(client: TestClient, db):
    """观察期时长改配置 → 开通到期日与退款分母同时跟着变（原来硬编码 30/365）。"""
    from backend.common.config_service import ConfigService, invalidate_config_cache
    from backend.domain.identity import refund_rules
    from backend.domain.identity.models import Child, Order
    from backend.seeds.seed_configs import CONFIG_SEEDS

    keys = {row[0] for row in CONFIG_SEEDS}
    assert {"observation_period_days", "formal_period_days"} <= keys, "配置键必须进种子目录"

    admin = _h(client)
    p = client.post(
        "/api/admin/members/parents",
        json={"name": "期限家长", "phone": "13800007102"},
        headers=admin,
    ).json()
    c = client.post(
        f"/api/admin/members/parents/{p['id']}/children",
        json={"name": "期限孩"},
        headers=admin,
    ).json()

    # 把观察期改成 10 天，再开通观察会员 → 到期日应为 10 天后（不是 30）
    ConfigService(db).update_config(
        _admin_row(db), "observation_period_days", "10", "批次A测试：口径同源"
    )
    invalidate_config_cache()
    order = client.post(
        "/api/admin/orders",
        json={"child_id": c["id"], "order_type": "observation_fee"},
        headers=admin,
    ).json()
    confirm = client.post(
        f"/api/admin/orders/{order['id']}/confirm-payment",
        json={"pay_method": "scan"},
        headers=admin,
    )
    assert confirm.status_code == 200, confirm.text
    db.expire_all()
    child = db.query(Child).filter(Child.id == c["id"]).one()
    assert child.member_expire == date.today() + timedelta(days=10)

    # 退款分母同源：0 天已用 → 全退；已用 5 天（分母 10）→ 一半
    paid = db.query(Order).filter(Order.id == order["id"]).one()
    assert refund_rules.refundable_amount(db, paid) == Decimal(str(paid.amount))
    assert "10 天期" in refund_rules.rule_text(db, paid)  # 说明文案也同源
    paid.paid_at = datetime.now() - timedelta(days=5)
    db.commit()
    half = (Decimal(str(paid.amount)) * Decimal(5) / Decimal(10)).quantize(Decimal("0.01"))
    assert refund_rules.refundable_amount(db, paid) == half


def _admin_row(db):
    from backend.domain.admin.models import AdminUser

    return db.query(AdminUser).filter(AdminUser.username == "admin").one()


# ---------------- 媒体 Cache-Control（报告 P2-7） ----------------


def test_media_cache_control_headers(client: TestClient, admin_headers: dict):
    """机器生成名（内容指纹/随机 token）长缓存；就地覆盖的（posters）协商缓存。"""
    root = os.path.abspath(get_settings().UPLOADS_DIR)
    cases = {
        # 生成图/凭证/音频 = `{stem}_{内容指纹或随机token}` → 写完不再变 → 长缓存
        "cover/9999/9780439064873_abcdef123456.jpg": "public, max-age=31536000, immutable",
        "voucher/ORD1_abcdef123456.jpg": "public, max-age=31536000, immutable",
        # 就该回源复验的两类：就地覆盖的（posters）与无可判指纹的
        "posters/poster_1.jpg": "no-cache",
        "observation/child_1/abcdef1234567890.jpg": "no-cache",
    }
    for rel, expected in cases.items():
        full = os.path.join(root, rel)
        os.makedirs(os.path.dirname(full), exist_ok=True)
        with open(full, "wb") as fh:
            fh.write(b"\xff\xd8\xff\xe0" + b"x" * 32)  # JPEG 魔数 + 填充
        resp = client.get(f"/api/admin/uploads/{rel}", headers=admin_headers)
        assert resp.status_code == 200, resp.text
        assert resp.headers.get("cache-control") == expected, f"{rel}: {dict(resp.headers)}"
