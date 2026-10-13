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
