# tests/unit/test_review11_activity_update_guards_20261025.py — 第十一轮 R11-08：活动编辑路径的输入/时间不变量
"""活动**编辑**路径原先只判旧值状态与名额，建单时的两条时间不变量与金额上界全都没有：

1. `ActivityUpdateRequest.fee` 只有 `ge=0`（create 有 `max_digits=10, decimal_places=2`）——
   列是 `Numeric(10,2)`，越界在严格模式 500、非严格模式静默截断（金额失真）。
2. 新 `start_at` 可以是过去时间 → 保存成功后 `update()` 首行的 `a.start_at <= now` 立刻把
   **整条编辑锁死**（运营改错了无法自救）；`enroll_deadline` 也可以晚于 `start_at`。

RED：去掉任一守卫（schema 的 max_digits / service 的两条时间判定）→ 对应用例红。
"""

from __future__ import annotations

from datetime import datetime, timedelta
from decimal import Decimal

import pytest

from backend.common.exceptions import ValidationError
from backend.domain.activity.admin_service import AdminActivityService
from backend.domain.activity.models import Activity
from backend.domain.activity.router import ActivityUpdateRequest
from backend.domain.admin.models import AdminUser


def test_update_request_rejects_overflow_fee():
    """金额上界必须在**入参层**拦（与 create 同口径），不能靠数据库报错。"""
    with pytest.raises(Exception):  # pydantic ValidationError（不 import 其私有类）
        ActivityUpdateRequest(fee=Decimal("99999999999999.99"))
    with pytest.raises(Exception):
        ActivityUpdateRequest(fee=Decimal("50.005"))  # 超过 2 位小数
    ActivityUpdateRequest(fee=Decimal("50.00"))  # 合法值


def _activity(db) -> Activity:
    a = Activity(
        title="R11 活动",
        activity_type="book_club",
        start_at=datetime.now() + timedelta(days=3),
        location="门店",
        max_quota=10,
        fee=Decimal("0.00"),
        status=Activity.STATUS_PUBLISHED,
    )
    db.add(a)
    db.commit()
    return a


def _admin(db) -> AdminUser:
    admin = db.query(AdminUser).filter(AdminUser.username == "admin").first()
    assert admin is not None
    return admin


def test_update_rejects_past_start_and_inverted_deadline(db):
    a = _activity(db)
    svc = AdminActivityService(db)
    admin = _admin(db)

    with pytest.raises(ValidationError) as ei:
        svc.update(admin, a.id, ActivityUpdateRequest(start_at=datetime.now() - timedelta(days=1)))
    assert "开始时间必须晚于当前时间" in str(ei.value)

    db.rollback()
    future = datetime.now() + timedelta(days=5)
    with pytest.raises(ValidationError) as ei2:
        svc.update(
            admin,
            a.id,
            ActivityUpdateRequest(start_at=future, enroll_deadline=future + timedelta(days=1)),
        )
    assert "报名截止时间不能晚于活动开始时间" in str(ei2.value)

    db.rollback()
    # 对照：合法编辑仍能通过（防"修过头"）
    ok = svc.update(admin, a.id, ActivityUpdateRequest(location="二号门店"))
    assert ok.location == "二号门店"
