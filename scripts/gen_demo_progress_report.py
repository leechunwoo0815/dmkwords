"""外教进度报告样例图生成器（评估报告页演示数据用，2026-09-21）。

**为什么需要**：PRD §3.3 承诺"报告会展示在小程序的孩子档案页"，但 seed 从无报告图——
验收家长端要么空态、要么是不可读的占位图（uploads 里 108B 的 1×1 PNG），
"家长看得清吗"这个唯一的验收点根本没法目视（T47-2 教训同族：**验收路径必须有造数**）。

**按真实形态造**：线下外教给的进度报告是竖版整页（用户给的样例 814×1143 = 1:1.40），
内容结构 = 抬头 + 学生/教师/课程信息栏 + GRADES 六级成绩 + Grading system + COMMENTS 评语框。
本生成器就按这个骨架出图，让小程序端的"整页展示 + 双指放大"能按真实比例被检验。

**出图口径**（docs/15 §十四）：走 `reading_circle/art.py` 的 Canvas / 令牌 / 字体，不自己 save；
落盘由 `Canvas.finish`（唯一出口）完成，调用方（seed）拿字节再交给 `file_storage.save_observation_image`
（M1 落盘单出口）。

用法（自看样子）：
    python -m scripts.gen_demo_progress_report --out /tmp/demo_report.jpg --student 观察期孩
"""

from __future__ import annotations

import argparse
import os
import tempfile

from backend.domain.reading_circle import art

#: 画布 = 样例比例 1:1.40（竖版整页文档）
W, H = 900, 1260
#: 报告里的成绩项（与外教样例同序：六项）
GRADE_ITEMS = ("Reading", "Writing", "Listening", "Speaking", "Attendance", "Assignments")
GRADING_SYSTEM = (
    ("A", "90 - 100"),
    ("B", "80 - 89"),
    ("C", "70 - 79"),
    ("D", "60 - 69"),
    ("E", "0 - 59"),
)
MARGIN = 44


def _font(size: int, *, round_=False):
    return art.font_round(size) if round_ else art.font_cn(size)


def _text(
    cv: art.Canvas,
    xy,
    s: str,
    size: int,
    color: str,
    *,
    alpha: int = 255,
    round_=False,
    anchor="lm",
):
    """左/自定义锚点的普通过字（sticker_text 默认描白边，表格正文不需要白边）。

    字体按**内容**选，不按调用方意愿选：`font_round`（SF NS Rounded）没有中文字形，
    拿它排"观察期孩"这类中文会直出**豆腐块**（本方首版实照即此形，已目视捕捉）。
    """
    use_round = round_ and s.isascii()
    art.sticker_text(
        cv, xy, s, _font(size, round_=use_round), color, anchor=anchor, stroke_w=0, alpha=alpha
    )


def _width(cv: art.Canvas, s: str, size: int, *, round_=False) -> float:
    """最终尺寸语义下的实测字宽（拿它排版，别靠目测写死 x）。"""
    font = _font(size, round_=round_ and s.isascii()).font_variant(size=int(size * art.SS))
    return cv.d.textlength(s, font=font) / art.SS


def _rule(
    cv: art.Canvas,
    x0: float,
    x1: float,
    y: float,
    color: str,
    *,
    width: float = 2.0,
    alpha: int = 90,
):
    cv.d.line(cv.p(x0, y, x1, y), fill=art.hex2rgb(color) + (alpha,), width=int(width * art.SS))


def _wrap(cv: art.Canvas, text: str, size: int, max_w: float) -> list[str]:
    """按实测宽度折行（不数空格猜宽度——中英混排必然错）。"""
    font = _font(size).font_variant(size=int(size * art.SS))
    lines: list[str] = []
    cur = ""
    for word in text.split():
        cand = f"{cur} {word}".strip()
        if cur and cv.d.textlength(cand, font=font) > max_w * art.SS:
            lines.append(cur)
            cur = word
        else:
            cur = cand
    if cur:
        lines.append(cur)
    return lines


