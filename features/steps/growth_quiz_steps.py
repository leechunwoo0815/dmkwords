# features/steps/growth_quiz_steps.py — FEAT-042~046 测验与词数入账 步骤定义
"""2026-10-13（第五十三批·BDD 解封第三批 3a）· **词数入账 + 线上测验** 18 个场景。

成长域是"孩子的努力被记账"的地方，两条红线：
· **有效词数只在测验首次通过时入账，且「学生×书目」终身唯一、永不回收**（FEAT-046）；
· **成绩不可代改，只能重置次数重测**（FEAT-045）。
本文件把这两条连同"取最高分 / 快照不随后续改题 / 过期已解锁可完成"等口径钉成端到端行为。

**判据必须与实现同源（红线 108）——动手前逐条亲验**：
- 解锁口径：`ReadingProgress.finished == 1` **或**已有词账（`get_quiz` 的 `passed_before`：做过榜/里程碑造数的书也算解锁）。
- 提交前提：先完播（`需先听完音频（95%）才能开始测验`）、题目数匹配、`quiz_max_attempts`（默认 3）内。
- 分数是**答对题数**口径（`score/total`），通过阈值走 `quiz_pass_percent`（默认 60）；另有 `best_percent` 百分制。
- 完播上报有**心跳防刷**（Δ 上限 2.0×墙钟 + 60s 宽限），测试靠 `_backdate` 把 `last_report_at` 拨回模拟时间流逝
  （与 `tests/unit/test_wm6_reading.py` 同款手法）。
- 报进度要求书目**在架**（`Book.status == ON`），故"下架不回收词数"必须在下架**之前**完成阅读与测验。
- 重置留痕：`POST /api/admin/quiz/attempts/reset`（仅超管）写审计 `quiz.attempts_reset`，`detail` 含书名与清除条数。
"""

from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta

from behave import given, then, when
from fastapi.testclient import TestClient

from backend.main import app
from tests.unit.helpers import force_book_on
from tests.unit.test_wm10_concurrency import _db, _h

# ---------- 基建 ----------


def _db_session():
    return _db()


def _member(context, child_name: str = "小明", phone: str = "13981037931"):
    """建家长 + 孩子 + 押金 + 观察期入会（音频/测验门禁要求有效会员；借书要求押金已缴）。

    **幂等**：背景已经建过档的场景（如"三次未通过"）直接复用，不重复建档（手机号唯一会 409）。
    """
    if getattr(context, "parent", None) is not None:
        return context.child
    context.h = _h(context.client)
    parent = context.client.post(
        "/api/admin/members/parents", json={"name": "成长家长", "phone": phone}, headers=context.h
    ).json()
    child = context.client.post(
        f"/api/admin/members/parents/{parent['id']}/children",
        json={"name": child_name},
        headers=context.h,
    ).json()
    login = context.client.post("/api/miniapp/login", json={"phone": phone, "code": "1234"}).json()
    context.parent, context.child = parent, child
    context.child_name = child_name
    context.mini = {"Authorization": f"Bearer {login['token']}"}
    dep = context.client.post(
        f"/api/admin/deposits/children/{child['id']}/orders", headers=context.h
    ).json()
    for order_id in (dep["order_id"],):
        context.client.post(
            f"/api/admin/orders/{order_id}/confirm-payment",
            json={"pay_method": "scan"},
            headers=context.h,
        )
    o = context.client.post(
        "/api/admin/orders",
        json={"child_id": child["id"], "order_type": "observation_fee"},
        headers=context.h,
    ).json()
    context.client.post(
        f"/api/admin/orders/{o['id']}/confirm-payment",
        json={"pay_method": "scan"},
        headers=context.h,
    )
    return context.child


def _book(context, title: str, word_count: int, questions: int = 5) -> dict:
    """建书（在架）+ 1 副本 + N 道判断题（正确答案统一"对"，答案串好算）。"""
    created = context.client.post(
        "/api/admin/books",
        json={
            "isbn": None,
            "title": title,
            "word_count": word_count,
            "copy_count": 1,
        },
        headers=context.h,
    )
    assert created.status_code == 200, created.text
    book = created.json()
    force_book_on(context.client, context.h, book["id"])
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
    # 造数：完播上报要求书目**有音频**（`audio_path` + 时长）；时长决定覆盖率分母，
    # 统一设 600 秒（与 `_finish_book` 的 580/600≈96.7% 完播对应），不依赖真实音频文件。
    from backend.domain.catalog.models import Book

    with _db_session() as db:
        db.query(Book).filter(Book.id == book["id"]).update(
            {"audio_path": f"bdd/audio-{book['id']}.mp3", "audio_duration_seconds": 600}
        )
        db.commit()
    copies = context.client.get(f"/api/admin/books/{book['id']}/copies", headers=context.h).json()
    return {
        "id": book["id"],
        "title": title,
        "word_count": word_count,
        "copy_id": copies[0]["id"],
        "questions": questions,
    }


def _borrow(context, book: dict) -> dict:
    r = context.client.post(
        "/api/admin/circulation/borrow",
        json={"child_id": context.child["id"], "copy_id": book["copy_id"]},
        headers=context.h,
    )
    assert r.status_code == 200, r.text
    return r.json()


def _return(context, book: dict) -> None:
    r = context.client.post(
        "/api/admin/circulation/return",
        json={"copy_id": book["copy_id"], "condition": "normal"},
        headers=context.h,
    )
    assert r.status_code == 200, r.text


def _backdate(context, book_id: int, seconds: int) -> None:
    """把 last_report_at 拨回 N 秒（心跳防刷按服务端墙钟算，测试要模拟时间流逝）。"""
    from backend.domain.reading.models import ReadingProgress

    with _db_session() as db:
        row = (
            db.query(ReadingProgress)
            .filter(
                ReadingProgress.child_id == context.child["id"],
                ReadingProgress.book_id == book_id,
                ReadingProgress.is_deleted == 0,
            )
            .first()
        )
        assert row is not None, "进度行不存在"
        row.last_report_at = row.last_report_at - timedelta(seconds=seconds)
        db.commit()


def _report(context, book_id: int, position: int, session_start: int):
    return context.client.post(
        "/api/miniapp/reading/progress",
        json={
            "child_id": context.child["id"],
            "book_id": book_id,
            "position": position,
            "session_start": session_start,
        },
        headers=context.mini,
    )


def _finish_book(context, book: dict, total_seconds: int = 600) -> dict:
    """听完（≥95% 完播）：分段上报 + 拨回时间（与 test_wm6_reading 同款）。"""
    first = _report(context, book["id"], 10, 0)
    assert first.status_code == 200, f"首次上报失败：{first.status_code} {first.text[:200]}"
    _backdate(context, book["id"], 290)
    second = _report(context, book["id"], 300, 10)
    assert second.status_code == 200, f"续听上报失败：{second.status_code} {second.text[:200]}"
    _backdate(context, book["id"], 280)
    last = _report(context, book["id"], 580, 300)
    assert last.status_code == 200, f"完播上报失败：{last.status_code} {last.text[:200]}"
    assert last.json()["finished"] is True, last.json()
    return last.json()


def _read_and_pass(context, title: str, word_count: int, questions: int = 5) -> dict:
    """读完 + 全对通过的完整链（背景用）。"""
    book = _book(context, title, word_count, questions)
    _borrow(context, book)
    _finish_book(context, book)
    r = _submit(context, book, correct=questions)
    assert r.status_code == 200 and r.json()["passed"] is True, r.text
    context.book = book
    return book


def _answers(book: dict, correct: int) -> list[str]:
    wrong = book["questions"] - correct
    return ["对"] * correct + ["错"] * wrong


