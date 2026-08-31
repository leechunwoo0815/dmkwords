# backend/domain/admin/todo_service.py — WM13 管理待办查询/聚合/显示态解析
"""AdminTodoService（编排层）。

B10 边界：写入（send/mark_handled）在 common/admin_notifications.py（纯写零域依赖）；
显示态实时算需要 JOIN 业务表 + 权限判定 + 审计留痕——归 admin 域（B10 检查点：
"handler 要查业务域 → 放编排层"）。列表与计数共用同一 resolver（口径一致，v2）。
"""

from __future__ import annotations

import json
from datetime import datetime

from sqlalchemy import func
from sqlalchemy.orm import Session

from backend.common.admin_notification_models import AdminNotification
from backend.common.admin_notifications import (
    ST_DONE,
    ST_INVALID,
    ST_PENDING,
    TEXT_DONE,
    TEXT_INVALID_CANCELLED,
    TEXT_INVALID_EXPIRED,
    TEXT_PENDING,
)
from backend.common.exceptions import NotFoundError, ValidationError
from backend.domain.admin.models import AdminUser
from backend.domain.admin.service import role_has_permission
from backend.domain.catalog.audit_events import publish_audit


class AdminTodoService:
    """WM13 管理待办查询/聚合/显示态解析（编排层）。

    B10 边界：写入（send/mark_handled）在 common/admin_notifications.py（纯写零域依赖）；
    显示态实时算需要 JOIN 业务表 + 权限判定 + 审计留痕——归 admin 域（B10 检查点：
    "handler 要查业务域 → 放编排层"）。列表与计数共用同一 resolver（口径一致，v2）。
    """

    def __init__(self, db: Session):
        self.db = db

    # ---------- StatusResolver（显示态实时算） ----------

    def resolve_many(self, notifications: list) -> dict[int, dict]:
        """批量解析显示态。返回 {通知id: {"effective_status", "status_text"}}。

        按 ref_type 分组 IN 查询业务表（零 N+1）；列表与计数共用本方法（口径一致）。
        找不到业务对象（被物理清理等极端情况）→ 已审结兜底（防御，不挂待办墙）。
        """
        result: dict[int, dict] = {}
        by_type: dict[str, list] = {}
        for n in notifications:
            by_type.setdefault(n.ref_type, []).append(n)

        refund_states = self._state_map("refund_request", by_type, self._refund_states())
        withdrawal_states = self._state_map(
            "withdrawal_request", by_type, self._withdrawal_states()
        )
        transfer_states = self._state_map("transfer", by_type, self._transfer_states())
        activity_states = self._activity_states(by_type.get("activity", []))

        for n in notifications:
            if n.ref_type == "refund_request":
                status = refund_states.get(n.ref_id)
                if n.scene == AdminNotification.SCENE_REFUND_EXECUTE_FAILED:
                    result[n.id] = self._decide_execute_failed(status)
                else:
                    result[n.id] = self._decide_refund(status)
            elif n.ref_type == "withdrawal_request":
                result[n.id] = self._decide_withdrawal(withdrawal_states.get(n.ref_id))
            elif n.ref_type == "transfer":
                result[n.id] = self._decide_transfer(transfer_states.get(n.ref_id))
            elif n.ref_type == "activity":
                result[n.id] = self._decide_activity(activity_states.get(n.ref_id, False))
            else:
                result[n.id] = {"effective_status": ST_DONE, "status_text": TEXT_DONE}
        return result

    def resolve_one(self, notification) -> dict:
        return self.resolve_many([notification])[notification.id]

    # ---------- 判定分支（拷问 Q5 裁定映射表） ----------

    @staticmethod
    def _decide_refund(status: str | None) -> dict:
        """refund_apply：pending→待处理；cancelled→失效·撤销；其余→已审结（含 failed，Q5）。"""
        if status is None:
            return {"effective_status": ST_DONE, "status_text": TEXT_DONE}
        if status == "pending":
            return {"effective_status": ST_PENDING, "status_text": TEXT_PENDING}
        if status == "cancelled":
            return {"effective_status": ST_INVALID, "status_text": TEXT_INVALID_CANCELLED}
        return {"effective_status": ST_DONE, "status_text": TEXT_DONE}

    @staticmethod
    def _decide_execute_failed(status: str | None) -> dict:
        """refund_execute_failed：failed→待处理（需重试）；refunded→已审结；cancelled→失效。"""
        if status is None:
            return {"effective_status": ST_DONE, "status_text": TEXT_DONE}
        if status == "failed":
            return {"effective_status": ST_PENDING, "status_text": TEXT_PENDING}
        if status == "cancelled":
            return {"effective_status": ST_INVALID, "status_text": TEXT_INVALID_CANCELLED}
        return {"effective_status": ST_DONE, "status_text": TEXT_DONE}

    @staticmethod
    def _decide_withdrawal(status: str | None) -> dict:
        if status is None:
            return {"effective_status": ST_DONE, "status_text": TEXT_DONE}
        if status == "applying":
            return {"effective_status": ST_PENDING, "status_text": TEXT_PENDING}
        if status == "cancelled":
            return {"effective_status": ST_INVALID, "status_text": TEXT_INVALID_CANCELLED}
        return {"effective_status": ST_DONE, "status_text": TEXT_DONE}

    @staticmethod
    def _decide_transfer(status: str | None) -> dict:
        if status is None:
            return {"effective_status": ST_DONE, "status_text": TEXT_DONE}
        if status == "pending":
            return {"effective_status": ST_PENDING, "status_text": TEXT_PENDING}
        if status == "expired":
            return {"effective_status": ST_INVALID, "status_text": TEXT_INVALID_EXPIRED}
        if status == "cancelled":
            return {"effective_status": ST_INVALID, "status_text": TEXT_INVALID_CANCELLED}
        return {"effective_status": ST_DONE, "status_text": TEXT_DONE}

    @staticmethod
    def _decide_activity(has_refund_pending: bool) -> dict:
        """activity_batch_refund：仍有 REFUND_PENDING→待处理；全部终态→已审结（A3 裁定）。"""
        if has_refund_pending:
            return {"effective_status": ST_PENDING, "status_text": TEXT_PENDING}
        return {"effective_status": ST_DONE, "status_text": TEXT_DONE}

    # ---------- 批量取业务状态（零 N+1） ----------

    @staticmethod
    def _state_map(ref_type: str, by_type: dict, query_fn) -> dict[str, str]:
        ids = [n.ref_id for n in by_type.get(ref_type, [])]
        if not ids:
            return {}
        return query_fn(ids)

    def _refund_states(self):
        from backend.domain.identity.models import RefundRequest

        def q(ids: list[str]) -> dict[str, str]:
            rows = self.db.query(RefundRequest.id, RefundRequest.status).filter(
                RefundRequest.id.in_([int(i) for i in ids]),
                RefundRequest.is_deleted == 0,
            )
            return {str(r.id): r.status for r in rows}

        return q

    def _withdrawal_states(self):
        from backend.domain.identity.models import WithdrawalRequest

        def q(ids: list[str]) -> dict[str, str]:
            rows = self.db.query(WithdrawalRequest.id, WithdrawalRequest.status).filter(
                WithdrawalRequest.id.in_([int(i) for i in ids]),
                WithdrawalRequest.is_deleted == 0,
            )
            return {str(r.id): r.status for r in rows}

        return q

    def _transfer_states(self):
        from backend.domain.identity.models import TransferRequest

        def q(ids: list[str]) -> dict[str, str]:
            rows = self.db.query(TransferRequest.id, TransferRequest.status).filter(
                TransferRequest.id.in_([int(i) for i in ids]),
                TransferRequest.is_deleted == 0,
            )
            return {str(r.id): r.status for r in rows}

        return q

    def _activity_states(self, notifications: list) -> dict[str, bool]:
        """{activity_id: 是否仍有 REFUND_PENDING}（A3：无则全部终态）。"""
        if not notifications:
            return {}
        from backend.domain.activity.models import ActivityEnrollment

        ids = [int(n.ref_id) for n in notifications]
        result = {str(i): False for i in ids}
        rows = self.db.query(ActivityEnrollment.activity_id, ActivityEnrollment.status).filter(
            ActivityEnrollment.activity_id.in_(ids),
            ActivityEnrollment.is_deleted == 0,
        )
        for activity_id, status in rows:
            if status == ActivityEnrollment.STATUS_REFUND_PENDING:
                result[str(activity_id)] = True
        return result

    # ---------- 收件箱（批次二） ----------

    def list_inbox(
        self,
        page: int,
        page_size: int,
        *,
        status_filter: str | None = None,
        scene: str | None = None,
        keyword: str | None = None,
        viewer_is_super: bool,
    ) -> dict:
        """管理待办收件箱：显示态实时算（与计数同一口径，v2 反口径分叉）。

        权限（S2/v2）：非超管返回空数据（不 403，空列表）。
        status_filter: pending=待处理 / finished=已审结+已失效 / None=全部。
        排序：待处理优先，组内 created_at 升序（等待最久的排前——运营处理优先级）。
        """
        if not viewer_is_super:
            return {
                "items": [],
                "total": 0,
                "pending_count": 0,
                "page": page,
                "page_size": page_size,
            }

        q = self.db.query(AdminNotification).filter(AdminNotification.is_deleted == 0)
        if scene:
            q = q.filter(AdminNotification.scene == scene)
        if keyword:
            like = f"%{keyword}%"
            q = q.filter(
                (AdminNotification.applicant_name.like(like))
                | (AdminNotification.content.like(like))
            )
        rows = q.order_by(AdminNotification.created_at.desc(), AdminNotification.id.desc()).all()

        resolved = self.resolve_many(rows)
        pending_count = sum(1 for n in rows if resolved[n.id]["effective_status"] == ST_PENDING)

        enriched: list[tuple] = []
        for n in rows:
            eff = resolved[n.id]
            if status_filter == "pending" and eff["effective_status"] != ST_PENDING:
                continue
            if status_filter == "finished" and eff["effective_status"] == ST_PENDING:
                continue
            enriched.append((n, eff))
        enriched.sort(
            key=lambda t: (
                0 if t[1]["effective_status"] == ST_PENDING else 1,
                t[0].created_at,
                t[0].id,
            )
        )
        total = len(enriched)
        page_rows = enriched[(page - 1) * page_size : (page - 1) * page_size + page_size]

        handled_by_ids = {n.handled_by for n, _ in page_rows if n.handled_by is not None}
        names: dict[int, str] = {}
        if handled_by_ids:
            for uid, uname in (
                self.db.query(AdminUser.id, AdminUser.display_name)
                .filter(AdminUser.id.in_(handled_by_ids))
                .all()
            ):
                names[uid] = uname or ""

        items = [
            {
                "id": n.id,
                "scene": n.scene,
                "title": n.title,
                "content": n.content,
                "ref_type": n.ref_type,
                "ref_id": n.ref_id,
                "applicant_name": n.applicant_name,
                "amount": str(n.amount) if n.amount is not None else None,
                "created_at": n.created_at.isoformat() if n.created_at else None,
                "handled_at": n.handled_at.isoformat() if n.handled_at else None,
                "handled_by_name": names.get(n.handled_by) if n.handled_by else None,
                "effective_status": eff["effective_status"],
                "status_text": eff["status_text"],
            }
            for n, eff in page_rows
        ]
        return {
            "items": items,
            "total": total,
            "pending_count": pending_count,
            "page": page,
            "page_size": page_size,
        }

    def handle(self, notification_id: int, admin, reason: str) -> dict:
        """手动兜底标记已处理（S4：reason 必填 + 审计留痕；幂等保留首次）。"""
        if not reason or not reason.strip():
            raise ValidationError("必须填写处理原因（留痕）")
        n = (
            self.db.query(AdminNotification)
            .filter(AdminNotification.id == notification_id, AdminNotification.is_deleted == 0)
            .first()
        )
        if not n:
            raise NotFoundError("通知不存在")
        if n.handled_at is not None:
            return {"id": n.id, "handled": True, "already": True}
        n.handled_at = datetime.now()
        n.handled_by = admin.id
        n.extra = json.dumps({"reason": reason.strip(), "method": "manual"}, ensure_ascii=False)
        publish_audit(
            self.db,
            admin=admin,
            action="admin_notification.handle",
            target_type="admin_notification",
            target_id=str(n.id),
            detail={"scene": n.scene, "ref_type": n.ref_type, "ref_id": n.ref_id},
            reason=reason.strip(),
        )
        self.db.commit()
        return {"id": n.id, "handled": True, "already": False}

    # ---------- 感知层聚合（批次三） ----------

    def todo_counts(self, admin) -> dict:
        """WM13 todo-counts（实时口径，与收件箱同一 resolver）。

        权限粒度（Q9 裁定）：审计五类仅超管（staff 为 0，S2）；
        order_pending_manual 跟 member.manage 权限走（staff 看真实数）；
        admin_total = 管理待办全部 pending（与 list_inbox.pending_count 严格一致）。
        """
        counts = {
            "refund_pending": 0,
            "withdrawal_pending": 0,
            "transfer_pending": 0,
            "transfer_expiring": 0,
            "activity_batch_refund": 0,
            "order_pending_manual": 0,
            "admin_total": 0,
        }
        if admin.role == AdminUser.ROLE_SUPER_ADMIN:
            all_rows = (
                self.db.query(AdminNotification).filter(AdminNotification.is_deleted == 0).all()
            )
            resolved = self.resolve_many(all_rows)
            scene_key = {
                AdminNotification.SCENE_REFUND_APPLY: "refund_pending",
                AdminNotification.SCENE_REFUND_EXECUTE_FAILED: "refund_pending",
                AdminNotification.SCENE_WITHDRAWAL_APPLY: "withdrawal_pending",
                AdminNotification.SCENE_TRANSFER_APPLY: "transfer_pending",
                AdminNotification.SCENE_TRANSFER_EXPIRING: "transfer_expiring",
                AdminNotification.SCENE_ACTIVITY_BATCH_REFUND: "activity_batch_refund",
            }
            for n in all_rows:
                if resolved[n.id]["effective_status"] != ST_PENDING:
                    continue
                key = scene_key.get(n.scene)
                if key:
                    counts[key] += 1
            counts["admin_total"] = sum(
                counts[k]
                for k in (
                    "refund_pending",
                    "withdrawal_pending",
                    "transfer_pending",
                    "transfer_expiring",
                    "activity_batch_refund",
                )
            )
        if role_has_permission(admin.role, "member.manage"):
            from backend.domain.identity.models import Order

            counts["order_pending_manual"] = (
                self.db.query(func.count(Order.id))
                .filter(Order.status == Order.STATUS_PENDING_MANUAL, Order.is_deleted == 0)
                .scalar()
                or 0
            )
        return counts
