# tests/unit/test_wm9b_activity_detail_blocks.py — 活动图文详情（2026-09-20 客户需求「像公众号一样」）
"""覆盖真实链路（HTTP + 真实 MySQL + 真实落盘）：

- 管理端能写图文块（段落/图片），顺序即展示顺序；小程序详情按顺序返回（图片出带 token 的 URL）
- 块级校验：类型非法 / 段落空 / 配图越权（不属于本活动）/ 文件不存在 / 超过 30 张 → 422
- **守卫差异是本次核心**：普通 `PUT /activities/{id}` 在活动开始后仍 422（时间/名额/费用不放宽），
  但 `PUT /{id}/detail-blocks` 允许（纯展示字段，活动前写招募图文、活动后补往期回顾）
- 配图落地 `uploads/activity_detail/`、经 `?name=` 端点可取且**拒绝路径穿越/越权文件名**
- 移出图文的配图文件被清理；cleanup 脚本把它列进引用集且列进保护名单
"""

from __future__ import annotations

import json
import os
from datetime import datetime, timedelta

from fastapi.testclient import TestClient

from tests.unit.test_wm10_concurrency import _family


def _h(client: TestClient, username: str = "admin") -> dict:
    r = client.post("/api/admin/login", json={"username": username, "password": "dmkwords123"})
    return {"Authorization": f"Bearer {r.json()['token']}"}


def _db():
    from backend.database import get_session

    return get_session()


def _mk_activity(client: TestClient, h: dict, title: str = "图文活动", **over):
    payload = {
        "title": title,
        "activity_type": "book_club",
        "start_at": (datetime.now() + timedelta(days=7)).strftime("%Y-%m-%dT%H:%M:%S"),
        "location": "馆内一层",
        "max_quota": 10,
        "fee": "0",
        "description": "一句话简介",
    }
    payload.update(over)
    r = client.post("/api/admin/activities", json=payload, headers=h)
    assert r.status_code == 200, r.text
    return r.json()


def _png_bytes(size=(400, 260), color=(200, 160, 120)) -> bytes:
    from io import BytesIO

    from PIL import Image

    buf = BytesIO()
    Image.new("RGB", size, color).save(buf, "PNG")
    return buf.getvalue()


def _upload_image(client: TestClient, h: dict, activity_id: int) -> str:
    r = client.post(
        f"/api/admin/activities/{activity_id}/detail-images",
        files={"file": ("photo.png", _png_bytes(), "image/png")},
        headers=h,
    )
    assert r.status_code == 200, r.text
    return r.json()["path"]


def _put_blocks(client: TestClient, h: dict, activity_id: int, blocks: list[dict]):
    return client.put(
        f"/api/admin/activities/{activity_id}/detail-blocks",
        json={"blocks": blocks},
        headers=h,
    )


def test_detail_blocks_write_and_miniapp_render(client: TestClient):
    """写入段落+图片块 → 管理端 200；小程序详情按顺序返回且图片给 URL。"""
    h = _h(client)
    act = _mk_activity(client, h, "图文顺序活动")
    p1 = _upload_image(client, h, act["id"])
    p2 = _upload_image(client, h, act["id"])
    blocks = [
        {"type": "paragraph", "text": "第一段：这周我们读《Brown Bear》。"},
        {"type": "image", "path": p1, "caption": "上次活动现场"},
        {"type": "paragraph", "text": "第二段：名额有限，先到先得。"},
        {"type": "image", "path": p2},
    ]
    r = _put_blocks(client, h, act["id"], blocks)
    assert r.status_code == 200, r.text
    assert [b["type"] for b in r.json()["blocks"]] == ["paragraph", "image", "paragraph", "image"]

    # 落库形态：JSON 文本
    with _db() as db:
        from backend.domain.activity.models import Activity

        raw = db.query(Activity.detail_blocks).filter(Activity.id == act["id"]).scalar()
    assert json.loads(raw)[1]["caption"] == "上次活动现场"

    # 小程序详情（先建一个家庭拿家长 token——测试库每次被清空，不能借用演示账号）
    _p, child, mini = _family(client, h, "13900060001", "图文孩")
    cid = child["id"]
    # 小程序活动详情必带 child_id（按孩子可见性过滤）
    r_detail = client.get(
        f"/api/miniapp/activities/{act['id']}", params={"child_id": cid}, headers=mini
    )
    assert r_detail.status_code == 200, r_detail.text
    detail = r_detail.json()
    got = detail["detail_blocks"]
    assert [b["type"] for b in got] == ["paragraph", "image", "paragraph", "image"]
    assert got[1]["image_url"].startswith(f"/api/miniapp/activities/{act['id']}/detail-image?name=")
    # 破缓存 token（换图必换 URL）：本端点 URL 形如 ?name=xxx&v=yyy
    assert "&v=" in got[1]["image_url"]

    # 图片端点可取（token 双通道）；basename 即 URL 里的 name
    name = got[1]["image_url"].split("name=")[1].split("&")[0]
    token = mini["Authorization"].split()[1]
    img = client.get(
        f"/api/miniapp/activities/{act['id']}/detail-image", params={"name": name, "token": token}
    )
    assert img.status_code == 200
    assert img.headers["content-type"] == "image/jpeg"


