# tests/unit/test_audit10_b8_uncertainties_20261028.py — 第十一轮「存疑」处置判据（B8）
"""B8 批：存疑清单里可直接验证的条目。

覆盖：
1. 退款金额舍入统一 ROUND_HALF_UP（0.005 边界与支付网关一致）；
2. 两个检查器的**活性钉**（把检测器改坏 → self_test 必须报失败，防"自证变死码"）；
3. CI 与本地 MySQL 同构（时区/字符集）与 Dockerfile 依赖层顺序（文本断言，已登记 scripts/ 前缀）。
"""

from __future__ import annotations

from datetime import datetime, timedelta
from decimal import Decimal
from types import SimpleNamespace

from backend.domain.identity.models import Order
from backend.domain.identity.refund_rules import refundable_amount
from tests.unit.test_p1_audit_20261015 import _db


def test_refund_amount_rounds_half_up():
    """观察期 99.75 元、已用 29/30 天 → 3.325 元；HALF_UP = 3.33（HALF_EVEN 会得 3.32）。"""
    with _db() as db:
        order = SimpleNamespace(
            order_type=Order.TYPE_OBSERVATION,
            amount=Decimal("99.75"),
            paid_at=datetime.now() - timedelta(days=29),
        )
        assert refundable_amount(db, order) == Decimal("3.33")


def test_media_discipline_selftest_is_alive(monkeypatch):
    """把写盘点检测器改成恒空 → 自证必须失败（否则自证是死码）。"""
    from scripts import check_media_discipline as mod

    monkeypatch.setattr(mod, "scan_backend_writes", lambda base: [])
    assert mod.self_test() != 0


def test_copy_emoji_selftest_is_alive(monkeypatch):
    """把 emoji 正则改成永不命中 → 区间探针必须失败。"""
    import re

    from scripts import check_copy_emoji as mod

    monkeypatch.setattr(mod, "RE_EMOJI", re.compile(r"^$"))
    assert mod.self_test()


def test_ci_mysql_matches_local_timezone_and_charset():
    """CI 的 MySQL 服务必须带 TZ/字符集/时区（与 docker-compose 同构）。"""
    import pathlib

    root = pathlib.Path(__file__).resolve().parents[2]
    ci = (root / ".github/workflows/ci.yml").read_text(encoding="utf-8")
    assert "TZ: Asia/Shanghai" in ci
    assert "--default-time-zone=+08:00" in ci
    assert "--character-set-server=utf8mb4" in ci


def test_dockerfile_installs_project_after_copy():
    """依赖层先 `--no-install-project`，COPY 之后再装本包（否则 venv 里是空映射）。"""
    import pathlib

    root = pathlib.Path(__file__).resolve().parents[2]
    dockerfile = (root / "Dockerfile").read_text(encoding="utf-8")
    assert "uv sync --frozen --no-dev --no-install-project" in dockerfile
    copy = dockerfile.index("COPY . .")
    assert dockerfile.index("uv sync --frozen --no-dev\n", copy) > copy
