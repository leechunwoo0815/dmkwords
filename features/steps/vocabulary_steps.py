# features/steps/vocabulary_steps.py — FEAT-054/055 生词本与查词 步骤定义
"""2026-10-16（第六十四批·BDD 解封第五批）· **生词本 10 场景**全部转真实执行。

判据与实现同源（动手前逐条亲验，红线 108/136）：
- **查词**（PRD §8.1）：第一期只支持**精确查询**（无模糊/容错）；命中即自动收录、同词唯一、
  记来源书；词库没有的词 404、非法输入 422（`VocabularyService.lookup`）。
- **查词不中断播放**（PRD §8.1 核心场景）：查词是**独立 GET**，不触碰播放心跳行。本文件用
  「查词前后进度行**逐字段不变** + 之后**还能继续上报心跳**」验，而不是只看查词接口 200
  （只看 200 的话，任何"查词顺手写了进度"的实现都能绿）。
- **统计三口径**（PRD §8.1.1 实现说明，2026-10-16）：收录词数 / 累计查词次数 / 来源书目数由
  服务端 `GET /api/miniapp/vocabulary/summary` 聚合，**前端不另算**（本文件含一条静态判据：
  页面必须调聚合接口、不得再 `reduce`/`Set` 自算）；累计查词**含已移出的词**——「删除后统计
  同步下降」的词数 -1、次数不回退。
- **会员态语义**（`identity.guards` R-313 矩阵，不是这里自定的判据）：
  `vocab_view` 过期放行（可查看）、`vocab_write` 过期 422「生词本只读」、
  `lookup` 过期**仅音频场景内**（手头在借书）才放行 —— 故"不可新增"验的是
  「过期后**脱离播放语境**查词被拒」。

手法与红线：
- 环境**不跑** `seed_dictionary`（`features/environment.py` 只播 admin/config 两支），而查词
  必须命中词典 → 造数前调一次 `seed_dictionary()`（幂等：只 SELECT 演示词是否已在表里；
  词典表不在每场景清表清单内，全量 335 万在不打扰范围）。
- 场景 2/3/4/10 的 feature 文本省略了"当"（承接背景"正在播放"的语境）——步骤内**真实发起**
  查词/删除再断言，**不预置 `vocabularies` 行**（预置行 = 假绿）。
- 场景 8「假如 小明已收录 "adventure" 且累计查词 20 次」但「那么 收录词数仍为 12」——
  假如句没写词数，按 feature 叙事（承接场景 7 的 12 词/3 书/20 次）把同一批数据建出来，
  **判据一行未放宽**（见 docs/09 第六十四轮）。
"""

from pathlib import Path

from behave import given, then, when  # type: ignore[import-not-found]

from tests.unit.test_wm10_concurrency import _db, _h

PHONE = "13981037961"  # 生词本场景专用家长（家长手机号唯一，与本仓其他 feature 错开）
BOOK_SECONDS = 1800  # 背景书目音频全长（心跳分母；本域只借它当"正在播放"的语境）

#: 12 个词全部取自 `seed_dictionary` 的演示词库（"adventure" 是手册验收词）
WORDS_12 = [
    "adventure",
    "amazing",
    "animal",
    "answer",
    "apple",
    "beautiful",
    "become",
    "begin",
    "believe",
    "bird",
    "birthday",
    "brave",
]

PAGE_JS = Path(__file__).resolve().parents[2] / "miniapp/pages/reading-pkg/vocabulary/vocabulary.js"


# ---------- 基建 ----------


def _db_session():
    return _db()


def _admin(context) -> dict:
    if getattr(context, "h", None) is None:
        context.h = _h(context.client)
    return context.h


def _ensure_dictionary() -> None:
    """词典演示词（environment 不播这支 seed；幂等，重复调用只多一次 SELECT）。"""
    from backend.seeds.seed_dictionary import seed as seed_dictionary

    seed_dictionary()


def _pay_order(context, order_id: int) -> None:
    r = context.client.post(
        f"/api/admin/orders/{order_id}/confirm-payment",
        json={"pay_method": "scan"},
        headers=context.h,
    )
    assert r.status_code == 200, r.text


