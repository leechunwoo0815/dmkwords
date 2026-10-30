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
from backend.common.events import (
    LevelAdvancedEvent,
    MilestoneAchievedEvent,
    event_bus,
)
from backend.common.exceptions import NotFoundError, ValidationError
from backend.domain.catalog.audit_events import publish_audit
from backend.domain.catalog.models import Book, QuizQuestion
from backend.domain.growth import quiz_queries
from backend.domain.growth.models import (
    ChildGrowthState,
    MilestoneAward,
    PointLedger,
    QuizAttempt,
    WordsLedger,
)
from backend.domain.growth.quiz_events import publish_quiz_result
from backend.domain.identity.models import Child

LEVEL_LETTERS = "ABCDEFGHIJKLMNOPQRSTUVWXYZ"


def levels_map(db: Session, child_ids: list[int]) -> dict[int, str]:
    """批量取孩子等级（fix34 R4：等级头像框的数据面）。

    **批查禁 N+1**——信息流/榜单/点赞头像墙都要等级时逐条查会打爆（头像墙同款纪律）。
    无成长状态的孩子按最低档 "A"（与前端 `frameForLevel` 兜底一致）。
    """
    from backend.domain.growth.models import ChildGrowthState

    if not child_ids:
        return {}
    rows = (
        db.query(ChildGrowthState.child_id, ChildGrowthState.level)
        .filter(
            ChildGrowthState.child_id.in_(child_ids),
            ChildGrowthState.is_deleted == 0,
        )
        .all()
    )
    return {int(cid): (lvl or "A") for cid, lvl in rows}


