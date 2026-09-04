# backend/domain/reading/vocabulary_service.py — 生词本与查词（WM8）
"""从 service.py 拆出（god file 800 行限制）：查词/生词本收录/移除。

引用路径保持：miniapp_router 经 service re-export 不变。
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import func
from sqlalchemy.orm import Session

from backend.common.exceptions import ConflictError, NotFoundError, ValidationError
from backend.domain.catalog.models import Book
from backend.domain.circulation.models import BorrowRecord
from backend.domain.identity.models import Child
from backend.domain.reading.models import (
    DictionaryWord,
    Favorite,
    Vocabulary,
)


class VocabularyService:
    """查词 + 生词本（FEAT-054/055：主动查词自动收录；同词唯一；记来源书）。"""

    def __init__(self, db: Session):
        self.db = db

    def lookup(self, child: Child, word: str, book_id: int | None = None) -> dict:
        w = (word or "").strip().lower()
        if not w or not w.isascii() or not w.isalpha() or len(w) > 64:
            raise ValidationError("请输入一个英文单词（仅字母）")
        entry = (
            self.db.query(DictionaryWord)
            .filter(DictionaryWord.word == w, DictionaryWord.is_deleted == 0)
            .first()
        )
        if not entry:
            raise NotFoundError(f"词库里没有「{w}」（第一期支持精确查询）")
        # 自动收录（同词唯一；重复查更新来源书记录但不重复）
        # 含软删行一起查：唯一索引 uq_vocab_child_word 不含 is_deleted，
        # 软删行会挡住重新 INSERT（C50：删词后再查同词曾 500）
        existing = (
            self.db.query(Vocabulary)
            .filter(Vocabulary.child_id == child.id, Vocabulary.word == w)
            .first()
        )
        recorded = False
        if not existing:
            self.db.add(Vocabulary(child_id=child.id, word=w, book_id=book_id))
            recorded = True
        else:
            if existing.is_deleted:  # 删除后再查 → 复活收录
                existing.is_deleted = 0
                recorded = True
            if book_id and not existing.book_id:
                existing.book_id = book_id
        self.db.commit()
        return {
            "word": entry.word,
            "phonetic": entry.phonetic,
            "definition": entry.definition,
            "translation": entry.translation,
            "recorded": recorded,
        }

    def list_words(self, child: Child) -> list[dict]:
        rows = (
            self.db.query(Vocabulary)
            .filter(Vocabulary.child_id == child.id, Vocabulary.is_deleted == 0)
            .order_by(Vocabulary.id.desc())
            .all()
        )
        book_ids = {r.book_id for r in rows if r.book_id}
        books = (
            {b.id: b.title for b in self.db.query(Book).filter(Book.id.in_(book_ids)).all()}
            if book_ids
            else {}
        )
        return [
            {
                "id": r.id,
                "word": r.word,
                "book_id": r.book_id,
                "source_title": books.get(r.book_id, ""),
                "created_at": str(r.created_at),
            }
            for r in rows
        ]

    def remove(self, child: Child, vocabulary_id: int) -> None:
        row = (
            self.db.query(Vocabulary)
            .filter(
                Vocabulary.id == vocabulary_id,
                Vocabulary.child_id == child.id,
                Vocabulary.is_deleted == 0,
            )
            .first()
        )
        if not row:
            raise NotFoundError("生词不存在")
        row.is_deleted = 1
        self.db.commit()


class FavoriteService:
    """收藏夹（FEAT-056：想读清单；不限量不占额度；下架书可见标注）。"""

    def __init__(self, db: Session):
        self.db = db

    def list_mine(self, child: Child) -> list[dict]:
        rows = (
            self.db.query(Favorite, Book)
            .join(Book, Favorite.book_id == Book.id)
            .filter(Favorite.child_id == child.id, Favorite.is_deleted == 0)
            .order_by(Favorite.id.desc())
            .all()
        )
        return [
            {
                "id": f.id,
                "book_id": b.id,
                "title": b.title,
                "author": b.author,
                "word_count": b.word_count,
                "ar_level": b.ar_level,
                "cover_url": f"/api/miniapp/covers/{b.id}" if b.cover_path else None,
                "has_audio": bool(b.audio_path),
                "off_shelf": b.status != Book.STATUS_ON,
                "created_at": str(f.created_at),
            }
            for f, b in rows
        ]

    def add(self, child: Child, book_id: int) -> dict:
        book = self.db.query(Book).filter(Book.id == book_id, Book.is_deleted == 0).first()
        if not book:
            raise NotFoundError("图书不存在")
        # B5/D-4（插修5）：uq_fav_child_book 唯一索引不含 is_deleted——软删行占索引，
        # 复加 INSERT 撞索引 500。查含软删行：活跃行 409；软删行复活（对齐
        # 同文件 VocabularyService 词本正解，D-4 登记债真实命中）
        existing = (
            self.db.query(Favorite)
            .filter(Favorite.child_id == child.id, Favorite.book_id == book_id)
            .first()
        )
        if existing:
            if existing.is_deleted == 0:
                raise ConflictError("已收藏过这本书")
            existing.is_deleted = 0
            self.db.commit()
            return {"book_id": book_id, "title": book.title}
        self.db.add(Favorite(child_id=child.id, book_id=book_id))
        self.db.commit()
        return {"book_id": book_id, "title": book.title}

    def remove(self, child: Child, book_id: int) -> None:
        row = (
            self.db.query(Favorite)
            .filter(
                Favorite.child_id == child.id,
                Favorite.book_id == book_id,
                Favorite.is_deleted == 0,
            )
            .first()
        )
        if not row:
            raise NotFoundError("未收藏该书")
        row.is_deleted = 1
        self.db.commit()


class ShelfService:
    """书架：当前在借（借书自动上架、还书自动下架）。"""

    def __init__(self, db: Session):
        self.db = db

    def current_borrows(self, child: Child) -> list[dict]:
        now = datetime.now()
        rows = (
            self.db.query(BorrowRecord, Book)
            .join(Book, BorrowRecord.book_id == Book.id)
            .filter(
                BorrowRecord.child_id == child.id,
                BorrowRecord.status.in_([BorrowRecord.STATUS_ACTIVE, BorrowRecord.STATUS_OVERDUE]),
                BorrowRecord.is_deleted == 0,
            )
            .order_by(BorrowRecord.due_at)
            .all()
        )
        return [
            {
                "record_id": r.id,
                "book_id": b.id,
                "title": b.title,
                "author": b.author,
                "word_count": b.word_count,
                "cover_url": f"/api/miniapp/covers/{b.id}" if b.cover_path else None,
                "has_audio": bool(b.audio_path),
                "borrowed_at": str(r.borrowed_at),
                "due_at": str(r.due_at),
                "overdue": r.due_at < now,
            }
            for r, b in rows
        ]
