# tests/unit/test_review10_b3c_state_matrices_20261025.py — 第十轮整改 B3（余）：W1-4 状态机矩阵
"""`W1-4`：六个资金/库存/活动状态机补**声明式转移矩阵**并把写点接上断言；另两条静态守卫，
防止"新增模型忘了画矩阵""新增写点绕开断言"。

RED（修复前）：`Deposit`/`Reservation`/`ActivityEnrollment`/`BorrowRecord`/`TransferRequest`/`Activity`
六者都没有 `ALLOWED_TRANSITIONS`（`Deposit` 注释却自称"R-312 状态机"），任何新增/旁路写点直赋状态
都没有集中校验点；`grep -rn ALLOWED_TRANSITIONS backend/domain` 只能命中 identity/catalog 那几处。
"""

from __future__ import annotations

import ast
import pathlib

import pytest

from backend.common.exceptions import ConflictError
from backend.domain.activity.models import Activity, ActivityEnrollment
from backend.domain.billing.models import Deposit
from backend.domain.circulation.models import BorrowRecord
from backend.domain.identity.models import TransferRequest
from backend.domain.reading.models import Reservation

ROOT = pathlib.Path(__file__).resolve().parents[2]

#: 被测的六个状态机（模型 → 状态常量前缀）
MODELS = [Activity, ActivityEnrollment, BorrowRecord, Deposit, Reservation, TransferRequest]


def _status_constants(model) -> set[str]:
    return {
        v
        for k, v in vars(model).items()
        if k.startswith("STATUS_") and isinstance(v, str) and not isinstance(v, bool)
    }


@pytest.mark.parametrize("model", MODELS, ids=lambda m: m.__name__)
def test_matrix_is_self_consistent(model):
    """矩阵必须覆盖**每一个**声明的状态常量，且目标值都是真实状态（防拼写/防漏态）。"""
    declared = _status_constants(model)
    keys = set(model.ALLOWED_TRANSITIONS)
    assert keys == declared, f"矩阵键与状态常量不一致：缺 {declared - keys} / 多 {keys - declared}"
    for src, targets in model.ALLOWED_TRANSITIONS.items():
        assert targets <= declared, f"{src} 的边上出现未声明状态：{targets - declared}"


@pytest.mark.parametrize("model", MODELS, ids=lambda m: m.__name__)
def test_illegal_edges_raise_and_self_write_is_allowed(model):
    """非法边抛 `ConflictError`；**同值写放行**（幂等写回是常态，不能因此炸线上）。"""
    declared = sorted(_status_constants(model))
    legal = {s: set(t) for s, t in model.ALLOWED_TRANSITIONS.items()}
    illegal_pairs = [
        (src, dst) for src in declared for dst in declared if dst != src and dst not in legal[src]
    ]
    assert illegal_pairs, f"{model.__name__} 居然没有非法边？（矩阵可能被写成全集）"

    for src, dst in illegal_pairs[:6]:
        obj = model(status=src)
        with pytest.raises(ConflictError):
            obj.assert_transition(dst)
    obj = model(status=declared[0])
    obj.assert_transition(declared[0])  # 同值写不抛
    for dst in sorted(legal[declared[0]]):
        model(status=declared[0]).assert_transition(dst)


def test_key_legal_edges_are_frozen():
    """关键合法边钉子：押金（缴/扣/补/退）、预约（核销/取消/超时/异常）、借阅（逾期/还/丢）、
    活动报名（待支付→报名→签到、退款链）、活动（结束/取消）、转让（审核四出口）。

    这些边是**实测**出来的（补边过程见 docs/09 第一百一十五批：在线退款执行完直接从余额态落
    `refunded`，是被 4 条既有测试顶出来的）。
    """
    D = Deposit
    assert D.STATUS_PAID in D.ALLOWED_TRANSITIONS[D.STATUS_UNPAID]
    assert {D.STATUS_PARTIALLY_DEDUCTED, D.STATUS_FULLY_DEDUCTED} <= D.ALLOWED_TRANSITIONS[
        D.STATUS_PAID
    ]
    assert D.STATUS_PAID in D.ALLOWED_TRANSITIONS[D.STATUS_FULLY_DEDUCTED]  # 补缴回 paid
    assert D.STATUS_REFUNDED in D.ALLOWED_TRANSITIONS[D.STATUS_REFUNDING]
    assert D.STATUS_UNPAID in D.ALLOWED_TRANSITIONS[D.STATUS_REFUNDED]  # 重新入会归零
    for st in (D.STATUS_PAID, D.STATUS_PARTIALLY_DEDUCTED, D.STATUS_FULLY_DEDUCTED):
        assert D.STATUS_REFUNDED in D.ALLOWED_TRANSITIONS[st], "在线退款直落终态（实测边）"

    R = Reservation
    assert R.ALLOWED_TRANSITIONS[R.STATUS_ACTIVE] == {
        R.STATUS_EXPIRED,
        R.STATUS_CANCELLED,
        R.STATUS_CHECKED_OUT,
        R.STATUS_EXCEPTION,
    }
    assert R.ALLOWED_TRANSITIONS[R.STATUS_CHECKED_OUT] == set(), "核销后不得再改状态"

    B = BorrowRecord
    assert B.STATUS_OVERDUE in B.ALLOWED_TRANSITIONS[B.STATUS_ACTIVE]
    assert B.STATUS_ACTIVE in B.ALLOWED_TRANSITIONS[B.STATUS_OVERDUE], "续借顺延可回 active"
    # 归还登记在同一事务内先写 returned 再按 condition 覆盖为 lost（实测边，见模型注释）
    assert B.ALLOWED_TRANSITIONS[B.STATUS_RETURNED] == {B.STATUS_LOST}

    E = ActivityEnrollment
    assert E.STATUS_ENROLLED in E.ALLOWED_TRANSITIONS[E.STATUS_PENDING_PAYMENT]
    assert E.STATUS_CHECKED_IN in E.ALLOWED_TRANSITIONS[E.STATUS_ENROLLED]
    assert E.STATUS_ENROLLED in E.ALLOWED_TRANSITIONS[E.STATUS_REFUND_PENDING]  # 退款被拒
    assert E.STATUS_REFUNDED in E.ALLOWED_TRANSITIONS[E.STATUS_REFUND_PENDING]

    A = Activity
    assert A.ALLOWED_TRANSITIONS[A.STATUS_PUBLISHED] == {A.STATUS_CANCELLED, A.STATUS_FINISHED}

    T = TransferRequest
    assert T.ALLOWED_TRANSITIONS[T.STATUS_PENDING] == {
        T.STATUS_APPROVED,
        T.STATUS_REJECTED,
        T.STATUS_CANCELLED,
        T.STATUS_EXPIRED,
    }


