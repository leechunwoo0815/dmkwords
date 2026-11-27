# backend/domain/identity/deposit_refund_guards.py — 押金退款的余额闸（2026-10-09 资金 P0-D）
"""**押金是担保账户，不是普通订单**：退款绝不允许把 `available_amount` 打成负数。

为什么单独成模块（两重原因，与 `refund_rules.py` 同一处置）：
① 这两处规则（执行前复核 / 终态超额留痕）语义自洽，与 `wm10_service` 的流程编排无关；
② `wm10_service.py` 触发架构关"god file 禁止"（>800 行）——服务类只留编排。

口径（不可回退，缺陷背景见 docs/09 第三十八轮）：
  执行前 —— 可用余额 < 本次申请金额 → **拒执行**（422），状态不流转（人工处理后仍可执行）；
           复核必须在**提交网关之前**：线上单一旦提交，钱就出去了，那时再发现晚了。
  终态   —— 真出现超额（旁路创建/历史脏数据）→ **不掩盖**：台账照实记负（损失的真实表示），
           另发一条异常审计 `deposit.refund_overdraft` 让对账与人工能逮到。
"""

from __future__ import annotations

from sqlalchemy.orm import Session

from backend.common.exceptions import ValidationError


def lock_deposit(db: Session, deposit_id: int):
    """锁押金行读余额：复核与扣减必须同一把锁，否则"两个执行各自读到 1200"照样穿透。"""
    from backend.domain.billing.models import Deposit

    return (
        db.query(Deposit)
        .filter(Deposit.id == deposit_id)
        .with_for_update()
        .populate_existing()
        .first()
    )


def assert_deposit_refund_executable(db: Session, req) -> None:
    """押金退款执行前**累计**复核（调用点：`RefundService.execute`，提交网关之前）。

    第八轮审查 P1-2（2026-10-19）补第二重判据。两重：
      ① **点余额**：本次 ≤ 当前可用余额（原 P0-D 闸，保留）；
      ② **网关在途累计**：本次 + 该押金**其它 `PROCESSING`** 退款单合计 ≤ 当前可用余额。

    为什么必须有 ②（实测复现）：`available_amount` 到**终态**才扣减（`wm10_service._finalize_refund`），
    而线上退款在 Phase 1 就把单子提交 `PROCESSING` 并交给网关——此时钱已出、余额未动。原闸只比
    "点余额"，于是第二张同额单**原样放行**（第八轮探针：余额 1200 + 一张 PROCESSING 1200，
    第二张 1200 过闸）→ 押金余额可被打成负数。订单侧早有同款累计闸
    （`assert_order_refund_executable`，2026-10-12），押金侧一直缺。

    **2026-11-27（B1-1 同族收口）**：统计原来是普通读（REPEATABLE READ 旧快照），改成**锁定读**
    （逐行 `FOR UPDATE` 后求和）。押金侧本来就有押金行锁串行化，锁定读是补"锁内读到的必须是
    最新已提交状态"这一条：否则拿旧快照照样能把两笔在途都判成"对方不存在"。

    **为什么只数 `PROCESSING`**：三种状态的钱在哪里不同——
      · `PENDING` / `APPROVED` 只是**申请**，钱没动，也不占余额（两笔都批准、只执行其中一笔是
        正常运营动作；把它们算进来会把第一笔也拦死，P0-D 老用例实测即如此）；
      · `REFUNDED` 的金额在终态已从 `available_amount` 扣掉，再计入会**跨会员周期误伤**
        （重新入会复位押金后，上一周期的历史退款单会把新周期正常退款判成超额——与
        `existing_dep_rr` 的历史判重同一个坑）；
      · `PROCESSING` 恰好是"**钱已交给网关、余额还没扣**"的唯一窗口，也正是重复出款的入口。
    """
    dep = lock_deposit(db, req.deposit_id)
    if dep is None:
        return
    if dep.available_amount < req.amount:
        raise ValidationError(
            f"押金可退金额不足（可退 {dep.available_amount}，本次申请 {req.amount}）——"
            "疑似重复退款单，请人工核对后再执行"
        )
    from decimal import Decimal

    from backend.domain.identity.models import RefundRequest

    in_flight = sum(
        (
            row[0]
            for row in db.query(RefundRequest.amount)
            .filter(
                RefundRequest.deposit_id == req.deposit_id,
                RefundRequest.id != req.id,
                RefundRequest.is_deleted == 0,
                RefundRequest.status == RefundRequest.STATUS_PROCESSING,
            )
            # 裸列锁定读（不加载实体，populate_existing 语义上是无操作，但架构关要求显式声明——
            # 它同时是"这是锁定读、不是普通读"的标记，防日后被当成普通聚合随手改回去）
            .with_for_update()
            .populate_existing()
            .all()
        ),
        Decimal("0"),
    )
    if in_flight + req.amount > dep.available_amount:
        raise ValidationError(
            f"该押金退款将超额（可用 {dep.available_amount}，"
            f"其它已提交网关的在途 {in_flight}，本次 {req.amount}）"
            "——疑似重复退款单，请人工核对后再执行"
        )


