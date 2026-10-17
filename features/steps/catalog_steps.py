# features/steps/catalog_steps.py — FEAT-007~011/013/014 图书资产管理 步骤定义
"""2026-10-15（第六十一批·BDD 解封第六批）· **图书资产管理 11 场景**全部转真实执行。

判据与实现同源（动手前逐条亲验，红线 108/136）；场景文本的五处订正与三处缺口见 `catalog.feature`
顶部注释。本文件要点：

- **录入即下架**：`POST /api/admin/books` 落库 `status=OFF`（D1：逐本完善后再上架），
  所以"要能被借/被读"的书必须显式上架——上架走**真实端点** `toggle-status`（受 D1 五项校验拦），
  或走 `force_book_on`（用配置开关跳过校验，`tests/unit/helpers.py`，幂等）。
- **Excel 逐行校验**：行号是**数据行序号**（第 1 条数据 = 第 1 行，表头不算），错误文案形如
  `第N行: 原因`；同 ISBN 命中则只加副本不建书目。
- **封面**：`save_cover_jpg` 规范化 JPEG（EXIF 摆正 + 长边限幅）→ 路径
  `cover/{isbn[:4]}/{isbn}_{content_tag}.jpg`（`content_tag` = sha256 前 12 位十六进制）。
- **下架影响面**（PRD §4.4 七行）：列表隐藏 / 禁借禁约 / **音频停播** / **未开始的测验不可开始** /
  已通过成绩保留 / 借出的仍要还 / 已得词数积分等级打卡不回收。后两条与"音频/测验"由本批
  G15/G16 补齐（此前只有禁借禁约是真的）。
- **副本状态机**：`ALLOWED_TRANSITIONS`（`catalog/models.py`）——`maintenance → lost` **是合法边**，
  本域断"非法流转被拒绝"必须挑真正非法的边（`lost → borrowed`）。
"""

import io
import re

from behave import given, then, when

from tests.unit.helpers import force_book_on
from tests.unit.test_wm10_concurrency import _db, _h

PHONE_OK = "13981037952"  # 本域专用家长（手机号唯一，与其它域错开）
PHONE_B = "13981037953"
PHONE_C = "13981037954"
ISBN_DOGMAN = "9780545582889"


# ---------- 基建 ----------


def _admin(context) -> dict:
    if getattr(context, "h", None) is None:
        context.h = _h(context.client)
    return context.h


def _body(**kw) -> dict:
    body = {"isbn": None, "title": "样例书", "word_count": 1000, "copy_count": 1}
    if "copies" in kw:  # 场景里说"副本数"，接口字段叫 copy_count
        kw["copy_count"] = kw.pop("copies")
    body.update(kw)
    return body


def _create_book(context, **kw) -> dict:
    r = context.client.post("/api/admin/books", json=_body(**kw), headers=_admin(context))
    assert r.status_code == 200, r.text
    return r.json()


def _copies(context, book_id: int) -> list[dict]:
    r = context.client.get(f"/api/admin/books/{book_id}/copies", headers=_admin(context))
    assert r.status_code == 200, r.text
    return r.json()


def _book_row(context, book_id: int) -> dict:
    r = context.client.get(f"/api/admin/books/{book_id}", headers=_admin(context))
    assert r.status_code == 200, r.text
    return r.json()


def _xlsx(rows: list[list]) -> io.BytesIO:
    """按真实模板表头造导入文件（列序与 `import_service.HEADER` 一致）。"""
    from openpyxl import Workbook

    wb = Workbook()
    ws = wb.active
    ws.title = "图书导入"
    ws.append(["ISBN", "书名*", "作者", "AR值", "词数*", "主题", "适读阶段", "副本数"])
    for r in rows:
        ws.append(r)
    buf = io.BytesIO()
    wb.save(buf)
    buf.seek(0)
    return buf


