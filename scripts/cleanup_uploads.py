#!/usr/bin/env python3
"""清理 uploads 中无数据库关联的孤儿媒体文件与空目录。

用法：
    python scripts/cleanup_uploads.py          # 直接删除
    python scripts/cleanup_uploads.py --dry-run # 只打印不删除
"""

from __future__ import annotations

import argparse
import os

from backend.config import get_settings
from backend.database import get_session
from backend.domain.catalog.models import Book


def main() -> None:
    parser = argparse.ArgumentParser(description="清理 uploads 孤儿文件")
    parser.add_argument("--dry-run", action="store_true", help="只打印，不删除")
    args = parser.parse_args()

    root = os.path.abspath(get_settings().UPLOADS_DIR)
    if not os.path.isdir(root):
        print(f"UPLOADS_DIR 不存在: {root}")
        return

    with get_session() as db:
        paths = set()
        for row in db.query(Book.cover_path, Book.audio_path).all():
            if row.cover_path:
                paths.add(os.path.abspath(os.path.join(root, row.cover_path)))
            if row.audio_path:
                paths.add(os.path.abspath(os.path.join(root, row.audio_path)))

    orphans: list[str] = []
    for dirpath, _dirnames, filenames in os.walk(root, topdown=False):
        for filename in filenames:
            full = os.path.abspath(os.path.join(dirpath, filename))
            if full not in paths:
                orphans.append(full)

    removed_files: list[str] = []
    removed_dirs: list[str] = []
    total_size = 0

    for p in orphans:
        rel = os.path.relpath(p, root)
        if args.dry_run:
            print(f"[dry-run] 将删除: {rel}")
            continue
        try:
            size = os.path.getsize(p)
            os.remove(p)
            removed_files.append(p)
            total_size += size
        except OSError as e:
            print(f"删除失败 {rel}: {e}")

    # 清理空目录
    for dirpath, _dirnames, _filenames in os.walk(root, topdown=False):
        if dirpath != root and not os.listdir(dirpath):
            rel = os.path.relpath(dirpath, root)
            if args.dry_run:
                print(f"[dry-run] 将删空目录: {rel}")
                continue
            try:
                os.rmdir(dirpath)
                removed_dirs.append(dirpath)
            except OSError as e:
                print(f"删目录失败 {rel}: {e}")

    if args.dry_run:
        print(f"\n[dry-run] 待删除文件: {len(orphans)} 个")
        return

    print(f"已删除文件: {len(removed_files)} 个")
    print(f"已删除空目录: {len(removed_dirs)} 个")
    print(f"释放空间: {total_size / 1024 / 1024:.2f} MB")


if __name__ == "__main__":
    main()
