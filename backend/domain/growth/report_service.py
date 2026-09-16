# backend/domain/growth/report_service.py — 周报/月报数据与图片生成（WM8，FEAT-053）
"""图片为家长传播素材：Pillow 绘制存 uploads/reports/，家长端可保存转发。

绘制**必须走 `reading_circle.art` 引擎**（阅读圈卡片/头像/勋章同一套视觉语言）——
禁止在本模块手搓矩形+系统字体（2026-09-15 用户报障「跟本项目的样式风格格格不入」的根因）。
"""

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

    def __init__(self, db: Session):
        self.db = db

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

    def range_summary(self, child: Child, start: datetime, end: datetime) -> dict:
        """任意区间阅读汇总（本数/词数/打卡天数）——**report_data 同源口径**。

        WM14-B：阅读圈周报卡需回溯任意历史周，故把区间聚合抽成公开方法，
        禁止在阅读圈侧另写一套（计数同源纪律）。
        """
        from backend.domain.reading.models import CheckIn

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
        return {
            "books": len(rows),
            "words": sum(r.word_count for r in rows),
            "checkin_days": int(checkin_days or 0),
        }

    def report_data(self, child: Child, kind: str) -> dict:
        start, end, label = self.period_range(kind)
        summary_range = self.range_summary(child, start, end)
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
            "books": summary_range["books"],
            "words": summary_range["words"],
            "checkin_days": summary_range["checkin_days"],
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

        data = self.report_data(child, kind)
        cv = paint_report(data, child, kind)
        out_dir = os.path.join(_uploads_root(), "reports")
        filename = f"report_{kind}_{child.id}_{uuid.uuid4().hex[:8]}.png"
        cv.finish(os.path.join(out_dir, filename))
        return f"reports/{filename}"


# ---------------- 报告图绘制（WM8 / 2026-09-15 绘本风重做） ----------------
# 用户原话：「生成的周报和月报，跟本项目的样式风格格格不入」。旧版是**手搓的通用报表**：
# 深蓝横幅（#2c4a6e）+ 系统字体（Hiragino/STHeiti）+ 细线白卡——与本项目绘本令牌
# （暖纸底 / 粗描边 / 圆体数字 / 云朵星闪 / 吉祥物）毫无关系，正是 art.py 开头警告的
# 「各画各的必然割裂」。本版**全部改用 reading_circle.art 绘制引擎**（阅读圈卡片的同一套：
# 超采样画布 + 马卡龙渐变 + 白描边贴纸字 + 圆角气泡 + 纸纹），与卡片/头像/勋章同源。
#
# 版式（750×1100，尺寸与旧版一致，家长端与管理端展示布局零改动）：
#   标题胶囊 → 孩子·周期 → 主数字卡（大词数 + 吉祥物探头）→ 2×2 统计卡 → 鼓励语 → 日期条 → 馆标

REPORT_W, REPORT_H = 750, 1100
#: 周报=薰衣草紫（art 里 weekly_report 专用色），月报=薄荷绿（复用 books_count 色）
REPORT_PALETTES = {"weekly": "weekly_report", "monthly": "books_count"}


def _mascot_kind(child: Child) -> str:
    """报告吉祥物 = 孩子自己的头像动物（`owl_sun` → `owl`），认不出则用小熊兜底。"""
    from backend.domain.reading_circle import art

    kind = str(child.avatar or "").split("_")[0]
    return kind if kind in art.KIND_BASE else "bear"


