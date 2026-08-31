# backend/common/admin_notifications.py — 管理端通知服务（WM13 批次一底座）
"""AdminNotifyService：写入（幂等）+ 审计回写 + StatusResolver 显示态实时计算。

设计（任务包 v2 灵魂）：
- 显示态实时算：resolve_* 实时 JOIN 业务表判定"待处理/已审结/已失效"，
  列表与计数共用同一口径（S1 结构性歼灭）；
- 审计态事件写：mark_handled 只写 handled_at/handled_by（审计展示，不参与显示态判定）；
- 幂等：send 先查后插 + INSERT IGNORE 撞唯一索引静默忽略（B11/家长通知 P0 修复同款，
  不污染共享事务）。

显示态映射（拷问 Q5 裁定表）：
- 待处理：业务对象仍在"待审核"态（refund pending / withdrawal applying / transfer pending /
  activity 有 REFUND_PENDING / refund_execute_failed 的 failed）
- 已失效：家长已撤销（cancelled）/ 转让超时（expired）——文案注明原因
- 已审结：审核动作已完成的所有下游态（approved/processing/refunded/rejected/…）
"""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal

from sqlalchemy import func
from sqlalchemy.dialects.mysql import insert as mysql_insert
from sqlalchemy.orm import Session

from backend.common.admin_notification_models import AdminNotification

# 显示态常量（effective_status）
ST_PENDING = "pending"
ST_DONE = "done"
ST_INVALID = "invalid"

TEXT_PENDING = "待处理"
TEXT_DONE = "已审结"
TEXT_INVALID_CANCELLED = "已失效·家长已撤销"
TEXT_INVALID_EXPIRED = "已失效·已超时自动失效"


