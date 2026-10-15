# backend/domain/reading/reservation_ops.py — 预约期间副本失效的处置漏斗（G5）
"""PRD §5.5：「**预约期间书出意外**（例如被标记维修）：系统自动尝试换同一种书的其他在馆副本；
没有其他副本了，预约转『异常』状态并通知家长和馆员，名额立即释放。」

**触发点枚举（2026-10-14，全仓 `copy.status = …` 赋值点逐条比对）**
——能把副本写成 `maintenance`/`lost` 的链只有三条：

| 链 | 位置 | 挂钩 |
|---|---|---|
| 管理端改副本状态 | `catalog/service.py::update_copy_status` | ✅ |
| 报损·遗失（2026-10-12 拍板 P5） | `catalog/service.py::report_copy_damage` | ✅ |
| 还书带损 `condition=maintenance\\|lost` | `circulation/service.py::return_book` | ✗ **结构上不可达** |

不挂第三条是**可达性论证**、不是遗漏：`return_book` 要求该副本有 `active`/`overdue` 的借阅记录
⇒ 副本必为 `borrowed`；而预约锁定要求副本 `available→reserved`（`ReservationService.create`），
两状态互斥，且 `circulation.borrow()` 遇 `reserved` 副本直接拒绝并提示"走预约核销"。
**若将来口径变成"预约可锁 borrowed 副本"或借出链改锁序，本段结论必须重判。**

**锁序（宪法 §二 并发红线：全局 `Reservation → copy`）**：调用方必须在**写 `copy.status` 之前**
调用本函数——本函数先锁预约行、再锁替换副本，原副本由调用方在其后更新，全程无反向边。
若倒过来（先改副本再进本函数），锁序会变成 `copy → Reservation`，与核销/取消/超时释放的
`Reservation → copy` 相反；家长此刻核销 + 馆员此刻报损两笔并发即构成死锁环。

**为什么不写进 `reading/service.py`**：该文件已 791 行（架构关 god-file ≤800）；且本处置是
"副本侧事件驱动的预约善后"，与 `ReservationService` 的"家长自助动作"职责不同，独立成模块。
"""

from __future__ import annotations

from sqlalchemy.orm import Session

from backend.common.admin_notification_models import AdminNotification
from backend.common.admin_notifications import AdminNotifyService
from backend.common.notification_models import Notification
from backend.common.notifications import SCENE_RESERVATION_EXCEPTION, NotificationService
from backend.domain.catalog.audit_events import publish_audit
from backend.domain.catalog.models import Book, BookCopy
from backend.domain.identity.models import Child, Parent
from backend.domain.reading.models import Reservation


def handle_copy_unavailable(db: Session, copy: BookCopy, *, reason: str, admin) -> dict:
    """副本转维护中/遗失后的预约善后（PRD §5.5）。**由调用方在写 `copy.status` 之前调用。**

    分两路处置该副本上**唯一一条**活跃预约（同副本同时只能有一条——`create` 是在把副本锁成
    `reserved` 的前提下建单的）：

    - **有**同书其他在馆副本 → 预约**改锁**那册（`expires_at` 不变，预约继续有效）；
    - **没有** → 预约转 `Reservation.STATUS_EXCEPTION` + 通知家长 + 馆员待办；异常态不再是
      `active`，故额度按 §5.4 公式（`可借 = 上限 − 在借 − **预约中(active)**`）**自动释放**。

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
                    f"《{title}》原预约的副本已转为维护/遗失，且暂无其他在馆副本可替换，"
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
                f"《{title}》预约（#{res.id}，{child_name}）因副本转维护/遗失且无其他在馆副本，"
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
