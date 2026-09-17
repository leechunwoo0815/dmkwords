# tests/unit/test_image_size_policy.py — 图片体积纪律（2026-09-17 用户裁定）
"""用户原话：「未来服务器磁盘空间可能没那么大…只要是上传或者是自动生成的图片，就必须控制大小，
不管运营人员上传多大的图片，后端都应该能自动压缩」。

覆盖四类断言（全部走真实链路 / 真实 Pillow，不用 mock）：

1. **上传必被限**：3000×2000 的原图 → 落盘长边 ≤ 配置上限、体积 ≤ 输出上限、格式 JPEG；
2. **文档类口径**：凭证 / 观察报告长边 ≤ 1600（要放大看清小字，故上限比封面宽）；
3. **EXIF 摆正**：手机竖拍（Orientation=6）不再被存成躺着的；
4. **超大上传拦截**：解码前按 `file.size` 直接 422（不把巨图读进内存）；
5. **生成图也受限**：阅读圈卡片双规格 + 周报图均为 JPEG 且 ≤ 250KB。

阈值一律取配置键（`image_*`）而非硬编码，改配置即改口径。
"""

from __future__ import annotations

import io
import json
import os
import random

from fastapi.testclient import TestClient

# ---------- 造图工具（真实像素，含噪点——压不动噪点才是本项目的历史痛点） ----------


def _noisy_jpeg(w: int, h: int) -> bytes:
    """带噪点的大图：模拟运营手机拍的照片（纯色图会掩盖压缩收益，噪点才真实）。"""
    from PIL import Image

    random.seed(11)
    img = Image.new("RGB", (w, h), (240, 236, 228))
    px = img.load()
    for y in range(0, h, 2):
        for x in range(0, w, 2):
            px[x, y] = (
                max(0, min(255, 150 + random.randint(-90, 90))),
                max(0, min(255, 190 + random.randint(-90, 90))),
                max(0, min(255, 210 + random.randint(-90, 90))),
            )
    buf = io.BytesIO()
    img.save(buf, "JPEG", quality=95)
    return buf.getvalue()


def _oriented_jpeg(w: int, h: int, orientation: int) -> bytes:
    """带 EXIF Orientation 的 JPEG（6 = 顺时针 90°，手机竖拍最常见）。"""
    from PIL import Image

    img = Image.new("RGB", (w, h), (120, 160, 200))
    exif = Image.Exif()
    exif[0x0112] = orientation
    buf = io.BytesIO()
    img.save(buf, "JPEG", exif=exif)
    return buf.getvalue()


def _size(path: str) -> tuple[int, int]:
    from PIL import Image

    with Image.open(path) as im:
        return im.size


def _admin(client: TestClient) -> dict:
    r = client.post("/api/admin/login", json={"username": "admin", "password": "dmkwords123"})
    assert r.status_code == 200, r.text
    return {"Authorization": f"Bearer {r.json()['token']}"}


# ---------- ① 上传必被限 ----------


def _mk_book(client: TestClient, h: dict, isbn: str = "9788300000001") -> dict:
    r = client.post(
        "/api/admin/books",
        json={"isbn": isbn, "title": "体积测试书", "word_count": 1200},
        headers=h,
    )
    assert r.status_code == 200, r.text
    return r.json()


def test_cover_upload_is_downscaled_and_capped(client: TestClient):
    """3000×2000 原图上传封面 → 长边压到配置上限、体积 ≤ 输出上限、格式 JPEG。"""
    from backend.common.config_service import ConfigService
    from backend.config import get_settings
    from backend.database import SessionLocal
    from backend.domain.catalog.models import Book

    h = _admin(client)
    book = _mk_book(client, h)
    raw = _noisy_jpeg(3000, 2000)
    assert len(raw) > 200 * 1024, "样本本身要够大才有意义"

    r = client.post(
        f"/api/admin/books/{book['id']}/cover",
        files={"file": ("手机拍的封面.jpg", raw, "image/jpeg")},
        headers=h,
    )
    assert r.status_code == 200, r.text
    with SessionLocal() as s:  # 响应只给 cover_url，路径直接查库更准
        cover_path = s.query(Book).filter(Book.id == book["id"]).first().cover_path
    assert cover_path.endswith(".jpg")
    full = os.path.join(get_settings().UPLOADS_DIR, cover_path)
    with SessionLocal() as s:
        max_edge = ConfigService(s).get_int("image_cover_max_edge", 1080)
        max_out = ConfigService(s).get_int("image_upload_max_output_kb", 600) * 1024
    w, hgt = _size(full)
    assert max(w, hgt) <= max_edge, f"长边 {max(w, hgt)} 超上限 {max_edge}"
    assert os.path.getsize(full) <= max_out, f"{os.path.getsize(full) / 1024:.0f}KB 超输出上限"
    assert os.path.getsize(full) < len(raw), "压缩后必须比原图小"


