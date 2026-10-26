# tests/unit/test_compliance_copy_20261026.py — 合规口径静态判据（docs/22）
"""小程序对客文案与支付描述的合规口径判据（2026-10-26，甲方合规要求）。

口径见 `docs/22-小程序合规口径与对客文案规范-20261026.md`：
- 主体无出版/出租/教育培训资质，小程序类目「工具-预约/报名」；
- 费用一律呈现为**线下服务费**（线上仅是付款方式）；
- 图书借阅/活动为线下实体服务；随书音频与阅读小挑战为会员服务附赠，不单独售卖。

三条判据（均可证伪）：
1. 小程序**用户可见层**（wxml 去注释文本、json 导航/tabBar、js 去注释源码）
   不得出现禁用词（出租/租赁/课程/培训/教育/教学/课时/图书馆/绘本馆/老师/
   测验/成绩/英语/分级阅读/听书/会员费/年费/线上开通/立即开通）。
2. 后端 `TYPE_LABELS` 六条必须是线下服务/图书借阅口径；支付描述模板不得含"课程"。
3. 每个引用 `session.*` 的小程序页面 JS 必须有模块级 require
   （`purchase.js` 曾在函数内局部 require、模块级裸用 → 进页即 ReferenceError）。

RED（可证伪）：
- 把任意一条 TYPE_LABELS 改回「观察期会员费」→ 第 2 条红；
- 在任一 wxml 文本里写回「测验」→ 第 1 条红；
- 删 `purchase.js` 顶部 `const session = require(...)` → 第 3 条红。
"""

from __future__ import annotations

import ast
import json
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent.parent
MINIAPP = ROOT / "miniapp"

#: 对客可见层禁用词（docs/22 §二.4 全量 + 本次改造统一替换的词）
FORBIDDEN = (
    "出租",
    "租赁",
    "课程",
    "培训",
    "教育",
    "教学",
    "课时",
    "图书馆",
    "绘本馆",
    "老师",
    "测验",
    "成绩",
    "英语",
    "分级阅读",
    "听书",
    "会员费",
    "年费",
    "线上开通",
    "立即开通",
)

#: 预期支付/订单文案（三个消费点同源：方案名 / 订单备注 / 微信支付描述）
EXPECTED_TYPE_LABELS = {
    "TYPE_FIRST_ACTIVITY": "线下亲子活动服务费（首场）",
    "TYPE_OBSERVATION": "线下会员服务费（观察期）",
    "TYPE_FORMAL": "线下会员服务费（年度）",
    "TYPE_DEPOSIT": "图书借阅押金",
    "TYPE_DEPOSIT_SUPPLEMENT": "图书借阅押金补缴",
    "TYPE_ACTIVITY": "线下活动服务费",
}


def _strip_wxml_comments(src: str) -> str:
    return re.sub(r"<!--.*?-->", "", src, flags=re.S)


def _strip_js_comments(src: str) -> str:
    """去掉 // 与 /* */ 注释，但保留字符串内容（字符串里的 // 不算注释）。"""
    out: list[str] = []
    i = 0
    n = len(src)
    state: str | None = None  # None | "'" | '"' | "`" | "//" | "/*"
    while i < n:
        ch = src[i]
        nxt = src[i + 1] if i + 1 < n else ""
        if state is None:
            if ch == "/" and nxt == "/":
                state = "//"
                i += 2
                continue
            if ch == "/" and nxt == "*":
                state = "/*"
                i += 2
                continue
            if ch in ("'", '"', "`"):
                state = ch
            out.append(ch)
            i += 1
            continue
        if state in ("'", '"', "`"):
            out.append(ch)
            if ch == "\\":
                if nxt:
                    out.append(nxt)
                i += 2
                continue
            if ch == state:
                state = None
            i += 1
            continue
        if state == "//":
            if ch == "\n":
                state = None
                out.append(ch)
            i += 1
            continue
        # state == "/*"
        if ch == "*" and nxt == "/":
            state = None
            i += 2
            continue
        i += 1
    return "".join(out)


