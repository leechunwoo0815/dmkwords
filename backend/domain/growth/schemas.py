"""growth 域 schemas（FE-2：测验提交响应契约，2026-10-28）。"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict


class QuizSubmitResponse(BaseModel):
    """小挑战提交响应。

    `extra="allow"`：通过/升级/里程碑等分支会给 dict 追加成长字段（`from_level`/`new_level`
    等），逐字段穷举会把它们过滤掉——声明已知字段 + 放行扩展，既让 OpenAPI 有真实形状，
    又不改变运行时载荷。
    """

    model_config = ConfigDict(extra="allow")

    score: int
    total: int
    passed: bool
    best_score: int
    best_percent: int
    attempts_left: int
    attempts_used: int
    points_added: int
    words_added: int
    wrong: list[Any]
    points_detail: list[Any]
    just_passed: bool
    level_up: bool
    new_milestones: list[Any]
