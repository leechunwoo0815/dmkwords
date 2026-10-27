# backend/domain/identity/router.py — 会员与订单 API
import os
from typing import Any

from fastapi import APIRouter, Depends, File, Form, Query, Request, Response, UploadFile
from pydantic import Field
from sqlalchemy.orm import Session

from backend.common.base_schema import BaseSchema, PaginatedResponse
from backend.common.list_meta import stamp_list_meta
from backend.common.media_paths import is_within
from backend.database import get_db
from backend.domain.identity import refund_queries
from backend.domain.identity.observation_service import ObservationReportService
from backend.domain.identity.schemas import (
    ChildCreateRequest,
    ChildResponse,
    ChildUpdateRequest,
    ChildWithParentResponse,
    OrderConfirmRequest,
    OrderCreateRequest,
    OrderResponse,
    ParentCreateRequest,
    ParentResponse,
    ParentUpdateRequest,
    ParentWithStatsResponse,
    PaymentReconcileListResponse,
    PaymentReconcileRunResponse,
    PaymentSimulateRefundResponse,
    VoucherUploadResponse,
)
from backend.domain.identity.service import ChildService, OrderService, ParentService
from backend.domain.identity.transfer_service import TransferService
from backend.domain.identity.wm10_service import RefundService, WithdrawalService
from backend.middleware.admin_rbac import require_perm, require_super_admin

router = APIRouter(tags=["identity"])


@router.post("/members/parents", response_model=ParentResponse)
def create_parent(
    body: ParentCreateRequest,
    admin: Any = Depends(require_perm("member.manage")),
    db: Session = Depends(get_db),
):
    return ParentResponse.model_validate(ParentService(db).create(admin, body))


@router.get("/members/parents", response_model=list[ParentResponse])
def search_parents(
    response: Response,
    keyword: str | None = Query(None),
    admin: Any = Depends(require_perm("member.manage")),
    db: Session = Depends(get_db),
):
    """家长搜索（W1：建档家长选择器远程搜索，姓名/手机号模糊匹配）。

    P2-44（第九轮 P3 补齐）：服务层默认只回 20 条（选择器够用），但原先**静默截断**——
    同名家长多时运营以为"就这 20 个"。上限经响应头如实告知。
    """
    rows = ParentService(db).search(keyword)
    stamp_list_meta(response, loaded=len(rows), limit=ParentService.SEARCH_LIMIT)
    return [ParentResponse.model_validate(p) for p in rows]


@router.get("/members/parents-page", response_model=PaginatedResponse[ParentWithStatsResponse])
def parents_page(
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    keyword: str | None = Query(None),
    admin: Any = Depends(require_perm("member.manage")),
    db: Session = Depends(get_db),
):
    """家长管理 tab 分页（WM3-B1：children_count / has_orders 守卫标志）。"""
    rows, total = ParentService(db).list_page(page, page_size, keyword)
    items = []
    for p, ccount, has_orders in rows:
        item = ParentWithStatsResponse.model_validate(p)
        item.children_count = ccount
        item.has_orders = has_orders
        items.append(item)
    return PaginatedResponse[ParentWithStatsResponse].create(
        items=items, total=total, page=page, page_size=page_size
    )


@router.patch("/members/parents/{parent_id}", response_model=ParentResponse)
def update_parent(
    parent_id: int,
    body: ParentUpdateRequest,
    admin: Any = Depends(require_perm("member.manage")),
    db: Session = Depends(get_db),
):
    """编辑家长（WM3-B1；订单守卫 409；手机号唯一 422——手机号即登录标识）。"""
    return ParentResponse.model_validate(ParentService(db).update(admin, parent_id, body))


@router.delete("/members/parents/{parent_id}")
def delete_parent(
    parent_id: int,
    admin: Any = Depends(require_perm("member.manage")),
    db: Session = Depends(get_db),
):
    """软删家长+名下孩子（WM3-B1；订单守卫 409）。"""
    ParentService(db).delete(admin, parent_id)
    return {"id": parent_id, "deleted": True}


