# tests/unit/test_verify_architecture_lesson41.py — 架构关"教训 41"检查器的自证样例
"""P3-1（第七轮）：检查器原先不看实体，把"改 A 表状态、对 B 表 COUNT"稳定误报成 3 条挂账。

本用例用**合成样例**钉住三条行为（报告建议的"回归/变异"）：
  ① 同实体：`child.status = ...` 后 `query(Child).count()` 无 flush → **必须告警**；
  ② 跨实体：`child.status = ...` 后 `query(TransferRequest).count()` → **零告警**（读旧态与本次赋值无关）；
  ③ 同实体但中间有 `flush()` → **零告警**（顺序正确）；
  ④ 同实体、解析不出模型（如只对属性赋值无 query 绑定）→ 不误报。
"""

from __future__ import annotations

import pathlib
import textwrap

from scripts.verify_architecture import check_mutation_count_flush

SAME_ENTITY = """
from backend.domain.identity.models import Child


class Svc:
    def run(self, db, child_id):
        child = db.query(Child).filter(Child.id == child_id).first()
        child.status = "expired"
        total = db.query(Child).filter(Child.status == "expired").count()
        return total
"""

CROSS_ENTITY = """
from backend.domain.identity.models import Child, TransferRequest


class Svc:
    def run(self, db, child_id, req_id):
        child = db.query(Child).filter(Child.id == child_id).first()
        child.member_status = "formal"
        # 关键：跨实体的 **COUNT**（不是 .first()）——否则这条用例对"看不看实体"不敏感
        pending = db.query(TransferRequest).filter(TransferRequest.status == "pending").count()
        return pending
"""

WITH_FLUSH = """
from backend.domain.identity.models import Child


class Svc:
    def run(self, db, child_id):
        child = db.query(Child).filter(Child.id == child_id).first()
        child.status = "expired"
        db.flush()
        return db.query(Child).filter(Child.status == "expired").count()
"""

UNRESOLVED = """
class Svc:
    def run(self, db, child):
        child.status = "expired"
        return db.query(Child).filter(Child.status == "expired").count()
"""


def _write(root: pathlib.Path, name: str, body: str) -> None:
    d = root / "backend" / "domain" / "demo"
    d.mkdir(parents=True, exist_ok=True)
    (d / f"{name}.py").write_text(textwrap.dedent(body), encoding="utf-8")


def _alerts(tmp_path: pathlib.Path, name: str, body: str) -> list[str]:
    _write(tmp_path, name, body)
    return check_mutation_count_flush(root=tmp_path)


def test_same_entity_alerts(tmp_path):
    """同实体、无 flush → 告警（防呆不能失灵）。"""
    alerts = _alerts(tmp_path, "same", SAME_ENTITY)
    assert alerts, "同实体状态赋值后 COUNT 未 flush 必须告警"
    assert "同一实体 Child" in alerts[0], alerts


def test_cross_entity_no_alert(tmp_path):
    """跨实体 → 零告警（P3-1 的误报正是这一形态）。"""
    assert _alerts(tmp_path, "cross", CROSS_ENTITY) == []


def test_flushed_no_alert(tmp_path):
    """中间有 flush → 零告警（顺序正确）。"""
    assert _alerts(tmp_path, "flushed", WITH_FLUSH) == []


def test_unresolved_receiver_no_alert(tmp_path):
    """解析不出实体（对象由参数传入）→ 宁缺勿滥，不误报。"""
    assert _alerts(tmp_path, "unresolved", UNRESOLVED) == []
