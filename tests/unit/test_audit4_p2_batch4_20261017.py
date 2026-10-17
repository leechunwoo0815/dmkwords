# tests/unit/test_audit4_p2_batch4_20261017.py — 第四轮大审查·P2 批量（子批 4）回归，2026-10-17
"""第六十九批（子批 4）：P2-6 软删家长同号再建档 · P2-12 转让并发锁/条件解锁 · P2-20 副本码冲突。

每条用例都是报告里那条缺陷的复现或判据的固化，逐条**去修复即红**（变异自证见 docs/09 第六十九轮）。
"""

import io
import threading
import time
from datetime import datetime, timedelta

from fastapi.testclient import TestClient

from tests.unit.test_wm10_concurrency import _db, _h

# ---------- P2-6：软删家长后同手机号再建档（唯一索引是全局的） ----------


def test_create_parent_revives_soft_deleted_phone(client: TestClient):
    """软删家长 → 同手机号再建档：原先撞 `parents.phone` 唯一索引 → 500；现在就地复活。"""
    h = _h(client)
    p = client.post(
        "/api/admin/members/parents", json={"name": "旧名字", "phone": "13800008801"}, headers=h
    ).json()
    assert client.delete(f"/api/admin/members/parents/{p['id']}", headers=h).status_code == 200

    r = client.post(
        "/api/admin/members/parents", json={"name": "新名字", "phone": "13800008801"}, headers=h
    )
    assert r.status_code == 200, f"同号再建档不许 500：{r.status_code} {r.text}"
    body = r.json()
    assert body["id"] == p["id"], "同一手机号 = 同一个人：应复活原档案（而不是新建/500）"
    assert body["name"] == "新名字", "复活时按本次建档覆盖姓名"

    # 审计留痕标注了"复活"（可追溯）
    from backend.domain.admin.models import AuditLog

    db = _db()
    try:
        row = (
            db.query(AuditLog)
            .filter(AuditLog.action == "parent.create", AuditLog.target_id == str(p["id"]))
            .order_by(AuditLog.id.desc())
            .first()
        )
    finally:
        db.close()
    assert row is not None and "revived_soft_deleted" in (row.detail or "")


# ---------- P2-12a：转让申请并发串行化（Child 行锁） ----------


def _transfer_ready(client, h, phone: str):
    """可转让的一对：src 正式会员 + 押金、tgt 未入会（条件全过）。返回 (parent, src, tgt, mini)。"""
    p = client.post(
        "/api/admin/members/parents", json={"name": "转让家长", "phone": phone}, headers=h
    ).json()
    src = client.post(
        f"/api/admin/members/parents/{p['id']}/children", json={"name": "大孩"}, headers=h
    ).json()
    tgt = client.post(
        f"/api/admin/members/parents/{p['id']}/children", json={"name": "二孩"}, headers=h
    ).json()
    o = client.post(
        "/api/admin/orders", json={"child_id": src["id"], "order_type": "formal_fee"}, headers=h
    ).json()
    client.post(
        f"/api/admin/orders/{o['id']}/confirm-payment", json={"pay_method": "scan"}, headers=h
    )
    d = client.post(f"/api/admin/deposits/children/{src['id']}/orders", headers=h).json()
    client.post(
        f"/api/admin/orders/{d['order_id']}/confirm-payment", json={"pay_method": "scan"}, headers=h
    )
    mini = {
        "Authorization": (
            "Bearer "
            + client.post("/api/miniapp/login", json={"phone": phone, "code": "1234"}).json()[
                "token"
            ]
        )
    }
    return p, src, tgt, mini


def test_transfer_apply_serialized_by_child_row_lock(client: TestClient):
    """并发双击：第二个申请必须等第一个的 Child 行锁，并在锁后看到 `operation_locked=1` 而失败。

    RED（还原修复：apply 不做锁定读）：第二个请求在第一个提交前就插单成功 → 两条 pending。
    """
    from backend.common.exceptions import ValidationError

    h = _h(client)
    p, src, tgt, mini = _transfer_ready(client, h, "13800008811")
    # 条件自检（确保是"只差锁"的场景）
    cond = client.get(
        f"/api/miniapp/transfers/conditions?source_child_id={src['id']}&target_child_id={tgt['id']}",
        headers=mini,
    ).json()
    assert [c["name"] for c in cond["conditions"] if not c["ok"]] == []

    # S1：占住"先被锁的那一行"（按 id 升序，两个请求都会先锁较小的 id）
    from sqlalchemy import text

    from backend.database import get_session
    from backend.domain.identity.models import Child, Parent
    from backend.domain.identity.transfer_service import TransferService

    s1 = get_session()
    try:
        lock_id = sorted([src["id"], tgt["id"]])[0]
        s1.execute(text("SELECT id FROM children WHERE id = :i FOR UPDATE"), {"i": lock_id})
        result: dict = {}

        def _apply():
            s2 = get_session()
            try:
                parent = s2.query(Parent).filter(Parent.id == p["id"]).first()
                TransferService(s2).apply(parent, src["id"], tgt["id"])
                result["ok"] = True
            except Exception as exc:  # noqa: BLE001 — 断言在下面
                result["error"] = exc
            finally:
                s2.rollback()
                s2.close()

        th = threading.Thread(target=_apply)
        th.start()
        time.sleep(0.6)
        assert th.is_alive(), "第二个申请必须被行锁挡住（原先会直接插单）"
        # 第一个申请"落库"：锁住的孩子进入冻结
        s1.query(Child).filter(Child.id == lock_id).update(
            {Child.operation_locked: 1}, synchronize_session=False
        )
        s1.commit()
        th.join(timeout=10)
    finally:
        s1.rollback()
        s1.close()

    assert not th.is_alive(), "行锁释放后第二个申请应当结束"
    assert "error" in result, f"锁后应看到冻结态并拒绝：{result}"
    assert isinstance(result["error"], (ValidationError,))
    assert "冻结" in str(result["error"])


