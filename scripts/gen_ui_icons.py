"""UI 图标生成器（2026-09-16）：把散落各页的 emoji 图标换成自家绘本风字形。

为什么需要：小程序 22 个页面里散着 ~130 处 emoji，其中约 40 处是**当图标用**的
（`<text class="fi-icon">📅</text>` 这类）。emoji 的问题不是丑，是**不受控**：
每个平台渲染不同（iOS/Android/开发者工具三副面孔）、颜色与绘本令牌无关、
圆角线条粗细都无法对齐——正是用户长期在提的"视觉割裂"。

画法与既有资产同源（`backend/domain/reading_circle/art.py`）：
粗墨线描边（INK #5B4636）+ 马卡龙实色 + 白色高光 + 轻微纸纹 → 与头像/勋章/卡片一套语言。
输出 96×96 透明 PNG（与 tabbar 图标同规格；显示尺寸 28~56rpx，2~3 倍冗余足够）。

用法：python -m scripts.gen_ui_icons            # 生成到 miniapp/icons/ui/
      python -m scripts.gen_ui_icons --sheet    # 额外出一张总览拼图（/tmp 供目视）
"""

from __future__ import annotations

import os
import sys

from PIL import Image, ImageDraw

from backend.domain.reading_circle.art import (
    INK,
    Canvas,
    hex2rgb,
    paper_grain,
    sparkle,
    star,
)

ICON = 96  # 输出边长（与 tabbar 图标一致）
OUT_DIR = os.path.join("miniapp", "icons", "ui")

# 与头像/勋章同一套马卡龙色（引用 art.PALETTES 的 accent 系，避免自成一套）
C = {
    "sun": "#FFC94D",
    "coral": "#F2935B",
    "rose": "#F2789F",
    "mint": "#4FB98A",
    "sky": "#6C9BF0",
    "lav": "#8E7BE8",
    "paper": "#FFFDF8",
    "dim": "#F1E7D8",
}


def _new() -> Canvas:
    """透明底画布（图标要能放在任何底色上）。"""
    return Canvas(ICON, ICON, {"top": "#FFFFFF", "bottom": "#FFFFFF"}, transparent=True)


