# tests/unit/test_wm2_p1_fixes.py — WM2 标杆定稿 P1 修复（R1/R2/R7/R9）
import io

import pytest
from fastapi.testclient import TestClient
from PIL import Image
from sqlalchemy.orm import Session

from backend.common.system_models import AuditLog
from backend.config import get_settings


def _h(client) -> dict:
    r = client.post("/api/admin/login", json={"username": "admin", "password": "dmkwords123"})
    return {"Authorization": f"Bearer {r.json()['token']}"}


def _create_book(client, h, **over) -> dict:
    body = {"isbn": None, "title": "P1 Fix", "word_count": 100, "copy_count": 1}
    body.update(over)
    resp = client.post("/api/admin/books", json=body, headers=h)
    assert resp.status_code == 200, resp.text
    return resp.json()


def _jpg_bytes(color="red") -> bytes:
    img = Image.new("RGB", (10, 10), color=color)
    buf = io.BytesIO()
    img.save(buf, format="JPEG")
    return buf.getvalue()


# ---------- R1 清空 ISBN 后补生成 internal_code ----------


def test_r1_clear_isbn_generates_internal_code(client: TestClient):
    """R1：创建时有 ISBN 的书，编辑清空 ISBN 后必须补生成 LOCAL-{id:06d}。"""
    h = _h(client)
    book = _create_book(client, h, isbn="9780545582889", title="Has ISBN")
    assert book["internal_code"] is None  # 创建时无内部编号

    resp = client.put(
        f"/api/admin/books/{book['id']}",
        json={
            "isbn": None,
            "title": "Has ISBN",
            "author": "A",
            "word_count": 100,
            "ar_level": None,
            "topic": "",
            "grade": "",
            "description": None,
        },
        headers=h,
    )
    assert resp.status_code == 200, resp.text
    data = resp.json()
    assert data["isbn"] is None
    assert data["internal_code"] == f"LOCAL-{book['id']:06d}"


# ---------- R2 上传媒体 commit 失败不删旧文件 ----------


def test_r2_upload_cover_keeps_old_file_when_commit_fails(
    client: TestClient, db: Session, monkeypatch
):
    """R2：commit 失败时旧文件必须还在（删除只允许发生在 commit 成功后）。"""
    import os

    from backend.domain.admin.models import AdminUser
    from backend.domain.catalog.service import BookService

    h = _h(client)
    book = _create_book(client, h, title="R2 Cover")

    resp = client.post(
        f"/api/admin/books/{book['id']}/cover",
        files={"file": ("a.jpg", _jpg_bytes("red"), "image/jpeg")},
        headers=h,
    )
    assert resp.status_code == 200
    path1 = resp.json()["cover_path"]
    root = os.path.abspath(get_settings().UPLOADS_DIR)
    assert os.path.isfile(os.path.join(root, path1))

    admin = db.query(AdminUser).filter_by(username="admin").first()

    def _boom() -> None:
        raise RuntimeError("commit failed")

    monkeypatch.setattr(db, "commit", _boom)
    svc = BookService(db)
    with pytest.raises(RuntimeError):
        svc.upload_cover(admin, book["id"], _jpg_bytes("blue"), ".jpg")
    db.rollback()

    # commit 失败：旧文件必须还在（若先删后 commit 则此断言失败）
    assert os.path.isfile(os.path.join(root, path1)), "commit 失败不应删除旧封面文件"


def test_r2_reupload_removes_old_after_commit(client: TestClient):
    """R2：重传成功（commit 后）旧文件被删、DB 路径为新值。"""
    import os

    from backend.config import get_settings

    h = _h(client)
    book = _create_book(client, h, title="R2 Reupload")

    r1 = client.post(
        f"/api/admin/books/{book['id']}/cover",
        files={"file": ("a.jpg", _jpg_bytes("red"), "image/jpeg")},
        headers=h,
    )
    r2 = client.post(
        f"/api/admin/books/{book['id']}/cover",
        files={"file": ("b.jpg", _jpg_bytes("blue"), "image/jpeg")},
        headers=h,
    )
    assert r1.status_code == 200 and r2.status_code == 200
    path1, path2 = r1.json()["cover_path"], r2.json()["cover_path"]
    assert path1 != path2
    root = os.path.abspath(get_settings().UPLOADS_DIR)
    assert not os.path.isfile(os.path.join(root, path1))
    assert os.path.isfile(os.path.join(root, path2))