def _book(context, title: str, *, with_audio: bool = False) -> dict:
    created = context.client.post(
        "/api/admin/books",
        json={"isbn": None, "title": title, "word_count": 2500, "copy_count": 1},
        headers=context.h,
    )
    assert created.status_code == 200, created.text
    book = created.json()
    from tests.unit.helpers import force_book_on

    force_book_on(context.client, context.h, book["id"])
    if with_audio:
        from backend.config import get_settings
        from backend.domain.catalog.models import Book

        rel = f"bdd/vocab-audio-{book['id']}.mp3"
        full = Path(get_settings().UPLOADS_DIR) / rel
        full.parent.mkdir(parents=True, exist_ok=True)
        if not full.is_file():
            full.write_bytes(b"ID3fake-mp3")
        with _db_session() as db:
            db.query(Book).filter(Book.id == book["id"]).update(
                {"audio_path": rel, "audio_duration_seconds": BOOK_SECONDS}
            )
            db.commit()
    book["title"] = title
    return book


def _ctx(context) -> dict:
    ctx = getattr(context, "vocab_ctx", None)
    assert ctx is not None, "背景步骤未执行：本功能每个场景都以「正在播放《Dog Man》音频」开场"
    return ctx


def _lookup(context, word: str, book_id: int | None = None, *, expect: int = 200):
    ctx = _ctx(context)
    params = {"word": word, "child_id": ctx["child"]["id"]}
    if book_id:
        params["book_id"] = book_id
    r = context.client.get("/api/miniapp/vocabulary/lookup", params=params, headers=ctx["mini"])
    assert r.status_code == expect, (word, r.status_code, r.text)
    return r.json()


def _list(context) -> list[dict]:
    ctx = _ctx(context)
    r = context.client.get(
        "/api/miniapp/vocabulary",
        params={"child_id": ctx["child"]["id"]},
        headers=ctx["mini"],
    )
    assert r.status_code == 200, r.text
    return r.json()


def _summary(context) -> dict:
    ctx = _ctx(context)
    r = context.client.get(
        "/api/miniapp/vocabulary/summary",
        params={"child_id": ctx["child"]["id"]},
        headers=ctx["mini"],
    )
    assert r.status_code == 200, r.text
    return r.json()


def _vocab_rows(context) -> list:
    """生词行（**含软删**——"历史查询记录不受影响"要能看到被移出的那行）。"""
    from backend.domain.reading.models import Vocabulary

    with _db_session() as db:
        return (
            db.query(Vocabulary)
            .filter(Vocabulary.child_id == _ctx(context)["child"]["id"])
            .order_by(Vocabulary.id)
            .all()
        )


def _row_of(context, word: str):
    rows = [r for r in _vocab_rows(context) if r.word == word]
    assert len(rows) == 1, f"{word} 的行数应为 1（同词唯一），实为 {len(rows)}"
    return rows[0]


def _record(context, word: str, book_id: int | None = None) -> dict:
    """确保该词已被收录（真实查词；已收录则再查一次也不影响"唯一"语义）。"""
    return _lookup(context, word, book_id)


def _progress_row(child_id: int, book_id: int):
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


def _progress_snapshot(child_id: int, book_id: int) -> dict:
    row = _progress_row(child_id, book_id)
    assert row is not None, "进度行不存在（背景步骤的心跳没落库）"
    return {
        "intervals": row.intervals,
        "coverage_seconds": row.coverage_seconds,
        "last_position": row.last_position,
        "finished": row.finished,
        "reading_minutes": row.reading_minutes,
        "last_report_at": row.last_report_at,
    }


def _backdate(child_id: int, book_id: int, seconds: int) -> None:
    """把 `last_report_at` 拨回 N 秒（防刷按**服务端墙钟**算，只能造时间流逝）。"""
    from datetime import timedelta

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
        assert row is not None
        row.last_report_at = row.last_report_at - timedelta(seconds=seconds)
        db.commit()


def _report(context, position: int, session_start: int = 0):
    ctx = _ctx(context)
    return context.client.post(
        "/api/miniapp/reading/progress",
        json={
            "child_id": ctx["child"]["id"],
            "book_id": ctx["book"]["id"],
            "position": position,
            "session_start": session_start,
        },
        headers=ctx["mini"],
    )