@router.get("/members/parents/{parent_id}/children", response_model=list[ChildResponse])
def list_children(
    parent_id: int,
    admin: Any = Depends(require_perm("member.manage")),
    db: Session = Depends(get_db),
):
    return [ChildResponse.model_validate(c) for c in ParentService(db).list_children(parent_id)]


@router.post("/members/parents/{parent_id}/children", response_model=ChildResponse)
def create_child(
    parent_id: int,
    body: ChildCreateRequest,
    admin: Any = Depends(require_perm("member.manage")),
    db: Session = Depends(get_db),
):
    return ChildResponse.model_validate(ChildService(db).create(admin, parent_id, body))


@router.get("/members/children", response_model=PaginatedResponse[ChildWithParentResponse])
def list_children_page(
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    keyword: str | None = Query(None),
    status: str | None = Query(None),
    admin: Any = Depends(require_perm("member.manage")),
    db: Session = Depends(get_db),
):
    rows, total = ChildService(db).list_children(page, page_size, keyword, status)
    items = []
    for child, parent_name, parent_phone in rows:
        item = ChildWithParentResponse.model_validate(child)
        item.parent_name = parent_name
        item.parent_phone = parent_phone
        item.has_orders = ChildService.has_orders(db, child.id)
        items.append(item)
    return PaginatedResponse[ChildWithParentResponse].create(
        items=items, total=total, page=page, page_size=page_size
    )


class MemberStatusActionRequest(BaseSchema):
    reason: str = Field("", max_length=200, description="操作原因（留痕）")


@router.post("/members/children/{child_id}/mark-pending-evaluation", response_model=ChildResponse)
def mark_pending_evaluation(
    child_id: int,
    body: MemberStatusActionRequest,
    admin: Any = Depends(require_perm("member.manage")),
    db: Session = Depends(get_db),
):
    """观察期 → 待评估（C13：馆员手动标记；自动转换任务在 WM11）。"""
    return ChildResponse.model_validate(
        ChildService(db).mark_pending_evaluation(admin, child_id, body.reason)
    )


@router.put("/members/children/{child_id}", response_model=ChildResponse)
def update_child(
    child_id: int,
    body: ChildUpdateRequest,
    admin: Any = Depends(require_perm("member.manage")),
    db: Session = Depends(get_db),
):
    """维护孩子资料（C19 + WM3-B1 扩展：姓名/性别/生日全开；AR 只升不降；订单守卫 409）。"""
    # P2-60：`birthday` 显式传 null = **清空**（Pydantic 的 `model_fields_set` 能区分
    # "没传"与"传了 null"）——否则前端没法把生日清掉。
    return ChildResponse.model_validate(
        ChildService(db).update_profile(
            admin,
            child_id,
            body.english_name,
            body.grade,
            body.ar_level,
            name=body.name,
            gender=body.gender,
            birthday=body.birthday,
            avatar=getattr(body, "avatar", None),
            clear_birthday=("birthday" in body.model_fields_set and body.birthday is None),
        )
    )


@router.delete("/members/children/{child_id}")
def delete_child(
    child_id: int,
    admin: Any = Depends(require_perm("member.manage")),
    db: Session = Depends(get_db),
):
    """软删孩子档案（WM3-B1；订单守卫 409）。"""
    ChildService(db).delete(admin, child_id)
    return {"id": child_id, "deleted": True}


@router.post("/members/children/{child_id}/evaluate-approve", response_model=OrderResponse)
def evaluate_approve(
    child_id: int,
    body: MemberStatusActionRequest,
    admin: Any = Depends(require_perm("member.manage")),
    db: Session = Depends(get_db),
):
    """评估通过转正（C13/R-101-5）：创建年费订单（二孩折扣沿用），收款确认后转正式会员。"""
    order = ChildService(db).evaluate_approve(admin, child_id, body.reason)
    item = OrderResponse.model_validate(order)
    item.amount = str(order.amount)
    return item


@router.post("/orders", response_model=OrderResponse)
def create_order(
    body: OrderCreateRequest,
    admin: Any = Depends(require_perm("member.manage")),
    db: Session = Depends(get_db),
):
    order = OrderService(db).create(admin, body)
    return OrderResponse.model_validate(order)