def test_cancel_keeps_lock_when_another_pending_transfer_exists(client: TestClient):
    """两条在途转让牵涉同一孩子：撤销其中一条**不许**解除冻结（P2-12 的第二半）。"""
    from backend.domain.identity.models import Child, Parent, TransferRequest
    from backend.domain.identity.transfer_service import TransferService

    h = _h(client)
    p, src, tgt, _mini = _transfer_ready(client, h, "13800008812")
    # 第三个孩子做第二条申请的受让方
    tgt2 = client.post(
        f"/api/admin/members/parents/{p['id']}/children", json={"name": "三孩"}, headers=h
    ).json()

    db = _db()
    try:
        r1 = TransferRequest(
            source_child_id=src["id"],
            target_child_id=tgt["id"],
            expires_at=datetime.now() + timedelta(hours=24),
        )
        r2 = TransferRequest(
            source_child_id=src["id"],
            target_child_id=tgt2["id"],
            expires_at=datetime.now() + timedelta(hours=24),
        )
        db.add_all([r1, r2])
        db.query(Child).filter(Child.id == src["id"]).update(
            {Child.operation_locked: 1}, synchronize_session=False
        )
        db.commit()
        r1_id, r2_id = r1.id, r2.id
    finally:
        db.close()

    db = _db()
    try:
        parent = db.query(Parent).filter(Parent.id == p["id"]).first()
        TransferService(db).cancel(parent, r1_id)
        src_row = db.query(Child).filter(Child.id == src["id"]).first()
        db.refresh(src_row)
        assert src_row.operation_locked == 1, "另一条仍在审核 → 冻结必须保留"
    finally:
        db.close()

    db = _db()
    try:
        parent = db.query(Parent).filter(Parent.id == p["id"]).first()
        TransferService(db).cancel(parent, r2_id)
        src_row = db.query(Child).filter(Child.id == src["id"]).first()
        db.refresh(src_row)
        assert src_row.operation_locked == 0, "没有在途申请了 → 解锁"
    finally:
        db.close()


# ---------- P2-20：副本码全局唯一冲突 → 409（不是 500），导入不丢整批 ----------


def _create_book(client, h, isbn: str, title: str, copies: int = 1):
    return client.post(
        "/api/admin/books",
        json={"isbn": isbn, "title": title, "word_count": 100, "copy_count": copies},
        headers=h,
    )


def test_copy_code_collision_returns_409(client: TestClient):
    """ISBN 前缀不同、后 8 位相同 → 副本码撞全局唯一索引：必须 409 且给出可执行指引。"""
    h = _h(client)
    r1 = _create_book(client, h, "9781234567890", "先建的书")
    assert r1.status_code == 200, r1.text
    r2 = _create_book(client, h, "9701234567890", "后建的书（后 8 位相同）")
    assert r2.status_code == 409, f"不许裸 500：{r2.status_code} {r2.text}"
    assert "副本编码冲突" in r2.json()["detail"]
    # 第一本不受影响（事务边界干净）
    assert client.get(f"/api/admin/books/{r1.json()['id']}", headers=h).status_code == 200
    # 冲突书没有被半建出来（书目行也回滚）
    lst = client.get("/api/admin/books?keyword=后建的书", headers=h).json()
    items = lst.get("items", lst if isinstance(lst, list) else [])
    assert all(it["title"] != "后建的书（后 8 位相同）" for it in items), "冲突行不许留下半本书"


def test_import_row_collision_does_not_lose_batch(client: TestClient):
    """Excel 导入：一行撞副本码 → 该行报错，其余行照常导入（原先整批 500、结果全丢）。"""
    import openpyxl

    from backend.domain.catalog.import_service import HEADER

    h = _h(client)
    assert _create_book(client, h, "9781234567890", "已在库的书").status_code == 200

    wb = openpyxl.Workbook()
    ws = wb.active
    ws.append(HEADER)
    # 第 1 行：撞码（同后 8 位）
    ws.append(["9701234567890", "撞码书", "作者", "", 100, "", "", 1])
    # 第 2 行：正常
    ws.append(["9789999999999", "正常导入书", "作者", "", 120, "", "", 1])
    buf = io.BytesIO()
    wb.save(buf)
    buf.seek(0)

    r = client.post(
        "/api/admin/books/import",
        files={
            "file": (
                "books.xlsx",
                buf.getvalue(),
                "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            )
        },
        headers=h,
    )
    assert r.status_code == 200, f"导入整批不许 500：{r.status_code} {r.text}"
    body = r.json()
    assert body["success_count"] == 1, body
    assert body["failed_count"] == 1 and "副本码冲突" in body["errors"][0], body
    # 正常那本真的进库了
    lst = client.get("/api/admin/books?keyword=正常导入书", headers=h).json()
    items = lst.get("items", lst if isinstance(lst, list) else [])
    assert any(it["title"] == "正常导入书" for it in items)