class AdminNotifyService:
    """管理端待办通知（写入 + 审计 + 显示态解析）。"""

    def __init__(self, db: Session):
        self.db = db

    # ---------- 写入（幂等） ----------

    def send(
        self,
        *,
        scene: str,
        title: str,
        content: str,
        ref_type: str,
        ref_id: str | int,
        applicant_name: str = "",
        amount: Decimal | None = None,
        dedup_key: str = "1",
    ) -> bool:
        """写入管理待办通知（幂等）。返回是否新写入（False=重复已存在）。

        并发安全：INSERT IGNORE 撞唯一约束静默忽略（rowcount=0），零异常、
        不污染事务——同事务触发点（申请落库）不会连坐回滚（B11/家长通知 P0 同款）。
        """
        exists = (
            self.db.query(func.count(AdminNotification.id))
            .filter(
                AdminNotification.scene == scene,
                AdminNotification.ref_type == ref_type,
                AdminNotification.ref_id == str(ref_id),
                AdminNotification.dedup_key == dedup_key,
                AdminNotification.is_deleted == 0,
            )
            .scalar()
        )
        if exists:
            return False

        stmt = mysql_insert(AdminNotification).values(
            scene=scene,
            title=title,
            content=content,
            ref_type=ref_type,
            ref_id=str(ref_id),
            applicant_name=applicant_name,
            amount=amount,
            dedup_key=dedup_key,
            created_at=datetime.now(),
        )
        result = self.db.execute(stmt.prefix_with("IGNORE"))
        if result.rowcount == 0:
            return False  # 并发窗口撞唯一索引，已由他事务写入
        return True

    # ---------- 审计回写（幂等，Q8：保留首次） ----------

    def mark_handled(self, *, ref_type: str, ref_id: str | int, admin) -> int:
        """审计回写：handled_at/handled_by。幂等：已处理跳过（保留首次审计）。返回更新条数。"""
        updated = (
            self.db.query(AdminNotification)
            .filter(
                AdminNotification.ref_type == ref_type,
                AdminNotification.ref_id == str(ref_id),
                AdminNotification.handled_at.is_(None),
                AdminNotification.is_deleted == 0,
            )
            .update(
                {
                    "handled_at": datetime.now(),
                    "handled_by": admin.id,
                },
                synchronize_session=False,
            )
        )
        return updated

    # ---------- StatusResolver（显示态实时算） ----------

    def resolve_many(self, notifications: list[AdminNotification]) -> dict[int, dict]:
        """批量解析显示态。返回 {通知id: {"effective_status", "status_text"}}。

        按 ref_type 分组 IN 查询业务表（零 N+1）；列表与计数共用本方法（口径一致）。
        找不到业务对象（被物理清理等极端情况）→ 已审结兜底（防御，不挂待办墙）。
        """
        result: dict[int, dict] = {}
        by_type: dict[str, list[AdminNotification]] = {}
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

    def resolve_one(self, notification: AdminNotification) -> dict:
        """单条显示态解析（与 resolve_many 同一判定逻辑）。"""
        return self.resolve_many([notification])[notification.id]

    # ---------- 判定分支（Q5 裁定映射表） ----------

    def _decide_refund(self, status: str | None) -> dict:
        """refund_apply：pending→待处理；cancelled→失效·撤销；其余→已审结（含 failed，Q5）。"""
        if status is None:
            return {"effective_status": ST_DONE, "status_text": TEXT_DONE}
        if status == "pending":
            return {"effective_status": ST_PENDING, "status_text": TEXT_PENDING}
        if status == "cancelled":
            return {"effective_status": ST_INVALID, "status_text": TEXT_INVALID_CANCELLED}
        return {"effective_status": ST_DONE, "status_text": TEXT_DONE}

    def _decide_execute_failed(self, status: str | None) -> dict:
        """refund_execute_failed：failed→待处理（需重试）；refunded→已审结；cancelled→失效。"""
        if status is None:
            return {"effective_status": ST_DONE, "status_text": TEXT_DONE}
        if status == "failed":
            return {"effective_status": ST_PENDING, "status_text": TEXT_PENDING}
        if status == "cancelled":
            return {"effective_status": ST_INVALID, "status_text": TEXT_INVALID_CANCELLED}
        return {"effective_status": ST_DONE, "status_text": TEXT_DONE}

    def _decide_withdrawal(self, status: str | None) -> dict:
        """withdrawal_apply：applying→待处理；cancelled→失效·撤销；其余→已审结。"""
        if status is None:
            return {"effective_status": ST_DONE, "status_text": TEXT_DONE}
        if status == "applying":
            return {"effective_status": ST_PENDING, "status_text": TEXT_PENDING}
        if status == "cancelled":
            return {"effective_status": ST_INVALID, "status_text": TEXT_INVALID_CANCELLED}
        return {"effective_status": ST_DONE, "status_text": TEXT_DONE}

    def _decide_transfer(self, status: str | None) -> dict:
        """transfer_apply / transfer_expiring：pending→待处理；expired→失效·超时；
        cancelled→失效·撤销；其余→已审结。"""
        if status is None:
            return {"effective_status": ST_DONE, "status_text": TEXT_DONE}
        if status == "pending":
            return {"effective_status": ST_PENDING, "status_text": TEXT_PENDING}
        if status == "expired":
            return {"effective_status": ST_INVALID, "status_text": TEXT_INVALID_EXPIRED}
        if status == "cancelled":
            return {"effective_status": ST_INVALID, "status_text": TEXT_INVALID_CANCELLED}
        return {"effective_status": ST_DONE, "status_text": TEXT_DONE}

    def _decide_activity(self, has_refund_pending: bool) -> dict:
        """activity_batch_refund：仍有 REFUND_PENDING→待处理；全部终态→已审结（A3 裁定）。"""
        if has_refund_pending:
            return {"effective_status": ST_PENDING, "status_text": TEXT_PENDING}
        return {"effective_status": ST_DONE, "status_text": TEXT_DONE}

    # ---------- 批量取业务状态（零 N+1） ----------

    def _state_map(self, ref_type: str, by_type: dict, query_fn) -> dict[str, str]:
        """按 ref_id 集合查业务表状态。query_fn(ids) -> {ref_id: status}。"""
        ids = [n.ref_id for n in by_type.get(ref_type, [])]
        if not ids:
            return {}
        return query_fn(ids)

    def _refund_states(self):
        def q(ids: list[str]) -> dict[str, str]:
            from backend.domain.identity.models import RefundRequest

            rows = self.db.query(RefundRequest.id, RefundRequest.status).filter(
                RefundRequest.id.in_([int(i) for i in ids]),
                RefundRequest.is_deleted == 0,
            )
            return {str(r.id): r.status for r in rows}

        return q

    def _withdrawal_states(self):
        def q(ids: list[str]) -> dict[str, str]:
            from backend.domain.identity.models import WithdrawalRequest

            rows = self.db.query(WithdrawalRequest.id, WithdrawalRequest.status).filter(
                WithdrawalRequest.id.in_([int(i) for i in ids]),
                WithdrawalRequest.is_deleted == 0,
            )
            return {str(r.id): r.status for r in rows}

        return q

    def _transfer_states(self):
        def q(ids: list[str]) -> dict[str, str]:
            from backend.domain.identity.models import TransferRequest

            rows = self.db.query(TransferRequest.id, TransferRequest.status).filter(
                TransferRequest.id.in_([int(i) for i in ids]),
                TransferRequest.is_deleted == 0,
            )
            return {str(r.id): r.status for r in rows}

        return q

    def _activity_states(self, notifications: list[AdminNotification]) -> dict[str, bool]:
        """活动批量退款：{activity_id: 是否仍有 REFUND_PENDING}（A3：无则全部终态）。"""
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
