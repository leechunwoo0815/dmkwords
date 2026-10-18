# tests/unit/test_audit7_p2_5_media_path_20261018.py — P2-5 收口：两处裸前缀路径判定
"""第七轮报告 P2-5（第四轮 P2-27 结转）：「裸前缀路径判定」全仓还剩 **2 处**未改。

裸前缀 = `full.startswith(os.path.abspath(root))`——**同前缀兄弟目录**（`uploads-evil/x.jpg`）能骗过它，
把 uploads 之外的文件当成 uploads 内的下发出去。单一口径是 `common/media_paths.is_within`
（`root + os.sep` 判定 + 两侧 abspath）。

两处：`activity/router.py`（活动封面 cover-media）与 `identity/router.py`（凭证 voucher-image）。
本用例**在端点层**造出该绕过（DB 里的 rel 写成 `../uploads-evil/x.jpg`，并在 uploads 的**兄弟目录**
放真文件）→ 必须 **404**；退回裸前缀 → **200**（红）。

RED：把任一处的 `is_within(root, full)` 改回 `full.startswith(os.path.abspath(root))` → 对应用例红。
"""

from __future__ import annotations

import os
import shutil

from fastapi.testclient import TestClient

from backend.config import get_settings
from tests.unit.test_p1_audit_20261015 import _db


def _make_sibling_file(rel_name: str) -> tuple[str, str]:
    """在 uploads 的**兄弟目录**造一个真文件；返回 (sibling_root, 相对 uploads 的穿越路径)。

    **目录名必须从 `UPLOADS_DIR` 实取**：conftest 把 uploads 指到临时目录
    （`dmk-test-uploads-XXXX`），写死 `uploads-evil` 的话穿越路径落不到兄弟目录上——
    文件不存在 → 两种实现都 404 → **用例变成假绿**（本批首轮实测踩到，变异存活才发现）。
    """
    root = os.path.abspath(get_settings().UPLOADS_DIR)
    sibling = root + "-evil"  # 同前缀：裸 startswith 会放行
    os.makedirs(sibling, exist_ok=True)
    with open(os.path.join(sibling, rel_name), "wb") as fh:
        fh.write(b"\xff\xd8\xff-jpeg")  # 真 JPEG 头，排除"文件不存在"这种别的 404 原因
    rel = f"../{os.path.basename(root)}-evil/{rel_name}"
    return sibling, rel


def _h(client: TestClient) -> dict:
    r = client.post("/api/admin/login", json={"username": "admin", "password": "dmkwords123"})
    assert r.status_code == 200, r.text
    return {"Authorization": f"Bearer {r.json()['token']}"}


def test_activity_cover_media_rejects_sibling_prefix_dir(client: TestClient):
    """活动封面：rel 指向 `../uploads-evil/x.jpg` → 404（裸前缀下会 200 把外部文件发出去）。"""
    h = _h(client)
    act = client.post(
        "/api/admin/activities",
        json={
            "title": "P2-5 路径用例",
            # 远期哨兵（≥2050）：门禁 check_fake_assertions 判"时间炸弹"（2030 会到期）
            "start_at": "2050-01-01T10:00:00",
            "max_quota": 5,
            "fee": "0",
        },
        headers=h,
    ).json()
    sibling, rel = _make_sibling_file("cover.png")
    try:
        from backend.domain.activity.models import Activity

        with _db() as db:
            db.query(Activity).filter(Activity.id == act["id"]).update({"cover_path": rel})
            db.commit()
        r = client.get(f"/api/admin/activities/{act['id']}/cover-media", headers=h)
        assert r.status_code == 404, (
            f"兄弟目录必须 404（裸前缀会放行），实 {r.status_code}——穿越路径 {rel}"
        )
    finally:
        shutil.rmtree(sibling, ignore_errors=True)


def test_voucher_image_rejects_sibling_prefix_dir(client: TestClient):
    """收款凭证：rel 指向 `../uploads-evil/v.png` → 404（同上）。"""
    h = _h(client)
    parent = client.post(
        "/api/admin/members/parents", json={"name": "P25 家长", "phone": "13986000001"}, headers=h
    ).json()
    child = client.post(
        f"/api/admin/members/parents/{parent['id']}/children", json={"name": "P25 孩"}, headers=h
    ).json()
    order = client.post(
        "/api/admin/orders",
        json={"child_id": child["id"], "order_type": "observation_fee"},
        headers=h,
    ).json()
    sibling, rel = _make_sibling_file("v.png")
    try:
        from backend.domain.identity.models import Order

        with _db() as db:
            db.query(Order).filter(Order.id == order["id"]).update({"voucher_path": rel})
            db.commit()
        token = h["Authorization"].split()[1]
        r = client.get(
            f"/api/admin/members/orders/{order['id']}/voucher-image", params={"token": token}
        )
        assert r.status_code == 404, (
            f"兄弟目录必须 404（裸前缀会放行），实 {r.status_code}——穿越路径 {rel}"
        )
    finally:
        shutil.rmtree(sibling, ignore_errors=True)
