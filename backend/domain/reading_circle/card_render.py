# backend/domain/reading_circle/card_render.py — 成就卡片渲染段（fix34 R3 从 card_engine 拆出）
"""职责：卡片图/缩略图的 Pillow 渲染、落盘、取图与存量缩略图对齐。

拆分缘由（fix34 R3，专家任务包强制）：`card_engine` 原 795 行贴死 god-file 800 行上限，
而 R1（缩略图改「完整版裁切」）还要改渲染段——先拆后改，两文件都在限内。

依赖方向（**单向，勿反向**）：`card_render` → `art` / `art_mascot` / `models`；
`card_engine` → `card_render`（仅 re-export，供既有调用方零改动）。
数据装配（assemble_card_data / enumerate_cards / 标签与配色表）仍留在 `card_engine`。
"""

from __future__ import annotations

import json
import os
import uuid

from sqlalchemy.orm import Session

from backend.common.exceptions import NotFoundError
from backend.domain.reading_circle.models import CirclePost

# 卡片类型 → 吉祥物（每类卡一个动物，形成系列感；与头像库同一套美术语言）
CARD_MASCOT = {
    CirclePost.CARD_MILESTONE: "lion",
    CirclePost.CARD_BOOKS_COUNT: "bear",
    CirclePost.CARD_LEVEL_UP: "fox",
    CirclePost.CARD_PERFECT_QUIZ: "bunny",
    CirclePost.CARD_STREAK: "panda",
    CirclePost.CARD_FINISH_BOOK: "cat",
    CirclePost.CARD_RANK_TOP: "deer",
    CirclePost.CARD_RANK_UP: "owl",
    CirclePost.CARD_WEEKLY_REPORT: "hedgehog",
    CirclePost.CARD_BREAKTHROUGH: "dino",
}

CARD_W, CARD_H = 750, 1000

# 缩略图规格版本：版本号进文件名 → `ensure_circle_thumbs` 能识别旧规格并重渲
# （幂等：已是当前规格即跳过），重渲后删旧文件，不留无主残留。
#   v2（fix33）= 与完整版同管线、仅跳文字层
THUMB_SPEC_VERSION = "v2"


