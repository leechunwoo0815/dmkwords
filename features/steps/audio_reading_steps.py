# features/steps/audio_reading_steps.py — FEAT-038~041 音频收听与完播判定 步骤定义
"""2026-10-14（第五十七批·BDD 解封第四批）· **音频收听 14 场景**全部转真实执行。

判据与实现同源（动手前逐条亲验，红线 108/136）：
- **收听资格**（PRD §6.1 / R-313 AUDIO 行）：未缴费禁、在册听**全馆在架**、过期**仅手头在借**、
  退会禁。判定收口在 `identity.guards.check_audio`——**预检端点 / 音频流 / 心跳上报三处同源**
  （禁漂移）；拒绝语义：预检返回 `{allowed:false, reason}`，音频流与上报直接 403。
- **完播判定**（PRD §6.2）：只统计**真实播放过的区间**，区间并集 ÷ 总时长 ≥
  `audio_finish_threshold_percent`（默认 95%）才算读完——拖动跳过的不计、重复段只计一次、
  跨天/多次收听累加合并。阈值进配置，本文件用 30 分钟（1800s）音频做分母（背景给定）。
- **防刷 R-151**：单次上报 Δ覆盖 ≤ 服务端**墙钟**差 × 2.0（最大倍速）× 1.2（容差）+ 60s
  宽限（首次上报/网络抖动）；客户端声明的跨度一律不作依据。
  **墙钟不可回拨** → 测试用 `_backdate`/`_seed_progress` 把 `last_report_at` 拨回或直接造
  "上一次心跳发生在 N 秒前/昨天"的时刻态（与 `tests/unit/test_wm6_reading.py`、
  `growth_quiz_steps._finish_book` 同款手法），并保证每段 Δ覆盖都在容差内（否则是造数假红）。
- **首次完播三件事**（PRD §6.2）：记阅读时长（= **音频原始时长** `total//60`，2 倍速听完也记
  30 分钟）、生成**当天**打卡、解锁该书测验。重复听不重复记时长、不重复打卡。
- **打卡**（PRD §6.3）：当天首次完播即打卡、**不看测验结果**（读完就算）；同一本书不重复打卡；
  一天多本只算一天；连续按**自然日**算，断一天从 1 重算且**已发积分不回收**；
  跨零点按**判定达成的那一刻**归属日期。
"""

import json
from datetime import date, datetime, timedelta
from datetime import time as dtime
from pathlib import Path

from behave import given, then, when

from tests.unit.helpers import force_book_on
from tests.unit.test_wm10_concurrency import _db, _h

PHONE = "13981037941"  # 听书场景专用家长（与 growth/quiz 域手机号错开：家长手机号唯一）
BOOK_SECONDS = 1800  # 背景口径：音频全长 30 分钟


# ---------- 基建 ----------


def _db_session():
    return _db()


def _admin(context) -> dict:
    if getattr(context, "h", None) is None:
        context.h = _h(context.client)
    return context.h


def _parent(context) -> dict:
    """背景家长（手机号唯一）：未入会/过期等场景的孩子都挂在同一家长下。

    **一次登录**（`/api/miniapp/login` 限流 3 次/60s，见 environment.before_scenario 的重置）：
    手机 A 就是这次登录的 token，"换手机"场景只用掉第 2 次。
    """
    if getattr(context, "parent", None) is None:
        _admin(context)
        context.parent = context.client.post(
            "/api/admin/members/parents",
            json={"name": "听书家长", "phone": PHONE},
            headers=context.h,
        ).json()
        login = context.client.post(
            "/api/miniapp/login", json={"phone": PHONE, "code": "1234"}
        ).json()
        assert login.get("token"), login
        context.login = login
        context.mini_token = login["token"]
        context.mini = {"Authorization": f"Bearer {login['token']}"}
    return context.parent


def _add_child(context, name: str) -> dict:
    _parent(context)
    child = context.client.post(
        f"/api/admin/members/parents/{context.parent['id']}/children",
        json={"name": name},
        headers=context.h,
    ).json()
    assert child.get("id"), child
    return child


