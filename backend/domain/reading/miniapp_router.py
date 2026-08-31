# backend/domain/reading/miniapp_router.py — 小程序家长端 API（/api/miniapp）
"""开发期登录简化：手机号 + 验证码（固定 1234，上线前接微信 code2session）。
家长 token = JWT（type=parent）。"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

from fastapi import APIRouter, Depends, Header
from pydantic import Field
from sqlalchemy import func
from sqlalchemy.orm import Session
from sqlalchemy.types import Numeric

from backend.common.base_schema import BaseSchema
from backend.common.exceptions import NotFoundError, UnauthorizedError, ValidationError
from backend.database import get_db
from backend.domain.catalog.models import Book
from backend.domain.identity import guards
from backend.domain.identity.models import Child, Parent
from backend.domain.reading.service import (
    FavoriteService,
    ReadingService,
    ReservationService,
    ShelfService,
    VocabularyService,
)

router = APIRouter(tags=["miniapp"])


def _parent_token(parent_id: int) -> str:
    import jwt as pyjwt

    from backend.common.security import decode_admin_token  # noqa: F401 — 复用密钥
    from backend.config import get_settings

    payload = {
        "sub": str(parent_id),
        "type": "parent",
        "exp": datetime.now(UTC) + timedelta(days=30),
    }
    return pyjwt.encode(payload, get_settings().SECRET_KEY, algorithm="HS256")


def _parent_from_token(token: str, db: Session) -> Parent:
    """query-token 鉴权（音频/图片等组件无法携带 Authorization 头时用）。"""
    import jwt as pyjwt

    from backend.config import get_settings

    try:
        payload = pyjwt.decode(token, get_settings().SECRET_KEY, algorithms=["HS256"])
    except pyjwt.PyJWTError as e:
        raise UnauthorizedError("请先登录") from e
    if payload.get("type") != "parent":
        raise UnauthorizedError("无效的家长凭证")
    parent = (
        db.query(Parent).filter(Parent.id == int(payload["sub"]), Parent.is_deleted == 0).first()
    )
    if not parent:
        raise UnauthorizedError("账号不存在")
    return parent


def get_current_parent(
    authorization: str = Header(...), db: Session = Depends(get_db)
) -> tuple[Parent, Session]:
    token = authorization.replace("Bearer ", "")
    return _parent_from_token(token, db), db


class LoginRequest(BaseSchema):
    phone: str = Field(..., pattern=r"^\d{11}$")
    code: str = Field(..., description="短信验证码（开发期固定 1234）")


class ProgressReportRequest(BaseSchema):
    child_id: int
    book_id: int
    position: int = Field(..., ge=0)
    session_start: int | None = Field(None, ge=0)


@router.post("/login")
def login(body: LoginRequest, db: Session = Depends(get_db)):
    if body.code != "1234":
        raise ValidationError("验证码错误（开发期固定 1234）")
    parent = db.query(Parent).filter(Parent.phone == body.phone, Parent.is_deleted == 0).first()
    if not parent:
        raise ValidationError("该手机号未注册（请到店建档）")
    children = (
        db.query(Child)
        .filter(Child.parent_id == parent.id, Child.is_deleted == 0)
        .order_by(Child.id)
        .all()
    )
    return {
        "token": _parent_token(parent.id),
        "parent": {"id": parent.id, "name": parent.name, "phone": parent.phone},
        "children": [
            {
                "id": c.id,
                "name": c.name,
                "english_name": c.english_name,
                "member_status": c.member_status,
                "avatar": c.avatar,
            }
            for c in children
        ],
    }


@router.get("/books")
def list_books(
    keyword: str | None = None,
    page: int = 1,
    page_size: int = 20,
    grade: str | None = None,
    topic: str | None = None,
    ar_min: float | None = None,
    ar_max: float | None = None,
    has_audio: bool = False,
    sort: str = "newest",
    auth: Any = Depends(get_current_parent),
):
    """书城列表（2000 本规模检索）：筛选（年级/主题/AR 区间/有音频）+ 排序 + 分页。
    ar_level 是字符串列，范围过滤/排序一律 CAST DECIMAL（非法值按 0 处理）。"""
    _, db = auth
    q = db.query(Book).filter(Book.is_deleted == 0, Book.status == Book.STATUS_ON)
    if keyword:
        like = f"%{keyword}%"
        q = q.filter(Book.title.like(like) | Book.author.like(like))
    if grade:
        q = q.filter(Book.grade == grade)
    if topic:
        q = q.filter(Book.topic == topic)
    ar_expr = func.cast(Book.ar_level, Numeric(4, 1))
    if ar_min is not None:
        q = q.filter(ar_expr >= ar_min)
    if ar_max is not None:
        q = q.filter(ar_expr <= ar_max)
    if has_audio:
        q = q.filter(Book.audio_path.isnot(None))
    order = {
        "ar_asc": ar_expr.asc(),
        "ar_desc": ar_expr.desc(),
        "words_asc": Book.word_count.asc(),
        "words_desc": Book.word_count.desc(),
    }.get(sort, Book.id.desc())
    total = q.count()
    books = q.order_by(order).offset((page - 1) * page_size).limit(page_size).all()
    return {"total": total, "items": [_book_view(b) for b in books]}


@router.get("/books/{book_id}/progress")
def get_progress(book_id: int, child_id: int, auth: Any = Depends(get_current_parent)):
    parent, db = auth
    child = _child_of_parent(db, parent.id, child_id)  # P0-F1 归属校验
    return ReadingService(db).get_progress(child, book_id)


@router.post("/reading/progress")
def report_progress(body: ProgressReportRequest, auth: Any = Depends(get_current_parent)):
    parent, db = auth
    child = _child_of_parent(db, parent.id, body.child_id)  # P0-F1 归属校验
    return ReadingService(db).report_progress(
        child, body.book_id, body.position, body.session_start
    )


@router.get("/checkins")
def checkin_calendar(child_id: int, days: int = 30, auth: Any = Depends(get_current_parent)):
    parent, db = auth
    child = _child_of_parent(db, parent.id, child_id)  # P0-F1 归属校验
    return ReadingService(db).checkin_calendar(child, days)


@router.get("/reservations")
def list_reservations(child_id: int, auth: Any = Depends(get_current_parent)):
    parent, db = auth
    child = _child_of_parent(db, parent.id, child_id)  # P0-F1 归属校验（含 None 检查）
    return ReservationService(db).list_mine(child)


class ReservationCreateRequest(BaseSchema):
    child_id: int
    book_id: int


class ReservationCancelRequest(BaseSchema):
    child_id: int


@router.post("/reservations")
def create_reservation(body: ReservationCreateRequest, auth: Any = Depends(get_current_parent)):
    parent, db = auth
    child = _child_of_parent(db, parent.id, body.child_id)  # P0-F1 归属校验
    res = ReservationService(db).create(child, body.book_id)
    return {"id": res.id, "expires_at": str(res.expires_at), "status": res.status}


@router.post("/reservations/{reservation_id}/cancel")
def cancel_reservation(
    reservation_id: int, body: ReservationCancelRequest, auth: Any = Depends(get_current_parent)
):
    parent, db = auth
    child = _child_of_parent(db, parent.id, body.child_id)  # P0-F1 归属校验
    res = ReservationService(db).cancel(child, reservation_id)
    return {"id": res.id, "status": res.status}


def _book_view(b: Book) -> dict:
    return {
        "id": b.id,
        "title": b.title,
        "author": b.author,
        "isbn": b.isbn,
        "word_count": b.word_count,
        "ar_level": b.ar_level,
        "topic": b.topic,
        "grade": b.grade,
        "description": b.description,
        "cover_url": f"/api/miniapp/covers/{b.id}" if b.cover_path else None,
        "has_audio": bool(b.audio_path),
        "audio_duration": b.audio_duration_seconds,
        "audio_url": f"/api/miniapp/books/{b.id}/audio" if b.audio_path else None,
        "off_shelf": b.status != Book.STATUS_ON,
    }


@router.get("/books/{book_id}")
def book_detail(book_id: int, auth: Any = Depends(get_current_parent)):
    """书目详情（书架收藏/在借进入时补全 audio_url 等字段）。"""
    _, db = auth
    book = db.query(Book).filter(Book.id == book_id, Book.is_deleted == 0).first()
    if not book:
        raise NotFoundError("图书不存在")
    return _book_view(book)


@router.get("/books/{book_id}/audio")
def book_audio(book_id: int, token: str = "", db: Session = Depends(get_db)):
    """音频流（query token：innerAudioContext 无法携带 Authorization 头）。"""
    import os

    from fastapi.responses import FileResponse

    from backend.config import get_settings

    _parent_from_token(token, db)
    book = db.query(Book).filter(Book.id == book_id, Book.is_deleted == 0).first()
    if not book or not book.audio_path:
        raise NotFoundError("音频不存在")
    root = os.path.abspath(get_settings().UPLOADS_DIR)
    full = os.path.abspath(os.path.join(root, book.audio_path))
    if not full.startswith(root) or not os.path.isfile(full):
        raise NotFoundError("音频不存在")
    return FileResponse(full, media_type="audio/mpeg")


@router.get("/observation-images/{path:path}")
def observation_image(path: str, token: str = "", db: Session = Depends(get_db)):
    """观察期评估报告图片（仅限 observation/ 目录；query token 鉴权）。"""
    import os

    from fastapi.responses import FileResponse

    from backend.config import get_settings

    _parent_from_token(token, db)
    root = os.path.abspath(get_settings().UPLOADS_DIR)
    full = os.path.abspath(os.path.join(root, "observation", path))
    if not full.startswith(os.path.join(root, "observation")) or not os.path.isfile(full):
        raise NotFoundError("图片不存在")
    return FileResponse(full, media_type="image/jpeg")


@router.get("/covers/{book_id}")
def book_cover(
    book_id: int,
    token: str = "",
    authorization: str | None = Header(None, alias="Authorization"),
    db: Session = Depends(get_db),
):
    """封面图（query token：image 组件无法携带 Authorization 头；同时兼容 Header 鉴权）。"""
    import os

    from fastapi.responses import FileResponse

    from backend.config import get_settings

    # 优先 query token，其次 Authorization 头，保持旧客户端兼容
    effective_token = token or (authorization or "").replace("Bearer ", "").strip()
    _parent_from_token(effective_token, db)
    book = db.query(Book).filter(Book.id == book_id, Book.is_deleted == 0).first()
    if not book or not book.cover_path:
        from backend.common.exceptions import NotFoundError

        raise NotFoundError("封面不存在")
    root = os.path.abspath(get_settings().UPLOADS_DIR)
    full = os.path.abspath(os.path.join(root, book.cover_path))
    if not full.startswith(root) or not os.path.isfile(full):
        from backend.common.exceptions import NotFoundError

        raise NotFoundError("封面不存在")
    return FileResponse(full)


# ---------- 生词本与查词（WM8） ----------


def _child_of_parent(db: Session, parent_id: int, child_id: int) -> Child:
    child = (
        db.query(Child)
        .filter(Child.id == child_id, Child.parent_id == parent_id, Child.is_deleted == 0)
        .first()
    )
    if not child:
        raise ValidationError("孩子不存在")
    return child


@router.get("/vocabulary/lookup")
def vocabulary_lookup(
    word: str,
    child_id: int,
    book_id: int | None = None,
    auth: Any = Depends(get_current_parent),
):
    """查词（命中自动进生词本；播放页下半屏查询不中断音频）。R-313：未缴费禁/过期仅音频场景/退会禁。"""
    parent, db = auth
    child = _child_of_parent(db, parent.id, child_id)
    guards.require_member_action(db, child, guards.LOOKUP, book_id=book_id)
    return VocabularyService(db).lookup(child, word, book_id)


@router.get("/vocabulary")
def vocabulary_list(child_id: int, auth: Any = Depends(get_current_parent)):
    parent, db = auth
    child = _child_of_parent(db, parent.id, child_id)
    guards.require_member_action(db, child, guards.VOCAB_VIEW)
    return VocabularyService(db).list_words(child)


@router.delete("/vocabulary/{vocabulary_id}")
def vocabulary_remove(vocabulary_id: int, child_id: int, auth: Any = Depends(get_current_parent)):
    parent, db = auth
    child = _child_of_parent(db, parent.id, child_id)
    guards.require_member_action(db, child, guards.VOCAB_WRITE)
    VocabularyService(db).remove(child, vocabulary_id)
    return {"detail": "已删除"}


# ---------- 收藏夹（WM8） ----------


@router.get("/favorites")
def favorites_list(child_id: int, auth: Any = Depends(get_current_parent)):
    parent, db = auth
    child = _child_of_parent(db, parent.id, child_id)
    return FavoriteService(db).list_mine(child)


@router.post("/favorites")
def favorites_add(body: dict, auth: Any = Depends(get_current_parent)):
    parent, db = auth
    child = _child_of_parent(db, parent.id, int(body.get("child_id") or 0))
    guards.require_member_action(db, child, guards.FAVORITE_WRITE)
    return FavoriteService(db).add(child, int(body.get("book_id") or 0))


@router.delete("/favorites/{book_id}")
def favorites_remove(book_id: int, child_id: int, auth: Any = Depends(get_current_parent)):
    parent, db = auth
    child = _child_of_parent(db, parent.id, child_id)
    guards.require_member_action(db, child, guards.FAVORITE_WRITE)
    FavoriteService(db).remove(child, book_id)
    return {"detail": "已取消收藏"}


# ---------- 书架（当前在借，WM8） ----------


@router.get("/borrows")
def current_borrows(child_id: int, auth: Any = Depends(get_current_parent)):
    parent, db = auth
    child = _child_of_parent(db, parent.id, child_id)
    return ShelfService(db).current_borrows(child)


@router.get("/continue-listening")
def continue_listening(child_id: int, auth: Any = Depends(get_current_parent)):
    """首页"继续听"卡：最近一次有进度但未读完（finished=0）的上一本。
    读完的/无进度的不返回；无续听对象返回 null（前端隐藏卡片）。"""
    from backend.domain.catalog.models import Book as BookModel
    from backend.domain.circulation.models import BorrowRecord
    from backend.domain.reading.models import ReadingProgress

    parent, db = auth
    child = _child_of_parent(db, parent.id, child_id)
    row = (
        db.query(ReadingProgress, BookModel, BorrowRecord)
        .join(BookModel, ReadingProgress.book_id == BookModel.id)
        .join(
            BorrowRecord,
            (BorrowRecord.child_id == ReadingProgress.child_id)
            & (BorrowRecord.book_id == ReadingProgress.book_id),
            isouter=True,
        )
        .filter(
            ReadingProgress.child_id == child.id,
            ReadingProgress.finished == 0,
            ReadingProgress.last_report_at.isnot(None),
            ReadingProgress.is_deleted == 0,
            BookModel.is_deleted == 0,
            BookModel.status == BookModel.STATUS_ON,
        )
        .order_by(ReadingProgress.last_report_at.desc())
        .first()
    )
    if not row:
        return None
    p, book, br = row
    in_borrow = bool(br and br.status in (BorrowRecord.STATUS_ACTIVE, BorrowRecord.STATUS_OVERDUE))
    return {
        "book": _book_view(book),
        "percent": round(p.coverage_seconds * 100 / p.total_seconds, 1) if p.total_seconds else 0,
        "last_position": p.last_position,
        "due_at": str(br.due_at) if in_borrow else None,
    }