@router.get("/orders", response_model=PaginatedResponse[OrderResponse])
def list_orders(
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    status: str | None = Query(None),
    keyword: str | None = Query(None),
    order_by: str | None = Query(None),
    admin: Any = Depends(require_perm("member.manage")),
    db: Session = Depends(get_db),
):
    rows, total, enrollment_map, deposit_map = OrderService(db).list_orders(
        page, page_size, status, keyword, order_by
    )
    items = []
    for order, child_name, parent_name in rows:
        item = OrderResponse.model_validate(order)
        item.amount = str(order.amount)
        item.child_name = child_name
        item.parent_name = parent_name
        item.enrollment_id = enrollment_map.get(order.id)
        if order.order_type in ("deposit", "deposit_supplement"):
            info = deposit_map.get(order.child_id) or {}
            item.deposit_refunded_amount = info.get("deposit_refunded_amount", "0.00")
            item.deposit_status = info.get("deposit_status", "")
        items.append(item)
    return PaginatedResponse[OrderResponse].create(
        items=items, total=total, page=page, page_size=page_size
    )


@router.get("/orders/counts")
def order_counts(
    admin: Any = Depends(require_perm("member.manage")),
    db: Session = Depends(get_db),
):
    """订单各状态计数（W3 待确认待办视角；语义化键名，WM13 待办聚合预留）。"""
    return OrderService(db).counts()


@router.post("/orders/{order_id}/confirm-payment", response_model=OrderResponse)
def confirm_payment(
    order_id: int,
    body: OrderConfirmRequest,
    admin: Any = Depends(require_perm("member.manage")),
    db: Session = Depends(get_db),
):
    order = OrderService(db).confirm_payment(admin, order_id, body)
    item = OrderResponse.model_validate(order)
    item.amount = str(order.amount)
    return item


@router.post("/orders/{order_id}/cancel", response_model=OrderResponse)
def cancel_order(
    order_id: int,
    admin: Any = Depends(require_perm("member.manage")),
    db: Session = Depends(get_db),
):
    order = OrderService(db).cancel(admin, order_id)
    item = OrderResponse.model_validate(order)
    item.amount = str(order.amount)
    return item


class OrderRefundRequest(BaseSchema):
    remark: str = Field("", max_length=200, description="退款说明（留痕）")


@router.post("/orders/{order_id}/voucher", response_model=VoucherUploadResponse)
def upload_order_voucher(
    order_id: int,
    file: UploadFile = File(...),
    admin: Any = Depends(require_perm("member.manage")),
    db: Session = Depends(get_db),
):
    """收款凭证上传（WM3-B2 两步式第一步；仅待人工确认订单可传；统一转 JPG）。

    存储/落库/失败清理全链在 OrderService.upload_voucher（Router 零异常处理纪律）。"""
    from backend.common.file_storage import ensure_upload_within_limit, read_image_policy

    ensure_upload_within_limit(file, read_image_policy(db, "doc"))
    data = file.file.read()
    order = OrderService(db).upload_voucher(admin, order_id, data, file.filename or "")
    return VoucherUploadResponse(
        id=order.id, order_no=order.order_no, voucher_path=order.voucher_path or ""
    )


@router.get("/members/orders/{order_id}/voucher-image")
def order_voucher_image(
    order_id: int,
    request: Request,
    token: str = "",
    db: Session = Depends(get_db),
):
    """凭证查看（WM3-B2；<img> 无法带 Authorization → query token 双通道）。

    鉴权（media 三道校验 + member.manage 权限实时派生）在 middleware/voucher_auth；
    订单查询/路径安全在 OrderService（Router 零 ORM/try/admin 依赖纪律）。"""
    from fastapi.responses import FileResponse

    from backend.config import get_settings
    from backend.middleware.voucher_auth import assert_voucher_viewer

    assert_voucher_viewer(request, token, db)
    rel = OrderService.get_voucher_rel_path(db, order_id)
    root = get_settings().UPLOADS_DIR
    full = os.path.abspath(os.path.join(root, rel))
    # P2-5（第七轮，2026-10-18）：原先**裸前缀** `full.startswith(abspath(root))`——同前缀兄弟目录
    # （`uploads-evil/x.jpg`）能骗过它；改用单一口径 `is_within`（`common/media_paths.py`）。
    # P3（第九轮）：补 `isfile` 守卫——库里登记了凭证路径但**磁盘文件已丢**（清理/迁移遗漏）时，
    # FileResponse 会在发送阶段抛 RuntimeError → 500，而语义应是"文件不存在"的 404。
    # 与全仓其余取图端点（书目/观察报告/活动配图/圈帖）同款双判。
    if not is_within(root, full) or not os.path.isfile(full):
        from backend.common.exceptions import NotFoundError

        raise NotFoundError("凭证文件不存在")
    return FileResponse(full, media_type="image/jpeg")