def _member_child(context, name: str = "小明") -> dict:
    """建档 + 押金已缴 + 观察期入会（音频门禁要求**有效会员**）。全程真实链路。"""
    child = _add_child(context, name)
    dep = context.client.post(
        f"/api/admin/deposits/children/{child['id']}/orders", headers=context.h
    ).json()
    r = context.client.post(
        f"/api/admin/orders/{dep['order_id']}/confirm-payment",
        json={"pay_method": "scan"},
        headers=context.h,
    )
    assert r.status_code == 200, r.text
    fee = context.client.post(
        "/api/admin/orders",
        json={"child_id": child["id"], "order_type": "observation_fee"},
        headers=context.h,
    ).json()
    r = context.client.post(
        f"/api/admin/orders/{fee['id']}/confirm-payment",
        json={"pay_method": "scan"},
        headers=context.h,
    )
    assert r.status_code == 200, r.text
    return child


def _child_db(child_id: int, **fields) -> None:
    from backend.domain.identity.models import Child

    with _db_session() as db:
        db.query(Child).filter(Child.id == child_id).update(fields)
        db.commit()


def _expire(child_id: int) -> None:
    """落到「会员已过期」态：观察期也在有效会员里 → 必须转正式会员且到期日已过才验得准。"""
    _child_db(child_id, member_status="formal", member_expire=date.today() - timedelta(days=1))


def _mk_audio_file(rel: str) -> None:
    """造真实音频文件（音频流端点有 isfile 校验；只为走通传输层，内容不解析）。"""
    from backend.config import get_settings

    full = Path(get_settings().UPLOADS_DIR) / rel
    full.parent.mkdir(parents=True, exist_ok=True)
    if not full.is_file():
        full.write_bytes(b"ID3fake-mp3")


def _book(
    context,
    title: str = "Dog Man",
    seconds: int = BOOK_SECONDS,
    questions: int = 5,
) -> dict:
    """建书（上架）+ 1 副本 + 音频（**时长即完播分母**）+ N 道判断题（解锁场景要用）。"""
    created = context.client.post(
        "/api/admin/books",
        json={"isbn": None, "title": title, "word_count": 2500, "copy_count": 1},
        headers=context.h,
    )
    assert created.status_code == 200, created.text
    book = created.json()
    force_book_on(context.client, context.h, book["id"])
    rel = f"bdd/audio-{book['id']}.mp3"
    _mk_audio_file(rel)
    from backend.domain.catalog.models import Book

    with _db_session() as db:
        db.query(Book).filter(Book.id == book["id"]).update(
            {"audio_path": rel, "audio_duration_seconds": seconds}
        )
        db.commit()
    for i in range(questions):
        q = context.client.post(
            f"/api/admin/books/{book['id']}/questions",
            json={
                "question_type": "boolean",
                "question_text": f"{title} 第 {i + 1} 题",
                "options": ["对", "错"],
                "answer": "对",
            },
            headers=context.h,
        )
        assert q.status_code == 200, q.text
    copies = context.client.get(f"/api/admin/books/{book['id']}/copies", headers=context.h).json()
    return {
        "id": book["id"],
        "title": title,
        "copy_id": copies[0]["id"],
        "seconds": seconds,
        "questions": questions,
    }


def _borrow(context, child: dict, book: dict) -> None:
    r = context.client.post(
        "/api/admin/circulation/borrow",
        json={"child_id": child["id"], "copy_id": book["copy_id"]},
        headers=context.h,
    )
    assert r.status_code == 200, r.text


# ---------- 音频三入口（预检 / 流 / 心跳） ----------


def _permission(context, child: dict, book: dict) -> dict:
    r = context.client.get(
        f"/api/miniapp/books/{book['id']}/audio-permission",
        params={"child_id": child["id"]},
        headers=context.mini,
    )
    assert r.status_code == 200, r.text
    return r.json()


def _stream(context, child: dict, book: dict, token: str | None = None):
    """音频流：query token（innerAudioContext 无法携带 Authorization 头）。"""
    return context.client.get(
        f"/api/miniapp/books/{book['id']}/audio",
        params={"child_id": child["id"], "token": token or context.mini_token},
    )


def _report(context, child: dict, book_id: int, position: int, session_start: int):
    return context.client.post(
        "/api/miniapp/reading/progress",
        json={
            "child_id": child["id"],
            "book_id": book_id,
            "position": position,
            "session_start": session_start,
        },
        headers=context.mini,
    )


def _quiz_view(context, child: dict, book_id: int) -> dict:
    r = context.client.get(
        f"/api/miniapp/quiz/{book_id}",
        params={"child_id": child["id"]},
        headers=context.mini,
    )
    assert r.status_code == 200, r.text
    return r.json()


