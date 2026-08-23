# backend/domain/circulation/router.py — 借阅操作台 API
from datetime import datetime
from typing import Any

from fastapi import APIRouter, Depends
from pydantic import Field
from sqlalchemy.orm import Session

from backend.common.base_schema import BaseSchema
from backend.database import get_db
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


class RenewRequest(BaseSchema):
    record_id: int


class BorrowRecordResponse(BaseSchema):
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
    records: list[BorrowRecordResponse]


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
        records=[BorrowRecordResponse.model_validate(r) for r in card["active_records"]],
    )


@router.get("/circulation/children/{child_id}/card", response_model=ChildCardResponse)
def child_card(
    child_id: int,
    admin: Any = Depends(require_perm("borrow.operate")),
    db: Session = Depends(get_db),
):
    return _card_payload(CirculationService(db).child_card(child_id))


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
