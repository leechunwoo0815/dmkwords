# backend/tasks/lock_names.py — 命名锁**锁名**的单一事实源（B5-2，2026-11-27）
"""两把 MySQL 命名锁的**名字**集中在这里，两个理由：

1. **契约名不许散落**：`dmkwords:scheduler`（领导权）与 `dmkwords:task:<name>`（单次执行互斥）
   是多进程/多副本互斥的唯一依据，名字散在各文件里就会漂移（改名 = 拆护栏）。
2. **测试后缀**：MySQL 命名锁是**服务器级**的，**不随库变**。pytest 用 xdist 并行时多个 worker
   共用一个 MySQL 服务器，会互相抢锁（实测症状：任务手动触发被别的持锁者 409、接管用例
   前置断言拿不到锁）。所以锁名支持一个可选后缀 `DMK_LOCK_SUFFIX`——**只在测试里设**
   （`tests/unit/conftest.py` 设成 `-gwN`），生产不设 → 锁名与文档逐字一致。

**绝不把数据库名拼进锁名**：那样滚动发布期新旧进程会各持一把锁 → 定时任务双跑
（正是这套锁要防的事）。隔离测试环境必须靠"后缀"，不能靠"库名"。
"""

from __future__ import annotations

import os

#: 锁名后缀（**只给测试用**；生产为空 → 锁名等于契约名）
LOCK_SUFFIX = os.environ.get("DMK_LOCK_SUFFIX", "")


def with_suffix(base: str) -> str:
    """给锁名加测试后缀。

    `base` 由**调用方**传入（`registry.SCHEDULER_LOCK_NAME` / `task_guard.TASK_LOCK_PREFIX`），
    而不是在这里另存一份——实测教训：契约名搬到本模块后，`monkeypatch.setattr(registry,
    "SCHEDULER_LOCK_NAME", …)` 就失效了（`test_prod_skeleton.lock_name` 三处机制用例齐红），
    因为被测代码读的是本模块的副本。契约名只有一处定义、且必须在能被打桩的那个模块里。
    """
    return base + LOCK_SUFFIX
