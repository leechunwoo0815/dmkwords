#!/usr/bin/env python
"""孤儿数据巡检（B5-5，2026-11-27）——**只读**。

**为什么需要它**：全仓 `models.py` 的 `ForeignKey` 计数 = **0**（实测 `grep -rc ForeignKey
backend/domain/*/models.py` 全为 0）。跨表完整性完全靠应用层维护：没有外键，就没有
"父行不许删"的数据库兜底——一次手写 SQL、一次脚本误删、一次导入错位，就会留下
**指向不存在父行的子行**（订单指向不存在的家长、词账指向不存在的孩子……）。
这些行平时看不见，只在对账/退款/统计时以"数字对不上"的形式冒出来。

本脚本把这件事做成**可执行的巡检**（输出孤儿率，超阈值非 0 退出），
据它的结果再决定要不要补外键（审查② P2 的原始建议：先有数据，再决定加约束）。

纪律：
  · **只跑 SELECT**——不写库、不改文件、不需要现场恢复（与 `probe_w3_invariants.py` 同款）；
  · 只看 `is_deleted = 0` 的活跃行（软删行留孤儿是历史清理的正常残留，不算缺陷）；
  · 阈值默认 **0**：任何一条活跃孤儿都是写入口漏了校验，不该"容忍一个比例"。
    真有大扫除需求时用 `--tolerance N` 显式放行，并在巡检记录里写明原因。

用法：
    .venv/bin/python scripts/probe_orphans.py            # 巡检当前 DB（settings 口径）
    .venv/bin/python scripts/probe_orphans.py --tolerance 5
    .venv/bin/python scripts/probe_orphans.py --self-check   # 注入自证（事务内 + 回滚）

退出码：0 = 全部在阈值内；1 = 有检查超阈值；2 = 连不上库/查询失败（fail-closed）。
"""

from __future__ import annotations

import sys
from dataclasses import dataclass

from sqlalchemy import text


@dataclass(frozen=True)
class OrphanCheck:
    """一条"子行指向不存在父行"的检查。"""

    cid: str
    child: str
    fk: str
    parent: str
    note: str


CHECKS: list[OrphanCheck] = [
    # 资金链（最贵：孤儿订单会让退款/开通找不到家长）
    OrphanCheck("ORD-1", "orders", "parent_id", "parents", "订单指向不存在的家长"),
    OrphanCheck("ORD-2", "orders", "child_id", "children", "订单指向不存在的孩子"),
    OrphanCheck(
        "REF-1",
        "refund_requests",
        "order_id",
        "orders",
        "退款单指向不存在的订单（按订单号的回调会找不到单）",
    ),
    OrphanCheck("REF-2", "refund_requests", "child_id", "children", "退款单指向不存在的孩子"),
    OrphanCheck("REF-3", "refund_requests", "deposit_id", "deposits", "退款单指向不存在的押金"),
    OrphanCheck("DPT-1", "deposits", "child_id", "children", "押金指向不存在的孩子"),
    OrphanCheck(
        "DPL-1", "deposit_ledgers", "deposit_id", "deposits", "押金流水指向不存在的押金（对账断链）"
    ),
    OrphanCheck("WDR-1", "withdrawal_requests", "child_id", "children", "退会申请指向不存在的孩子"),
    # 借还/预约（副本与书目是两跳，缺一跳就出现"借了不存在的副本"）
    OrphanCheck("BRW-1", "borrow_records", "child_id", "children", "借阅记录指向不存在的孩子"),
    OrphanCheck("BRW-2", "borrow_records", "copy_id", "book_copies", "借阅记录指向不存在的副本"),
    OrphanCheck("RSV-1", "reservations", "child_id", "children", "预约指向不存在的孩子"),
    OrphanCheck("RSV-2", "reservations", "copy_id", "book_copies", "预约指向不存在的副本"),
    # 成长/词账（统计口径会凭空多出分数）
    OrphanCheck("WLD-1", "words_ledgers", "child_id", "children", "词账指向不存在的孩子"),
    OrphanCheck("PTL-1", "point_ledgers", "child_id", "children", "积分流水指向不存在的孩子"),
    OrphanCheck("CIN-1", "checkins", "child_id", "children", "打卡指向不存在的孩子"),
    OrphanCheck("ENR-1", "activity_enrollments", "child_id", "children", "报名指向不存在的孩子"),
]


def _orphan_sql(c: OrphanCheck) -> str:
    """孤儿行（LEFT JOIN 比 NOT IN 安全：NOT IN 遇 NULL 会整条判假——静默漏报）。"""
    return (
        f"SELECT c.id, c.{c.fk} FROM {c.child} c "
        f"LEFT JOIN {c.parent} p ON p.id = c.{c.fk} "
        f"WHERE c.is_deleted = 0 AND c.{c.fk} IS NOT NULL AND p.id IS NULL "
        f"ORDER BY c.id LIMIT 5"
    )


def _total_sql(c: OrphanCheck) -> str:
    return f"SELECT COUNT(*) FROM {c.child} c WHERE c.is_deleted = 0 AND c.{c.fk} IS NOT NULL"


