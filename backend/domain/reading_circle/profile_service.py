# backend/domain/reading_circle/profile_service.py — 孩子名片页 + 名片海报（WM15 R4/R5）
"""名片页 = 阅读圈的社交枢纽（点帖子头像/名字进入；点赞通知深链目标）。

隐私红线（R-317/318 延伸，专家裁决 C2）：
- 只展示 **英文名（空则「小朋友NNN」兜底）/ 头像 / 成就数据**
- **不得出现**中文名、家长名、手机号、生日
- 可见域 = 馆内全部登录家长（与阅读圈信息流一致）
- 退会/过期孩子 **可见**（历史荣誉域）——与榜单「竞争排名域排除退会」分域声明；
  「历史小读者」标签只标身份不标原因，不暴露退会/退款原因
"""

from __future__ import annotations

import os
import uuid

from sqlalchemy import func
from sqlalchemy.orm import Session

from backend.common.exceptions import NotFoundError
from backend.domain.growth.models import CheckinStreakRecord, MilestoneAward, WordsLedger
from backend.domain.identity.models import Child
from backend.domain.reading_circle import art
from backend.domain.reading_circle.models import CirclePost

POSTER_W, POSTER_H = 750, 1150


class CircleProfileService:
    def __init__(self, db: Session):
        self.db = db

    # ---------- 名片数据 ----------

    def child_profile(self, child: Child) -> dict:
        from backend.domain.growth.service import GrowthService

        summary = GrowthService(self.db).summary(child)
        books = (
            self.db.query(func.count(WordsLedger.id))
            .filter(WordsLedger.child_id == child.id, WordsLedger.is_deleted == 0)
            .scalar()
            or 0
        )
        badges = self._badges(child)
        return {
            "child_id": child.id,
            "english_name": child.english_name or f"小朋友{child.id:03d}",
            "avatar": child.avatar,
            "level": summary.get("level", "A"),
            "words_total": summary.get("words_total", 0),
            "books": int(books),
            "checkin_days": self._checkin_days(child),
            "badges": badges,
            "member_status": child.member_status,
            # 历史荣誉态：退会/过期孩子名片可见但只展示累计值（无"本周"类动态元素）
            "is_history": bool(child.is_expired_member)
            or child.member_status == Child.MEMBER_WITHDRAWN,
        }

    def _checkin_days(self, child: Child) -> int:
        from backend.domain.reading.models import CheckIn

        return int(
            self.db.query(func.count(CheckIn.id))
            .filter(CheckIn.child_id == child.id, CheckIn.is_deleted == 0)
            .scalar()
            or 0
        )

    def _badges(self, child: Child) -> list[dict]:
        """勋章墙（C5）：里程碑节点 + 等级 + 连击，资产 id 对应三端 badges 目录。"""
        from backend.domain.growth.models import ChildGrowthState

        out: list[dict] = []
        nodes = [
            int(r[0])
            for r in self.db.query(MilestoneAward.node_words)
            .filter(MilestoneAward.child_id == child.id, MilestoneAward.is_deleted == 0)
            .order_by(MilestoneAward.node_words)
            .all()
        ]
        # 里程碑 6 档：按节点序号映射到 milestone_m1..m6（超出取最高档）
        for i, node in enumerate(nodes[:6]):
            out.append({"badge_id": f"milestone_m{i + 1}", "label": self._node_label(node)})
        state = (
            self.db.query(ChildGrowthState)
            .filter(ChildGrowthState.child_id == child.id, ChildGrowthState.is_deleted == 0)
            .first()
        )
        if state:
            out.append({"badge_id": "level_template", "label": f"{state.level} 级小读者"})
        streaks = [
            int(r[0])
            for r in self.db.query(CheckinStreakRecord.streak_at)
            .filter(
                CheckinStreakRecord.child_id == child.id,
                CheckinStreakRecord.is_deleted == 0,
            )
            .order_by(CheckinStreakRecord.streak_at.desc())
            .all()
        ]
        if streaks:
            top = streaks[0]
            out.append(
                {
                    "badge_id": "streak_30" if top >= 30 else "streak_7",
                    "label": f"连续打卡 {top} 天",
                }
            )
        return out

    @staticmethod
    def _node_label(node: int) -> str:
        if node >= 10000:
            return f"累计阅读 {node / 10000:.0f} 万词"
        return f"累计阅读 {node} 词"

    def child_posts(self, child: Child, viewer_parent_id: int) -> list[dict]:
        """TA 晒过的帖子（复用信息流视图；批查点赞头像墙）。"""
        from backend.domain.reading_circle.service import CircleService

        svc = CircleService(self.db)
        rows = (
            self.db.query(CirclePost)
            .filter(CirclePost.child_id == child.id, CirclePost.is_deleted == 0)
            .order_by(CirclePost.created_at.desc(), CirclePost.id.desc())
            .limit(50)
            .all()
        )
        likers = svc._likers_map([r.id for r in rows])
        return [svc._post_view(r, viewer_parent_id, likers) for r in rows]

    def get_child(self, child_id: int) -> Child:
        child = self.db.query(Child).filter(Child.id == child_id, Child.is_deleted == 0).first()
        if not child:
            raise NotFoundError("孩子不存在")
        return child

    # ---------- 名片海报（R5） ----------

    def render_poster(self, child: Child) -> str:
        """生成「阅读名片」海报 → uploads/posters/{child_id}.png（同孩子覆盖，幂等）。

        消费通道：GET /api/miniapp/circle/children/{child_id}/poster（query token，
        照抄 posts/{id}/image 先例）——小程序没有通用 uploads 取图通道（A3 裁决）。
        """
        data = self.child_profile(child)
        base_key = self._mascot_for(child)
        base = art.KIND_BASE[base_key]
        pal = {"top": "#FFF6E4", "bot": "#FFE3D2", "accent": "#F2935B", "deep": "#C4693A"}

        cv = art.Canvas(POSTER_W, POSTER_H, pal)
        art.glow(cv, 375, 150, 240, "#FFFFFF", 110)
        art.cloud(cv, 116, 120, 138, "#FFFFFF", 200)
        art.cloud(cv, 636, 150, 104, "#FFFFFF", 160)
        for cx, cy, r in ((60, 380, 12), (692, 470, 11), (54, 760, 10)):
            art.star(cv, cx, cy, r, "#FFFFFF", outline=pal["accent"], width=2.0, rotate=0.2)

        art.sticker_text(
            cv, (375, 86), "阅读名片", art.font_cn(44), "#FFFFFF", stroke=pal["deep"], stroke_w=6
        )

        # 头像（本地包资产按 id 取源图；后端入库目录 backend/assets/avatars）
        avatar_path = self._avatar_asset(child.avatar)
        if avatar_path:
            from PIL import Image

            av = Image.open(avatar_path).convert("RGBA").resize((260, 260), Image.LANCZOS)
            cv.img.alpha_composite(av, (int((POSTER_W / 2 - 130) * art.SS), int(150 * art.SS)))

        art.sticker_text(cv, (375, 470), data["english_name"], art.font_cn(54), art.INK)
        art.bubble(cv, (206, 516, 544, 576), radius=30, fill=pal["accent"], outline=None)
        art.sticker_text(
            cv,
            (375, 546),
            f"{data['level']} 级 · 已读 {data['books']} 本",
            art.font_cn(28),
            "#FFFFFF",
        )

        # 三数据块
        stats = (
            ("总词数", f"{data['words_total']:,}"),
            ("读本数", str(data["books"])),
            ("打卡", f"{data['checkin_days']} 天"),
        )
        for i, (k, v) in enumerate(stats):
            x0 = 66 + i * 208
            art.bubble(
                cv,
                (x0, 618, x0 + 184, 752),
                radius=32,
                fill=art.PAPER,
                outline=pal["deep"],
                width=5,
            )
            art.sticker_text(cv, (x0 + 92, 662), v, art.font_round(46), pal["deep"])
            art.sticker_text(cv, (x0 + 92, 716), k, art.font_cn(24), art.INK)

        # 勋章墙
        art.sticker_text(cv, (375, 800), "我的勋章", art.font_cn(30), art.INK)
        for i, badge in enumerate(data["badges"][:6]):
            bp = self._badge_asset(badge["badge_id"])
            if not bp:
                continue
            from PIL import Image

            bi = Image.open(bp).convert("RGBA").resize((96, 96), Image.LANCZOS)
            cv.img.alpha_composite(bi, (int((66 + i * 110) * art.SS), int(830 * art.SS)))

        art.mascot(
            cv, 375, 1010, 74, kind=base_key, fur=base["fur"], ear=base["ear"], blush=base["blush"]
        )
        art.bubble(cv, (48, 1090, 702, 1140), radius=26, fill=pal["accent"], outline=None)
        art.sticker_text(cv, (375, 1115), "DmkWords 少儿英语阅读馆", art.font_cn(26), "#FFFFFF")
        art.paper_grain(cv)

        out_dir = os.path.join(self._uploads_root(), "posters")
        os.makedirs(out_dir, exist_ok=True)
        from PIL import Image

        img = cv.img.resize((POSTER_W, POSTER_H), Image.LANCZOS)
        filename = f"poster_{child.id}_{uuid.uuid4().hex[:6]}.png"
        img.save(os.path.join(out_dir, filename), "PNG")
        return f"posters/{filename}"

    @staticmethod
    def _mascot_for(child: Child) -> str:
        """按头像 id 前缀推出吉祥物种类；无头像用猫兜底。"""
        av = (child.avatar or "cat_sun").split("_")[0]
        return av if av in art.KIND_BASE else "cat"

    @staticmethod
    def _asset_root() -> str:
        return os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", "assets"))

    def _avatar_asset(self, avatar_id: str | None) -> str | None:
        if not avatar_id or avatar_id not in art.AVATAR_IDS:
            return None
        path = os.path.join(self._asset_root(), "avatars", f"{avatar_id}.png")
        return path if os.path.isfile(path) else None

    def _badge_asset(self, badge_id: str) -> str | None:
        if badge_id not in art.BADGE_IDS:
            return None
        path = os.path.join(self._asset_root(), "badges", f"{badge_id}.png")
        return path if os.path.isfile(path) else None

    @staticmethod
    def _uploads_root() -> str:
        from backend.config import get_settings

        return os.path.abspath(get_settings().UPLOADS_DIR)
