# tests/unit/test_audit8_p3_admin_labels_20261022.py — 第八轮 P3 管理端族（第一百一十二批）
"""P3 管理端族两条「前端映射落后后端」的守护（第八轮审查 §3.3 管理端族）：

- **审计动作/对象筛选**：`AuditLog.tsx` 原先只列 2 项（`login` / `config.update`），
  而后端 `publish_audit(action="…")` 实际写入 **69 个 action**、21 个 `target_type` →
  其余既**筛不出来**，场景列还显示**裸英文**（运营看不懂）。本批把两张表补齐到全量。
- **收款方式枚举**：`PAY_METHOD_OPTIONS` 原先只 5 项、缺 `wechat`（线上支付回调落库即该值）
  → 管理端订单/退款列表显示裸英文 `wechat`。本批抽到 `constants/pay.ts` 并补齐。

判据（可证伪）：**后端字面量集合 ⊆ 前端映射键集合**——少一个就红。
RED（还原修复）：删掉 `ACTION_LABEL` 里任意一个后端真实存在的 action（例如 `refund.execute`），
或从 `PAY_METHOD_OPTIONS` 删掉 `wechat`，对应断言立即失败。

**覆盖面诚实说明（R11-07，2026-10-26 订正）**：本用例钉两种**字面量**形态——
`publish_audit(action="x")` 关键字 + `catalog/service.py::_audit(admin, "x")` 位置参数
（该封装层的 action 不是 keyword-only，14 个 book/copy/quiz 动作曾因此全部逃逸）。
若将来出现动态拼接的 action（f-string / 变量），本用例看不见——那类新增需靠 code review 兜。
"""

from __future__ import annotations

import ast
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent.parent
BACKEND = ROOT / "backend"
AUDIT_TSX = ROOT / "admin-web/src/pages/AuditLog.tsx"
PAY_TS = ROOT / "admin-web/src/constants/pay.ts"


def _backend_literals(kwarg: str) -> set[str]:
    """扫后端全部 `kwarg="字面量"`（只认小写点分命名，避免误吃注释里的示例）。"""
    pat = re.compile(rf'{kwarg}="([a-z][a-z0-9_.]*)"')
    found: set[str] = set()
    for py in BACKEND.rglob("*.py"):
        found |= set(pat.findall(py.read_text(encoding="utf-8")))
    return found


def _backend_positional_audit_actions() -> set[str]:
    """扫 `_audit(..., "action", ...)` 的位置参数形态（R11-07 根因）。

    `publish_audit` 的 action 是 keyword-only；但 catalog 的封装层
    `_audit(self, admin, action, target, detail, reason="")` 收的是第 2 个位置参数，
    其调用点全是 `_audit(admin, "book.create", …)` ⇒ 只扫 `action="…"` 会整层漏掉。
    """
    found: set[str] = set()
    for py in BACKEND.rglob("*.py"):
        tree = ast.parse(py.read_text(encoding="utf-8"), filename=str(py))
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call) or len(node.args) < 2:
                continue
            func = node.func
            name = getattr(func, "id", None) or getattr(func, "attr", None)
            if name != "_audit":
                continue
            arg = node.args[1]
            if isinstance(arg, ast.Constant) and isinstance(arg.value, str):
                if re.fullmatch(r"[a-z][a-z0-9_.]*", arg.value):
                    found.add(arg.value)
    return found


def _tsx_map_keys(path: Path, const_name: str) -> set[str]:
    """取 TSX 里 `const <NAME> = { "k": "v", … }` 的键集合。"""
    text = path.read_text(encoding="utf-8")
    block = re.search(rf"const {const_name}[^=]*= \{{([\s\S]*?)\n\}};", text)
    assert block, f"找不到 {const_name} 块（{path.name}）"
    return set(re.findall(r'^\s*"([a-z][a-z0-9_.]*)":', block.group(1), re.M))


def _tsx_option_values(path: Path, const_name: str) -> set[str]:
    """取 TSX 里 `const <NAME> = [{ value: "k", label: "v" }, …]` 的 value 集合。"""
    text = path.read_text(encoding="utf-8")
    block = re.search(rf"const {const_name}[^=]*= \[([\s\S]*?)\n\];", text)
    assert block, f"找不到 {const_name} 数组（{path.name}）"
    return set(re.findall(r'value:\s*"([a-z][a-z0-9_]*)"', block.group(1)))


