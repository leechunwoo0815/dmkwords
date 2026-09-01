# tests/unit/test_wm2_media_auth.py — P0-F3：媒体端点 token type + gen 撤销校验
"""项目记忆硬约束"Media endpoints must verify token type is 'admin'"的落实：
家长 token（同密钥 HS256，30 天）不得访问管理端媒体；admin 改密（gen+1）后旧 token 失效。"""

import io

from fastapi.testclient import TestClient
from PIL import Image


def _h(client, username="admin"):
    r = client.post("/api/admin/login", json={"username": username, "password": "dmkwords123"})
    return {"Authorization": f"Bearer {r.json()['token']}"}


def _book_with_cover(client, h, title="媒体鉴权书") -> int:
    book = client.post(
        "/api/admin/books",
        json={"isbn": None, "title": title, "word_count": 100, "copy_count": 1},
        headers=h,
    ).json()
    img = Image.new("RGB", (10, 10), color="red")
    buf = io.BytesIO()
    img.save(buf, format="JPEG")
    buf.seek(0)
    resp = client.post(
        f"/api/admin/books/{book['id']}/cover",
        files={"file": ("x.jpg", buf, "image/jpeg")},
        headers=h,
    )
    assert resp.status_code == 200, resp.text
    return book["id"]


def _parent_token(client, phone="13800002101") -> str:
    client.post(
        "/api/admin/members/parents",
        json={"name": "越权测试家长", "phone": phone},
        headers=_h(client),
    )
    return client.post("/api/miniapp/login", json={"phone": phone, "code": "1234"}).json()["token"]


def test_parent_token_cannot_access_media(client: TestClient):
    """家长 token（type=parent，同密钥签发）调管理端媒体端点 → 401（修复前 200）。"""
    h = _h(client)
    book_id = _book_with_cover(client, h)
    parent_token = _parent_token(client)
    r1 = client.get(f"/api/admin/books/{book_id}/cover-media?token={parent_token}")
    assert r1.status_code == 401, f"parent token 拉封面未拦: {r1.status_code}"
    r2 = client.get(f"/api/admin/books/{book_id}/audio-media?token={parent_token}")
    assert r2.status_code == 401, f"parent token 拉音频未拦: {r2.status_code}"
    # Bearer header 通道同样拦截
    r3 = client.get(
        f"/api/admin/books/{book_id}/cover-media",
        headers={"Authorization": f"Bearer {parent_token}"},
    )
    assert r3.status_code == 401, f"parent token Bearer 通道未拦: {r3.status_code}"


def test_admin_token_still_works(client: TestClient):
    """防误伤：admin token 正常拉媒体 200。"""
    h = _h(client)
    book_id = _book_with_cover(client, h, "管理员书")
    token = h["Authorization"].split()[1]
    r = client.get(f"/api/admin/books/{book_id}/cover-media?token={token}")
    assert r.status_code == 200, r.text
    assert r.headers["content-type"] == "image/jpeg"


def test_stale_admin_token_generation_rejected(client: TestClient):
    """gen 撤销：admin.token_generation +1（改密后果）后，旧 token（gen=0）媒体端点 401。"""
    h = _h(client)
    book_id = _book_with_cover(client, h, "改密书")
    token = h["Authorization"].split()[1]
    from backend.database import get_session
    from backend.domain.admin.models import AdminUser

    with get_session() as db:
        admin = db.query(AdminUser).filter(AdminUser.username == "admin").first()
        # 直改库模拟 gen bump——审查确认：改密 API（staff reset_password / 修改密码）
        # 当前均不 bump token_generation（全库零 bump 点），API 路径走不通；
        # bump 机制接线属后续任务，此处直改测的是媒体端点的 gen 校验行为
        admin.token_generation = admin.token_generation + 1
        db.commit()
    r = client.get(f"/api/admin/books/{book_id}/cover-media?token={token}")
    assert r.status_code == 401, f"改密后旧 token 拉媒体未拦: {r.status_code}"
