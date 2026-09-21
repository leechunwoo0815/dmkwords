# backend/domain/circulation/router.py — 借阅操作台 API
from datetime import datetime
from typing import Any

from fastapi import APIRouter, Depends, Query
from fastapi.responses import StreamingResponse
from pydantic import Field
from sqlalchemy.orm import Session

from backend.common.base_schema import BaseSchema, PaginatedResponse
from backend.common.exceptions import ValidationError
from backend.database import get_db
from backend.domain.circulation.records_service import BorrowRecordsService
from backend.domain.circulation.scan_service import ScanService
from backend.domain.circulation.service import CirculationService
from backend.middleware.admin_rbac import require_perm

router = APIRouter(tags=["circulation"])


class BorrowRequest(BaseSchema):
    child_id: int
    isbn: str | None = Field(None, max_length=20, description="扫 ISBN 借书")
    copy_id: int | None = Field(None, description="指定副本")
    override_reason: str | None = Field(
        None, max_length=200, description="人工放行原因（异常借书）"
    )


class ReturnRequest(BaseSchema):
    copy_id: int
    condition: str = Field("normal", pattern="^(normal|maintenance|lost)$")


class ScanRequest(BaseSchema):
    child_id: int = Field(..., description="当前读者（借阅台已选中的孩子）")
    code: str = Field(..., max_length=40, description="扫码枪读到的码：会员码或图书 ISBN")


class RenewRequest(BaseSchema):
    record_id: int


class BorrowRecordResponse(BaseSchema):
    # 2026-09-21：卡面在借表要显示"这是哪本书"（用户反馈"光显示日期谁知道是什么书"）；
    # 但它是借阅记录表，书名/副本码在别的表 → 由 child_card() 附加同名属性后序列化。
    book_title: str = ""
    copy_code: str = ""
    id: int
    child_id: int
    copy_id: int
    book_id: int
    borrowed_at: datetime
    due_at: datetime
    returned_at: datetime | None
    status: str
    renew_used: int
    override_reason: str | None
    warnings: list[str] = Field(
        default_factory=list, description="借书软提示（AR 超范围等，不拦截）"
    )


class ChildCardResponse(BaseSchema):
    child_id: int
    name: str
    english_name: str | None
    member_status: str
    parent_name: str
    parent_phone: str
    active_borrows: int
    overdue_count: int
    available_quota: int
    borrow_limit: int
    deposit_status: str
    deposit_available: str
    # 借书资格提示（2026-09-21）：不可借时给中文原因；hard=True 表示放行也没用
    borrow_block: str | None = None
    borrow_block_hard: bool = False
    records: list[BorrowRecordResponse]


class ScanResponse(BaseSchema):
    """扫码判定结果（2026-09-21 C 批）。

    只覆盖**成功路径**（member / borrow / return / checkout）；"不能借/不能还"一律走既有异常
    （409/422 + 中文原因），前端沿用同一套提示与「人工放行」弹窗，不新增第二套错误协议。
    """

    action: str = Field(..., description="member=扫到会员码 / borrow / return / checkout")
    message: str
    book_title: str | None = None
    copy_id: int | None = None
    due_at: datetime | None = None
    warnings: list[str] = []
    record: BorrowRecordResponse | None = None
    card: ChildCardResponse | None = None


class RecordItemResponse(BaseSchema):
    """借还记录一行（2026-09-21 D 批）。操作人姓名为"未记录"时表示该行没记过（不编造）。"""

    record_id: int
    child_id: int
    child_name: str
    parent_phone: str
    book_id: int
    book_title: str
    copy_id: int
    copy_code: str
    status: str
    borrowed_at: datetime
    due_at: datetime
    returned_at: datetime | None = None
    returned_condition: str | None = None
    renew_used: int
    days_overdue: int
    borrowed_by_name: str
    returned_by_name: str


class OverdueItemResponse(BaseSchema):
    record_id: int
    child_name: str
    parent_phone: str
    book_title: str
    copy_code: str
    due_at: datetime
    days_overdue: int


def _card_payload(card: dict) -> ChildCardResponse:
    child = card["child"]
    parent = card["parent"]
    return ChildCardResponse(
        child_id=child.id,
        name=child.name,
        english_name=child.english_name,
        member_status=child.member_status,
        parent_name=parent.name,
        parent_phone=parent.phone,
        active_borrows=card["active_borrows"],
        overdue_count=card["overdue_count"],
        available_quota=card["available_quota"],
        borrow_limit=card["borrow_limit"],
        deposit_status=card["deposit_status"],
        deposit_available=card["deposit_available"],
        borrow_block=card.get("borrow_block"),
        borrow_block_hard=card.get("borrow_block_hard", False),
        records=[BorrowRecordResponse.model_validate(r) for r in card["active_records"]],
    )


@router.get("/circulation/records", response_model=PaginatedResponse[RecordItemResponse])
def list_records(
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    keyword: str | None = Query(None, description="孩子名 / 家长手机号 / 书名 / ISBN / 副本码"),
    status: str | None = Query(None, description="active/overdue/returned/lost"),
    date_field: str = Query("borrowed", description="按借出时间(borrowed)或归还时间(returned)筛"),
    date_from: datetime | None = Query(None),
    date_to: datetime | None = Query(None),
    admin: Any = Depends(require_perm("borrow.operate")),
    db: Session = Depends(get_db),
):
    """借还记录查询（2026-09-21 D 批）：借出与归还两条时间线、**含归还操作人**、分页 + 多条件。"""
    items, total = BorrowRecordsService(db).list_records(
        page=page,
        page_size=page_size,
        keyword=keyword,
        status=status,
        date_field=date_field,
        date_from=date_from,
        date_to=date_to,
    )
    return PaginatedResponse[RecordItemResponse].create(items, total, page, page_size)


