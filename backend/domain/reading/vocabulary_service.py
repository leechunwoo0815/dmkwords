# backend/domain/reading/vocabulary_service.py — 生词本与查词（WM8）
"""从 service.py 拆出（god file 800 行限制）：查词/生词本收录/移除。

引用路径保持：miniapp_router 经 service re-export 不变。
"""

from __future__ import annotations

from datetime import datetime, timedelta

from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from backend.common.exceptions import ConflictError, NotFoundError, ValidationError
from backend.common.file_utils import book_cover_url
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
        # R11-c/B10：book_id 必须指向真实书目——原先直接落库，summary 的"来源书目"
        # 会统计到不存在的书（列表 source_title 为空）。
        if book_id is not None and not (
            self.db.query(Book.id).filter(Book.id == book_id, Book.is_deleted == 0).first()
        ):
            raise NotFoundError("图书不存在")
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
            # 重复查同一个词累加次数（生词本的成就感来源；新词由 default=1 起算）
            # P2-7（2026-10-11 审查）：**原子 UPDATE**——原先读改写，并发查同一词会丢计数；
            # 与阅读圈 like_count 同款纪律（`col = col + 1` 交给数据库）。
            from sqlalchemy import func as _func

            from backend.domain.reading.models import Vocabulary as _Vocab

            self.db.query(_Vocab).filter(_Vocab.id == existing.id).update(
                {_Vocab.lookup_count: _func.coalesce(_Vocab.lookup_count, 0) + 1},
                synchronize_session=False,
            )
        try:
            self.db.commit()
        except IntegrityError:
            # B2/P2-4（第九轮审查）：并发双击/重放 → 后到者撞 `uq_vocab_child_word`（唯一索引
            # 不含 is_deleted）→ 原先裸 500。整笔回滚后按"已收录"幂等返回；
            # 代价：这一次的 `lookup_count` 累加随之回滚（两次并发查词只记 1 次）——比 500 好，
            # 且"累计查词"另有 `summary()` 的服务端聚合口径兜底。
            self.db.rollback()
            recorded = False
        return {
            "word": entry.word,
            "phonetic": entry.phonetic,
            "definition": entry.definition,
            "translation": entry.translation,
            "recorded": recorded,
        }

    #: 生词本列表每页条数（路由的 `page_size` 默认值与之同源，避免两处各写一个数）
    PAGE_SIZE = 20

    def list_words(self, child: Child, page: int = 1, page_size: int = PAGE_SIZE) -> list[dict]:
        """生词本列表（含释义/音标/查词次数）。**分页**（P3 第一百一十六批）。

        2026-09-15：原先只回 word + 来源书名，前端因此只能把「书名单行 + 生词」挤在
        同一行、同色同字号，且点单词没有任何可弹的东西——而词典（DictionaryWord）
        的音标/释义/翻译本来就在库。这里一次 join 出去，前端才能做闪卡。

        为什么补分页（第八轮 P3 实测）：原实现 `.all()` 全表回吐 + 前端整表 `setData`——
        生词本是**只增不减**的（查过的词自动收录），重度用户几年下来会把一次响应撑到
        几百 KB、`setData` 卡帧。分页后列表只渲染当前页，而「收录词数/累计查词/来源书目/
        本周新增/查过多次」五口径**全部**由 `summary()` 服务端聚合（本文件 `summary`
        的 docstring 早已写明"列表一旦分页/裁剪，前端 reduce 出来的数就与真实口径漂移"）。
        """
        rows = (
            self.db.query(Vocabulary)
            .filter(Vocabulary.child_id == child.id, Vocabulary.is_deleted == 0)
            .order_by(Vocabulary.id.desc())
            .offset(max(0, (page - 1) * page_size))
            .limit(page_size)
            .all()
        )
        book_ids = {r.book_id for r in rows if r.book_id}
        books = (
            {b.id: b.title for b in self.db.query(Book).filter(Book.id.in_(book_ids)).all()}
            if book_ids
            else {}
        )
        words = [r.word for r in rows]
        dicts = (
            {
                e.word: e
                for e in self.db.query(DictionaryWord).filter(
                    DictionaryWord.word.in_(words), DictionaryWord.is_deleted == 0
                )
            }
            if words
            else {}
        )
        out: list[dict] = []
        for r in rows:
            entry = dicts.get(r.word)
            out.append(
                {
                    "id": r.id,
                    "word": r.word,
                    "book_id": r.book_id,
                    "source_title": books.get(r.book_id, ""),
                    "lookup_count": r.lookup_count or 1,
                    "phonetic": (entry.phonetic if entry else "") or "",
                    "definition": (entry.definition if entry else "") or "",
                    "translation": (entry.translation if entry else "") or "",
                    "created_at": str(r.created_at),
                }
            )
        return out

    def summary(self, child: Child) -> dict:
        """生词本统计五口径（PRD §8.1.1 实现说明：**服务端聚合，前端不另算**）。

        BDD 解封暴露的缺口（2026-10-16）：早先这三个数字由小程序拿到列表后
        `reduce`/`Set` 现算——列表一旦分页/裁剪就与真实口径漂移，且同一数字两处算法。

        - `word_count`：当前**可见**生词条数（不含已移出）；
        - `lookup_total`：**历史全部**查词次数累加，**含已移出的词**——查过的次数是已发生的
          历史，删除不回退（否则"移出一个词"等于抹掉一段阅读足迹）；
        - `book_count`：当前可见生词的来源书数（同一本只算一次、未记来源的词不计）；
        - `week_count` / `repeat_count`（P3 第一百一十六批补）：**本周新增**与**查过多次**
          两枚展示指标。原先由前端对**整个列表**滤波现算，列表一分页这两个数就只剩
          当前页的结果（少得离谱）——按同一条纪律一并下沉到服务端。
        """
        from sqlalchemy import case as _case
        from sqlalchemy import func as _func

        word_count = (
            self.db.query(_func.count(Vocabulary.id))
            .filter(Vocabulary.child_id == child.id, Vocabulary.is_deleted == 0)
            .scalar()
            or 0
        )
        # 不加 is_deleted 过滤：软删行的次数仍计入（见 docstring 第二条）
        lookup_total = (
            self.db.query(_func.coalesce(_func.sum(Vocabulary.lookup_count), 0))
            .filter(Vocabulary.child_id == child.id)
            .scalar()
            or 0
        )
        book_count = (
            self.db.query(_func.count(_func.distinct(Vocabulary.book_id)))
            .filter(
                Vocabulary.child_id == child.id,
                Vocabulary.is_deleted == 0,
                Vocabulary.book_id.isnot(None),
            )
            .scalar()
            or 0
        )
        top = self.db.query(
            _func.coalesce(
                _func.sum(
                    _case((Vocabulary.created_at >= datetime.now() - timedelta(days=7), 1), else_=0)
                ),
                0,
            ),
            _func.coalesce(
                _func.sum(
                    _case(
                        (_func.coalesce(Vocabulary.lookup_count, 1) > 1, 1),
                        else_=0,
                    )
                ),
                0,
            ),
        ).filter(Vocabulary.child_id == child.id, Vocabulary.is_deleted == 0)
        week_count, repeat_count = (int(x or 0) for x in top.one())
        return {
            "word_count": int(word_count),
            "lookup_total": int(lookup_total),
            "book_count": int(book_count),
            "week_count": week_count,
            "repeat_count": repeat_count,
        }

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

    #: 列表硬上限（P2-44：经响应头如实告知）。收藏本身不限量，但一次取全量没有意义——
    #: 按收藏 id 倒序（最近收藏在前）取前 200 条，更早的本次不展示。
    LIST_LIMIT = 200

    def __init__(self, db: Session):
        self.db = db

    def list_mine(self, child: Child) -> list[dict]:
        rows = (
            self.db.query(Favorite, Book)
            .join(Book, Favorite.book_id == Book.id)
            .filter(
                Favorite.child_id == child.id,
                Favorite.is_deleted == 0,
                # P2-10（2026-10-17 第六十九批·大审查）：**软删书目不进收藏夹**。原先不过滤
                # `Book.is_deleted`，而软删书 status 仍是上架 → 卡片显示成正常书（off_shelf=false），
                # 点进详情 404。与全站"软删=对用户不存在"口径一致（续听/书架/列表都过滤它）。
                Book.is_deleted == 0,
            )
            .order_by(Favorite.id.desc())
            .limit(self.LIST_LIMIT)
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
                "cover_url": book_cover_url(b.id, b.cover_path),
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
        try:
            self.db.commit()
        except IntegrityError:
            # R11-c/B9：并发双击/重放 → 后到者撞 uq_fav_child_book（唯一索引不含 is_deleted）。
            # 原先裸 500；整笔回滚后按"已收藏"幂等返回（与 like/lookup 同款）。
            self.db.rollback()
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
        """当前在借（含续借元数据：家长自助续借的按钮依据，服务端算、前端不猜）。

        2026-10-13（第五十三轮 G6）：补 `renew_used/renew_limit/renew_days/can_renew`。
        `can_renew` 只判"未逾期 + 次数未用尽"（列表态）；冻结期/会员状态由提交时的
        `CirculationService.renew_self` 兜底把关——**不把提交时的判定复制到列表**（避免两套）。
        """
        from backend.domain.circulation.service import CirculationService

        now = datetime.now()
        meta = CirculationService(self.db).renew_meta()
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
                "cover_url": book_cover_url(b.id, b.cover_path),
                "has_audio": bool(b.audio_path),
                "borrowed_at": str(r.borrowed_at),
                "due_at": str(r.due_at),
                "overdue": r.due_at < now,
                "renew_used": r.renew_used,
                "renew_limit": meta["renew_limit"],
                "renew_days": meta["renew_days"],
                "can_renew": r.due_at >= now and r.renew_used < meta["renew_limit"],
            }
            for r, b in rows
        ]
