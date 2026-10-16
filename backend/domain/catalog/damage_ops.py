# backend/domain/catalog/damage_ops.py — 图书损坏/遗失的**赔偿待办**（口径唯一来源）
"""PRD §3.6：押金不够扣时差额记"待结清"，系统持续提醒馆员；赔偿从押金扣或记待结清。

**为什么单开模块**（2026-10-15 大审查 P2 #14）：同一个业务事实「这本书在读者手上损坏/丢失」
有两条登记链——管理端「报损·遗失」与**还书时登记损坏/遗失**——原先只有前者写赔偿待办，
后者只改状态（`circulation/service.py` 的 docstring 还自称"遗失联动押金赔偿提示"，实际没有）。
两条链口径必须一致，否则不是"漏一条待办"的问题，而是运营视角下**同一件事两种后果**：
一条能被对账/待办捞到，另一条只能靠人记得。

不放进 `catalog/service.py`：该文件已在架构关 god-file 上限附近，而本函数是"副本侧事件 →
运营待办"的独立职责，与 `reading/reservation_ops.py` 同一处置。
"""

from __future__ import annotations

from sqlalchemy.orm import Session

from backend.common.admin_notification_models import AdminNotification
from backend.common.admin_notifications import AdminNotifyService
from backend.domain.catalog.models import Book, BookCopy


def notify_return_damage_compensation(
    db: Session,
    copy: BookCopy,
    *,
    condition: str,
    admin,
    child_id: int | None,
    borrow_record_id: int | None = None,
) -> None:
    """**还书带损**（`condition=maintenance|lost`）的赔偿待办入口。

    与「报损·遗失」同口径（P2 #14，2026-10-15 大审查）：同一次"书在读者手上坏了"，
    走哪个入口都要落同一条赔偿待办——否则运营视角下同一件事两种后果
    （一条能被待办/对账捞到，另一条只剩状态变更）。

    `borrow_record_id`：幂等键用（P1-3）——**借阅记录 id 是一次借阅的"不会重来的事实"**，
    同一册书"借了坏、还了再借又坏"能各提醒一条。
    """
    kind = "lost" if condition == "lost" else "damage"
    label = "遗失" if kind == "lost" else "损坏"
    notify_compensation_todo(
        db,
        copy,
        kind=kind,
        reason=f"还书时由馆员登记为{label}",
        admin=admin,
        child_id=child_id,
        event_key=f"borrow:{borrow_record_id}" if borrow_record_id is not None else None,
    )


def notify_compensation_todo(
    db: Session,
    copy: BookCopy,
    *,
    kind: str,
    reason: str,
    admin,
    child_id: int | None = None,
    event_key: str | None = None,
) -> None:
    """写一条「图书报损·待赔偿」管理端待办（调用方负责 commit / 审计留痕）。

    `kind`：`damage` | `lost`（与 `catalog/service.py::report_copy_damage` 同一枚举）。
    `child_id`：在借孩子；没有在借（库存书自损）时留 None，待办挂在副本上（ref_id=副本 id）。
    `event_key`：**幂等键**（第四轮大审查 P1-3，2026-10-17）。此前不传键 → 默认 "1"，
    而 `ref_id` 是 child_id → **同一孩子的第二笔损坏/遗失被静默吞掉**（一次还两本都带损、
    先丢 A 再丢 B 都只留一条待办），"库存书自损"分支还把副本 id 当孩子 id 用、两个自增空间撞号。
    现在：`ref_id` 有在借时仍是孩子 id、无在借时带 `copy:` 前缀（不再与孩子 id 同池）；
    幂等键由调用方给"不会重来的事实"——还书带损传 `borrow:{借阅记录id}`、报损传
    `copy:{副本id}:{kind}`。`ignore_handled=True`：处理完后再出事还能再提醒。
    """
    from backend.domain.identity.models import Child

    book = db.query(Book).filter(Book.id == copy.book_id).first()
    title = book.title if book else f"副本{copy.copy_code}"
    child_name = "（未在借）"
    if child_id is not None:
        child = db.query(Child).filter(Child.id == child_id).first()
        child_name = child.name if child else child_name
    AdminNotifyService(db).send(
        scene=AdminNotification.SCENE_COPY_DAMAGE_COMPENSATION,
        title="【图书报损·待赔偿】",
        content=(
            f"【图书报损·待赔偿】《{title}》（副本 {copy.copy_code}，借阅人 {child_name}）"
            f"登记为{'遗失' if kind == 'lost' else '损坏'}：{reason}"
            "——请按赔偿规则处理（押金扣除或记待结清）"
        ),
        ref_type=AdminNotification.REF_CHILD,
        ref_id=str(child_id) if child_id is not None else f"copy:{copy.id}",
        applicant_name=child_name,
        dedup_key=event_key or f"{kind}:{copy.id}",
        ignore_handled=True,
    )