def render_report(
    *,
    student: str,
    teacher: str,
    course: str,
    year: str,
    grades: dict[str, str] | None = None,
    comment: str = "",
    issue: int = 1,
    mascot_kind: str = "fox",
) -> bytes:
    """画一张外教进度报告（竖版整页），返回 JPEG 字节。"""
    from backend.domain.reading_circle.art_mascot import mascot as art_mascot

    grades = grades or {item: "A" for item in GRADE_ITEMS}
    pal = art.PALETTES["milestone"]  # 暖粉奶油档，与"给孩子的成绩单"语义相称
    cv = art.Canvas(W, H, pal)

    # ---- 页面装饰（只放在卡片外的安全边距，禁止压到正文） ----
    art.glow(cv, 780, 74, 120, "#FFFFFF", 90)
    art.sparkle(cv, 40, 118, 11, "#FFFFFF", 220)
    art.sparkle(cv, 866, 210, 10, "#FFFFFF", 205)
    art.star(cv, 34, 466, 10, "#FFFFFF", outline=pal["accent"], width=1.8, rotate=0.3)
    art.star(cv, 870, 640, 9, "#FFFFFF", outline=pal["accent"], width=1.7, rotate=-0.2)
    art.sparkle(cv, 36, 930, 10, "#FFFFFF", 200)

    # ---- 抬头横幅 ----
    art.bubble(
        cv, (MARGIN, 40, W - MARGIN, 186), radius=40, fill=pal["accent"], outline=art.INK, width=5
    )
    _text(cv, (W / 2, 92), "PROGRESS REPORT", 52, "#FFFFFF", round_=True, anchor="mm")
    _text(
        cv,
        (W / 2, 148),
        f"DmkWords 学习评估报告 · 第 {issue} 期",
        30,
        "#FFFFFF",
        alpha=235,
        anchor="mm",
    )

    # ---- 学生 / 教师 / 课程 信息栏 ----
    art.soft_shadow(cv, (MARGIN, 210, W - MARGIN, 400), radius=32, blur=10, alpha=48)
    art.bubble(
        cv, (MARGIN, 210, W - MARGIN, 400), radius=32, fill=art.PAPER, outline=art.INK, width=4
    )
    rows = (
        ("Student's name", student, "Year", year),
        ("Teacher's name", teacher, "", ""),
        ("Course/Level", course, "", ""),
    )
    # 值列 x 由**实测最宽标签**推出：首版写死 276，标签实际排到 ~270，值与下划线直接压在字上（已目视捕捉）
    label_x, label_size, gap = 78, 25, 22
    val_x = label_x + max(_width(cv, lbl, label_size) for lbl, *_ in rows) + gap
    for i, (lbl, val, lbl2, val2) in enumerate(rows):
        y = 258 + i * 56
        _text(cv, (label_x, y), lbl, label_size, art.INK, alpha=175)
        _text(cv, (val_x, y), val, 33, pal["deep"])
        _rule(cv, val_x - 8, 560, y + 16, pal["accent"], alpha=110)
        if lbl2:
            x2 = 596
            _text(cv, (x2, y), lbl2, label_size, art.INK, alpha=175)
            _text(
                cv, (x2 + _width(cv, lbl2, label_size) + gap, y), val2, 33, pal["deep"], round_=True
            )
            _rule(
                cv,
                x2 + _width(cv, lbl2, label_size) + gap - 8,
                816,
                y + 16,
                pal["accent"],
                alpha=110,
            )
    art_mascot(
        cv,
        802,
        356,
        34,
        kind=mascot_kind,
        fur=art.KIND_BASE[mascot_kind]["fur"],
        ear=art.KIND_BASE[mascot_kind]["ear"],
        blush=art.KIND_BASE[mascot_kind]["blush"],
    )

    # ---- GRADES ----
    _chip(cv, 300, 424, 600, 482, "GRADES 成绩", pal)
    art.bubble(cv, (MARGIN, 500, 560, 902), radius=32, fill=art.PAPER, outline=art.INK, width=4)
    for i, item in enumerate(GRADE_ITEMS):
        y = 546 + i * 58
        _text(cv, (80, y), item, 26, art.INK, alpha=205)
        art.bubble(
            cv, (392, y - 24, 528, y + 24), radius=20, fill="#FFFFFF", outline=art.INK, width=3
        )
        _text(cv, (460, y), grades.get(item, "—"), 30, pal["deep"], round_=True, anchor="mm")

    # ---- Grading system ----
    art.bubble(cv, (584, 500, W - MARGIN, 902), radius=32, fill=art.PAPER, outline=art.INK, width=4)
    _text(cv, (720, 546), "Grading system", 25, art.INK, alpha=205, anchor="mm")
    for i, (letter, rng) in enumerate(GRADING_SYSTEM):
        y = 596 + i * 56
        _text(cv, (622, y), letter, 27, pal["deep"], round_=True)
        _text(cv, (824, y), rng, 25, art.INK, alpha=190, round_=True, anchor="rm")

    # ---- COMMENTS ----
    _chip(cv, 300, 930, 600, 988, "COMMENTS 评语", pal)
    art.bubble(
        cv, (MARGIN, 1006, W - MARGIN, 1200), radius=32, fill=art.PAPER, outline=art.INK, width=4
    )
    wrapped = _wrap(cv, comment, 26, 700)
    lines = wrapped[:4]
    if len(wrapped) > 4:  # 截断必须留痕：首版直接砍在第 4 行中间，读起来像报告本身没写完
        lines[3] = (lines[3].rstrip() + "…") if lines[3] else "…"
    for i in range(5):  # 第 5 行留空 = 样张的"书写行"观感
        y = 1038 + i * 34
        if i < len(lines):
            _text(cv, (80, y), lines[i], 26, art.INK, alpha=235)
        _rule(cv, 76, 824, y + 16, art.INK, width=1.6, alpha=55)

    # ---- 页脚 ----
    _text(
        cv,
        (W / 2, 1234),
        "DmkWords 少儿英语分级阅读 · 外教评估报告",
        22,
        art.INK,
        alpha=150,
        anchor="mm",
    )

    fd, tmp = tempfile.mkstemp(suffix=".jpg", prefix="dmk_progress_report_")
    os.close(fd)
    try:
        cv.finish(tmp, quality=85)
        with open(tmp, "rb") as f:
            return f.read()
    finally:
        os.unlink(tmp)


