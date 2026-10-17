# backend/domain/reading/progress_utils.py — 阅读进度区间工具（从 service.py 抽出）
"""为什么单开：`reading/service.py` 触架构关 god-file 上限（>800 行，第六十九批）。

这里放的是**纯函数**：历史 intervals 清洗、区间并集合并、覆盖秒数、进度行原子插入。
口径不变（脏数据永不 500），调用方通过 `reading.service` 的 re-export 继续可用。
"""

from __future__ import annotations

from sqlalchemy.orm import Session

from backend.domain.reading.models import ReadingProgress


def _clean_intervals(raw: object) -> list[list[int]]:
    """清洗历史 intervals：丢弃非法区间（非 [a,b]、含非数字、a>=b、负起点），
    保证 merge/coverage 对脏数据永不 500（E-20260830-12 防御）。"""
    if not isinstance(raw, list):
        return []
    cleaned: list[list[int]] = []
    for item in raw:
        if (
            isinstance(item, (list, tuple))
            and len(item) >= 2
            and isinstance(item[0], (int, float))
            and isinstance(item[1], (int, float))
        ):
            start, end = int(item[0]), int(item[1])
            if 0 <= start < end:
                cleaned.append([start, end])
    return cleaned


def merge_intervals(intervals: object, new: list[int]) -> list[list[int]]:
    """合并区间并集（排序 + 线性合并）。intervals 为脏数据时清洗后合并，不抛异常。"""
    all_intervals = sorted(_clean_intervals(intervals) + [list(new)], key=lambda x: x[0])
    merged: list[list[int]] = []
    for start, end in all_intervals:
        if merged and start <= merged[-1][1]:
            merged[-1][1] = max(merged[-1][1], end)
        else:
            merged.append([start, end])
    return merged


def coverage_of(intervals: list[list[int]]) -> int:
    return sum(e - s for s, e in intervals)


def ensure_progress_row(db: Session, *, child_id: int, book_id: int, total_seconds: int) -> None:
    """确保 (孩子, 书目) 有阅读进度行（PRD §5.2「借出时自动创建阅读记录」，第五十九批 G8）。

    由 circulation.borrow 调用（放在本域因为 `reading_progress` 的归属在这里）；
    原子插入 + `uq_reading_progress_child_book` 唯一索引兜底：并发借同书两册只留一行，
    既有行原样保留（空更新）。`total_seconds` 先按书目音频时长写，书后补音频时由
    `report_progress` 自愈刷新（见该函数内注释）。
    """
    from sqlalchemy.dialects.mysql import insert as mysql_insert

    db.execute(
        mysql_insert(ReadingProgress)
        .values(child_id=child_id, book_id=book_id, total_seconds=total_seconds, is_deleted=0)
        .on_duplicate_key_update(id=ReadingProgress.id)  # 空更新：保留既有行
    )