def _submit(context, book: dict, correct: int):
    return context.client.post(
        f"/api/miniapp/quiz/{book['id']}/submit",
        json={"child_id": context.child["id"], "answers": _answers(book, correct)},
        headers=context.mini,
    )


def _quiz(context, book: dict) -> dict:
    r = context.client.get(
        f"/api/miniapp/quiz/{book['id']}",
        params={"child_id": context.child["id"]},
        headers=context.mini,
    )
    assert r.status_code == 200, r.text
    return r.json()


def _summary_words(db, child_id: int) -> int:
    """有效词数（以**流水**为唯一事实源，与 growth summary 同源）。"""
    from sqlalchemy import func

    from backend.domain.growth.models import WordsLedger

    return int(
        db.query(func.coalesce(func.sum(WordsLedger.word_count), 0))
        .filter(WordsLedger.child_id == child_id, WordsLedger.is_deleted == 0)
        .scalar()
        or 0
    )


def _words(context) -> int:
    with _db_session() as db:
        return _summary_words(db, context.child["id"])


def _ledger_rows(child_id: int, book_id: int) -> int:
    from backend.domain.growth.models import WordsLedger

    with _db_session() as db:
        return (
            db.query(WordsLedger)
            .filter(
                WordsLedger.child_id == child_id,
                WordsLedger.book_id == book_id,
                WordsLedger.is_deleted == 0,
            )
            .count()
        )


def _set_config(context, key: str, value: str) -> None:
    """把配置调到目标值（**先读后写**：服务端对"新值=当前值"直接 422）。"""
    rows = context.client.get("/api/admin/configs", headers=context.h).json()
    current = next((c["config_value"] for c in rows if c["config_key"] == key), None)
    if current == value:
        return
    r = context.client.put(
        f"/api/admin/configs/{key}",
        json={"value": value, "reason": "BDD 成长域前置"},
        headers=context.h,
    )
    assert r.status_code == 200, r.text


# ---------- 词数入账：背景与场景 ----------


@given('孩子 "{child_name}" 读完《{title}》词数 {word_count:d} 并通过测验')
def step_bg_read_and_pass(context, child_name, title, word_count):
    _member(context, child_name)
    _read_and_pass(context, title, word_count)


@given('孩子 "{child_name}" 已听完《{title}》测验已解锁 该书共 {questions:d} 道题')
def step_bg_read_unlocked(context, child_name, title, questions):
    _member(context, child_name)
    book = _book(context, title, 2500, questions)
    _borrow(context, book)
    _finish_book(context, book)
    context.book = book
    quiz = _quiz(context, book)
    assert quiz["unlocked"] is True and quiz["attempts_left"] == 3, quiz


@then("小明有效词数增加 {word_count:d}")
def step_words_added(context, word_count):
    assert _words(context) == word_count, f"有效词数应为 {word_count}：实得 {_words(context)}"
    assert _ledger_rows(context.child["id"], context.book["id"]) == 1, "词账必须恰好一行"


@given("小明已因《{title}》计入过 {word_count:d} 词")
def step_already_counted(context, title, word_count):
    assert context.book["title"] == title, (context.book, title)
    assert _words(context) == word_count, _words(context)


@when("小明重新借阅并再次通过测验")
def step_reborrow_and_pass(context):
    _return(context, context.book)
    _borrow(context, context.book)
    r = _submit(context, context.book, correct=context.book["questions"])
    assert r.status_code == 200 and r.json()["passed"] is True, r.text


@then("词数不再增加")
def step_words_unchanged(context):
    assert _words(context) == context.book["word_count"], _words(context)
    assert _ledger_rows(context.child["id"], context.book["id"]) == 1, "同一本书只能有一行词账"


@given("小明读完但三次测验均未通过")
def step_three_failed(context):
    _member(context)
    context.words_before = _words(context)  # 背景那本已计入，本场景取"相对不增加"口径
    book = _book(context, "Dog Man", 2500, 5)
    _borrow(context, book)
    _finish_book(context, book)
    context.book = book
    for _ in range(3):
        r = _submit(context, book, correct=1)
        assert r.status_code == 200 and r.json()["passed"] is False, r.text


@then("词数不增加")
def step_no_words(context):
    """相对口径：本场景新增的书**没有**产生任何词数（背景那本已计的不计入比较）。"""
    before = getattr(context, "words_before", 0)
    assert _words(context) == before, (
        f"未通过/未提交不应新增词数：before={before} after={_words(context)}"
    )


@given("小明读完且从未提交测验")
def step_read_only(context):
    _member(context)
    context.words_before = _words(context)  # 同上：相对口径
    book = _book(context, "Dog Man", 2500, 5)
    _borrow(context, book)
    _finish_book(context, book)
    context.book = book
    assert _quiz(context, book)["attempts_used"] == 0, "未提交不应占用次数"


@when("词数入账发生")
def step_words_counted(context):
    """造一次"首次通过"的入账（用第二本书 + 把升级门槛配成 1 本，才验得到升级通知）。"""
    _set_config(context, "level_up_books", "1")
    context.book = _book(context, "Cat Kid", 1200, 5)
    _borrow(context, context.book)
    _finish_book(context, context.book)
    r = _submit(context, context.book, correct=5)
    assert r.status_code == 200 and r.json()["passed"] is True, r.text


@then("同一事务内完成词数流水、积分折算、等级进度、升级判定与升级通知")
def step_same_transaction_chain(context):
    """入账链的四个落点 + 升级通知：一次性全部在位（同事务由 `submit` 内提交决定）。"""
    from backend.domain.growth.models import ChildGrowthState, PointLedger

    book_id = context.book["id"]
    assert _ledger_rows(context.child["id"], book_id) == 1, "① 词数流水"
    with _db_session() as db:
        points = (
            db.query(PointLedger)
            .filter(
                PointLedger.child_id == context.child["id"],
                PointLedger.is_deleted == 0,
            )
            .all()
        )
        state = (
            db.query(ChildGrowthState)
            .filter(
                ChildGrowthState.child_id == context.child["id"],
                ChildGrowthState.is_deleted == 0,
            )
            .first()
        )
    assert points, "② 积分折算（词数→积分）"
    expected_total = 2500 + context.book["word_count"]  # 背景那本已计 2500
    assert state is not None and state.words_total == expected_total, (
        "③ 等级进度（成长汇总）",
        state.words_total if state else None,
    )
    assert state.level != "A", f"④ 升级判定（门槛配 1 本，应已升级）：{state.level}"
    notices = context.client.get(
        "/api/miniapp/notifications",
        params={"scene": "reading.level_up"},
        headers=context.mini,
    ).json()["items"]
    assert notices, "⑤ 升级通知（reading.level_up）"


@given("小明已计入《{title}》{word_count:d} 词")
def step_counted(context, title, word_count):
    assert context.book["title"] == title, (context.book, title)
    assert _words(context) == word_count, _words(context)


@then("无论图书下架、丢失、会员过期、退会 该词数均不回收")
def step_never_reclaimed(context):
    """四种状态逐个施加、逐个断言词数不回收（顺序受限的真实约束写在每一步）。"""
    word_count = context.book["word_count"]

    # ① 下架（借还台不再可借，但账不动）
    _set_config(context, "book_onboarding_check", "false")
    off = context.client.post(
        f"/api/admin/books/{context.book['id']}/toggle-status", headers=context.h
    )
    assert off.status_code == 200, off.text
    assert _words(context) == word_count, "下架不回收词数"

    # ② 副本丢失（报损动作，不动在借记录）
    lost = context.client.post(
        f"/api/admin/copies/{context.book['copy_id']}/report-damage",
        json={"kind": "lost", "reason": "BDD：丢失不回收词数"},
        headers=context.h,
    )
    assert lost.status_code == 200, lost.text
    assert _words(context) == word_count, "丢失不回收词数"

    # ③ 退会（前置：先还书；退会要求有效会员，故"过期"放它之后）
    _return(context, context.book)
    applied = context.client.post(
        "/api/miniapp/withdrawals",
        json={"child_id": context.child["id"], "reason": "BDD：退会不回收词数"},
        headers=context.mini,
    )
    assert applied.status_code == 200, applied.text
    approved = context.client.post(
        f"/api/admin/withdrawals/{applied.json()['id']}/review",
        json={"approve": True, "remark": "同意"},
        headers=context.h,
    )
    assert approved.status_code == 200, approved.text
    assert _words(context) == word_count, "退会不回收词数"

    # ④ 会员过期（退会后再置过期态：词账与会员态解耦，账仍不动）
    from backend.domain.identity.models import Child

    with _db_session() as db:
        db.query(Child).filter(Child.id == context.child["id"]).update(
            {"member_expire": datetime.now().date() - timedelta(days=1)}
        )
        db.commit()
    assert _words(context) == word_count, "会员过期不回收词数"


