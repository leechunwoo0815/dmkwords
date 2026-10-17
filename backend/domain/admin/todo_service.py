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
from backend.common.sql_utils import escape_like
from backend.domain.activity.models import ActivityEnrollment
from backend.domain.admin.models import AdminUser
from backend.domain.admin.service import role_has_permission
from backend.domain.catalog.audit_events import publish_audit

#: **知会类场景**（运营感知，不是待审事项）：不进待办墙默认名单，只有显式按 `scene` 筛选才出现。
#: G9（2026-10-13 第五十五轮）：里程碑达成是"通知馆员去线下表扬/发朋友圈"的知会，
#: 没有待审状态机、也没有可处理动作——混在待办列表里只是噪声（用户 2026-10-13 明确要求分开）。
#: 后续新增同类（纯知会、无人工审结动作）的 scene 时加进本元组即可。
NOTICE_SCENES: tuple[str, ...] = (AdminNotification.SCENE_MILESTONE_ACHIEVED,)

#: **人工销项类场景**（2026-10-14 第六十轮新增）：事项**没有可推导显示态的业务单据**或
#: 推导依据（报损赔偿要等馆员线下定赔、资金异常要人工核查、活动订单作废要知会收银台），
#: 故生成即「待处理」，馆员跟进完后**手动标记已处理**收口（T20c 覆盖层对 pending 生效）。
#:
#: 为什么必须在这里显式列举：`resolve_many` 原先**只按 `ref_type` 分派**，这三类
#: （2026-10-12 拍板 P4/P5/P10 新增）的 ref_type 分别撞上了别人的分支——
#: `copy_damage_compensation`/`payment_anomaly` 用 `REF_CHILD` → 落进「入会跟进」推导
#: （孩子是有效会员就显示"已审结"，赔偿待办等于挂不上墙）；`activity_order_voided` 用
#: `REF_ACTIVITY` → 落进「活动批量退款」推导（活动已取消、无退款单 → 立即"已审结"）。
#: 且 `payment_anomaly` 的 ref_id 是**订单号 `DMK…`**（非数字），流经 `_child_states` 的
#: `int()` 会抛 ValueError → **整张待办墙 500**（超管视角 `/to-do-counts` 与 `/inbox` 同挂）。
#: 场景一级判定放在 ref_type 分派之前，两个症状一并消除。
MANUAL_PENDING_SCENES: dict[str, str] = {
    AdminNotification.SCENE_COPY_DAMAGE_COMPENSATION: "赔偿待处理",
    AdminNotification.SCENE_PAYMENT_ANOMALY: "资金异常·待核查",
    AdminNotification.SCENE_ACTIVITY_ORDER_VOIDED: "已作废·请知会收银台",
}


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
        enroll_states = self._enroll_states(by_type.get("activity_enrollment", []))
        child_states = self._child_states(by_type.get("child", []))
        reservation_states = self._state_map(
            AdminNotification.REF_RESERVATION, by_type, self._reservation_states()
        )
        settle_states = self._deposit_states(
            [n for n in notifications if n.scene == AdminNotification.SCENE_DEPOSIT_SETTLE]
        )

        for n in notifications:
            if n.scene in MANUAL_PENDING_SCENES:
                # 2026-10-14 第六十轮：人工销项类**先于 ref_type 判定**（理由见常量处注释）
                result[n.id] = {
                    "effective_status": ST_PENDING,
                    "status_text": MANUAL_PENDING_SCENES[n.scene],
                }
            elif n.scene == AdminNotification.SCENE_MILESTONE_ACHIEVED:
                # G9（2026-10-13 第五十五轮）：里程碑达成是**知会**（线下表扬/朋友圈/颁奖候选），
                # 没有"待审"状态机 → 固定显示态、不计入待办数（PRD §7.4 口径）。
                result[n.id] = {"effective_status": ST_DONE, "status_text": "已达成·可线下表扬"}
            elif n.scene == AdminNotification.SCENE_PENDING_EVALUATION:
                # G12（2026-10-14 第五十九批）：待评估跟进——仍处待评估 → 待处理；
                # 馆员处理（转正/退会/过期）后**自动审结**，不需要人工销项。
                result[n.id] = self._decide_pending_evaluation(child_states.get(n.ref_id))
            elif n.scene == AdminNotification.SCENE_DEPOSIT_SETTLE:
                # G1（2026-10-14 第五十九批)：待结清跟进——仍有未结清余额 → 待处理；
                # 补缴结清/押金账户消失 → 自动审结。
                result[n.id] = self._decide_deposit_settle(settle_states.get(n.ref_id))
            elif n.ref_type == "refund_request":
                status = refund_states.get(n.ref_id)
                if n.scene == AdminNotification.SCENE_REFUND_EXECUTE_FAILED:
                    result[n.id] = self._decide_execute_failed(status)
                else:
                    result[n.id] = self._decide_refund(status)
            elif n.ref_type == "withdrawal_request":
                st = withdrawal_states.get(n.ref_id)
                if st is None:
                    result[n.id] = self._decide_withdrawal(None)
                else:
                    status, source = st
                    result[n.id] = self._decide_withdrawal(status, source)
            elif n.ref_type == "transfer":
                result[n.id] = self._decide_transfer(transfer_states.get(n.ref_id))
            elif n.ref_type == "activity":
                result[n.id] = self._decide_activity(activity_states.get(n.ref_id, False))
            elif n.ref_type == "child":
                # R-313：入会跟进——按孩子会员状态实时推导（入会即自动审结）
                result[n.id] = self._decide_member_followup(child_states.get(n.ref_id))
            elif n.ref_type == AdminNotification.REF_RESERVATION:
                # G5（2026-10-14 第六十轮）：预约异常待跟进——预约仍在 exception → 待处理；
                # 单据推进（ended/消失）或馆员手动标记 → 自动审结。
                result[n.id] = self._decide_reservation(reservation_states.get(n.ref_id))
            elif n.ref_type == "activity_enrollment":
                # T6：报名待确认——pending_payment→待处理；enrolled/checked_in/
                # refund_pending→已审结（收款/流转完成）；cancelled→已失效
                result[n.id] = self._decide_enroll(enroll_states.get(n.ref_id))
            else:
                result[n.id] = {"effective_status": ST_DONE, "status_text": TEXT_DONE}
            # T20c（#5）：人工标记意图优先于机器推导——但仅覆盖"待处理"推导。
            # mark_handled（家长撤销/审核终态 L2 回写）也写 handled_at，若无条件
            # 前置会把"已失效·家长已撤销"等终态推导错误覆盖为手动标记；
            # 故只在机器推导为 pending 且有人工标记时覆盖（业务单据真实推进
            # 不受影响——通知只是感知层）。
            if n.handled_at is not None and result[n.id]["effective_status"] == ST_PENDING:
                result[n.id] = {
                    "effective_status": ST_DONE,
                    "status_text": "已处理·手动标记",
                }
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
    def _decide_withdrawal(status: str | None, source: str | None = None) -> dict:
        # T20d（#6）：联动退会单（退款/转让驱动，source != normal）在活跃态显示
        # 灰态"联动处理中"——运营无需操作（execute 成功→withdrawn→completed 链
        # 已有）；不入 pending 计数；直接退会单（normal）保持待处理+操作按钮
        if (source or "normal") != "normal" and status in (
            "applying",
            "pending_settle",
            "refunding",
        ):
            return {
                "effective_status": ST_DONE,
                "status_text": "联动处理中·随退款自动推进",
                "linkage": True,
            }
        if status is None:
            return {"effective_status": ST_DONE, "status_text": TEXT_DONE, "linkage": False}
        if status == "applying":
            return {"effective_status": ST_PENDING, "status_text": TEXT_PENDING, "linkage": False}
        if status == "cancelled":
            return {
                "effective_status": ST_INVALID,
                "status_text": TEXT_INVALID_CANCELLED,
                "linkage": False,
            }
        return {"effective_status": ST_DONE, "status_text": TEXT_DONE, "linkage": False}

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
    def _decide_pending_evaluation(member_status: str | None) -> dict:
        """G12：待评估跟进显示态——仍待评估 → 待处理；其余（转正/退会/过期/清理）→ 已审结。

        孩子被物理清理等极端情况（status 为 None）→ 已审结兜底（不挂待办墙）。
        """
        from backend.domain.identity.models import Child

        if member_status == Child.MEMBER_PENDING_EVALUATION:
            return {"effective_status": ST_PENDING, "status_text": TEXT_PENDING}
        return {"effective_status": ST_DONE, "status_text": TEXT_DONE}

    @staticmethod
    def _decide_deposit_settle(unpaid_balance) -> dict:
        """G1：待结清跟进显示态——仍有未结清余额 → 待处理；结清/查无押金账户 → 已审结。"""
        if unpaid_balance is None:
            return {"effective_status": ST_DONE, "status_text": TEXT_DONE}
        if unpaid_balance > 0:
            return {"effective_status": ST_PENDING, "status_text": TEXT_PENDING}
        return {"effective_status": ST_DONE, "status_text": TEXT_DONE}

    @staticmethod
    def _decide_reservation(status: str | None) -> dict:
        """G5：预约异常待跟进显示态——仍是 `exception` → 待处理；单据推进/消失 → 已审结。

        `exception` 是预约的终态（没有代码路径把它改回去），所以这条待办正常就靠馆员
        线下跟进后**手动标记已处理**收口；预约行被软删/物理清理等极端情况按已审结兜底。
        """
        from backend.domain.reading.models import Reservation

        if status is None:
            return {"effective_status": ST_DONE, "status_text": TEXT_DONE}
        if status == Reservation.STATUS_EXCEPTION:
            return {"effective_status": ST_PENDING, "status_text": TEXT_PENDING}
        return {"effective_status": ST_DONE, "status_text": TEXT_DONE}

    def _reservation_states(self):
        """{预约id: status}（ref_id=reservation，批量查，零 N+1）。"""
        from backend.domain.reading.models import Reservation

        def q(ids: list[str]) -> dict[str, str]:
            rows = self.db.query(Reservation.id, Reservation.status).filter(
                Reservation.id.in_([int(i) for i in ids]),
                Reservation.is_deleted == 0,
            )
            return {str(r.id): r.status for r in rows}

        return q

    def _deposit_states(self, notifications: list) -> dict[str, object]:
        """{child_id: 待结清余额}（ref_id=child，批量查押金表，零 N+1）。"""
        if not notifications:
            return {}
        from backend.domain.billing.models import Deposit

        ids = [int(n.ref_id) for n in notifications]
        rows = self.db.query(Deposit.child_id, Deposit.unpaid_balance).filter(
            Deposit.child_id.in_(ids), Deposit.is_deleted == 0
        )
        return {str(c): b for c, b in rows}

    @staticmethod
    def _decide_member_followup(member_status: str | None) -> dict:
        """会籍跟进（ref_type=child）显示态：**未入会 / 已过期 → 待处理；成为有效会员 → 自动审结**。

        - 未入会（none）→ 「入会跟进」待办（R-313 原有口径）；
        - 已过期（expired，含"formal 但到期日已过"的读时兜底态）→ 「续费跟进」待办
          （PRD §3.7，2026-10-14 G11）。
        两者共用同一 scene，靠通知标题区分；家长一旦入会/续费即自动审结，无需人工销项。

        孩子被物理清理等极端情况 → 已审结兜底（不挂待办墙，与其它场景同款防御）。"""
        from backend.domain.identity.models import Child

        if member_status is None:
            return {"effective_status": ST_DONE, "status_text": TEXT_DONE}
        if member_status in (Child.MEMBER_NONE, Child.MEMBER_EXPIRED):
            return {"effective_status": ST_PENDING, "status_text": TEXT_PENDING}
        return {"effective_status": ST_DONE, "status_text": TEXT_DONE}

    def _scene_pending_count(self, scene: str) -> int:
        """某场景下**显示态为待处理**的条数（与列表同源 resolver，禁各算一遍）。"""
        rows = (
            self.db.query(AdminNotification)
            .filter(AdminNotification.scene == scene, AdminNotification.is_deleted == 0)
            .all()
        )
        resolved = self.resolve_many(rows)
        return sum(1 for n in rows if resolved[n.id]["effective_status"] == ST_PENDING)

    def _child_states(self, notifications: list) -> dict[str, str]:
        """{child_id: **有效**会员状态}（ref_type=child 批量查，零 N+1）。

        取的是**有效状态**（不是库里的原始 status）：`formal` 但到期日已过 → 归一为 `expired`，
        与 `Child.is_expired_member` 的读时兜底同口径。为什么必须归一：过期放行的「续费跟进」
        待办生成在**任务落库之前**（馆员当场放行那一刻），若只看原始 status（还是 formal），
        待办会立刻被判成"已审结"而挂不上墙——跟进就此丢失。
        """
        if not notifications:
            return {}
        from datetime import date

        from backend.domain.identity.models import Child

        # 2026-10-14 第六十轮：**非数字 ref_id 必须跳过**——`ref_type=child` 是张通用标签，
        # 挂在这上面的不都是孩子 id：资金异常待办挂的是**订单号 `DMK…`**、报损待办在"未在借"
        # 时挂的是**副本 id**（与孩子 id 同域，错查会读到别人的会员状态）。
        # 原先裸 `int(n.ref_id)` 遇订单号直接 ValueError → 超管的待办墙与计数**整页 500**。
        # 这些场景的显示态已由 `MANUAL_PENDING_SCENES` 在进入本函数前定好，这里只需不炸。
        ids = [int(n.ref_id) for n in notifications if str(n.ref_id).isdigit()]
        rows = self.db.query(Child.id, Child.member_status, Child.member_expire).filter(
            Child.id.in_(ids), Child.is_deleted == 0
        )
        today = date.today()
        return {
            str(i): (
                Child.MEMBER_EXPIRED if s == Child.MEMBER_FORMAL and exp and exp < today else s
            )
            for i, s, exp in rows
        }

    @staticmethod
    def _decide_enroll(status: str | None) -> dict:
        """T6：activity_enroll_manual 显示态（enrollment 状态实时推导）。"""
        if status is None:
            return {"effective_status": ST_DONE, "status_text": TEXT_DONE}
        if status == ActivityEnrollment.STATUS_PENDING_PAYMENT:
            return {"effective_status": ST_PENDING, "status_text": TEXT_PENDING}
        if status == ActivityEnrollment.STATUS_CANCELLED:
            return {"effective_status": ST_INVALID, "status_text": TEXT_INVALID_CANCELLED}
        return {"effective_status": ST_DONE, "status_text": TEXT_DONE}

    def _enroll_states(self, notifications: list) -> dict[str, str]:
        """T6：{enrollment_id: status}（ref_type=activity_enrollment 批量查）。"""
        if not notifications:
            return {}
        ids = [int(n.ref_id) for n in notifications]
        rows = self.db.query(ActivityEnrollment.id, ActivityEnrollment.status).filter(
            ActivityEnrollment.id.in_(ids),
            ActivityEnrollment.is_deleted == 0,
        )
        return {str(i): s for i, s in rows}

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

        def q(ids: list[str]) -> dict[str, tuple[str, str]]:
            rows = self.db.query(
                WithdrawalRequest.id, WithdrawalRequest.status, WithdrawalRequest.source
            ).filter(
                WithdrawalRequest.id.in_([int(i) for i in ids]),
                WithdrawalRequest.is_deleted == 0,
            )
            return {str(r.id): (r.status, r.source) for r in rows}

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
        G9（2026-10-13）：知会类场景（`NOTICE_SCENES`）**不进默认名单**——它们没有待审状态、
        没有可处理动作，只有显式按 `scene` 筛选时才列出（前端"里程碑知会"视图走这条）。
        """
        # T20g（#8 产品决策）：staff 可见只读——返回全量数据 + read_only 标记
        # （看记录+超管处理结果；handle 端点权限矩阵不动仍仅超管；todo-counts 对
        # staff 保持现状只含 member.manage 相关——徽标语义是"你能处理的"）

        q = self.db.query(AdminNotification).filter(AdminNotification.is_deleted == 0)
        if scene:
            q = q.filter(AdminNotification.scene == scene)
        else:
            # G9：待办墙默认名单 = **待审事项**。知会类（里程碑达成…）有独立视图，
            # 不混进来（否则"已处理"里全是没人处理过的知会，看着乱且误导）。
            q = q.filter(AdminNotification.scene.notin_(NOTICE_SCENES))
        if keyword:
            like = f"%{escape_like(keyword)}%"
            q = q.filter(
                (AdminNotification.applicant_name.like(like, escape="\\"))
                | (AdminNotification.content.like(like, escape="\\"))
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
                "linkage": eff.get("linkage", False),
            }
            for n, eff in page_rows
        ]
        return {
            "items": items,
            "total": total,
            "pending_count": pending_count,
            "read_only": not viewer_is_super,
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
            # 2026-10-12 拍板三类（活动订单作废 / 副本报损赔偿 / 资金异常）——原先缺这三个键，
            # 结果既没被 scene_key 计数落桶、也没进 admin_total（**待办墙有、徽标没有**）。
            # 2026-10-14 第六十轮补齐：口径 = 与收件箱 pending_count 严格一致（同 resolver）。
            "activity_orders_voided": 0,
            "copy_damage_pending": 0,
            "payment_anomaly": 0,
            "order_pending_manual": 0,
            "admin_total": 0,
            # T1（计数同源第 6 案）：家长通知未读全量数——胶囊兜底数据源，
            # 与视角无关（家长 tab loadParent 的筛选口径落地后本地覆盖，双源同值）
            "parent_unread": 0,
            # T6：活动报名待确认（R10 裁定后 admin_total 也含——细分值仍供活动徽标）
            "activity_enroll_pending": 0,
            # R-313：入会跟进待办（member.manage 角色可见可处理；计入 admin_total）
            "member_follow_up": 0,
            # G5（2026-10-14 第六十轮）：预约异常待跟进（副本转维护/遗失且无副本可换）——计入 admin_total
            "reservation_exception": 0,
            # P2-28（2026-10-17 第六十九批）：审核超时提醒（退款/退会）——审计类，仅超管、计入 admin_total
            "refund_review_timeout": 0,
            "withdrawal_review_timeout": 0,
            # WM14-A：阅读圈今日新帖未馆长赞数（冷启动巡场动线——单独口径
            # 不进 admin_total，侧边栏阅读圈徽标+页顶胶囊共用同一源）
            "circle_unliked": 0,
        }
        # 家长未读数对所有能进通知中心的角色开放（导航感知，不挂权限分支）
        from backend.common.notification_models import Notification

        counts["parent_unread"] = (
            self.db.query(func.count(Notification.id))
            .filter(Notification.read_at.is_(None), Notification.is_deleted == 0)
            .scalar()
            or 0
        )
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
                # 2026-10-12：三类新待办（活动订单作废 / 副本报损赔偿 / 资金异常）
                AdminNotification.SCENE_ACTIVITY_ORDER_VOIDED: "activity_orders_voided",
                AdminNotification.SCENE_COPY_DAMAGE_COMPENSATION: "copy_damage_pending",
                AdminNotification.SCENE_PAYMENT_ANOMALY: "payment_anomaly",
                # G5（2026-10-14 第六十轮）：预约异常待跟进
                AdminNotification.SCENE_RESERVATION_EXCEPTION: "reservation_exception",
                # P2-28（2026-10-17 第六十九批）：审核超时提醒（退款/退会）——同属"审计类仅超管"，
                # 计数进 admin_total（否则"待办墙有、徽标没有"，P1-10 假绿家族）。
                AdminNotification.SCENE_REFUND_REVIEW_TIMEOUT: "refund_review_timeout",
                AdminNotification.SCENE_WITHDRAWAL_REVIEW_TIMEOUT: "withdrawal_review_timeout",
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
                    # 2026-10-12 三类 + 2026-10-14 G5 预约异常，均与收件箱 pending_count 同源
                    "activity_orders_voided",
                    "copy_damage_pending",
                    "payment_anomaly",
                    "reservation_exception",
                    # P2-28（2026-10-17 第六十九批）：审核超时提醒两条
                    "refund_review_timeout",
                    "withdrawal_review_timeout",
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
            # T6：活动报名待确认（与收件箱同 resolver 口径：显示态 pending 数；
            # 手动标记 handled_at 覆盖同款生效）
            enroll_rows = (
                self.db.query(AdminNotification)
                .filter(
                    AdminNotification.scene == AdminNotification.SCENE_ACTIVITY_ENROLL_MANUAL,
                    AdminNotification.is_deleted == 0,
                )
                .all()
            )
            # P2-2（2026-10-11 审查）：**整批 resolve 一次**——原先逐行 `resolve_many([n])`
            # 每次发 3~4 条 IN 查询（行数越多越慢），与列表路径同款"一次算全表"。
            resolved_enroll = self.resolve_many(enroll_rows)
            counts["activity_enroll_pending"] = sum(
                1 for n in enroll_rows if resolved_enroll[n.id]["effective_status"] == ST_PENDING
            )
            # R10（计数同源第 7 案，20260907 用户裁定）：活动报名待确认统一进
            # admin_total——运营视角一个数=全部要干的活（"审核五类"历史口径
            # 废弃；T6 旧裁定"单独口径不进 admin_total"推翻）；tab 数字
            # （list_inbox.pending_count 全场景）与徽标自此严格同源
            counts["admin_total"] += counts["activity_enroll_pending"]
            # R-313：入会跟进（同一 resolver 口径：显示态 pending 才计入；
            # 孩子入会/被清理后自动归零）
            followup_rows = (
                self.db.query(AdminNotification)
                .filter(
                    AdminNotification.scene == AdminNotification.SCENE_MEMBER_FOLLOW_UP,
                    AdminNotification.is_deleted == 0,
                )
                .all()
            )
            resolved_followup = self.resolve_many(followup_rows)
            counts["member_follow_up"] = sum(
                1
                for n in followup_rows
                if resolved_followup[n.id]["effective_status"] == ST_PENDING
            )
            counts["admin_total"] += counts["member_follow_up"]
            # G12/G1（2026-10-14 第五十九批）：待评估跟进 / 待结清跟进——同一 resolver 口径
            # （显示态 pending 才计入；馆员处理完自动归零），进 admin_total（运营视角一个数）。
            counts["pending_evaluation_followup"] = self._scene_pending_count(
                AdminNotification.SCENE_PENDING_EVALUATION
            )
            counts["deposit_settle_followup"] = self._scene_pending_count(
                AdminNotification.SCENE_DEPOSIT_SETTLE
            )
            counts["admin_total"] += (
                counts["pending_evaluation_followup"] + counts["deposit_settle_followup"]
            )
            # WM14-A：阅读圈未赞数（member.manage 角色可赞——徽标语义是"你能处理的"）
            from backend.domain.reading_circle.admin_service import AdminCircleService

            counts["circle_unliked"] = AdminCircleService(self.db).unliked_count()
        return counts
