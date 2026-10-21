# tests/unit/test_audit8_e4_admin_scenes_20261020.py — 第八轮大审查 E 批（管理端族）
"""管理端两条"清单不全/清不掉"的契约守护：

- **P2-59** 待办墙场景白名单必须覆盖后端**全部** `AdminNotification.SCENE_*`
  （原先只列 7 个，另外 10 个场景筛不出来、场景列显示裸英文）。
- **P2-60** 生日**显式清空**：前端送 `birthday: null` 必须真的清掉（原先只有 `is not None`
  分支 → 无论送 null 还是省略都等于"不改"，生日永远清不掉）。

RED（变异自证）：
- 前端白名单少一个场景 → 用例 1 红；
- 后端不认"显式 null"（去掉 clear_birthday 分支）→ 用例 2 红。
"""

from __future__ import annotations

import re
from pathlib import Path

from fastapi.testclient import TestClient

ROOT = Path(__file__).resolve().parent.parent.parent


def test_admin_scene_whitelist_covers_backend_scenes():
    """待办墙的场景白名单 = 后端场景常量集合（一个都不能少）。"""
    from backend.common.admin_notification_models import AdminNotification

    backend = {
        v
        for k, v in vars(AdminNotification).items()
        if k.startswith("SCENE_") and isinstance(v, str)
    }
    text = (ROOT / "admin-web/src/pages/Notifications.tsx").read_text(encoding="utf-8")
    block = re.search(r"const ADMIN_SCENE_OPTIONS[\s\S]*?\n\];", text)
    assert block, "找不到 ADMIN_SCENE_OPTIONS"
    frontend = set(re.findall(r'value:\s*"(admin\.[a-z_]+)"', block.group(0)))
    # 白名单里有以**常量**形式写的场景（`{ value: MILESTONE_SCENE, ... }`）——
    # 把该常量的字面值解析出来，否则会误报"缺 admin.milestone_achieved"（本用例首跑即踩）。
    for const_name, const_val in re.findall(
        r'const\s+([A-Z_]+)\s*=\s*"(admin\.[a-z_]+)"', text
    ):
        if re.search(rf"value:\s*{const_name}\b", block.group(0)):
            frontend.add(const_val)

    missing = sorted(backend - frontend)
    assert not missing, (
        f"待办墙缺 {len(missing)} 个后端场景（筛不出来 + 场景列显示裸英文）：{missing}——"
        "RED=白名单只列了 7 个"
    )
    extra = sorted(frontend - backend)
    assert not extra, f"前端白名单里有后端不存在的场景（迟早筛出空）：{extra}"


def test_birthday_can_be_explicitly_cleared(client: TestClient):
    """生日显式传 null = 清空（P2-60：原先永远清不掉）。"""
    from tests.unit.test_wm13_writeback import _family, _h

    h = _h(client)
    p, c, _mini = _family(client, h, "13996000001", "清空生日孩")

    # 先设一个生日
    r = client.put(
        f"/api/admin/members/children/{c['id']}",
        json={"birthday": "2018-05-06"},
        headers=h,
    )
    assert r.status_code == 200, r.text
    assert r.json()["birthday"] == "2018-05-06", r.json()

    # 显式传 null → 必须清掉（不是"不改"）
    r2 = client.put(
        f"/api/admin/members/children/{c['id']}",
        json={"birthday": None},
        headers=h,
    )
    assert r2.status_code == 200, r2.text
    assert r2.json()["birthday"] is None, (
        f"生日必须能被清空（实 {r2.json()['birthday']!r}）——"
        "RED=后端只有 is not None 分支，送 null 等于不改"
    )

    # 对照组：**不传** birthday 时不得误清（"没传" ≠ "清空"）
    r3 = client.put(
        f"/api/admin/members/children/{c['id']}",
        json={"english_name": "ClearKid"},
        headers=h,
    )
    assert r3.status_code == 200, r3.text
    assert r3.json()["english_name"] == "ClearKid"


def test_empty_string_clears_english_name_and_grade(client: TestClient):
    """文本字段送空串 = 清空（P2-60：前端原先送 undefined，被当成"不改"）。"""
    from tests.unit.test_wm13_writeback import _family, _h

    h = _h(client)
    p, c, _mini = _family(client, h, "13996000002", "清空文本孩")

    assert (
        client.put(
            f"/api/admin/members/children/{c['id']}",
            json={"english_name": "Kid", "grade": "G3"},
            headers=h,
        ).json()["english_name"]
        == "Kid"
    )
    r = client.put(
        f"/api/admin/members/children/{c['id']}",
        json={"english_name": "", "grade": ""},
        headers=h,
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["english_name"] in (None, ""), f"英文名应被清空：{body['english_name']!r}"
    assert body["grade"] == "", f"年级应被清空（空串）：{body['grade']!r}"