# ---------------------------------------------------------------- 静态守卫（防"新模型/新写点绕开矩阵"）


def _string_status_models() -> list[tuple[pathlib.Path, ast.ClassDef]]:
    """扫出所有"status 列是**字符串**状态机"的模型类（整数开关位不算状态机）。"""
    out = []
    for f in pathlib.Path(ROOT / "backend").rglob("models*.py"):
        tree = ast.parse(f.read_text(encoding="utf-8"))
        for node in tree.body:
            if not isinstance(node, ast.ClassDef):
                continue
            for stmt in node.body:
                if not isinstance(stmt, ast.Assign):
                    continue
                if not any(getattr(t, "id", "") == "status" for t in stmt.targets):
                    continue
                call = stmt.value
                if not (isinstance(call, ast.Call) and getattr(call.func, "id", "") == "Column"):
                    continue
                if call.args and isinstance(call.args[0], ast.Call):
                    if getattr(call.args[0].func, "id", "") == "String":
                        out.append((f, node))
    return out


def test_every_string_status_model_has_a_matrix():
    """**含字符串 status 列的模型必须有转移矩阵**（`W1-4` 建议的"静态检查"落点）。

    为什么只认字符串列：`Book.status` / `AdminUser.status` 是 `SmallInteger` 开关位
    （1=上架/启用、0=下架/禁用），不承载流程，硬套矩阵只会制造噪音。
    为什么只要求"矩阵 + can_transition"：identity 的 Order/RefundRequest/WithdrawalRequest 与
    catalog 的 BookCopy 早就有矩阵（本次不改它们的写点）；本批**新接入断言**的是六个
    （见 `test_touched_models_expose_assert_transition` 与写点守卫）。
    """
    found = _string_status_models()
    assert len(found) >= 10, f"扫描面异常：只找到 {len(found)} 个字符串状态机模型"
    missing = []
    for f, node in found:
        body_names = {getattr(s, "name", "") for s in node.body if isinstance(s, ast.FunctionDef)}
        has_matrix = any(
            isinstance(s, ast.Assign)
            and any(getattr(t, "id", "") == "ALLOWED_TRANSITIONS" for t in s.targets)
            for s in node.body
        )
        # 存量 idiom 不统一：Order 只有 can_transition，RefundRequest/WithdrawalRequest 只有
        # assert_transition ⇒ 判据只要求"矩阵 + 二者之一"，但六个新接入的模型必须两者齐（另有用例）
        if not has_matrix or not ({"can_transition", "assert_transition"} & body_names):
            missing.append(f"{f.relative_to(ROOT)}::{node.name}")
    assert missing == [], f"这些状态机模型没有转移矩阵：{missing}"


@pytest.mark.parametrize("model", MODELS, ids=lambda m: m.__name__)
def test_touched_models_expose_assert_transition(model):
    """本批接入的六个模型必须同时提供 `assert_transition`（写点守卫靠它）。"""
    assert callable(model.assert_transition)


def test_every_status_write_goes_through_assert_transition():
    """每个 `X.status = <Model.STATUS_*>` 写点前面都要有 `X.assert_transition(...)`。

    为什么必须有这条：矩阵只是声明，**写点不接等于没接**。判据按"右侧表达式里的模型名"识别
    归属（变量名跨域会重名，光看变量名会误判），并要求写点前 6 行窗口内出现断言调用。
    """
    models = {m.__name__ for m in MODELS}
    offenders = []
    for f in pathlib.Path(ROOT / "backend").rglob("*.py"):
        src = f.read_text(encoding="utf-8", errors="ignore")
        try:
            tree = ast.parse(src)
        except SyntaxError:
            continue
        lines = src.split("\n")
        for node in ast.walk(tree):
            if not isinstance(node, ast.Assign):
                continue
            for t in node.targets:
                if not (
                    isinstance(t, ast.Attribute)
                    and t.attr == "status"
                    and isinstance(t.value, ast.Name)
                ):
                    continue
                expr = ast.unparse(node.value)
                owner = next((m for m in models if f"{m}." in expr), None)
                if owner is None:
                    continue
                window = "\n".join(lines[max(0, node.lineno - 7) : node.lineno - 1])
                if "assert_transition(" not in window:
                    offenders.append(f"{f.relative_to(ROOT)}:{node.lineno} {expr[:40]}")
    assert offenders == [], f"这些写点绕过了状态机断言：{offenders}"
