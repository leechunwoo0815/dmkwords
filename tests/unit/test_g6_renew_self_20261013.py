# tests/unit/test_g6_renew_self_20261013.py — G6 小程序自助续借（2026-10-13 第五十三轮）
"""PRD §5.1「家长在小程序对**未逾期**的书一键续借」此前**只有馆员代办**（无端点无入口）。

本文件钉住家长自助路径的六条口径 + 书架元数据契约（前端"能不能点续借"的依据）：
① 从**原到期日**加 `renew_days`（不是从续借当天）；② 每本 1 次，二次拒；
③ 逾期拒；④ 冻结期（转让/退会审核中）拒；⑤ 越权与不存在**同回应 404**；
⑥ 馆员代办行为不回归；⑦ 书架在借列表必须给出 `can_renew/renew_days/renew_limit`。

回退即红：删掉 `renew_self` 与端点、或让 `current_borrows` 不再返回续借元数据，本文件相应用例转红。
"""

from datetime import datetime, timedelta

from fastapi.testclient import TestClient

from tests.unit.helpers import force_book_on
from tests.unit.test_wm10_concurrency import _db, _family, _h, _pay, _pay_deposit


def _db_session():
    return _db()


def _member_with_book(client: TestClient, h: dict, phone: str = "13981012301"):
    """正式会员 + 押金已缴 + 借出 1 本（返回 child / mini / record_id / 原到期日）。"""
    p, c, mini = _family(client, h, phone, name="继借孩")
    _pay(client, h, c["id"], "observation_fee")
    _pay(client, h, c["id"], "formal_fee")
    _pay_deposit(client, h, c["id"])
    book_id = client.post(
        "/api/admin/books",
        json={"isbn": None, "title": "续借样例书", "word_count": 100, "copy_count": 1},
        headers=h,
    ).json()["id"]
    force_book_on(client, h, book_id)
    copy_id = client.get(f"/api/admin/books/{book_id}/copies", headers=h).json()[0]["id"]
    r = client.post(
        "/api/admin/circulation/borrow",
        json={"child_id": c["id"], "copy_id": copy_id},
        headers=h,
    )
    assert r.status_code == 200, r.text
    record = r.json()
    return p, c, mini, record


def _renew_self(client: TestClient, mini: dict, child_id: int, record_id: int):
    return client.post(
        f"/api/miniapp/borrows/{record_id}/renew", json={"child_id": child_id}, headers=mini
    )


def test_renew_self_extends_from_original_due(client: TestClient):
    h = _h(client)
    _, child, mini, record = _member_with_book(client, h)
    original_due = datetime.fromisoformat(record["due_at"])

    r = _renew_self(client, mini, child["id"], record["id"])
    assert r.status_code == 200, r.text
    data = r.json()
    new_due = datetime.fromisoformat(data["due_at"])
    assert abs((new_due - original_due).total_seconds() - 7 * 86400) < 2, (
        f"必须从**原到期日**加 7 天：{original_due} → {new_due}"
    )
    assert data["renew_used"] == 1 and data["renew_limit"] == 1, data


def test_renew_self_rejects_second_attempt(client: TestClient):
    h = _h(client)
    _, child, mini, record = _member_with_book(client, h, "13981012302")
    assert _renew_self(client, mini, child["id"], record["id"]).status_code == 200

    again = _renew_self(client, mini, child["id"], record["id"])
    assert again.status_code == 422, again.text
    assert "续借机会已用完" in again.json()["detail"], again.text


def test_renew_self_rejects_overdue(client: TestClient):
    from backend.domain.circulation.models import BorrowRecord

    h = _h(client)
    _, child, mini, record = _member_with_book(client, h, "13981012303")
    with _db_session() as db:
        row = db.query(BorrowRecord).filter(BorrowRecord.id == record["id"]).first()
        row.due_at = datetime.now() - timedelta(days=1)
        row.status = BorrowRecord.STATUS_OVERDUE
        db.commit()

    r = _renew_self(client, mini, child["id"], record["id"])
    assert r.status_code == 422, r.text
    assert "已逾期" in r.json()["detail"], r.text


def test_renew_self_rejected_when_frozen(client: TestClient):
    from backend.domain.identity.models import Child

    h = _h(client)
    _, child, mini, record = _member_with_book(client, h, "13981012304")
    with _db_session() as db:
        db.query(Child).filter(Child.id == child["id"]).update({"operation_locked": 1})
        db.commit()

    r = _renew_self(client, mini, child["id"], record["id"])
    assert r.status_code == 422, r.text
    assert "续借已冻结" in r.json()["detail"], r.text


def test_renew_self_rejects_other_child_record(client: TestClient):
    """越权：拿别人的 record_id 续借 → 与"记录不存在"同回应（不暴露他人记录存在性）。"""
    h = _h(client)
    _, child_a, _, record_a = _member_with_book(client, h, "13981012305")
    _, child_b, mini_b, _ = _member_with_book(client, h, "13981012306")

    r = _renew_self(client, mini_b, child_b["id"], record_a["id"])
    assert r.status_code == 404, r.text
    assert "不存在" in r.json()["detail"], r.text


def test_admin_renew_still_works(client: TestClient):
    """馆员代办不回归：仍可续借 + 仍写管理员审计（action=circulation.renew）。"""
    from backend.common.system_models import AuditLog

    h = _h(client)
    _, child, _, record = _member_with_book(client, h, "13981012307")
    r = client.post("/api/admin/circulation/renew", json={"record_id": record["id"]}, headers=h)
    assert r.status_code == 200, r.text
    with _db_session() as db:
        rows = (
            db.query(AuditLog)
            .filter(
                AuditLog.action == "circulation.renew",
                AuditLog.target_id == str(record["id"]),
            )
            .count()
        )
    assert rows == 1, f"馆员代办必须留审计：{rows}"

    # 而家长自助**不写管理员审计**（口径见 docs/09 第五十三轮：audit_logs.actor_id 是管理员 id，
    # 家长 id 混入会污染操作人追溯；自助事实由 borrow_records.due_at/renew_used 承载）
    _, child2, mini2, record2 = _member_with_book(
        client,
        h,
        "13981012309",
    )
    renewed = _renew_self(client, mini2, child2["id"], record2["id"])
    assert renewed.status_code == 200, renewed.text
    with _db_session() as db:
        rows2 = (
            db.query(AuditLog)
            .filter(
                AuditLog.action == "circulation.renew",
                AuditLog.target_id == str(record2["id"]),
            )
            .count()
        )
    assert rows2 == 0, f"家长自助不应写管理员审计：{rows2}"


def test_shelf_exposes_renew_meta(client: TestClient):
    """书架在借列表必须给出续借元数据（前端按钮依据），且续借后 can_renew 变 false。"""
    h = _h(client)
    _, child, mini, record = _member_with_book(client, h, "13981012308")
    rows = client.get("/api/miniapp/borrows", params={"child_id": child["id"]}, headers=mini).json()
    row = [x for x in rows if x["record_id"] == record["id"]][0]
    assert row["can_renew"] is True and row["renew_days"] == 7 and row["renew_limit"] == 1, row

    assert _renew_self(client, mini, child["id"], record["id"]).status_code == 200
    rows = client.get("/api/miniapp/borrows", params={"child_id": child["id"]}, headers=mini).json()
    row = [x for x in rows if x["record_id"] == record["id"]][0]
    assert row["can_renew"] is False and row["renew_used"] == 1, row