def paint_report(data: dict, child: Child, kind: str):
    """按绘本视觉语言绘制周报/月报画布（返回超采样 Canvas，调用方落盘）。"""
    from backend.domain.reading_circle import art
    from backend.domain.reading_circle.art_mascot import mascot as art_mascot

    pal = art.PALETTES[REPORT_PALETTES.get(kind, "weekly_report")]
    cv = art.Canvas(REPORT_W, REPORT_H, pal)

    # ---- 页面级装饰：只放卡片框外的安全边距，杜绝"被边框裁切" ----
    art.glow(cv, 620, 92, 150, "#FFFFFF", 95)
    art.glow(cv, 118, 84, 112, "#FFFFFF", 70)
    # 云朵从标题横幅两侧探出来（横幅 100..650 压在云上，露出的部分即装饰）
    art.cloud(cv, 70, 104, 140, "#FFFFFF", 200)
    art.cloud(cv, 682, 100, 128, "#FFFFFF", 160)
    art.sparkle(cv, 30, 176, 12, "#FFFFFF", 225)
    art.sparkle(cv, 720, 184, 11, "#FFFFFF", 215)
    art.star(cv, 24, 258, 11, "#FFFFFF", outline=pal["accent"], width=2.0, rotate=0.3)
    art.star(cv, 726, 326, 10, "#FFFFFF", outline=pal["accent"], width=1.8, rotate=-0.2)
    art.sparkle(cv, 22, 640, 11, "#FFFFFF", 205)
    art.sparkle(cv, 728, 712, 10, "#FFFFFF", 200)
    art.star(cv, 726, 906, 10, "#FFFFFF", outline=pal["accent"], width=1.7, rotate=0.24)

    # ---- 标题横幅 + 孩子·周期 ----
    # 横幅宽度按实测字宽（44px「DmkWords 阅读周报」= 454px）+ 左右各 48px 内边距定，
    # 别再靠目测写死：首版 450 宽恰好比字窄 4px，白字直接糊在渐变背景上（已目视暴露）。
    art.bubble(cv, (100, 54, 650, 146), radius=46, fill=pal["accent"], outline=None)
    title = "DmkWords 阅读周报" if kind == "weekly" else "DmkWords 阅读月报"
    art.sticker_text(cv, (375, 100), title, art.font_cn(44), "#FFFFFF")
    art.sticker_text(
        cv,
        (375, 176),
        f"{data['child_name']} · {data['period_label']}",
        art.font_cn(29),
        art.INK,
        alpha=205,
    )

    # ---- 主数字卡（本期词数）+ 吉祥物探头 ----
    art.soft_shadow(cv, (40, 216, 710, 544), radius=46, blur=12, alpha=58)
    art.bubble(cv, (40, 216, 710, 544), radius=46, fill=art.PAPER, outline=pal["accent"], width=6)
    art.glow(cv, 375, 372, 230, "#FFFFFF", 88)
    art.sticker_text(cv, (375, 272), "本期有效阅读词数", art.font_cn(28), art.INK, alpha=165)
    art.sticker_pair(
        cv,
        (375, 372),
        f"{data['words']:,}",
        art.font_round(118),
        "词",
        art.font_cn(52),
        pal["deep"],
        stroke="#FFFFFF",
        stroke_w=9,
        dy_unit=20,
    )
    art.sticker_text(
        cv,
        (375, 478),
        f"累计 {data['total_words']:,} 词 · {data['level']} 级 · {data['points_total']} 积分",
        art.font_cn(27),
        art.INK,
    )
    kind_name = _mascot_kind(child)
    base = art.KIND_BASE[kind_name]
    art_mascot(
        cv, 642, 306, 60, kind=kind_name, fur=base["fur"], ear=base["ear"], blush=base["blush"]
    )

    # ---- 2×2 统计卡（数字用圆体，量词用中文字体，作为整词居中） ----
    stats: list[tuple[str, str, str]] = [
        ("读完本书", f"{data['books']}", "本"),
        ("打卡天数", f"{data['checkin_days']}", "天"),
        ("测验次数", f"{data['quiz_count']}", "次"),
        (
            "平均正确率",
            f"{data['quiz_avg_percent']}" if data["quiz_avg_percent"] is not None else "—",
            "%" if data["quiz_avg_percent"] is not None else "",
        ),
    ]
    for i, (lbl, val, unit) in enumerate(stats):
        x0 = 40 if i % 2 == 0 else 388
        y0 = 560 if i < 2 else 716
        cx = x0 + 161
        art.soft_shadow(cv, (x0, y0, x0 + 322, y0 + 140), radius=36, blur=9, alpha=46)
        art.bubble(
            cv,
            (x0, y0, x0 + 322, y0 + 140),
            radius=36,
            fill=art.PAPER,
            outline=pal["accent"],
            width=5,
        )
        art.sticker_text(cv, (cx, y0 + 44), lbl, art.font_cn(26), art.INK, alpha=165)
        if unit:
            art.sticker_pair(
                cv,
                (cx, y0 + 100),
                val,
                art.font_round(46),
                unit,
                art.font_cn(30),
                pal["deep"],
                dy_unit=10,
            )
        else:
            art.sticker_text(cv, (cx, y0 + 100), val, art.font_round(46), pal["deep"])

    # ---- 鼓励语 + 日期条 + 馆标 ----
    # 零阅读周期不摆冷冰冰的空卡：换一句"邀请开始"，家长转发出去也不难看
    art.bubble(cv, (40, 876, 710, 952), radius=30, fill=art.PAPER, outline=pal["accent"], width=5)
    art.sticker_text(
        cv,
        (375, 914),
        (
            "每一分钟的聆听，都在悄悄变成孩子的翅膀。"
            if data["words"] or data["books"] or data["checkin_days"]
            else "这个周期还没有阅读记录，今晚挑一本开始吧～"
        ),
        art.font_cn(28),
        art.INK,
    )
    art.bubble(cv, (60, 976, 690, 1028), radius=26, fill=pal["accent"], outline=None)
    art.sticker_text(
        cv,
        (375, 1002),
        f"{datetime.now():%Y-%m-%d} · 保存分享这份成长",
        art.font_cn(26),
        "#FFFFFF",
    )
    art.sticker_text(cv, (375, 1062), "DmkWords 少儿英语分级阅读", art.font_cn(23), art.INK)
    art.paper_grain(cv)
    return cv


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
        # WM11：周报/月报生成通知家长
        from backend.common.notification_models import Notification
        from backend.common.notifications import SCENE_REPORT_GENERATED, NotificationService

        NotificationService(self.db).send(
            parent_id=child.parent_id,
            scene=SCENE_REPORT_GENERATED,
            title="阅读报告已生成",
            content=(
                "周报已生成，可查看孩子本周阅读成果。"
                if kind == "weekly"
                else "月报已生成，可查看孩子本月阅读成果。"
            ),
            category=Notification.CATEGORY_REPORT,
            child_id=child.id,
            ref_type="child",
            ref_id=str(child.id),
            dedup_key=kind,
        )
        self.db.commit()
        return {"path": rel, "url": f"/api/admin/uploads/{rel}", "data": data}
