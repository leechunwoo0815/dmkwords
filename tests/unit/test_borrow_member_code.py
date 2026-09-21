# tests/unit/test_borrow_member_code.py — 借阅台扫码闭环 A 批：会员码（2026-09-21 任务包）
"""会员码 = 借阅台扫孩子身份的唯一凭据。锁四件事：

1. **创建即发码**（列默认值兜底，seed/直插路径也绕不过）+ 格式/校验位合法 + 不重复；
2. **校验位是可判定的**——错码给"码不合法"（422），而不是让馆员去猜"这孩子是不是没建档"；
3. 按码能取到孩子卡片（借阅台"扫会员码"框走这个端点）；
4. 小程序载荷带 member_code（出示页的数据来源；缺了前端就出不了码）。
"""

from fastapi.testclient import TestClient

from backend.domain.identity.member_code import (
    ALPHABET,
    CODE_LEN,
    generate_member_code,
    is_valid_member_code,
    normalize_member_code,
)
from tests.unit.test_wm10_concurrency import _family, _h


def test_generate_and_validate():
    """生成器自证：格式/字符集/校验位；且**改动任一字符都必须被判非法**（校验位不是摆设）。"""
    code = generate_member_code()
    assert len(code) == CODE_LEN and code.startswith("M")
    assert is_valid_member_code(code)
    assert is_valid_member_code(f"  {code.lower()}  ")  # 扫码枪带空白 + 小写归一化

    # 逐位改一个字符 → 必须判非法（覆盖 8 位主体 + 校验位）
    for i in range(1, CODE_LEN):
        alt = ALPHABET[(ALPHABET.index(code[i]) + 1) % len(ALPHABET)]
        assert not is_valid_member_code(code[:i] + alt + code[i + 1 :]), f"第 {i} 位改坏仍被判合法"
    assert not is_valid_member_code("")
    assert not is_valid_member_code("X" + code[1:])  # 前缀错
    assert not is_valid_member_code(code + "M")  # 多一位
    assert normalize_member_code(" m234567a ") == "M234567A"


def test_child_created_with_member_code(client: TestClient):
    """建孩即发码，且不同孩子码不同（唯一索引兜底）。"""
    h = _h(client)
    _p1, c1, _mini1 = _family(client, h, "13981014001", "发码孩甲")
    _p2, c2, _mini2 = _family(client, h, "13981014002", "发码孩乙")

    rows = client.get("/api/admin/members/children?page=1&page_size=50", headers=h).json()
    by_id = {r["id"]: r for r in rows["items"]}
    code1, code2 = by_id[c1["id"]].get("member_code"), by_id[c2["id"]].get("member_code")
    assert code1 and code2, "建孩必须自动发码"
    assert code1 != code2
    assert is_valid_member_code(code1) and is_valid_member_code(code2)


def test_card_by_member_code(client: TestClient):
    """按码取卡片：命中 / 码不合法 422 / 合法但不存在 404（两种错误分开）。"""
    h = _h(client)
    _p, c, _mini = _family(client, h, "13981014003", "扫码头孩")
    code = _member_code_of(client, h, c["id"])

    r = client.get(f"/api/admin/circulation/children/by-code/{code}/card", headers=h)
    assert r.status_code == 200, r.text
    assert r.json()["child_id"] == c["id"]
    assert r.json()["name"] == "扫码头孩"

    # 小写 + 空白也要认（扫码枪/手输常态）
    r2 = client.get(f"/api/admin/circulation/children/by-code/{code.lower()}/card", headers=h)
    assert r2.status_code == 200, r2.text

    # 错一位 → 422「会员码不合法」（校验位拦下，不进查询）
    bad = code[:-1] + ALPHABET[(ALPHABET.index(code[-1]) + 1) % len(ALPHABET)]
    r3 = client.get(f"/api/admin/circulation/children/by-code/{bad}/card", headers=h)
    assert r3.status_code == 422, r3.text
    assert "不合法" in r3.json()["detail"]

    # 合法但库里没有 → 404「未找到该会员码对应的孩子」
    ghost = generate_member_code()
    r4 = client.get(f"/api/admin/circulation/children/by-code/{ghost}/card", headers=h)
    assert r4.status_code == 404, r4.text
    assert "未找到" in r4.json()["detail"]


def test_miniapp_children_payload_has_member_code(client: TestClient):
    """小程序孩子载荷必须带 member_code（「会员码」页据此出码）。"""
    h = _h(client)
    _p, c, mini = _family(client, h, "13981014004", "载荷码孩")
    body = client.get(f"/api/miniapp/children?child_id={c['id']}", headers=mini).json()
    items = body["children"] if isinstance(body, dict) else body
    assert items, f"载荷结构异常：{body}"
    target = [x for x in items if x["id"] == c["id"]][0]
    assert target.get("member_code"), "小程序载荷缺 member_code"
    assert is_valid_member_code(target["member_code"])


def _member_code_of(client: TestClient, h: dict, child_id: int) -> str:
    rows = client.get("/api/admin/members/children?page=1&page_size=50", headers=h).json()
    return {r["id"]: r.get("member_code") for r in rows["items"]}[child_id]