def _miniapp_wxml_files() -> list[Path]:
    return sorted(MINIAPP.rglob("*.wxml"))


def _miniapp_page_json_files() -> list[Path]:
    return sorted(MINIAPP.rglob("*.json"))


def _user_visible_json_values(path: Path) -> list[str]:
    """只取用户可见的 JSON 字段：导航标题 / tabBar 文案。"""
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, UnicodeDecodeError):
        return []
    values: list[str] = []
    if isinstance(data, dict):
        nav = data.get("navigationBarTitleText")
        if isinstance(nav, str):
            values.append(nav)
        tab_bar = data.get("tabBar")
        if isinstance(tab_bar, dict):
            for item in tab_bar.get("list") or []:
                if isinstance(item, dict) and isinstance(item.get("text"), str):
                    values.append(item["text"])
    return values


def test_miniapp_user_visible_copy_has_no_forbidden_terms():
    """判据 1：用户可见层不得出现禁用词（注释不算）。"""
    hits: list[str] = []
    for path in _miniapp_wxml_files():
        text = _strip_wxml_comments(path.read_text(encoding="utf-8"))
        hits += [f"{path.relative_to(ROOT)}: {word}" for word in FORBIDDEN if word in text]
    for path in _miniapp_page_json_files():
        for value in _user_visible_json_values(path):
            hits += [
                f"{path.relative_to(ROOT)}: {word}（{value}）"
                for word in FORBIDDEN
                if word in value
            ]
    for path in sorted(MINIAPP.rglob("*.js")):
        text = _strip_js_comments(path.read_text(encoding="utf-8"))
        hits += [f"{path.relative_to(ROOT)}: {word}" for word in FORBIDDEN if word in text]
    assert hits == [], "对客文案出现合规禁用词（docs/22 §二.4）：\n" + "\n".join(hits)


def test_payment_labels_use_offline_service_wording():
    """判据 2：支付/订单三类展示同源文案必须是线下服务与图书借阅口径。"""
    src = (ROOT / "backend/domain/identity/payment_service.py").read_text(encoding="utf-8")
    tree = ast.parse(src)
    labels: dict[str, str] | None = None
    for node in tree.body:
        if not isinstance(node, ast.Assign):
            continue
        if not any(isinstance(t, ast.Name) and t.id == "TYPE_LABELS" for t in node.targets):
            continue
        assert isinstance(node.value, ast.Dict), "TYPE_LABELS 必须是字面量字典"
        labels = {}
        for key, value in zip(node.value.keys, node.value.values, strict=True):
            assert isinstance(key, ast.Attribute), "TYPE_LABELS 的键必须是 Order.* 常量"
            assert isinstance(value, ast.Constant) and isinstance(value.value, str)
            labels[key.attr] = value.value
    assert labels is not None, "payment_service.py 找不到 TYPE_LABELS"
    assert labels == EXPECTED_TYPE_LABELS, (
        "支付描述/方案名/订单备注口径不符（docs/22 §四.1）：\n"
        f"实际={labels}\n预期={EXPECTED_TYPE_LABELS}"
    )
    for key, value in labels.items():
        banned = [w for w in FORBIDDEN if w in value]
        assert banned == [], f"{key} 含禁用词 {banned}：{value}"

    pay_src = (ROOT / "backend/integrations/wechat/config.py").read_text(encoding="utf-8")
    pay_tree = ast.parse(pay_src)
    # 只认**字符串常量**（注释里的"禁用课程"是口径说明，不是对客文案）
    pay_strings = [
        node.value
        for node in ast.walk(pay_tree)
        if isinstance(node, ast.Constant) and isinstance(node.value, str)
    ]
    for required in (
        "DmkWords亲子阅读活动",
        "DmkWords观察期会员服务",
        "DmkWords年度会员服务",
        "DmkWords图书借阅押金",
        "DmkWords线下活动",
    ):
        assert required in pay_strings, f"支付描述模板缺失线下口径：{required}"
    assert all("课程" not in s for s in pay_strings), (
        f"支付描述模板仍含「课程」（教育培训口径）：{[s for s in pay_strings if '课程' in s]}"
    )