def _build_library(context, total_words: int, books: int, total_lookups: int) -> dict:
    """真实链路造「N 个词 / M 本书 / K 次查词」：先铺词（每词 1 次），再把剩余次数摊到已有词上。"""
    ctx = _ctx(context)
    created = [_book(context, t) for t in ("Cat Kid", "Pete the Cat", "Frog and Toad")[:books]]
    assert len(created) == books
    for i, word in enumerate(WORDS_12[:total_words]):
        _lookup(context, word, created[i % books]["id"])
    extra = total_lookups - total_words
    assert extra >= 0, "累计查词次数不能小于收录词数（每词至少查过一次）"
    for i in range(extra):
        _lookup(context, WORDS_12[i % total_words], created[i % books]["id"])
    s = _summary(context)
    three = ("word_count", "lookup_total", "book_count")
    # P3 第一百一十六批：summary 由三口径扩为五口径（week_count/repeat_count 一并下沉）。
    # 这里**只投影造数关心的三口径**返回——本场景的判据是"造多少就是多少"，
    # 五口径齐全性由下一支（`五个数字与服务端聚合口径一致`）断言，两处不重复也不互吞。
    assert {k: s[k] for k in three} == {
        "word_count": total_words,
        "lookup_total": total_lookups,
        "book_count": books,
    }, s
    ctx["source_books"] = created
    return {k: s[k] for k in three}


# ---------- 背景：播放语境 ----------


@given('孩子 "{name}" 正在播放《{title}》音频')
def step_child_playing(context, name: str, title: str) -> None:
    """背景：建有效会员孩子 + 上架带音频的书 + **真实上报一次心跳**（服务端有进度行）。

    「正在播放」= 有一条已落库的播放心跳（查词场景要验的正是它**不被动到**）；
    不借出：在册会员听**全馆在架**音频本来就不需要借阅（PRD §6.1），
    过期态场景因此天然落到"手头无在借书"的矩阵格子上。
    """
    _ensure_dictionary()
    h = _admin(context)
    parent = context.client.post(
        "/api/admin/members/parents", json={"name": "生词本家长", "phone": PHONE}, headers=h
    ).json()
    child = context.client.post(
        f"/api/admin/members/parents/{parent['id']}/children", json={"name": name}, headers=h
    ).json()
    dep = context.client.post(
        f"/api/admin/deposits/children/{child['id']}/orders", headers=h
    ).json()
    _pay_order(context, dep["order_id"])
    fee = context.client.post(
        "/api/admin/orders",
        json={"child_id": child["id"], "order_type": "observation_fee"},
        headers=h,
    ).json()
    _pay_order(context, fee["id"])
    login = context.client.post("/api/miniapp/login", json={"phone": PHONE, "code": "1234"}).json()
    assert login.get("token"), login
    book = _book(context, title, with_audio=True)
    context.vocab_ctx = {
        "parent": parent,
        "child": child,
        "mini": {"Authorization": f"Bearer {login['token']}"},
        "book": book,
    }
    r = _report(context, position=10, session_start=0)
    assert r.status_code == 200, r.text
    context.vocab_ctx["progress_before"] = _progress_snapshot(child["id"], book["id"])


# ---------- 场景 1：查词不中断播放 ----------


@when('{child}在下半屏输入单词 "{word}"')
def step_type_word(context, child: str, word: str) -> None:
    """播放页下半屏查词（真实 GET；上半屏是 innerAudioContext，服务端侧看进度行）。"""
    context.vocab_ctx["lookup_resp"] = _lookup(context, word, _ctx(context)["book"]["id"])


@then("释义与音标即时显示")
def step_definition_shown(context) -> None:
    """「即时」= 同步返回（不是异步轮询/待生成），且音标与释义都在这一个响应里。"""
    resp = context.vocab_ctx.get("lookup_resp")
    assert resp, "上一步没有发起查词"
    assert resp["word"] == "adventure"
    assert resp["phonetic"].startswith("/") and resp["phonetic"].endswith("/"), resp
    assert resp["translation"] or resp["definition"], resp
    assert resp["recorded"] is True