def test_detail_blocks_validation(client: TestClient):
    """类型非法 / 空段落 / 越权配图 / 不存在的配图 / 超 30 张 → 全部 422。"""
    h = _h(client)
    act = _mk_activity(client, h, "图文校验活动")
    other = _mk_activity(client, h, "别人的活动")
    mine = _upload_image(client, h, act["id"])
    theirs = _upload_image(client, h, other["id"])

    assert _put_blocks(client, h, act["id"], [{"type": "video", "text": "x"}]).status_code == 422
    assert (
        _put_blocks(client, h, act["id"], [{"type": "paragraph", "text": "   "}]).status_code == 422
    )
    # 越权：引用了别家活动的配图
    r = _put_blocks(client, h, act["id"], [{"type": "image", "path": theirs}])
    assert r.status_code == 422 and "不属于本活动" in r.text
    # 不存在（但路径前缀合法）
    r = _put_blocks(
        client,
        h,
        act["id"],
        [{"type": "image", "path": f"activity_detail/{act['id']}_deadbeef.jpg"}],
    )
    assert r.status_code == 422 and "文件不存在" in r.text
    # 超上限
    too_many = [{"type": "image", "path": mine} for _ in range(31)]
    r = _put_blocks(client, h, act["id"], too_many)
    assert r.status_code == 422 and "最多 30 张" in r.text
    # 未知字段（BaseSchema extra=forbid）
    assert (
        _put_blocks(client, h, act["id"], [{"type": "paragraph", "text": "ok", "x": 1}]).status_code
        == 422
    )


def test_detail_editable_after_start_but_core_fields_not(client: TestClient):
    """**核心守卫差异**：活动开始后 → 普通编辑 422，图文编辑仍 200。"""
    h = _h(client)
    act = _mk_activity(client, h, "已开始活动")
    with _db() as db:
        from backend.domain.activity.models import Activity

        a = db.query(Activity).filter(Activity.id == act["id"]).first()
        a.start_at = datetime.now() - timedelta(hours=3)  # 模拟"活动已开始"
        db.commit()

    # 时间/名额/费用仍被旧守卫拦住
    r = client.put(
        f"/api/admin/activities/{act['id']}",
        json={"max_quota": 99},
        headers=h,
    )
    assert r.status_code == 422, r.text
    assert "已开始" in r.text

    # 图文可以写（活动后补回顾的关键）
    p = _upload_image(client, h, act["id"])
    r2 = _put_blocks(client, h, act["id"], [{"type": "paragraph", "text": "活动回顾"}])
    assert r2.status_code == 200, r2.text
    r3 = _put_blocks(client, h, act["id"], [{"type": "image", "path": p, "caption": "现场"}])
    assert r3.status_code == 200, r3.text


def test_cancelled_activity_detail_locked(client: TestClient):
    """活动取消后图文也不可写（取消=整场作废，不该再挂招募图文）。"""
    h = _h(client)
    act = _mk_activity(client, h, "取消活动")
    assert client.post(
        f"/api/admin/activities/{act['id']}/cancel", json={}, headers=h
    ).status_code in (200, 201)
    r = _put_blocks(client, h, act["id"], [{"type": "paragraph", "text": "x"}])
    assert r.status_code == 422 and "已取消" in r.text


