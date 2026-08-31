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

import json
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

    def mark_handled(self, *, ref_type: str, ref_id: str | int, admin=None, note: str = "") -> int:
        """审计回写：handled_at/handled_by。幂等：已处理跳过（保留首次审计）。返回更新条数。

        admin=None 的路径（家长撤销/超时自动失效）不写 handled_by，note 记录来源。
        """
        values: dict = {"handled_at": datetime.now()}
        if admin is not None:
            values["handled_by"] = admin.id
        if note:
            values["extra"] = json.dumps({"method": note}, ensure_ascii=False)
        updated = (
            self.db.query(AdminNotification)
            .filter(
                AdminNotification.ref_type == ref_type,
                AdminNotification.ref_id == str(ref_id),
                AdminNotification.handled_at.is_(None),
                AdminNotification.is_deleted == 0,
            )
            .update(values, synchronize_session=False)
        )
        return updated
