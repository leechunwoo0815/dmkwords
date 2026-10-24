#!/usr/bin/env python3
"""
DmkWords 软删除数据物理清理脚本（第十轮 W3-01 重写，2026-10-24）

用法: python scripts/purge_soft_deleted.py [--dry-run] [--table children]
依赖: 项目 .env 配置（DSN 走 `get_settings().database_url`，与 alembic/env.py 同源）
约束:
  - 默认 --dry-run，仅输出将清理的记录数
  - 生产环境需手动指定 --no-dry-run
  - 实际清理前自动备份待删数据到 backups/purge_YYYYMMDD/
  - 父表删除前校验下游**活跃**引用（防孤儿行）

**为什么重写（第十轮审查 W3-01，P2）**：旧版三处不可执行/不符实——
  ① `create_engine(settings.DATABASE_URL)`：`Settings` **根本没有** `DATABASE_URL` 这个属性
     （全仓仅此一处这么写）→ 连库之前就 `AttributeError`，一行 SQL 都发不出去；
  ② `PURGE_POLICY` 11 个表名是**历史 schema**（`child` / `order` / `reading_session` /
     `voice_recording` / `system_message` …），与当前 42 张真实表 **0/11 命中**；
  ③ `REFERENCE_CHECK` 的子表名同样全是历史名，且查询失败被 `except Exception: pass`
     **静默吞掉** → 引用守卫形同不存在（假阴性）。
现在的口径：策略表名 = 真实表名；引用清单在启动时对 `information_schema` **逐项校验**，
任一表/列不存在即报错退出（不再静默跳过）；校验结果也被单测钉死（改坏→红）。

历史策略键 → 真实表的映射（删除项一并说明，避免"看起来还在管"）：
  child→children、user→parents、order→orders、deposit_record→deposits、
  borrow_record→borrow_records、activity→activities、reservation→reservations、
  system_message→notifications、reading_session→reading_progress；
  `voice_recording` / `book_damage_report` 当前**无对应表**（历史 schema 遗留）→ 不再列策略。
"""

import argparse
import csv
import sys
from datetime import datetime, timedelta
from pathlib import Path

from sqlalchemy import create_engine, text

# 项目根目录加入 sys.path（脚本可独立于 pytest 直接跑）
PROJECT_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_DIR))

from backend.config import get_settings  # noqa: E402

# 清理策略表（键 = 真实表名，必须存在于 information_schema；见 validate_policy）
PURGE_POLICY = {
    "children": {"retention_days": 1095, "reason": "儿童隐私合规"},
    "parents": {"retention_days": 1095, "reason": "账号注销后保留期"},
    "orders": {"retention_days": 1825, "reason": "交易记录法定保留5年"},
    "deposits": {"retention_days": 1095, "reason": "押金记录同步订单"},
    "borrow_records": {"retention_days": 730, "reason": "借阅记录保留2年"},
    "reading_progress": {"retention_days": 365, "reason": "阅读进度数据量大"},
    "notifications": {"retention_days": 180, "reason": "消息数据量大"},
    "activities": {"retention_days": 365, "reason": "活动数据保留1年"},
    "reservations": {"retention_days": 180, "reason": "预约数据量大"},
}

