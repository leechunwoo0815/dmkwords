# tests/unit/test_reading_resume_20261103.py — 续播点取「已覆盖最远点」（2026-11-03）
"""甲方报障：Harry Potter 显示覆盖 81.5%，点进去却从 0:24 开始。

根因：覆盖（intervals 并集）与 last_position 是两套口径。历史拖动只推进了覆盖、
没推进 last_position 时，按 last_position 续播就会"显示听了 81.5%、实际从 13.5% 开始"。
本判据锁住 `_progress_view.resume_position = max(last_position, 已覆盖最远点)`。
"""

from types import SimpleNamespace

from backend.domain.reading.service import ReadingService


def _view(intervals: str, last_position: int, total: int = 178, coverage: int = 0) -> dict:
    svc = ReadingService.__new__(ReadingService)
    p = SimpleNamespace(
        coverage_seconds=coverage,
        total_seconds=total,
        finished=0,
        last_position=last_position,
        intervals=intervals,
        reading_minutes=0,
    )
    return svc._progress_view(p)


def test_resume_position_prefers_furthest_covered_point():
    """甲方实测数据：coverage 145/178=81.5%，last_position 24 → 续播 145。"""
    view = _view("[[0, 145]]", 24, coverage=145)
    assert view["coverage_percent"] == 81.5
    assert view["last_position"] == 24
    assert view["resume_position"] == 145


def test_resume_position_keeps_later_playhead():
    """播放头已经超过覆盖终点（例如刚 seek 到未上报的位置）→ 取播放头。"""
    view = _view("[[0, 100]]", 120, coverage=100)
    assert view["resume_position"] == 120


def test_resume_position_handles_gaps():
    """区间有空洞时取最远终点，不是覆盖秒数（135）。"""
    view = _view("[[0, 50], [60, 145]]", 0, coverage=135)
    assert view["resume_position"] == 145


def test_resume_position_falls_back_on_corrupt_intervals():
    """intervals 损坏 → 不 500，退回 last_position。"""
    view = _view("not-json", 24)
    assert view["resume_position"] == 24


def test_resume_position_caps_at_total():
    """脏区间终点超过总时长 → 钳到总时长，避免续播 seek 越界。"""
    view = _view("[[0, 200]]", 0, total=178, coverage=178)
    assert view["resume_position"] == 178