# ---------- 进度/打卡造数与探针 ----------


def _progress_db(child_id: int, book_id: int):
    from backend.domain.reading.models import ReadingProgress

    with _db_session() as db:
        return (
            db.query(ReadingProgress)
            .filter(
                ReadingProgress.child_id == child_id,
                ReadingProgress.book_id == book_id,
                ReadingProgress.is_deleted == 0,
            )
            .first()
        )


def _backdate(child_id: int, book_id: int, seconds: int) -> None:
    """把 `last_report_at` 拨回 N 秒（防刷按服务端墙钟算，测试须模拟时间流逝）。"""
    from backend.domain.reading.models import ReadingProgress

    with _db_session() as db:
        row = (
            db.query(ReadingProgress)
            .filter(
                ReadingProgress.child_id == child_id,
                ReadingProgress.book_id == book_id,
                ReadingProgress.is_deleted == 0,
            )
            .first()
        )
        assert row is not None, "进度行不存在（拨钟前必须先有一次上报）"
        row.last_report_at = row.last_report_at - timedelta(seconds=seconds)
        db.commit()


def _seed_progress(
    child_id: int,
    book_id: int,
    *,
    intervals: str,
    coverage: int,
    last_position: int,
    last_report_at: datetime,
    total: int = BOOK_SECONDS,
    finished: int = 0,
    reading_minutes: int = 0,
) -> None:
    """直插进度行：造"上一次心跳发生在昨天 23:50 / N 秒前"这类**时刻态**。

    墙钟改不了（跑测时刻由真实时钟决定），而防刷判据正是拿墙钟算的 —— 只能造数，
    与"不可达态用 DB 直插构造"同一手法（`docs/09` 第五十六轮 §三 第 4 条）。
    """
    from backend.domain.reading.models import ReadingProgress

    with _db_session() as db:
        db.add(
            ReadingProgress(
                child_id=child_id,
                book_id=book_id,
                intervals=intervals,
                coverage_seconds=coverage,
                total_seconds=total,
                finished=finished,
                last_position=last_position,
                last_report_at=last_report_at,
                reading_minutes=reading_minutes,
                is_deleted=0,
            )
        )
        db.commit()


def _shift_or_seed(child_id: int, book_id: int, seconds: int, total: int = BOOK_SECONDS) -> None:
    """把墙钟基准往前拨 N 秒；还没有进度行就先造一行"刚开播、上次心跳在 N 秒前"。"""
    if _progress_db(child_id, book_id) is None:
        _seed_progress(
            child_id,
            book_id,
            intervals="[]",
            coverage=0,
            last_position=0,
            last_report_at=datetime.now() - timedelta(seconds=seconds),
            total=total,
        )
    else:
        _backdate(child_id, book_id, seconds)


def _set_last_report(child_id: int, book_id: int, moment: datetime) -> None:
    """把上次上报时刻设成某个具体时刻（跨天/跨零点场景用）。"""
    from backend.domain.reading.models import ReadingProgress

    with _db_session() as db:
        db.query(ReadingProgress).filter(
            ReadingProgress.child_id == child_id,
            ReadingProgress.book_id == book_id,
            ReadingProgress.is_deleted == 0,
        ).update({"last_report_at": moment})
        db.commit()


def _checkins(child_id: int) -> list:
    from backend.domain.reading.models import CheckIn

    with _db_session() as db:
        return (
            db.query(CheckIn)
            .filter(CheckIn.child_id == child_id, CheckIn.is_deleted == 0)
            .order_by(CheckIn.checkin_date)
            .all()
        )


def _seed_checkin(child_id: int, book_id: int, days_ago: int, streak: int) -> None:
    """直插历史打卡行（"过去某天有没有打卡"只能造数：日期是自然日，改不了）。"""
    from backend.domain.reading.models import CheckIn

    with _db_session() as db:
        db.add(
            CheckIn(
                child_id=child_id,
                checkin_date=date.today() - timedelta(days=days_ago),
                book_id=book_id,
                streak=streak,
                is_deleted=0,
            )
        )
        db.commit()


def _points_rows(child_id: int) -> list:
    from backend.domain.growth.models import PointLedger

    with _db_session() as db:
        return (
            db.query(PointLedger)
            .filter(PointLedger.child_id == child_id, PointLedger.is_deleted == 0)
            .order_by(PointLedger.id)
            .all()
        )


