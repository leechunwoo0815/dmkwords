# tests/unit/test_borrow_records.py — 借还记录查询与导出（2026-09-21 任务包 D 批）
"""锁四件事（用户拍板）：① 借出/归还都能按条件查到；② **归还操作人**真的记下来了
（历史行没记的显示"未记录"，不编造）；③ 副本码不再恒为空；④ 导出的是**当前筛选结果**。"""

from io import BytesIO

from fastapi.testclient import TestClient

from tests.unit.test_wm10_concurrency import _family, _h, _pay, _pay_deposit


def _book(client: TestClient, h: dict, isbn: str, title: str) -> int:
    r = client.post(
        "/api/admin/books", json={"isbn": isbn, "title": title, "word_count": 100}, headers=h
    )
    assert r.status_code == 200, r.text
    return r.json()["id"]


def _ready_child(client: TestClient, h: dict, phone: str, name: str):
    _p, c, mini = _family(client, h, phone, name)
    _pay(client, h, c["id"], "observation_fee")
    _pay_deposit(client, h, c["id"])
    return c, mini


def _scan(client: TestClient, h: dict, child_id: int, code: str):
    return client.post(
        "/api/admin/circulation/scan", json={"child_id": child_id, "code": code}, headers=h
    )


def test_records_show_borrow_and_return_with_operator(client: TestClient):
    """借出 + 归还各一条记录，且**记下归还操作人**（本轮新增列的意义所在）。"""
    h = _h(client)
    c, _mini = _ready_child(client, h, "13981016001", "记录孩")
    _book(client, h, "9780394800201", "记录书")

    assert _scan(client, h, c["id"], "9780394800201").json()["action"] == "borrow"
    assert _scan(client, h, c["id"], "9780394800201").json()["action"] == "return"

    r = client.get("/api/admin/circulation/records?page=1&page_size=20", headers=h)
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["total"] == 1
    row = body["items"][0]
    assert row["child_name"] == "记录孩"
    assert row["status"] == "returned"
    assert row["returned_at"], "归还时间必须落库"
    assert row["copy_code"], "副本码不该是空串（旧逾期接口的坑别复制过来）"
    assert row["borrowed_by_name"] == "超级管理员"
    assert row["returned_by_name"] == "超级管理员", "2026-09-21 新增：归还操作人必须记下"


def test_records_filters(client: TestClient):
    """关键词（孩子名/书名/手机号/副本码）、状态、时间段四个维度都能筛。"""
    h = _h(client)
    c1, _m1 = _ready_child(client, h, "13981016002", "筛选孩甲")
    c2, _m2 = _ready_child(client, h, "13981016003", "筛选孩乙")
    _book(client, h, "9780394800202", "筛选书甲")
    _book(client, h, "9780394800203", "筛选书乙")
    _scan(client, h, c1["id"], "9780394800202")  # 甲借甲（仍在借）
    _scan(client, h, c2["id"], "9780394800203")
    _scan(client, h, c2["id"], "9780394800203")  # 乙借又还

    def ids(qs: str) -> set[str]:
        body = client.get(
            f"/api/admin/circulation/records?page=1&page_size=20&{qs}", headers=h
        ).json()
        return {r["child_name"] for r in body["items"]}

    assert ids("keyword=筛选孩甲") == {"筛选孩甲"}
    assert ids("keyword=9780394800203") == {"筛选孩乙"}
    assert ids("keyword=13981016002") == {"筛选孩甲"}
    assert ids("status=active") == {"筛选孩甲"}
    assert ids("status=returned") == {"筛选孩乙"}
    assert ids("status=active&date_field=borrowed&date_from=2000-01-01T00:00:00") == {"筛选孩甲"}
    assert ids("status=active&date_field=borrowed&date_from=2999-01-01T00:00:00") == set()
    # 非法参数不静默：状态白名单 + date_field 白名单（沿用项目惯例抛 422）
    assert (
        client.get("/api/admin/circulation/records?status=nonsense", headers=h).status_code == 422
    )
    assert (
        client.get("/api/admin/circulation/records?date_field=whatever", headers=h).status_code
        == 422
    )


def test_records_export_excel_follows_filter(client: TestClient):
    """导出的是**当前筛选结果**（筛完再导，导的必须是他看到的那批）。"""
    h = _h(client)
    c1, _m1 = _ready_child(client, h, "13981016004", "导出孩甲")
    c2, _m2 = _ready_child(client, h, "13981016005", "导出孩乙")
    _book(client, h, "9780394800204", "导出书甲")
    _book(client, h, "9780394800205", "导出书乙")
    _scan(client, h, c1["id"], "9780394800204")
    _scan(client, h, c2["id"], "9780394800205")

    r = client.get("/api/admin/circulation/records/export?keyword=导出孩甲", headers=h)
    assert r.status_code == 200, r.text
    assert "spreadsheet" in r.headers["content-type"]
    from openpyxl import load_workbook

    ws = load_workbook(BytesIO(r.content)).active
    names = [ws.cell(row=i, column=2).value for i in range(2, ws.max_row + 1)]
    assert names == ["导出孩甲"], f"导出应只含筛选命中的行，实得 {names}"
    assert ws.cell(row=1, column=14).value == "归还操作人", "表头必须含归还操作人"