def _paint_card(card_data: dict, pal: dict, kind: str, base: dict, *, with_text: bool):
    """卡片共绘画布（fix33 R1）：完整版与缩略图**走同一条管线、坐标完全一致**。

    唯一差异是文字层——`with_text=False`（缩略图）跳过标题胶囊/成就文字/署名/日期/馆标
    （这些信息由信息流原生文字渲染），插画、主数字、吉祥物、星闪、纸纹全留。
    """
    from backend.domain.reading_circle import art
    from backend.domain.reading_circle.art_mascot import mascot as art_mascot

    cv = art.Canvas(CARD_W, CARD_H, pal)
    # 页面级装饰只放安全边距（卡片框外），杜绝首版"被边框裁切"
    art.glow(cv, 628, 92, 165, "#FFFFFF", 100)
    art.glow(cv, 120, 78, 120, "#FFFFFF", 70)
    art.cloud(cv, 112, 104, 128, "#FFFFFF", 205)
    art.cloud(cv, 646, 116, 96, "#FFFFFF", 165)
    art.star(cv, 40, 330, 11, "#FFFFFF", outline=pal["accent"], width=2.0, rotate=0.3)
    art.star(cv, 710, 566, 10, "#FFFFFF", outline=pal["accent"], width=1.8, rotate=-0.2)
    art.sparkle(cv, 28, 466, 12, "#FFFFFF", 225)
    art.sparkle(cv, 722, 258, 11, "#FFFFFF", 215)

    # 标题胶囊（accent 填充 + 白字）——纯文字容器，缩略图略去（空胶囊是视觉噪声）
    if with_text:
        art.bubble(cv, (196, 64, 554, 146), radius=41, fill=pal["accent"], outline=None)
        art.sticker_text(
            cv, (375, 105), str(card_data.get("label", "")), art.font_cn(44), "#FFFFFF"
        )

    # 卡面
    art.soft_shadow(cv, (66, 176, 684, 770), radius=46, blur=12, alpha=58)
    art.bubble(cv, (66, 176, 684, 770), radius=46, fill=art.PAPER, outline=pal["accent"], width=6)
    art.glow(cv, 375, 340, 190, "#FFFFFF", 90)

    # 主数字：**两规格都画**（信息流原生文字只渲染 title/value_label，数值只在图上）
    big = str(card_data.get("value_text", ""))
    num, _, unit = big.partition(" ")
    if unit:
        art.sticker_pair(
            cv,
            (375, 330),
            num,
            art.font_round(150),
            unit,
            art.font_cn(74),
            pal["deep"],
            stroke="#FFFFFF",
            stroke_w=10,
            dy_unit=26,
        )
    else:
        art.sticker_text(
            cv, (375, 330), big, art.font_round(142), pal["deep"], stroke="#FFFFFF", stroke_w=9
        )
    if with_text:
        title = str(card_data.get("title", ""))
        label = str(card_data.get("value_label", ""))
        if title:
            art.sticker_text(cv, (375, 462), title, art.font_cn(36), art.INK)
        if label:
            art.sticker_text(cv, (375, 518), label, art.font_cn(32), pal["deep"])

    art_mascot(cv, 190, 650, 82, kind=kind, fur=base["fur"], ear=base["ear"], blush=base["blush"])
    art.star(cv, 520, 636, 26, "#FFE08A", outline=pal["accent"], width=3.2, rotate=0.22)
    art.star(cv, 604, 700, 17, "#FFF3C4", outline=pal["accent"], width=2.4, rotate=-0.24)
    art.sparkle(cv, 486, 566, 15, "#FFFFFF", 235)

    if with_text:
        art.bubble(
            cv, (268, 800, 482, 856), radius=28, fill=art.PAPER, outline=pal["deep"], width=4
        )
        art.sticker_text(
            cv, (375, 829), str(card_data.get("english_name", "")), art.font_cn(28), art.INK
        )
        art.bubble(cv, (48, 876, 702, 936), radius=26, fill=pal["accent"], outline=None)
        art.sticker_text(
            cv,
            (375, 907),
            f"{card_data.get('date', '')} · 保存分享这份成长",
            art.font_cn(26),
            "#FFFFFF",
        )
        art.sticker_text(cv, (375, 966), "DmkWords 少儿英语阅读馆", art.font_cn(23), art.INK)
    art.paper_grain(cv)
    return cv


def _render_full(card_data: dict, pal: dict, kind: str, base: dict, out_dir: str, tag: str) -> str:
    """含字完整版（预览/保存转发用）：标题胶囊 + 主数字 + 说明行 + 吉祥物 + 页脚。"""
    cv = _paint_card(card_data, pal, kind, base, with_text=True)
    return _save(cv, out_dir, f"card_{card_data.get('card_type', 'x')}_{tag}.png")


def _render_thumb(card_data: dict, pal: dict, kind: str, base: dict, out_dir: str, tag: str) -> str:
    """无字缩略版（信息流小图）：**同构图**（fix33 R1）——同一管线去掉文字层。"""
    cv = _paint_card(card_data, pal, kind, base, with_text=False)
    return _save(
        cv, out_dir, f"thumb_{THUMB_SPEC_VERSION}_{card_data.get('card_type', 'x')}_{tag}.png"
    )


def _save(cv, out_dir: str, filename: str) -> str:
    from PIL import Image

    img = cv.img.resize((cv.w, cv.h), Image.LANCZOS)
    img.save(os.path.join(out_dir, filename), "PNG")
    return f"circle/{filename}"