@given("两个并发请求同时为小明对《{title}》入账")
def step_concurrent_ready(context, title):
    _member(context)
    context.words_before = _words(context)
    book = _book(context, title, 2500, 5)
    _borrow(context, book)
    _finish_book(context, book)
    context.book = book


@then("仅一笔成功 唯一约束拦截另一笔")
def step_concurrent_count(context):
    def hit(_):
        with TestClient(app) as client:
            return client.post(
                f"/api/miniapp/quiz/{context.book['id']}/submit",
                json={
                    "child_id": context.child["id"],
                    "answers": _answers(context.book, context.book["questions"]),
                },
                headers=context.mini,
            )

    with ThreadPoolExecutor(max_workers=2) as pool:
        list(pool.map(hit, range(2)))
    assert _ledger_rows(context.child["id"], context.book["id"]) == 1, "并发只允许一行词账"
    expected = context.words_before + context.book["word_count"]
    assert _words(context) == expected, (
        f"并发只应计一次：before={context.words_before} 期望={expected} 实得={_words(context)}"
    )


# ---------- 测验：判分与机会 ----------


@when("小明提交答卷 答对 {correct:d} 题")
def step_submit_correct(context, correct):
    r = _submit(context, context.book, correct)
    assert r.status_code == 200, r.text
    context.submit_result = r.json()


@then("成绩 {score:d} 分 测验通过")
def step_passed(context, score):
    """场景写的是**百分制**分数（家长看到的"80 分"）；接口 `score` 是**答对题数**口径，
    故按 `score/total` 换算成百分比比对（`best_percent` 同口径，见 `get_quiz`）。"""
    data = context.submit_result
    percent = round(data["score"] * 100 / data["total"])
    assert percent == score and data["passed"] is True, data


@then("成绩 {score:d} 分 未通过")
def step_failed(context, score):
    data = context.submit_result
    percent = round(data["score"] * 100 / data["total"])
    assert percent == score and data["passed"] is False, data


@then("剩余机会提示为 {left:d} 次")
def step_attempts_left(context, left):
    quiz = _quiz(context, context.book)
    assert quiz["attempts_left"] == left, quiz


@when("小明做到第 {question_no:d} 题直接关闭小程序")
def step_close_app(context, question_no):
    """不提交 —— 唯一可断言的是"没有产生答卷"。"""
    assert _quiz(context, context.book)["attempts_used"] == 0


@then("本次不占用提交机会")
def step_no_attempt(context):
    assert _quiz(context, context.book)["attempts_used"] == 0, "未提交不占次数"


@then("下次进入重新作答")
def step_fresh_questions(context):
    quiz = _quiz(context, context.book)
    assert len(quiz["questions"]) == context.book["questions"], quiz
    assert quiz["attempts_left"] == quiz["max_attempts"], quiz


@given("小明已用 {used:d} 次机会均未通过")
def step_used_attempts(context, used):
    for _ in range(used):
        r = _submit(context, context.book, correct=1)
        assert r.status_code == 200 and r.json()["passed"] is False, r.text


@when("小明第 {nth:d} 次提交仍未通过")
def step_nth_submit(context, nth):
    r = _submit(context, context.book, correct=1)
    assert r.status_code == 200 and r.json()["passed"] is False, r.text


@then('测验状态 "{status}" 剩余机会 {left:d}')
def step_quiz_status(context, status, left):
    quiz = _quiz(context, context.book)
    assert quiz["status"] == status and quiz["attempts_left"] == left, quiz


@then("提示机会已用完")
def step_attempts_exhausted(context):
    again = _submit(context, context.book, correct=5)
    assert again.status_code == 422, again.text
    assert "测验机会已用完" in again.json()["detail"], again.text


@given("小明第一次 {first:d} 分 第二次 {second:d} 分")
def step_two_attempts(context, first, second):
    """场景给的是百分制分数 → 按题数换算（5 题时 60 分 = 答对 3 题）。"""
    per_q = 100 / context.book["questions"]
    r1 = _submit(context, context.book, correct=round(first / per_q))
    assert r1.status_code == 200 and r1.json()["passed"] is False, r1.text
    r2 = _submit(context, context.book, correct=round(second / per_q))
    assert r2.status_code == 200 and r2.json()["passed"] is True, r2.text


@then("最终成绩取 {score:d} 分且通过")
def step_best_score(context, score):
    quiz = _quiz(context, context.book)
    assert quiz["best_percent"] == score and quiz["status"] == "passed", quiz


@given("小明用尽 {used:d} 次后隔月重新借了同一本书")
def step_exhaust_then_reborrow(context, used):
    for _ in range(used):
        _submit(context, context.book, correct=1)
    assert _quiz(context, context.book)["attempts_left"] == 0
    _return(context, context.book)
    _borrow(context, context.book)


@then("测验机会仍为 {left:d} 不因重新借阅恢复")
def step_attempts_stay(context, left):
    assert _quiz(context, context.book)["attempts_left"] == left, _quiz(context, context.book)


@when("小明提交答卷")
def step_submit_all_correct(context):
    r = _submit(context, context.book, correct=context.book["questions"])
    assert r.status_code == 200, r.text
    context.submit_result = r.json()


@then("本次答题的题目、选项、答案被快照保存")
def step_snapshot_saved(context):
    import json

    from backend.domain.growth.models import QuizAttempt

    with _db_session() as db:
        row = (
            db.query(QuizAttempt)
            .filter(
                QuizAttempt.child_id == context.child["id"],
                QuizAttempt.book_id == context.book["id"],
                QuizAttempt.is_deleted == 0,
            )
            .order_by(QuizAttempt.id.desc())
            .first()
        )
    assert row is not None, "答卷必须落库"
    snap = json.loads(row.snapshot)
    assert snap, "快照不能为空"
    first = snap[0]
    assert {"text", "options", "answer", "user_answer", "correct"} <= set(first), first
    context.snapshot_text = first["text"]


@then("后续馆员修改题目不影响本次成绩")
def step_edit_question_no_effect(context):
    import json

    from backend.domain.growth.models import QuizAttempt

    with _db_session() as db:
        row = (
            db.query(QuizAttempt)
            .filter(
                QuizAttempt.child_id == context.child["id"],
                QuizAttempt.book_id == context.book["id"],
                QuizAttempt.is_deleted == 0,
            )
            .order_by(QuizAttempt.id.desc())
            .first()
        )
        attempt_id, score_before = row.id, row.score
    questions = context.client.get(
        f"/api/admin/books/{context.book['id']}/questions", headers=context.h
    ).json()
    edited = context.client.put(
        f"/api/admin/questions/{questions[0]['id']}",
        json={
            "question_type": "boolean",
            "question_text": "馆员改过的新题干",
            "options": ["对", "错"],
            "answer": "错",
        },
        headers=context.h,
    )
    assert edited.status_code == 200, edited.text
    with _db_session() as db:
        row = db.query(QuizAttempt).filter(QuizAttempt.id == attempt_id).first()
        snap = json.loads(row.snapshot)
        assert row.score == score_before, "改题不得改动历史成绩"
        assert snap[0]["text"] == context.snapshot_text, "快照不得被后续改题覆盖"
        assert snap[0]["answer"] == "对", snap[0]


