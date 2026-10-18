# tests/unit/test_audit5_p1_batch1_20261018.py — 第五轮大审查·P1 批量（子批 1：软删书目同 ISBN）
"""第五/六/七轮结转的 P1-4：软删书目同 ISBN 重新入库不得裸 500。

`books.isbn` 是**单列唯一索引（不含 is_deleted）**，而服务层查重只查未删行
（`BaseRepository.get_by_field` 自动过滤软删）→ 软删后同号重建必撞 1062 → 500。
与 `parents.phone`（第六十九批 P2-6）同形，同法处置：**同号复活原行**。

三条用例逐条**去修复即红**（变异自证见 docs/09 第七十五轮）：
  1. 建书 → 软删 → 同 ISBN 再建：复活原行（id 不变）/ 200 / 回下架态 / 副本码不撞；
  2. 软删书占用的 ISBN 被**另一本书** update 抢占：409（不得 5xx）；
  3. 未删的重号仍 409（既有行为守护）。
"""

from __future__ import annotations

from fastapi.testclient import TestClient

ISBN_A = "9780194789999"
ISBN_B = "9780194788888"


def _h(client: TestClient, username: str = "admin") -> dict:
    r = client.post("/api/admin/login", json={"username": username, "password": "dmkwords123"})
    assert r.status_code == 200, r.text
    return {"Authorization": f"Bearer {r.json()['token']}"}


def _book_body(isbn: str | None, title: str, copies: int = 2) -> dict:
    return {
        "isbn": isbn,
        "title": title,
        "author": "审查测试",
        "word_count": 1200,
        "ar_level": "2.5",
        "topic": "动物",
        "grade": "A",
        "description": "",
        "copy_count": copies,
    }


def _update_body(isbn: str | None, title: str) -> dict:
    """BookUpdateRequest 无 copy_count（extra=forbid）。"""
    body = _book_body(isbn, title)
    body.pop("copy_count")
    return body


def _create(client: TestClient, h: dict, isbn: str | None, title: str, copies: int = 2):
    return client.post("/api/admin/books", json=_book_body(isbn, title, copies), headers=h)


def test_soft_deleted_book_same_isbn_create_revives(client: TestClient):
    """软删后同 ISBN 再建 = 复活原行（不 5xx、不新建行、副本码不撞）。"""
    h = _h(client)
    r1 = _create(client, h, ISBN_A, "同号复活书")
    assert r1.status_code == 200, r1.text
    b1 = r1.json()
    assert client.delete(f"/api/admin/books/{b1['id']}", headers=h).status_code == 200

    r2 = _create(client, h, ISBN_A, "同号复活书·重入库")
    assert r2.status_code == 200, f"软删后同 ISBN 重建必须成功（复活），实际 {r2.status_code}: {r2.text}"
    b2 = r2.json()
    assert b2["id"] == b1["id"], "同号必须复活原行（保副本与借阅史），不得新建行"
    assert b2["title"] == "同号复活书·重入库", "复活应被当次请求的元数据覆盖"
    assert b2["status"] == 0, "复活后回到下架态：媒体已在软删时清除，必须重新完善再上架"
    assert b2["isbn"] == ISBN_A

    copies = client.get(f"/api/admin/books/{b1['id']}/copies", headers=h).json()
    codes = sorted(c["copy_code"] for c in copies)
    assert len(codes) == 4, f"老 2 本 + 新 2 本 = 4 本，实际 {codes}"
    assert len(set(codes)) == 4, f"副本码不得相撞（next_copy_code 计入全部行）：{codes}"


def test_isbn_owned_by_soft_deleted_book_blocks_update(client: TestClient):
    """改号改到"已删书占用"的 ISBN：如实 409 + 可行动文案（不得裸 500）。"""
    h = _h(client)
    a = _create(client, h, ISBN_A, "被删的书").json()
    b = _create(client, h, ISBN_B, "要改号的书").json()
    assert client.delete(f"/api/admin/books/{a['id']}", headers=h).status_code == 200

    r = client.put(
        f"/api/admin/books/{b['id']}", json=_update_body(ISBN_A, "要改号的书"), headers=h
    )
    assert r.status_code == 409, f"改到已删书占用的 ISBN 应是 409，实际 {r.status_code}: {r.text}"
    assert "已删除" in r.text, r.text


def test_duplicate_active_isbn_still_conflict(client: TestClient):
    """既有行为守护：未删重号仍是 409（不是 5xx，也不是复活）。"""
    h = _h(client)
    assert _create(client, h, ISBN_A, "先来的").status_code == 200
    r = _create(client, h, ISBN_A, "后到的")
    assert r.status_code == 409, r.text


def test_excel_import_same_isbn_of_soft_deleted_book_row_errors(client: TestClient):
    """对偶面：Excel 导入同族——软删书占用的 ISBN 落**行级错误**，不得整批 500。"""
    import io

    from openpyxl import Workbook

    h = _h(client)
    a = _create(client, h, ISBN_A, "被删的书").json()
    assert client.delete(f"/api/admin/books/{a['id']}", headers=h).status_code == 200

    wb = Workbook()
    ws = wb.active
    ws.append(["ISBN", "书名", "作者", "AR值", "总词数", "主题", "年级", "副本数量"])
    ws.append([ISBN_A, "被删的书", "", "", 1000, "", "", 1])  # 撞软删行占用
    ws.append([ISBN_B, "正常新书", "", "", 900, "", "", 1])  # 同批其余行照常入账
    buf = io.BytesIO()
    wb.save(buf)
    buf.seek(0)
    resp = client.post(
        "/api/admin/books/import",
        files={"file": ("books.xlsx", buf, "application/octet-stream")},
        headers=h,
    )
    assert resp.status_code == 200, f"整批不得 500：{resp.status_code} {resp.text}"
    body = resp.json()
    assert body["success_count"] == 1, body
    assert body["failed_count"] == 1, body
    assert "已删除" in body["errors"][0], body["errors"]
    # 行级错误不得把整批回滚掉：正常那行必须真的入库
    assert client.get("/api/admin/books", params={"keyword": "正常新书"}, headers=h).json()[
        "total"
    ] == 1