def test_pages_using_session_have_module_level_require():
    """判据 3：页面 JS 只要引用 session.*，就必须在模块顶层 require（防运行期 ReferenceError）。"""
    pattern = re.compile(r"^const\s+session\s*=\s*require\(['\"][^'\"]*utils/session['\"]\)", re.M)
    offenders: list[str] = []
    for path in sorted(MINIAPP.rglob("*.js")):
        src = path.read_text(encoding="utf-8")
        code = _strip_js_comments(src)
        if re.search(r"\bsession\.", code) and not pattern.search(src):
            offenders.append(str(path.relative_to(ROOT)))
    assert offenders == [], (
        "以下文件引用了 session.* 但缺少模块级 require（函数内局部 require 不算）：\n"
        + "\n".join(offenders)
    )


def test_offline_labels_reach_three_consumption_points(client, db, admin_headers, monkeypatch):
    """判据 4：`TYPE_LABELS` 的三个消费点必须同时生效（专家复核明确要求）。

    ① 购买页方案名（`GET /api/miniapp/payment/plans`）
    ② 在线订单备注（`Order.remark`，订单列表可见）
    ③ 微信支付商品描述（网关 `create_order` 的入参）
    """
    from backend.common.gateways.payment.types import PaymentOrderResponse
    from backend.domain.identity.models import Order, Parent
    from backend.domain.identity.payment_service import PaymentService

    phone = "13800009977"
    parent = client.post(
        "/api/admin/members/parents",
        json={"name": "合规文案家长", "phone": phone},
        headers=admin_headers,
    )
    assert parent.status_code == 200, parent.text
    child = client.post(
        f"/api/admin/members/parents/{parent.json()['id']}/children",
        json={"name": "合规文案孩"},
        headers=admin_headers,
    )
    assert child.status_code == 200, child.text
    login = client.post("/api/miniapp/login", json={"phone": phone, "code": "1234"})
    assert login.status_code == 200, login.text
    mini = {"Authorization": f"Bearer {login.json()['token']}"}

    # ① 购买页方案名
    plans = client.get(f"/api/miniapp/payment/plans?child_id={child.json()['id']}", headers=mini)
    assert plans.status_code == 200, plans.text
    labels = {item["order_type"]: item["label"] for item in plans.json()["items"]}
    assert labels["observation_fee"] == "线下会员服务费（观察期）", labels

    # ② 在线订单备注
    created = client.post(
        "/api/miniapp/orders",
        json={"child_id": child.json()["id"], "order_type": "observation_fee"},
        headers=mini,
    )
    assert created.status_code == 200, created.text
    order = db.query(Order).filter(Order.id == created.json()["id"]).first()
    assert order is not None
    assert order.remark == "小程序在线下单 · 线下会员服务费（观察期）", order.remark

    # ③ 微信支付商品描述（假网关只记录入参，不做即时结算）
    row = db.query(Parent).filter(Parent.id == parent.json()["id"]).first()
    row.wechat_openid = "o_compliance_probe"
    db.commit()

    class _CapturingGateway:
        supports_instant_payment = False

        def __init__(self) -> None:
            self.requests: list = []

        async def create_order(self, req):
            self.requests.append(req)
            return PaymentOrderResponse(
                success=True,
                prepay_id="wx_probe",
                pay_params={"package": "prepay_id=wx_probe"},
            )

    gateway = _CapturingGateway()
    monkeypatch.setattr(PaymentService, "_gateway", staticmethod(lambda: gateway))
    paid = client.post(f"/api/miniapp/orders/{created.json()['id']}/pay", headers=mini)
    assert paid.status_code == 200, paid.text
    assert [req.description for req in gateway.requests] == ["线下会员服务费（观察期）"], (
        f"微信支付描述未同步线下口径：{[req.description for req in gateway.requests]}"
    )