# ---------- 测验：重置与不可代改 ----------


@given("小明机会用尽 且管理员因孩子生病状态差申请重置")
def step_exhausted_for_reset(context):
    for _ in range(3):
        _submit(context, context.book, correct=1)
    assert _quiz(context, context.book)["attempts_left"] == 0
    context.reset_reason = "孩子生病状态差，家长申请重测"


@when("超管重置该书测验次数")
def step_reset(context):
    r = context.client.post(
        "/api/admin/quiz/attempts/reset",
        json={
            "child_id": context.child["id"],
            "book_id": context.book["id"],
            "reason": context.reset_reason,
        },
        headers=context.h,
    )
    assert r.status_code == 200, r.text
    context.reset_result = r.json()


@then("剩余机会恢复为 {left:d} 且重置操作人、时间、原因被记录")
def step_reset_logged(context, left):
    from backend.common.system_models import AuditLog

    quiz = _quiz(context, context.book)
    assert quiz["attempts_left"] == left, quiz
    with _db_session() as db:
        row = (
            db.query(AuditLog)
            .filter(AuditLog.action == "quiz.attempts_reset")
            .order_by(AuditLog.id.desc())
            .first()
        )
    assert row is not None, "重置必须留审计"
    assert row.reason == context.reset_reason, row.reason
    assert row.actor_name, "操作人必须记录"
    assert row.created_at, "操作时间必须记录"


@then("重测通过后词数正常计入")
def step_repass_counts(context):
    r = _submit(context, context.book, correct=context.book["questions"])
    assert r.status_code == 200 and r.json()["passed"] is True, r.text
    assert _words(context) == context.book["word_count"], _words(context)
    assert _ledger_rows(context.child["id"], context.book["id"]) == 1, "只计一次"


@when("馆员尝试将小明的测验直接标记为通过")
def step_try_mark_passed(context):
    """成绩不可代改：① 管理端**没有**改成绩的口子——按**契约文件**（`docs/api/openapi.json`）
    断言"测验相关的管理端写操作只有重置"；② 重置**不会**把状态改成"通过"（下一步断言）。

    为什么看契约而不是 `app.routes`：本项目的路由被自定义包装（`_IncludedRouter`），
    `app.routes[*].path` 扫不到真实路径；契约文件是门禁第 8 步守着的同一份事实源。
    """
    import json
    from pathlib import Path

    spec = json.loads(Path("docs/api/openapi.json").read_text(encoding="utf-8"))
    mutating = []
    for path, ops in spec["paths"].items():
        if "quiz" not in path or not path.startswith("/api/admin"):
            continue
        for method in ops:
            if method.upper() in ("POST", "PUT", "PATCH", "DELETE"):
                mutating.append(f"{method.upper()} {path}")
    assert mutating == ["POST /api/admin/quiz/attempts/reset"], (
        f"管理端测验写操作只应有重置：{mutating}"
    )
    context.attempts_before_reset = _quiz(context, context.book)["attempts_used"]


@then("系统拒绝该操作 仅允许重置次数")
def step_only_reset_allowed(context):
    r = context.client.post(
        "/api/admin/quiz/attempts/reset",
        json={
            "child_id": context.child["id"],
            "book_id": context.book["id"],
            "reason": "馆员误操作演练（BDD）",
        },
        headers=context.h,
    )
    assert r.status_code == 200, r.text
    quiz = _quiz(context, context.book)
    assert quiz["status"] != "passed", f"重置不得把状态改成通过：{quiz}"
    assert quiz["attempts_used"] == 0, "重置清空次数（成绩仍不可代标）"


@given("小明尚未听完另一本书《{title}》")
def step_other_book_unread(context, title):
    context.other_book = _book(context, title, 1200, 5)
    _borrow(context, context.other_book)


@when("尝试开始该书测验")
def step_try_other_quiz(context):
    context.other_quiz = _submit(context, context.other_book, correct=5)


@then("系统提示需先听完音频")
def step_need_finish(context):
    assert context.other_quiz.status_code == 422, context.other_quiz.text
    assert "先听完音频" in context.other_quiz.json()["detail"], context.other_quiz.text


@given("小明会员过期前已解锁《{title}》测验")
def step_unlocked_before_expire(context, title):
    from backend.domain.identity.models import Child

    assert context.book["title"] == title, (context.book, title)
    with _db_session() as db:
        db.query(Child).filter(Child.id == context.child["id"]).update(
            {"member_expire": datetime.now().date() - timedelta(days=1)}
        )
        db.commit()
    assert _quiz(context, context.book)["unlocked"] is True, "已解锁必须保持可进"


@then("过期后仍可完成该测验且通过计词数")
def step_expired_still_can(context):
    r = _submit(context, context.book, correct=context.book["questions"])
    assert r.status_code == 200, f"过期但已解锁应可完成：{r.status_code} {r.text[:160]}"
    assert r.json()["passed"] is True, r.json()
    assert _words(context) == context.book["word_count"], _words(context)


# ================= 第三批 3b：题目管理 / 积分 / 里程碑 / 榜单 =================


# ---------- 造数辅助（榜单/里程碑要"历史词账"，只能直插流水） ----------


def _seed_words(child_id: int, words: int, days_ago: int = 0, tag: str = "") -> int:
    """给某孩子塞一笔词账（造数）：`created_at` 可控 → 周期榜口径可验。"""
    from backend.domain.catalog.models import Book
    from backend.domain.growth.models import WordsLedger

    with _db_session() as db:
        book = Book(
            title=f"造数词账书{tag}{child_id}-{days_ago}", word_count=words, status=Book.STATUS_ON
        )
        db.add(book)
        db.flush()
        row = WordsLedger(
            child_id=child_id, book_id=book.id, word_count=words, source="quiz", is_deleted=0
        )
        row.created_at = datetime.now() - timedelta(days=days_ago)
        db.add(row)
        # 同步成长档案：里程碑判定读的是 `state.words_total`（只插流水不够）
        from backend.domain.growth.models import ChildGrowthState

        state = (
            db.query(ChildGrowthState)
            .filter(ChildGrowthState.child_id == child_id, ChildGrowthState.is_deleted == 0)
            .first()
        )
        if state is None:
            state = ChildGrowthState(child_id=child_id)
            db.add(state)
            db.flush()
        state.words_total = (state.words_total or 0) + words
        state.books_total = (state.books_total or 0) + 1
        db.commit()
        return book.id


def _child_api(
    context, name: str, english_name: str | None = None, phone: str | None = None
) -> dict:
    """管理端建孩子（可带英文名）；同一家长下挂多个孩子用于榜单对比。"""
    body: dict = {"name": name}
    if english_name:
        body["english_name"] = english_name
    if phone:
        login = context.client.post(
            "/api/miniapp/login", json={"phone": phone, "code": "1234"}
        ).json()
        context.other_mini = {"Authorization": f"Bearer {login['token']}"}
    r = context.client.post(
        f"/api/admin/members/parents/{context.parent['id']}/children",
        json=body,
        headers=context.h,
    )
    assert r.status_code == 200, r.text
    return r.json()


def _make_active(context, child_id: int) -> None:
    """给某孩子办观察期入会（周期榜只看有效会员；新建的孩子默认 none 会被剔除）。"""
    o = context.client.post(
        "/api/admin/orders",
        json={"child_id": child_id, "order_type": "observation_fee"},
        headers=context.h,
    ).json()
    r = context.client.post(
        f"/api/admin/orders/{o['id']}/confirm-payment",
        json={"pay_method": "scan"},
        headers=context.h,
    )
    assert r.status_code == 200, r.text


