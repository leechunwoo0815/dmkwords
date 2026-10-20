"""activity_enrollments 活跃报名条件唯一约束（P2-9，2026-10-20 第九十三批）

背景（第八轮全量审查 P2-9）：索引 `uq_enroll_active_child` **名字像唯一索引、实际
`unique=False`**（建表迁移 `c9d3a6b8e2f1` 写死了非唯一）。PRD §9.2「同一活动同一孩子
只能有一个有效报名」因此全靠应用层"活动行锁 + 先查后插"——漏锁的写口（管理端造活动单
不在活动行锁内）并发即可造出两条活跃报名，并可叠出超卖。

MySQL 没有部分索引（`WHERE` 条件唯一），故用**生成列 + 唯一索引**做条件唯一：
活跃四态（pending_payment / enrolled / checked_in / refund_pending）且未软删 → 键 =
`活动id-孩子id`；其余（cancelled / refunded / 软删）→ NULL（唯一索引不比较 NULL）。
口径与 PRD §9.2 增补一致：**取消/退款后同一孩子可以重新报名**（新券码，旧券作废）。

本迁移三步：
  1. **存量去重**（幂等）：同一 (activity_id, child_id) 若有多条活跃报名，保留**最早一条**
     （id 最小），其余置 `cancelled` + `cancel_reason` 留痕——只改状态，**不物理删除**
     （宪法：业务主数据禁止物理删除）。开发库实测 0 条重复，此处是生产兜底。
  2. 删掉名不副实的旧非唯一索引（`activity_id` 单列索引仍在，查询覆盖不受影响）。
  3. 加生成列 `active_uniq_key` + 同名的**真唯一**索引 `uq_enroll_active_child`。

downgrade 只回滚 schema（旧非唯一索引还原）；**去重不回滚**（被取消的历史重复报名不复活）。

Revision ID: a1b8c4d9e2f3
Revises: 0f345c121f66
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "a1b8c4d9e2f3"
down_revision: Union[str, Sequence[str], None] = "0f345c121f66"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

#: 与 `backend/domain/activity/models.py:ACTIVE_UNIQ_KEY_SQL` 逐字一致（两侧同步，
#: 单元用例从 information_schema 读回真实表达式与 `ACTIVE_STATUSES` 比对防漂移）。
ACTIVE_UNIQ_KEY_SQL = (
    "IF(is_deleted = 0 AND status IN "
    "('pending_payment','enrolled','checked_in','refund_pending'),"
    " CONCAT(activity_id, '-', child_id), NULL)"
)

_ACTIVE_LIST_SQL = "'pending_payment','enrolled','checked_in','refund_pending'"

_DEDUPE_SQL = f"""
UPDATE activity_enrollments AS e
JOIN (
    SELECT activity_id, child_id, MIN(id) AS keep_id
    FROM activity_enrollments
    WHERE is_deleted = 0 AND status IN ({_ACTIVE_LIST_SQL})
    GROUP BY activity_id, child_id
    HAVING COUNT(*) > 1
) AS d ON d.activity_id = e.activity_id AND d.child_id = e.child_id
SET e.status = 'cancelled',
    e.cancel_reason = '历史重复报名（P2-9 唯一约束去重，保留最早一条）',
    e.update_time = NOW()
WHERE e.is_deleted = 0 AND e.status IN ({_ACTIVE_LIST_SQL}) AND e.id <> d.keep_id
"""

_DUP_CHECK_SQL = f"""
SELECT activity_id, child_id, COUNT(*) AS n
FROM activity_enrollments
WHERE is_deleted = 0 AND status IN ({_ACTIVE_LIST_SQL})
GROUP BY activity_id, child_id
HAVING COUNT(*) > 1
"""


def upgrade() -> None:
    conn = op.get_bind()

    # ① 存量去重（幂等：无重复时 0 行受影响）
    deduped = conn.execute(sa.text(_DEDUPE_SQL)).rowcount
    if deduped:
        print(f"[p2-9] 存量重复报名去重：{deduped} 条置 cancelled（保留每组最早一条）")

    # ② 建唯一索引前再确认无重复——有残留就中止（不建半截约束）
    left = conn.execute(sa.text(_DUP_CHECK_SQL)).fetchall()
    if left:
        raise RuntimeError(f"[p2-9] 去重后仍有重复活跃报名，中止迁移：{left}")

    # ③ 旧索引名不副实（unique=False）→ 删除；新约束复用同名，让"名字=事实"
    op.drop_index("uq_enroll_active_child", table_name="activity_enrollments")
    op.add_column(
        "activity_enrollments",
        sa.Column(
            "active_uniq_key",
            sa.String(length=40),
            sa.Computed(ACTIVE_UNIQ_KEY_SQL, persisted=True),
            nullable=True,
            comment="活跃报名唯一键（生成列：活跃态=活动-孩子，其余 NULL）",
        ),
    )
    op.create_index(
        "uq_enroll_active_child",
        "activity_enrollments",
        ["active_uniq_key"],
        unique=True,
    )


def downgrade() -> None:
    op.drop_index("uq_enroll_active_child", table_name="activity_enrollments")
    op.drop_column("activity_enrollments", "active_uniq_key")
    # 还原旧形态（非唯一复合索引）——与 `c9d3a6b8e2f1` 一致
    op.create_index(
        "uq_enroll_active_child",
        "activity_enrollments",
        ["activity_id", "child_id"],
        unique=False,
    )