@then("上半屏音频继续播放不受影响")
def step_playback_untouched(context) -> None:
    """判据不是"查词接口 200"，而是进度行**逐字段不变**且**还能继续上报心跳**。

    进度行任何一个字段被查词动过（区间合并/覆盖秒数/上次位置/完播/阅读时长/上次上报时刻），
    都会让"边听边查"变成"查词即丢进度"——这是 PRD §8.1 核心场景要拦的。
    """
    ctx = _ctx(context)
    child_id, book_id = ctx["child"]["id"], ctx["book"]["id"]
    assert _progress_snapshot(child_id, book_id) == ctx["progress_before"], "查词动了播放心跳行"
    # 音频权限也没被查词影响（同一本书仍可播）
    perm = context.client.get(
        f"/api/miniapp/books/{book_id}/audio-permission",
        params={"child_id": child_id},
        headers=ctx["mini"],
    )
    assert perm.status_code == 200 and perm.json()["allowed"] is True, perm.text
    # 播放继续：拨回 30 秒墙钟后继续上报（Δ覆盖 15s ≤ 30s×2×1.2 容差），覆盖秒数只增不减
    before = _progress_snapshot(child_id, book_id)
    _backdate(child_id, book_id, 30)
    r = _report(context, position=25, session_start=10)
    assert r.status_code == 200, r.text
    after = _progress_snapshot(child_id, book_id)
    assert after["coverage_seconds"] > before["coverage_seconds"], (before, after)
    assert after["last_position"] == 25, after


# ---------- 场景 2/3：自动收录与唯一 ----------


@then('"{word}" 被自动收录且记录来源《{title}》')
def step_auto_collected(context, word: str, title: str) -> None:
    """本场景在 feature 文本里没有"当"（承接背景"正在播放"的语境）——步骤内真实发起查词。"""
    ctx = _ctx(context)
    resp = _lookup(context, word, ctx["book"]["id"])
    assert resp["recorded"] is True, resp
    rows = [r for r in _list(context) if r["word"] == word]
    assert len(rows) == 1, rows
    assert rows[0]["source_title"] == title, rows[0]
    assert rows[0]["book_id"] == ctx["book"]["id"], rows[0]


@when('{child}再次查询 "{word}"')
def step_lookup_again(context, child: str, word: str) -> None:
    """「再次」= 已经有第一次。若本场景此前没查过这个词（场景 3 没有"假如"，只查一次），
    先补一次真实首次查词；已经收录过的（场景 8 的 12 词库）则**只查这一次**——
    否则次数会比"那么"里写的多 1（判据是 20→21，不是 22）。"""
    ctx = _ctx(context)
    book_id = ctx["book"]["id"]
    active = [r for r in _vocab_rows(context) if r.word == word and r.is_deleted == 0]
    seeded = None
    if not active:
        seeded = _lookup(context, word, book_id)
    before = _row_of(context, word).lookup_count
    second = _lookup(context, word, book_id)
    ctx["repeat"] = {"seeded": seeded, "before": before, "second": second}


@then("生词本不重复收录")
def step_no_duplicate(context) -> None:
    info = context.vocab_ctx["repeat"]
    if info["seeded"] is not None:
        assert info["seeded"]["recorded"] is True, info["seeded"]
    assert info["second"]["recorded"] is False, info["second"]
    rows = [r for r in _list(context) if r["word"] == "adventure"]
    assert len(rows) == 1, rows
    assert rows[0]["lookup_count"] == info["before"] + 1, (rows[0], info)


# ---------- 场景 4/10：删除 ----------


@when('{child}删除生词 "{word}"')
def step_remove_word(context, child: str, word: str) -> None:
    """先确保这个词（另有第二个词垫底，便于"词数减少 1"有对照），再走真实删除接口。"""
    ctx = _ctx(context)
    book_id = ctx["book"]["id"]
    _record(context, word, book_id)
    _record(context, "apple", book_id)
    ctx["summary_before_remove"] = _summary(context)
    row = _row_of(context, word)
    ctx["removed_row_id"] = row.id
    ctx["removed_lookup_count"] = row.lookup_count
    r = context.client.delete(
        f"/api/miniapp/vocabulary/{row.id}",
        params={"child_id": ctx["child"]["id"]},
        headers=ctx["mini"],
    )
    assert r.status_code == 200, r.text