# ---------- 播放（含防刷口径） ----------


def _play(
    context,
    child: dict,
    book_id: int,
    start: int,
    end: int,
    wall: int = 0,
    expect: int = 200,
    total: int = BOOK_SECONDS,
):
    """上报一段真实播放 [start, end]；`wall` = 模拟这一段的墙钟耗时（先把基准拨回 `wall` 秒）。

    R-151：单段 Δ覆盖 ≤ 墙钟 × 2.0 × 1.2 + 60s（首次上报宽限）。每段都必须落在容差内，
    否则测出来的"拒绝"是造数造成的假红（红线 136：断言先自问"可能是假的吗"）。
    还没有进度行时（本次是**首次心跳**），`wall` 落成"上一条心跳是不可能的 —— 播放器已跑了
    `wall` 秒"的基准行（见 `_shift_or_seed`），语义与"上一拍在 wall 秒前"一致。
    """
    if wall:
        _shift_or_seed(child["id"], book_id, wall, total)
    r = _report(context, child, book_id, end, start)
    assert r.status_code == expect, (
        f"上报 [{start},{end}] 期望 {expect} 实得 {r.status_code}：{r.text[:200]}"
    )
    return r


def _finish(
    context, child: dict, book: dict, *, chunks: int = 4, wall_each: int = 225
) -> list[dict]:
    """听完整本（区间并集 100%）：把书切成 `chunks` 段逐段上报，每段配 `wall_each` 秒墙钟。

    默认 4 段 × 450s 内容、每段 225s 墙钟 → 墙钟合计 900s = 音频 1800s 的**恰好一半**
    （2 倍速口径，PRD §6.2 判读完）；单段 Δ覆盖 450 ≤ 225×2.4+60 = 600 ✓（R-151 容差内）。
    """
    total = book["seconds"]
    step = total // chunks
    views: list[dict] = []
    start = 0
    for i in range(chunks):
        end = total if i == chunks - 1 else start + step
        views.append(
            _play(context, child, book["id"], start, end, wall=wall_each, total=total).json()
        )
        start = end
    return views


# ================= 场景步骤 =================


@given('孩子 "{child_name}" 是有效会员 且《{title}》音频全长 {minutes:d} 分钟 在架可听')
def step_bg(context, child_name, title, minutes):
    context.ming = _member_child(context, child_name)
    context.book = _book(context, title, seconds=minutes * 60)
    context.audio_minutes = minutes


@then("小明无需借阅《{title}》也可播放其音频")
def step_play_without_borrow(context, title):
    """有效会员听**全馆在架**（PRD §6.1）：没借这本书照样能听、能上报。
    边界：额度被这本书占住不是前提——先钉死"零借阅记录"。"""
    from backend.domain.circulation.models import BorrowRecord

    child, book = context.ming, context.book
    with _db_session() as db:
        borrows = (
            db.query(BorrowRecord)
            .filter(
                BorrowRecord.child_id == child["id"],
                BorrowRecord.book_id == book["id"],
                BorrowRecord.is_deleted == 0,
            )
            .count()
        )
    assert borrows == 0, "本场景前提是**这本书从未借过**"
    assert _permission(context, child, book) == {"allowed": True, "reason": "ok"}
    stream = _stream(context, child, book)
    assert stream.status_code == 200, stream.text
    assert stream.headers["content-type"].startswith("audio/"), stream.headers
    first = _report(context, child, book["id"], 10, 0)
    assert first.status_code == 200, first.text
    assert first.json()["coverage_seconds"] == 10, first.json()


@given('孩子 "{child_name}" 尚未入会')
def step_unpaid_child(context, child_name):
    # 措辞与 growth_quiz_steps 的同义步骤（`孩子 "{child_name}" 未入会`）错开一个字：
    # behave 步骤表**全局唯一**（红线 144），那边的定义是榜单造数助手（顺带撒 5000 词）不可借用。
    context.unpaid = _add_child(context, child_name)


