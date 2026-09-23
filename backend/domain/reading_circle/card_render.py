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

CARD_W, CARD_H = 750, 1180

# 缩略图规格版本：版本号进文件名 → `ensure_circle_thumbs` 能识别旧规格并重渲
# （幂等：已是当前规格即跳过），重渲后删旧文件，不留无主残留。
#   v2（fix33）= 与完整版同管线、仅跳文字层（两套版式，用户仍觉"割裂"）
#   v3（fix34）= **从完整版画布直接裁切插画区**（像素同源，点开=放大同一画面）
#   v4（2026-09-17）= 落盘格式 PNG → **JPEG**（体积：大图 769KB→67KB、缩略图 317KB→28KB；
#       带 paper_grain 噪点的插画用 PNG 压不动）。版本号进文件名 ⇒ URL 必变 ⇒ 客户端缓存必刷。
THUMB_SPEC_VERSION = "v4"

#: 生成图 JPEG 质量兜底值（真实值走 SystemConfig `image_generated_jpeg_quality`）
DEFAULT_JPEG_QUALITY = 85

# 插画区（方形 618×618，位于卡面上部）：完整版里它**零文字**，
# 缩略图 = 这块的裁切 → 缩略图与大图必然一致（fix34 R1-C 的几何前提）。
ART_BOX = (66, 176, 684, 794)


def _paint_card(card_data: dict, pal: dict, kind: str, base: dict):
    """完整版卡片画布（fix34 R1-C 版式）：上部**方形插画区** + 下部**文字条**。

    插画区（ART_BOX）只放主数字/吉祥物/星闪（无任何文字）；成就文字/署名/日期/馆标
    全在插画区之外的文字条里 → 缩略图裁插画区即可，既无重复文字、又与完整版像素同源。
    R5 全馆播报随 `value_label`（副标题）走 —— 同一行文字在卡片与信息流原生副标题都显示。
    """
    from backend.domain.reading_circle import art
    from backend.domain.reading_circle.art_mascot import mascot as art_mascot

    cv = art.Canvas(CARD_W, CARD_H, pal)
    # 页面级装饰只放安全边距（卡片框外），杜绝"被边框裁切"
    art.glow(cv, 628, 92, 165, "#FFFFFF", 100)
    art.glow(cv, 120, 78, 120, "#FFFFFF", 70)
    art.cloud(cv, 112, 104, 128, "#FFFFFF", 205)
    art.cloud(cv, 646, 116, 96, "#FFFFFF", 165)
    art.star(cv, 38, 300, 11, "#FFFFFF", outline=pal["accent"], width=2.0, rotate=0.3)
    art.star(cv, 712, 648, 10, "#FFFFFF", outline=pal["accent"], width=1.8, rotate=-0.2)
    art.sparkle(cv, 26, 520, 12, "#FFFFFF", 225)
    art.sparkle(cv, 724, 320, 11, "#FFFFFF", 215)
    art.sparkle(cv, 718, 902, 10, "#FFFFFF", 200)

    # 标题胶囊（accent 填充 + 白字）
    art.bubble(cv, (196, 64, 554, 146), radius=41, fill=pal["accent"], outline=None)
    art.sticker_text(cv, (375, 105), str(card_data.get("label", "")), art.font_cn(44), "#FFFFFF")

    # 方形插画区 = 缩略图裁切区（卡面 + 主数字 + 吉祥物，零文字）
    art.soft_shadow(cv, ART_BOX, radius=46, blur=12, alpha=58)
    art.bubble(cv, ART_BOX, radius=46, fill=art.PAPER, outline=pal["accent"], width=6)
    art.glow(cv, 375, 356, 230, "#FFFFFF", 90)

    big = str(card_data.get("value_text", ""))
    num, _, unit = big.partition(" ")
    if unit:
        art.sticker_pair(
            cv,
            (375, 346),
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
            cv, (375, 346), big, art.font_round(142), pal["deep"], stroke="#FFFFFF", stroke_w=9
        )

    art_mascot(cv, 206, 660, 88, kind=kind, fur=base["fur"], ear=base["ear"], blush=base["blush"])
    art.star(cv, 520, 632, 26, "#FFE08A", outline=pal["accent"], width=3.2, rotate=0.22)
    art.star(cv, 604, 704, 17, "#FFF3C4", outline=pal["accent"], width=2.4, rotate=-0.24)
    art.sparkle(cv, 486, 560, 15, "#FFFFFF", 235)
    art.sparkle(cv, 268, 468, 12, "#FFFFFF", 220)

    # ---- 文字条（插画区之外，只有完整版有） ----
    title = str(card_data.get("title", ""))
    label = str(card_data.get("value_label", ""))
    if title:
        art.sticker_text(cv, (375, 866), title, art.font_cn(38), art.INK)
    if label:
        art.sticker_text(cv, (375, 922), label, art.font_cn(32), pal["deep"])
    art.bubble(cv, (268, 1000, 482, 1056), radius=28, fill=art.PAPER, outline=pal["deep"], width=4)
    art.sticker_text(
        cv, (375, 1029), str(card_data.get("english_name", "")), art.font_cn(28), art.INK
    )
    art.bubble(cv, (48, 1076, 702, 1136), radius=26, fill=pal["accent"], outline=None)
    art.sticker_text(
        cv,
        (375, 1107),
        f"{card_data.get('date', '')} · 保存分享这份成长",
        art.font_cn(26),
        "#FFFFFF",
    )
    art.sticker_text(cv, (375, 1162), "DmkWords 少儿英语阅读馆", art.font_cn(23), art.INK)
    art.paper_grain(cv)
    return cv


