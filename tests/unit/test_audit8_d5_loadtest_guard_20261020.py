# tests/unit/test_audit8_d5_loadtest_guard_20261020.py — 第八轮大审查 D 批（下）P2-43
"""压测脚本 `scripts/load_test.py` 的**护栏**：目标自检 / 全任务枚举 / 结构化判定。

背景（P2-43）：脚本原先"不能支撑『容量已验』"——G5 只跑 9/21 个任务、造量缺 orders/refunds/words、
无 PASS/FAIL 阈值、G4 只信接口自报、不可重跑、`--base-url` 不校验（指到 :8002 就把演示库
灌成 2000 书目 / 6000 借阅）。本用例钉住其中**可离线判定**的部分（真跑压测要一次性实例，
不在单测范围内）。

RED（变异自证）：
- `guard_target` 去掉端口黑名单 → 用例 1 红；
- `all_task_names` 退回手抄 9 个 → 用例 3 红；
- `collect_verdicts` 退回字符串计数 → 用例 4 红（嵌套 FAIL 数不到）。
"""

from __future__ import annotations

import pytest


def test_guard_refuses_demo_port(monkeypatch):
    """`--base-url` 指向 dev.sh 的端口（演示库）必须**直接拒绝**。"""
    from scripts.load_test import guard_target

    monkeypatch.setenv("DB_NAME", "dmkwords_loadtest")  # 库名这关先过，专测端口这关
    for base in ("http://127.0.0.1:8002", "http://127.0.0.1:8443", "http://localhost:5173"):
        with pytest.raises(SystemExit) as exc:
            guard_target(base)
        assert "演示" in str(exc.value), f"{base} 应被端口闸拦下：{exc.value}"


def test_guard_refuses_non_loadtest_db(monkeypatch):
    """库名没自报压测/演练库 → 拒绝（**默认就是演示库名**，不给"忘了带 DB_NAME"留后门）。"""
    from scripts.load_test import guard_target

    monkeypatch.delenv("DB_NAME", raising=False)
    with pytest.raises(SystemExit) as exc:
        guard_target("http://127.0.0.1:8003")
    assert "DB_NAME" in str(exc.value), str(exc.value)

    monkeypatch.setenv("DB_NAME", "dmkwords")
    with pytest.raises(SystemExit):
        guard_target("http://127.0.0.1:8003")


def test_guard_allows_one_off_instance(monkeypatch):
    """正确姿势（压测库 + 一次性端口）必须放行——否则这闸门会挡正常使用。"""
    from scripts.load_test import guard_target

    monkeypatch.setenv("DB_NAME", "dmkwords_loadtest")
    guard_target("http://127.0.0.1:8003")  # 不抛即通过
    monkeypatch.setenv("DB_NAME", "dmkwords_drill")
    guard_target("http://127.0.0.1:8004")


def test_g5_covers_every_registered_task():
    """G5 必须跑**注册表里的全部任务**（原先手抄 9 个，漏 12 个——含随流水增长的聚合类）。"""
    from backend.tasks.registry import TASKS
    from scripts.load_test import all_task_names

    names = all_task_names()
    assert set(names) == set(TASKS), (
        f"压测任务集与注册表不一致（漏={sorted(set(TASKS) - set(names))}，"
        f"多={sorted(set(names) - set(TASKS))}）——RED=手抄任务清单，新增任务压不到"
    )
    assert len(names) == len(TASKS) >= 20, f"任务数异常：{len(names)}"


def test_verdicts_are_collected_structurally():
    """判定必须**结构化收集**（嵌套也要数到），不是对 JSON 文本做字符串 grep。"""
    from scripts.load_test import collect_verdicts

    report = {
        "G1": {"判定": "PASS"},
        "G5": {"任务": {"a": {"判定": "PASS"}, "b": {"判定": "FAIL"}}, "判定": "FAIL"},
        "其他": {"嵌套": [{"判定": "FAIL"}]},
    }
    verdicts = dict(collect_verdicts(report))
    fails = [p for p, v in verdicts.items() if v == "FAIL"]
    # 5 处判定：G1 / G5 / G5·任务a / G5·任务b / 其他·嵌套[0]；其中 FAIL 三处
    assert len(verdicts) == 5, verdicts
    assert len(fails) == 3, f"嵌套 FAIL 必须数到：{verdicts}"
    assert any("G5/任务/b" in p for p in fails), fails


def test_seed_volume_refuses_demo_db(monkeypatch):
    """造量前同样必须过库名闸（它是直连库写 2000+ 行的动作，比压测本身更危险）。"""
    from scripts import load_test

    monkeypatch.setenv("DB_NAME", "dmkwords")
    with pytest.raises(SystemExit) as exc:
        load_test._seed_volume(books=1, children=1, borrows=1, orders=1, refunds=1, words=1)
    assert "拒绝造量" in str(exc.value), str(exc.value)