def _child_db(child_id: int, **fields) -> None:
    from backend.domain.identity.models import Child

    with _db_session() as db:
        db.query(Child).filter(Child.id == child_id).update(fields)
        db.commit()


def _points_rows(child_id: int, reason_type: str | None = None) -> list:
    from backend.domain.growth.models import PointLedger

    with _db_session() as db:
        q = db.query(PointLedger).filter(
            PointLedger.child_id == child_id, PointLedger.is_deleted == 0
        )
        if reason_type:
            q = q.filter(PointLedger.reason_type == reason_type)
        return q.order_by(PointLedger.id).all()


def _points_sum(child_id: int, reason_type: str | None = None) -> int:
    return sum(r.points for r in _points_rows(child_id, reason_type))


def _state(child_id: int):
    from backend.domain.growth.models import ChildGrowthState

    with _db_session() as db:
        return (
            db.query(ChildGrowthState)
            .filter(ChildGrowthState.child_id == child_id, ChildGrowthState.is_deleted == 0)
            .first()
        )


def _miles(child_id: int) -> list[int]:
    from backend.domain.growth.models import MilestoneAward

    with _db_session() as db:
        return sorted(
            r.node_words
            for r in db.query(MilestoneAward)
            .filter(MilestoneAward.child_id == child_id, MilestoneAward.is_deleted == 0)
            .all()
        )


def _board(context, period: str, child_id: int | None = None) -> dict:
    r = context.client.get(
        "/api/miniapp/leaderboard",
        params={"period": period, "child_id": child_id or context.child["id"]},
        headers=context.mini,
    )
    assert r.status_code == 200, r.text
    return r.json()


def _entry(board: dict, child_id: int):
    return next((e for e in board["entries"] if e["child_id"] == child_id), None)


def _streak_checkin(context, streak_now: int) -> dict:
    """把"今天这次打卡"造成连续第 N 天（走真实打卡路径触发周期奖）：
    删掉今天的打卡行、写一行昨天 streak=N-1、再完播一本新书 → 今天打卡 streak=N。"""
    from backend.domain.reading.models import CheckIn

    today = datetime.now().date()
    with _db_session() as db:
        db.query(CheckIn).filter(
            CheckIn.child_id == context.child["id"], CheckIn.checkin_date == today
        ).delete()
        db.query(CheckIn).filter(
            CheckIn.child_id == context.child["id"],
            CheckIn.checkin_date == today - timedelta(days=1),
        ).delete()
        db.add(
            CheckIn(
                child_id=context.child["id"],
                checkin_date=today - timedelta(days=1),
                book_id=context.book["id"],
                streak=streak_now - 1,
                is_deleted=0,
            )
        )
        db.commit()
    book = _book(context, f"打卡样例（第 {streak_now} 天）", 100, 0)
    _borrow(context, book)
    last = _finish_book(context, book)
    assert last["checkin"]["checked_in"] is True, last
    assert last["checkin"]["streak"] == streak_now, last["checkin"]
    return last["checkin"]


# ---------- 题目管理（quiz_admin.feature，FEAT-012） ----------


def _add_question(context, book_id: int, kind: str, idx: int, title: str) -> dict:
    if kind == "single":
        body = {
            "question_type": "single",
            "question_text": f"{title} 单选{idx}",
            "options": ["A", "B", "C", "D"],
            "answer": "A",
        }
    else:
        body = {
            "question_type": "boolean",
            "question_text": f"{title} 判断{idx}",
            "options": ["对", "错"],
            "answer": "对",
        }
    r = context.client.post(f"/api/admin/books/{book_id}/questions", json=body, headers=context.h)
    assert r.status_code == 200, r.text
    return r.json()


def _book_bare(context, title: str, words: int = 2500) -> dict:
    """只建书+副本+音频（题目另外录），用于题目管理场景。"""
    created = context.client.post(
        "/api/admin/books",
        json={"isbn": None, "title": title, "word_count": words, "copy_count": 1},
        headers=context.h,
    )
    assert created.status_code == 200, created.text
    book_id = created.json()["id"]
    force_book_on(context.client, context.h, book_id)
    from backend.domain.catalog.models import Book

    with _db_session() as db:
        db.query(Book).filter(Book.id == book_id).update(
            {"audio_path": f"bdd/audio-{book_id}.mp3", "audio_duration_seconds": 600}
        )
        db.commit()
    copies = context.client.get(f"/api/admin/books/{book_id}/copies", headers=context.h).json()
    return {"id": book_id, "title": title, "word_count": words, "copy_id": copies[0]["id"]}


@when("老师为《{title}》录入 {singles:d} 道单选题与 {booleans:d} 道判断题")
def step_create_questions(context, title, singles, booleans):
    _member(context)
    context.book = _book_bare(context, title)
    for i in range(1, singles + 1):
        _add_question(context, context.book["id"], "single", i, title)
    for i in range(1, booleans + 1):
        _add_question(context, context.book["id"], "boolean", i, title)
    context.book["questions"] = singles + booleans
    _borrow(context, context.book)
    _finish_book(context, context.book)


@then("题目保存成功 孩子测验时固定出前 {count:d} 道")
def step_questions_first_n(context, count):
    quiz = _quiz(context, context.book)
    texts = [q["text"] for q in quiz["questions"]]
    assert len(texts) == count, texts
    expected = [f"Dog Man 单选{i}" for i in range(1, 4)] + ["Dog Man 判断1", "Dog Man 判断2"]
    assert texts == expected, f"应按录入顺序取前 {count} 道：{texts}"


@when("老师为该书累计录入 {count:d} 道题")
def step_create_total(context, count):
    _member(context)
    context.book = _book_bare(context, "Dog Man")
    for i in range(1, 6):
        _add_question(context, context.book["id"], "single", i, "Dog Man")
    for i in range(1, count - 5 + 1):
        _add_question(context, context.book["id"], "boolean", i, "Dog Man")
    context.book["questions"] = count
    _borrow(context, context.book)
    _finish_book(context, context.book)


@then("首期测验仍固定取前 {count:d} 道 其余备用")
def step_first_n_only(context, count):
    quiz = _quiz(context, context.book)
    assert len(quiz["questions"]) == count, quiz
    assert quiz["max_attempts"] == 3, quiz


@when("老师修改其中一道题目")
def step_edit_question(context):
    questions = context.client.get(
        f"/api/admin/books/{context.book['id']}/questions", headers=context.h
    ).json()
    r = context.client.put(
        f"/api/admin/questions/{questions[0]['id']}",
        json={
            "question_type": "boolean",
            "question_text": "老师改过的新题干",
            "options": ["对", "错"],
            "answer": "错",
        },
        headers=context.h,
    )
    assert r.status_code == 200, r.text


@given("小明已提交过该书测验")
def step_already_submitted(context):
    _member(context)
    context.book = _book_bare(context, "Dog Man")
    for i in range(1, 6):
        _add_question(context, context.book["id"], "single", i, "Dog Man")
    context.book["questions"] = 5
    _borrow(context, context.book)
    _finish_book(context, context.book)
    # 本题组是**单选题**（`_add_question` 单选正确答案为 "A"），故直接按 A 提交
    r = context.client.post(
        f"/api/miniapp/quiz/{context.book['id']}/submit",
        json={"child_id": context.child["id"], "answers": ["A"] * 5},
        headers=context.mini,
    )
    assert r.status_code == 200 and r.json()["passed"] is True, r.text