def test_removed_image_file_is_cleaned(client: TestClient):
    """把图片块移出图文 → 对应文件被删除（不留孤儿）；仍引用的图不动。"""
    from backend.config import get_settings

    h = _h(client)
    act = _mk_activity(client, h, "图集清理活动")
    keep = _upload_image(client, h, act["id"])
    drop = _upload_image(client, h, act["id"])
    assert (
        _put_blocks(
            client,
            h,
            act["id"],
            [
                {"type": "image", "path": keep},
                {"type": "image", "path": drop},
            ],
        ).status_code
        == 200
    )
    root = os.path.abspath(get_settings().UPLOADS_DIR)
    assert os.path.isfile(os.path.join(root, drop))

    assert _put_blocks(client, h, act["id"], [{"type": "image", "path": keep}]).status_code == 200
    assert not os.path.isfile(os.path.join(root, drop)), "被移出的配图应删除"
    assert os.path.isfile(os.path.join(root, keep)), "仍在用的配图不得删"


def test_detail_image_endpoint_rejects_traversal(client: TestClient):
    """配图端点只认 basename + 归属前缀：路径穿越/别家文件名 → 404。"""
    h = _h(client)
    act = _mk_activity(client, h, "穿越活动")
    other = _mk_activity(client, h, "被引用活动")
    theirs = os.path.basename(_upload_image(client, h, other["id"]))
    _p, _c, mini = _family(client, h, "13900060002", "穿越孩")
    token = mini["Authorization"].split()[1]

    for bad in ("../../etc/passwd", "..%2F..%2Fetc%2Fpasswd", f"sub/{theirs}", theirs, "a.jpg"):
        r = client.get(
            f"/api/miniapp/activities/{act['id']}/detail-image",
            params={"name": bad, "token": token},
        )
        assert r.status_code == 404, f"{bad} 应 404，实 {r.status_code}"


def test_cleanup_registers_detail_images(client: TestClient):
    """cleanup 脚本：把图文配图列进引用集 + 列进保护名单（防被当孤儿清掉）。"""
    h = _h(client)
    act = _mk_activity(client, h, "清理对账活动")
    rel = _upload_image(client, h, act["id"])
    assert _put_blocks(client, h, act["id"], [{"type": "image", "path": rel}]).status_code == 200

    from scripts.cleanup_uploads import collect_referenced, is_protected

    referenced, _missing = collect_referenced(os.path.abspath("uploads"))
    assert rel in referenced, "图文配图必须进引用集"
    assert is_protected(rel), "图文配图必须在保护名单里"


def test_admin_detail_image_preview_endpoint(client: TestClient):
    """管理端编辑器预览端点（管理员 token 通道）：可取图；与小程序端点同套拒绝规则。

    为什么单测这条：管理端 <img> 打不开 Authorization 头，只能拼 query token；而它拿的是
    **管理员** token，走不了小程序那个要家长 token 的端点——漏了就是"编辑器里配图全是裂图"。
    """
    h = _h(client)
    act = _mk_activity(client, h, "管理端预览活动")
    rel = _upload_image(client, h, act["id"])
    name = os.path.basename(rel)
    tok = h["Authorization"].split()[1]

    ok = client.get(
        f"/api/admin/activities/{act['id']}/detail-image",
        params={"name": name, "token": tok},
    )
    assert ok.status_code == 200, ok.text
    assert ok.headers["content-type"] == "image/jpeg"

    # 同套拒绝：穿越 / 别家文件名 / 非常规名字
    other = _mk_activity(client, h, "别人的预览活动")
    theirs = os.path.basename(_upload_image(client, h, other["id"]))
    for bad in ("../../etc/passwd", theirs, "a.jpg", f"sub/{name}"):
        r = client.get(
            f"/api/admin/activities/{act['id']}/detail-image",
            params={"name": bad, "token": tok},
        )
        assert r.status_code == 404, f"{bad} 应 404，实 {r.status_code}"
