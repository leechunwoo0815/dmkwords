# backend/domain/reading/reservation_ops.py — 预约期间副本失效的处置漏斗（G5）
"""PRD §5.5：「**预约期间书出意外**（例如被标记维修）：系统自动尝试换同一种书的其他在馆副本；
没有其他副本了，预约转『异常』状态并通知家长和馆员，名额立即释放。」

**触发点枚举（2026-10-14，全仓 `copy.status = …` 赋值点逐条比对）**
——能把副本写成 `maintenance`/`lost` 的链只有三条：

| 链 | 位置 | 挂钩 |
|---|---|---|
| 管理端改副本状态 | `catalog/service.py::update_copy_status` | ✅ |
| 报损·遗失（2026-10-12 拍板 P5） | `catalog/service.py::report_copy_damage` | ✅ |
| **管理端「释放锁定」**（`reserved→available`，2026-10-15 大审查 P1 #8） | `catalog/service.py::update_copy_status` | ✅ |
| 还书带损 `condition=maintenance\\|lost` | `circulation/service.py::return_book` | ✗ **结构上不可达** |

不挂后者是**可达性论证**、不是遗漏：`return_book` 要求该副本有 `active`/`overdue` 的借阅记录
⇒ 副本必为 `borrowed`；而预约锁定要求副本 `available→reserved`（`ReservationService.create`），
两状态互斥，且 `circulation.borrow()` 遇 `reserved` 副本直接拒绝并提示"走预约核销"。
**若将来口径变成"预约可锁 borrowed 副本"或借出链改锁序，本段结论必须重判。**

**「释放锁定」为什么也走本漏斗**（P1 #8）：该跃迁只把副本改成可借、**不动预约单**，结果
预约单仍 `active`（占额度、家长端仍显示"预约中"），副本却已能被任何人借走——家长到店核销必 409。
与"副本退场"是同一个善后：有别的在馆副本就改锁，没有就转异常 + 通知 + 待办。

**锁序（宪法 §二 并发红线：全局 `Reservation → copy`）**：调用方必须在**写 `copy.status` 之前**
调用本函数——本函数先锁预约行、再锁替换副本，原副本由调用方在其后更新，全程无反向边。
若倒过来（先改副本再进本函数），锁序会变成 `copy → Reservation`，与核销/取消/超时释放的
`Reservation → copy` 相反；家长此刻核销 + 馆员此刻报损两笔并发即构成死锁环。

**为什么不写进 `reading/service.py`**：该文件已 791 行（架构关 god-file ≤800）；且本处置是
"副本侧事件驱动的预约善后"，与 `ReservationService` 的"家长自助动作"职责不同，独立成模块。
"""

from __future__ import annotations

from sqlalchemy import func
from sqlalchemy.orm import Session

from backend.common.admin_notification_models import AdminNotification
from backend.common.admin_notifications import AdminNotifyService
from backend.common.notification_models import Notification
from backend.common.notifications import SCENE_RESERVATION_EXCEPTION, NotificationService
from backend.domain.catalog.audit_events import publish_audit
from backend.domain.catalog.models import Book, BookCopy
from backend.domain.identity.models import Child, Parent
from backend.domain.reading.models import Reservation