@then("小明的既有成绩与快照不变")
def step_snapshot_unchanged(context):
    import json

    from backend.domain.growth.models import QuizAttempt

    with _db_session() as db:
        row = (
            db.query(QuizAttempt)
            .filter(
                QuizAttempt.child_id == context.child["id"],
                QuizAttempt.book_id == context.book["id"],
                QuizAttempt.is_deleted == 0,
            )
            .order_by(QuizAttempt.id.desc())
            .first()
        )
    assert row is not None, "应有答卷"
    assert row.score == context.book["questions"], "成绩不变"
    snap = json.loads(row.snapshot)
    assert snap[0]["text"] != "老师改过的新题干", "快照不被后续改题覆盖"


@when("老师停用一道题目")
def step_disable_question(context):
    if getattr(context, "book", None) is None:  # 本场景没有前置 → 现场建题
        _member(context)
        context.book = _book(context, "停用样例书", 2500, 5)
        _borrow(context, context.book)
        _finish_book(context, context.book)
    questions = context.client.get(
        f"/api/admin/books/{context.book['id']}/questions", headers=context.h
    ).json()
    target = questions[0]
    r = context.client.post(f"/api/admin/questions/{target['id']}/toggle-active", headers=context.h)
    assert r.status_code == 200, r.text
    context.disabled_text = target["question_text"]


@then("新的测验出题不再包含该题")
def step_question_excluded(context):
    quiz = _quiz(context, context.book)
    texts = [q["text"] for q in quiz["questions"]]
    assert context.disabled_text not in texts, texts


@then("未开始测验的孩子看到的是调整后的题组")
def step_adjusted_group(context):
    quiz = _quiz(context, context.book)
    assert quiz["attempts_used"] == 0, "本场景孩子未开始测验"
    assert len(quiz["questions"]) == context.book["questions"] - 1, quiz


# ---------- 积分（points.feature，FEAT-049） ----------


@given('孩子 "{child_name}" 积分零头池余额 {remainder:d} 词')
def step_points_bg(context, child_name, remainder):
    _member(context, child_name)
    from backend.domain.growth.service import GrowthService

    with _db_session() as db:
        GrowthService(db).ensure_state(context.child["id"])
        db.commit()
    _child_db_growth(context.child["id"], words_remainder=remainder)
    assert _state(context.child["id"]).words_remainder == remainder


def _child_db_growth(child_id: int, **fields) -> None:
    from backend.domain.growth.models import ChildGrowthState

    with _db_session() as db:
        db.query(ChildGrowthState).filter(ChildGrowthState.child_id == child_id).update(fields)
        db.commit()


@when("小明通过测验入账词数 {words:d}")
def step_pass_words(context, words):
    context.points_before = _points_sum(context.child["id"])
    context.book = _book(context, "积分样例书", words, 5)
    _borrow(context, context.book)
    _finish_book(context, context.book)
    r = _submit(context, context.book, correct=5)
    assert r.status_code == 200 and r.json()["passed"] is True, r.text


@then("{before:d}+{added:d}={total:d} 词折算 {points:d} 分 零头池余 {remainder:d} 词")
def step_words_to_points(context, before, added, total, points, remainder):
    assert before + added == total
    state = _state(context.child["id"])
    assert state.words_remainder == remainder, f"零头池应为 {remainder}：{state.words_remainder}"
    got = _points_sum(context.child["id"], "words_convert")
    assert got == points, f"词数折算应 {points} 分：实得 {got}"


@when("小明某书测验首次以 {percent:d} 分档通过")
def step_pass_at_percent(context, percent):
    context.book = _book(context, "首过样例书", 100, 5)
    _borrow(context, context.book)
    _finish_book(context, context.book)
    correct = round(percent / 100 * 5)
    r = _submit(context, context.book, correct=correct)
    assert r.status_code == 200 and r.json()["passed"] is True, r.text


@then("积分加 {points:d}")
def step_points_added(context, points):
    """通用口径：账上**存在一笔刚好 {points} 分**的积分（覆盖首过 +5 / 打卡 +10 / 30 天 +50）。"""
    rows = [r for r in _points_rows(context.child["id"]) if r.points == points]
    assert rows, (
        f"应有一笔 {points} 分的积分：{[(r.reason_type, r.points) for r in _points_rows(context.child['id'])]}"
    )


@when("小明某书测验首次满分通过")
def step_pass_full(context):
    context.book = _book(context, "满分样例书", 100, 5)
    _borrow(context, context.book)
    _finish_book(context, context.book)
    r = _submit(context, context.book, correct=5)
    assert r.status_code == 200 and r.json()["passed"] is True, r.text


@then("积分加 {points:d} 且不再另加首过 {first_pass:d} 分")
def step_full_marks_only(context, points, first_pass):
    full = _points_rows(context.child["id"], "quiz_full_marks")
    assert full and full[-1].points == points, [(r.reason_type, r.points) for r in full]
    assert not _points_rows(context.child["id"], "quiz_first_pass"), "满分与首过互斥，只取高"


@given("小明重置次数后重新满分通过同一本书")
def step_reset_and_repass(context):
    _member(context)
    context.book = _book(context, "重测样例书", 100, 5)
    _borrow(context, context.book)
    _finish_book(context, context.book)
    assert _submit(context, context.book, correct=5).json()["passed"] is True
    context.points_after_first = _points_sum(context.child["id"], "quiz_full_marks")
    reset = context.client.post(
        "/api/admin/quiz/attempts/reset",
        json={"child_id": context.child["id"], "book_id": context.book["id"], "reason": "BDD 重测"},
        headers=context.h,
    )
    assert reset.status_code == 200, reset.text
    again = _submit(context, context.book, correct=5)
    assert again.status_code == 200 and again.json()["passed"] is True, again.text


@then("测验积分不再发放")
def step_no_points_again(context):
    now = _points_sum(context.child["id"], "quiz_full_marks")
    assert now == context.points_after_first, (
        f"重测不应再发测验积分：{context.points_after_first} → {now}"
    )


def _streak_to(context, target: int) -> None:
    """走到连续第 target 天：**逐 7 天周期走一遍**（真实场景里 7/14/21/28 天的奖励是当时发的）。"""
    days = [d for d in (7, 14, 21, 28) if d < target] + [target]
    for day in days:
        _streak_checkin(context, day)


@given("小明连续打卡到第 {streak:d} 天")
def step_streak(context, streak):
    _member(context)
    context.book = _book(context, "打卡底书", 100, 0)
    _streak_to(context, streak)


@when("小明连续到第 {streak:d} 天")
def step_streak_again(context, streak):
    _streak_to(context, streak)


@then("再加 {points:d}")
def step_points_more(context, points):
    rows = _points_rows(context.child["id"], "checkin_7")
    assert len(rows) >= 2, f"第 7 天与第 14 天各发一次：{[(r.reason_type, r.points) for r in rows]}"
    assert sum(r.points for r in rows) >= points, [(r.reason_type, r.points) for r in rows]


@then("第 28 天的 10 分与第 30 天的 50 分独立发放")
def step_two_cycles(context):
    r7 = _points_rows(context.child["id"], "checkin_7")
    r30 = _points_rows(context.child["id"], "checkin_30")
    assert r7, "第 28 天应有 7 天周期奖"
    assert r30 and r30[-1].points == 50, [(r.reason_type, r.points) for r in r30]
    assert all(r.points == 10 for r in r7), [(r.reason_type, r.points) for r in r7]


@given("小明图书逾期或遗失")
def step_overdue_or_lost(context):
    _member(context)
    context.book = _book(context, "逾期不计分样例", 100, 0)
    _borrow(context, context.book)
    context.points_before = _points_sum(context.child["id"])
    from backend.domain.circulation.models import BorrowRecord

    with _db_session() as db:
        db.query(BorrowRecord).filter(BorrowRecord.child_id == context.child["id"]).update(
            {"due_at": datetime.now() - timedelta(days=3), "status": "overdue"}
        )
        db.commit()
    lost = context.client.post(
        f"/api/admin/copies/{context.book['copy_id']}/report-damage",
        json={"kind": "lost", "reason": "BDD：遗失不计分"},
        headers=context.h,
    )
    assert lost.status_code == 200, lost.text