def _scan(conn, tolerance: int) -> tuple[int, list[str]]:
    """跑全部检查，返回 (超阈值条数, 失败明细)。"""
    over = 0
    details: list[str] = []
    print(f"{'检查':<7}{'表.列':<34}{'总数':>8}{'孤儿':>8}{'孤儿率':>10}  结论")
    for c in CHECKS:
        total = conn.execute(text(_total_sql(c))).scalar() or 0
        rows = conn.execute(text(_orphan_sql(c))).fetchall()
        orphans = len(rows) if total else 0
        # 抽样只取 5 行，真实计数另跑一次（孤儿数可能 > 5，展示用样本就够定位）
        if orphans >= 5:
            orphans = (
                conn.execute(
                    text(
                        f"SELECT COUNT(*) FROM {c.child} c "
                        f"LEFT JOIN {c.parent} p ON p.id = c.{c.fk} "
                        f"WHERE c.is_deleted = 0 AND c.{c.fk} IS NOT NULL AND p.id IS NULL"
                    )
                ).scalar()
                or 0
            )
        rate = (orphans / total * 100) if total else 0.0
        bad = orphans > tolerance
        if bad:
            over += 1
            details.append(
                f"{c.cid} {c.child}.{c.fk} → {c.parent}：{orphans}/{total} 孤儿（{c.note}）"
            )
            for row in rows[:3]:
                details.append(f"    样本：id={row[0]} {c.fk}={row[1]}")
        print(
            f"{c.cid:<7}{c.child + '.' + c.fk:<34}{total:>8}{orphans:>8}{rate:>9.2f}%  "
            f"{'✗' if bad else '✓'}"
        )
    return over, details


def self_check() -> int:
    """注入自证：造两条真实孤儿（事务内 + 回滚），证明检查真的会变红。

    为什么必须自证：一个永远返回 0 行的巡检脚本看起来和"数据很干净"完全一样——
    这正是本项目历史上最贵的失败模式（"改了检查器没人知道"）。
    """
    from backend.database import engine

    injections = [
        (
            "ORD-1/ORD-2",
            """
            INSERT INTO orders
              (order_no, order_type, parent_id, child_id, amount, status, remark,
               create_time, update_time, refund_status)
            VALUES ('SELFTEST-ORPHAN-B55', 'membership', -999001, -999002, 0.01, 'pending',
                    'B5-5 自证注入', NOW(), NOW(), '')
            """,
            ("ORD-1", "ORD-2"),
        ),
        (
            "WLD-1",
            """
            INSERT INTO words_ledgers
              (child_id, book_id, word_count, source, created_at, create_time, update_time)
            VALUES (-999001, -999001, 1, 'selftest', NOW(), NOW(), NOW())
            """,
            ("WLD-1",),
        ),
    ]
    failed: list[str] = []
    with engine.connect() as conn:
        tr = conn.begin()
        try:
            for label, sql, checks in injections:
                before = {
                    c.cid: conn.execute(text(_total_sql(c))).scalar() or 0
                    for c in CHECKS
                    if c.cid in checks
                }
                conn.execute(text(sql))
                for c in CHECKS:
                    if c.cid not in checks:
                        continue
                    hit = conn.execute(text(_total_sql(c))).scalar() or 0
                    n = (
                        conn.execute(
                            text(
                                f"SELECT COUNT(*) FROM {c.child} c "
                                f"LEFT JOIN {c.parent} p ON p.id = c.{c.fk} "
                                f"WHERE c.is_deleted = 0 AND c.{c.fk} IS NOT NULL AND p.id IS NULL"
                            )
                        ).scalar()
                        or 0
                    )
                    ok = n > 0 and hit >= before[c.cid]
                    print(f"{'✓' if ok else '✗'} 注入 {label} → {c.cid} 检出孤儿 {n}")
                    if not ok:
                        failed.append(c.cid)
        finally:
            tr.rollback()
    if failed:
        print(f"FAIL：{len(failed)} 条注入后未检出：{failed}（巡检 SQL 写错）")
        return 1
    print(f"PASS：{len(injections)} 组注入均被检出，且已回滚（库未被修改）✓")
    return 0


def main(argv: list[str]) -> int:
    tolerance = 0
    if "--tolerance" in argv:
        try:
            tolerance = int(argv[argv.index("--tolerance") + 1])
        except (IndexError, ValueError):
            print("--tolerance 需要一个整数")
            return 2
    try:
        from backend.database import engine
    except Exception as exc:  # noqa: BLE001 —— 连不上库属于环境问题，显式报错而非静默
        print(f"✗ 无法加载数据库配置：{exc}")
        return 2

    print(f"===== 孤儿数据巡检（B5-5；阈值 {tolerance}）=====")
    try:
        with engine.connect() as conn:
            over, details = _scan(conn, tolerance)
    except Exception as exc:  # noqa: BLE001
        print(f"✗ 巡检执行失败（fail-closed，不当成通过）：{type(exc).__name__}: {exc}")
        return 2

    print()
    if over:
        print(f"✗ {over} 条检查超阈值（tolerance={tolerance}）：")
        for line in details:
            print("  " + line)
        print("  处置：先定位写入口（哪条路径允许父行不存在），再清理孤儿行；")
        print("        连续两轮为 0 后，才考虑把这几列补成真外键。")
        return 1
    print(f"✓ 全部 {len(CHECKS)} 条检查在阈值内：跨表引用完整（无活跃孤儿行）")
    return 0


if __name__ == "__main__":
    if "--self-check" in sys.argv:
        sys.exit(self_check())
    sys.exit(main(sys.argv))
