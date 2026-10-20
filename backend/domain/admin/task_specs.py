# backend/domain/admin/task_specs.py — 任务看板数据组装（从 admin/service.py 抽出）
"""为什么单开：`admin/service.py` 触及架构关 god-file 上限（>800 行）。

任务列表 + 最近一次运行 + **连续失败次数**（2026-10-12 拍板 P10：一次成功不该把连续失败洗白）。
"""

from __future__ import annotations

from sqlalchemy.orm import Session

from backend.common.exceptions import ForbiddenError
from backend.common.notification_models import TaskRunLog  # noqa: F401 — 类型/常量引用
from backend.domain.admin.models import AdminUser

#: 每任务回看的运行记录条数（算"连续失败"用）。
#: 第八轮审查 P1-12：原先取**全局最新 500 条**再按任务分组——三个 300 秒任务每天约 864 行、
#: 全部任务约 1400 行/天 → 500 行只覆盖约 8.5 小时，`03:30/04:00/08:00` 的 cron 任务
#: 当天下午就滑出窗口 → 看板显示"从未运行"、连续失败数失真（运营据此误判"调度器停了"）。
#: 现在**按任务各取最新 N 条**（`task_name` 有索引，21 次单查），窗口与"全局写入量"解耦。
_PER_TASK_HISTORY = 50


def build_specs(db: Session) -> list[dict]:
    """任务清单 + 每任务最近一次运行 + 连续失败次数。"""
    from backend.tasks.registry import list_task_specs

    specs = list_task_specs()
    for s in specs:
        # 每个任务的最新一条运行记录（Q2 裁决：last_run join；空值前端显示"从未运行"）
        rows = (
            db.query(TaskRunLog)
            .filter(TaskRunLog.is_deleted == 0, TaskRunLog.task_name == s["name"])
            .order_by(TaskRunLog.id.desc())
            .limit(_PER_TASK_HISTORY)
            .all()
        )
        last = rows[0] if rows else None
        s["last_run"] = (
            {
                "status": last.status,
                "processed": last.processed,
                "error": last.error,
                "started_at": last.started_at.strftime("%Y-%m-%d %H:%M:%S")
                if last.started_at
                else "",
            }
            if last
            else None
        )
        # 2026-10-12 拍板（P10）：**连续失败要在看板标红**——原先只有"最后一次状态"，
        # 一次成功就把连续失败盖掉了（失败三次最后一次碰巧成功 = 看板全绿）。
        streak = 0
        for r in rows:  # 已按 id desc（最新在前）
            if r.status == TaskRunLog.STATUS_FAILED:
                streak += 1
            else:
                break
        s["consecutive_failures"] = streak
    return specs


class TaskAdminService:
    """WM11 定时任务看板（任务清单/运行记录/手动触发）。"""

    def __init__(self, db: Session):
        self.db = db

    def specs(self, admin=None) -> list[dict]:
        """任务清单 + 最近一次运行 + 连续失败次数（组装逻辑见 task_specs.build_specs）。

        P2-21（2026-10-17 第六十九批·大审查）：传入 `admin` 时逐条带 `manual_allowed`
        ——任务看板据此**禁用**无权触发的那几个按钮（此前 UI 对专员全量露出，点了必 403）。
        判据与 `run()` 强制点是**同一个方法**（同一口径只有一份来源，红线 154）。
        """
        from backend.domain.admin.task_specs import build_specs

        items = build_specs(self.db)
        if admin is not None:
            for it in items:
                it["manual_allowed"] = self.manual_trigger_allowed(it.get("group", ""), admin.role)
        return items

    def recent_runs(self, limit: int = 20) -> list[dict]:
        from backend.common.notification_models import TaskRunLog

        rows = (
            self.db.query(TaskRunLog)
            .filter(TaskRunLog.is_deleted == 0)
            .order_by(TaskRunLog.id.desc())
            .limit(limit)
            .all()
        )
        return [
            {
                "task_name": r.task_name,
                "status": r.status,
                "processed": r.processed,
                "error": r.error,
                "started_at": r.started_at.strftime("%Y-%m-%d %H:%M:%S") if r.started_at else "",
                "finished_at": r.finished_at.strftime("%Y-%m-%d %H:%M:%S") if r.finished_at else "",
            }
            for r in rows
        ]

    #: 手动触发**仅超管**的任务分组——资金（动钱：订单超时取消/转让超时）与
    #: 系统（动磁盘：媒体体检含回收站到期清除）。其余分组（会员/借阅/活动/阅读圈）
    #: 专员可触发（日常运维需要）。审查 P1-5：此前一律 dashboard.view，专员可间接触发删文件。
    SUPERADMIN_ONLY_TASK_GROUPS = ("资金", "系统")

    @classmethod
    def manual_trigger_allowed(cls, group: str, role: str) -> bool:
        """手动触发判据（**唯一实现**）：`run()` 的强制点与任务看板的提示共用它。

        P2-21（2026-10-17 第六十九批）：此前判据只在服务层 `run()` 里，看板对专员全量
        露出按钮 → 点了必 403（"能点不能用"）。同一规则两处各写一遍必然漂移，故收成一处。
        """
        return not (group in cls.SUPERADMIN_ONLY_TASK_GROUPS and role != AdminUser.ROLE_SUPER_ADMIN)

    def run(self, task_name: str, manual: bool = False, admin=None) -> dict:
        from backend.tasks.registry import TASKS, run_task

        if manual and admin is not None:
            spec = TASKS.get(task_name)
            if spec and not self.manual_trigger_allowed(spec.group, admin.role):
                raise ForbiddenError(f"「{spec.display_name}」仅超级管理员可手动触发")
        return run_task(task_name, manual=manual, admin=admin)
