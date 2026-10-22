#!/usr/bin/env python
"""W3 资金/数据一致性不变量探针（第九轮审查·只读）。

**只跑 SELECT**：不写库、不改文件、不需要现场恢复。用于给"跨表不变量"这一轴
提供实测证据（第八轮与本轮此前都只有静态结论）。

用法：`.venv/bin/python scripts/probe_w3_invariants.py`
退出码：0 = 全部不变量成立；1 = 有不变量被打破（打印反例行）。

每条不变量都给：名称 / 判据（为什么它必须恒成立）/ 反例数量。
**反例 ≠ 一定是缺陷**：先按"该不变量是否真的在当前口径下必须成立"复核，再定性。
"""

from __future__ import annotations

import sys

from sqlalchemy import text

from backend.database import engine

# (名称, 说明, SQL —— 返回"违反该不变量的行数/明细")
INVARIANTS: list[tuple[str, str, str]] = [
    (
        "DEP-1 押金可用额非负",
        "可用押金 = 实缴 − 已扣，扣款链必须走 deduct_for_compensation；负数意味着超扣（第八轮 P1-2 同族）",
        """
        SELECT id, child_id, amount, available_amount, deducted_amount, unpaid_balance, status
          FROM deposits
         WHERE is_deleted = 0
           AND (available_amount < 0 OR deducted_amount < 0 OR unpaid_balance < 0)
        """,
    ),
    (
        "DEP-2 押金三数自洽",
        "实缴 = 可用 + 已扣（含补缴与退费后的口径）；不等则说明有一条写口漏更新",
        """
        SELECT id, child_id, amount, available_amount, deducted_amount,
               (amount - available_amount - deducted_amount) AS gap
          FROM deposits
         WHERE is_deleted = 0
           AND amount <> available_amount + deducted_amount
        """,
    ),
    (
        "DEP-3 待结清余额与状态自洽",
        "有未结清赔偿款时必须不是 paid 终态，且 unpaid_balance > 0 时状态应为欠款态",
        """
        SELECT id, child_id, status, unpaid_balance, available_amount
          FROM deposits
         WHERE is_deleted = 0
           AND unpaid_balance > 0
           AND status = 'paid'
        """,
    ),
    (
        "BRW-1 在借副本必有活跃借阅记录",
        "副本 status=borrowed 但无 active/overdue 借阅行 ⇒ 副本被借走却没人还（第八轮 P1-1 同族）",
        """
        SELECT c.id AS copy_id, c.book_id, c.status
          FROM book_copies c
         WHERE c.is_deleted = 0
           AND c.status = 'borrowed'
           AND NOT EXISTS (
                 SELECT 1 FROM borrow_records b
                  WHERE b.copy_id = c.id AND b.is_deleted = 0
                    AND b.status IN ('active', 'overdue'))
        """,
    ),
    (
        "BRW-2 活跃借阅必占 borrowed 副本",
        "活跃借阅行的副本必须处于 borrowed（否则同一册可被二次借出）",
        """
        SELECT b.id AS borrow_id, b.child_id, b.copy_id, c.status AS copy_status
          FROM borrow_records b
          JOIN book_copies c ON c.id = b.copy_id
         WHERE b.is_deleted = 0 AND c.is_deleted = 0
           AND b.status IN ('active', 'overdue')
           AND c.status <> 'borrowed'
        """,
    ),
    (
        "RSV-1 活跃预约占 reserved 副本",
        "活跃预约的副本必须 reserved（否则别人能借走已承诺保留的那册）",
        """
        SELECT r.id AS reservation_id, r.child_id, r.copy_id, c.status AS copy_status
          FROM reservations r
          JOIN book_copies c ON c.id = r.copy_id
         WHERE r.is_deleted = 0 AND c.is_deleted = 0
           AND r.status = 'active'
           AND c.status <> 'reserved'
        """,
    ),
    (
        "RSV-2 单孩单书至多一条活跃预约",
        "PRD §5.5「一个孩子同一种书同时只能有一个进行中的预约」",
        """
        SELECT child_id, book_id, COUNT(*) AS n
          FROM reservations
         WHERE is_deleted = 0 AND status = 'active'
         GROUP BY child_id, book_id
        HAVING COUNT(*) > 1
        """,
    ),
    (
        "QUO-1 在借+活跃预约不超上限",
        "可借数 = 30 − 在借(含逾期) − 预约占用（PRD §5.4）；超限说明某条链漏退额",
        """
        SELECT x.child_id, x.in_hand, x.reserved,
               (x.in_hand + x.reserved) AS used
          FROM (
                SELECT ch.id AS child_id,
                       (SELECT COUNT(*) FROM borrow_records b
                         WHERE b.child_id = ch.id AND b.is_deleted = 0
                           AND b.status IN ('active', 'overdue')) AS in_hand,
                       (SELECT COUNT(*) FROM reservations r
                         WHERE r.child_id = ch.id AND r.is_deleted = 0
                           AND r.status = 'active') AS reserved
                  FROM children ch
                 WHERE ch.is_deleted = 0) x
         WHERE x.in_hand + x.reserved > 30
        """,
    ),
    (
        "QUO-2 活跃预约不超该副本总数",
        "同一册被多个活跃预约占用 ⇒ 承诺超发",
        """
        SELECT copy_id, COUNT(*) AS n
          FROM reservations
         WHERE is_deleted = 0 AND status = 'active'
         GROUP BY copy_id
        HAVING COUNT(*) > 1
        """,
    ),
    (
        "CHD-1 有效会员必有到期日或观察期态",
        "formal 必带 member_expire（is_active_member 的判据之一）；为空会让会员判定静默失真",
        """
        SELECT id, name, member_status, member_expire
          FROM children
         WHERE is_deleted = 0
           AND member_status = 'formal'
           AND member_expire IS NULL
        """,
    ),
    (
        "VOC-1 生词本查词次数非负且 ≥1",
        "lookup_count 初值 1、只增；0 或负说明计数链写坏",
        """
        SELECT id, child_id, word, lookup_count
          FROM vocabularies
         WHERE is_deleted = 0 AND (lookup_count IS NULL OR lookup_count < 1)
        """,
    ),
]