def card_images(card_data: dict):
    """→（完整版图, 缩略图）：**缩略图 = 完整版 ART_BOX 的裁切**（同一像素来源）。

    在**超采样层裁切再降采样**（而非先降采样再裁）→ 缩略图与完整版只有分辨率差异。
    样图脚本与生产渲染共用本函数，保证"取证用的图"与"线上出的图"同源。
    """
    from PIL import Image

    pal, kind, base = _palette_and_mascot(card_data)
    cv = _paint_card(card_data, pal, kind, base)
    full = cv.img.resize((cv.w, cv.h), Image.LANCZOS)
    x0, y0, x1, y1 = ART_BOX
    ss = cv.img.width // cv.w  # Canvas 内部超采样倍率（art.SS）
    thumb = cv.img.crop((x0 * ss, y0 * ss, x1 * ss, y1 * ss))
    return full, thumb.resize((x1 - x0, y1 - y0), Image.LANCZOS)


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


def jpeg_bytes(img, quality: int) -> bytes:
    """把 PIL 图编码成 JPEG 字节（参数与 `save_jpeg` 完全一致）。

    内容寻址需要**先拿到字节**才能算指纹定文件名（2026-09-23 G7），故把编码与落盘拆开。
    """
    import io

    buf = io.BytesIO()
    img.convert("RGB").save(buf, "JPEG", quality=quality, optimize=True, progressive=True)
    return buf.getvalue()


def save_jpeg(img, path: str, quality: int) -> str:
    """生成图统一落盘（JPEG，optimize + progressive）——**唯一出口**，禁止各处自己 save。

    2026-09-17 用户裁定「自动生成的图片也要控体积」：带 paper_grain 噪点的插画 PNG
    压不动（卡片 769KB），JPEG q85 实测 67KB（8.8%）。
    2026-09-23：新代码优先走 `file_storage.save_generated_media`（内容寻址命名），本函数保留
    给"路径已定"的场景（M1 白名单出口）。
    """
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "wb") as fh:
        fh.write(jpeg_bytes(img, quality))
    return path


def render_thumb(card_data: dict, *, jpeg_quality: int = DEFAULT_JPEG_QUALITY) -> str:
    """只输出缩略图（存量回填用，不落大图）：内容 = 完整版插画区的裁切。

    2026-09-23：文件名改**内容指纹**（同内容同名复用；随机 tag 时代每次 seed 重建都多一堆孤儿）。
    """
    from backend.common.file_storage import save_generated_media

    card_type = card_data.get("card_type", "x")
    _, thumb = card_images(card_data)
    return save_generated_media(
        "circle", f"thumb_{THUMB_SPEC_VERSION}_{card_type}", jpeg_bytes(thumb, jpeg_quality)
    )


def ensure_circle_thumbs(db: Session) -> dict:
    """把存量帖缩略图对齐到**当前规格**（fix33 R1）：缺图则补渲、旧规格则重渲。

    幂等：文件名已含当前 `THUMB_SPEC_VERSION` 即跳过（重跑零渲染）；
    重渲成功后删旧缩略图文件（避免无主残留），**大图 image_path 一律不动**。
    单帖失败跳过不阻塞整批（返回 skipped 计数供调用方显式报告）。
    """
    from backend.common.file_storage import generated_jpeg_quality
    from backend.domain.reading_circle.models import CirclePost as Post

    quality = generated_jpeg_quality(db, DEFAULT_JPEG_QUALITY)
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
            post.thumb_path = render_thumb(json.loads(post.card_data or "{}"), jpeg_quality=quality)
        except Exception:  # 单帖失败不影响整批
            skipped += 1
            continue
        if old_rel and old_rel != post.thumb_path:
            # 2026-09-23：内容寻址后"重渲结果与旧文件同名"是常态（内容没变）——
            # 必须比对路径，否则会把刚写好的文件删掉，post.thumb_path 指向空气。
            old_full = os.path.abspath(os.path.join(root, old_rel))
            if old_full.startswith(circle_dir) and os.path.isfile(old_full):
                try:
                    os.remove(old_full)
                except OSError:
                    pass  # 删不掉只留孤儿文件，不影响正确性（清理任务可兜底）
        rendered += 1
    db.commit()
    return {"rendered": rendered, "skipped": skipped}


def render_card(card_data: dict, *, jpeg_quality: int = DEFAULT_JPEG_QUALITY) -> dict:
    """渲染**双规格**卡片图 → {"image_path": 完整版, "thumb_path": 缩略图}。

    fix34 R1-C：两规格**同源**——缩略图 = 完整版画布 ART_BOX（插画区）的裁切，
    点开大图所见即缩略图的放大版 + 下方文字条，观感是"放大"而不是"换了一张图"。
    两文件同为 uploads/circle/ 运行时产物，生命周期绑定同一帖
    （删帖由 CircleImageCleanupService 两列一起清）。

    2026-09-17：落盘改 JPEG（大图 769KB→67KB、缩略图 317KB→28KB）。
    2026-09-23：文件名改**内容指纹**（`sha256(字节)[:12]`）——内容变⇒名字变⇒客户端不吃旧缓存；
    内容不变⇒同名复用（不重绘、不堆文件）。此前用随机 tag，清库+seed 每轮新增 40 个孤儿（G7）。
    """
    from backend.common.file_storage import save_generated_media

    card_type = card_data.get("card_type", "x")
    full, thumb = card_images(card_data)
    return {
        "image_path": save_generated_media(
            "circle", f"card_{card_type}", jpeg_bytes(full, jpeg_quality)
        ),
        "thumb_path": save_generated_media(
            "circle", f"thumb_{THUMB_SPEC_VERSION}_{card_type}", jpeg_bytes(thumb, jpeg_quality)
        ),
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