def _ring(cv: Canvas, cx: float, cy: float, r: float, fill: str, lw: float = 5.0) -> None:
    cv.d.ellipse(
        cv.box(cx - r, cy - r, cx + r, cy + r),
        fill=hex2rgb(fill) + (255,),
        outline=hex2rgb(INK) + (255,),
        width=int(lw * cv.img.width // cv.w),
    )


def _rr(cv: Canvas, box, fill: str, radius: float, lw: float = 5.0) -> None:
    cv.d.rounded_rectangle(
        cv.box(*box),
        radius=radius * cv.img.width // cv.w,
        fill=hex2rgb(fill) + (255,),
        outline=hex2rgb(INK) + (255,),
        width=int(lw * cv.img.width // cv.w),
    )


def _blob(cv: Canvas, pts, fill: str, lw: float = 5.0) -> None:
    cv.d.polygon(
        [cv.p(*p) for p in pts],
        fill=hex2rgb(fill) + (255,),
        outline=hex2rgb(INK) + (255,),
        width=int(lw * cv.img.width // cv.w),
    )


# ---------------- 各图标 ----------------
def icon_bell(cv: Canvas) -> None:
    """通知：钟形铃铛（顶钮 + 钟身 + 铃舌）。"""
    _blob(
        cv,
        [
            (32, 62),
            (34, 40),
            (48, 30),
            (62, 40),
            (64, 62),
            (70, 70),
            (26, 70),
        ],
        C["sun"],
    )
    _rr(cv, (43, 20, 53, 32), C["coral"], 4)
    _ring(cv, 48, 78, 6, C["coral"])


def icon_book(cv: Canvas) -> None:
    """书（在借/书城/生词本通用）：两页摊开的书。"""
    _blob(cv, [(16, 30), (48, 40), (48, 78), (16, 68)], C["mint"])
    _blob(cv, [(80, 30), (48, 40), (48, 78), (80, 68)], C["sky"])
    cv.d.line(cv.p(48, 40, 48, 78), fill=hex2rgb(INK) + (255,), width=int(4 * cv.img.width // cv.w))


def icon_bookmark(cv: Canvas) -> None:
    """预约：书签。"""
    _blob(cv, [(32, 18), (64, 18), (64, 80), (48, 66), (32, 80)], C["rose"])


def icon_headphone(cv: Canvas) -> None:
    """听书：头戴耳机。"""
    cv.d.arc(
        cv.box(20, 22, 76, 78),
        start=180,
        end=360,
        fill=hex2rgb(INK) + (255,),
        width=int(7 * cv.img.width // cv.w),
    )
    _rr(cv, (16, 48, 34, 78), C["lav"], 9)
    _rr(cv, (62, 48, 80, 78), C["lav"], 9)


def icon_calendar(cv: Canvas) -> None:
    """打卡：日历。"""
    _rr(cv, (18, 26, 78, 80), C["paper"], 10)
    cv.d.rectangle(cv.box(18, 26, 78, 44), fill=hex2rgb(C["coral"]) + (255,))
    _rr(cv, (18, 26, 78, 80), C["paper"], 10)
    cv.d.rectangle(cv.box(22, 30, 74, 42), fill=hex2rgb(C["coral"]) + (255,))
    cv.d.rounded_rectangle(cv.box(22, 30, 74, 42), radius=8, fill=hex2rgb(C["coral"]) + (255,))
    for dx, dy in ((32, 54), (48, 54), (64, 54), (32, 68), (48, 68)):
        cv.d.ellipse(cv.box(dx - 4, dy - 4, dx + 4, dy + 4), fill=hex2rgb(INK) + (150,))
    _rr(cv, (26, 14, 34, 30), C["sun"], 4)
    _rr(cv, (62, 14, 70, 30), C["sun"], 4)


def icon_trophy(cv: Canvas) -> None:
    """等级勋章/成就：奖杯。"""
    _blob(cv, [(28, 24), (68, 24), (64, 56), (32, 56)], C["sun"])
    cv.d.arc(
        cv.box(14, 26, 34, 52),
        start=90,
        end=270,
        fill=hex2rgb(INK) + (255,),
        width=int(5 * cv.img.width // cv.w),
    )
    cv.d.arc(
        cv.box(62, 26, 82, 52),
        start=270,
        end=90,
        fill=hex2rgb(INK) + (255,),
        width=int(5 * cv.img.width // cv.w),
    )
    _rr(cv, (42, 56, 54, 68), C["sun"], 3)
    _rr(cv, (30, 68, 66, 80), C["coral"], 6)


def icon_card(cv: Canvas) -> None:
    """阅读护照：证件卡。"""
    _rr(cv, (16, 26, 80, 74), C["sky"], 10)
    _ring(cv, 36, 50, 11, C["paper"], lw=4)
    for y, w in ((42, 26), (52, 20), (60, 14)):
        cv.d.rounded_rectangle(
            cv.box(54, y, 54 + w, y + 5), radius=3, fill=hex2rgb(C["paper"]) + (235,)
        )


def icon_report(cv: Canvas) -> None:
    """周报月报：带折线的报表。"""
    _rr(cv, (20, 18, 76, 80), C["paper"], 8)
    _blob(cv, [(30, 62), (42, 48), (54, 56), (68, 36), (68, 62)], C["mint"], lw=4)
    cv.d.line(cv.p(30, 68, 68, 68), fill=hex2rgb(INK) + (200,), width=int(4 * cv.img.width // cv.w))


def icon_chart(cv: Canvas) -> None:
    """排行榜：三根柱状图（分开画，避免糊成一团）。"""
    _rr(cv, (16, 72, 80, 82), C["dim"], 6)
    _rr(cv, (24, 46, 38, 72), C["sky"], 4, lw=4)
    _rr(cv, (43, 30, 57, 72), C["sun"], 4, lw=4)
    _rr(cv, (62, 54, 76, 72), C["rose"], 4, lw=4)


def icon_clipboard(cv: Canvas) -> None:
    """评估报告：带夹子的记录板。"""
    _rr(cv, (22, 22, 74, 80), C["paper"], 9)
    _rr(cv, (36, 14, 60, 28), C["coral"], 5)
    for y in (40, 52, 64):
        cv.d.rounded_rectangle(cv.box(32, y, 64, y + 5), radius=3, fill=hex2rgb(INK) + (120,))


def icon_search(cv: Canvas) -> None:
    """搜索：放大镜。"""
    _ring(cv, 44, 44, 22, C["sky"], lw=6)
    cv.d.line(cv.p(60, 60, 78, 78), fill=hex2rgb(INK) + (255,), width=int(8 * cv.img.width // cv.w))


def icon_cover(cv: Canvas) -> None:
    """无封面占位：一本书 + 星。"""
    _rr(cv, (24, 20, 72, 78), C["dim"], 6)
    cv.d.rounded_rectangle(cv.box(30, 26, 66, 72), radius=4, fill=hex2rgb(C["paper"]) + (255,))
    star(cv, 48, 48, 12, C["sun"], outline=INK, width=3.0)


def icon_lock(cv: Canvas) -> None:
    """未解锁：锁。"""
    _rr(cv, (26, 44, 70, 80), C["sun"], 8)
    cv.d.arc(
        cv.box(34, 22, 62, 52),
        start=180,
        end=360,
        fill=hex2rgb(INK) + (255,),
        width=int(7 * cv.img.width // cv.w),
    )
    _ring(cv, 48, 60, 6, C["paper"], lw=3)


def icon_party(cv: Canvas) -> None:
    """通过/升级/晒成功：庆祝（三角彩带筒 + 纸屑 + 星闪）。"""
    _blob(cv, [(24, 78), (36, 40), (62, 62)], C["rose"])
    _rr(cv, (18, 74, 34, 84), C["coral"], 4)
    for cx, cy, col in ((52, 26, C["sun"]), (72, 40, C["sky"]), (64, 18, C["mint"])):
        _ring(cv, cx, cy, 5, col, lw=3)
    sparkle(cv, 80, 70, 8, "#FFFFFF", 230)


def icon_tip(cv: Canvas) -> None:
    """提示：灯泡。"""
    _ring(cv, 48, 42, 20, C["sun"], lw=5)
    _rr(cv, (40, 60, 56, 76), C["dim"], 5)
    cv.d.line(cv.p(42, 68, 54, 68), fill=hex2rgb(INK) + (170,), width=int(4 * cv.img.width // cv.w))
    sparkle(cv, 76, 26, 7, "#FFFFFF", 225)


def icon_warning(cv: Canvas) -> None:
    """异常/无权限：三角感叹。"""
    _blob(cv, [(48, 16), (84, 78), (12, 78)], C["sun"])
    cv.d.line(cv.p(48, 40, 48, 58), fill=hex2rgb(INK) + (255,), width=int(7 * cv.img.width // cv.w))
    _ring(cv, 48, 68, 5, INK, lw=0)


def icon_star(cv: Canvas) -> None:
    """星级（点亮）：亮黄实心 + 墨线勾边。

    2026-09-17 三轮定档：
    ① 原 sun(#FFC94D) 落在 PASSED 金卡（sun→accent-light 渐变）上时，星心 (246,202,100)
       与卡底 (243,198,103) 只差 3 个色阶 → 填充隐形只剩墨线，用户报「星星变空心」；
    ② 压深到 #F59E0B → 用户「太暗了」；
    ③ 提到 #FBBF24 → 用户「没有变亮」（amber 到 amber 幅度不够感知）。
    定档亮黄 #FFD84D + 半径 36→40（44rpx 框里星星从占 75% 提到 83%，视觉更大）。
    星星垫在 85% 白药丸上（book-detail.wxss .qhc-stars），
    与药丸底的亮度差 34、蓝通道差 149——形状靠墨线、底色靠色相，两头都立得住。"""
    star(cv, 48, 48, 40, "#FFD84D", outline=INK, width=4.5)


def icon_star_off(cv: Canvas) -> None:
    """星级（未点亮）：米白实心（比金卡底色更亮，读作"空星"）+ 墨线勾边。
    半径/描边与 icon_star 保持一致，否则同一行里亮星暗星大小不一。"""
    star(cv, 48, 48, 40, C["dim"], outline=INK, width=4.5)


def icon_globe(cv: Canvas) -> None:
    """全馆播报：地球（一条赤道弧 + 一条经线，线细一点）。"""
    _ring(cv, 48, 48, 32, C["sky"], lw=5)
    cv.d.arc(
        cv.box(20, 34, 76, 62),
        start=0,
        end=360,
        fill=hex2rgb(INK) + (120,),
        width=int(3 * cv.img.width // cv.w),
    )
    cv.d.arc(
        cv.box(34, 18, 62, 78),
        start=90,
        end=270,
        fill=hex2rgb(INK) + (120,),
        width=int(3 * cv.img.width // cv.w),
    )


def icon_sparkle(cv: Canvas) -> None:
    """馆长推荐/晒卡入口：大星闪。"""
    sparkle(cv, 48, 40, 26, C["sun",] if False else C["sun"], 255)
    sparkle(cv, 70, 66, 14, C["rose"], 235)
    sparkle(cv, 26, 68, 11, C["sky"], 225)


def icon_edit(cv: Canvas) -> None:
    """编辑称呼：铅笔。"""
    _blob(cv, [(24, 74), (32, 52), (64, 22), (76, 34), (46, 64)], C["sun"])
    cv.d.line(cv.p(60, 26, 72, 38), fill=hex2rgb(INK) + (200,), width=int(4 * cv.img.width // cv.w))


def icon_arrow_down(cv: Canvas) -> None:
    """转让方向：向下箭头。"""
    _blob(cv, [(40, 16), (56, 16), (56, 52), (74, 52), (48, 80), (22, 52), (40, 52)], C["mint"])


def icon_ticket(cv: Canvas) -> None:
    """活动/入场券：票 + 撕口虚线。"""
    _rr(cv, (16, 30, 80, 70), C["sun"], 8)
    cv.d.line(cv.p(56, 34, 56, 66), fill=hex2rgb(INK) + (200,), width=int(4 * cv.img.width // cv.w))
    for y in (38, 46, 54, 62):
        cv.d.ellipse(
            cv.box(34, y - 3, 42, y + 3),
            fill=hex2rgb(C["sun"]) + (255,),
            outline=hex2rgb(INK) + (200,),
            width=int(2 * cv.img.width // cv.w),
        )


def icon_refund(cv: Canvas) -> None:
    """退款申请：硬币 + 回转箭头。"""
    _ring(cv, 40, 52, 24, C["sun"], lw=5)
    cv.d.arc(
        cv.box(26, 38, 54, 66),
        start=40,
        end=300,
        fill=hex2rgb(INK) + (200,),
        width=int(5 * cv.img.width // cv.w),
    )
    _blob(cv, [(24, 30), (40, 22), (42, 40)], C["sun"], lw=4)


def icon_crown(cv: Canvas) -> None:
    """会员：王冠。"""
    _blob(cv, [(22, 34), (34, 54), (48, 30), (62, 54), (74, 34), (72, 72), (24, 72)], C["sun"])
    _rr(cv, (24, 72, 72, 82), C["coral"], 5)


def icon_door(cv: Canvas) -> None:
    """退会：门 + 出场箭头。"""
    _rr(cv, (26, 16, 62, 82), C["mint"], 6)
    _ring(cv, 54, 52, 4, C["paper"], lw=2)
    cv.d.line(cv.p(62, 50, 84, 50), fill=hex2rgb(INK) + (255,), width=int(6 * cv.img.width // cv.w))
    _blob(cv, [(76, 40), (88, 50), (76, 60)], C["sun"], lw=4)


def icon_transfer(cv: Canvas) -> None:
    """权益转让：两条对向箭头。"""
    _rr(cv, (18, 34, 62, 44), C["sky"], 5, lw=4)
    _blob(cv, [(56, 26), (74, 39), (56, 52)], C["sky"], lw=4)
    _rr(cv, (34, 56, 78, 66), C["rose"], 5, lw=4)
    _blob(cv, [(40, 48), (22, 61), (40, 74)], C["rose"], lw=4)


def icon_clock(cv: Canvas) -> None:
    """时间：时钟。"""
    _ring(cv, 48, 48, 30, C["paper"], lw=5)
    cv.d.line(cv.p(48, 48, 48, 30), fill=hex2rgb(INK) + (230,), width=int(6 * cv.img.width // cv.w))
    cv.d.line(cv.p(48, 48, 62, 56), fill=hex2rgb(INK) + (230,), width=int(6 * cv.img.width // cv.w))


def icon_pin(cv: Canvas) -> None:
    """地点：定位针。"""
    _blob(cv, [(48, 14), (72, 38), (60, 62), (48, 84), (36, 62), (24, 38)], C["rose"])
    _ring(cv, 48, 40, 10, C["paper"], lw=4)


def icon_heart(cv: Canvas) -> None:
    """收藏：实心心（已收藏）。"""
    cv.d.pieslice(
        cv.box(20, 24, 52, 56),
        start=180,
        end=360,
        fill=hex2rgb(C["rose"]) + (255,),
        outline=hex2rgb(INK) + (255,),
        width=int(5 * cv.img.width // cv.w),
    )
    cv.d.pieslice(
        cv.box(44, 24, 76, 56),
        start=180,
        end=360,
        fill=hex2rgb(C["rose"]) + (255,),
        outline=hex2rgb(INK) + (255,),
        width=int(5 * cv.img.width // cv.w),
    )
    _blob(cv, [(22, 44), (74, 44), (48, 78)], C["rose"])


def icon_heart_off(cv: Canvas) -> None:
    """收藏：空心底（未收藏）。"""
    cv.d.pieslice(
        cv.box(20, 24, 52, 56),
        start=180,
        end=360,
        fill=hex2rgb(C["paper"]) + (255,),
        outline=hex2rgb(INK) + (255,),
        width=int(5 * cv.img.width // cv.w),
    )
    cv.d.pieslice(
        cv.box(44, 24, 76, 56),
        start=180,
        end=360,
        fill=hex2rgb(C["paper"]) + (255,),
        outline=hex2rgb(INK) + (255,),
        width=int(5 * cv.img.width // cv.w),
    )
    _blob(cv, [(22, 44), (74, 44), (48, 78)], C["paper"])


def icon_empty(cv: Canvas) -> None:
    """空态：敞开的纸箱（箱体 + 两片翻开的盖板 + 星点）。"""
    _blob(cv, [(30, 46), (78, 46), (70, 80), (38, 80)], C["paper"])
    _blob(cv, [(30, 46), (50, 42), (44, 26), (24, 32)], C["dim"])
    _blob(cv, [(78, 46), (58, 42), (64, 26), (84, 32)], C["dim"])
    sparkle(cv, 84, 66, 8, "#FFFFFF", 235)
    sparkle(cv, 20, 54, 7, "#FFFFFF", 215)


def icon_phone(cv: Canvas) -> None:
    """手机号输入：手机。"""
    _rr(cv, (30, 14, 66, 82), C["sky"], 10)
    cv.d.rounded_rectangle(cv.box(36, 26, 60, 64), radius=4, fill=hex2rgb(C["paper"]) + (255,))
    _ring(cv, 48, 73, 4, C["paper"], lw=2)


def icon_key(cv: Canvas) -> None:
    """验证码输入：钥匙。"""
    _ring(cv, 34, 40, 16, C["sun"], lw=5)
    _ring(cv, 34, 40, 5, C["paper"], lw=3)
    cv.d.line(cv.p(46, 52, 76, 78), fill=hex2rgb(INK) + (255,), width=int(7 * cv.img.width // cv.w))
    cv.d.line(cv.p(62, 66, 70, 58), fill=hex2rgb(INK) + (255,), width=int(6 * cv.img.width // cv.w))


def icon_wallet(cv: Canvas) -> None:
    """押金：钱包（带扣）。"""
    _rr(cv, (16, 30, 80, 76), C["mint"], 10)
    _rr(cv, (16, 44, 80, 60), C["sun"], 6, lw=4)
    _ring(cv, 66, 52, 6, C["paper"], lw=3)


def icon_receipt(cv: Canvas) -> None:
    """订单：小票（锯齿底边 + 三条明细）。"""
    _blob(
        cv,
        [(28, 16), (68, 16), (68, 74), (60, 68), (52, 74), (44, 68), (36, 74), (28, 68)],
        C["paper"],
    )
    for y in (30, 42, 54):
        cv.d.rounded_rectangle(cv.box(36, y, 60, y + 5), radius=3, fill=hex2rgb(INK) + (130,))


def icon_child(cv: Canvas) -> None:
    """名片/无头像占位：小脸。"""
    _ring(cv, 48, 50, 30, C["sun"], lw=5)
    _ring(cv, 39, 46, 5, INK, lw=0)
    _ring(cv, 57, 46, 5, INK, lw=0)
    cv.d.arc(
        cv.box(38, 52, 58, 68),
        start=0,
        end=180,
        fill=hex2rgb(INK) + (255,),
        width=int(5 * cv.img.width // cv.w),
    )


ICONS = {
    "bell": icon_bell,
    "book": icon_book,
    "bookmark": icon_bookmark,
    "headphone": icon_headphone,
    "calendar": icon_calendar,
    "trophy": icon_trophy,
    "card": icon_card,
    "report": icon_report,
    "chart": icon_chart,
    "clipboard": icon_clipboard,
    "search": icon_search,
    "cover": icon_cover,
    "lock": icon_lock,
    "party": icon_party,
    "tip": icon_tip,
    "warning": icon_warning,
    "star": icon_star,
    "star-off": icon_star_off,
    "globe": icon_globe,
    "sparkle": icon_sparkle,
    "edit": icon_edit,
    "arrow-down": icon_arrow_down,
    "ticket": icon_ticket,
    "refund": icon_refund,
    "crown": icon_crown,
    "door": icon_door,
    "transfer": icon_transfer,
    "clock": icon_clock,
    "pin": icon_pin,
    "heart": icon_heart,
    "heart-off": icon_heart_off,
    "empty": icon_empty,
    "phone": icon_phone,
    "key": icon_key,
    "wallet": icon_wallet,
    "receipt": icon_receipt,
    "child": icon_child,
}


def render(name: str) -> Image.Image:
    cv = _new()
    ICONS[name](cv)
    paper_grain(cv, alpha=8)
    return cv.img.resize((ICON, ICON), Image.LANCZOS)


def main() -> int:
    sheet = "--sheet" in sys.argv
    os.makedirs(OUT_DIR, exist_ok=True)
    imgs = {}
    for name in ICONS:
        im = render(name)
        im.save(os.path.join(OUT_DIR, f"{name}.png"), "PNG")
        imgs[name] = im
    print(f"✓ 生成 {len(imgs)} 个图标 → {OUT_DIR}/")

    if sheet:
        cols, cell = 6, 120
        rows = (len(imgs) + cols - 1) // cols
        board = Image.new("RGB", (cols * cell, rows * (cell + 26)), "#F1E7D8")
        d = ImageDraw.Draw(board)
        for i, (name, im) in enumerate(sorted(imgs.items())):
            x, y = (i % cols) * cell + 12, (i // cols) * (cell + 26) + 12
            board.paste(im, (x, y), im)
            d.text((x + 4, y + cell - 4), name, fill="#3B2F2F")
        path = "/tmp/ui_icons_sheet.png"
        board.save(path)
        print(f"✓ 总览拼图：{path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