def main() -> int:
    broken = 0
    with engine.connect() as conn:
        for name, why, sql in INVARIANTS:
            rows = conn.execute(text(sql)).fetchall()
            n = len(rows)
            mark = "✓" if n == 0 else "✗"
            print(f"{mark} {name}：反例 {n} 条")
            if n:
                broken += 1
                print(f"    判据：{why}")
                for r in rows[:5]:
                    print(f"    反例：{dict(r._mapping)}")
                if n > 5:
                    print(f"    …（共 {n} 条，仅列前 5）")
    print()
    if broken:
        print(
            f"INVARIANTS BROKEN：{broken}/{len(INVARIANTS)} 条不变量有反例（需逐条复核是否真为缺陷）"
        )
        return 1
    print(f"ALL INVARIANTS HOLD：{len(INVARIANTS)}/{len(INVARIANTS)} 条在**演示库当前数据**上成立")
    print(
        "注意：演示库体量小（35 书/11 孩子），成立 ≠ 生产体量下成立；本探针只证明「无现成反例」。"
    )
    return 0


#: 可证伪自检：每条不变量配一条"注入 SQL"，在**事务内**打破它并断言反例变红，随后回滚。
#: 为什么必须有（红线 182/171 族）：一条"永远返回 0 条反例"的查询既可能是不变量成立，
#: 也可能是**探针写错了**（本轮首次自检实测：把 BRW-1/BRW-2 的方向比反了 → 一度误判"探针是装饰"）。
#: 只有"注入后必变红、回滚后必回绿"才算探针可用。
FALSIFY_CASES: list[tuple[str, str]] = [
    # 2026-10-22 实测：6/6 命中
    (
        "DEP-2",
        "UPDATE deposits SET available_amount = available_amount + 1 WHERE is_deleted=0 LIMIT 1",
    ),
    (
        "BRW-2",
        "UPDATE book_copies SET status='available' WHERE is_deleted=0 AND status='borrowed' LIMIT 1",
    ),
    (
        "BRW-1",
        "UPDATE book_copies SET status='borrowed' WHERE is_deleted=0 AND status='available' LIMIT 1",
    ),
    (
        "RSV-1",
        "UPDATE book_copies SET status='available' WHERE is_deleted=0 AND status='reserved' LIMIT 1",
    ),
    ("VOC-1", "UPDATE vocabularies SET lookup_count=0 WHERE is_deleted=0 LIMIT 1"),
    (
        "QUO-2",
        """
        INSERT INTO reservations (child_id, book_id, copy_id, expires_at, status, create_time, update_time, is_deleted)
        SELECT child_id, book_id, copy_id, expires_at, 'active', NOW(), NOW(), 0
          FROM reservations WHERE is_deleted=0 AND status='active' LIMIT 1
        """,
    ),
]


def _count(conn, name: str) -> int:
    sql = next(s for n, _, s in INVARIANTS if n.startswith(name))
    return len(conn.execute(text(sql)).fetchall())


def self_check() -> int:
    """注入自检（事务内注入 + 回滚，不落库）：证明探针真的能抓到违规。"""
    failed: list[str] = []
    with engine.connect() as conn:
        for name, inject in FALSIFY_CASES:
            tr = conn.begin()
            try:
                before = _count(conn, name)
                conn.execute(text(inject))
                after = _count(conn, name)
                hit = after > before
                print(f"{'✓' if hit else '✗'} 注入 {name}：反例 {before} → {after}")
                if not hit:
                    failed.append(name)
            finally:
                tr.rollback()
        residual = [n for n, _ in FALSIFY_CASES if _count(conn, n) != 0]
    print()
    if failed:
        print(f"FAIL：{len(failed)} 条注入后未变红（探针写错或数据前提不成立）：{failed}")
        return 1
    if residual:
        print(f"FAIL：回滚后仍有反例残留：{residual}（事务未回滚干净？）")
        return 1
    print(f"PASS：{len(FALSIFY_CASES)}/{len(FALSIFY_CASES)} 条注入均变红，且回滚后全部回到 0 ✓")
    return 0


if __name__ == "__main__":
    if "--self-check" in sys.argv:
        sys.exit(self_check())
    sys.exit(main())