def handle_copy_unavailable(
    db: Session,
    copy: BookCopy,
    *,
    reason: str,
    admin,
    cause: str = "原预约的副本已转为维护/遗失",
) -> dict:
    """副本退场（维护/遗失/**馆员释放锁定**）后的预约善后（PRD §5.5）。
    **由调用方在写 `copy.status` 之前调用。**

    分两路处置该副本上**唯一一条**活跃预约（同副本同时只能有一条——`create` 是在把副本锁成
    `reserved` 的前提下建单的）：

    - **有**同书其他在馆副本 → 预约**改锁**那册（`expires_at` 不变，预约继续有效）；
    - **没有** → 预约转 `Reservation.STATUS_EXCEPTION` + 通知家长 + 馆员待办；异常态不再是
      `active`，故额度按 §5.4 公式（`可借 = 上限 − 在借 − **预约中(active)**`）**自动释放**。

    `cause`：给家长/馆员看的**原因整句**（触发点不同，措辞不同；默认是维护/遗失口径）。

    返回 `{"switched": [预约id…], "excepted": [预约id…]}`（调用方可不理会；单测/审计取用）。
    """
    rows = (
        db.query(Reservation)
        .filter(
            Reservation.copy_id == copy.id,
            Reservation.status == Reservation.STATUS_ACTIVE,
            Reservation.is_deleted == 0,
        )
        .order_by(Reservation.id)  # 锁序稳定：多行也按 id 升序取锁，避免并发互等
        .with_for_update()
        .populate_existing()  # 锁后当前读（P1-F4 同款）：扫描期快照可能已被并发核销
        .all()
    )
    out: dict[str, list[int]] = {"switched": [], "excepted": []}
    if not rows:
        return out

    book = db.query(Book).filter(Book.id == copy.book_id).first()
    title = book.title if book else f"书目#{copy.book_id}"

    for res in rows:
        alt = (
            db.query(BookCopy)
            .filter(
                BookCopy.book_id == copy.book_id,
                BookCopy.id != copy.id,
                BookCopy.status == BookCopy.STATUS_AVAILABLE,
                BookCopy.is_deleted == 0,
            )
            .order_by(BookCopy.id)
            .with_for_update()
            .populate_existing()
            .first()
        )
        if alt is not None:
            # 改锁：副本换一册，预约单本身（孩子/书/到期时间）不动——家长侧无感，
            # 故 PRD 未要求改锁时通知家长（不造无需求的通知类型）。
            old_copy_id = res.copy_id
            res.copy_id = alt.id
            alt.status = BookCopy.STATUS_RESERVED
            db.flush()
            out["switched"].append(res.id)
            publish_audit(
                db,
                admin=admin,
                action="reservation.copy_switched",
                target_type="reservation",
                target_id=str(res.id),
                detail={"book": title, "from_copy": old_copy_id, "to_copy": alt.id},
                reason=reason,
            )
            continue

        res.status = Reservation.STATUS_EXCEPTION
        db.flush()
        out["excepted"].append(res.id)
        child = db.query(Child).filter(Child.id == res.child_id).first()
        parent = db.query(Parent).filter(Parent.id == child.parent_id).first() if child else None
        child_name = child.name if child else f"孩子#{res.child_id}"
        if child:
            NotificationService(db).send(
                parent_id=child.parent_id,
                scene=SCENE_RESERVATION_EXCEPTION,
                title="预约已转异常",
                content=(
                    f"《{title}》{cause}，且暂无其他在馆副本可替换，"
                    f"该预约已转为异常（不再占用借阅名额）。馆员会尽快与您联系，"
                    f"您也可以在小程序重新预约该书或其他书。"
                ),
                category=Notification.CATEGORY_RESERVATION,
                child_id=child.id,
                ref_type="reservation",
                ref_id=str(res.id),
                openid=parent.wechat_openid if parent else None,
            )
        AdminNotifyService(db).send(
            scene=AdminNotification.SCENE_RESERVATION_EXCEPTION,
            title="【预约异常·待跟进】",
            content=(
                f"《{title}》预约（#{res.id}，{child_name}）因{cause}且无其他在馆副本，"
                f"已转异常并已通知家长：{reason}——请跟进改约或补书"
            ),
            ref_type=AdminNotification.REF_RESERVATION,
            ref_id=str(res.id),
            applicant_name=child_name,
        )
        publish_audit(
            db,
            admin=admin,
            action="reservation.copy_unavailable_exception",
            target_type="reservation",
            target_id=str(res.id),
            detail={"book": title, "copy": copy.copy_code, "copy_id": copy.id},
            reason=reason,
        )
    return out


def settle_orphans_after_copy_gone(db: Session, *, copy_id: int, admin) -> None:
    """副本退场（**还书带损**）后的预约善后 —— 由 `circulation.return_book` **提交后**调用。

    P3-3（2026-10-18 第七十六批）：`报损`/`手工改状态` 都走 `handle_copy_unavailable`（副本退场的
    预约善后漏斗），**唯独"还书带损"没走**——靠的是"`reserved` 副本不会被借出，所以归还中的副本上
    没有 active 预约"这条**隐式前提**（审查报告 P3-3 记为"不可利用但属隐式前提"）。

    为什么必须**提交后**调用：`return_book` 持 `record → copy` 行锁，而漏斗按 **`预约 → 副本`**
    取锁（红线锁序）——反向加锁会与并发借书/预约创建互等死锁。提交后行锁已释放，锁序自然正确。

    正常路径零副作用（查不到 active 预约直接返回）；真命中 = 隐式前提被打破 → **错误日志 + 审计留痕**
    后按漏斗善后（预约改锁到同书其它在馆副本，或转 exception + 通知家长 + 馆员待办）。
    """
    import logging

    from backend.domain.catalog.models import BookCopy

    logger = logging.getLogger(__name__)
    copy = db.query(BookCopy).filter(BookCopy.id == copy_id).first()
    if copy is None:
        return
    active = (
        db.query(func.count(Reservation.id))
        .filter(
            Reservation.copy_id == copy_id,
            Reservation.status == Reservation.STATUS_ACTIVE,
            Reservation.is_deleted == 0,
        )
        .scalar()
    )
    if not active:
        return
    logger.error(
        "副本 #%s 退场时仍挂着 %s 条 active 预约——「reserved 副本不会被借出」的隐式前提被打破，"
        "按漏斗善后（预约改锁 / 转异常并通知）",
        copy_id,
        active,
    )
    publish_audit(
        db,
        admin=admin,
        action="circulation.copy_unavailable_with_active_reservation",
        target_type="book_copy",
        target_id=str(copy_id),
        detail={"active_reservations": active, "trigger": "return_with_damage"},
        reason="还书带损：副本退场时存在 active 预约（隐式前提被打破，已按漏斗善后）",
    )
    handle_copy_unavailable(
        db,
        copy,
        reason="还书带损：副本转为维护/遗失",
        admin=admin,
        cause="原预约的副本在还书时被判定损坏或遗失",
    )
    db.commit()
