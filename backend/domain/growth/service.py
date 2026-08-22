# backend/domain/growth/service.py — 测验/词数/积分/等级/里程碑
"""红线对齐：
- Quiz 公平性：3 次终身机会；未提交不占次；取最高分；快照保真
- 有效词数：测验通过才入账；学生×书目唯一；永不回收
- 积分：三通道只加不减；零头池滚动；周期奖防重发
- 等级：只升不降；Z 封顶继续累计
事务纪律：Service 统一 commit；跨域留痕走审计事件。
"""

from __future__ import annotations

import json
from datetime import datetime

from sqlalchemy import func
from sqlalchemy.orm import Session

from backend.common.config_service import ConfigService
from backend.common.exceptions import NotFoundError, ValidationError
from backend.domain.catalog.audit_events import publish_audit
from backend.domain.catalog.models import Book, QuizQuestion
from backend.domain.growth.models import (
    CheckinStreakRecord,
    ChildGrowthState,
    MilestoneAward,
    PointLedger,
    QuizAttempt,
    WordsLedger,
)
from backend.domain.identity.models import Child

LEVEL_LETTERS = "ABCDEFGHIJKLMNOPQRSTUVWXYZ"


class GrowthService:
    def __init__(self, db: Session):
        self.db = db

    # ---------- 汇总状态 ----------
    def ensure_state(self, child_id: int) -> ChildGrowthState:
        state = (
            self.db.query(ChildGrowthState)
            .filter(ChildGrowthState.child_id == child_id, ChildGrowthState.is_deleted == 0)
            .first()
        )
        if not state:
            state = ChildGrowthState(child_id=child_id)
            self.db.add(state)
            self.db.flush()
        return state

    def _add_points(
        self,
        state: ChildGrowthState,
        points: int,
        reason_type: str,
        detail: str,
        related_id: int | None = None,
        operator_id: int | None = None,
    ) -> None:
        state.points_total += points
        self.db.add(
            PointLedger(
                child_id=state.child_id,
                points=points,
                reason_type=reason_type,
                detail=detail,
                related_id=related_id,
                operator_id=operator_id,
            )
        )

    def _check_level_up(self, state: ChildGrowthState) -> bool:
        """升级判定（只升不降；Z 封顶继续累计本数）。返回是否升级。"""
        threshold = int(ConfigService(self.db).get_value("level_up_books"))
        if threshold <= 0:
            return False
        target_idx = min(state.books_total // threshold, len(LEVEL_LETTERS) - 1)
        current_idx = LEVEL_LETTERS.index(state.level) if state.level in LEVEL_LETTERS else 0
        if target_idx > current_idx:
            state.level = LEVEL_LETTERS[target_idx]
            return True
        return False

    def _check_milestones(self, state: ChildGrowthState) -> list[int]:
        """达成未发过的节点 → 补发勋章（永不回收）。返回本次新达成节点。"""
        nodes = [
            int(n)
            for n in ConfigService(self.db).get_value("milestone_nodes").split(",")
            if n.strip()
        ]
        awarded = {
            r.node_words
            for r in self.db.query(MilestoneAward.node_words)
            .filter(MilestoneAward.child_id == state.child_id, MilestoneAward.is_deleted == 0)
            .all()
        }
        new_nodes = [n for n in sorted(nodes) if n <= state.words_total and n not in awarded]
        for n in new_nodes:
            self.db.add(MilestoneAward(child_id=state.child_id, node_words=n))
        return new_nodes

    def on_quiz_passed(self, child: Child, book: Book, score: int, total_q: int) -> dict:
        """测验通过后的入账链（同事务）：词数→积分折算（零头池）→首过/满分奖→等级→里程碑。"""
        state = self.ensure_state(child.id)
        result: dict = {"points_detail": []}

        # 1) 词数入账（调用方已保证唯一）
        state.words_total += book.word_count
        state.books_total += 1
        self.db.add(WordsLedger(child_id=child.id, book_id=book.id, word_count=book.word_count))
        result["words_added"] = book.word_count

        # 2) 词数折算积分（零头池滚动）
        per_point = int(ConfigService(self.db).get_value("words_per_point"))
        if per_point > 0:
            pool = state.words_remainder + book.word_count
            earn = pool // per_point
            if earn > 0:
                state.words_remainder = pool % per_point
                self._add_points(
                    state,
                    earn,
                    "words_convert",
                    f"《{book.title}》入账 {book.word_count} 词折算",
                    related_id=book.id,
                )
                result["points_detail"].append({"type": "words_convert", "points": earn})
            else:
                state.words_remainder = pool

        # 3) 测验奖励（每本书只发一次；满分与首过互斥取高）
        bonus_exists = (
            self.db.query(func.count(PointLedger.id))
            .filter(
                PointLedger.child_id == child.id,
                PointLedger.reason_type.in_(["quiz_first_pass", "quiz_full_marks"]),
                PointLedger.related_id == book.id,
                PointLedger.is_deleted == 0,
            )
            .scalar()
        )
        if not bonus_exists:
            full_bonus = int(ConfigService(self.db).get_value("quiz_full_marks_bonus"))
            pass_bonus = int(ConfigService(self.db).get_value("quiz_pass_bonus"))
            if score == total_q and full_bonus > 0:
                self._add_points(
                    state,
                    full_bonus,
                    "quiz_full_marks",
                    f"《{book.title}》测验满分",
                    related_id=book.id,
                )
                result["points_detail"].append({"type": "quiz_full_marks", "points": full_bonus})
            elif pass_bonus > 0:
                self._add_points(
                    state,
                    pass_bonus,
                    "quiz_first_pass",
                    f"《{book.title}》测验首次通过",
                    related_id=book.id,
                )
                result["points_detail"].append({"type": "quiz_first_pass", "points": pass_bonus})

        # 4) 等级 + 里程碑
        result["level_up"] = self._check_level_up(state)
        result["new_level"] = state.level
        result["new_milestones"] = self._check_milestones(state)
        return result

    def on_checkin(self, child_id: int, streak: int) -> list[dict]:
        """打卡周期奖（7/30 独立周期；防重复发）。"""
        state = self.ensure_state(child_id)
        awarded: list[dict] = []
        for cycle_days, key, rtype in (
            (7, "checkin_7days_bonus", "checkin_7"),
            (30, "checkin_30days_bonus", "checkin_30"),
        ):
            if streak > 0 and streak % cycle_days == 0:
                cycle_no = streak // cycle_days
                dup = (
                    self.db.query(func.count(CheckinStreakRecord.id))
                    .filter(
                        CheckinStreakRecord.child_id == child_id,
                        CheckinStreakRecord.cycle_type == f"days{cycle_days}",
                        CheckinStreakRecord.cycle_no == cycle_no,
                        CheckinStreakRecord.is_deleted == 0,
                    )
                    .scalar()
                )
                if not dup:
                    bonus = int(ConfigService(self.db).get_value(key))
                    if bonus > 0:
                        self._add_points(
                            state,
                            bonus,
                            rtype,
                            f"连续打卡 {streak} 天（第 {cycle_no} 个 {cycle_days} 天周期）",
                        )
                        self.db.add(
                            CheckinStreakRecord(
                                child_id=child_id,
                                cycle_type=f"days{cycle_days}",
                                cycle_no=cycle_no,
                                streak_at=streak,
                            )
                        )
                        awarded.append({"type": rtype, "points": bonus})
        return awarded

    # ---------- 汇总视图 ----------
    def summary(self, child: Child) -> dict:
        """成长汇总（等级进度 + 里程碑 + 积分 + 零头池）。"""
        state = self.ensure_state(child.id)
        threshold = int(ConfigService(self.db).get_value("level_up_books"))
        letters = LEVEL_LETTERS
        level_idx = letters.index(state.level) if state.level in letters else 0
        nodes = [
            int(n)
            for n in ConfigService(self.db).get_value("milestone_nodes").split(",")
            if n.strip()
        ]
        awarded_nodes = [
            r.node_words
            for r in self.db.query(MilestoneAward.node_words)
            .filter(MilestoneAward.child_id == child.id, MilestoneAward.is_deleted == 0)
            .order_by(MilestoneAward.node_words)
            .all()
        ]
        # 词数/本数/积分以流水为唯一事实源（避免 state 与 ledger 漂移）
        agg = (
            self.db.query(
                func.coalesce(func.sum(WordsLedger.word_count), 0), func.count(WordsLedger.id)
            )
            .filter(WordsLedger.child_id == child.id, WordsLedger.is_deleted == 0)
            .one()
        )
        words_total, books_total = int(agg[0]), int(agg[1])
        points_total = (
            self.db.query(func.coalesce(func.sum(PointLedger.points), 0))
            .filter(PointLedger.child_id == child.id, PointLedger.is_deleted == 0)
            .scalar()
        )
        return {
            "child_id": child.id,
            "child_name": child.name,
            "words_total": words_total,
            "books_total": books_total,
            "points_total": int(points_total),
            "words_remainder": state.words_remainder,
            "level": state.level,
            "level_books_threshold": threshold,
            "progress_in_level": max(
                0, books_total - (level_idx * threshold) if level_idx < len(letters) - 1 else 0
            ),
            "milestone_nodes": nodes,
            "milestones_awarded": awarded_nodes,
            "is_z_capped": state.level == letters[-1],
        }

    def points_list(self, child_id: int, limit: int = 100) -> list[dict]:
        rows = (
            self.db.query(PointLedger)
            .filter(PointLedger.child_id == child_id, PointLedger.is_deleted == 0)
            .order_by(PointLedger.id.desc())
            .limit(limit)
            .all()
        )
        return [
            {
                "id": r.id,
                "points": r.points,
                "reason_type": r.reason_type,
                "detail": r.detail,
                "created_at": str(r.created_at),
            }
            for r in rows
        ]

    def words_list(self, child_id: int, limit: int = 100) -> list[dict]:
        from backend.domain.catalog.models import Book

        rows = (
            self.db.query(WordsLedger, Book)
            .join(Book, WordsLedger.book_id == Book.id)
            .filter(WordsLedger.child_id == child_id, WordsLedger.is_deleted == 0)
            .order_by(WordsLedger.id.desc())
            .limit(limit)
            .all()
        )
        return [
            {
                "id": w.id,
                "book_id": w.book_id,
                "title": b.title,
                "word_count": w.word_count,
                "created_at": str(w.created_at),
            }
            for w, b in rows
        ]

    def quiz_overview(self, child_id: int) -> list[dict]:
        """孩子全部书目测验状态（管理端）。"""
        from backend.domain.catalog.models import Book

        rows = (
            self.db.query(
                QuizAttempt.book_id,
                func.count(QuizAttempt.id),
                func.max(QuizAttempt.score),
            )
            .filter(QuizAttempt.child_id == child_id, QuizAttempt.is_deleted == 0)
            .group_by(QuizAttempt.book_id)
            .all()
        )
        max_attempts = int(ConfigService(self.db).get_value("quiz_max_attempts"))
        books = {b.id: b for b in self.db.query(Book).filter(Book.is_deleted == 0).all()}
        words_books = {
            w.book_id
            for w in self.db.query(WordsLedger.book_id)
            .filter(WordsLedger.child_id == child_id, WordsLedger.is_deleted == 0)
            .all()
        }
        out = []
        for book_id, used, best in rows:
            out.append(
                {
                    "book_id": book_id,
                    "title": books[book_id].title if book_id in books else f"#{book_id}",
                    "attempts_used": int(used),
                    "best_score": int(best or 0),
                    "max_attempts": max_attempts,
                    "passed": book_id in words_books,
                }
            )
        return out

    def child_growth(self, child_id: int) -> dict:
        """管理端成长档案（汇总 + 词数流水 + 积分明细 + 测验状态）。"""
        child = self.db.query(Child).filter(Child.id == child_id, Child.is_deleted == 0).first()
        if not child:
            raise NotFoundError("孩子不存在")
        return {
            "summary": self.summary(child),
            "words_ledger": self.words_list(child_id),
            "points_ledger": self.points_list(child_id),
            "quiz_overview": self.quiz_overview(child_id),
        }

    # ---------- 管理端 ----------
    def adjust_points(self, admin, child_id: int, points: int, reason: str) -> dict:
        if points == 0:
            raise ValidationError("调整积分不能为 0")
        if not reason or not reason.strip():
            raise ValidationError("调整积分必须填写原因")
        child = self.db.query(Child).filter(Child.id == child_id, Child.is_deleted == 0).first()
        if not child:
            raise NotFoundError("孩子不存在")
        state = self.ensure_state(child_id)
        self._add_points(state, points, "manual_adjust", reason.strip(), operator_id=admin.id)
        publish_audit(
            self.db,
            admin=admin,
            action="growth.points_adjust",
            target_type="child",
            target_id=str(child_id),
            detail={"points": points, "after": state.points_total},
            reason=reason.strip(),
        )
        self.db.commit()
        return {"child_id": child_id, "points_total": state.points_total}

    def recalc_levels(self, admin) -> dict:
        """等级阈值变更后的全量重算（幂等、只升不降）。"""
        threshold = int(ConfigService(self.db).get_value("level_up_books"))
        if threshold <= 0:
            raise ValidationError("level_up_books 必须为正整数")
        states = self.db.query(ChildGrowthState).filter(ChildGrowthState.is_deleted == 0).all()
        changed = 0
        for state in states:
            target_idx = min(state.books_total // threshold, len(LEVEL_LETTERS) - 1)
            current_idx = LEVEL_LETTERS.index(state.level) if state.level in LEVEL_LETTERS else 0
            if target_idx > current_idx:
                state.level = LEVEL_LETTERS[target_idx]
                changed += 1
        # 阈值调低后补发里程碑（按新口径）
        milestone_new = 0
        for state in states:
            milestone_new += len(self._check_milestones(state))
        publish_audit(
            self.db,
            admin=admin,
            action="growth.level_recalc",
            target_type="config",
            target_id="level_up_books",
            detail={
                "threshold": threshold,
                "level_changed": changed,
                "milestone_new": milestone_new,
            },
            reason="等级阈值变更重算",
        )
        self.db.commit()
        return {
            "threshold": threshold,
            "states": len(states),
            "level_changed": changed,
            "milestone_new": milestone_new,
        }

    def check_milestones_now(self, admin, child_id: int) -> list[int]:
        """手动触发里程碑补发（配置调低节点后用）。"""
        state = self.ensure_state(child_id)
        new_nodes = self._check_milestones(state)
        publish_audit(
            self.db,
            admin=admin,
            action="growth.milestone_check",
            target_type="child",
            target_id=str(child_id),
            detail={"new_nodes": new_nodes},
            reason="里程碑节点核对",
        )
        self.db.commit()
        return new_nodes


class QuizService:
    def __init__(self, db: Session):
        self.db = db
        self.growth = GrowthService(db)

    # ---------- 查询 ----------
    def _attempts_used(self, child_id: int, book_id: int) -> int:
        return (
            self.db.query(func.count(QuizAttempt.id))
            .filter(
                QuizAttempt.child_id == child_id,
                QuizAttempt.book_id == book_id,
                QuizAttempt.is_deleted == 0,
            )
            .scalar()
        )

    def _best_score(self, child_id: int, book_id: int) -> int:
        best = (
            self.db.query(func.max(QuizAttempt.score))
            .filter(
                QuizAttempt.child_id == child_id,
                QuizAttempt.book_id == book_id,
                QuizAttempt.is_deleted == 0,
            )
            .scalar()
        )
        return int(best or 0)

    def _load_questions(self, book_id: int) -> list[QuizQuestion]:
        limit = int(ConfigService(self.db).get_value("quiz_questions_per_book"))
        return (
            self.db.query(QuizQuestion)
            .filter(
                QuizQuestion.book_id == book_id,
                QuizQuestion.is_active == 1,
                QuizQuestion.is_deleted == 0,
            )
            .order_by(QuizQuestion.sort_order, QuizQuestion.id)
            .limit(limit)
            .all()
        )

    def get_quiz(self, child: Child, book_id: int) -> dict:
        book = self.db.query(Book).filter(Book.id == book_id, Book.is_deleted == 0).first()
        if not book:
            raise NotFoundError("图书不存在")
        from backend.domain.reading.models import ReadingProgress

        progress = (
            self.db.query(ReadingProgress)
            .filter(
                ReadingProgress.child_id == child.id,
                ReadingProgress.book_id == book_id,
                ReadingProgress.is_deleted == 0,
            )
            .first()
        )
        unlocked = bool(progress and progress.finished == 1)
        max_attempts = int(ConfigService(self.db).get_value("quiz_max_attempts"))
        used = self._attempts_used(child.id, book_id)
        best = self._best_score(child.id, book_id)
        passed_before = (
            self.db.query(func.count(WordsLedger.id))
            .filter(
                WordsLedger.child_id == child.id,
                WordsLedger.book_id == book_id,
                WordsLedger.is_deleted == 0,
            )
            .scalar()
        ) > 0
        if not unlocked:
            status = "locked"
        elif passed_before:
            status = "passed"
        elif used >= max_attempts:
            status = "failed"
        else:
            status = "available"
        return {
            "book_id": book_id,
            "book_title": book.title,
            "unlocked": unlocked,
            "status": status,
            "attempts_used": used,
            "attempts_left": max(0, max_attempts - used),
            "best_score": best,
            "max_attempts": max_attempts,
            "questions": [
                {
                    "id": q.id,
                    "type": q.question_type,
                    "text": q.question_text,
                    "options": json.loads(q.options),
                }
                for q in self._load_questions(book_id)
            ],
        }

    # ---------- 提交 ----------
    def submit(self, child: Child, book_id: int, answers: list[str]) -> dict:
        # 锁主体行：同一孩子的并发提交串行化
        child = self.db.query(Child).filter(Child.id == child.id).with_for_update().first()
        if child.operation_locked:
            raise ValidationError("孩子正在转让/退会审核流程中，新测验已冻结")
        book = self.db.query(Book).filter(Book.id == book_id, Book.is_deleted == 0).first()
        if not book:
            raise NotFoundError("图书不存在")

        # 解锁校验（红线：读完才可测，不等还书）
        from backend.domain.reading.models import ReadingProgress

        progress = (
            self.db.query(ReadingProgress)
            .filter(
                ReadingProgress.child_id == child.id,
                ReadingProgress.book_id == book_id,
                ReadingProgress.is_deleted == 0,
            )
            .first()
        )
        if not progress or progress.finished != 1:
            raise ValidationError("需先听完音频（95%）才能开始测验")

        questions = self._load_questions(book_id)
        if not questions:
            raise ValidationError("该书暂无题目")
        if len(answers) != len(questions):
            raise ValidationError(f"答案数量不匹配（需 {len(questions)} 题）")

        max_attempts = int(ConfigService(self.db).get_value("quiz_max_attempts"))
        used = self._attempts_used(child.id, book_id)
        if used >= max_attempts:
            raise ValidationError("测验机会已用完（3 次）")

        pass_percent = int(ConfigService(self.db).get_value("quiz_pass_percent"))

        # 评分 + 快照
        snapshot, score = [], 0
        for q, ans in zip(questions, answers, strict=False):
            correct = q.answer == ans
            score += 1 if correct else 0
            snapshot.append(
                {
                    "question_id": q.id,
                    "text": q.question_text,
                    "options": json.loads(q.options),
                    "answer": q.answer,
                    "user_answer": ans,
                    "correct": correct,
                }
            )
        total_q = len(questions)
        passed = 1 if (total_q > 0 and score * 100 >= total_q * pass_percent) else 0

        self.db.add(
            QuizAttempt(
                child_id=child.id,
                book_id=book_id,
                score=score,
                total_questions=total_q,
                passed=passed,
                snapshot=json.dumps(snapshot, ensure_ascii=False),
                submitted_at=datetime.now(),
            )
        )

        result = {
            "score": score,
            "total": total_q,
            "passed": bool(passed),
            "best_score": max(score, self._best_score(child.id, book_id)),
            "attempts_left": max_attempts - used - 1,
            "wrong": [s for s in snapshot if not s["correct"]],
            "just_passed": False,
            "words_added": 0,
            "points_detail": [],
            "level_up": False,
            "new_milestones": [],
        }

        # 通过且首次 → 同事务入账链
        if passed:
            exists = (
                self.db.query(func.count(WordsLedger.id))
                .filter(
                    WordsLedger.child_id == child.id,
                    WordsLedger.book_id == book_id,
                    WordsLedger.is_deleted == 0,
                )
                .scalar()
            )
            if not exists:
                growth = self.growth.on_quiz_passed(child, book, score, total_q)
                result.update(growth)
                result["just_passed"] = True
        self.db.commit()
        return result

    def reset_attempts(self, admin, child_id: int, book_id: int, reason: str) -> dict:
        """超管重置测验次数：软删历史提交（成绩只能孩子自己做，任何人不许代标）。"""
        if not reason or not reason.strip():
            raise ValidationError("重置测验次数必须填写原因")
        child = self.db.query(Child).filter(Child.id == child_id, Child.is_deleted == 0).first()
        if not child:
            raise NotFoundError("孩子不存在")
        book = self.db.query(Book).filter(Book.id == book_id, Book.is_deleted == 0).first()
        if not book:
            raise NotFoundError("图书不存在")
        attempts = (
            self.db.query(QuizAttempt)
            .filter(
                QuizAttempt.child_id == child_id,
                QuizAttempt.book_id == book_id,
                QuizAttempt.is_deleted == 0,
            )
            .all()
        )
        for a in attempts:
            a.is_deleted = 1
        publish_audit(
            self.db,
            admin=admin,
            action="quiz.attempts_reset",
            target_type="child",
            target_id=str(child_id),
            detail={"book": book.title, "cleared": len(attempts)},
            reason=reason.strip(),
        )
        self.db.commit()
        return {
            "child_id": child_id,
            "book_id": book_id,
            "cleared": len(attempts),
            "attempts_left": int(ConfigService(self.db).get_value("quiz_max_attempts")),
        }