@router.post("/orders/{order_id}/refund")
def refund_order(
    order_id: int,
    body: OrderRefundRequest,
    admin: Any = Depends(require_super_admin()),
    db: Session = Depends(get_db),
):
    """超管代家长发起退款申请（B-15 改造 20260903）：走 R-308 审核链，不再直接退款。
    返回 RefundRequest 视图（pending），后续 review→execute 与家长申请同链。"""
    req = OrderService(db).refund_order(admin, order_id, body.remark)
    return {
        "id": req.id,
        "kind": req.kind,
        "order_id": req.order_id,
        "amount": str(req.amount),
        "status": req.status,
    }


# ==================== WM10：退款 / 退会 / 转让 / 评估报告（管理端） ====================


class ReviewRequest(BaseSchema):
    approve: bool
    remark: str = Field("", max_length=200, description="审核备注（拒绝必填，家长可见）")


@router.get("/refund-requests")
def admin_refund_list(
    response: Response,
    status: str | None = None,
    page: int = Query(1, ge=1, description="页码（服务端分页，P1-7）"),
    page_size: int = Query(200, ge=1, le=200, description="每页条数（上限 200）"),
    admin: Any = Depends(require_super_admin()),
    db: Session = Depends(get_db),
):
    """退款申请列表（订单类 + 押金类统一，超管逐单审）。

    P2-44：响应头如实告知硬上限与"是否已达上限"（`X-List-Limit` / `X-List-Truncated`），
    管理端据此提示"可能还有更多"——不再让 200 条上限看起来像"这就是全部"。
    """
    rows, total = refund_queries.admin_list(db, status, page, page_size)
    stamp_list_meta(
        response,
        loaded=len(rows),
        limit=refund_queries.ADMIN_LIST_LIMIT,
        total=total,
        offset=(page - 1) * page_size,
    )
    return rows


@router.post("/refund-requests/{request_id}/review")
def admin_refund_review(
    request_id: int,
    body: ReviewRequest,
    admin: Any = Depends(require_super_admin()),
    db: Session = Depends(get_db),
):
    return RefundService(db).review(admin, request_id, body.approve, body.remark)


class RefundExecuteRequest(BaseSchema):
    success: bool = Field(
        ..., description="执行结果：true=退款成功（凭证登记）/ false=失败（可重试）"
    )
    remark: str = Field("", max_length=200, description="退款凭证/失败原因（留痕）")


@router.post("/refund-requests/{request_id}/execute")
def admin_refund_execute(
    request_id: int,
    body: RefundExecuteRequest,
    admin: Any = Depends(require_super_admin()),
    db: Session = Depends(get_db),
):
    """执行退款（R-308：approved → processing → refunded/failed；线下打款登记凭证）。"""
    return RefundService(db).execute(admin, request_id, body.success, body.remark)


@router.post("/refund-requests/{request_id}/query-gateway")
def admin_refund_query_gateway(
    request_id: int,
    admin: Any = Depends(require_super_admin()),
    db: Session = Depends(get_db),
):
    """退款查单（WM12-C 审查 P0-1）：未知态（网关超时）退款单调微信确认结果后收口。

    为什么必须是独立动作：未知态下换新商户退款单号重提 = 可能二次出款；查得"未受理"才允许重试。
    """
    return RefundService(db).query_gateway(admin, request_id)


