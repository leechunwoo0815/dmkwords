# backend/domain/catalog/quiz_service.py — 测验题目管理（从 service.py 抽出）
"""为什么单开：`catalog/service.py` 触架构关 god-file 上限（>800 行，第六十九批）。

题目 CRUD + 审计留痕；调用方通过 `catalog.service` 的 re-export 继续可用。
"""

from __future__ import annotations

import json

from sqlalchemy import func
from sqlalchemy.orm import Session

from backend.common.exceptions import NotFoundError, ValidationError
from backend.domain.catalog.audit_events import publish_audit
from backend.domain.catalog.models import QuizQuestion
from backend.domain.catalog.repository import QuizQuestionRepository


class QuizQuestionService:
    def __init__(self, db: Session):
        self.db = db
        self.question_repo = QuizQuestionRepository(db)

    def _audit(self, admin, action: str, book_id: int, question_id: int, detail: dict) -> None:
        """R9：题目写操作留痕（影响 WM7 计分，必须可追溯）。"""
        publish_audit(
            self.db,
            admin=admin,
            action=action,
            target_type="book",
            target_id=str(book_id),
            detail={"question_id": question_id, **detail},
            reason="测验题库",
        )

    def _ensure_book(self, book_id: int) -> None:
        """R11-c/G7：题目必须挂在真实书目上——否则可造孤儿题（无 FK），列表还回 200 []。"""
        from backend.domain.catalog.models import Book

        if not self.db.query(Book.id).filter(Book.id == book_id, Book.is_deleted == 0).first():
            raise NotFoundError("图书不存在")

    def list_by_book(self, book_id: int) -> list[QuizQuestion]:
        self._ensure_book(book_id)
        return self.question_repo.list_by_book(book_id, active_only=False)

    def _validate_question(self, question_type: str, options: list[str], answer: str) -> list[str]:
        if question_type == QuizQuestion.TYPE_BOOLEAN:
            valid_options = ["对", "错"]
            if [o.strip() for o in options] != valid_options:
                raise ValidationError("判断题选项固定为 [对, 错]")
        else:
            if len(options) < 2:
                raise ValidationError("单选题至少 2 个选项")
        if answer not in options:
            raise ValidationError("正确答案必须是选项之一")
        return answer

    def create(self, admin, book_id: int, req) -> QuizQuestion:
        self._ensure_book(book_id)
        self._validate_question(req.question_type, req.options, req.answer)
        # C29：序号由服务端分配（max(sort_order)+1），前端不再传（避免删除后重号）
        max_sort = (
            self.db.query(func.max(QuizQuestion.sort_order))
            .filter(
                QuizQuestion.book_id == book_id,
                QuizQuestion.is_deleted == 0,
            )
            .scalar()
        )
        q = QuizQuestion(
            book_id=book_id,
            question_type=req.question_type,
            question_text=req.question_text.strip(),
            options=json.dumps(req.options, ensure_ascii=False),
            answer=req.answer,
            sort_order=(max_sort or 0) + 1,
        )
        self.question_repo.create(q)
        self._audit(
            admin,
            "quiz.create",
            book_id,
            q.id,
            {"question_type": q.question_type, "question_text": q.question_text},
        )
        self.db.commit()
        return q

    def update(self, admin, question_id: int, req) -> QuizQuestion:
        """C28：编辑题目（题干/类型/选项/答案；sort_order 不动）。"""
        q = self.question_repo.get_by_id_or_raise(question_id)
        self._validate_question(req.question_type, req.options, req.answer)
        q.question_type = req.question_type
        q.question_text = req.question_text.strip()
        q.options = json.dumps(req.options, ensure_ascii=False)
        q.answer = req.answer
        self.question_repo.update(q)
        self._audit(
            admin,
            "quiz.update",
            q.book_id,
            q.id,
            {
                "question_type": q.question_type,
                "question_text": q.question_text,
                "answer": q.answer,
            },
        )
        self.db.commit()
        return q

    def toggle_active(self, admin, question_id: int) -> QuizQuestion:
        q = self.question_repo.get_by_id_or_raise(question_id)
        q.is_active = 0 if q.is_active == 1 else 1
        self.question_repo.update(q)
        self._audit(admin, "quiz.toggle", q.book_id, q.id, {"is_active": q.is_active})
        self.db.commit()
        return q

    def delete(self, admin, question_id: int) -> None:
        q = self.question_repo.get_by_id_or_raise(question_id)
        self.question_repo.soft_delete(question_id)
        self._audit(admin, "quiz.delete", q.book_id, q.id, {"question_text": q.question_text})
        self.db.commit()
