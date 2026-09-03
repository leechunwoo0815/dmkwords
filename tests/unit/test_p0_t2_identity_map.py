# tests/unit/test_p0_t2_identity_map.py — P0 第一批 T2（B-16）with_for_update 补 populate_existing
"""identity map 陷阱红测试（参照 reading checkout 顺带-1 探针同款结构）。

现象（B-16）：同 session 前序普通查询已加载实体 → with_for_update 命中
identity map 返回旧实例（锁定读的新行数据被丢弃）→ 状态守卫读旧值。
与 T1（隔离级别）独立：RC 修不了 ORM 层缺陷。

结构（双对照，先证 bug 机制、后锁修复路径）：
- 对照 1（RED 语义）：s1 普通读载 pending → s2 推进 approved 提交 →
  s1 裸 with_for_update（无 populate_existing）→ 返回 identity map 旧实例 pending
  ——证明陷阱真实存在，若无此断言则"不补也能过"的假绿无依据
- 对照 2（GREEN 锚点）：同场景带 .populate_existing() → 读到 approved
  ——修复后路径语义，防回归（参照 checkout 顺带-1）
"""

from decimal import Decimal

from backend.database import get_session
from backend.domain.identity.models import RefundRequest


def _db():
    return get_session()


def test_review_lock_refreshes_identity_map(session_pair):
    s1, s2 = session_pair

    # 造一条 pending 退款申请（直插，无关联订单也不影响锁语义验证）
    with _db() as db:
        rr = RefundRequest(
            kind=RefundRequest.KIND_ORDER,
            child_id=1,
            amount=Decimal("99.00"),
            reason="identity map 探针",
            status=RefundRequest.STATUS_PENDING,
        )
        db.add(rr)
        db.commit()
        rid = rr.id

    # s1 普通读：identity map 载入 pending 旧实例（模拟 review 前段业务普通读）
    stale = s1.query(RefundRequest).filter(RefundRequest.id == rid).first()
    assert stale.status == RefundRequest.STATUS_PENDING

    # s2 推进 approved 并提交（模拟并发审批既成事实）
    s2.query(RefundRequest).filter(RefundRequest.id == rid).with_for_update().first()
    s2.query(RefundRequest).filter(RefundRequest.id == rid).update(
        {"status": RefundRequest.STATUS_APPROVED}
    )
    s2.commit()

    # 对照 1（RED 语义）：裸锁定读命中 identity map 旧值——陷阱机制实锤
    stale_locked = (
        s1.query(RefundRequest).filter(RefundRequest.id == rid).with_for_update().first()
    )
    assert stale_locked.status == RefundRequest.STATUS_PENDING, (
        "identity map 旧值未命中？——场景构造失效，对照 1 无效"
    )

    # 对照 2（GREEN 锚点）：带 populate_existing 强制行数据刷新 → 读到最新已提交
    fresh = (
        s1.query(RefundRequest)
        .filter(RefundRequest.id == rid)
        .with_for_update()
        .populate_existing()
        .first()
    )
    assert fresh.status == RefundRequest.STATUS_APPROVED, (
        f"锁定读未刷新 identity map 旧值——守卫将读 pending 误判（B-16 语义）：{fresh.status}"
    )
    s1.rollback()