# tests/unit/test_wm3_voucher.py — WM3-B2 收款凭证上传与查看（真实链路）
#
# 红测试四例（用户拍板：凭证做，已支付订单可查看凭证截图）：
#   1. 传凭证 + 确认收款 → voucher_path 落库
#   2. 无凭证订单查看凭证 → 404
#   3. 家长 token 拉凭证 → 401（P0-F3 同款越权回归，必测）
#   4. staff01（member.manage 无 book.manage）可查看凭证 → 200（权限点回归）

import io

from fastapi.testclient import TestClient


def _h(client, username="admin"):
    r = client.post("/api/admin/login", json={"username": username, "password": "dmkwords123"})
    return {"Authorization": f"Bearer {r.json()['token']}"}


def _parent(client, h, phone="13800003001") -> dict:
    r = client.post("/api/admin/members/parents", json={"name": "凭证家长", "phone": phone}, headers=h)
    assert r.status_code == 200, r.text
    return r.json()


def _child(client, h, parent_id: int, name="凭证孩") -> dict:
    r = client.post(
        f"/api/admin/members/parents/{parent_id}/children", json={"name": name}, headers=h
    )
    assert r.status_code == 200, r.text
    return r.json()


def _order(client, h, child_id: int, order_type="observation_fee") -> dict:
    r = client.post(
        "/api/admin/orders", json={"child_id": child_id, "order_type": order_type}, headers=h
    )
    assert r.status_code == 200, r.text
    return r.json()


def _png_bytes() -> bytes:
    from PIL import Image

    buf = io.BytesIO()
    Image.new("RGB", (16, 16), color=(200, 60, 60)).save(buf, "PNG")
    return buf.getvalue()


def test_b2_upload_voucher_and_confirm(client: TestClient, tmp_path):
    """传凭证（两步式第一步）→ voucher_path 落库 → 确认收款仍 200 且凭证保留。"""
    h = _h(client)
    p = _parent(client, h, "13800003001")
    c = _child(client, h, p["id"])
    o = _order(client, h, c["id"])
    r = client.post(
        f"/api/admin/orders/{o['id']}/voucher",
        files={"file": ("收款截图.png", _png_bytes(), "image/png")},
        headers=h,
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["voucher_path"].startswith("voucher/")
    # 确认收款（不带 voucher_path——主路径上传端点已落库）
    r2 = client.post(
        f"/api/admin/orders/{o['id']}/confirm-payment",
        json={"pay_method": "scan", "remark": "线下扫码"},
        headers=h,
    )
    assert r2.status_code == 200, r2.text
    assert r2.json()["status"] == "paid"
    assert r2.json()["voucher_path"] == body["voucher_path"]
    # 列表回读
    lr = client.get("/api/admin/orders?page=1&page_size=20", headers=h)
    row = next(i for i in lr.json()["items"] if i["id"] == o["id"])
    assert row["voucher_path"] == body["voucher_path"]


def test_b2_voucher_image_of_order_without_voucher_404(client: TestClient):
    """无凭证订单查看凭证 → 404。"""
    h = _h(client)
    p = _parent(client, h, "13800003002")
    c = _child(client, h, p["id"])
    o = _order(client, h, c["id"])
    r = client.post(
        f"/api/admin/orders/{o['id']}/confirm-payment",
        json={"pay_method": "cash"},
        headers=h,
    )
    assert r.status_code == 200, r.text
    r2 = client.get(f"/api/admin/members/orders/{o['id']}/voucher-image", headers=h)
    assert r2.status_code == 404, r2.text


def test_b2_voucher_image_parent_token_rejected(client: TestClient):
    """家长 token 拉凭证 → 401（P0-F3 同款越权回归）。"""
    h = _h(client)
    p = _parent(client, h, "13800003003")
    c = _child(client, h, p["id"])
    o = _order(client, h, c["id"])
    up = client.post(
        f"/api/admin/orders/{o['id']}/voucher",
        files={"file": ("v.png", _png_bytes(), "image/png")},
        headers=h,
    )
    assert up.status_code == 200, up.text
    # 家长小程序登录拿 parent token（对齐 test_wm2_media_auth 同款路径）
    ptok = client.post("/api/miniapp/login", json={"phone": p["phone"], "code": "1234"}).json()["token"]
    r = client.get(f"/api/admin/members/orders/{o['id']}/voucher-image?token={ptok}")
    assert r.status_code == 401, r.text


def test_b2_voucher_image_staff_member_manage_allowed(client: TestClient):
    """staff01（member.manage 无 book.manage）可查看凭证 → 200（权限点回归）。"""
    h = _h(client)
    p = _parent(client, h, "13800003004")
    c = _child(client, h, p["id"])
    o = _order(client, h, c["id"])
    up = client.post(
        f"/api/admin/orders/{o['id']}/voucher",
        files={"file": ("v.png", _png_bytes(), "image/png")},
        headers=h,
    )
    assert up.status_code == 200, up.text
    # staff 登录取 token，query token 走 <img> 通道
    sr = client.post("/api/admin/login", json={"username": "staff01", "password": "dmkwords123"})
    stok = sr.json()["token"]
    r = client.get(f"/api/admin/members/orders/{o['id']}/voucher-image?token={stok}")
    assert r.status_code == 200, r.text
    assert r.headers["content-type"].startswith("image/")


def test_b2_upload_voucher_invalid_ext_rejected(client: TestClient):
    """非图片类型 → 422（对齐封面上传校验口径）。"""
    h = _h(client)
    p = _parent(client, h, "13800003005")
    c = _child(client, h, p["id"])
    o = _order(client, h, c["id"])
    r = client.post(
        f"/api/admin/orders/{o['id']}/voucher",
        files={"file": ("v.txt", b"not an image", "text/plain")},
        headers=h,
    )
    assert r.status_code in (400, 422), r.text


def test_b2_upload_voucher_on_paid_order_rejected(client: TestClient):
    """已支付订单不可再传凭证 → 422。"""
    h = _h(client)
    p = _parent(client, h, "13800003006")
    c = _child(client, h, p["id"])
    o = _order(client, h, c["id"])
    cf = client.post(
        f"/api/admin/orders/{o['id']}/confirm-payment", json={"pay_method": "cash"}, headers=h
    )
    assert cf.status_code == 200
    r = client.post(
        f"/api/admin/orders/{o['id']}/voucher",
        files={"file": ("v.png", _png_bytes(), "image/png")},
        headers=h,
    )
    assert r.status_code == 422, r.text
