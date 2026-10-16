# tests/unit/test_vocabulary_summary_20261016.py — 生词本统计三口径（PRD §8.1.1 实现说明）
"""2026-10-16（第六十四批·BDD 解封 vocabulary）：统计三口径收口服务端。

背景：BDD 场景「生词本顶部统计行三口径」的末句是**判据**——"三个数字与服务端聚合口径一致
（不前端另算）"，而解封前这三个数由小程序拿到列表后 `reduce`/`Set` 现算（后端无聚合）。
本文件把口径钉死，BDD 场景则验真机链路：

- `word_count` 只看**可见**行（软删不算）；
- `lookup_total` **含软删行**——「生词本删除后统计同步下降」要求"次数不因删除而回退"；
- `book_count` 去重且忽略空来源（未记来源书的词不算一本书）；
- 同词重复查：次数 +1、词数不变；
- 门禁与列表同源：VOCAB_VIEW（未缴费 422、越权 422），**与 `/vocabulary` 同一行矩阵**。
"""

from fastapi.testclient import TestClient

PHONE = "13800000891"

#: 12 个词全部取自 `seed_dictionary` 的演示词库（dictionary_words 不清表、seed 幂等）
WORDS = [
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


def _h(client, username="admin"):
    r = client.post("/api/admin/login", json={"username": username, "password": "dmkwords123"})
    return {"Authorization": f"Bearer {r.json()['token']}"}


def _member_child(client, h, phone, name):
    """建档 + 缴观察期费用入会（查词门禁要求有效会员）+ 小程序登录。"""
    p = client.post(
        "/api/admin/members/parents", json={"name": "家长", "phone": phone}, headers=h
    ).json()
    c = client.post(
        f"/api/admin/members/parents/{p['id']}/children", json={"name": name}, headers=h
    ).json()
    o = client.post(
        "/api/admin/orders", json={"child_id": c["id"], "order_type": "observation_fee"}, headers=h
    ).json()
    client.post(
        f"/api/admin/orders/{o['id']}/confirm-payment", json={"pay_method": "scan"}, headers=h
    )
    m = client.post("/api/miniapp/login", json={"phone": phone, "code": "1234"}).json()
    return c, {"Authorization": f"Bearer {m['token']}"}


def _book(client, h, title):
    r = client.post(
        "/api/admin/books",
        json={"isbn": None, "title": title, "word_count": 1000, "copy_count": 1},
        headers=h,
    )
    assert r.status_code == 200, r.text
    return r.json()


def _lookup(client, mini, child_id, word, book_id=None):
    params = {"word": word, "child_id": child_id}
    if book_id:
        params["book_id"] = book_id
    r = client.get("/api/miniapp/vocabulary/lookup", params=params, headers=mini)
    assert r.status_code == 200, r.text
    return r.json()


def _summary(client, mini, child_id):
    r = client.get("/api/miniapp/vocabulary/summary", params={"child_id": child_id}, headers=mini)
    assert r.status_code == 200, r.text
    return r.json()


def _list(client, mini, child_id):
    r = client.get("/api/miniapp/vocabulary", params={"child_id": child_id}, headers=mini)
    assert r.status_code == 200, r.text
    return r.json()


def _seed_12_words_3_books_20_lookups(client, h, mini, child_id):
    """真实链路造数：12 个词来自 3 本书、累计 20 次查词（每词 1 次 + 前 8 词各多查 1 次）。"""
    books = [_book(client, h, t) for t in ("Dog Man", "Cat Kid", "Pete the Cat")]
    ids = []
    for i, w in enumerate(WORDS):
        ids.append(_lookup(client, mini, child_id, w, books[i // 4]["id"])["word"])
    for w in WORDS[:8]:
        _lookup(client, mini, child_id, w, books[0]["id"])
    return books, ids


def test_summary_three_metrics_match_server_aggregate(client: TestClient):
    h = _h(client)
    c, mini = _member_child(client, h, PHONE, "小明")
    books, _ = _seed_12_words_3_books_20_lookups(client, h, mini, c["id"])

    s = _summary(client, mini, c["id"])
    assert s == {"word_count": 12, "lookup_total": 20, "book_count": 3}, s
    # 与列表口径必须自洽（前端渲染的是列表，统计行不能与它打架）
    rows = _list(client, mini, c["id"])
    assert len(rows) == s["word_count"]
    assert sum(r["lookup_count"] for r in rows) == s["lookup_total"]
    assert len({r["source_title"] for r in rows if r["source_title"]}) == s["book_count"]
    # 来源书确实分散在 3 本（不是恰好 3 个空来源）
    assert {r["book_id"] for r in rows} == {b["id"] for b in books}

    # 同词重复查：次数 +1、词数不变（成就口径：「查得越多=越该复习」）
    _lookup(client, mini, c["id"], "adventure", books[0]["id"])
    s2 = _summary(client, mini, c["id"])
    assert s2["word_count"] == 12 and s2["lookup_total"] == 21, s2


def test_summary_lookup_total_does_not_roll_back_on_removal(client: TestClient):
    """删除只减「收录词数」；累计查词次数是已发生的历史，不回退（BDD 场景 10 的判据）。"""
    h = _h(client)
    c, mini = _member_child(client, h, PHONE, "小明")
    books, _ = _seed_12_words_3_books_20_lookups(client, h, mini, c["id"])
    before = _summary(client, mini, c["id"])
    rows = _list(client, mini, c["id"])
    target = next(r for r in rows if r["word"] == "adventure" and r["lookup_count"] == 2)

    r = client.delete(
        f"/api/miniapp/vocabulary/{target['id']}", params={"child_id": c["id"]}, headers=mini
    )
    assert r.status_code == 200, r.text
    after = _summary(client, mini, c["id"])
    assert after["word_count"] == before["word_count"] - 1, after
    assert after["lookup_total"] == before["lookup_total"], "累计查词次数不因删除而回退"
    # 该书还剩 3 个词 → 来源书数不变
    assert after["book_count"] == before["book_count"], after
    # 删除后重新查同一个词：复活收录，次数在原值上继续累加（历史没被抹掉）
    _lookup(client, mini, c["id"], "adventure", books[0]["id"])
    revived = _summary(client, mini, c["id"])
    assert revived["word_count"] == before["word_count"], revived
    assert revived["lookup_total"] == before["lookup_total"] + 1, revived


def test_summary_same_guard_as_list(client: TestClient):
    """统计行是列表页的一部分：门禁必须与 `/vocabulary` 同一行矩阵，否则出现
    "看得到列表却拉不到统计数字"。"""
    h = _h(client)
    c, mini = _member_child(client, h, PHONE, "小明")
    _lookup(client, mini, c["id"], "apple")
    # 越权：别的家长的孩子 → 422「孩子不存在」（与列表同语义）
    other, other_mini = _member_child(client, h, "13800000892", "小红")
    r = client.get(
        "/api/miniapp/vocabulary/summary", params={"child_id": other["id"]}, headers=mini
    )
    assert r.status_code == 422 and r.json()["detail"] == "孩子不存在", r.text
    r2 = client.get("/api/miniapp/vocabulary", params={"child_id": other["id"]}, headers=mini)
    assert r2.status_code == r.status_code and r2.json() == r.json()
    # 未缴费（同一家长的另一孩）→ 与列表同拒
    from backend.database import get_session
    from backend.domain.identity.models import Child

    with get_session() as db:
        unpaid = Child(parent_id=other["parent_id"], name="未缴费孩")
        db.add(unpaid)
        db.commit()
        unpaid_id = unpaid.id
    r3 = client.get(
        "/api/miniapp/vocabulary/summary", params={"child_id": unpaid_id}, headers=other_mini
    )
    r4 = client.get("/api/miniapp/vocabulary", params={"child_id": unpaid_id}, headers=other_mini)
    assert r3.status_code == 422, r3.text
    assert r3.json() == r4.json(), (r3.text, r4.text)
    assert "入会" in r3.json()["detail"]
