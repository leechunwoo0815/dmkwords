# tests/unit/test_observation_admin_view_20261103.py — 管理端查看已上传评估报告
"""甲方报障：小程序端能看到评估报告，管理端点「评估报告」只有上传框，看不到历史。

根因：管理端只有 `POST /children/{id}/observation-reports`，没有 GET；`observation/`
又是受保护目录（`/api/admin/uploads/*` 不下发），后台即使知道路径也取不到图。
本判据锁住：管理端列表 + 专用图片端点 + 归属校验（跨孩子 404、无凭证 401）。
"""

from io import BytesIO

from fastapi.testclient import TestClient
from PIL import Image

from tests.unit.test_wm10_concurrency import _family, _h


def _png_bytes() -> bytes:
    buf = BytesIO()
    Image.new("RGB", (32, 32), (10, 120, 200)).save(buf, "PNG")
    return buf.getvalue()


def test_admin_can_list_and_view_uploaded_report(client: TestClient):
    h = _h(client)
    _, child, _ = _family(client, h, "13981031001", "报告可见孩")
    uploaded = client.post(
        f"/api/admin/children/{child['id']}/observation-reports",
        files=[("files", ("report.png", _png_bytes(), "image/png"))],
        data={"remark": "第一阶段听力优秀"},
        headers=h,
    )
    assert uploaded.status_code == 200, uploaded.text
    images = uploaded.json()["images"]
    assert len(images) == 1

    # 列表：后台能看到刚上传的报告（含备注/图片路径/时间）
    listed = client.get(f"/api/admin/children/{child['id']}/observation-reports", headers=h)
    assert listed.status_code == 200, listed.text
    rows = listed.json()
    assert len(rows) == 1, rows
    assert rows[0]["remark"] == "第一阶段听力优秀"
    assert rows[0]["images"] == images
    assert rows[0]["created_at"]

    # 图片：query token 通道（<img> 带不了 Authorization）
    sub = images[0].replace("observation/", "")
    token = h["Authorization"].replace("Bearer ", "")
    img = client.get(f"/api/admin/children/{child['id']}/observation-images/{sub}?token={token}")
    assert img.status_code == 200, img.text
    assert img.headers["content-type"].startswith("image/jpeg")

    # 跨孩子：同一个 path 挂到另一个孩子 id 下必须 404（归属按报告行校验）
    _, other, _ = _family(client, h, "13981031002", "报告越权孩")
    bad = client.get(f"/api/admin/children/{other['id']}/observation-images/{sub}?token={token}")
    assert bad.status_code == 404, bad.text

    # 无凭证：列表 401
    anon = client.get(f"/api/admin/children/{child['id']}/observation-reports")
    assert anon.status_code == 401, anon.text
