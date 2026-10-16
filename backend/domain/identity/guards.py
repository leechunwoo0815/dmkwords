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
AUDIO = "audio"  # R2（插修 15/C-13）：音频流会员门禁——403 语义（ForbiddenError）


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


# R3：AUDIO 拒因简码（audio-permission 端点返回给前端做引导文案分流）
AUDIO_REASON_TEXT = {
    "unpaid": "需入会后才能收听音频（请到店咨询）",
    "withdrawn": "已退会，无法收听音频",
    "expired": "会员已过期，只能收听手中在借的图书音频",
    # G15（2026-10-15 第六十一批）：PRD §4.4「下架后：在线音频 停止播放（已经听了一半的，进度保留）」
    "off_shelf": "该书已下架，暂不能播放（已听进度保留）",
}


def check_audio(db: Session, child: Child, book_id: int | None) -> str:
    """R3：音频收听资格判定（R-313 AUDIO 行）——返回简码 ok/unpaid/withdrawn/expired/off_shelf。

    require_member_action(AUDIO) 与 audio-permission 预检端点同源消费（禁漂移）。

    **G15（2026-10-15 第六十一批）**：补 PRD §4.4「下架后在线音频停止播放」。此前本函数只判会员四态，
    书下架了照放（预检 `allowed=true`、音频流 200，只有进度上报 404）——2026-10-15 解封 catalog 场景时
    实测到（`docs/09` 第六十一轮）。判定放在**会员判定之前**：下架对谁都不开门，包括"在手在借可听"的
    过期会员。进度行**不动**（§4.4「已经听了一半的，进度保留」），恢复上架即接着听。
    """
    if book_id is not None:
        # 域间依赖用函数内 import（identity 不引入 catalog 的模块级依赖，避免导入环）
        from backend.domain.catalog.models import Book

        row = db.query(Book.status).filter(Book.id == book_id, Book.is_deleted == 0).first()
        if row is None or row.status != Book.STATUS_ON:
            return "off_shelf"
    state = _member_state(child)
    if state == "active":
        return "ok"
    if state == "unpaid":
        return "unpaid"
    if state == "withdrawn":
        return "withdrawn"
    # expired：仅手头在借书允
    if not _holding_book(db, child.id, book_id):
        return "expired"
    return "ok"


def require_member_action(
    db: Session, child: Child, action: str, book_id: int | None = None
) -> None:
    """按 R-313 矩阵校验；不通过抛 ValidationError（422）。通过返回 None。"""
    state = _member_state(child)
    # 在册：全部放行 —— **AUDIO 例外**（G15，2026-10-15 第六十一批）：下架即停播（PRD §4.4）
    # 与会员态无关，"在册"这条早退还曾把新闸整个绕过（预检走 check_audio 判为 off_shelf、
    # 音频流却在早退处放行 → 同一本书两个端点结论相反）。放到下面 AUDIO 分支里统一判。
    if state == "active" and action != AUDIO:
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

    if action == AUDIO:
        # R2/C-13：未缴费禁（直链可绕的洞）；过期仅手头在借书允（复用 _holding_book）；
        # 退会禁。403 语义（ForbiddenError）——innerAudioContext 直链失败即 onError。
        # R3：判定抽 check_audio 同源（audio-permission 预检端点消费，禁两端点漂移）。
        # P1 #12（2026-10-15 大审查）：403 带 `error_code=audio.<reason>`——此前**全仓没有任何
        # error_code 生产方**（小程序 request.js 早留了这条通道、恒为空），于是 403 只能弹通用
        # "无权限"，真实拒因（下架/过期/退会）被丢掉。给通道第一个生产方：调用方可按 code 分支
        # （如详情页停播并引导入会），`detail` 仍是给人看的原因整句（小程序已按它出文案）。
        reason = check_audio(db, child, book_id)
        if reason != "ok":
            from backend.common.exceptions import ForbiddenError

            raise ForbiddenError(AUDIO_REASON_TEXT[reason], error_code=f"audio.{reason}")
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
