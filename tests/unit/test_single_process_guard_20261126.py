# tests/unit/test_single_process_guard_20261126.py — B2-7 单进程守卫（2026-11-26 双审查 + 复审补强）
"""复审实锤（2026-11-26）：守卫只认 `--workers 2` 与 `WEB_CONCURRENCY`，**`--workers=2` 一行就绕过**，
而且当时零测试。这里把各种拼法都钉住——限流/指标/live 缓存/调度器锁都是进程内状态，
多起一个 worker 就是静默失真（限流阈值翻倍、看板腰斩、采样重复写）。

覆盖五种写法：`--workers 2`、`--workers=2`、`-w 2`、`-w2`、`WEB_CONCURRENCY=2`；
以及不该误报的：单 worker 各写法、无参数。
"""

from __future__ import annotations

import pytest

from backend.main import _multi_process_sources


@pytest.mark.parametrize(
    "argv",
    [
        ["uvicorn", "backend.main:app", "--workers", "2"],
        ["uvicorn", "backend.main:app", "--workers=2"],
        ["uvicorn", "backend.main:app", "-w", "4"],
        ["uvicorn", "backend.main:app", "-w4"],
        ["gunicorn", "-k", "uvicorn.workers.UvicornWorker", "-w", "3", "backend.main:app"],
    ],
)
def test_worker_flags_are_all_detected(argv: list[str]):
    offenders = _multi_process_sources(argv, {})
    assert offenders, f"多 worker 写法没被拦住：{argv}"


@pytest.mark.parametrize(
    "argv",
    [
        ["uvicorn", "backend.main:app"],
        ["uvicorn", "backend.main:app", "--workers", "1"],
        ["uvicorn", "backend.main:app", "--workers=1"],
        ["uvicorn", "backend.main:app", "-w1"],
    ],
)
def test_single_worker_is_not_flagged(argv: list[str]):
    assert _multi_process_sources(argv, {}) == [], f"单进程被误报：{argv}"


def test_env_web_concurrency_detected():
    assert _multi_process_sources(["uvicorn"], {"WEB_CONCURRENCY": "2"}) == ["WEB_CONCURRENCY=2"]
    assert _multi_process_sources(["uvicorn"], {"WEB_CONCURRENCY": "1"}) == []
    assert _multi_process_sources(["uvicorn"], {"WEB_CONCURRENCY": ""}) == []


def test_offenders_are_reported_verbatim():
    """报错信息要带上原始拼法，否则运维看着 "多 worker" 却不知道自己哪一行踩的。"""
    assert _multi_process_sources(["x", "--workers=8"], {}) == ["--workers=8"]
    assert _multi_process_sources(["x", "-w", "8"], {}) == ["-w 8"]


def test_startup_actually_refuses(monkeypatch):
    """端到端：真去 import 一次不该崩（当前单进程），但把 argv 改成多 worker 必须 RuntimeError。"""
    import importlib
    import sys

    monkeypatch.setattr(sys, "argv", ["uvicorn", "backend.main:app", "--workers=2"])
    monkeypatch.delitem(sys.modules, "backend.main", raising=False)
    with pytest.raises(RuntimeError, match="只允许单进程"):
        importlib.import_module("backend.main")
    # 复原：删掉半初始化的模块，后续用例重新导入
    monkeypatch.delitem(sys.modules, "backend.main", raising=False)