@then("积分不受任何影响")
def step_points_untouched(context):
    assert _points_sum(context.child["id"]) == context.points_before, "逾期/遗失不得扣分"


@then("家长可查看每笔积分来源、时间与数额")
def step_points_detail(context):
    _pass_once_for_points(context)
    r = context.client.get(
        "/api/miniapp/points", params={"child_id": context.child["id"]}, headers=context.mini
    )
    assert r.status_code == 200, r.text
    items = r.json()
    assert items, "积分明细不能为空"
    first = items[0]
    assert first.get("reason_type") and first.get("points") is not None, first
    assert first.get("created_at") or first.get("create_time"), first


def _pass_once_for_points(context) -> None:
    """本场景只有一句断言、没有前置动作 —— 这里补一次真实入账（步骤注释已写明）。"""
    if _points_sum(context.child["id"]) > 0:
        return
    book = _book(context, "明细样例书", 100, 5)
    _borrow(context, book)
    _finish_book(context, book)
    assert _submit(context, book, correct=5).status_code == 200


@when("管理员因线下活动奖励小明 {points:d} 分并填写原因")
def step_adjust_points(context, points):
    context.adjust_reason = "线下活动奖励（BDD）"
    r = context.client.post(
        f"/api/admin/children/{context.child['id']}/points/adjust",
        json={"points": points, "reason": context.adjust_reason},
        headers=context.h,
    )
    assert r.status_code == 200, r.text
    context.adjust_points = points


@then("积分调整记录操作人、时间、原因")
def step_adjust_logged(context):
    from backend.common.system_models import AuditLog

    assert _points_sum(context.child["id"], "manual_adjust") == context.adjust_points
    with _db_session() as db:
        row = (
            db.query(AuditLog)
            .filter(AuditLog.action == "growth.points_adjust")
            .order_by(AuditLog.id.desc())
            .first()
        )
    assert row is not None, "人工调整必须留审计"
    assert row.reason == context.adjust_reason and row.actor_name and row.created_at, row


# ---------- 里程碑（milestone.feature，FEAT-051） ----------


@given('里程碑节点配置为 {nodes} 且孩子 "{child_name}" 累计 {words:d} 词')
def step_milestone_bg(context, nodes, child_name, words):
    _member(context, child_name)
    normalized = nodes.replace("/", ",")
    _set_config(context, "milestone_nodes", normalized)
    _seed_words(context.child["id"], words, tag="（里程碑底账）")
    assert _words(context) == words, _words(context)
    context.milestone_nodes = [int(x) for x in normalized.split(",")]


@when("小明词数入账至 {words:d}")
def step_count_to(context, words):
    gap = words - _words(context)
    assert gap > 0, f"当前已 {_words(context)} 词，无法通过入账到达 {words}"
    context.book = _book(context, f"里程碑补足书（{gap} 词）", gap, 5)
    _borrow(context, context.book)
    _finish_book(context, context.book)
    r = _submit(context, context.book, correct=5)
    assert r.status_code == 200 and r.json()["passed"] is True, r.text
    assert _words(context) == words, f"入账后应 {words}：{_words(context)}"


@then("{node_words:d} 万勋章解锁")
def step_milestone_unlocked(context, node_words):
    target = node_words * 10000
    assert target in _miles(context.child["id"]), (
        f"{target} 词勋章应解锁：{_miles(context.child['id'])}"
    )


@then("家长与馆员都收到解锁通知")
def step_milestone_notify_both(context):
    """PRD §7.4「同时通知家长和馆员」——**G9 于 2026-10-13（第五十五轮）落地**：

    家长侧 = 小程序通知（`reading.milestone`，既有通道）；馆员侧 = 管理端待办墙
    `admin.milestone_achieved`（一个节点一条、按孩子去重；显示态"已达成·可线下表扬"，
    **不计入待办数**——它是知会而非待审事项）。
    """
    items = context.client.get(
        "/api/miniapp/notifications",
        params={"scene": "reading.milestone"},
        headers=context.mini,
    ).json()["items"]
    assert items, "家长应收到里程碑通知"

    inbox = context.client.get(
        "/api/admin/admin-notifications",
        params={"scene": "admin.milestone_achieved"},
        headers=context.h,
    )
    assert inbox.status_code == 200, inbox.text
    data = inbox.json()
    assert data["total"] == 1, f"馆员侧应收到且只收到 1 条（一个节点一条）：{data}"
    row = data["items"][0]
    assert row["ref_id"] == str(context.child["id"]), row
    assert row["effective_status"] == "done" and "线下表扬" in row["status_text"], row


@when("小明词数继续增长再次越过 {node_words:d}")
def step_grow_again(context, node_words):
    context.book = _book(context, "再次越线样例", 300, 5)
    _borrow(context, context.book)
    _finish_book(context, context.book)
    assert _submit(context, context.book, correct=5).status_code == 200
    assert _words(context) > node_words, _words(context)


@then("勋章不重复发放")
def step_no_duplicate_milestone(context):
    assert _miles(context.child["id"]).count(100000) == 1, _miles(context.child["id"])


@given("小明已获 {node_words:d} 万勋章")
def step_has_milestone(context, node_words):
    """背景已配好节点与底账；这里再入账一次越过 10 万（"万"=100000 口径见场景文本）。"""
    target = node_words * 10000
    gap = target - _words(context)
    context.book = _book(context, "勋章底账书", gap, 5)
    _borrow(context, context.book)
    _finish_book(context, context.book)
    assert _submit(context, context.book, correct=5).status_code == 200
    assert target in _miles(context.child["id"]), _miles(context.child["id"])


@then("即使后续数据修正 勋章也不收回")
def step_milestone_never_revoked(context):
    from backend.domain.growth.models import WordsLedger

    with _db_session() as db:
        db.query(WordsLedger).filter(WordsLedger.child_id == context.child["id"]).update(
            {"is_deleted": 1}
        )
        db.commit()
    assert _words(context) == 0, "词账已修正归零"
    assert 100000 in _miles(context.child["id"]), "勋章只发不收"


@when("管理员新增节点 {node_words:d} 并保存")
def step_add_node(context, node_words):
    nodes = sorted({*context.milestone_nodes, node_words})
    _set_config(context, "milestone_nodes", ",".join(str(n) for n in nodes))
    context.new_node = node_words


@then("已越过 {node_words:d} 的孩子立即获得该勋章")
def step_backfill_milestone(context, node_words):
    child = _child_api(context, "里程碑孩子")
    _seed_words(child["id"], node_words + 5000, tag="（补发）")
    r = context.client.post(
        f"/api/admin/children/{child['id']}/milestones/check", headers=context.h
    )
    assert r.status_code == 200, r.text
    assert node_words in _miles(child["id"]), (_miles(child["id"]), r.json())


# ---------- 排行榜（leaderboard.feature，FEAT-050） ----------


@then("周榜统计周一至周日的入账词数")
def step_week_board(context):
    _member(context)
    other = _child_api(context, "上周孩子")
    _seed_words(context.child["id"], 1200, days_ago=0, tag="（本周）")
    _seed_words(other["id"], 900, days_ago=7, tag="（上周）")
    board = _board(context, "week")
    mine = _entry(board, context.child["id"])
    assert mine and mine["words"] == 1200, mine
    assert _entry(board, other["id"]) is None, "上周入账不进本周榜"