@when("{child_name}尝试播放任一音频")
def step_try_every_audio(context, child_name):
    # 注意：特征文件里"小红尝试…"中间**没有空格**，步骤文本必须逐字对齐（behave 不做中文分词）
    child = context.unpaid
    perm = _permission(context, child, context.book)
    assert perm["allowed"] is False and perm["reason"] == "unpaid", perm
    stream = _stream(context, child, context.book)
    assert stream.status_code == 403, stream.text
    assert "需入会" in stream.json()["detail"], stream.text
    context.denied_report = _report(context, child, context.book["id"], 10, 0)


@then("系统拒绝播放")
def step_refuse_play(context):
    assert context.denied_report.status_code == 403, context.denied_report.text
    assert "入会" in context.denied_report.json()["detail"], context.denied_report.text
    # 零副作用：拿不到收听权就不该留下任何进度行
    assert _progress_db(context.unpaid["id"], context.book["id"]) is None, "被拒路径不得写进度"


@given("小明会员已过期 且手里有 2 本未还")
def step_expired_with_holding(context):
    child = context.ming
    # 顺序很重要：借阅要求**有效会员** → 先借满 2 本，再把会员落成"过期"
    context.holding = [context.book, _book(context, "过期在借样书二", seconds=600)]
    for b in context.holding:
        _borrow(context, child, b)
    _expire(child["id"])
    context.outside = _book(context, "过期不可听样书", seconds=600)


@then("小明仅能播放这 2 本的音频")
def step_expired_only_holding(context):
    child = context.ming
    for b in context.holding:
        assert _permission(context, child, b) == {"allowed": True, "reason": "ok"}, b["title"]
        assert _stream(context, child, b).status_code == 200, b["title"]
    perm = _permission(context, child, context.outside)
    assert perm["allowed"] is False and perm["reason"] == "expired", perm
    stream = _stream(context, child, context.outside)
    assert stream.status_code == 403, stream.text
    assert "只能收听手中在借" in stream.json()["detail"], stream.text
    # 边界（"在手"= 在借）：还掉一本 → 那本的收听权立刻收回（另一本仍在手）
    ret = context.client.post(
        "/api/admin/circulation/return",
        json={"copy_id": context.holding[0]["copy_id"], "condition": "normal"},
        headers=context.h,
    )
    assert ret.status_code == 200, ret.text
    assert _permission(context, child, context.holding[0])["allowed"] is False
    assert _permission(context, child, context.holding[1])["allowed"] is True


@when("小明在手机 A 听到第 15 分钟退出")
def step_phone_a(context):
    # 手机 A = 背景那次登录（同一家长）；进度存在**服务器**上，与设备无关。
    # 真实播到第 15 分钟：900s 内容 / 900s 墙钟（1 倍速），首段由基准行承载墙钟
    _play(context, context.ming, context.book["id"], 0, 900, wall=900)


@when("小明在手机 B 打开同一本书")
def step_phone_b(context):
    parent = _parent(context)
    login = context.client.post("/api/miniapp/login", json={"phone": PHONE, "code": "1234"}).json()
    assert login.get("token"), login
    context.mini_b = {"Authorization": f"Bearer {login['token']}"}
    assert login.get("parent", {}).get("id", parent["id"]) == parent["id"], (
        "手机 B 必须是**同一个家长**换设备登录"
    )
    r = context.client.get(
        f"/api/miniapp/books/{context.book['id']}/progress",
        params={"child_id": context.ming["id"]},
        headers=context.mini_b,
    )
    assert r.status_code == 200, r.text
    context.device_b = r.json()


@then("播放从第 15 分钟继续")
def step_resume_from(context):
    view = context.device_b
    assert view["last_position"] == 900, view
    assert view["coverage_seconds"] == 900 and view["coverage_percent"] == 50.0, view
    assert view["finished"] is False, view


@when("小明分两天累计真实播放覆盖全书 100%")
def step_two_days(context):
    child, book = context.ming, context.book
    # 第一天：真实播到 40%（[0,720]），当天没收完就睡了
    _play(context, child, book["id"], 0, 60)
    day1 = _play(context, child, book["id"], 60, 720, wall=300)
    assert day1.json()["finished"] is False, day1.json()
    # 跨天：把"上次心跳"落到昨天 21:00（一夜过去）
    _set_last_report(
        child["id"],
        book["id"],
        datetime.combine(date.today() - timedelta(days=1), dtime(21, 0)),
    )
    # 第二天：接着听完整本
    _play(context, child, book["id"], 720, 1440, wall=300)
    context.last = _play(context, child, book["id"], 1440, 1800, wall=300)


