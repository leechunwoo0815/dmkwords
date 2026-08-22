# backend/domain/catalog/router.py — 图书资产 API
import os
from typing import TYPE_CHECKING, Any

from fastapi import APIRouter, Depends, File, Query, UploadFile
from fastapi.responses import FileResponse
from sqlalchemy.orm import Session

from backend.common.base_schema import PaginatedResponse
from backend.config import get_settings
from backend.database import get_db

if TYPE_CHECKING:
    pass

from backend.domain.catalog.import_service import import_books
from backend.domain.catalog.schemas import (
    BookCreateRequest,
    BookResponse,
    BookUpdateRequest,
    CopyResponse,
    CopyStatusUpdateRequest,
    ImportResultResponse,
    QuizQuestionCreateRequest,
    QuizQuestionResponse,
)
from backend.domain.catalog.service import BookService, QuizQuestionService
from backend.middleware.admin_rbac import require_perm

router = APIRouter(tags=["catalog"])


def _to_book_response(book, copy_count: int) -> BookResponse:
    return BookResponse(
        id=book.id,
        isbn=book.isbn,
        internal_code=book.internal_code,
        title=book.title,
        author=book.author,
        cover_path=book.cover_path,
        audio_path=book.audio_path,
        audio_duration_seconds=book.audio_duration_seconds,
        word_count=book.word_count,
        ar_level=book.ar_level,
        topic=book.topic,
        grade=book.grade,
        description=book.description,
        status=book.status,
        copy_count=copy_count,
    )


@router.get("/books", response_model=PaginatedResponse[BookResponse])
def list_books(
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    keyword: str | None = Query(None),
    ar_pending: bool = Query(False),
    status: int | None = Query(None),
    admin: Any = Depends(require_perm("book.manage")),
    db: Session = Depends(get_db),
):
    books, counts, total = BookService(db).list_books(page, page_size, keyword, ar_pending, status)
    return PaginatedResponse[BookResponse].create(
        items=[_to_book_response(b, counts.get(b.id, 0)) for b in books],
        total=total,
        page=page,
        page_size=page_size,
    )


@router.post("/books", response_model=BookResponse)
def create_book(
    body: BookCreateRequest,
    admin: Any = Depends(require_perm("book.manage")),
    db: Session = Depends(get_db),
):
    book = BookService(db).create_book(admin, body)
    return _to_book_response(book, body.copy_count)


@router.get("/books/{book_id}", response_model=BookResponse)
def get_book(
    book_id: int,
    admin: Any = Depends(require_perm("book.manage")),
    db: Session = Depends(get_db),
):
    book, copies = BookService(db).get_book(book_id)
    return _to_book_response(book, len(copies))


@router.put("/books/{book_id}", response_model=BookResponse)
def update_book(
    book_id: int,
    body: BookUpdateRequest,
    admin: Any = Depends(require_perm("book.manage")),
    db: Session = Depends(get_db),
):
    book = BookService(db).update_book(admin, book_id, body)
    _, copies = BookService(db).get_book(book_id)
    return _to_book_response(book, len(copies))


@router.post("/books/{book_id}/copies", response_model=list[CopyResponse])
def add_copies(
    book_id: int,
    count: int = Query(..., ge=1, le=99),
    admin: Any = Depends(require_perm("book.manage")),
    db: Session = Depends(get_db),
):
    copies = BookService(db).add_copies(admin, book_id, count)
    return [CopyResponse.model_validate(c) for c in copies]


@router.get("/books/{book_id}/copies", response_model=list[CopyResponse])
def list_copies(
    book_id: int,
    admin: Any = Depends(require_perm("book.manage")),
    db: Session = Depends(get_db),
):
    _, copies = BookService(db).get_book(book_id)
    return [CopyResponse.model_validate(c) for c in copies]