def test_audit_action_labels_cover_backend_actions():
    """审计页动作标签必须覆盖后端**全部** action 字面量（含 `_audit` 位置形态）。"""
    backend = _backend_literals("action") | _backend_positional_audit_actions()
    frontend = _tsx_map_keys(AUDIT_TSX, "ACTION_LABEL")
    missing = sorted(backend - frontend)
    assert not missing, (
        f"后端有 {len(backend)} 个 action，前端 ACTION_LABEL 缺 {len(missing)} 个：{missing}"
        "（缺的既筛不出来、又显示裸英文——新增 action 必须同步 AuditLog.tsx）"
    )


def test_audit_target_labels_cover_backend_target_types():
    """对象类型标签同理（后端 21 个 target_type 必须都有中文名）。"""
    backend = _backend_literals("target_type")
    frontend = _tsx_map_keys(AUDIT_TSX, "TARGET_LABEL")
    missing = sorted(backend - frontend)
    assert not missing, f"前端 TARGET_LABEL 缺：{missing}"


def test_pay_method_options_cover_backend_enum():
    """收款方式枚举必须覆盖后端 schema 的 pattern（含线上支付回调写入的 `wechat`）。

    RED（还原修复：删掉 wechat 项）：后端 `payment_service.py` 写 `pay_method="wechat"`，
    管理端订单/退款列表回落成裸英文。
    """
    from backend.domain.identity import payment_service  # noqa: F401  触发导入，确保模块存在

    pattern = re.search(
        r'pattern="\^\(([a-z|]+)\)\$"',
        (ROOT / "backend/domain/identity/schemas.py").read_text(encoding="utf-8"),
    )
    assert pattern, "找不到收款方式 pattern（后端契约变更时同步本用例）"
    backend_values = set(pattern.group(1).split("|"))
    frontend = _tsx_option_values(PAY_TS, "PAY_METHOD_OPTIONS")
    missing = sorted(backend_values - frontend)
    assert not missing, f"PAY_METHOD_OPTIONS 缺：{missing}（会显示裸英文）"


def _mask_template_literals(text: str) -> str:
    """把 `` `…` `` 模板串（**可跨行、可含 `${}` 嵌套**）整体涂白，保留换行与长度。

    为什么必须真扫一遍：首版判据是「去掉行内反引号片段」，被**跨行模板串**骗出 9 处假红
    （如 `TaskBoard.tsx:60-61` 的 `` `连续失败 ${n} 次（…）`` 跨了两行）。
    """
    out: list[str] = []
    i, n, depth = 0, len(text), 0
    while i < n:
        ch = text[i]
        if ch == "`":
            depth += 1
            out.append(" ")
            i += 1
            while i < n and depth > 0:
                c = text[i]
                if c == "\\":
                    out.append(" ")
                    if i + 1 < n:
                        out.append("\n" if text[i + 1] == "\n" else " ")
                    i += 2
                    continue
                if c == "$" and i + 1 < n and text[i + 1] == "{":
                    out.append("  ")
                    depth += 1  # `${` 自身开了一个花括号——不计数会提前退出模板态（首版实测）
                    i += 2
                    continue
                if c == "{":
                    depth += 1
                elif c == "}":
                    depth -= 1
                elif c == "`" and depth == 1:
                    depth = 0
                out.append("\n" if c == "\n" else " ")
                i += 1
            continue
        out.append(ch)
        i += 1
    return "".join(out)


def test_no_dollar_literal_in_jsx_text():
    """JSX **文本节点**里的 `$` 会原样渲染（「$99 元」）——按"类"钉住，不只修三处。

    判据：把模板串整体涂白后，若仍出现 `$` 紧邻 `{`，即判违例（模板串里的 `${}` 是正确用法）。
    RED（还原修复）：把 `（{cfgNum(configs, "first_activity_fee", 99)} 元…` 改回
    `（${cfgNum(...)} 元…` → 本用例点名该文件:行。
    """
    bad: list[str] = []
    for tsx in (ROOT / "admin-web/src").rglob("*.tsx"):
        raw = tsx.read_text(encoding="utf-8")
        masked = _mask_template_literals(raw).splitlines()
        for i, line in enumerate(masked, 1):
            if re.search(r"\$\{", line):
                bad.append(f"{tsx.relative_to(ROOT)}:{i}: {raw.splitlines()[i - 1].strip()[:90]}")
    assert not bad, "JSX 文本里出现 `$` 字面量（会原样显示，如「$99 元」）：\n" + "\n".join(bad)