@then("该书判定为读完 且仅首次触发记录")
def step_finished_once(context):
    view = context.last.json()
    assert view["finished"] is True and view["just_finished"] is True, view
    assert view["coverage_seconds"] == BOOK_SECONDS and view["coverage_percent"] == 100.0, view
    p = _progress_db(context.ming["id"], context.book["id"])
    assert json.loads(p.intervals) == [[0, BOOK_SECONDS]], p.intervals
    # 仅首次触发：紧接着再报同一段 → 已完成态不再"首次"
    again = _report(context, context.ming, context.book["id"], BOOK_SECONDS, BOOK_SECONDS - 60)
    assert again.status_code == 200, again.text
    assert again.json()["just_finished"] is False, again.json()


@when("小明播放到第 5 分钟后直接拖动到最后一秒")
def step_drag_to_end(context):
    child, book = context.ming, context.book
    _play(context, child, book["id"], 0, 60)
    _play(context, child, book["id"], 60, 300, wall=250)
    # 末秒那一小段要真的花掉 1 秒墙钟（`wall=1`，判定口径 `Δt×2.4`）——
    # 2026-10-16 订正：原先不给墙钟（=上报距上一拍 0 秒却声称覆盖了 1 秒音频），
    # 属于**物理上不可能的造数**；宽限改为"只给首笔"（P1 #1）后当场被 422 拒，
    # 暴露的正是这处造假（判据本身没问题：真实客户端是 onSeeked 记新起点、下一次心跳
    # 才上报新段，中间自然有 ≥10 秒墙钟）。
    context.seek = _play(context, child, book["id"], BOOK_SECONDS - 1, BOOK_SECONDS, wall=1)


@then("覆盖仅计 5 分钟多一点 判定未读完")
def step_drag_verify(context):
    p = _progress_db(context.ming["id"], context.book["id"])
    assert json.loads(p.intervals) == [[0, 300], [1799, 1800]], p.intervals
    assert p.coverage_seconds == 301, p.coverage_seconds
    assert context.seek.json()["finished"] is False, context.seek.json()
    assert p.finished == 0, "拖过去的那 25 分钟没有真实播放"


@when("小明把第 10 到 20 分钟反复听了 5 遍其余未听")
def step_repeat_segment(context):
    child, book = context.ming, context.book
    # 从第 10 分钟起播（首段只能覆盖 60s 宽限内的量，之后就正常了）
    _play(context, child, book["id"], 600, 660)
    _play(context, child, book["id"], 660, 1200, wall=280)
    for _ in range(4):
        _play(context, child, book["id"], 600, 1200, wall=280)


@then("覆盖仅计 10 分钟 判定未读完")
def step_repeat_verify(context):
    p = _progress_db(context.ming["id"], context.book["id"])
    assert json.loads(p.intervals) == [[600, 1200]], f"重复段只计一次：{p.intervals}"
    assert p.coverage_seconds == 600, p.coverage_seconds
    assert p.finished == 0, "只听了 1/3（600/1800）不能判读完"


@when("小明用 2 倍速实际花 15 分钟听完全书")
def step_double_speed(context):
    child, book = context.ming, context.book
    context.finish_views = _finish(context, child, book, chunks=4, wall_each=225)
    context.wall_used = 225 * 4  # 4 段各 225s 墙钟
    assert context.wall_used * 2 == book["seconds"], "构造前提：墙钟正好是音频时长的一半"


@then("判定读完 且阅读时长记录为音频原始时长 {minutes:d} 分钟")
def step_double_speed_verify(context, minutes):
    view = context.finish_views[-1]
    assert view["finished"] is True and view["just_finished"] is True, view
    assert view["coverage_percent"] == 100.0, view
    p = _progress_db(context.ming["id"], context.book["id"])
    assert p.reading_minutes == minutes == context.audio_minutes, (
        f"阅读时长按**音频原始时长**记（{minutes} 分钟），不按实际花掉的 15 分钟："
        f"{p.reading_minutes}"
    )


@when("上报数据声称 1 分钟内覆盖了 10 分钟的音频内容")
def step_cheat_report(context):
    child, book = context.ming, context.book
    # 先来一段**合法**上报（首段吃 60s 宽限：Δ覆盖 60 ≤ 0×2.4+60）
    _play(context, child, book["id"], 0, 60)
    # 再声称：1 分钟墙钟（拨回 60s）里覆盖了 10 分钟内容（Δ600 > 60×2.4+60=204）
    context.cheat = _play(context, child, book["id"], 60, 660, wall=60, expect=422)