def _import(context, rows: list[list]) -> dict:
    r = context.client.post(
        "/api/admin/books/import",
        files={
            "file": (
                "books.xlsx",
                _xlsx(rows),
                "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            )
        },
        headers=_admin(context),
    )
    assert r.status_code == 200, r.text
    return r.json()


def _png_bytes() -> bytes:
    """真 PNG（走图片管线：规范化 JPEG 要能真的解码这张图）。"""
    from PIL import Image

    buf = io.BytesIO()
    Image.new("RGB", (120, 80), (200, 40, 40)).save(buf, format="PNG")
    assert buf.getvalue()[:8] == b"\x89PNG\r\n\x1a\n", "造出来的必须真是 PNG"
    return buf.getvalue()


def _wav_bytes() -> bytes:
    """真 WAV（合法文件 + 合法扩展名 ⇒ 被拒的原因只能是格式规则本身）。"""
    import struct
    import wave

    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(1)
        w.setframerate(8000)
        w.writeframes(struct.pack("<800b", *([0] * 800)))
    return buf.getvalue()


def _upload_cover(context, book_id: int, data: bytes, filename: str):
    return context.client.post(
        f"/api/admin/books/{book_id}/cover",
        files={"file": (filename, data, "application/octet-stream")},
        headers=_admin(context),
    )


def _upload_audio(context, book_id: int, data: bytes, filename: str):
    return context.client.post(
        f"/api/admin/books/{book_id}/audio",
        files={"file": (filename, data, "application/octet-stream")},
        headers=_admin(context),
    )


def _family(context, phone: str, name: str) -> tuple[dict, dict, dict]:
    """建家长 + 孩子 + 小程序 token，返回 (parent, child, mini_headers)。"""
    parent = context.client.post(
        "/api/admin/members/parents",
        json={"name": f"家长{name}", "phone": phone},
        headers=_admin(context),
    ).json()
    child = context.client.post(
        f"/api/admin/members/parents/{parent['id']}/children",
        json={"name": name},
        headers=_admin(context),
    ).json()
    login = context.client.post("/api/miniapp/login", json={"phone": phone, "code": "1234"}).json()
    return parent, child, {"Authorization": f"Bearer {login['token']}"}


def _pay(context, child_id: int, order_type: str) -> None:
    o = context.client.post(
        "/api/admin/orders",
        json={"child_id": child_id, "order_type": order_type},
        headers=_admin(context),
    ).json()
    rc = context.client.post(
        f"/api/admin/orders/{o['id']}/confirm-payment",
        json={"pay_method": "scan"},
        headers=_admin(context),
    )
    assert rc.status_code == 200, rc.text


def _member_child(context, phone: str, name: str) -> tuple[dict, dict]:
    """有效会员孩子（押金 + 观察期 + 年费），返回 (child, mini_headers)。"""
    _, child, mini = _family(context, phone, name)
    dep = context.client.post(
        f"/api/admin/deposits/children/{child['id']}/orders", headers=_admin(context)
    ).json()
    rc = context.client.post(
        f"/api/admin/orders/{dep['order_id']}/confirm-payment",
        json={"pay_method": "scan"},
        headers=_admin(context),
    )
    assert rc.status_code == 200, rc.text
    _pay(context, child["id"], "observation_fee")
    _pay(context, child["id"], "formal_fee")
    return child, mini


def _seed_progress(child_id: int, book_id: int, *, finished: bool) -> None:
    """造"这本书的阅读进度行"：`finished=1` 即测验解锁（完播是解锁的唯一客观依据）。"""
    from backend.domain.reading.models import ReadingProgress

    with _db() as db:
        db.add(
            ReadingProgress(
                child_id=child_id,
                book_id=book_id,
                total_seconds=1800,
                coverage_seconds=1800 if finished else 600,
                intervals="[[0,1800]]" if finished else "[[0,600]]",
                finished=1 if finished else 0,
                last_position=1800 if finished else 600,
                is_deleted=0,
            )
        )
        db.commit()


def _seed_words(child_id: int, book_id: int, words: int) -> None:
    """造"测验已通过"的词账（PRD §6.2：通过才计词；有词账 ⇒ 该书视为已解锁）。"""
    from backend.domain.growth.models import ChildGrowthState, WordsLedger

    with _db() as db:
        db.add(
            WordsLedger(
                child_id=child_id, book_id=book_id, word_count=words, source="quiz", is_deleted=0
            )
        )
        state = (
            db.query(ChildGrowthState)
            .filter(ChildGrowthState.child_id == child_id, ChildGrowthState.is_deleted == 0)
            .first()
        )
        if state is None:
            state = ChildGrowthState(child_id=child_id)
            db.add(state)
        state.words_total = (state.words_total or 0) + words
        db.commit()


def _words_total(child_id: int) -> int:
    from backend.domain.growth.models import ChildGrowthState

    with _db() as db:
        row = (
            db.query(ChildGrowthState)
            .filter(ChildGrowthState.child_id == child_id, ChildGrowthState.is_deleted == 0)
            .first()
        )
        return int(row.words_total or 0) if row else 0


def _copy_status(copy_id: int) -> str:
    from backend.domain.catalog.models import BookCopy

    with _db() as db:
        return db.query(BookCopy).filter(BookCopy.id == copy_id).first().status


def _toggle(context, book_id: int):
    return context.client.post(f"/api/admin/books/{book_id}/toggle-status", headers=_admin(context))


def _off_shelf(context, book_id: int):
    """下架（幂等：已上架才切）。"""
    if _book_row(context, book_id)["status"] == 1:
        assert _toggle(context, book_id).status_code == 200
    assert _book_row(context, book_id)["status"] == 0, "此刻必须是下架态"


# ---------- 场景 1：录入书目 ----------


@when('馆员录入 ISBN "{isbn}" 书名《{title}》总词数 {wc:d}')
def step_create_with_isbn(context, isbn, title, wc):
    context.book = _create_book(context, isbn=isbn, title=title, word_count=wc)


@then("书目创建成功 副本数默认 1")
def step_created_one_copy(context):
    book = context.book
    assert book["id"] and book["title"] == "Dog Man", book
    assert book["isbn"] == ISBN_DOGMAN and book["word_count"] == 2500, book
    copies = _copies(context, book["id"])
    assert len(copies) == 1, f"副本数应默认 1，实得 {len(copies)}"


# ---------- 场景 2：AR 值可后补 ----------


@when("馆员录入书目时未填 AR 值")
def step_create_without_ar(context):
    context.book = _create_book(context, title="无 AR 样例", word_count=800)


@then('录入成功 且该书出现在"AR 待配置"筛选列表')
def step_ar_pending(context):
    assert context.book["id"], context.book
    r = context.client.get(
        "/api/admin/books", params={"ar_pending": "true"}, headers=_admin(context)
    )
    assert r.status_code == 200, r.text
    ids = [b["id"] for b in r.json()["items"]]
    assert context.book["id"] in ids, f"「AR 待配置」应包含该书：{ids}"
    assert _book_row(context, context.book["id"])["ar_level"] in (None, ""), "AR 应仍为空"


# ---------- 场景 3：无 ISBN 图书走内部编号 ----------


@when("馆员录入无 ISBN 的书目")
def step_create_no_isbn(context):
    context.book = _create_book(context, title="内部编号样例", word_count=700)


@then("系统生成内部编号 且仅支持后台单本录入")
def step_internal_code(context):
    code = context.book["internal_code"]
    assert code and code.startswith("LOCAL-"), f"应生成内部编号，实得 {code!r}"
    # "不走 Excel"（PRD §4.3）：无 ISBN 行必须被**行级拒绝**，不建书目
    before = context.client.get("/api/admin/books", headers=_admin(context)).json()["total"]
    res = _import(context, [["", "Excel 里的无 ISBN 书", "作者", "", "500", "", "", "1"]])
    assert res["success_count"] == 0, res
    assert res["failed_count"] == 1 and "只能后台单本录入" in res["errors"][0], res
    after = context.client.get("/api/admin/books", headers=_admin(context)).json()["total"]
    assert after == before, "被拒的行不能留下书目"


# ---------- 场景 4：同 ISBN 再次导入增加副本 ----------


@given("系统《{title}》已有 {n:d} 副本")
def step_book_with_copies(context, title, n):
    context.book = _create_book(context, isbn=ISBN_DOGMAN, title=title, copies=n)
    assert len(_copies(context, context.book["id"])) == n


@when("馆员导入同 ISBN 行 副本数量 {m:d}")
def step_import_same_isbn(context, m):
    context.import_result = _import(
        context,
        [[ISBN_DOGMAN, "Dog Man", "Dav Pilkey", "2.5", "2500", "", "", str(m)]],
    )


@then("书目不重复创建 副本增加至 {total:d}")
def step_copies_added(context, total):
    assert context.import_result["success_count"] == 1, context.import_result
    listed = context.client.get(
        "/api/admin/books", params={"keyword": ISBN_DOGMAN}, headers=_admin(context)
    ).json()
    same = [b for b in listed["items"] if b["isbn"] == ISBN_DOGMAN]
    assert len(same) == 1, f"同 ISBN 必须并入原书目，实得 {len(same)} 条"
    assert len(_copies(context, context.book["id"])) == total, "副本数应累加到 3"


# ---------- 场景 5：Excel 批量导入逐行校验 ----------


@when("馆员上传含 {rows:d} 行的导入文件 其中第 {bad:d} 行词数无效")
def step_import_with_bad_row(context, rows, bad):
    lines = []
    for i in range(1, rows + 1):
        isbn = f"978000000{i:04d}"
        if i == bad:
            lines.append([isbn, f"批量书 {i}", "作者", "1.5", "0", "", "", "1"])  # 0 词 = 无效
        else:
            lines.append([isbn, f"批量书 {i}", "作者", "1.5", "300", "", "", "1"])
    context.import_result = _import(context, lines)


@then("{ok:d} 行导入成功")
def step_import_success(context, ok):
    res = context.import_result
    assert res["success_count"] == ok, (
        f"应 {ok} 行成功，实得 {res['success_count']}：{res['errors']}"
    )
    assert res["total_rows"] == 100, res


@then('第 {bad:d} 行报错"{msg}"且不影响其他行')
def step_import_error_row(context, bad, msg):
    res = context.import_result
    assert res["failed_count"] == 1, res
    err = res["errors"][0]
    assert err.startswith(f"第{bad}行"), err
    assert msg in err, f"错误文案应含「{msg}」，实得 {err!r}"
    # "不影响其他行"：99 本真的建出来了（不是只报了个数）
    listed = context.client.get(
        "/api/admin/books", params={"keyword": "批量书", "page_size": 100}, headers=_admin(context)
    ).json()
    assert len(listed["items"]) == 99, f"应有 99 本落库，实得 {len(listed['items'])}"


# ---------- 场景 6：封面统一转 JPG 存储 ----------


@when("馆员上传 PNG 格式封面")
def step_upload_png_cover(context):
    context.book = _create_book(context, isbn=ISBN_DOGMAN, title="封面样例", word_count=1200)
    r = _upload_cover(context, context.book["id"], _png_bytes(), "cover.png")
    assert r.status_code == 200, r.text
    context.cover_path = r.json()["cover_path"]


@then("服务端转换为 JPG 存储")
def step_cover_is_jpg(context):
    import os

    from backend.config import get_settings

    assert context.cover_path.lower().endswith(".jpg"), context.cover_path
    full = os.path.join(get_settings().UPLOADS_DIR, context.cover_path)
    assert os.path.isfile(full), f"封面应真的落盘：{full}"
    with open(full, "rb") as f:
        head = f.read(3)
    assert head == b"\xff\xd8\xff", f"落盘内容必须是 JPEG（实得 {head!r}）"
    # 端上取图也必须是 image/jpeg
    r = context.client.get(
        f"/api/admin/books/{context.book['id']}/cover-media", headers=_admin(context)
    )
    assert r.status_code == 200 and r.headers["content-type"].startswith("image/jpeg"), r.headers


@then("存储路径按内容指纹规则命名（cover/ISBN前4位/ISBN_指纹.jpg）")
def step_cover_path(context):
    pattern = rf"^cover/{ISBN_DOGMAN[:4]}/{ISBN_DOGMAN}_[0-9a-f]{{12}}\.jpg$"
    assert re.fullmatch(pattern, context.cover_path), (
        f"路径应符合内容指纹规则（M2 破缓存单出口），实得 {context.cover_path!r}"
    )


# ---------- 场景 7：音频仅支持 MP3 ----------


@when("馆员上传 WAV 格式音频")
def step_upload_wav(context):
    context.book = _create_book(context, title="音频格式样例", word_count=900)
    context.audio_response = _upload_audio(context, context.book["id"], _wav_bytes(), "story.wav")


@then("上传被拒绝 提示仅支持 MP3")
def step_audio_rejected(context):
    r = context.audio_response
    assert r.status_code == 422, f"应 422，实得 {r.status_code}：{r.text[:160]}"
    assert "仅支持 MP3" in r.json()["detail"], r.text
    assert _book_row(context, context.book["id"])["audio_path"] in (None, ""), "被拒不能留路径"


# ---------- 场景 8：无音频书目入库提醒 ----------


@when("馆员入库一本无音频的书")
def step_create_without_audio(context):
    context.book = _create_book(context, title="无音频样例", word_count=600)
    assert _book_row(context, context.book["id"])["audio_path"] in (None, ""), "前提：确实没音频"


@then("系统提示该书缺少音频 未确认前不可上架")
def step_onboard_blocked(context):
    r = _toggle(context, context.book["id"])
    assert r.status_code == 409, f"D1：缺音频应拦上架（409），实得 {r.status_code}：{r.text[:160]}"
    assert "未传音频" in r.json()["detail"], r.text
    assert _book_row(context, context.book["id"])["status"] == 0, "被拦后仍是下架态"


@then("馆员确认放行后可上架")
def step_onboard_override(context):
    """PRD §4.2「由馆员确认后放行入库」在本平台的载体 = `book_onboarding_check` 开关
    （改配置必填原因 + 留审计；**PRD 要的是单本确认、实现是全局开关** —— 偏差登记在 docs/09 第六十一轮）。"""
    r = context.client.put(
        "/api/admin/configs/book_onboarding_check",
        json={"value": "false", "reason": "该批老书暂无音频，馆员确认先上架（BDD）"},
        headers=_admin(context),
    )
    assert r.status_code == 200, r.text
    try:
        assert _toggle(context, context.book["id"]).status_code == 200
        assert _book_row(context, context.book["id"])["status"] == 1, "放行后应上架"
    finally:
        back = context.client.put(
            "/api/admin/configs/book_onboarding_check",
            json={"value": "true", "reason": "恢复默认"},
            headers=_admin(context),
        )
        assert back.status_code == 200, back.text


# ---------- 场景 9：图书下架影响范围 ----------


@given("《{title}》被某孩子借出且已被另一孩子读完计词")
def step_borrowed_and_read(context, title):
    context.book = _create_book(context, isbn=ISBN_DOGMAN, title=title, word_count=2500)
    book_id = context.book["id"]
    force_book_on(context.client, _admin(context), book_id, "下架场景前提：先上架")
    assert _book_row(context, book_id)["status"] == 1
    context.copy_id = _copies(context, book_id)[0]["id"]

    # 孩子 A：借走（副本 borrowed，且"在借仍需归还"要用它）
    kid_a, _mini_a = _member_child(context, PHONE_OK, "借书孩")
    r = context.client.post(
        "/api/admin/circulation/borrow",
        json={"child_id": kid_a["id"], "copy_id": context.copy_id},
        headers=_admin(context),
    )
    assert r.status_code == 200, r.text
    assert _copy_status(context.copy_id) == "borrowed"

    # 孩子 B：读完并通过测验（词账 + 进度）——用来验"已通过成绩保留 / 已计词数不回收"
    kid_b, mini_b = _member_child(context, PHONE_B, "计词孩")
    context.kid_b, context.mini_b = kid_b, mini_b
    context.words_before = _words_total(kid_b["id"])
    _seed_progress(kid_b["id"], book_id, finished=True)
    _seed_words(kid_b["id"], book_id, 2500)
    context.words_before = _words_total(kid_b["id"])

    # 孩子 C：只听完、没通过（测验已解锁但未开始）——用来验"未开始的测验不可开始"
    kid_c, mini_c = _member_child(context, PHONE_C, "待测孩")
    context.kid_c, context.mini_c = kid_c, mini_c
    _seed_progress(kid_c["id"], book_id, finished=True)
    pre = context.client.get(
        f"/api/miniapp/quiz/{book_id}", params={"child_id": kid_c["id"]}, headers=mini_c
    )
    assert pre.status_code == 200, f"前提：下架前这门测验是能开始的（{pre.text[:160]}）"


@when("馆员下架该书")
def step_off_shelf(context):
    _off_shelf(context, context.book["id"])


@then("小程序不再展示 禁借禁约 音频停止播放")
def step_offshelf_blocking(context):
    book_id = context.book["id"]
    # ① 书城列表不再出现（PRD §4.4「家长和孩子看不到这本书了」）
    listed = context.client.get(
        "/api/miniapp/books", params={"keyword": "Dog Man"}, headers=context.mini_b
    )
    assert listed.status_code == 200, listed.text
    assert all(i["id"] != book_id for i in listed.json()["items"]), "下架书不该出现在书城"
    # ② 禁借（副本路径 + ISBN 路径都拦）
    for payload in (
        {"child_id": context.kid_b["id"], "copy_id": context.copy_id},
        {"child_id": context.kid_b["id"], "isbn": ISBN_DOGMAN},
    ):
        r = context.client.post(
            "/api/admin/circulation/borrow", json=payload, headers=_admin(context)
        )
        assert r.status_code == 422 and "下架" in r.json()["detail"], (payload, r.text[:160])
    # ③ 禁约
    r = context.client.post(
        "/api/miniapp/reservations",
        json={"child_id": context.kid_b["id"], "book_id": book_id},
        headers=context.mini_b,
    )
    assert r.status_code == 422 and "下架" in r.json()["detail"], r.text[:160]
    # ④ 音频停播（PRD §4.4「在线音频 停止播放」）：预检 false + 音频流 403
    pre = context.client.get(
        f"/api/miniapp/books/{book_id}/audio-permission",
        params={"child_id": context.kid_b["id"]},
        headers=context.mini_b,
    )
    assert pre.status_code == 200, pre.text
    assert pre.json()["allowed"] is False, f"下架后预检必须为 false：{pre.json()}"
    assert pre.json()["reason"] == "off_shelf", pre.json()
    token = context.mini_b["Authorization"].split()[1]
    stream = context.client.get(
        f"/api/miniapp/books/{book_id}/audio",
        params={"child_id": context.kid_b["id"], "token": token},
    )
    assert stream.status_code == 403, (
        f"音频流应 403，实得 {stream.status_code}：{stream.text[:200]}"
    )


@then("未开始的测验不可开始 已通过成绩保留")
def step_offshelf_quiz(context):
    book_id = context.book["id"]
    # ① 没开始的不能开始（同一个孩子下架前能开、下架后不能——前后对照）
    r = context.client.get(
        f"/api/miniapp/quiz/{book_id}",
        params={"child_id": context.kid_c["id"]},
        headers=context.mini_c,
    )
    assert r.status_code == 422, f"下架后不能开始测验，实得 {r.status_code}：{r.text[:160]}"
    assert "下架" in r.json()["detail"], r.text
    # ② 已通过的保留：词账还在 + 已通过书单仍收录
    passed = context.client.get(
        "/api/miniapp/growth/passed-books",
        params={"child_id": context.kid_b["id"]},
        headers=context.mini_b,
    )
    assert passed.status_code == 200, passed.text
    assert book_id in [i["book_id"] for i in passed.json()], passed.json()


@then("借出的书仍需归还 已计词数不回收")
def step_offshelf_keep(context):
    # ① 借出的仍要归还（下架不影响归还）
    r = context.client.post(
        "/api/admin/circulation/return",
        json={"copy_id": context.copy_id},
        headers=_admin(context),
    )
    assert r.status_code == 200, f"下架不拦归还：{r.text[:160]}"
    assert _copy_status(context.copy_id) == "available"
    # ② 已计词数一分不差（读的是成长档案的累计值）
    assert _words_total(context.kid_b["id"]) == context.words_before, "已得词数不回收"


# ---------- 场景 10：副本状态流转 ----------


@given('副本处于 "{status}"')
def step_copy_status(context, status):
    context.book = _create_book(context, title="状态流转样例", word_count=800)
    context.copy_id = _copies(context, context.book["id"])[0]["id"]
    r = context.client.put(
        f"/api/admin/copies/{context.copy_id}/status",
        json={"status": status, "reason": "建制基线状态"},
        headers=_admin(context),
    )
    assert r.status_code == 200, r.text
    assert _copy_status(context.copy_id) == status


@when('尝试将其直接置为 "{to_status}"')
def step_try_transition(context, to_status):
    context.transition = context.client.put(
        f"/api/admin/copies/{context.copy_id}/status",
        json={"status": to_status, "reason": "试探非法流转"},
        headers=_admin(context),
    )


@then("状态变更按转移矩阵校验 非法流转被拒绝")
def step_transition_rejected(context):
    r = context.transition
    assert r.status_code == 422, f"非法流转应 422，实得 {r.status_code}：{r.text[:160]}"
    # 2026-10-17（第六十七批·第四轮大审查 P1-9/P2-7）：`borrowed`/`reserved` 是**工作流状态**
    # （借阅/预约流程置位），现在由更早的一道闸拦下——提示改走借还台/预约动线，不再落到矩阵文案。
    # 判据未变：非法流转必须被拒 **且副本状态原样不动**。
    detail = r.json()["detail"]
    assert ("转移矩阵拦截" in detail) or ("不能手工修改" in detail), r.text
    assert _copy_status(context.copy_id) == "lost", "被拒后状态必须原样不动"


# ---------- 场景 11：图书找回恢复上架 ----------


@given('副本状态为 "{status}"')
def step_copy_status_lost(context, status):
    context.book = _create_book(context, title="找回样例", word_count=800)
    book_id = context.book["id"]
    force_book_on(context.client, _admin(context), book_id, "找回场景前提：先上架")
    context.copy_id = _copies(context, book_id)[0]["id"]
    r = context.client.put(
        f"/api/admin/copies/{context.copy_id}/status",
        json={"status": status, "reason": "登记遗失"},
        headers=_admin(context),
    )
    assert r.status_code == 200, r.text


# 「馆员登记找回」用 deposit_steps.py 里已注册的同名步骤（同一动作：lost → available + 必填原因），
# 步骤表全局唯一（红线 118/144）——两域共用一条实现，语义完全相同。


@then('副本恢复为 "{to_status}" 且历史记录保留')
def step_found_restored(context, to_status):
    assert _copy_status(context.copy_id) == to_status
    # 历史留痕：两次流转都在审计表里（detail 含 old/new），不是只留最后一条
    from backend.common.system_models import AuditLog

    with _db() as db:
        rows = (
            db.query(AuditLog)
            .filter(AuditLog.target_id == str(context.copy_id), AuditLog.is_deleted == 0)
            .order_by(AuditLog.id)
            .all()
        )
    transitions = [(r.detail or "") for r in rows if r.action == "copy.status"]
    assert len(transitions) >= 2, f"遗失 + 找回两条都要留痕：{transitions}"
    assert any('"new": "lost"' in d for d in transitions), transitions
    assert any('"new": "available"' in d for d in transitions), transitions
    # 两次流转都要留**原因原文**（可追责：谁因为什么把书标成遗失、又为什么恢复）
    assert all((r.reason or "").strip() for r in rows if r.action == "copy.status"), (
        "流转原因不能为空"
    )