@router.get("/circulation/records/export")
def export_records(
    keyword: str | None = Query(None),
    status: str | None = Query(None),
    date_field: str = Query("borrowed"),
    date_from: datetime | None = Query(None),
    date_to: datetime | None = Query(None),
    admin: Any = Depends(require_perm("borrow.operate")),
    db: Session = Depends(get_db),
):
    """借还记录导出 Excel（**按当前筛选**导出，不是全量）。"""
    data = BorrowRecordsService(db).export_excel(
        keyword=keyword,
        status=status,
        date_field=date_field,
        date_from=date_from,
        date_to=date_to,
    )
    return StreamingResponse(
        iter([data]),
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": 'attachment; filename="borrow-records.xlsx"'},
    )


@router.get("/circulation/children/by-code/{member_code}/card", response_model=ChildCardResponse)
def child_card_by_code(
    member_code: str,
    admin: Any = Depends(require_perm("borrow.operate")),
    db: Session = Depends(get_db),
):
    """按**会员码**取孩子卡片（借阅台扫"会员码"框走这里，2026-09-21 A 批）。

    声明在 `/{child_id}/card` **之前**——否则 "by-code" 会被当成 int 路径参数解析（同活动域
    `activities/past` 的先例）。码不合法与查无此人是**两种**错误：前者 422 提示"码不合法"，
    后者 404 提示"未找到该会员码对应的孩子"，让馆员分得清"扫错码"还是"这孩子没建档"。
    """
    from backend.domain.identity.member_code import is_valid_member_code, normalize_member_code

    code = normalize_member_code(member_code)
    if not is_valid_member_code(code):
        raise ValidationError("会员码不合法（请核对是否扫错或手输错位）")
    child_id = CirculationService(db).child_id_by_member_code(code)
    return _card_payload(CirculationService(db).child_card(child_id))


@router.get("/circulation/children/{child_id}/card", response_model=ChildCardResponse)
def child_card(
    child_id: int,
    admin: Any = Depends(require_perm("borrow.operate")),
    db: Session = Depends(get_db),
):
    return _card_payload(CirculationService(db).child_card(child_id))


@router.post("/circulation/scan", response_model=ScanResponse)
def scan_code(
    body: ScanRequest,
    admin: Any = Depends(require_perm("borrow.operate")),
    db: Session = Depends(get_db),
):
    """扫码统一入口（2026-09-21 C 批）：**扫会员码 → 孩子卡片；扫 ISBN → 自动判借/还/核销**。

    馆员不再需要按"借出"按钮：一个枪、两个框（先会员后图书），扫完即出结果。
    """
    result = ScanService(db).scan(admin, body.child_id, body.code)
    out = ScanResponse(
        action=result["action"],
        message=result["message"],
        book_title=result.get("book_title"),
        copy_id=result.get("copy_id"),
        due_at=result.get("due_at"),
        warnings=result.get("warnings") or [],
    )
    if result.get("record") is not None:
        out.record = BorrowRecordResponse.model_validate(result["record"])
    if result.get("card") is not None:
        out.card = _card_payload(result["card"])
    return out


@router.post("/circulation/borrow", response_model=BorrowRecordResponse)
def borrow_book(
    body: BorrowRequest,
    admin: Any = Depends(require_perm("borrow.operate")),
    db: Session = Depends(get_db),
):
    record, warnings = CirculationService(db).borrow(
        admin, body.child_id, body.copy_id, body.isbn, body.override_reason
    )
    resp = BorrowRecordResponse.model_validate(record)
    resp.warnings = warnings
    return resp


@router.post("/circulation/return", response_model=BorrowRecordResponse)
def return_book(
    body: ReturnRequest,
    admin: Any = Depends(require_perm("borrow.operate")),
    db: Session = Depends(get_db),
):
    record = CirculationService(db).return_book(admin, body.copy_id, body.condition)
    return BorrowRecordResponse.model_validate(record)


@router.post("/circulation/renew", response_model=BorrowRecordResponse)
def renew_book(
    body: RenewRequest,
    admin: Any = Depends(require_perm("borrow.operate")),
    db: Session = Depends(get_db),
):
    record = CirculationService(db).renew(admin, body.record_id)
    return BorrowRecordResponse.model_validate(record)


@router.get("/circulation/overdue", response_model=list[OverdueItemResponse])
def overdue_list(
    admin: Any = Depends(require_perm("borrow.operate")), db: Session = Depends(get_db)
):
    rows = CirculationService(db).overdue_list()
    now = datetime.now()
    out = []
    for record, child, parent, book in rows:
        out.append(
            OverdueItemResponse(
                record_id=record.id,
                child_name=child.name,
                parent_phone=parent.phone,
                book_title=book.title,
                copy_code="",
                due_at=record.due_at,
                days_overdue=max(0, (now - record.due_at).days),
            )
        )
    return out