#: 每个父表引用它的**真实子表列**（用于"父行待删但下游仍有活跃引用"时跳过）。
#: 说明：当前库**没有物理外键**（information_schema.REFERENTIAL_CONSTRAINTS = 0），
#: 故这里是人工维护的等价清单——**不是**从 FK 生成（旧注释曾这么自述，与实现不符）。
#: 真实性由 `validate_policy()` 在启动时对 information_schema 逐项校验兜底。
REFERENCE_CHECK = {
    "children": [
        ("activity_enrollments", "child_id", "id"),
        ("borrow_records", "child_id", "id"),
        ("checkin_streak_awards", "child_id", "id"),
        ("checkins", "child_id", "id"),
        ("child_growth_states", "child_id", "id"),
        ("circle_likes", "child_id", "id"),
        ("circle_posts", "child_id", "id"),
        ("circle_rank_snapshots", "child_id", "id"),
        ("deposits", "child_id", "id"),
        ("favorites", "child_id", "id"),
        ("milestone_awards", "child_id", "id"),
        ("notifications", "child_id", "id"),
        ("observation_reports", "child_id", "id"),
        ("orders", "child_id", "id"),
        ("point_ledgers", "child_id", "id"),
        ("quiz_attempts", "child_id", "id"),
        ("reading_progress", "child_id", "id"),
        ("refund_requests", "child_id", "id"),
        ("reservations", "child_id", "id"),
        ("vocabularies", "child_id", "id"),
        ("withdrawal_requests", "child_id", "id"),
        ("words_ledgers", "child_id", "id"),
    ],
    "parents": [
        ("children", "parent_id", "id"),
        ("circle_likes", "parent_id", "id"),
        ("circle_posts", "parent_id", "id"),
        ("notifications", "parent_id", "id"),
        ("orders", "parent_id", "id"),
    ],
    "orders": [
        ("activity_enrollments", "order_id", "id"),
        ("refund_requests", "order_id", "id"),
    ],
    "deposits": [
        ("deposit_ledgers", "deposit_id", "id"),
        ("refund_requests", "deposit_id", "id"),
    ],
    "activities": [("activity_enrollments", "activity_id", "id")],
    # 无下游引用列（schema 实测）：删除时无需父行守卫
    "borrow_records": [],
    "reading_progress": [],
    "notifications": [],
    "reservations": [],
}


def validate_policy(engine) -> list[str]:
    """校验 PURGE_POLICY / REFERENCE_CHECK 的每个表与列在库里真实存在。

    返回问题清单（空 = 全部命中）。**不静默跳过**：调用方拿到非空即报错退出——
    这正是旧版"表名全错还能一路绿灯跑完"的根因。
    """
    problems: list[str] = []
    cols_sql = text(
        "SELECT TABLE_NAME, COLUMN_NAME FROM information_schema.COLUMNS "
        "WHERE TABLE_SCHEMA = DATABASE()"
    )
    with engine.connect() as conn:
        rows = conn.execute(cols_sql).fetchall()
    known: dict[str, set[str]] = {}
    for table, column in rows:
        known.setdefault(table, set()).add(column)

    for table in PURGE_POLICY:
        if table not in known:
            problems.append(f"策略表 {table} 在库中不存在")
        elif not {"is_deleted", "create_time"} <= known[table]:
            problems.append(f"策略表 {table} 缺 is_deleted/create_time 列")
    for parent, refs in REFERENCE_CHECK.items():
        if parent not in PURGE_POLICY:
            problems.append(f"引用清单里的父表 {parent} 不在 PURGE_POLICY")
        for child_table, child_col, _pcol in refs:
            if child_table not in known:
                problems.append(f"{parent} 的子表 {child_table} 在库中不存在")
            elif child_col not in known[child_table]:
                problems.append(f"{child_table}.{child_col} 在库中不存在")
    return problems


