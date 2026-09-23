#!/usr/bin/env python3
"""清理 uploads 里"无数据库关联"的孤儿媒体文件。

⚠️ 2026-09-15 重写（原版是**危险**的）：原版只认 `Book.cover_path/audio_path` 两列，
   拿它跑会把**活动封面、阅读圈卡片图、收款凭证、评估报告图、以及被文档引用的证据目录
   （miniapp-audit / *-samples）全部当垃圾删掉**。重写后：
   ①引用集覆盖全部存路径的列（含软删行）；②只清理**可再生**目录；
   ③证据/上传类目录写进保护名单，永不动；④默认只打印计划，`--trash/--apply` 才动手。

引用集来源（全部包含 is_deleted=1 的软删行——软删记录 purge 时才删文件，提前删会造成悬空）：
    books.cover_path / books.audio_path / activities.cover_path /
    orders.voucher_path / observation_reports.images(JSON) / circle_posts.image_path+thumb_path

只清理（可再生 = 种子或服务端按需重新生成）：
    cover/        书封面（seed 与 scripts/regen_covers.py 重生成）
    book_audio/   演示音频（seed_wm11_demo 按固定名写入）
    circle/       阅读圈卡片图与缩略图（card_engine 重生成）
    reports/      周报月报图（ReportService 按需生成）

永不清理（保护名单 + "不在白名单内一律不动"）：
    miniapp-audit-*/  *-samples/   视觉证据目录（保留在名单里以防将来再生成；**2026-09-17 用户裁定
                                  已整体删除 31MB**，见 LEDGER 同日行——重出用 gen_* 脚本，不再回仓）
    posters/     按 child_id 定名、就地覆盖，体积极小
    voucher/     收款凭证（**人工上传，不可再生**）
    observation/ 评估报告图（**人工上传，不可再生**）

用法：
    python scripts/cleanup_uploads.py                # 只打印计划（默认）
    python scripts/cleanup_uploads.py --trash DIR    # 移到 DIR（保留相对结构，可回滚）★推荐
    python scripts/cleanup_uploads.py --apply        # 直接删除（不可恢复）
"""

from __future__ import annotations

import argparse
import os
import shutil
from collections import defaultdict

from backend.common.media_paths import (
    PROTECTED_PREFIXES,  # noqa: F401 — 下面文档字符串与保护判定共用同一份名单
    PROTECTED_SUFFIX_DIRS,  # noqa: F401
    REGENERABLE_PREFIXES,  # noqa: F401
    TRASH_DIRNAME,
    is_protected,
)
from backend.database import SessionLocal

# 引用口径**单一来源**（2026-09-23，红线 51）：此前本脚本自己写了一份 `collect_referenced`，
# 管理端媒体体检又写一份 → 两份口径迟早分叉（R16a/R12 那次的误删就是这么来的，错误库 §一百零二）。
# 现在两边共用 `backend/domain/admin/media_service.collect_referenced`；
# 目录白名单/保护名单共用 `backend/common/media_paths`。
from backend.domain.admin.media_service import collect_referenced as _collect_referenced


def collect_referenced(root: str) -> tuple[set[str], list[str]]:
    """DB 里所有被引用的相对路径 + 引用存在但磁盘缺失的清单（诊断用）。"""
    db = SessionLocal()
    try:
        rels = _collect_referenced(db)
    finally:
        db.close()
    missing = [r for r in sorted(rels) if not os.path.isfile(os.path.join(root, r))]
    return rels, missing


def main() -> int:
    ap = argparse.ArgumentParser(description="清理 uploads 孤儿媒体文件")
    ap.add_argument("--apply", action="store_true", help="直接删除（不可恢复）")
    ap.add_argument("--trash", metavar="DIR", help="改为移动到 DIR（保留相对路径，可回滚）")
    args = ap.parse_args()

    root = os.path.abspath("uploads")
    if not os.path.isdir(root):
        print(f"uploads 目录不存在: {root}")
        return 1
    mode = "删除" if args.apply else (f"移到 {args.trash}" if args.trash else "仅打印计划")

    referenced, missing = collect_referenced(root)
    print(f"uploads 根: {root}")
    print(f"模式: {mode}")
    print(f"DB 引用文件: {len(referenced)} 个")
    if missing:
        print(f"  ⚠ 其中 {len(missing)} 个在磁盘上不存在（引用悬空）：")
        for m in missing[:10]:
            print(f"     {m}")
        if len(missing) > 10:
            print(f"     … 另有 {len(missing) - 10} 个")
    print()

    stat: dict[str, list[int]] = defaultdict(lambda: [0, 0])  # prefix -> [文件数, 字节]
    protected_hits = 0
    for dirpath, dirnames, filenames in os.walk(root):
        if dirpath == root:
            # 回收站（管理端媒体体检的 .trash/）单独计量：里面的东西是"已判定孤儿"的人质，
            # 不该再被本脚本当孤儿重复处理（白名单本来也拦得住，这里是显式声明）
            dirnames[:] = [d for d in dirnames if d != TRASH_DIRNAME]
        for fn in filenames:
            full = os.path.join(dirpath, fn)
            rel = os.path.relpath(full, root).replace(os.sep, "/")
            if rel in referenced:
                continue
            if is_protected(rel):
                protected_hits += 1
                continue
            if not any(rel.startswith(p) for p in REGENERABLE_PREFIXES):
                continue  # 不在白名单 → 不动（保守）
            try:
                size = os.path.getsize(full)
            except OSError:
                size = 0
            bucket = rel.split("/")[0] + "/"
            stat[bucket][0] += 1
            stat[bucket][1] += size
            if args.apply:
                os.remove(full)
            elif args.trash:
                dest = os.path.join(args.trash, rel)
                os.makedirs(os.path.dirname(dest), exist_ok=True)
                shutil.move(full, dest)

    print("按目录统计（孤儿）：")
    total_files = total_bytes = 0
    for bucket in sorted(stat):
        files, size = stat[bucket]
        total_files += files
        total_bytes += size
        print(f"  {bucket:<14} {files:>5} 个   {size / 1048576:>8.1f} MB")
    print(f"  {'合计':<12} {total_files:>5} 个   {total_bytes / 1048576:>8.1f} MB")
    print(f"保护名单跳过: {protected_hits} 个（证据/人工上传类，永不自动清理）")

    if args.apply or args.trash:
        removed_dirs = 0
        for dirpath, dirnames, filenames in os.walk(root, topdown=False):
            if dirnames or filenames or dirpath == root:
                continue
            rel = os.path.relpath(dirpath, root).replace(os.sep, "/") + "/"
            if any(rel.startswith(p) for p in REGENERABLE_PREFIXES):
                os.rmdir(dirpath)
                removed_dirs += 1
        print(f"清理空目录: {removed_dirs} 个")

    if not (args.apply or args.trash):
        print("\n（未做任何改动；加 --trash DIR 或 --apply 才执行）")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