@router.post("/books/{book_id}/toggle-status", response_model=BookResponse)
def toggle_book_status(
    book_id: int,
    admin: Any = Depends(require_perm("book.manage")),
    db: Session = Depends(get_db),
):
    book = BookService(db).toggle_status(admin, book_id)
    _, copies = BookService(db).get_book(book_id)
    return _to_book_response(book, len(copies))


@router.put("/copies/{copy_id}/status", response_model=CopyResponse)
def update_copy_status(
    copy_id: int,
    body: CopyStatusUpdateRequest,
    admin: Any = Depends(require_perm("book.manage")),
    db: Session = Depends(get_db),
):
    copy = BookService(db).update_copy_status(admin, copy_id, body.status, body.reason)
    return CopyResponse.model_validate(copy)


@router.post("/books/{book_id}/cover", response_model=BookResponse)
async def upload_cover(
    book_id: int,
    file: UploadFile = File(...),
    admin: Any = Depends(require_perm("book.manage")),
    db: Session = Depends(get_db),
):
    data = await file.read()
    ext = os.path.splitext(file.filename or "")[1]
    book = BookService(db).upload_cover(admin, book_id, data, ext)
    return _to_book_response(book, 0)


@router.post("/books/{book_id}/audio", response_model=BookResponse)
async def upload_audio(
    book_id: int,
    file: UploadFile = File(...),
    admin: Any = Depends(require_perm("audio.manage")),
    db: Session = Depends(get_db),
):
    data = await file.read()
    book = BookService(db).upload_audio(admin, book_id, data, file.filename or "")
    return _to_book_response(book, 0)


@router.post("/books/import", response_model=ImportResultResponse)
async def import_books_excel(
    file: UploadFile = File(...),
    admin: Any = Depends(require_perm("book.manage")),
    db: Session = Depends(get_db),
):
    data = await file.read()
    result = import_books(db, admin, data)
    return ImportResultResponse(**result)


@router.get("/uploads/{path:path}")
def serve_upload(path: str, admin: Any = Depends(require_perm("book.manage"))):
    """上传文件访问（鉴权下发；封面后续小程序端另行开放只读路由）。"""
    root = os.path.abspath(get_settings().UPLOADS_DIR)
    full = os.path.abspath(os.path.join(root, path))
    if not full.startswith(root):
        from backend.common.exceptions import NotFoundError

        raise NotFoundError("文件不存在")
    if not os.path.isfile(full):
        from backend.common.exceptions import NotFoundError

        raise NotFoundError("文件不存在")
    return FileResponse(full)


# ---------- 测验题目 ----------
@router.get("/books/{book_id}/questions", response_model=list[QuizQuestionResponse])
def list_questions(
    book_id: int,
    admin: Any = Depends(require_perm("quiz.manage")),
    db: Session = Depends(get_db),
):
    questions = QuizQuestionService(db).list_by_book(book_id)
    return [QuizQuestionResponse.model_validate(q) for q in questions]


@router.post("/books/{book_id}/questions", response_model=QuizQuestionResponse)
def create_question(
    book_id: int,
    body: QuizQuestionCreateRequest,
    admin: Any = Depends(require_perm("quiz.manage")),
    db: Session = Depends(get_db),
):
    q = QuizQuestionService(db).create(admin, book_id, body)
    return QuizQuestionResponse.model_validate(q)


@router.post("/questions/{question_id}/toggle-active", response_model=QuizQuestionResponse)
def toggle_question(
    question_id: int,
    admin: Any = Depends(require_perm("quiz.manage")),
    db: Session = Depends(get_db),
):
    q = QuizQuestionService(db).toggle_active(admin, question_id)
    return QuizQuestionResponse.model_validate(q)


@router.delete("/questions/{question_id}")
def delete_question(
    question_id: int,
    admin: Any = Depends(require_perm("quiz.manage")),
    db: Session = Depends(get_db),
):
    QuizQuestionService(db).delete(admin, question_id)
    return {"detail": "已删除"}