def backup_deleted_rows(engine, table: str, cutoff, backup_dir: Path) -> Path | None:
    """删除前导出待删行到 CSV，作为兜底备份。"""
    export_sql = text(f"SELECT * FROM `{table}` WHERE is_deleted=1 AND create_time < :cutoff")
    with engine.connect() as conn:
        result = conn.execute(export_sql, {"cutoff": cutoff})
        rows = result.fetchall()
        if not rows:
            return None
        columns = list(result.keys())

    csv_path = backup_dir / f"{table}_purge_{datetime.now().strftime('%Y%m%d_%H%M%S')}.csv"
    with open(csv_path, "w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerow(columns)
        writer.writerows(rows)
    return csv_path


def main():
    parser = argparse.ArgumentParser(description="软删除数据物理清理")
    parser.add_argument(
        "--dry-run",
        action="store_true",
        default=True,
        help="仅输出不执行（默认）",
    )
    parser.add_argument(
        "--no-dry-run",
        dest="dry_run",
        action="store_false",
        help="实际执行清理",
    )
    parser.add_argument("--table", help="只清理指定表")
    args = parser.parse_args()

    settings = get_settings()
    engine = create_engine(settings.database_url)

    problems = validate_policy(engine)
    if problems:
        print("❌ 清理策略与真实 schema 不符，已中止（请先修 PURGE_POLICY / REFERENCE_CHECK）：")
        for p in problems:
            print(f"   - {p}")
        return 1

    cutoff_date = datetime.now()
    total_purged = 0

    print(f"模式: {'DRY-RUN（仅输出）' if args.dry_run else '实际清理'}")
    print(f"清理截止日期: {cutoff_date.strftime('%Y-%m-%d %H:%M:%S')}")
    print("-" * 80)

    tables = args.table and [args.table] or list(PURGE_POLICY.keys())

    # 实际清理时创建备份目录
    backup_dir = None
    if not args.dry_run:
        backup_dir = PROJECT_DIR / "backups" / f"purge_{datetime.now().strftime('%Y%m%d_%H%M%S')}"
        backup_dir.mkdir(parents=True, exist_ok=True)
        print(f"📦 删除前备份目录: {backup_dir}")
        print("-" * 80)

    for table in tables:
        if table not in PURGE_POLICY:
            print(f"⚠️  跳过 {table}: 无清理策略")
            continue

        policy = PURGE_POLICY[table]
        cutoff = cutoff_date - timedelta(days=policy["retention_days"])

        with engine.connect() as conn:
            # 统计待清理记录数
            count_sql = text(
                f"SELECT COUNT(*) FROM `{table}` WHERE is_deleted=1 AND create_time < :cutoff"
            )
            count = conn.execute(count_sql, {"cutoff": cutoff}).scalar()

            if count == 0:
                print(f"✅ {table:25s} | 0 条待清理 | 保留期 {policy['retention_days']} 天")
                continue

            print(
                f"{'🔍' if args.dry_run else '🗑️'} {table:25s} | "
                f"{count:6d} 条待清理 | "
                f"保留期 {policy['retention_days']:4d} 天 | "
                f"{policy['reason']}"
            )

            if not args.dry_run:
                # 删除前备份待删行到 CSV
                csv_path = backup_deleted_rows(engine, table, cutoff, backup_dir)
                if csv_path:
                    print(f"   → 备份到: {csv_path.name}")

                # 父表删除前校验下游引用——有待删父行的活跃引用则跳过（防孤儿行）
                ref_skipped = 0
                refs = REFERENCE_CHECK.get(table, [])
                if refs:
                    parent_ids_sql = text(
                        f"SELECT id FROM `{table}` WHERE is_deleted=1 AND create_time < :cutoff"
                    )
                    parent_ids = [r[0] for r in conn.execute(parent_ids_sql, {"cutoff": cutoff})]
                    if parent_ids:
                        for child_table, child_col, _pcol in refs:
                            check_sql = text(
                                f"SELECT COUNT(*) FROM `{child_table}` "
                                f"WHERE `{child_col}` IN :ids AND is_deleted=0"
                            )
                            ref_count = conn.execute(check_sql, {"ids": tuple(parent_ids)}).scalar()
                            ref_skipped += ref_count or 0
                    if ref_skipped > 0:
                        print(
                            f"   ⚠️  跳过 {table}: {ref_skipped} 条活跃下游引用（防孤儿行，需先清理子表）"
                        )
                        continue

                # 实际删除
                delete_sql = text(
                    f"DELETE FROM `{table}` WHERE is_deleted=1 AND create_time < :cutoff"
                )
                result = conn.execute(delete_sql, {"cutoff": cutoff})
                conn.commit()
                total_purged += result.rowcount
                print(f"   → 已物理删除 {result.rowcount} 条")

    print("-" * 80)
    if args.dry_run:
        print("这是 DRY-RUN。实际清理请使用: --no-dry-run")
    else:
        print(f"清理完成。共物理删除 {total_purged} 条记录。")
        print(f"删除前备份位于: {backup_dir}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
