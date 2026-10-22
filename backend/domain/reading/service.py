# backend/domain/reading/service.py — 完播判定（区间并集）/ 打卡 / 预约
"""防刷核心（红线：音频完播防刷）：
- 区间并集合并（重复段只计一次，seek 跳过不计）
- 覆盖增速校验（PRD R-151）：Δ覆盖 ≤ 服务端时间差 × 2.0（最大倍速）× 1.2（容差）+ 宽限
  服务端时间差 = 距上次上报的墙上时钟（声明跨度可伪造，不作依据）
- 完播 = 覆盖/总时长 ≥ 95%（阈值进配置）
"""

from __future__ import annotations

import json
from datetime import date, datetime, timedelta

from sqlalchemy import func
from sqlalchemy.orm import Session

from backend.common.config_service import ConfigService
from backend.common.exceptions import NotFoundError, ValidationError
from backend.domain.catalog.models import Book
from backend.domain.circulation.models import BorrowRecord
from backend.domain.identity.models import Child
from backend.domain.reading.models import (
    CheckIn,
    ReadingProgress,
)

MAX_SPEED = 2.0  # PRD D20：倍速五档上限
SPEED_TOLERANCE = 1.2  # R-151：容差
REPORT_GRACE_SECONDS = (
    60  # **仅首笔上报**的宽限（对时/首帧抖动）；心跳 10s 的理论上限为 10×2.0×1.2=24s
)


# 进度区间/进度行工具已抽至 progress_utils.py（第六十九批：service.py 触 800 行上限）——
# 保持 re-export，circulation 与测试的既有 import 路径不变。
from backend.domain.reading.progress_utils import (  # noqa: E402,F401 — 兼容旧引用
    coverage_of,
    ensure_progress_row,
    merge_intervals,
)