@router.get("/refund-center/counts")
def admin_refund_center_counts(
    admin: Any = Depends(require_super_admin()),
    db: Session = Depends(get_db),
):
    """退款中心三个台账的按状态计数（P1-7：服务端分页后徽标必须来自后端，不能只数本页）。"""
    return refund_queries.refund_center_counts(db)


@router.get("/withdrawals")
def admin_withdrawal_list(
    response: Response,
    status: str | None = None,
    page: int = Query(1, ge=1, description="页码（服务端分页，P1-7）"),
    page_size: int = Query(200, ge=1, le=200, description="每页条数（上限 200）"),
    admin: Any = Depends(require_super_admin()),
    db: Session = Depends(get_db),
):
    """退会台账（P1-7 服务端分页 + P2-44 上限经响应头如实告知）。"""
    svc = WithdrawalService(db)
    rows, total = svc.admin_list(status, page, page_size)
    stamp_list_meta(
        response,
        loaded=len(rows),
        limit=svc.ADMIN_LIST_LIMIT,
        total=total,
        offset=(page - 1) * page_size,
    )
    return rows


@router.get("/withdrawals/{request_id}/settle-preview")
def admin_withdrawal_settle_preview(
    request_id: int,
    admin: Any = Depends(require_super_admin()),
    db: Session = Depends(get_db),
):
    """X2：退会审核预估结算明细（审核前明批，与真正结算同一份计算代码）。"""
    return WithdrawalService(db).settle_preview(request_id)


@router.post("/withdrawals/{request_id}/review")
def admin_withdrawal_review(
    request_id: int,
    body: ReviewRequest,
    admin: Any = Depends(require_super_admin()),
    db: Session = Depends(get_db),
):
    return WithdrawalService(db).review(admin, request_id, body.approve, body.remark)


@router.get("/transfers")
def admin_transfer_list(
    response: Response,
    status: str | None = None,
    page: int = Query(1, ge=1, description="页码（服务端分页，P1-7）"),
    page_size: int = Query(200, ge=1, le=200, description="每页条数（上限 200）"),
    admin: Any = Depends(require_super_admin()),
    db: Session = Depends(get_db),
):
    """转让台账（P1-7 服务端分页 + P2-44 上限经响应头如实告知）。"""
    svc = TransferService(db)
    rows, total = svc.admin_list(status, page, page_size)
    stamp_list_meta(
        response,
        loaded=len(rows),
        limit=svc.ADMIN_LIST_LIMIT,
        total=total,
        offset=(page - 1) * page_size,
    )
    return rows


@router.post("/transfers/{request_id}/review")
def admin_transfer_review(
    request_id: int,
    body: ReviewRequest,
    admin: Any = Depends(require_super_admin()),
    db: Session = Depends(get_db),
):
    return TransferService(db).review(admin, request_id, body.approve, body.remark)


class PaymentSimulateRequest(BaseSchema):
    """演练回调参数（仅 mock 通道）：留空 amount=用订单金额，transaction_id=每次随机。"""

    order_no: str
    amount: str = ""
    trade_state: str = "SUCCESS"
    transaction_id: str = ""


@router.post("/payments/simulate-callback")
def admin_simulate_payment_callback(
    body: PaymentSimulateRequest,
    admin: Any = Depends(require_super_admin()),
    db: Session = Depends(get_db),
):
    """超管演练微信支付回调（**仅 mock 支付通道可用**）：重复回调 / 金额篡改 / 非成功状态。

    为什么需要它：商户号未到位时无法真机验签，但"重复回调不重复入账""金额不符拒绝"
    这类判据必须有人能现场演练（判据见 docs/09 WM12-A §三 / docs/04 WM12 步骤 4-6）。
    """
    from backend.domain.identity.payment_service import PaymentService

    return PaymentService(db).simulate_callback(
        admin,
        body.order_no,
        amount=body.amount,
        trade_state=body.trade_state,
        transaction_id=body.transaction_id,
    )


class PaymentRefundSimulateRequest(BaseSchema):
    """退款回调演练参数（仅 mock 通道）：`out_refund_no` 形如 `RF{退款单id}-1-1`。

    `amount` 留空 = 用退款单申请金额（正常路径）；传金额（元）可演练"退款金额不符被拒"。
    """

    out_refund_no: str
    refund_status: str = "SUCCESS"
    amount: str = ""


