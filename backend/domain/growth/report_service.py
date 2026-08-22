# backend/domain/growth/report_service.py — 周报/月报数据与图片生成（WM8，FEAT-053）
"""图片为家长传播素材：Pillow 绘制存 uploads/reports/，家长端可保存转发。"""

from __future__ import annotations

from datetime import datetime, timedelta

from sqlalchemy import func
from sqlalchemy.orm import Session

from backend.common.exceptions import ValidationError
from backend.domain.growth.models import QuizAttempt, WordsLedger
from backend.domain.growth.service import GrowthService
from backend.domain.identity.models import Child


class ReportService:
    """周报/月报图片（FEAT-053：家长可保存转发）。Pillow 绘制，存 uploads/reports/。"""

    FONT_CANDIDATES = [
        "/System/Library/Fonts/Hiragino Sans GB.ttc",
        "/System/Library/Fonts/STHeiti Light.ttc",
        "/System/Library/Fonts/Supplemental/Songti.ttc",
    ]

    def __init__(self, db: Session):
        self.db = db

    def _font(self, size: int):
        from PIL import ImageFont

        for path in self.FONT_CANDIDATES:
            try:
                return ImageFont.truetype(path, size)
            except OSError:
                continue
        return ImageFont.load_default()

    def period_range(self, kind: str) -> tuple[datetime, datetime, str]:
        """周报=上个自然周；月报=上个自然月。"""
        now = datetime.now()
        today = now.date()
        if kind == "weekly":
            this_monday = today - timedelta(days=today.weekday())
            start = datetime.combine(this_monday - timedelta(days=7), datetime.min.time())
            end = datetime.combine(this_monday, datetime.min.time())
            label = f"{start:%Y年%m月%d日} - {end:%m月%d日}"
        elif kind == "monthly":
            first_this_month = today.replace(day=1)
            end = datetime.combine(first_this_month, datetime.min.time())
            last_month_end = end - timedelta(days=1)
            start = datetime.combine(last_month_end.replace(day=1), datetime.min.time())
            label = f"{start:%Y年%m月}"
        else:
            raise ValidationError("报告类型仅支持 weekly/monthly")
        return start, end, label

    def report_data(self, child: Child, kind: str) -> dict:
        start, end, label = self.period_range(kind)
        rows = (
            self.db.query(WordsLedger)
            .filter(
                WordsLedger.child_id == child.id,
                WordsLedger.created_at >= start,
                WordsLedger.created_at < end,
                WordsLedger.is_deleted == 0,
            )
            .all()
        )
        from backend.domain.reading.models import CheckIn

        checkin_days = (
            self.db.query(func.count(CheckIn.id))
            .filter(
                CheckIn.child_id == child.id,
                CheckIn.checkin_date >= start.date(),
                CheckIn.checkin_date < end.date(),
                CheckIn.is_deleted == 0,
            )
            .scalar()
        )
        attempts = (
            self.db.query(QuizAttempt)
            .filter(
                QuizAttempt.child_id == child.id,
                QuizAttempt.submitted_at >= start,
                QuizAttempt.submitted_at < end,
                QuizAttempt.is_deleted == 0,
            )
            .all()
        )
        summary = GrowthService(self.db).summary(child)
        avg_score = (
            round(100 * sum(a.score for a in attempts) / sum(a.total_questions for a in attempts))
            if attempts
            else None
        )
        return {
            "kind": kind,
            "period_label": label,
            "child_name": child.name,
            "english_name": child.english_name,
            "books": len(rows),
            "words": sum(r.word_count for r in rows),
            "checkin_days": int(checkin_days or 0),
            "quiz_count": len(attempts),
            "quiz_avg_percent": avg_score,
            "total_words": summary["words_total"],
            "level": summary["level"],
            "points_total": summary["points_total"],
        }

    def generate_image(self, child: Child, kind: str) -> str:
        """生成报告图片，返回相对路径（uploads/ 下）。"""
        import os
        import uuid

        from PIL import Image, ImageDraw

        data = self.report_data(child, kind)
        W, H = 750, 1100
        BG, CARD, INK, MUTED, ACCENT = "#f6f2e9", "#fffefa", "#262419", "#6f685a", "#2c4a6e"
        GOLD = "#c9a227"
        img = Image.new("RGB", (W, H), BG)
        d = ImageDraw.Draw(img)
        f_title = self._font(52)
        f_sub = self._font(30)
        f_big = self._font(72)
        f_lbl = self._font(26)

        # 头部
        d.rectangle([0, 0, W, 210], fill=ACCENT)
        title = "DmkWords 阅读周报" if kind == "weekly" else "DmkWords 阅读月报"
        d.text((48, 52), title, font=f_title, fill="#fffefa")
        d.text(
            (48, 130), f"{data['child_name']} · {data['period_label']}", font=f_sub, fill="#d9e2ee"
        )

        # 核心数字卡
        d.rounded_rectangle(
            [40, 250, W - 40, 470], radius=24, fill=CARD, outline="#e4dcc8", width=2
        )
        d.text((72, 285), "本期有效阅读词数", font=f_lbl, fill=MUTED)
        d.text((72, 320), f"{data['words']:,}", font=f_big, fill=ACCENT)
        d.text(
            (72, 430),
            f"累计 {data['total_words']:,} 词 · {data['level']} 级 · {data['points_total']} 积分",
            font=f_sub,
            fill=INK,
        )

        # 四格统计
        stats = [
            ("读完本书", f"{data['books']} 本"),
            ("打卡天数", f"{data['checkin_days']} 天"),
            ("测验次数", f"{data['quiz_count']} 次"),
            (
                "平均正确率",
                f"{data['quiz_avg_percent']}%" if data["quiz_avg_percent"] is not None else "—",
            ),
        ]
        x0, y0, bw, bh, gap = 40, 510, (W - 80 - 24) // 2, 150, 24
        for i, (lbl, val) in enumerate(stats):
            x = x0 + (i % 2) * (bw + gap)
            y = y0 + (i // 2) * (bh + gap)
            d.rounded_rectangle(
                [x, y, x + bw, y + bh], radius=20, fill=CARD, outline="#e4dcc8", width=2
            )
            d.text((x + 24, y + 28), lbl, font=f_lbl, fill=MUTED)
            d.text((x + 24, y + 66), val, font=self._font(44), fill=INK)

        # 鼓励语
        d.rounded_rectangle(
            [40, 860, W - 40, 960], radius=20, fill="#f6edd3", outline=GOLD, width=2
        )
        msg = "每一分钟的聆听，都在悄悄变成孩子的翅膀。"
        d.text((72, 900), msg, font=f_sub, fill="#736013")

        # 底部品牌
        d.text((48, H - 70), "DmkWords 少儿英语分级阅读 · 保存分享这份成长", font=f_lbl, fill=MUTED)

        rel_dir = "reports"
        out_dir = os.path.join(_uploads_root(), rel_dir)
        os.makedirs(out_dir, exist_ok=True)
        filename = f"report_{kind}_{child.id}_{uuid.uuid4().hex[:8]}.png"
        img.save(os.path.join(out_dir, filename), "PNG")
        return f"{rel_dir}/{filename}"


def _uploads_root() -> str:
    import os

    from backend.config import get_settings

    return os.path.abspath(get_settings().UPLOADS_DIR)


class ReportAdminService:
    """管理端报告生成入口（查档 + 生成 + 审计留痕，Router 零 ORM）。"""

    def __init__(self, db: Session):
        self.db = db

    def generate_for_admin(self, admin, child_id: int, kind: str) -> dict:
        from backend.common.exceptions import NotFoundError

        child = self.db.query(Child).filter(Child.id == child_id, Child.is_deleted == 0).first()
        if not child:
            raise NotFoundError("孩子不存在")
        svc = ReportService(self.db)
        rel = svc.generate_image(child, kind)
        data = svc.report_data(child, kind)
        from backend.domain.catalog.audit_events import publish_audit

        publish_audit(
            self.db,
            admin=admin,
            action="growth.report_generate",
            target_type="child",
            target_id=str(child_id),
            detail={"kind": kind, "path": rel},
            reason="报告图片生成",
        )
        self.db.commit()
        return {"path": rel, "url": f"/api/admin/uploads/{rel}", "data": data}