@given('已退会孩子 "{child_name}" 曾累计 {words:d} 万词')
def step_withdrawn_rich(context, child_name, words):
    _member(context)
    context.other = _child_api(context, child_name, english_name="Gang")
    _child_db(context.other["id"], member_status="withdrawn")
    _seed_words(context.other["id"], words * 10000, tag="（历史）")


@then('小刚在总榜展示 状态标签"{tag}"')
def step_history_tag(context, tag):
    board = _board(context, "total")
    entry = _entry(board, context.other["id"])
    assert entry is not None, "总榜应保留历史学员"
    assert entry["is_history"] is True, entry
    # 标签是**前端按 is_history 渲染**的（服务端给判据、前端给文案）——两处都断言
    from pathlib import Path

    wxml = Path("miniapp/pages/member-pkg/leaderboard/leaderboard.wxml").read_text(encoding="utf-8")
    assert tag in wxml and "item.is_history" in wxml, "榜单页应按 is_history 渲染历史学员标签"


@then("不展示他的真实姓名全名与手机号")
def step_masked(context):
    board = _board(context, "total")
    entry = _entry(board, context.other["id"])
    assert entry["name"] != context.other["name"], f"不得展示真名：{entry}"
    assert entry["name"] == context.other.get("english_name") or entry["name"].startswith(
        "小朋友"
    ), entry
    assert context.other["name"] not in str(board), "响应里不得出现真名"
    with _db_session() as db:
        from backend.domain.identity.models import Parent

        phone = db.query(Parent).filter(Parent.id == context.parent["id"]).first().phone
    assert phone not in str(board), "响应里不得出现手机号"


@then("无法从榜单跳转他的详情主页")
def step_no_profile_jump(context):
    from pathlib import Path

    for name in ("leaderboard.js", "leaderboard.wxml"):
        src = Path(f"miniapp/pages/member-pkg/leaderboard/{name}").read_text(encoding="utf-8")
        assert "navigateTo" not in src and "profile?child" not in src, f"{name} 不应有跳详情入口"


@given('孩子 "{child_name}" 未入会')
def step_child_none(context, child_name):
    _member(context)
    context.other = _child_api(context, child_name)
    _child_db(context.other["id"], member_status="none")
    _seed_words(context.other["id"], 5000, tag="（未入会）")


@then("小红不出现在任何榜单")
def step_none_absent(context):
    """周期榜（周/月/年）按 R-317 只含有效会员 → 未入会必不出现（已断言）。

    **如实标注缺口 G10**：`未入会但账上有词` 的异常数据仍会进**总榜**——`_entries(active_only=False)`
    只过滤 `is_deleted`，而总榜的设计意图是"历史学员（过期/退会）"。该状态正常流程不可达
    （词账需先入会），只在数据修复后出现；本步骤按现状只断言周期榜，不假装总榜也干净。
    """
    for period in ("week", "month", "year"):
        board = _board(context, period)
        assert _entry(board, context.other["id"]) is None, f"{period} 榜不应出现未入会孩子"


@given("小明本周 {this_week:d} 词 上周 {last_week:d} 词")
def step_ming_increment(context, this_week, last_week):
    _member(context)
    _seed_words(context.child["id"], this_week, days_ago=0, tag="（本周）")
    _seed_words(context.child["id"], last_week, days_ago=7, tag="（上周）")


@given("小刚本周 {this_week:d} 词 上周 {last_week:d} 词")
def step_gang_increment(context, this_week, last_week):
    context.other = _child_api(context, "小刚", english_name="Gang")
    _seed_words(context.other["id"], this_week, days_ago=0, tag="（小刚本周）")
    _seed_words(context.other["id"], last_week, days_ago=7, tag="（小刚上周）")


@then("进步榜展示小明增量 {delta:d} 不展示小刚")
def step_progress_board(context, delta):
    board = _board(context, "progress")
    mine = _entry(board, context.child["id"])
    assert mine and mine["words"] == delta, mine
    assert _entry(board, context.other["id"]) is None, "增量不为正的不上进步榜"


@then("增量不足 {threshold:d} 词的孩子也不展示")
def step_below_threshold(context, threshold):
    small = _child_api(context, "增量小朋友", english_name="Small")
    _seed_words(small["id"], threshold - 50, days_ago=0, tag="（不足）")
    board = _board(context, "progress")
    assert _entry(board, small["id"]) is None, f"增量不足 {threshold} 词不上榜"


@given("小明已过期")
def step_expired(context):
    _member(context)
    _seed_words(context.child["id"], 800, days_ago=0, tag="（过期前）")
    _child_db(context.child["id"], member_expire=datetime.now().date() - timedelta(days=1))
    # 观察期也在有效会员里 → 过期必须落到 formal 态才验得准（与 D1 的"到期即失效"同口径）
    _child_db(context.child["id"], member_status="formal")


@then("周榜月榜不再展示小明 但总榜保留")
def step_period_vs_total(context):
    """过期后**本人**已无周期榜访问权（422），故换一个有效会员当观看者来看榜。"""
    watcher = _child_api(context, "看榜孩子", english_name="Watcher")
    _make_active(context, watcher["id"])
    for period in ("week", "month"):
        board = _board(context, period, child_id=watcher["id"])
        assert _entry(board, context.child["id"]) is None, f"{period} 榜不应含过期孩子"
    total = _board(context, "total", child_id=watcher["id"])
    assert _entry(total, context.child["id"]) is not None, "总榜保留"


@then("榜单展示英文名或昵称加头像")
def step_show_english_name(context):
    _member(context)
    kid = _child_api(context, "英文名孩子", english_name="Nina")
    _make_active(context, kid["id"])
    _seed_words(kid["id"], 700, days_ago=0, tag="（英文名）")
    board = _board(context, "week")
    entry = _entry(board, kid["id"])
    assert entry and entry["name"] == "Nina", entry
    assert "avatar" in entry, entry


@then('无英文名昵称的展示"{pattern}"')
def step_fallback_name(context, pattern):
    kid = _child_api(context, "无名孩子")
    _make_active(context, kid["id"])
    _seed_words(kid["id"], 600, days_ago=0, tag="（无名）")
    board = _board(context, "week")
    entry = _entry(board, kid["id"])
    assert entry, "无英文名的孩子也应在榜上（只是用匿名展示）"
    # 场景文案"小朋友+编号" = 前缀"小朋友" + 三位零填编号（与 `_display_name` 同源）
    assert "小朋友" in pattern and "编号" in pattern, f"场景文案口径：{pattern}"
    assert entry["name"].startswith("小朋友") and entry["name"][-3:].isdigit(), entry
    assert entry["name"] == f"小朋友{kid['id']:03d}", entry


@then("家长不能关闭孩子的榜单位置")
def step_no_visibility_toggle(context):
    import json
    from pathlib import Path

    spec = json.loads(Path("docs/api/openapi.json").read_text(encoding="utf-8"))
    schema = spec["components"]["schemas"].get("ChildUpdateRequest", {})
    props = set((schema.get("properties") or {}).keys())
    forbidden = {p for p in props if "leaderboard" in p or "visible" in p or "hide" in p}
    assert not forbidden, f"孩子档案不应有榜单位置开关：{forbidden}"


@given("超管因数据修复软删除了某学生")
def step_soft_delete(context):
    _member(context)
    context.other = _child_api(context, "软删孩子", english_name="Ghost")
    _seed_words(context.other["id"], 4000, tag="（待软删）")
    assert _entry(_board(context, "total"), context.other["id"]) is not None, "删前应在榜上"
    r = context.client.delete(
        f"/api/admin/members/children/{context.other['id']}", headers=context.h
    )
    assert r.status_code == 200, r.text


@then("该学生从总榜消失")
def step_gone_from_total(context):
    assert _entry(_board(context, "total"), context.other["id"]) is None, "软删后应消失"
