# backend/domain/identity/guards.py — R-313 小程序权限矩阵统一守卫
"""用法：miniapp 各路由拿到 child 后调用 require_member_action(db, child, ACTION)。
矩阵（R-313，未缴费=member_status none；过期=is_expired_member；退会=withdrawn）：

| 动作            | 未缴费 | 在册 | 过期           | 退会       |
|-----------------|--------|------|----------------|------------|
| lookup 查词     | 禁     | 允   | 仅音频场景(在借书) | 禁     |
| vocab_view 看生词本 | 禁  | 允   | 允（只读）     | 允（只读） |
| vocab_write     | 禁     | 允   | 禁（只读）     | 禁（只读） |
| favorite_write  | 允     | 允   | 允             | 禁（只读） |
| quiz            | 禁     | 允   | 已解锁可完成（允） | 禁    |
| passport_view   | 禁     | 允   | 允（只读）     | 允（只读） |
| points_view     | 禁     | 允   | 允（只读）     | 允（只读） |
| report_view     | 禁     | 允   | 允（只读）     | 允（只读） |
| deposit_supplement | —  | 允   | 允             | 禁        |

只读语义 = 查看类动作放行、写入类动作拒绝（由 view/write 两个动作分别表达）。
"""

from __future__ import annotations

from sqlalchemy import func
from sqlalchemy.orm import Session

from backend.common.exceptions import ValidationError
from backend.domain.identity.models import Child

# 动作常量
LOOKUP = "lookup"
VOCAB_VIEW = "vocab_view"
VOCAB_WRITE = "vocab_write"
FAVORITE_WRITE = "favorite_write"
QUIZ = "quiz"
PASSPORT_VIEW = "passport_view"
POINTS_VIEW = "points_view"
REPORT_VIEW = "report_view"
DEPOSIT_SUPPLEMENT = "deposit_supplement"


def _member_state(child: Child) -> str:
    """归并为 R-313 四态：unpaid / active / expired / withdrawn。"""
    if child.member_status == Child.MEMBER_WITHDRAWN:
        return "withdrawn"
    if child.is_active_member:
        return "active"
    if child.is_expired_member:
        return "expired"
    return "unpaid"


def _holding_book(db: Session, child_id: int, book_id: int | None) -> bool:
    """孩子是否在借某书（过期查词"仅音频场景内"判定）。"""
    if book_id is None:
        return False
    from backend.domain.circulation.models import BorrowRecord

    return bool(
        db.query(func.count(BorrowRecord.id))
        .filter(
            BorrowRecord.child_id == child_id,
            BorrowRecord.book_id == book_id,
            BorrowRecord.status.in_([BorrowRecord.STATUS_ACTIVE, BorrowRecord.STATUS_OVERDUE]),
            BorrowRecord.is_deleted == 0,
        )
        .scalar()
    )


def require_member_action(
    db: Session, child: Child, action: str, book_id: int | None = None
) -> None:
    """按 R-313 矩阵校验；不通过抛 ValidationError（422）。通过返回 None。"""
    state = _member_state(child)
    # 在册：全部放行
    if state == "active":
        return

    if action == FAVORITE_WRITE:
        # R-314：未缴费可收藏、在册/过期可收藏、退会只读
        if state == "withdrawn":
            raise ValidationError("已退会，收藏夹只读")
        return

    if action in (VOCAB_VIEW, PASSPORT_VIEW, POINTS_VIEW, REPORT_VIEW):
        # 未缴费禁；过期/退会只读（查看放行）
        if state == "unpaid":
            raise ValidationError("入会后可查看（请到店咨询）")
        return

    if action == LOOKUP:
        if state == "unpaid":
            raise ValidationError("入会后可查词（请到店咨询）")
        if state == "withdrawn":
            raise ValidationError("已退会，无法查词")
        # 过期：仅音频场景内（书在借）
        if not _holding_book(db, child.id, book_id):
            raise ValidationError("会员已过期，仅可在播放已借图书时查词")
        return

    if action in (VOCAB_WRITE, QUIZ, DEPOSIT_SUPPLEMENT):
        if state == "unpaid":
            raise ValidationError("入会员后可用（请到店咨询）")
        if state == "withdrawn":
            raise ValidationError("已退会，该功能不可用")
        if action == VOCAB_WRITE:
            raise ValidationError("会员已过期，生词本只读")
        # QUIZ：过期=已解锁可完成 → 放行（解锁校验在 QuizService 内）
        # DEPOSIT_SUPPLEMENT：过期可用 → 放行
        return

    raise ValidationError(f"未知权限动作: {action}")
