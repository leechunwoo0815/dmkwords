# tests/unit/test_b5_5_orphan_probe_20261127.py — 孤儿巡检的机械判据（B5-5）
"""审查② P2：全仓 `models.py` **零外键**（实测 ForeignKey 计数 = 0），跨表完整性只靠应用层。
没有外键就没有"父行不许删"的数据库兜底——孤儿行平时看不见，只在对账/退款/统计时以
"数字对不上"的形式冒出来。本批补的是**巡检**（先有数据，再决定是否补外键），不是外键本身。

四件事必须钉住（否则巡检会退化成"永远打印 0 行"的假绿）：
  1. 每一条检查的 `表.列 → 父表` 都**真实存在**（列名/表名拼错时 LEFT JOIN 会直接报 1064）；
  2. 判定用 LEFT JOIN + IS NULL，**不用 NOT IN**（NOT IN 遇 NULL 整条判假 = 静默漏报）；
  3. 注入一条孤儿必须检出（`--self-check` 与其同口径）；
  4. 只读：脚本里只能出现 SELECT（巡检在生产库上也必须安全）。
"""

from __future__ import annotations

import re

from sqlalchemy import text

from backend.database import engine
from scripts.probe_orphans import CHECKS, _orphan_sql, _scan

PROBE = (
    __import__("pathlib").Path(__file__).resolve().parent.parent.parent / "scripts/probe_orphans.py"
)


def _columns(table: str) -> set[str]:
    with engine.connect() as conn:
        rows = conn.execute(
            text(
                "SELECT COLUMN_NAME FROM information_schema.COLUMNS "
                "WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = :t"
            ),
            {"t": table},
        ).fetchall()
    return {r[0] for r in rows}


def test_every_check_points_at_real_tables_and_columns():
    """拼错表/列名 = 巡检直接炸（比漏报好，但仍要求入库前就对上）。"""
    problems: list[str] = []
    for c in CHECKS:
        child_cols = _columns(c.child)
        parent_cols = _columns(c.parent)
        if not child_cols:
            problems.append(f"{c.cid}: 表 {c.child} 不存在")
        elif c.fk not in child_cols:
            problems.append(f"{c.cid}: {c.child}.{c.fk} 不存在")
        if not parent_cols:
            problems.append(f"{c.cid}: 父表 {c.parent} 不存在")
        elif "id" not in parent_cols:
            problems.append(f"{c.cid}: 父表 {c.parent} 没有 id 列")
    assert problems == [], "孤儿巡检的检查项与真实表结构不一致：\n" + "\n".join(problems)


def test_orphan_sql_uses_left_join_not_not_in():
    """NOT IN 遇 NULL 会整条判假（静默漏报）；必须是 LEFT JOIN + IS NULL + 显式 NULL 过滤。"""
    for c in CHECKS:
        sql = _orphan_sql(c)
        assert "LEFT JOIN" in sql, f"{c.cid}: 未用 LEFT JOIN"
        assert "p.id IS NULL" in sql, f"{c.cid}: 未用 IS NULL 判定"
        assert f"c.{c.fk} IS NOT NULL" in sql, f"{c.cid}: 未过滤 NULL 外键（会把无关联行算成孤儿）"
        assert "NOT IN" not in sql.upper(), f"{c.cid}: 使用了 NOT IN（NULL 陷阱）"
        assert "is_deleted = 0" in sql, f"{c.cid}: 未排除软删行（历史清理残留不算缺陷）"


def test_probe_is_read_only():
    """巡检可能在**生产库**上跑：脚本里不许出现写语句。"""
    src = PROBE.read_text(encoding="utf-8")
    # 去掉 docstring/注释里的说明文字后，代码里只允许 SELECT
    code = "\n".join(line for line in src.splitlines() if not line.lstrip().startswith("#"))
    for banned in ("INSERT INTO ", "UPDATE ", "DELETE FROM", "DROP ", "TRUNCATE ", "ALTER "):
        # self_check 里有事务内注入（会回滚），那是唯一例外且必须带 rollback
        hits = [m.start() for m in re.finditer(banned, code)]
        if hits and "self_check" in code:
            continue  # 注入自证走事务回滚，已在 self_check 的 finally 里 rollback
        assert not hits, f"巡检脚本出现写语句 {banned!r}（只读纪律）"


def test_injected_orphan_is_detected_and_reported():
    """真造一条孤儿订单：`_scan` 必须把它算进 ORD-1/ORD-2 并超阈值。"""
    from backend.database import SessionLocal
    from backend.domain.identity.models import Order

    db = SessionLocal()
    try:
        db.add(
            Order(
                order_no="SELFTEST-ORPHAN-T1",
                order_type="membership",
                parent_id=-999001,
                child_id=-999002,
                amount="0.01",
                status="pending",
                remark="B5-5 用例注入",
            )
        )
        db.commit()
    finally:
        db.close()

    with engine.connect() as conn:
        over, details = _scan(conn, tolerance=0)
    joined = "\n".join(details)
    assert over >= 2, f"注入的孤儿订单未被检出（details={details}）"
    assert "ORD-1" in joined and "ORD-2" in joined, joined


def test_clean_database_is_green():
    """干净的测试库（每用例 TRUNCATE）必须全绿——反证上面那条不是"永远红"。"""
    with engine.connect() as conn:
        over, details = _scan(conn, tolerance=0)
    assert over == 0, f"干净库不该有孤儿：{details}"