@router.post("/payments/simulate-refund-callback", response_model=PaymentSimulateRefundResponse)
def admin_simulate_refund_callback(
    body: PaymentRefundSimulateRequest,
    admin: Any = Depends(require_super_admin()),
    db: Session = Depends(get_db),
):
    """超管演练微信退款结果通知（**仅 mock 支付通道可用**）：重复通知 / 迟到的失败通知 / 金额不符。

    判据见 docs/09 WM12-B §三 / docs/04 WM12 步骤 8-10、15、17。
    """
    from backend.domain.identity.payment_service import PaymentService

    return PaymentService(db).simulate_refund_callback(
        admin, body.out_refund_no, refund_status=body.refund_status, amount=body.amount
    )


class ReconcileRunRequest(BaseSchema):
    """跑一轮对账：留空 bill_date=今天；with_wechat=false 只跑本地一致性审计。"""

    bill_date: str = ""
    with_wechat: bool = True


@router.post("/payments/reconcile", response_model=PaymentReconcileRunResponse)
def admin_run_reconcile(
    body: ReconcileRunRequest,
    admin: Any = Depends(require_super_admin()),
    db: Session = Depends(get_db),
):
    """手动跑一轮资金对账（超管）：本地一致性审计 + 微信账单比对（真通道时）。"""
    from datetime import date as _date

    from backend.domain.identity.payment_models import PaymentReconciliation
    from backend.domain.identity.payment_reconcile_service import PaymentReconcileService

    day = _date.fromisoformat(body.bill_date) if body.bill_date else _date.today()
    return PaymentReconcileService(db).run(
        bill_date=day,
        with_wechat=body.with_wechat,
        trigger=PaymentReconciliation.TRIGGER_MANUAL,
        actor=admin,
    )


@router.get("/payments/reconciliations/export.csv")
def admin_export_reconcile_diffs(
    bill_date: str = "",
    admin: Any = Depends(require_perm("dashboard.view")),
    db: Session = Depends(get_db),
):
    """对账差异**全量明细 CSV**（专家裁决④配套，2026-10-11）。

    `detail` 落库只留样本（TEXT 64KB 上限）→ 审计全貌靠这个二次导出：**重算本地审计**
    并全量导出（口径与当轮同源）。读语义（不写库）→ 与列表同权限码。
    """
    from datetime import date as _date

    from fastapi.responses import Response

    from backend.domain.identity.payment_reconcile_service import PaymentReconcileService

    day = _date.fromisoformat(bill_date) if bill_date else None
    csv_text = PaymentReconcileService(db).export_local_diffs_csv(day)
    return Response(
        content=csv_text,
        media_type="text/csv",
        headers={
            "Content-Disposition": f'attachment; filename="reconcile-diffs-{day or _date.today()}.csv"'
        },
    )


@router.get("/payments/reconciliations", response_model=PaymentReconcileListResponse)
def admin_list_reconciliations(
    limit: int = Query(10, ge=1, le=50),
    admin: Any = Depends(require_perm("dashboard.view")),
    db: Session = Depends(get_db),
):
    """最近的对账报告（专员可读：这是"账对不对"的公开看板，不是敏感资金数据）。"""
    from backend.domain.identity.payment_reconcile_service import PaymentReconcileService

    return {"items": PaymentReconcileService(db).latest(limit)}


@router.post("/children/{child_id}/observation-reports")
def upload_observation_report(
    child_id: int,
    remark: str = Form(""),
    files: list[UploadFile] = File(...),
    admin: Any = Depends(require_perm("member.manage")),
    db: Session = Depends(get_db),
):
    """观察期评估报告上传（≤9 张图；家长端可见）。"""
    from backend.common.file_storage import ensure_upload_within_limit, read_image_policy

    policy = read_image_policy(db, "doc")
    for f in files:  # 解码前逐张拦超限（服务层还会按同一口径压缩）
        ensure_upload_within_limit(f, policy)
    return ObservationReportService(db).upload_for_admin(admin, child_id, files, remark or None)
