"""one active withdrawal per child

B1-2（2026-11-26 双审查 P1-2）：同一孩子原先可以同时存在**两张活跃退会单**
（`applying` / `pending_settle` / `refunding`）——两条建单路径（家长主动申请、会员费退款 R-309
联动）各自的"已有进行中退会"判重都是普通读，并发时互相看不见对方未提交的行。后果不是"多一张单"，
而是**押金退款单各建一笔，押金退两遍**。

MySQL 没有部分索引（`WHERE` 条件唯一），沿用仓内既有做法（`a1b8c4d9e2f3` 活动报名 P2-9）：
**生成列 + 唯一索引**做条件唯一。活跃三态且未软删 → 键 = `child_id`；其余（completed / rejected /
cancelled / 软删）→ NULL（唯一索引不比较 NULL，反复申请不受影响）。

三层防线（本迁移是第三层）：
  1. 全局锁序 `Child → Order → RefundRequest → Withdrawal → Deposit`
     （`backend/domain/identity/lock_order.py`）：两条建单路径都以 Child 行为串行化点；
  2. 应用层冲突 → `ConflictError`（409）；
  3. 本唯一索引：锁被绕过、将来新入口忘了锁，数据库也只允许一张活跃单。

本迁移三步：
  1. **存量去重**（幂等）：同一 child_id 若有多条活跃退会单，保留 id 最小的一条，其余置
     `cancelled` + `review_remark` 留痕——只改状态、不物理删除（宪法：业务主数据禁止物理删除）。
  2. 加 STORED 生成列 `active_uniq_key`（MySQL 需重建表，见文件末"锁表窗口"）。
  3. 建唯一索引 `uq_withdrawal_active_child`。
  4. 建好后打印一次实际表达式，便于与 `backend/domain/identity/models.py:ACTIVE_WITHDRAWAL_KEY_SQL`
     对照（用例 `test_b1_refund_withdrawal_concurrency_20261127.py` 从 information_schema 读回比对）。

downgrade 只回滚 schema；**去重不回滚**（被取消的历史重复单不复活）。

**锁表窗口**：`op.add_column(Computed(..., persisted=True))` 给既有表加 STORED 生成列，MySQL 必须
重建表（COPY / INPLACE+rebuild），期间持元数据锁并阻塞写入。`withdrawal_requests` 是单馆量级
（百行级），实测秒级；线上执行前先 `SHOW PROCESSLIST` 确认无长事务，低峰执行。

Revision ID: e7c4d1a9b3f6
Revises: d5b7c2e8f1a3
Create Date: 2026-11-27 03:10:00.000000

"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "e7c4d1a9b3f6"
down_revision: Union[str, Sequence[str], None] = "d5b7c2e8f1a3"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

#: 与 `backend/domain/identity/models.py:ACTIVE_WITHDRAWAL_KEY_SQL` **逐字一致**（两侧同步）。
ACTIVE_WITHDRAWAL_KEY_SQL = (
    "IF(is_deleted = 0 AND status IN ('applying','pending_settle','refunding'), child_id, NULL)"
)

_ACTIVE_LIST_SQL = "'applying','pending_settle','refunding'"

_DEDUPE_SQL = f"""
UPDATE withdrawal_requests AS w
JOIN (
    SELECT child_id, MIN(id) AS keep_id
    FROM withdrawal_requests
    WHERE is_deleted = 0 AND status IN ({_ACTIVE_LIST_SQL})
    GROUP BY child_id
    HAVING COUNT(*) > 1
) AS d ON d.child_id = w.child_id
SET w.status = 'cancelled',
    w.review_remark = '历史重复退会单（B1-2 唯一约束去重，保留最早一条）',
    w.update_time = NOW()
WHERE w.is_deleted = 0 AND w.status IN ({_ACTIVE_LIST_SQL}) AND w.id <> d.keep_id
"""

_DUP_CHECK_SQL = f"""
SELECT child_id, COUNT(*) AS n
FROM withdrawal_requests
WHERE is_deleted = 0 AND status IN ({_ACTIVE_LIST_SQL})
GROUP BY child_id
HAVING COUNT(*) > 1
"""


def upgrade() -> None:
    conn = op.get_bind()

    # ① 存量去重（幂等：无重复时 0 行受影响）
    deduped = conn.execute(sa.text(_DEDUPE_SQL)).rowcount
    if deduped:
        print(f"[b1-2] 存量重复退会单去重：{deduped} 条置 cancelled（保留每个孩子最早一条）")

    # ② 建约束前再确认无重复——有残留就中止（不建半截约束）
    left = conn.execute(sa.text(_DUP_CHECK_SQL)).fetchall()
    if left:
        raise RuntimeError(f"[b1-2] 去重后仍有重复活跃退会单，中止迁移：{left}")

    # ③ 生成列 + 唯一索引
    op.add_column(
        "withdrawal_requests",
        sa.Column(
            "active_uniq_key",
            sa.Integer(),
            sa.Computed(ACTIVE_WITHDRAWAL_KEY_SQL, persisted=True),
            nullable=True,
            comment="活跃退会唯一键（生成列：活跃态=child_id，其余 NULL）",
        ),
    )
    op.create_index(
        "uq_withdrawal_active_child",
        "withdrawal_requests",
        ["active_uniq_key"],
        unique=True,
    )

    # ④ 如实回读实际表达式（防止迁移与模型两侧写法漂移）
    expr = conn.execute(
        sa.text(
            "SELECT generation_expression FROM information_schema.COLUMNS "
            "WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = 'withdrawal_requests' "
            "AND COLUMN_NAME = 'active_uniq_key'"
        )
    ).scalar()
    print(f"[b1-2] active_uniq_key 实际表达式：{expr}")


def downgrade() -> None:
    op.drop_index("uq_withdrawal_active_child", table_name="withdrawal_requests")
    op.drop_column("withdrawal_requests", "active_uniq_key")