@then("服务端校验覆盖增速超过容差 拒绝该次上报")
def step_cheat_verify(context):
    detail = context.cheat.json()["detail"]
    assert "覆盖增速超过物理可能" in detail, detail
    # 零副作用：被拒的上报不得改动任何进度字段（"拒绝了但已经写进去"是最坏的假绿）
    p = _progress_db(context.ming["id"], context.book["id"])
    assert p.coverage_seconds == 60 and json.loads(p.intervals) == [[0, 60]], (
        p.coverage_seconds,
        p.intervals,
    )
    assert p.last_position == 60, p.last_position
    assert p.finished == 0, p.finished


@when("小明首次达成完播判定")
def step_first_finish(context):
    context.finish_views = _finish(context, context.ming, context.book)


@then("阅读时长记录一次")
def step_minutes_recorded(context):
    p = _progress_db(context.ming["id"], context.book["id"])
    assert p.finished == 1 and p.finished_at is not None, (p.finished, p.finished_at)
    assert p.reading_minutes == context.audio_minutes, p.reading_minutes


@then("当天打卡生成")
def step_checkin_today(context):
    rows = _checkins(context.ming["id"])
    assert len(rows) == 1 and rows[0].checkin_date == date.today(), [
        (str(r.checkin_date), r.streak) for r in rows
    ]
    assert context.finish_views[-1]["checkin"]["checked_in"] is True, context.finish_views[-1]


@then("该书测验解锁")
def step_quiz_unlock(context):
    data = _quiz_view(context, context.ming, context.book["id"])
    assert data["unlocked"] is True and data["status"] == "available", data
    assert data["attempts_used"] == 0, data


@given("小明已读完《{title}》")
def step_already_finished(context, title):
    context.finish_views = _finish(context, context.ming, context.book)
    assert context.finish_views[-1]["finished"] is True, context.finish_views[-1]
    p = _progress_db(context.ming["id"], context.book["id"])
    context.finished_at_before = p.finished_at
    context.checkins_before = len(_checkins(context.ming["id"]))


@when("小明再次完整听一遍")
def step_relisten(context):
    # 隔天重听（把墙钟基准挪到 20 小时前 → 第二天再听一遍）
    _set_last_report(context.ming["id"], context.book["id"], datetime.now() - timedelta(hours=20))
    context.relisten_views = _finish(context, context.ming, context.book)


@then("不再记录时长、不重复打卡、测验保持已解锁")
def step_relisten_verify(context):
    p = _progress_db(context.ming["id"], context.book["id"])
    assert p.reading_minutes == context.audio_minutes, p.reading_minutes
    assert p.finished_at == context.finished_at_before, "完播时刻不得被重听刷新"
    assert all(v["just_finished"] is False for v in context.relisten_views), [
        v["just_finished"] for v in context.relisten_views
    ]
    rows = _checkins(context.ming["id"])
    assert len(rows) == context.checkins_before == 1, [
        (str(r.checkin_date), r.streak) for r in rows
    ]
    data = _quiz_view(context, context.ming, context.book["id"])
    assert data["unlocked"] is True and data["status"] == "available", data


@given("小明今天读完的书测验还没做")
def step_read_without_quiz(context):
    context.finish_views = _finish(context, context.ming, context.book)
    assert context.finish_views[-1]["finished"] is True, context.finish_views[-1]
    data = _quiz_view(context, context.ming, context.book["id"])
    assert data["attempts_used"] == 0 and data["status"] == "available", data


@then("今天打卡仍然有效")
def step_checkin_stands(context):
    r = context.client.get(
        "/api/miniapp/checkins",
        params={"child_id": context.ming["id"], "days": 30},
        headers=context.mini,
    )
    assert r.status_code == 200, r.text
    data = r.json()
    assert data["today_checked"] is True and data["current_streak"] >= 1, data
    assert str(date.today()) in data["dates"], data["dates"]