class GrowthService:
    #: 流水回看条数（P2-44：硬上限必须经响应头如实告知，前端才知道"还有更早的"）
    POINTS_LIST_LIMIT = 100
    WORDS_LIST_LIMIT = 100

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
            # B8（2026-10-28）：并发首访会同时通过"先查" → 后到者撞 uq_growth_child 整单 500。
            # 用 SAVEPOINT 兜底（冲突只回滚保存点），失败后重查拿对方插入的行。
            from backend.common.sql_utils import add_with_unique_fallback

            candidate = ChildGrowthState(child_id=child_id)
            if not add_with_unique_fallback(self.db, candidate):
                self.db.expunge(candidate)
            state = (
                self.db.query(ChildGrowthState)
                .filter(ChildGrowthState.child_id == child_id, ChildGrowthState.is_deleted == 0)
                .first()
            )
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
        # 2026-10-30 大审查 P2-2：积分是读改写，并发（手动调整/测验/打卡）会丢更新。
        # 用**原子自增**落库（不是"读绝对值再覆盖"），再把内存值同步成库内真值；
        # 这样即使两个事务基于同一旧快照，最终也是两次相加而不是后写覆盖。
        self.db.query(ChildGrowthState).filter(ChildGrowthState.id == state.id).update(
            {ChildGrowthState.points_total: ChildGrowthState.points_total + points},
            synchronize_session="fetch",
        )
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

    def _milestone_nodes(self) -> list[int]:
        """配置的里程碑节点（升序）。"""
        return sorted(
            int(n)
            for n in ConfigService(self.db).get_value("milestone_nodes").split(",")
            if n.strip()
        )

    def _check_milestones(self, state: ChildGrowthState) -> list[int]:
        """达成未发过的节点 → 补发勋章（永不回收）。返回本次新达成节点。"""
        nodes = self._milestone_nodes()
        awarded = {
            r.node_words
            for r in self.db.query(MilestoneAward.node_words)
            .filter(MilestoneAward.child_id == state.child_id, MilestoneAward.is_deleted == 0)
            .all()
        }
        new_nodes = [n for n in nodes if n <= state.words_total and n not in awarded]
        from backend.common.sql_utils import add_with_unique_fallback

        granted: list[int] = []
        for n in new_nodes:
            # B8：并发测验/补发会同时通过上面的 awarded 预取 → 撞 uq_milestone_child_node。
            # SAVEPOINT 兜底，冲突即跳过（勋章不重复发，也不整单 500）。
            if add_with_unique_fallback(
                self.db, MilestoneAward(child_id=state.child_id, node_words=n)
            ):
                granted.append(n)
        return granted

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
                    f"《{book.title}》小挑战满分",
                    related_id=book.id,
                )
                result["points_detail"].append({"type": "quiz_full_marks", "points": full_bonus})
            elif pass_bonus > 0:
                self._add_points(
                    state,
                    pass_bonus,
                    "quiz_first_pass",
                    f"《{book.title}》小挑战首次通过",
                    related_id=book.id,
                )
                result["points_detail"].append({"type": "quiz_first_pass", "points": pass_bonus})

        # 4) 等级 + 里程碑
        prev_level = state.level
        result["level_up"] = self._check_level_up(state)
        result["from_level"] = prev_level
        result["new_level"] = state.level
        result["new_milestones"] = self._check_milestones(state)
        return result

    def on_checkin(self, child_id: int, streak: int) -> list[dict]:
        """打卡周期奖（7/30 独立周期；幂等与周期口径见 `growth.checkin_awards`）。"""
        from backend.domain.growth.checkin_awards import award_checkin_cycles

        state = self.ensure_state(child_id)
        return award_checkin_cycles(self.db, state, child_id, streak, add_points=self._add_points)

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
        # 达成记录连**日期**一起取（2026-09-21）：护照/勋章页要显示"2026-09-21 达成"——
        # 只有节点数字的话，前端没法表达"什么时候拿到的"（成就感的另一半在时间上）。
        award_rows = (
            self.db.query(MilestoneAward.node_words, MilestoneAward.awarded_at)
            .filter(MilestoneAward.child_id == child.id, MilestoneAward.is_deleted == 0)
            .order_by(MilestoneAward.node_words)
            .all()
        )
        awarded_nodes = [r.node_words for r in award_rows]
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
            "milestone_awards": [
                {"node": r.node_words, "awarded_at": str(r.awarded_at)} for r in award_rows
            ],
            "is_z_capped": state.level == letters[-1],
        }

    def points_list(self, child_id: int, limit: int = POINTS_LIST_LIMIT) -> list[dict]:
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

    def words_list(self, child_id: int, limit: int = WORDS_LIST_LIMIT) -> list[dict]:
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
        """孩子全部书目测验状态（管理端）。

        P2-16（2026-10-17 第六十九批·大审查）：此前**不回 `total_questions`**，管理端只能用
        硬编码分母 5 渲染「最高分」——题量配置改成 8 就显示 `5/5` 满分假象；某书只有 3 题时
        显示 `3/5` 假未通过。分母取 **`QuizAttempt.total_questions`（那次作答的实际题量快照）**：
        服务端出题按 `min(题库启用题数, quiz_questions_per_book)` 截断（`_load_questions`），
        故"题库题数"与"卷面题数"并不相等——只有快照才如实反映那次得分是几分制；
        同分时取**最近**一次（题量配置改过就以后来的为准）。
        """
        from backend.domain.catalog.models import Book

        rows = (
            self.db.query(
                QuizAttempt.book_id,
                QuizAttempt.score,
                QuizAttempt.total_questions,
                QuizAttempt.id,
            )
            .filter(QuizAttempt.child_id == child_id, QuizAttempt.is_deleted == 0)
            .order_by(QuizAttempt.id)  # 升序：同分时后面的覆盖前面的 = 取最近一次
            .all()
        )
        # R11-c2/G3：最佳 = 正确率最高的一次（分子分母同源）；R11-c2/G4：书名只查用到的书
        from backend.domain.growth.quiz_stats import best_attempt_stats

        grouped: dict[int, list[tuple]] = {}
        for book_id, score, total, attempt_id in rows:
            grouped.setdefault(book_id, []).append((score, total, attempt_id))
        per_book: dict[int, dict] = {}
        for book_id, lst in grouped.items():
            best, total = best_attempt_stats(lst) or (0, 0)
            per_book[book_id] = {"used": len(lst), "best": best, "total": total}

        max_attempts = int(ConfigService(self.db).get_value("quiz_max_attempts"))
        book_ids = list(per_book)
        books = (
            {
                b.id: b
                for b in self.db.query(Book)
                .filter(Book.id.in_(book_ids), Book.is_deleted == 0)
                .all()
            }
            if book_ids
            else {}
        )
        words_books = {
            w.book_id
            for w in self.db.query(WordsLedger.book_id)
            .filter(WordsLedger.child_id == child_id, WordsLedger.is_deleted == 0)
            .all()
        }
        out = []
        for book_id, agg in per_book.items():
            out.append(
                {
                    "book_id": book_id,
                    "title": books[book_id].title if book_id in books else f"#{book_id}",
                    "attempts_used": agg["used"],
                    "best_score": agg["best"],
                    "max_attempts": max_attempts,
                    "total_questions": agg["total"],
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
        # 阈值调低后补发里程碑（按新口径）。
        # R11-c2/G4：抽到 `milestone_service`——一次取全量已授节点（原逐孩子查询 = N+1）。
        from backend.domain.growth.milestone_service import award_pending_milestones

        milestone_new = award_pending_milestones(self.db, states, self._milestone_nodes())
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
        # G16（2026-10-15 第六十一批）：PRD §4.4「下架后：测验 没开始的不能开始；已经通过的成绩保留」。
        # 此前开始/提交两条链都只过滤 `is_deleted`，下架书只要已解锁照样能考、还能入账（2026-10-15
        # 解封 catalog 场景时实测）。这里只拦**开始**：§4.4 原话约束的是"没开始的"——**已在做/已通过的
        # 不动**（已通过的词账与成绩由 `passed_before` / 词账读取链路原样保留，不回收）。
        if book.status != Book.STATUS_ON:
            raise ValidationError(f"《{book.title}》已下架，不能开始小挑战")
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
        # 2026-09-15：**已通过的书必须视为已解锁**。原判定只看 ReadingProgress.finished，
        # 而词账/测验记录可能早于（或独立于）进度存在 —— 例如榜单/里程碑造数只落词账。
        # 于是书架「已通过」列表里的书点进详情页反而显示「未开始听 → 已听 0% + 未解锁」，
        # 与列表自相矛盾（用户实测：3 本已通过里只有 1 本显示 PASSED）。
        # 通过测验的前提就是听完，所以「有词账」⇒「已解锁」。
        passed_before = (
            self.db.query(func.count(WordsLedger.id))
            .filter(
                WordsLedger.child_id == child.id,
                WordsLedger.book_id == book_id,
                WordsLedger.is_deleted == 0,
            )
            .scalar()
        ) > 0
        unlocked = bool(progress and progress.finished == 1) or passed_before
        max_attempts = int(ConfigService(self.db).get_value("quiz_max_attempts"))
        used = self._attempts_used(child.id, book_id)
        # 插修10：best_score 是答对题数口径（前端金卡曾显示"最佳成绩 5 分"+一星），
        # 补 best_percent 百分制口径；words_added/points_added 供 quiz-result 兜底
        # 显示真实到账（原硬编码 0 与护照片矛盾）
        best_attempt = quiz_queries.best_attempt(self.db, child.id, book_id)
        best = int(best_attempt.score) if best_attempt else 0
        best_percent = (
            round(best * 100 / best_attempt.total_questions)
            if best_attempt and best_attempt.total_questions
            else 0
        )
        words_added = (
            self.db.query(WordsLedger.word_count)
            .filter(
                WordsLedger.child_id == child.id,
                WordsLedger.book_id == book_id,
                WordsLedger.is_deleted == 0,
            )
            .scalar()
            or 0
        )
        points_added = quiz_queries.points_added_for_book(self.db, child.id, book_id)
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
            # P2-51（第八轮）：**下发该次作答的题量**。前端原先用"当前题量"
            # （`q.questions.length`）当分母——题量配置改小后，历史 best_score 除当前题量
            # 会算出 >100% 的正确率（星级/文案全错）。分母必须来自**那次作答**。
            "best_total": best_attempt.total_questions if best_attempt else 0,
            "best_percent": best_percent,
            "words_added": int(words_added),
            "points_added": int(points_added),
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
        child = (
            self.db.query(Child)
            .filter(Child.id == child.id)
            .with_for_update()
            .populate_existing()
            .first()
        )
        if child.operation_locked:
            raise ValidationError("孩子正在转让/退会审核流程中，新小挑战已冻结")
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
            raise ValidationError("需先听完音频（95%）才能开始小挑战")

        questions = self._load_questions(book_id)
        if not questions:
            raise ValidationError("该书暂无题目")
        if len(answers) != len(questions):
            raise ValidationError(f"答案数量不匹配（需 {len(questions)} 题）")

        max_attempts = int(ConfigService(self.db).get_value("quiz_max_attempts"))
        used = self._attempts_used(child.id, book_id)
        if used >= max_attempts:
            raise ValidationError(f"小挑战机会已用完（{max_attempts} 次）")

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

        # P1 #2（2026-10-15 大审查）：flush 取提交记录 id 作通知去重键——默认键会让同一孩子
        # 第 2 次起的成绩通知被唯一索引静默吞掉（PRD §十要「每次提交后」）。
        attempt = QuizAttempt(
            child_id=child.id,
            book_id=book_id,
            score=score,
            total_questions=total_q,
            passed=passed,
            snapshot=json.dumps(snapshot, ensure_ascii=False),
            submitted_at=datetime.now(),
        )
        self.db.add(attempt)
        self.db.flush()

        result = {
            "score": score,
            "total": total_q,
            "passed": bool(passed),
            "best_score": max(score, quiz_queries.best_score(self.db, child.id, book_id)),
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

        # WM11：测验成绩 / 等级升级 / 里程碑达成 通知事件（同一事务）
        publish_quiz_result(
            self.db,
            child=child,
            book=book,
            passed=bool(passed),
            score=score,
            submission_id=attempt.id,  # 通知去重键（P1 #2：每次提交一条）
        )
        if result.get("level_up"):
            event_bus.publish(
                LevelAdvancedEvent(
                    child_id=child.id,
                    from_level=result.get("from_level", ""),
                    to_level=result.get("new_level", ""),
                ),
                db=self.db,
            )
        if result.get("new_milestones"):
            event_bus.publish(
                MilestoneAchievedEvent(
                    child_id=child.id,
                    nodes=tuple(result["new_milestones"]),
                ),
                db=self.db,
            )
        # FE-2（2026-10-28）：提交响应补齐成绩单字段——小程序把本次响应直接缓存给结果页，
        # 缺 best_percent/points_added 会让金卡正确率与积分明细要么空白、要么走另一条
        # 服务端兜底路径（两条路径口径不一致）。复用状态页同一查询（不新增来源）。
        best_attempt = quiz_queries.best_attempt(self.db, child.id, book_id)
        result["best_percent"] = (
            round(best_attempt.score * 100 / best_attempt.total_questions)
            if best_attempt and best_attempt.total_questions
            else 0
        )
        result["attempts_used"] = used + 1
        result["points_added"] = quiz_queries.points_added_for_book(self.db, child.id, book_id)
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