def test_voucher_upload_uses_doc_edge(client: TestClient):
    """收款凭证走 doc 口径（长边 1600）：要能放大看清小字，故比封面宽。"""
    from backend.common.config_service import ConfigService
    from backend.config import get_settings
    from backend.database import SessionLocal

    h = _admin(client)
    p = client.post(
        "/api/admin/members/parents", json={"name": "体积家长", "phone": "13800007001"}, headers=h
    ).json()
    c = client.post(
        f"/api/admin/members/parents/{p['id']}/children", json={"name": "体积孩"}, headers=h
    ).json()
    o = client.post(
        "/api/admin/orders", json={"child_id": c["id"], "order_type": "observation_fee"}, headers=h
    ).json()

    raw = _noisy_jpeg(2400, 3200)
    r = client.post(
        f"/api/admin/orders/{o['id']}/voucher",
        files={"file": ("收款截图.jpg", raw, "image/jpeg")},
        headers=h,
    )
    assert r.status_code == 200, r.text
    rel = r.json()["voucher_path"]
    full = os.path.join(get_settings().UPLOADS_DIR, rel)
    max_edge = ConfigService(SessionLocal()).get_int("image_doc_max_edge", 1600)
    w, hgt = _size(full)
    assert max(w, hgt) <= max_edge, f"长边 {max(w, hgt)} 超 doc 上限 {max_edge}"
    assert rel.endswith(".jpg")


def test_observation_report_is_compressed(client: TestClient):
    """观察报告**原先原图直存**（运营传 9 张手机原图可落几十 MB）→ 现走统一管线。"""
    from backend.common.config_service import ConfigService
    from backend.config import get_settings
    from backend.database import SessionLocal

    h = _admin(client)
    p = client.post(
        "/api/admin/members/parents", json={"name": "报告家长", "phone": "13800007002"}, headers=h
    ).json()
    c = client.post(
        f"/api/admin/members/parents/{p['id']}/children", json={"name": "报告孩"}, headers=h
    ).json()

    raw = _noisy_jpeg(2000, 2600)
    r = client.post(
        f"/api/admin/children/{c['id']}/observation-reports",
        files=[
            ("files", ("评估页1.jpg", raw, "image/jpeg")),
            ("files", ("评估页2.jpg", raw, "image/jpeg")),
        ],
        data={"remark": "体积测试"},
        headers=h,
    )
    assert r.status_code == 200, r.text
    from backend.domain.identity.models import ObservationReport

    with SessionLocal() as s:
        rep = s.query(ObservationReport).filter(ObservationReport.child_id == c["id"]).first()
        rels = json.loads(rep.images)
    max_edge = ConfigService(SessionLocal()).get_int("image_doc_max_edge", 1600)
    for rel in rels:
        assert rel.endswith(".jpg"), f"落盘必须统一 JPEG：{rel}"
        full = os.path.join(get_settings().UPLOADS_DIR, rel)
        w, hgt = _size(full)
        assert max(w, hgt) <= max_edge
        assert os.path.getsize(full) < len(raw), "必须比原始上传小"


# ---------- ③ EXIF 摆正 ----------


def test_exif_orientation_is_applied(client: TestClient):
    """手机竖拍（Orientation=6）上传后必须**摆正**——旧实现不校正会存成躺着的。"""
    from backend.config import get_settings
    from backend.database import SessionLocal

    h = _admin(client)
    book = _mk_book(client, h, "9788300000002")

    # 横躺存储 + EXIF 6：人眼看到的是 900×600 竖图
    raw = _oriented_jpeg(900, 600, 6)
    r = client.post(
        f"/api/admin/books/{book['id']}/cover",
        files={"file": ("竖拍封面.jpg", raw, "image/jpeg")},
        headers=h,
    )
    assert r.status_code == 200, r.text
    from backend.domain.catalog.models import Book

    with SessionLocal() as s:
        cover_path = s.query(Book).filter(Book.id == book["id"]).first().cover_path
    w, hgt = _size(os.path.join(get_settings().UPLOADS_DIR, cover_path))
    assert hgt > w, f"EXIF 未摆正：存成了 {w}x{hgt}（应竖版）"


# ---------- ④ 超大上传拦截 ----------


