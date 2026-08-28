# tests/unit/test_wm2_d1d3.py — WM2 追加 D1 上架强校验（D2/D3 为前端小改，走验收表）
import io

import pytest
from fastapi.testclient import TestClient
from PIL import Image


def _h(client, username="admin") -> dict:
    r = client.post("/api/admin/login", json={"username": username, "password": "dmkwords123"})
    return {"Authorization": f"Bearer {r.json()['token']}"}


def _create_book(client, h, **over) -> dict:
    body = {"isbn": None, "title": "D1 Book", "word_count": 100, "copy_count": 1}
    body.update(over)
    resp = client.post("/api/admin/books", json=body, headers=h)
    assert resp.status_code == 200, resp.text
    return resp.json()


def _jpg_bytes(color="red") -> bytes:
    img = Image.new("RGB", (10, 10), color=color)
    buf = io.BytesIO()
    img.save(buf, format="JPEG")
    return buf.getvalue()


def _mp3_bytes(n: int = 125000) -> bytes:
    return b"\xff\xfb\x90\x00" + b"\x00" * n


def _upload_cover(client, h, book_id):
    return client.post(
        f"/api/admin/books/{book_id}/cover",
        files={"file": ("c.jpg", _jpg_bytes(), "image/jpeg")},
        headers=h,
    )


def _upload_audio(client, h, book_id):
    return client.post(
        f"/api/admin/books/{book_id}/audio",
        files={"file": ("a.mp3", io.BytesIO(_mp3_bytes()), "audio/mpeg")},
        headers=h,
    )


def _add_questions(client, h, book_id, n: int):
    for i in range(n):
        r = client.post(
            f"/api/admin/books/{book_id}/questions",
            json={
                "question_type": "single",
                "question_text": f"Q{i}",
                "options": ["A", "B"],
                "answer": "A",
            },
            headers=h,
        )
        assert r.status_code == 200, r.text


def test_d1_create_defaults_off(client: TestClient):
    """D1：新书一律下架入库。"""
    h = _h(client)
    book = _create_book(client, h)
    assert book["status"] == 0


def test_d1_import_defaults_off(client: TestClient):
    """D1：Excel 导入同样默认下架。"""
    from openpyxl import Workbook

    h = _h(client)
    wb = Workbook()
    ws = wb.active
    ws.append(["ISBN", "书名*", "作者", "AR值", "词数*", "主题", "适读阶段", "副本数"])
    ws.append([None, "导入书", "", "3.5", "100", "", "", "1"])
    buf = io.BytesIO()
    wb.save(buf)
    buf.seek(0)
    resp = client.post(
        "/api/admin/books/import",
        files={"file": ("t.xlsx", buf, "application/vnd.ms-excel")},
        headers=h,
    )
    assert resp.status_code == 200
    assert resp.json()["success_count"] == 1
    book = client.get("/api/admin/books", params={"keyword": "导入书"}, headers=h).json()["items"][
        0
    ]
    assert book["status"] == 0


def test_d1_toggle_on_blocked_incomplete(client: TestClient):
    """不完整书上架 → 409 + detail 含「未传封面」与书名；状态保持下架。"""
    h = _h(client)
    book = _create_book(client, h, title="不完整书")
    resp = client.post(f"/api/admin/books/{book['id']}/toggle-status", headers=h)
    assert resp.status_code == 409, resp.text
    detail = resp.json()["detail"]
    assert "无法上架" in detail
    assert "不完整书" in detail
    assert "未传封面" in detail
    assert client.get(f"/api/admin/books/{book['id']}", headers=h).json()["status"] == 0


def _complete_book(client, h, skip: str) -> dict:
    """造一本五项全齐的书；skip 指定故意缺的项（cover/audio/ar/quiz）。"""
    book = _create_book(client, h, title=f"完整书-{skip}", ar_level="3.5")
    if skip != "cover":
        assert _upload_cover(client, h, book["id"]).status_code == 200
    if skip != "audio":
        assert _upload_audio(client, h, book["id"]).status_code == 200
    if skip == "ar":
        # 清空 AR（schema 允许 None）
        r = client.put(
            f"/api/admin/books/{book['id']}",
            json={"isbn": None, "title": f"完整书-{skip}", "word_count": 100, "ar_level": None},
            headers=h,
        )
        assert r.status_code == 200
    if skip != "quiz":
        _add_questions(client, h, book["id"], 5)
    return book