@then("从生词本移除且历史查询记录不受影响")
def step_removed_history_kept(context) -> None:
    word = "adventure"
    assert all(r["word"] != word for r in _list(context)), "仍出现在生词本列表里"
    row = _row_of(context, word)
    assert row.is_deleted == 1, "应软删（业务主数据禁物理删除）"
    assert row.lookup_count == context.vocab_ctx["removed_lookup_count"], "历史查词次数被改写"
    # 历史没被抹掉的正面证据：重新查同一个词 → 复活收录，次数在原值上继续累加
    again = _lookup(context, word, _ctx(context)["book"]["id"])
    assert again["recorded"] is True, again
    assert _row_of(context, word).lookup_count == context.vocab_ctx["removed_lookup_count"] + 1


@then("收录词数减少 {n:d}")
def step_word_count_drops(context, n: int) -> None:
    before = context.vocab_ctx["summary_before_remove"]["word_count"]
    assert _summary(context)["word_count"] == before - n, _summary(context)


@then("累计查词次数不因删除而回退（历史查询已发生）")
def step_lookup_total_kept(context) -> None:
    before = context.vocab_ctx["summary_before_remove"]["lookup_total"]
    after = _summary(context)["lookup_total"]
    assert after == before, f"删除回退了累计查词：{before} → {after}"


# ---------- 场景 5：精确查询 ----------


@when('查询单词 "{word}"')
def step_lookup_plain(context, word: str) -> None:
    context.vocab_ctx["lookup_resp"] = _lookup(context, word)


@then("返回音标与中文释义")
def step_phonetic_and_zh(context) -> None:
    resp = context.vocab_ctx["lookup_resp"]
    assert resp["word"] == "apple"
    assert resp["phonetic"].startswith("/") and resp["phonetic"].endswith("/"), resp
    assert any("\u4e00" <= ch <= "\u9fff" for ch in resp["translation"]), resp


# ---------- 场景 6：过期只读 ----------


@given("{child}会员已过期")
def step_member_expired(context, child: str) -> None:
    """先留一个已收录的词（在册时查的词——过期后"可查看"才有内容可看），再落到过期态。"""
    from datetime import date, timedelta

    from backend.domain.identity.models import Child

    ctx = _ctx(context)
    _record(context, "adventure", ctx["book"]["id"])
    with _db_session() as db:
        db.query(Child).filter(Child.id == ctx["child"]["id"]).update(
            {"member_status": "formal", "member_expire": date.today() - timedelta(days=1)}
        )
        db.commit()


@then("生词本可查看但不可新增")
def step_vocab_readonly(context) -> None:
    """R-313 矩阵的"只读"= 查看类放行 + 写入类拒绝；过期成员查词仅限播放语境（手头在借书）。

    本场景孩子**没有借书**（背景的"正在播放"是在册时的全馆在架音频）→ 过期后任何查词都落在
    "仅可在播放已借图书时查词"这一格，生词本因而不能再扩张；历史生词仍看得见。
    """
    ctx = _ctx(context)
    rows = _list(context)
    assert [r["word"] for r in rows] == ["adventure"], rows  # 可查看（且历史还在）
    resp = context.client.get(
        "/api/miniapp/vocabulary/lookup",
        params={"word": "apple", "child_id": ctx["child"]["id"]},
        headers=ctx["mini"],
    )
    assert resp.status_code == 422, resp.text
    assert "仅可在播放已借图书时查词" in resp.json()["detail"], resp.text
    # 写入类（移除生词）同样只读
    rm = context.client.delete(
        f"/api/miniapp/vocabulary/{rows[0]['id']}",
        params={"child_id": ctx["child"]["id"]},
        headers=ctx["mini"],
    )
    assert rm.status_code == 422, rm.text
    assert rm.json()["detail"] == "会员已过期，生词本只读", rm.text
    # 既没新增、也没删掉：生词本内容一字未变
    assert [r["word"] for r in _list(context)] == ["adventure"]


# ---------- 场景 7/8：统计口径 ----------


@given("{child}共收录 {n:d} 个生词 来自 {b:d} 本书 累计查词 {k:d} 次")
def step_build_library(context, child: str, n: int, b: int, k: int) -> None:
    context.vocab_ctx["built"] = _build_library(context, n, b, k)


@then("统计行显示 收录 {n:d} 词 / 累计查询 {k:d} 次 / 来源 {b:d} 本书")
def step_summary_row(context, n: int, k: int, b: int) -> None:
    built = context.vocab_ctx.get("built") or context.vocab_ctx.get("padded")
    assert built, "统计行场景缺少造数步骤"
    assert {"word_count": n, "lookup_total": k, "book_count": b} == built, built
    s = _summary(context)
    assert {key: s[key] for key in built} == built, s  # 三口径与造数一致（五口径见下一支）