def _palette_and_mascot(card_data: dict) -> tuple[dict, str, dict]:
    """按卡型取（配色 / 吉祥物 kind / 吉祥物基色）。"""
    from backend.domain.reading_circle import art

    card_type = card_data.get("card_type", CirclePost.CARD_FINISH_BOOK)
    pal = art.PALETTES.get(card_type, art.PALETTES[CirclePost.CARD_FINISH_BOOK])
    kind = CARD_MASCOT.get(card_type, "cat")
    return pal, kind, art.KIND_BASE[kind]


def _circle_dir() -> str:
    out_dir = os.path.join(_uploads_root(), "circle")
    os.makedirs(out_dir, exist_ok=True)
    return out_dir


def render_thumb(card_data: dict) -> str:
    """只渲染缩略图（WM15-B3：旧帖回填用，不重渲大图；规格随 _render_thumb 走）。"""
    pal, kind, base = _palette_and_mascot(card_data)
    return _render_thumb(card_data, pal, kind, base, _circle_dir(), uuid.uuid4().hex[:8])


def ensure_circle_thumbs(db: Session) -> dict:
    """把存量帖缩略图对齐到**当前规格**（fix33 R1）：缺图则补渲、旧规格则重渲。

    幂等：文件名已含当前 `THUMB_SPEC_VERSION` 即跳过（重跑零渲染）；
    重渲成功后删旧缩略图文件（避免无主残留），**大图 image_path 一律不动**。
    单帖失败跳过不阻塞整批（返回 skipped 计数供调用方显式报告）。
    """
    from backend.domain.reading_circle.models import CirclePost as Post

    marker = f"thumb_{THUMB_SPEC_VERSION}_"
    rows = db.query(Post).filter(Post.is_deleted == 0).all()
    root = _uploads_root()
    circle_dir = os.path.join(root, "circle") + os.sep
    rendered = skipped = 0
    for post in rows:
        if post.thumb_path and marker in os.path.basename(post.thumb_path):
            continue
        old_rel = post.thumb_path or ""
        try:
            post.thumb_path = render_thumb(json.loads(post.card_data or "{}"))
        except Exception:  # 单帖失败不影响整批
            skipped += 1
            continue
        if old_rel:
            old_full = os.path.abspath(os.path.join(root, old_rel))
            if old_full.startswith(circle_dir) and os.path.isfile(old_full):
                try:
                    os.remove(old_full)
                except OSError:
                    pass  # 删不掉只留孤儿文件，不影响正确性（清理任务可兜底）
        rendered += 1
    db.commit()
    return {"rendered": rendered, "skipped": skipped}


def render_card(card_data: dict) -> dict:
    """渲染**双规格**卡片图（WM15-R2）→ {"image_path": 含字完整版, "thumb_path": 缩略版}。

    两规格同为 uploads/circle/ 下的运行时产物，生命周期绑定同一帖
    （删帖由 CircleImageCleanupService 两列一起清）。
    """
    pal, kind, base = _palette_and_mascot(card_data)
    out_dir = _circle_dir()
    tag = uuid.uuid4().hex[:8]
    return {
        "image_path": _render_full(card_data, pal, kind, base, out_dir, tag),
        "thumb_path": _render_thumb(card_data, pal, kind, base, out_dir, tag),
    }


def _uploads_root() -> str:
    from backend.config import get_settings

    return os.path.abspath(get_settings().UPLOADS_DIR)


def post_thumb_image(db: Session, post_id: int) -> str:
    """取帖子缩略图相对路径（信息流小图；旧帖无缩略图 → 回落大图，前端仍留 wx:if 防空）。"""
    p = db.query(CirclePost).filter(CirclePost.id == post_id, CirclePost.is_deleted == 0).first()
    if not p:
        raise NotFoundError("帖子不存在")
    return p.thumb_path or p.image_path


def post_card_image(db: Session, post_id: int) -> str:
    """取帖子的卡片图相对路径（双端 image 端点共用；ORM 不进 Router）。"""
    p = db.query(CirclePost).filter(CirclePost.id == post_id, CirclePost.is_deleted == 0).first()
    if not p:
        raise NotFoundError("帖子不存在")
    return p.image_path
