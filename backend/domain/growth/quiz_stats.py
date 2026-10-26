# backend/domain/growth/quiz_stats.py — 最佳正确率的唯一口径（R11-c2/G3，2026-10-26）
"""同一本书"最佳正确率"三处消费方（书架角标 / 管理端成长档案 / 已通过页）的统一判据。

修前各写一套：`quiz_batch` 把 `MAX(score)` 与 `MAX(total_questions)` **分开取**（分子分母
来自不同次尝试）、`quiz_overview` 同分取最近、`passed_books` 同分取任意首条 → 同一本书
三个百分率可以不同。

统一口径：**正确率最高的一次尝试**；同率取题量更大的一次（分母更稳）；再同取 id 更大（最近）。
"""

from __future__ import annotations

from collections.abc import Iterable


def accuracy(score, total) -> float:
    """单次尝试的正确率（题量为 0 → 0）。"""
    total = int(total or 0)
    if total <= 0:
        return 0.0
    return int(score or 0) / total


def best_attempt_stats(rows: Iterable[tuple]) -> tuple[int, int] | None:
    """rows = 可迭代的 `(score, total_questions, attempt_id)`；返回该书的
    `(best_score, that_total)`（分子分母来自**同一次**尝试），无记录返回 None。"""
    best_key = None
    best = None
    for score, total, attempt_id in rows:
        key = (accuracy(score, total), int(total or 0), int(attempt_id or 0))
        if best_key is None or key > best_key:
            best_key = key
            best = (int(score or 0), int(total or 0))
    return best