def test_oversized_upload_rejected_before_decode(client: TestClient):
    """超过 `image_upload_max_mb` 的上传在**解码前**被拒（错误文案可辨，证明拦的是体积不是解析失败）。"""
    h = _admin(client)
    book = _mk_book(client, h, "9788300000003")
    junk = b"\x00" * (9 * 1024 * 1024)  # 9MB > 默认 8MB
    r = client.post(
        f"/api/admin/books/{book['id']}/cover",
        files={"file": ("巨图.png", junk, "image/png")},
        headers=h,
    )
    assert r.status_code == 422, r.text
    assert "体积超限" in r.text, r.text


# ---------- ⑤ 生成图也受限 ----------


def test_circle_card_and_thumb_are_jpeg_and_small(client: TestClient):
    """阅读圈双规格卡片：JPEG + 各自 ≤ 250KB（PNG 时代大图 769KB / 缩略图 317KB）。"""
    from backend.config import get_settings
    from backend.domain.reading_circle.card_render import render_card

    rendered = render_card(
        {
            "card_type": "milestone",
            "label": "里程碑",
            "value_text": "1500 词",
            "value_label": "累计有效词数",
            "child_name": "体积孩",
            "date_text": "2026-09-17",
        }
    )
    root = get_settings().UPLOADS_DIR
    for key, cap in (("image_path", 250 * 1024), ("thumb_path", 250 * 1024)):
        rel = rendered[key]
        assert rel.endswith(".jpg"), rel
        full = os.path.join(root, rel)
        size = os.path.getsize(full)
        assert size <= cap, f"{key} {size / 1024:.0f}KB 超 250KB"
        with open(full, "rb") as f:
            assert f.read(3) == b"\xff\xd8\xff"
        os.remove(full)  # 本用例自产自清，不留孤儿


def test_report_image_is_idempotent_per_view(client: TestClient):
    """报告图**每次查看都会重新生成**（端点按需出图）——文件名必须按内容摘要幂等，
    否则家长每看一次就多一个文件，服务器磁盘无上限增长（2026-09-17 发现的真实泄漏）。"""
    import glob

    from backend.config import get_settings

    h = _admin(client)
    p = client.post(
        "/api/admin/members/parents",
        json={"name": "报告体积家长", "phone": "13800007003"},
        headers=h,
    ).json()
    c = client.post(
        f"/api/admin/members/parents/{p['id']}/children", json={"name": "报告体积孩"}, headers=h
    ).json()

    paths = set()
    for _ in range(3):  # 连看三次（等价于家长反复打开报告页）
        r = client.post(f"/api/admin/children/{c['id']}/reports/weekly/generate", headers=h)
        assert r.status_code == 200, r.text
        paths.add(r.json()["path"])
    assert len(paths) == 1, f"同内容重复出图应命中同一文件，实际落了 {len(paths)} 个：{paths}"
    rel = paths.pop()
    assert rel.endswith(".jpg")
    full = os.path.join(get_settings().UPLOADS_DIR, rel)
    assert os.path.getsize(full) <= 250 * 1024
    # 该孩子的报告目录里不该堆积同 (孩子, 类型) 的历史文件
    siblings = glob.glob(
        os.path.join(get_settings().UPLOADS_DIR, "reports", f"report_weekly_{c['id']}_*")
    )
    assert len(siblings) == 1, f"残留 {len(siblings)} 个文件：{siblings}"


def test_legacy_png_observation_still_served_as_png(client: TestClient):
    """存量观察报告是**原图直存**的（可能是 .png），新上传才统一 JPEG。

    故 media_type 必须**按扩展名派生**而非写死 image/jpeg——否则存量 .png 会带错 MIME
    （README 里的 C-12/T25 端点曾硬编码 image/jpeg）。
    """
    import json

    from backend.config import get_settings
    from backend.database import SessionLocal
    from backend.domain.identity.models import ObservationReport
    from tests.unit.test_wm10_concurrency import _family

    h = _admin(client)
    _p, c, mini = _family(client, h, "13800007004", "存量图孩")

    # 手工造一张"存量 PNG"（模拟本批之前上传、原图直存的文件）
    rel = os.path.join("observation", f"child_{c['id']}", "legacy_report.png")
    full = os.path.join(get_settings().UPLOADS_DIR, rel)
    os.makedirs(os.path.dirname(full), exist_ok=True)
    with open(full, "wb") as f:
        f.write(_oriented_jpeg(64, 64, 1))  # 内容不重要，本用例只验 MIME 按扩展名派生
    with SessionLocal() as s:
        s.add(ObservationReport(child_id=c["id"], images=json.dumps([rel])))
        s.commit()

    token = mini["Authorization"].replace("Bearer ", "")
    url_path = f"child_{c['id']}/legacy_report.png"
    r = client.get(f"/api/miniapp/observation-images/{url_path}?token={token}")
    assert r.status_code == 200, r.text
    assert r.headers["content-type"] == "image/png", r.headers
