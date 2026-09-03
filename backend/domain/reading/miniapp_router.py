# backend/domain/reading/miniapp_router.py — 小程序家长端 API（/api/miniapp）
"""开发期登录简化：手机号 + 验证码（固定 1234，上线前接微信 code2session）。
家长 token = JWT（type=parent）。"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, Header
from pydantic import Field
from sqlalchemy.orm import Session

from backend.common.base_schema import BaseSchema
from backend.common.exceptions import NotFoundError
from backend.database import get_db
from backend.domain.catalog.models import Book
from backend.domain.identity import guards
from backend.domain.identity.auth import (
    _parent_from_token,
    authenticate_parent,
    child_of_parent,
    get_current_parent,
)
from backend.domain.reading.service import (
    FavoriteService,
    ReadingService,
    ReservationService,
    ShelfService,
    VocabularyService,
)
from backend.middleware.rate_limit import rate_limit

router = APIRouter(tags=["miniapp"])


class LoginRequest(BaseSchema):
    phone: str = Field(..., pattern=r"^\d{11}$")
    code: str = Field(..., description="短信验证码（开发期固定 1234）")


class ProgressReportRequest(BaseSchema):
    child_id: int
    book_id: int
    position: int = Field(..., ge=0)
    session_start: int | None = Field(None, ge=0)


@router.post("/login", dependencies=[Depends(rate_limit(3, 60))])
def login(body: LoginRequest, db: Session = Depends(get_db)):
    """家长登录（A-1/T6 下沉：逻辑在 identity.auth.authenticate_parent）。"""
    return authenticate_parent(db, body.phone, body.code)


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
    from backend.domain.catalog.service import BookService

    _, db = auth
    total, books = BookService(db).list_miniapp_books(
        keyword=keyword,
        page=page,
        page_size=page_size,
        grade=grade,
        topic=topic,
        ar_min=ar_min,
        ar_max=ar_max,
        has_audio=has_audio,
        sort=sort,
    )
    return {"total": total, "items": [_book_view(b) for b in books]}


@router.get("/books/{book_id}/progress")
def get_progress(book_id: int, child_id: int, auth: Any = Depends(get_current_parent)):
    parent, db = auth
    child = child_of_parent(db, parent.id, child_id)  # P0-F1 归属校验
    return ReadingService(db).get_progress(child, book_id)


@router.post("/reading/progress")
def report_progress(body: ProgressReportRequest, auth: Any = Depends(get_current_parent)):
    parent, db = auth
    child = child_of_parent(db, parent.id, body.child_id)  # P0-F1 归属校验
    return ReadingService(db).report_progress(
        child, body.book_id, body.position, body.session_start
    )


@router.get("/checkins")
def checkin_calendar(child_id: int, days: int = 30, auth: Any = Depends(get_current_parent)):
    parent, db = auth
    child = child_of_parent(db, parent.id, child_id)  # P0-F1 归属校验
    return ReadingService(db).checkin_calendar(child, days)


@router.get("/reservations")
def list_reservations(child_id: int, auth: Any = Depends(get_current_parent)):
    parent, db = auth
    child = child_of_parent(db, parent.id, child_id)  # P0-F1 归属校验（含 None 检查）
    return ReservationService(db).list_mine(child)


class ReservationCreateRequest(BaseSchema):
    child_id: int
    book_id: int


class ReservationCancelRequest(BaseSchema):
    child_id: int


@router.post("/reservations")
def create_reservation(body: ReservationCreateRequest, auth: Any = Depends(get_current_parent)):
    parent, db = auth
    child = child_of_parent(db, parent.id, body.child_id)  # P0-F1 归属校验
    res = ReservationService(db).create(child, body.book_id)
    return {"id": res.id, "expires_at": str(res.expires_at), "status": res.status}


@router.post("/reservations/{reservation_id}/cancel")
def cancel_reservation(
    reservation_id: int, body: ReservationCancelRequest, auth: Any = Depends(get_current_parent)
):
    parent, db = auth
    child = child_of_parent(db, parent.id, body.child_id)  # P0-F1 归属校验
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
    from backend.domain.catalog.service import BookService

    _, db = auth
    book = BookService(db).get_book_public(book_id)
    if not book:
        raise NotFoundError("图书不存在")
    return _book_view(book)


@router.get("/books/{book_id}/audio")
def book_audio(book_id: int, token: str = "", db: Session = Depends(get_db)):
    """音频流（query token：innerAudioContext 无法携带 Authorization 头）。"""
    import os

    from fastapi.responses import FileResponse

    from backend.config import get_settings
    from backend.domain.catalog.service import BookService

    _parent_from_token(token, db)
    book = BookService(db).get_book_public(book_id)
    if not book or not book.audio_path:
        raise NotFoundError("音频不存在")
    root = os.path.abspath(get_settings().UPLOADS_DIR)
    full = os.path.abspath(os.path.join(root, book.audio_path))
    if not full.startswith(root) or not os.path.isfile(full):
        raise NotFoundError("音频不存在")
    return FileResponse(full, media_type="audio/mpeg")


@router.get("/observation-images/{path:path}")
def observation_image(path: str, token: str = "", db: Session = Depends(get_db)):
    """观察期评估报告图片（仅限 observation/ 目录；query token 鉴权 + 归属校验）。"""
    import os

    from fastapi.responses import FileResponse

    from backend.config import get_settings
    from backend.domain.identity.observation_service import ObservationReportService

    parent = _parent_from_token(token, db)
    root = os.path.abspath(get_settings().UPLOADS_DIR)
    full = os.path.abspath(os.path.join(root, "observation", path))
    if not full.startswith(os.path.join(root, "observation")) or not os.path.isfile(full):
        raise NotFoundError("图片不存在")
    # C-12/T25（P0-F1 同族第五案）：数据归属校验下沉 Service（架构关 Router 零 ORM）
    # images JSON 精确匹配 + 家长归属；查无归属一律 404（防枚举探测）
    stored_path = f"observation/{path}"
    if not ObservationReportService(db).image_owned_by(parent.id, stored_path, path):
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
    from backend.domain.catalog.service import BookService

    effective_token = token or (authorization or "").replace("Bearer ", "").strip()
    _parent_from_token(effective_token, db)
    book = BookService(db).get_book_public(book_id)
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


@router.get("/vocabulary/lookup")
def vocabulary_lookup(
    word: str,
    child_id: int,
    book_id: int | None = None,
    auth: Any = Depends(get_current_parent),
):
    """查词（命中自动进生词本；播放页下半屏查询不中断音频）。R-313：未缴费禁/过期仅音频场景/退会禁。"""
    parent, db = auth
    child = child_of_parent(db, parent.id, child_id)
    guards.require_member_action(db, child, guards.LOOKUP, book_id=book_id)
    return VocabularyService(db).lookup(child, word, book_id)


@router.get("/vocabulary")
def vocabulary_list(child_id: int, auth: Any = Depends(get_current_parent)):
    parent, db = auth
    child = child_of_parent(db, parent.id, child_id)
    guards.require_member_action(db, child, guards.VOCAB_VIEW)
    return VocabularyService(db).list_words(child)


@router.delete("/vocabulary/{vocabulary_id}")
def vocabulary_remove(vocabulary_id: int, child_id: int, auth: Any = Depends(get_current_parent)):
    parent, db = auth
    child = child_of_parent(db, parent.id, child_id)
    guards.require_member_action(db, child, guards.VOCAB_WRITE)
    VocabularyService(db).remove(child, vocabulary_id)
    return {"detail": "已删除"}


# ---------- 收藏夹（WM8） ----------


@router.get("/favorites")
def favorites_list(child_id: int, auth: Any = Depends(get_current_parent)):
    parent, db = auth
    child = child_of_parent(db, parent.id, child_id)
    return FavoriteService(db).list_mine(child)


@router.post("/favorites")
def favorites_add(body: dict, auth: Any = Depends(get_current_parent)):
    parent, db = auth
    child = child_of_parent(db, parent.id, int(body.get("child_id") or 0))
    guards.require_member_action(db, child, guards.FAVORITE_WRITE)
    return FavoriteService(db).add(child, int(body.get("book_id") or 0))


@router.delete("/favorites/{book_id}")
def favorites_remove(book_id: int, child_id: int, auth: Any = Depends(get_current_parent)):
    parent, db = auth
    child = child_of_parent(db, parent.id, child_id)
    guards.require_member_action(db, child, guards.FAVORITE_WRITE)
    FavoriteService(db).remove(child, book_id)
    return {"detail": "已取消收藏"}


# ---------- 书架（当前在借，WM8） ----------


@router.get("/borrows")
def current_borrows(child_id: int, auth: Any = Depends(get_current_parent)):
    parent, db = auth
    child = child_of_parent(db, parent.id, child_id)
    return ShelfService(db).current_borrows(child)


@router.get("/continue-listening")
def continue_listening(child_id: int, auth: Any = Depends(get_current_parent)):
    """首页"继续听"卡：最近一次有进度但未读完（finished=0）的上一本。
    读完的/无进度的不返回；无续听对象返回 null（前端隐藏卡片）。"""
    parent, db = auth
    child = child_of_parent(db, parent.id, child_id)
    data = ReadingService(db).continue_listening(child)
    if not data:
        return None
    return {
        "book": _book_view(data["book"]),
        "percent": data["percent"],
        "last_position": data["last_position"],
        "due_at": data["due_at"],
    }