class ReadingService:
    def __init__(self, db: Session):
        self.db = db

    def report_progress(
        self, child: Child, book_id: int, position: int, session_start: int | None = None
    ) -> dict:
        """小程序播放心跳上报。

        position: 当前播放位置（秒）；session_start: 本次会话起始位置（用于构造区间）。
        服务端记录 [session_start, position] 区间（含 seek 段由客户端如实上报）。
        """
        book = (
            self.db.query(Book)
            .filter(Book.id == book_id, Book.is_deleted == 0, Book.status == Book.STATUS_ON)
            .first()
        )
        if not book:
            raise NotFoundError("图书不存在或已下架")
        if not book.audio_path or not book.audio_duration_seconds:
            raise ValidationError("该书暂无音频")
        # 会员权限已收口 guards.AUDIO（R2/C-13——Router 层统一守卫，此处删除
        # 散落判定防两处漂移；Router 已 require_member_action(AUDIO) 403）
        total = book.audio_duration_seconds
        if position < 0 or position > total + 5:
            raise ValidationError(f"播放位置异常（0-{total}）")

        progress = (
            self.db.query(ReadingProgress)
            .filter(
                ReadingProgress.child_id == child.id,
                ReadingProgress.book_id == book_id,
                ReadingProgress.is_deleted == 0,
            )
            # 2026-10-12 审查 P1：**必须锁行读**——本方法是"读快照 → 合并区间 → 整列覆盖写"，
            # 无锁时并发心跳（双开播放器/心跳重试）各自基于同一陈旧快照写回，后到者会把先到者的
            # 区间整段覆盖掉（确定性两会话演示：终态 `[[30,60]]`/coverage 30，期望并集 60）
            # → 家长侧表现为"读完的书又没读完"；反向还会因陈旧 `old_cov` 把合法上报判成"播放数据异常"。
            # `populate_existing()` 是项目锁定读的统一纪律（防旧快照）。
            .with_for_update()
            .populate_existing()
            .first()
        )
        if not progress:
            # P1-8（2026-10-09 审查）：**原子插入**——原 check-then-insert 在并发心跳重试/双开下
            # 会落两行同一 (child, book)，进度被 `first()` 随机劈开 → 覆盖率永远到不了 95%、
            # 测验永久解不开（实测）。唯一索引（uq_reading_progress_child_book）是最终防线，
            # ON DUPLICATE KEY 让并发只有一个赢家，随后重读拿到那一行（可能是别人刚插的）。
            from sqlalchemy.dialects.mysql import insert as mysql_insert

            self.db.execute(
                mysql_insert(ReadingProgress)
                .values(child_id=child.id, book_id=book_id, total_seconds=total, is_deleted=0)
                .on_duplicate_key_update(id=ReadingProgress.id)  # 空更新：保留既有行
            )
            progress = (
                self.db.query(ReadingProgress)
                .filter(
                    ReadingProgress.child_id == child.id,
                    ReadingProgress.book_id == book_id,
                    ReadingProgress.is_deleted == 0,
                )
                .with_for_update()  # 同上：拿到锁再读，后续合并/覆盖写才安全
                .populate_existing()
                .first()
            )

        # G8（2026-10-14 第五十九批）：借出即建的进度行当时可能**还没有音频**（total_seconds=0），
        # 之后馆员补传音频 → 这里把分母刷新成书目真实时长，避免覆盖率永远算 0（自愈，幂等）。
        if progress.total_seconds != total:
            progress.total_seconds = total

        # 构造本次区间
        start = (
            session_start
            if session_start is not None and 0 <= session_start <= total
            else max(0, position - 30)
        )
        end = min(position, total)
        if end <= start:
            return self._progress_view(progress)

        # ---- 防刷：覆盖增速校验（R-151）----
        now = datetime.now()
        try:
            intervals = json.loads(progress.intervals or "[]")
        except (json.JSONDecodeError, TypeError):
            intervals = []  # E-20260830-12：字段损坏时按空区间处理，不 500
        old_cov = progress.coverage_seconds
        merged = merge_intervals(intervals, [start, end])
        new_cov = coverage_of(merged)
        delta_cov = new_cov - old_cov
        # 服务端时间差 = 距上次上报的墙上时钟秒数；声明跨度可伪造，不作依据。
        # `max(0, ...)`：`last_report_at` 是 DATETIME（秒级四舍五入），上一拍 05.63s 落库成 06s，
        # 下一拍立刻上报就读到"未来 0.4s" → elapsed 为负、容差跟着变负 → 零新增覆盖的正常上报被
        # 判成异常（2026-10-16 实测随机红/绿）。钳 0 = 这一拍不给新增额度，也不给刷量留口子。
        raw_elapsed = (
            (now - progress.last_report_at).total_seconds() if progress.last_report_at else 0.0
        )
        elapsed = max(0.0, raw_elapsed)
        # P1 #1（2026-10-15 大审查）：宽限**只在首笔**加——原实现对每笔都加 60 秒，心跳 1 秒一次时
        # 每笔可新增 62.4 秒覆盖（≈60 倍速）：600 秒书 29 秒墙上时间即可刷满 95% 完播。
        grace = REPORT_GRACE_SECONDS if progress.last_report_at is None else 0.0
        allowed = elapsed * MAX_SPEED * SPEED_TOLERANCE + grace
        if delta_cov > allowed:
            raise ValidationError("播放数据异常（覆盖增速超过物理可能），本次上报被拒绝")

        just_finished = False
        if progress.finished == 0:
            threshold = int(ConfigService(self.db).get_value("audio_finish_threshold_percent"))
            if total > 0 and new_cov * 100 >= total * threshold:
                progress.finished = 1
                progress.finished_at = datetime.now()
                progress.reading_minutes = max(1, total // 60)
                just_finished = True

        progress.intervals = json.dumps(merged)
        progress.coverage_seconds = new_cov
        progress.last_position = position
        progress.last_report_at = now
        self.db.flush()

        result = self._progress_view(progress)
        result["just_finished"] = just_finished

        if just_finished:
            checkin = self._checkin(child, book_id)
            result["checkin"] = checkin
        self.db.commit()
        return result

    def _checkin(self, child: Child, book_id: int) -> dict:
        """当天首次完播 → 打卡（同天幂等；连续天数计算）。"""
        today = date.today()
        exists = (
            self.db.query(func.count(CheckIn.id))
            .filter(
                CheckIn.child_id == child.id, CheckIn.checkin_date == today, CheckIn.is_deleted == 0
            )
            .scalar()
        )
        if exists:
            return {"checked_in": False, "reason": "今日已打卡"}
        yesterday = (
            self.db.query(CheckIn)
            .filter(
                CheckIn.child_id == child.id,
                CheckIn.checkin_date == today - timedelta(days=1),
                CheckIn.is_deleted == 0,
            )
            .first()
        )
        streak = (yesterday.streak + 1) if yesterday else 1
        # P1-8（2026-10-09 审查）：**原子插入**——原 check-then-insert 在并发完播下
        # 6 线程落 6 行同日打卡（实测）。唯一索引 + ON DUPLICATE KEY 让并发只有一个赢家。
        #
        # 2026-10-12 审查 P1：**判据必须换**——原先靠 `result.rowcount != 1`（注释假设
        # "1=插入成功，2=命中唯一键"）判定重复，但 SQLAlchemy 的 MySQL 方言硬编码
        # `CLIENT_FOUND_ROWS`，"命中唯一键 + 空更新"的 affected-rows 由 0 变 1 →
        # **实测（真跑）首次插入 1、重复插入也是 1**，判据恒不成立 → 并发第二个请求会
        # 返回"打卡成功"并**二次发布 CheckInEvent**（周期日双发积分 + 唯一索引冲突 500）。
        # 现在改用 `inserted_primary_key`（真插入才有值；空更新路径不带 AUTO_INCREMENT 值）
        # 作为唯一判据，并保留 rowcount 兜底。
        from sqlalchemy.dialects.mysql import insert as mysql_insert

        result = self.db.execute(
            mysql_insert(CheckIn)
            .values(
                child_id=child.id,
                checkin_date=today,
                book_id=book_id,
                streak=streak,
                is_deleted=0,
            )
            .on_duplicate_key_update(id=CheckIn.id)  # 空更新：不覆盖既有 streak
        )
        inserted = bool(
            getattr(result, "inserted_primary_key", None) and result.inserted_primary_key[0]
        )
        if not inserted:
            return {"checked_in": False, "reason": "今日已打卡"}
        self.db.flush()
        # 打卡事件 → growth 域发周期积分（同事务；无订阅者时为 no-op）
        from backend.common.events import CheckInEvent, event_bus

        event_bus.publish(CheckInEvent(child_id=child.id, streak_days=streak), db=self.db)
        return {"checked_in": True, "streak": streak, "date": str(today)}

    def _progress_view(self, p: ReadingProgress) -> dict:
        return {
            "coverage_seconds": p.coverage_seconds,
            "total_seconds": p.total_seconds,
            "coverage_percent": round(p.coverage_seconds * 100 / p.total_seconds, 1)
            if p.total_seconds
            else 0,
            "finished": bool(p.finished),
            "last_position": p.last_position,
            "reading_minutes": p.reading_minutes,
        }

    def continue_listening(self, child: Child) -> dict | None:
        """首页"继续听"卡：最近一次有进度但未读完（finished=0）的上一本（A-1/T6 下沉）。

        P2-9（2026-10-17 第六十九批·大审查）：借阅行原先用无条件 `isouter` join——同一本书
        "还了又借"时有多条借阅记录，join 出多行、排序键只有 `last_report_at`（同书同进度行的
        时间完全相同）→ **可能取到 `returned` 那一条** ⇒ `in_borrow=False`、`due_at` 丢失
        （"X 天后到期"退化成"可续听"）。现分两步定行：先选进度行（口径不变：最近一次未读完），
        再单独取该 (孩子, 书目) 的**优先级最高**借阅行（在借/逾期优先、其次最新）——
        两次都是单行查询，结果确定（相关子查询写法会退化成笛卡尔积，SQLAlchemy 已告警）。
        """
        from sqlalchemy import case

        row = (
            self.db.query(ReadingProgress, Book)
            .join(Book, ReadingProgress.book_id == Book.id)
            .filter(
                ReadingProgress.child_id == child.id,
                ReadingProgress.finished == 0,
                ReadingProgress.last_report_at.isnot(None),
                ReadingProgress.is_deleted == 0,
                Book.is_deleted == 0,
                Book.status == Book.STATUS_ON,
            )
            .order_by(ReadingProgress.last_report_at.desc())
            .first()
        )
        if not row:
            return None
        p, book = row
        br = (
            self.db.query(BorrowRecord)
            .filter(
                BorrowRecord.child_id == child.id,
                BorrowRecord.book_id == book.id,
                BorrowRecord.is_deleted == 0,
            )
            .order_by(
                case(
                    (
                        BorrowRecord.status.in_(
                            [BorrowRecord.STATUS_ACTIVE, BorrowRecord.STATUS_OVERDUE]
                        ),
                        0,
                    ),
                    else_=1,
                ),
                BorrowRecord.id.desc(),
            )
            .first()
        )
        in_borrow = bool(
            br and br.status in (BorrowRecord.STATUS_ACTIVE, BorrowRecord.STATUS_OVERDUE)
        )
        return {
            "book": book,
            "percent": round(p.coverage_seconds * 100 / p.total_seconds, 1)
            if p.total_seconds
            else 0,
            "last_position": p.last_position,
            "due_at": str(br.due_at) if in_borrow else None,
        }

    def get_progress(self, child: Child, book_id: int) -> dict:
        p = (
            self.db.query(ReadingProgress)
            .filter(
                ReadingProgress.child_id == child.id,
                ReadingProgress.book_id == book_id,
                ReadingProgress.is_deleted == 0,
            )
            .first()
        )
        return (
            self._progress_view(p)
            if p
            else {
                "coverage_seconds": 0,
                "total_seconds": 0,
                "coverage_percent": 0,
                "finished": False,
                "last_position": 0,
                "reading_minutes": 0,
            }
        )

    #: 打卡日历单次区间查询的行数兜底（366 = 闰年整年；区间由路由侧另行限制跨度）
    CALENDAR_MAX_ROWS = 366

    def checkin_calendar(
        self,
        child: Child,
        days: int = 30,
        start_date: date | None = None,
        end_date: date | None = None,
    ) -> dict:
        """打卡日历。两种取数模式（P3 第一百一十六批：补区间模式）：

        - **默认（无 start/end）**：最近 `days` **条**打卡记录——首页只要
          `today_checked`/`current_streak`，不关心是哪几天（历史实现，行为不变）。
        - **区间模式（给了 start_date/end_date）**：该日期段内的打卡日——日历页翻月时
          按**所看的那个月**取数。

        为什么必须补区间模式（第八轮 P3 实测）：日历页原先固定请求「最近 90 条」，
        往前翻月超过这个窗口就**一片空白**——家长以为孩子那几个月没坚持（"禁假 0" 同族）。

        `today_checked` / `current_streak` **与取数模式解耦**：单独按今天这一行查。
        否则翻到历史月份时区间里没有今天 → 连续天数被算成 0（正是上面那类假象）。
        """
        q = self.db.query(CheckIn).filter(CheckIn.child_id == child.id, CheckIn.is_deleted == 0)
        if start_date is not None or end_date is not None:
            if start_date is not None:
                q = q.filter(CheckIn.checkin_date >= start_date)
            if end_date is not None:
                q = q.filter(CheckIn.checkin_date <= end_date)
            rows = q.order_by(CheckIn.checkin_date.desc()).limit(self.CALENDAR_MAX_ROWS).all()
        else:
            rows = q.order_by(CheckIn.checkin_date.desc()).limit(days).all()
        today_row = (
            self.db.query(CheckIn)
            .filter(
                CheckIn.child_id == child.id,
                CheckIn.checkin_date == date.today(),
                CheckIn.is_deleted == 0,
            )
            .first()
        )
        return {
            "dates": [str(r.checkin_date) for r in rows],
            "today_checked": bool(today_row),
            "current_streak": today_row.streak if today_row else 0,
        }


# ReservationService / ReservationAdminService 拆出至 reservation_service.py（god file 800 行限制）；
# re-export 保持 router / miniapp_router / tasks.registry / 测试引用路径不变
from backend.domain.reading.reservation_service import (  # noqa: E402, F401  # isort: skip
    ReservationAdminService,
    ReservationService,
)

# VocabularyService / FavoriteService 拆出至 vocabulary_service.py（god file 800 行限制）；
# re-export 保持 miniapp_router / 测试引用路径不变
from backend.domain.reading.vocabulary_service import (  # noqa: E402, F401  # isort: skip
    FavoriteService,
    ShelfService,
    VocabularyService,
)