# ---------- R7 AR 值三路校验 ----------


@pytest.mark.parametrize("bad", ["abc", "1.2.3", "-1", "13.0", "99"])
def test_r7_schema_rejects_dirty_ar(client: TestClient, bad: str):
    """R7：create 路径脏 AR（格式非法或 >12.9）必须 422。"""
    h = _h(client)
    resp = client.post(
        "/api/admin/books",
        json={"isbn": None, "title": f"bad {bad}", "word_count": 100, "ar_level": bad},
        headers=h,
    )
    assert resp.status_code == 422, f"ar_level={bad} 应被拒绝"


@pytest.mark.parametrize("good", ["0", "4.5", "12.9"])
def test_r7_schema_accepts_valid_ar(client: TestClient, good: str):
    """R7：合法 AR（0-12.9）必须通过。"""
    h = _h(client)
    resp = client.post(
        "/api/admin/books",
        json={"isbn": None, "title": f"ok {good}", "word_count": 100, "ar_level": good},
        headers=h,
    )
    assert resp.status_code == 200, resp.text


def test_r7_update_path_rejects_dirty_ar(client: TestClient):
    """R7：update 路径同样拦截脏 AR。"""
    h = _h(client)
    book = _create_book(client, h, title="Update AR")
    resp = client.put(
        f"/api/admin/books/{book['id']}",
        json={
            "isbn": None,
            "title": "Update AR",
            "author": "",
            "word_count": 100,
            "ar_level": "abc",
            "topic": "",
            "grade": "",
            "description": None,
        },
        headers=h,
    )
    assert resp.status_code == 422


def test_r7_import_rejects_dirty_ar_rows(client: TestClient):
    """R7：导入路径行级校验报「第N行：AR 值…」，合法行不受影响。"""
    from openpyxl import Workbook

    h = _h(client)
    wb = Workbook()
    ws = wb.active
    ws.append(["ISBN", "书名*", "作者", "AR值", "词数*", "主题", "适读阶段", "副本数"])
    ws.append([None, "AR坏格式", "", "abc", "100", "", "", "1"])
    ws.append([None, "AR超上限", "", "13.5", "100", "", "", "1"])
    ws.append([None, "AR合法", "", "4.5", "100", "", "", "1"])
    buf = io.BytesIO()
    wb.save(buf)
    buf.seek(0)

    resp = client.post(
        "/api/admin/books/import",
        files={"file": ("t.xlsx", buf, "application/vnd.ms-excel")},
        headers=h,
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["success_count"] == 1
    assert data["failed_count"] == 2
    errs = [e for e in data["errors"] if "AR" in e]
    assert len(errs) == 2, data["errors"]
    assert any("第1行" in e and "AR 值格式不正确" in e for e in errs)
    assert any("第2行" in e and "AR 值超出范围" in e for e in errs)


# ---------- R9 QuizQuestion 四写操作审计 ----------


def _audit_actions(db: Session) -> set:
    return {a for (a,) in db.query(AuditLog.action).filter(AuditLog.action.like("quiz.%")).all()}


def test_r9_quiz_question_write_ops_are_audited(client: TestClient, db: Session):
    """R9：题目 create/update/toggle/delete 四操作各留一条审计。"""
    h = _h(client)
    book = _create_book(client, h, title="R9 Quiz")

    resp = client.post(
        f"/api/admin/books/{book['id']}/questions",
        json={
            "question_type": "single",
            "question_text": "Q1",
            "options": ["A", "B"],
            "answer": "A",
        },
        headers=h,
    )
    assert resp.status_code == 200, resp.text
    qid = resp.json()["id"]

    resp = client.put(
        f"/api/admin/questions/{qid}",
        json={
            "question_type": "single",
            "question_text": "Q1改",
            "options": ["A", "B"],
            "answer": "B",
        },
        headers=h,
    )
    assert resp.status_code == 200, resp.text

    resp = client.post(f"/api/admin/questions/{qid}/toggle-active", headers=h)
    assert resp.status_code == 200, resp.text

    resp = client.delete(f"/api/admin/questions/{qid}", headers=h)
    assert resp.status_code == 200, resp.text

    actions = _audit_actions(db)
    assert {"quiz.create", "quiz.update", "quiz.toggle", "quiz.delete"} <= actions, actions