def _chip(
    cv: art.Canvas, x0: float, y0: float, x1: float, y1: float, label: str, pal: dict
) -> None:
    """分区标题贴片（GRADES / COMMENTS 这类"章节名"）。"""
    art.bubble(cv, (x0, y0, x1, y1), radius=28, fill=art.PAPER, outline=art.INK, width=4)
    _text(cv, ((x0 + x1) / 2, (y0 + y1) / 2), label, 27, pal["deep"], anchor="mm")


def main() -> None:
    ap = argparse.ArgumentParser(description="生成外教进度报告样例图（演示数据）")
    ap.add_argument("--out", required=True, help="输出 jpg 路径")
    ap.add_argument("--student", default="观察期孩")
    ap.add_argument("--teacher", default="Demo Teacher")
    ap.add_argument("--course", default="Butterfly B")
    ap.add_argument("--year", default="2026")
    ap.add_argument("--issue", type=int, default=1)
    ap.add_argument(
        "--comment",
        default=(
            "He has been very engaged and shows great effort in his reading. "
            "His phonics and listening skills are progressing well, and he listens "
            "attentively in class. Speaking more during activities will help him "
            "practice pronunciation and improve his overall English fluency."
        ),
    )
    args = ap.parse_args()
    data = render_report(
        student=args.student,
        teacher=args.teacher,
        course=args.course,
        year=args.year,
        comment=args.comment,
        issue=args.issue,
    )
    with open(args.out, "wb") as f:
        f.write(data)
    print(f"已生成：{args.out}（{len(data) // 1024} KB，{W}×{H}）")


if __name__ == "__main__":
    main()
