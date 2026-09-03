# tests/unit/test_p0b3_t32_flush_guard.py — P0 第三批 T32 教训 41 防呆扫描校准
"""红测试（扫描器校准）：构造样例函数验证 check_mutation_count_flush 命中/不命中。

- 命中：同函数「X.status = ...」→「COUNT(query+count)」中间无 flush（教训 41
  修复前形态：T5/WM13 回写两案的抽象）
- 不命中：赋值与 COUNT 之间有 flush（修复后形态）/ 无赋值 / 无 COUNT
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))


def _scan(src: str) -> list[str]:
    """对样例源码跑扫描（借临时文件落盘路径）。"""
    import tempfile

    from scripts import verify_architecture as va

    with tempfile.TemporaryDirectory() as td:
        domain_dir = Path(td) / "backend" / "domain"
        domain_dir.mkdir(parents=True)
        f = domain_dir / "sample.py"
        f.write_text(src)
        original = va.ROOT
        va.ROOT = Path(td)
        try:
            return va.check_mutation_count_flush()
        finally:
            va.ROOT = original


HIT = """
def bad_case(self):
    child.member_status = Child.MEMBER_EXPIRED
    n = self.db.query(func.count(Order.id)).filter(Order.id == 1).count()
    return n
"""

CLEAN_FLUSH = """
def good_case(self):
    child.member_status = Child.MEMBER_EXPIRED
    self.db.flush()
    n = self.db.query(func.count(Order.id)).filter(Order.id == 1).count()
    return n
"""

CLEAN_NO_COUNT = """
def also_good(self):
    child.member_status = Child.MEMBER_EXPIRED
    self.db.commit()
"""


def test_mutation_then_count_without_flush_hits():
    alerts = _scan(HIT)
    assert any("bad_case" in a for a in alerts), f"教训 41 模式应命中，实 {alerts}"


def test_flush_between_mutation_and_count_clean():
    alerts = _scan(CLEAN_FLUSH)
    assert not any("good_case" in a for a in alerts), f"flush 后 COUNT 应不命中，实 {alerts}"


def test_no_count_query_clean():
    alerts = _scan(CLEAN_NO_COUNT)
    assert not any("also_good" in a for a in alerts), f"无 COUNT 应不命中，实 {alerts}"