@given("小明连续打卡 {days:d} 天 第 {missed:d} 天未读完任何书")
def step_streak_broken(context, days, missed):
    child = context.ming
    # 今天是"第 missed+1 天"：往前数第 n 天的打卡落在 (missed+1-n) 天前，**第 missed 天留空**
    for n in range(1, days + 1):
        _seed_checkin(child["id"], context.book["id"], days_ago=missed + 1 - n, streak=n)
    rows = _checkins(child["id"])
    assert [r.streak for r in rows] == list(range(1, days + 1)), [
        (str(r.checkin_date), r.streak) for r in rows
    ]
    assert rows[-1].checkin_date == date.today() - timedelta(days=2), (
        f"第 {missed} 天（昨天）必须留空，否则测的不是中断：{[str(r.checkin_date) for r in rows]}"
    )
    # 中断前已获积分：走真实的管理端调整链（口径：中断不回收已发积分）
    adj = context.client.post(
        f"/api/admin/children/{child['id']}/points/adjust",
        json={"points": 30, "reason": "连续打卡中断前的历史奖励（B13）"},
        headers=context.h,
    )
    assert adj.status_code == 200, adj.text
    context.points_before = sum(r.points for r in _points_rows(child["id"]))
    assert context.points_before == 30, context.points_before


@when("第 {day:d} 天小明读完一本书")
def step_day_after_gap(context, day):
    book = _book(context, "中断后完播样书", seconds=600)
    context.gap_book = book
    context.gap_views = _finish(context, context.ming, book, chunks=4, wall_each=90)
    assert context.gap_views[-1]["finished"] is True, context.gap_views[-1]


@then("连续计数重新从 {start:d} 开始 已获积分不回收")
def step_streak_reset(context, start):
    view = context.gap_views[-1]
    checkin = view["checkin"]
    assert checkin["checked_in"] is True, checkin
    assert checkin["streak"] == start == 1, f"断了必须从 1 重算：{checkin}"
    assert checkin["date"] == str(date.today()), checkin
    rows = _checkins(context.ming["id"])
    today_row = next((r for r in rows if r.checkin_date == date.today()), None)
    assert today_row is not None, [(str(r.checkin_date), r.streak) for r in rows]
    assert today_row.streak == start == 1, f"断了必须从 1 重算：{today_row.streak}"
    assert len(rows) == 7, "历史 6 天原样保留，今天新起 1 行"
    r = context.client.get(
        "/api/miniapp/checkins",
        params={"child_id": context.ming["id"], "days": 30},
        headers=context.mini,
    )
    assert r.json()["current_streak"] == 1, r.json()
    # 已获积分不回收：中断前那 30 分仍在账上（今天打卡另有周期积分，故用 >= 与逐笔存在性判定）
    rows_points = _points_rows(context.ming["id"])
    assert any(p.points == 30 for p in rows_points), [
        (p.reason_type, p.points) for p in rows_points
    ]
    assert sum(p.points for p in rows_points) >= context.points_before, context.points_before


@when("小明 23:50 开始听 00:10 达成完播")
def step_cross_midnight(context):
    child, book = context.ming, context.book
    # 墙钟不可回拨 → 用两个**时刻态**造出"跨零点会话"：上一次心跳在昨天 23:50（差 100s 收完），
    # 且昨天已有打卡行。这样"按会话起点归属"与"按判定时刻归属"会给出可观测的不同结果：
    # 会话起点口径会判成"昨天已打卡"而漏发今天的卡；实现按**判定时刻**（今天）归属。
    _seed_checkin(child["id"], book["id"], days_ago=1, streak=5)
    _seed_progress(
        child["id"],
        book["id"],
        intervals="[[0,1700]]",
        coverage=1700,
        last_position=1700,
        last_report_at=datetime.combine(date.today() - timedelta(days=1), dtime(23, 50)),
    )
    context.cross = _play(context, child, book["id"], 1700, 1800)


@then("打卡记录归属第二天")
def step_cross_verify(context):
    view = context.cross.json()
    assert view["finished"] is True and view["just_finished"] is True, view
    checkin = view["checkin"]
    assert checkin["checked_in"] is True, checkin
    assert checkin["date"] == str(date.today()), checkin
    rows = _checkins(context.ming["id"])
    assert [str(r.checkin_date) for r in rows] == [
        str(date.today() - timedelta(days=1)),
        str(date.today()),
    ], [(str(r.checkin_date), r.streak) for r in rows]
    assert rows[1].streak == 6, f"昨天 5 → 今天 6（连续不断）：{rows[1].streak}"
    assert rows[0].streak == 5, "昨天那行不得被覆盖"