def assert_order_refund_executable(db: Session, req) -> None:
    """订单退款执行前**累计**复核（2026-10-12 审查 P1；调用点：`RefundService.execute`）。

    为什么必须有：`failed` 既不计入"进行中"（家长申请 / 退会结算的 dup 检查只看
    pending/approved/processing），又**可被重试执行**——实测（订单 500）：RR#3 执行判失败 →
    再申请 RR#4 放行 → RR#4 成功 → RR#3 重试成功 → **同一订单出款 1000**。
    押金侧早有同款闸（`assert_deposit_refund_executable`），订单侧一直缺。

    口径：本次金额 + 该订单**已成功/在途**的其它退款单合计 ≤ 订单金额，超出即拒（422，状态不流转）。

    **B1-1（2026-11-27，双审查 P1-1）：这道闸原来自己就有个重复出款的洞。**
    两笔 `failed` 的退款单**同时**重试时：统计刻意不含 `failed`（含了会把合法重试也拦死），
    而普通读吃的是事务开始时的快照 —— 两笔互相看不见对方（对方还没提交），各自算出
    "0 + 本单金额 ≤ 订单金额" → **两笔都放行**，同一订单出款两倍。
    修法不是改统计口径（那样会把"两笔失败、只重试一笔"的合法路径拦死），而是**串行化**：
    调用方（`RefundService.execute`）现在按全局锁序先锁 `Child → Order`，订单行锁就是
    "同一订单退款"的串行化点；本函数在锁内用**锁定读**统计（`FOR UPDATE`，绕过 REPEATABLE READ
    快照，读到的必是最新已提交状态）→ 第二笔进闸时第一笔必然已是 `processing/refunded`，被计入。
    锁序见 `backend/domain/identity/lock_order.py`。
    """
    from decimal import Decimal

    from backend.domain.identity.models import Order, RefundRequest

    order = db.query(Order).filter(Order.id == req.order_id).first()
    if order is None:
        return
    in_flight_or_paid = sum(
        (
            row[0]
            for row in db.query(RefundRequest.amount)
            .filter(
                RefundRequest.order_id == req.order_id,
                RefundRequest.id != req.id,
                RefundRequest.is_deleted == 0,
                RefundRequest.status.in_(
                    [
                        RefundRequest.STATUS_PENDING,
                        RefundRequest.STATUS_APPROVED,
                        RefundRequest.STATUS_PROCESSING,
                        RefundRequest.STATUS_REFUNDED,
                    ]
                ),
            )
            # 裸列锁定读（不加载实体，populate_existing 语义上是无操作，但架构关要求显式声明——
            # 它同时是"这是锁定读、不是普通读"的标记，防日后被当成普通聚合随手改回去）
            .with_for_update()
            .populate_existing()
            .all()
        ),
        Decimal("0"),
    )
    total = in_flight_or_paid + req.amount
    if total > order.amount:
        raise ValidationError(
            f"该订单退款将超额（订单 {order.amount}，已退/在途 {in_flight_or_paid}，"
            f"本次 {req.amount}）——疑似重复退款单，请人工核对后再执行"
        )


def publish_deposit_overdraft(db: Session, *, admin, req, dep) -> None:
    """超额执行留痕（台账照实记负 + 异常审计）——正常路径已被执行前复核拦住。"""
    from backend.domain.catalog.audit_events import publish_audit

    publish_audit(
        db,
        admin=admin,
        action="deposit.refund_overdraft",
        target_type="deposit",
        target_id=str(dep.id),
        detail={
            "available_before": str(dep.available_amount),
            "refund_amount": str(req.amount),
            "refund_request_id": req.id,
        },
        reason="押金退款超额（可退金额不足仍执行）——疑似重复退款单，请人工核对",
    )


def reset_deposit_if_active_member(
    db: Session, child_id: int, withdrawal_id: int | None = None
) -> None:
    """押金退款成功时若孩子已重新入会 → 账户复位为「未缴」（2026-10-30 大审查 P1-4）。

    竞态来源：押金退款在途（`refunding`）时孩子重新入会，入会复位只认 `refunded` 会跳过；
    退款随后落 `refunded` 就成了终态——四条缴款入口全拒、借书无担保。

    两条必须同时成立的闸，缺一就会误伤正常退会：
    ① **不属于进行中的退会流程**——正常退会的押金退款终态就是 `refunded`
       （BDD `features/deposit.feature:53` 明确断言），只有退会已结束/无退会关联时才可能存在
       "重新入会后又执行旧押金退款"的时序；
    ② **只清"退空"的账户**（`available_amount == 0`）——部分退（如退 1100 留 100）是合法路径，
       余额必须留在台账上（判据 `test_p0b2_t24_ledger_locks`）。
    """
    from backend.domain.billing.models import Deposit
    from backend.domain.billing.service import DepositService
    from backend.domain.identity.models import Child, WithdrawalRequest

    if withdrawal_id:
        w = db.query(WithdrawalRequest).filter(WithdrawalRequest.id == withdrawal_id).first()
        if w is not None and w.status in (
            WithdrawalRequest.STATUS_APPLYING,
            WithdrawalRequest.STATUS_PENDING_SETTLE,
            WithdrawalRequest.STATUS_REFUNDING,
        ):
            return
    child = db.query(Child).filter(Child.id == child_id).first()
    if child is None or not child.is_active_member:
        return
    dep = db.query(Deposit).filter(Deposit.child_id == child_id, Deposit.is_deleted == 0).first()
    if dep is None or dep.status != Deposit.STATUS_REFUNDED:
        return
    if dep.available_amount != 0:
        return
    DepositService(db).reset_refunded_on_rejoin(child_id)
