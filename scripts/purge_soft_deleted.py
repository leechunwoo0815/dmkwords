#!/usr/bin/env python3
"""
DmkWords 软删除数据物理清理脚本

用法: python scripts/purge_soft_deleted.py [--dry-run] [--table child]
依赖: 项目 .env 配置
约束:
  - 默认 --dry-run，仅输出将清理的记录数
  - 生产环境需手动指定 --no-dry-run
  - 实际清理前自动备份待删数据到 backups/purge_YYYYMMDD/
"""

import argparse
import csv
import sys
from datetime import datetime, timedelta
from pathlib import Path

# 项目根目录加入 sys.path
PROJECT_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_DIR))

from sqlalchemy import create_engine, text  # noqa: E402

from backend.config import get_settings  # noqa: E402

# 清理策略表
PURGE_POLICY = {
    "child": {"retention_days": 1095, "reason": "儿童隐私合规"},
    "user": {"retention_days": 1095, "reason": "账号注销后保留期"},
    "order": {"retention_days": 1825, "reason": "交易记录法定保留5年"},
    "deposit_record": {"retention_days": 1095, "reason": "押金记录同步订单"},
    "borrow_record": {"retention_days": 730, "reason": "借阅记录保留2年"},
    "reading_session": {"retention_days": 365, "reason": "阅读会话数据量大"},
    "voice_recording": {"retention_days": 180, "reason": "语音数据涉及儿童隐私"},
    "book_damage_report": {"retention_days": 730, "reason": "损坏记录审计保留"},
    "system_message": {"retention_days": 180, "reason": "消息数据量大"},
    "activity": {"retention_days": 365, "reason": "活动数据保留1年"},
    "reservation": {"retention_days": 180, "reason": "预约数据量大"},
}

# voice_recording 物理删除时需同步清理的文件目录
VOICE_UPLOAD_DIR = PROJECT_DIR / "uploads" / "voice"

# L3-027：父表物理删除前校验下游引用（防孤儿外键）——(子表, 子表引用列, 父表主键列)
# 清单 = information_schema 全量 FK（child 26 表 / user 5 表 / activity 1 / order 1 / reservation 1），
#       仅含 is_deleted 列的表（REFERENCE_CHECK 依赖 is_deleted=0 过滤活跃引用）。
REFERENCE_CHECK = {
    "child": [
        ("activity_enrollment", "child_id", "id"),
        ("ar_evaluation", "child_id", "id"),
        ("assessment", "child_id", "id"),
        ("book_damage_report", "child_id", "id"),
        ("book_waitlist", "child_id", "id"),
        ("bookshelf", "child_id", "id"),
        ("borrow_record", "child_id", "id"),
        ("check_in", "child_id", "id"),
        ("child_achievement", "child_id", "id"),
        ("child_level", "child_id", "id"),
        ("deposit_record", "child_id", "id"),
        ("fine_payment", "child_id", "id"),
        ("guidance_record", "child_id", "id"),
        ("learning_report", "child_id", "id"),
        ("level_certificate", "child_id", "id"),
        ("observation_evaluation", "child_id", "id"),
        ("observation_report", "child_id", "id"),
        ("order", "child_id", "id"),
        ("quiz", "child_id", "id"),
        ("reading_progress", "child_id", "id"),
        ("reading_session", "child_id", "id"),
        ("reading_submission", "child_id", "id"),
        ("refund_application", "child_id", "id"),
        ("reservation", "child_id", "id"),
        ("user_vocabulary", "child_id", "id"),
        ("voice_recording", "child_id", "id"),
    ],
    "user": [
        ("child", "user_id", "id"),
        ("message_read_status", "user_id", "id"),
        ("order", "user_id", "id"),
        ("refund_application", "user_id", "id"),
        ("system_message", "user_id", "id"),
    ],
    "activity": [("activity_enrollment", "activity_id", "id")],
    "order": [("refund_application", "order_id", "id")],
    # reservation 无下游 FK 引用（reservation.borrow_record_id 是反向引用，不构成孤儿），清单为空
}


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


def collect_voice_files(engine, cutoff) -> list[str]:
    """DELETE 前查出待删 voice_recording 行的 audio_url 列表。

    必须在 DELETE+commit 之前调用，否则行已不存在。
    """
    sql = text(
        "SELECT id, audio_url FROM voice_recording WHERE is_deleted=1 AND create_time < :cutoff"
    )
    with engine.connect() as conn:
        rows = conn.execute(sql, {"cutoff": cutoff}).fetchall()
    return [row[1] for row in rows if row[1]]


def delete_voice_files(audio_urls: list[str]) -> int:
    """按 audio_url 列表删除本地音频文件。

    唯一实现已下沉 backend.common.file_utils（终审 P2 分层修正），
    此处保留委托包装以兼容既有调用与测试（monkeypatch 本模块 PROJECT_DIR 生效）。
    """
    from backend.common.file_utils import delete_voice_files as _impl

    return _impl(audio_urls, base_dir=PROJECT_DIR)


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
    engine = create_engine(settings.DATABASE_URL)

    cutoff_date = datetime.now()
    total_purged = 0
    total_files_deleted = 0

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
                # 修正1: 删除前备份待删行到 CSV
                csv_path = backup_deleted_rows(engine, table, cutoff, backup_dir)
                if csv_path:
                    print(f"   → 备份到: {csv_path.name}")

                # 修正3: voice_recording 删除前先收集 audio_url
                # 必须在 DELETE 之前查，否则行已不存在
                voice_urls = []
                if table == "voice_recording":
                    voice_urls = collect_voice_files(engine, cutoff)

                # L3-027：父表删除前校验下游引用——有待删父行的活跃引用则跳过（防孤儿外键）
                ref_skipped = 0
                refs = REFERENCE_CHECK.get(table, [])
                if refs:
                    parent_ids_sql = text(
                        f"SELECT id FROM `{table}` WHERE is_deleted=1 AND create_time < :cutoff"
                    )
                    parent_ids = [r[0] for r in conn.execute(parent_ids_sql, {"cutoff": cutoff})]
                    if parent_ids:
                        for child_table, child_col, _pcol in refs:
                            try:
                                check_sql = text(
                                    f"SELECT COUNT(*) FROM `{child_table}` "
                                    f"WHERE `{child_col}` IN :ids AND is_deleted=0"
                                )
                                ref_count = conn.execute(
                                    check_sql, {"ids": tuple(parent_ids)}
                                ).scalar()
                                ref_skipped += ref_count or 0
                            except Exception:
                                pass  # 子表不存在/无该列则跳过检查
                    if ref_skipped > 0:
                        print(
                            f"   ⚠️  跳过 {table}: {ref_skipped} 条活跃下游引用（防孤儿外键，需先清理子表）"
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

                # DELETE+commit 之后按之前收集的列表删文件
                if table == "voice_recording" and voice_urls:
                    files_deleted = delete_voice_files(voice_urls)
                    total_files_deleted += files_deleted
                    if files_deleted > 0:
                        print(f"   → 同步删除音频文件 {files_deleted} 个")

    print("-" * 80)
    if args.dry_run:
        print("这是 DRY-RUN。实际清理请使用: --no-dry-run")
    else:
        print(f"清理完成。共物理删除 {total_purged} 条记录，{total_files_deleted} 个文件。")
        print(f"删除前备份位于: {backup_dir}")


if __name__ == "__main__":
    main()
