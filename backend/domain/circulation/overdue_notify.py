# backend/domain/circulation/overdue_notify.py — 逾期标记落库 + 家长通知（W6 压测整改）
"""`CirculationService.overdue_mark` 的**执行体**（2026-10-18 第七轮 W6 压测后抽出）。

**为什么抽出来**：`circulation/service.py` 已在 god-file 上限（800 行），而本次整改要**加**批量取数；
抽成独立模块既给服务文件腾出空间，也让"逾期通知"这条动线单独可测（与 `reminders.py` 同款先例）。

**压测实测**（2026-10-18，一次性压测库、6000 条逾期）：原实现**每条逾期**各查一次
`Child`/`Book`/`Parent`（3N 条 SELECT）+ 逐条状态守卫 UPDATE + 逐条通知 → **104.39 秒**。
任务间隔 300 秒（调度器 `max_instances=1` 不重叠，见 `tasks/registry.py:543`），6000 条尚能跑完，
**2 万条会吃掉整个周期**（逾期通知延迟）。整改：三张表各一次 `IN` 批量取——**查询数与记录数解耦**；
UPDATE 与通知保持逐条（前者是并发状态守卫 P1-F5，后者带去重键语义，都不能合并）。
"""

from __future__ import annotations

from sqlalchemy import update as sa_update
from sqlalchemy.orm import Session

from backend.common.notification_models import Notification
from backend.common.notifications import SCENE_BORROW_OVERDUE, NotificationService
from backend.domain.catalog.models import Book
from backend.domain.circulation.models import BorrowRecord
from backend.domain.identity.models import Child, Parent


def _by_ids(db: Session, model, ids: list[int]) -> list:
    """批量取行（空列表直接返回空，避免 `IN ()` 语法错）。"""
    if not ids:
        return []
    return db.query(model).filter(model.id.in_(set(ids))).all()


def _notified_keys(db: Session, record_ids: list[int]) -> set[tuple[str, str]]:
    """本轮候选记录里**已经通知过**的 `(ref_id, dedup_key)` 集合（一次 IN 查询）。

    为什么要它（第八轮审查 P1-1 修复的一部分）：扫描面从"只扫 ACTIVE"放宽到
    "ACTIVE ∪ OVERDUE"后，每一轮都会重新看到**全部存量逾期行**——若不做这个预取，
    6000 条存量逾期每 300 秒就要各发一次"存在性 SELECT"（`NotificationService.send`
    内部先查重）。预取让稳态开销回到"一次 IN 查询 + 只对新记录写通知"。
    """
    ids = [str(i) for i in set(record_ids)]
    if not ids:
        return set()
    rows = (
        db.query(Notification.ref_id, Notification.dedup_key)
        .filter(
            Notification.scene == SCENE_BORROW_OVERDUE,
            Notification.ref_type == "borrow_record",
            Notification.ref_id.in_(ids),
            Notification.is_deleted == 0,
        )
        .all()
    )
    return {(r[0], r[1]) for r in rows}


def mark_and_notify(db: Session, overdue: list[BorrowRecord]) -> int:
    """把扫描出的逾期记录标 `OVERDUE` 并逐条通知家长；返回**本轮新标记**条数。

    调用方（`CirculationService.overdue_mark`）负责扫描与最终 commit——本函数只执行，
    不提交，保持"一次任务一次提交"的原语义。

    **第八轮审查 P1-1（2026-10-19）**：扫描面含 `OVERDUE` 之后，本函数对"已 OVERDUE"
    的行**不再重复 CAS**（那是借阅台惰性标记干的），但**仍然补发通知**——修复前
    `overdue_mark` 只扫 `ACTIVE`，而借阅台（`GET /api/admin/circulation/overdue`）
    会先把 ACTIVE 改成 OVERDUE，于是"馆员先看到的那批"永远等不到 `borrow.overdue` 通知。
    """
    marked = 0
    # 批量上下文：三张表各一次查询（原先是 3N 次）
    kids = {c.id: c for c in _by_ids(db, Child, [r.child_id for r in overdue])}
    books = {b.id: b for b in _by_ids(db, Book, [r.book_id for r in overdue])}
    parents = {p.id: p for p in _by_ids(db, Parent, [c.parent_id for c in kids.values()])}
    notified = _notified_keys(db, [r.id for r in overdue])

    for rec in overdue:
        key = f"overdue:{rec.due_at:%Y%m%d}"
        if rec.status == BorrowRecord.STATUS_ACTIVE:
            # P1-F5：状态守卫条件写（只 ACTIVE→OVERDUE），防与还书并发时覆盖已 returned。
            # **synchronize_session=False 是性能命门**（W6 压测 + cProfile 实证）：ORM 版 UPDATE
            # 默认 `synchronize_session="auto"` → 会在 **session 身份映射**里逐对象求值 WHERE 条件，
            # 而本函数的 session 恰好装着扫描出的全部逾期对象 → **O(N²)**：6000 条 = 3600 万次
            # `evaluator.evaluate`，占 334s 里的 305s（实测）。这里紧接着手工 `rec.status = OVERDUE`
            # 已同步对象状态，故关掉自动同步语义等价、复杂度回到 O(N)。
            result = db.execute(
                sa_update(BorrowRecord)
                .where(
                    BorrowRecord.id == rec.id,
                    BorrowRecord.status == BorrowRecord.STATUS_ACTIVE,
                )
                .values(status=BorrowRecord.STATUS_OVERDUE)
                .execution_options(synchronize_session=False)
            )
            if result.rowcount == 0:
                continue  # 已被并发方还书/推进，跳过（不误发逾期通知）
            rec.status = BorrowRecord.STATUS_OVERDUE
            marked += 1
        if (str(rec.id), key) in notified:
            continue  # 本周期已通知过（去重键=到期日，续借后再逾期会生成新键）
        notified.add((str(rec.id), key))
        child = kids.get(rec.child_id)
        if not child:
            continue
        book = books.get(rec.book_id)
        title = book.title if book else f"书目#{rec.book_id}"
        parent = parents.get(child.parent_id)
        NotificationService(db).send(
            parent_id=child.parent_id,
            scene=SCENE_BORROW_OVERDUE,
            title="图书已逾期",
            content=f"《{title}》已逾期未还（到期日 {rec.due_at:%Y-%m-%d}），请尽快归还。",
            category=Notification.CATEGORY_BORROW,
            child_id=child.id,
            ref_type="borrow_record",
            ref_id=str(rec.id),
            # P1-2（2026-10-18 第七十五批）：原先不传 dedup_key → 落默认 "1"
            # = 「同一家长 × 场景 × 借阅行**终身一条**」。续借（G7 人工办理）后再次逾期时，
            # 条件 UPDATE 会重新标记 OVERDUE，但通知被唯一索引静默吞掉 → 家长不知道又逾期了。
            # 周期类场景的去重键必须**带周期**（本次到期日）。
            dedup_key=key,
            openid=parent.wechat_openid if parent else None,
        )
    return marked