@pytest.mark.parametrize(
    "skip,fragment",
    [
        ("cover", "未传封面"),
        ("audio", "未传音频"),
        ("ar", "未配置 AR 值"),
        ("quiz", "未满 5 道测验题（当前 0 道）"),
    ],
)
def test_d1_five_checks_parametrized(client: TestClient, skip: str, fragment: str):
    h = _h(client)
    book = _complete_book(client, h, skip=skip)
    resp = client.post(f"/api/admin/books/{book['id']}/toggle-status", headers=h)
    assert resp.status_code == 409, resp.text
    assert fragment in resp.json()["detail"]


def test_d1_quiz_missing_count_in_message(client: TestClient):
    """测验项提示带当前启用数：3 道 → 「未满 5 道测验题（当前 3 道）」。"""
    h = _h(client)
    book = _complete_book(client, h, skip="quiz")
    _add_questions(client, h, book["id"], 3)
    resp = client.post(f"/api/admin/books/{book['id']}/toggle-status", headers=h)
    assert resp.status_code == 409
    assert "未满 5 道测验题（当前 3 道）" in resp.json()["detail"]


def test_d1_complete_book_can_onboard(client: TestClient):
    """五项全齐 → 上架 200，状态转 1。"""
    h = _h(client)
    book = _complete_book(client, h, skip="")
    resp = client.post(f"/api/admin/books/{book['id']}/toggle-status", headers=h)
    assert resp.status_code == 200, resp.text
    assert resp.json()["status"] == 1


def test_d1_switch_off_bypasses_check(client: TestClient):
    """配置开关关闭 → 不完整书也可上架（演示/特殊场景）。"""
    h = _h(client)
    book = _create_book(client, h, title="开关关上架")
    r = client.put(
        "/api/admin/configs/book_onboarding_check",
        json={"value": "false", "reason": "演示场景"},
        headers=h,
    )
    assert r.status_code == 200, r.text
    resp = client.post(f"/api/admin/books/{book['id']}/toggle-status", headers=h)
    assert resp.status_code == 200, resp.text
    # 恢复开关
    client.put(
        "/api/admin/configs/book_onboarding_check",
        json={"value": "true", "reason": "恢复默认"},
        headers=h,
    )


def test_d1_batch_toggle_mixed_partial_success(client: TestClient):
    """batch-toggle status=1：完整书成功、不完整书进失败明细（部分成功语义）。"""
    h = _h(client)
    ok_book = _complete_book(client, h, skip="")
    bad_book = _create_book(client, h, title="批量里的半成品")
    resp = client.post(
        "/api/admin/books/batch-toggle-status",
        json={"ids": [ok_book["id"], bad_book["id"]], "status": 1},
        headers=h,
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["success"] == 1
    assert data["failed"] == 1
    assert any(str(bad_book["id"]) in e and "未传封面" in e for e in data["errors"])


def test_d1_batch_toggle_off_no_check(client: TestClient):
    """批量下架方向不校验：不完整书也能下架（本就下架，幂等成功）。"""
    h = _h(client)
    book = _create_book(client, h)
    resp = client.post(
        "/api/admin/books/batch-toggle-status", json={"ids": [book["id"]], "status": 0}, headers=h
    )
    assert resp.status_code == 200
    assert resp.json()["success"] == 1


def test_d1_detail_missing_field(client: TestClient):
    """GET /books/{id}：下架态返回 missing 数组；上架态返回 []。"""
    h = _h(client)
    book = _create_book(client, h, title="缺失清单书")
    detail = client.get(f"/api/admin/books/{book['id']}", headers=h).json()
    assert any("未传封面" in m for m in detail["missing"])
    assert any("未传音频" in m for m in detail["missing"])

    complete = _complete_book(client, h, skip="")
    client.put(
        "/api/admin/configs/book_onboarding_check",
        json={"value": "false", "reason": "测试"},
        headers=h,
    )
    client.post(f"/api/admin/books/{complete['id']}/toggle-status", headers=h)
    client.put(
        "/api/admin/configs/book_onboarding_check",
        json={"value": "true", "reason": "恢复"},
        headers=h,
    )
    detail2 = client.get(f"/api/admin/books/{complete['id']}", headers=h).json()
    assert detail2["missing"] == []