@then("五个数字与服务端聚合口径一致（不前端另算）")
def step_server_side_aggregate(context) -> None:
    """三层证据：① 服务端有聚合端点且**五口径**齐全；② 列表自洽（前端渲染的列表与口径不打架）；
    ③ 页面**消费**聚合端点、不再 `reduce`/`Set` 自算（"不前端另算"是判据原文，属可判定事实）。

    P3 第一百一十六批：列表改分页后，本周新增/查过多次若仍由前端对**整表**滤波就会只剩当前页
    的结果——两枚指标随之下沉服务端，故这里是"五口径齐全"而不是"键数随意"。
    本场景词数为 12 < 每页 20，所以"列表行数 == word_count"这一条成立（跨页口径由
    `tests/unit/test_audit8_p3_miniapp_20261022.py::test_vocabulary_summary_counts_all_pages` 覆盖 25 词）。
    """
    s = _summary(context)
    assert set(s) == {
        "word_count",
        "lookup_total",
        "book_count",
        "week_count",
        "repeat_count",
    }, s
    rows = _list(context)
    assert len(rows) == s["word_count"]
    assert sum(r["lookup_count"] for r in rows) == s["lookup_total"]
    assert len({r["source_title"] for r in rows if r["source_title"]}) == s["book_count"]
    # 新下沉的两枚：语义必须与列表逐行一致（不是另算一套）
    assert s["repeat_count"] == sum(1 for r in rows if (r["lookup_count"] or 1) > 1), s
    assert s["week_count"] <= s["word_count"], s
    src = PAGE_JS.read_text(encoding="utf-8")
    assert "api.summaryVocabulary(" in src, "生词本页没有消费服务端聚合接口"
    assert "list.reduce(" not in src, "生词本页仍在用 reduce 自算统计（判据：不前端另算）"
    assert "new Set(list.map(" not in src, "生词本页仍在用 Set 自算来源书目数"


@given('{child}已收录 "{word}" 且累计查词 {k:d} 次')
def step_has_word_and_total(context, child: str, word: str, k: int) -> None:
    """假如句只点名一个词与总次数，而"那么"要求词数为 12 —— 按 feature 叙事把同一批
    数据建出来（12 个词含该词、3 本书、总数 k），判据未放宽。"""
    assert word in WORDS_12, f"{word} 不在本场景词表里（词表见 WORDS_12）"
    s = _build_library(context, len(WORDS_12), 3, k)
    assert s["lookup_total"] == k and s["word_count"] == len(WORDS_12), s
    context.vocab_ctx["padded"] = s


@then("收录词数仍为 {n:d}")
def step_word_count_unchanged(context, n: int) -> None:
    assert _summary(context)["word_count"] == n, _summary(context)


@then("累计查词次数变为 {k:d}")
def step_lookup_total_plus_one(context, k: int) -> None:
    s = _summary(context)
    assert s["lookup_total"] == k, s
    assert s["lookup_total"] == context.vocab_ctx["padded"]["lookup_total"] + 1, s


# ---------- 场景 9：闪卡 ----------


@when('{child}点击生词本里的 "{word}"')
def step_tap_word(context, child: str, word: str) -> None:
    """点词要弹的闪卡，数据全来自列表接口的词典 join（点开不额外请求）——把那一行取出来。"""
    ctx = _ctx(context)
    _record(context, word, ctx["book"]["id"])
    rows = [r for r in _list(context) if r["word"] == word]
    assert len(rows) == 1, rows
    ctx["card"] = rows[0]


@then("弹出闪卡显示音标、中文释义与来源书目")
def step_flashcard_fields(context) -> None:
    card = context.vocab_ctx["card"]
    assert card["phonetic"].startswith("/"), card
    assert any("\u4e00" <= ch <= "\u9fff" for ch in card["translation"]), card
    assert card["definition"], card
    assert card["source_title"] == "Dog Man", card
    # 闪卡里的"查 N 次/收录日期"也同源（前端不再猜）
    assert card["lookup_count"] >= 1 and card["created_at"], card
