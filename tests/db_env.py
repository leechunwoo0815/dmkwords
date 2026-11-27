# tests/db_env.py — 测试/BDD 的**专用数据库隔离**（B5-2，2026-11-27）
"""为什么必须隔离：pytest / behave 的每个用例、每个场景都 `TRUNCATE` 业务表——
原先它们跑在**开发库 `dmkwords`** 上（与 `dev.sh` 后端同库），后果有三：
  ① 跑测试必须先 `dev.sh stop`，否则后端持表锁 → TRUNCATE 挂起/超时，表现为**一片假红**
     （2026-11-27 实测：门禁尾部自动重启 dev 后紧接着再跑门禁，单测步骤冒出 20+ 个 F）；
  ② 一个手滑的 `DB_NAME=dmkwords`（甚至生产库）就会把真实数据清空；
  ③ 无法并行（两个 pytest worker 互相清表）。

分工：
  · 库名由调用方在 **import backend 之前**用 `os.environ.setdefault("DB_NAME", …)` 定好
    （`tests/unit/conftest.py` → `dmkwords_test[_gwN]`；`features/environment.py` → `dmkwords_bdd`）。
    写成 `os.environ.…` 语句是刻意的：ruff 的 E402 只对这类"必须早于 import 的变量设置"开白名单。
  · 本模块负责**白名单硬拦截 + 建库 + 迁移**（`ensure_database()`，每进程跑一次）。
"""

from __future__ import annotations

import os
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

#: 允许被清空的库名（**精确白名单**，不做模糊匹配）：
#: pytest → dmkwords_test[_gwN]；BDD → dmkwords_bdd；CI → dmkwords_ci[_gwN]
ALLOWED_DB = re.compile(r"^(dmkwords_test|dmkwords_bdd|dmkwords_ci)(_gw\d+)?$")

_ensured: set[str] = set()


def _server_url() -> str:
    """不带库名的连接串（用于 CREATE DATABASE）。"""
    from backend.config import get_settings

    s = get_settings()
    return f"mysql+pymysql://{s.DB_USER}:{s.DB_PASSWORD}@{s.DB_HOST}:{s.DB_PORT}/?charset=utf8mb4"


def ensure_database() -> str:
    """校验库名 → 建库（IF NOT EXISTS）→ `alembic upgrade head`（幂等）。返回库名。

    **先校验再动手**：库名不在白名单里就直接抛错——宁可测试跑不起来，
    也不许把开发库/生产库的表清空（本模块唯一的权限就是"能建空库、能迁空库"）。
    """
    from sqlalchemy import create_engine, text

    from backend.config import get_settings

    name = get_settings().DB_NAME
    if not ALLOWED_DB.match(name):
        raise RuntimeError(
            f"拒绝在数据库 {name!r} 上跑测试：pytest/behave 会 TRUNCATE 业务表。\n"
            "允许的库名只限 dmkwords_test / dmkwords_bdd / dmkwords_ci（xdist 可带 _gwN 后缀）。\n"
            "要换库请先把 DB_NAME 改成上述形态（或清空 DB_NAME 让它自动取默认测试库）。"
        )
    if name in _ensured:
        return name

    engine = create_engine(_server_url(), isolation_level="AUTOCOMMIT")
    try:
        with engine.connect() as conn:
            conn.execute(
                text(
                    f"CREATE DATABASE IF NOT EXISTS `{name}` "
                    "CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci"
                )
            )
    finally:
        engine.dispose()

    # 与 CI 同口径：表结构由**迁移**产出（不用 create_all）——否则"迁移漏列"在测试里永远发现不了
    proc = subprocess.run(
        [sys.executable, "-m", "alembic", "upgrade", "head"],
        cwd=str(ROOT),
        env={**os.environ},
        capture_output=True,
        text=True,
    )
    if proc.returncode != 0:
        raise RuntimeError(
            f"[db_env] 在 {name} 上跑 alembic upgrade head 失败：\n"
            f"stdout={proc.stdout[-2000:]}\nstderr={proc.stderr[-2000:]}"
        )
    _ensured.add(name)
    return name
