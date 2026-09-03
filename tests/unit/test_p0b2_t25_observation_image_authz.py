# tests/unit/test_p0b2_t25_observation_image_authz.py — P0 第二批 T25（C-12）observation-images 越权
"""安全红测试（P0-F1 同族第五案）：GET /observation-images/{path} 仅验 token 是
合法家长——无数据归属校验，任何登录家长可拉取任意孩子的观察期评估报告图。

修复：反查 ObservationReport（images JSON 数组精确匹配完整相对路径，防子串
误命中）→ child_of_parent 归属校验；查无归属一律 404（防枚举探测）。
"""

import json
import os

from fastapi.testclient import TestClient

from tests.unit.test_wm10_concurrency import _db, _family, _h


def _mk_report_image(child_id: int) -> str:
    """造一张真实存在的观察报告图 + 报告记录，返回 URL path 部分。"""
    from backend.config import get_settings
    from backend.domain.identity.models import ObservationReport

    rel = os.path.join("observation", f"child_{child_id}", "report_t25.jpg")
    root = os.path.abspath(get_settings().UPLOADS_DIR)
    full = os.path.join(root, rel)
    os.makedirs(os.path.dirname(full), exist_ok=True)
    with open(full, "wb") as f:
        f.write(b"fake-jpeg")
    with _db() as db:
        db.add(ObservationReport(child_id=child_id, images=json.dumps([rel])))
        db.commit()
    return rel


def test_observation_image_enforces_ownership(client: TestClient):
    h = _h(client)
    pA, cA, miniA = _family(client, h, "13981025001", "归属A孩")
    pB, cB, miniB = _family(client, h, "13981025002", "归属B孩")
    rel = _mk_report_image(cB["id"])
    tokenB = miniB["Authorization"].replace("Bearer ", "")
    tokenA = miniA["Authorization"].replace("Bearer ", "")
    # 端点 path 不含 observation/ 前缀（full = uploads/observation/{path}）；
    # images JSON 存储含前缀的完整相对路径
    url_path = os.path.relpath(rel, "observation")
    url = f"/api/miniapp/observation-images/{url_path}"

    try:
        # 本人孩子 → 200 回归
        r_ok = client.get(f"{url}?token={tokenB}")
        assert r_ok.status_code == 200, f"本人孩子的报告图应 200：{r_ok.status_code}"
        # 家长 A 拉家长 B 孩子的图 → 404（RED：当前 200 越权）
        r_bad = client.get(f"{url}?token={tokenA}")
        assert r_bad.status_code == 404, (
            f"越权拉取他人孩子报告图应 404，实 {r_bad.status_code}（RED=无归属校验）"
        )
        # 不存在的路径 → 404（不区分不存在/无权）
        r_none = client.get(f"/api/miniapp/observation-images/child_999/none.jpg?token={tokenA}")
        assert r_none.status_code == 404
    finally:
        from backend.config import get_settings

        full = os.path.join(os.path.abspath(get_settings().UPLOADS_DIR), rel)
        if os.path.isfile(full):
            os.remove(full)
