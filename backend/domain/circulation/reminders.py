# backend/domain/circulation/reminders.py — 借阅到期提醒（定时任务体）
"""为什么单开（与 `reading/reservation_ops.py` 同一处置）：`circulation/service.py` 触及架构关
god-file 上限（>800 行），把"定时任务体"这类**与请求链无关**的自洽逻辑分出来。

调用方：`tasks/registry`（每日任务）→ `CirculationService.book_due_remind()` → 本函数。

已知挂账（**未修，下一批**）：`docs/审查报告-全量-2026-10-15.md` P2 #18——补发窗口重叠会让
**当天到期**的记录同时命中"0 天"与"1 天"两个节点，从而发两条站内信（两节点 dedup_key 不同）。
"""

from __future__ import annotations

from datetime import datetime, timedelta

from sqlalchemy.orm import Session

from backend.common.config_service import ConfigService
from backend.common.notification_models import Notification
from backend.common.notifications import SCENE_BORROW_DUE_REMIND, NotificationService
from backend.domain.catalog.models import Book
from backend.domain.circulation.models import BorrowRecord
from backend.domain.identity.models import Child, Parent


def book_due_remind(db: Session) -> int:
    """借阅即将到期提醒（due_remind_days 节点；每节点一次）。

    补发窗口（P2-5，2026-10-09 审查）：每个节点向前放宽 1 天，停机一整天也能补发；
    文案按**实际剩余天数**算；幂等键仍是节点值 `str(days)`。
    """
    remind_days = [
        int(x)
        for x in ConfigService(db).get_value("due_remind_days", "5,3,1,0").split(",")
        if x.strip() != ""
    ]
    if not remind_days:
        return 0
    today = datetime.now().date()
    sent = 0
    catchup = timedelta(days=1)
    nodes = sorted(set(remind_days))
    for idx, days in enumerate(nodes):
        target = today + timedelta(days=days)
        # P2 #18（2026-10-16，第三轮大审查）：**节点窗口必须互不重叠**。原实现每个节点都向前放宽
        # 1 天（补发窗口），于是相邻整数节点（1 与 0）的窗口都盖住"今天到期"这一档 → 同一条记录
        # 命中两个节点、发**两条**站内信（两节点 dedup_key 不同，唯一索引拦不住）。
        # 修法：低边界取 `max(节点-1天, 上一个更小节点的 target+1天)`——补发能力保留（最低节点与
        # 间隔 ≥2 天的节点窗口不变），只是不再与更紧迫的节点抢同一天。
        lower = target - catchup
        if idx > 0:
            prev_target = today + timedelta(days=nodes[idx - 1])
            lower = max(lower, prev_target + timedelta(days=1))
        records = (
            db.query(BorrowRecord)
            .filter(
                BorrowRecord.is_deleted == 0,
                BorrowRecord.status == BorrowRecord.STATUS_ACTIVE,
                BorrowRecord.due_at >= lower,
                BorrowRecord.due_at < target + timedelta(days=1),
            )
            .all()
        )
        for rec in records:
            child = db.query(Child).filter(Child.id == rec.child_id).first()
            if not child:
                continue
            book = db.query(Book).filter(Book.id == rec.book_id).first()
            title = book.title if book else f"书目#{rec.book_id}"
            parent = db.query(Parent).filter(Parent.id == child.parent_id).first()
            actual_days = (rec.due_at.date() - today).days
            label = (
                "今天"
                if actual_days == 0
                else (f"{actual_days} 天后" if actual_days > 0 else f"已逾期 {abs(actual_days)} 天")
            )
            if NotificationService(db).send(
                parent_id=child.parent_id,
                scene=SCENE_BORROW_DUE_REMIND,
                title="借阅到期提醒",
                content=f"《{title}》将于{label}（{rec.due_at:%Y-%m-%d}）到期，请及时归还或续借。",
                category=Notification.CATEGORY_BORROW,
                child_id=child.id,
                ref_type="borrow_record",
                ref_id=str(rec.id),
                dedup_key=str(days),
                openid=parent.wechat_openid if parent else None,
            ):
                sent += 1
    if sent:
        db.commit()
    return sent
