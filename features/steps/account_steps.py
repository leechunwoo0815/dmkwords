# features/steps/account_steps.py — FEAT-001/002 家长账号与孩子档案 步骤定义
"""2026-10-17（第七十批·BDD 解封第十三批）· **account 域 6 场景**转真实执行
（FEAT-006「隐私协议同意留痕」后端零实现，仍 `@draft`，见 account.feature 头注）。

判据与实现同源（动手前逐条亲验，红线 108/136）：
- **登录三通道**（`identity.auth`）：微信 openid 主通道（首登 `need_bind` + 绑定凭证 → 认领手机号）、
  短信兜底、开发固定码（`LOGIN_DEV_CODE`，生产置空即 fail-closed）。**家长端不开放自助注册**：
  绑定=认领馆员已建档的手机号（PRD §2.2；docs/04 S49「该手机号未注册（请到店建档）」）。
- **家长 token**（`auth._parent_token`）：`sub`=家长 id、`type=parent`、带 `gen` 撤销代数（P1-11），
  `exp = now + 30 天`。`{type: child}` 这类凭证全系统**没有**（令牌类型只有 admin/parent/bind）。
- **孩子档案**：只有管理端能建（`POST /api/admin/members/parents/{id}/children`）；
  新档 `member_status=none`，等级 A——`levels_map` 对**无成长状态行**的孩子按最低档兜底。
- **数据独立**：词数/积分以**流水**为唯一事实源（`words_ledger` / `point_ledger`），
  等级看**读完本数** `books_total // level_up_books`（默认 100 本/级），阈值变更后走
  `POST /api/admin/growth/levels/recalc` 全量重算（只升不降）。
- **当前孩子是客户端态**（`miniapp/utils/session.js` 写 `wx.setStorageSync('currentChildId')`）：
  服务端契约 = 每个 child 维度端点按 `child_id` 取数、**不串号**——本域断的就是这条。

外部边界只顶微信 `code2session`（不打真微信接口，与 `tests/unit/test_login_wechat_sms.py` 同款）；
短信走 mock 网关（真实落库哈希 + 真实校验，码从 `MockSmsGateway.get_code` 读）。
"""

from contextlib import contextmanager
from datetime import UTC, datetime, timedelta
from unittest.mock import patch

from behave import given, then, when

from backend.common.gateways.sms.mock import MockSmsGateway
from tests.unit.test_wm10_concurrency import _db, _h

PHONE = "13800000001"  # 张女士手机号（背景给定；手机号全局唯一，本域独占此号）
WECHAT_OPENID = "oAccountZhang"  # 张女士的微信 openid（绑定态造数用）
OTHER_OPENID = "oAccountOther"  # 「另一微信用户」
PARENT_TOKEN_DAYS = 30  # `auth._parent_token` 实签时长（改口径须同步 feature 头注 ①）

# 造数用 ISBN（真库唯一；一个场景一本书，与其余测试错开取号）
SEED_ISBN = "9780000000071"


# ---------- 基建 ----------


def _admin(context) -> dict:
    if getattr(context, "h", None) is None:
        context.h = _h(context.client)
    return context.h


def _parent(context) -> dict:
    """背景：系统中已存在家长账号「张女士」且已绑定手机号（馆员建档，家长端不开放自助注册）。"""
    if getattr(context, "parent_row", None) is None:
        r = context.client.post(
            "/api/admin/members/parents",
            json={"name": "张女士", "phone": PHONE},
            headers=_admin(context),
        )
        assert r.status_code == 200, r.text
        context.parent_row = r.json()
        context.children = {}
    return context.parent_row


def _mini(context) -> dict:
    """家长登录态请求头（懒加载：短信固定码通道）。

    `/api/miniapp/login` 限 3 次/60 秒（`environment.before_scenario` 已重置限流桶），
    故本文件只在需要"家长视角数据"时登一次。
    """
    if getattr(context, "mini", None) is None:
        r = context.client.post("/api/miniapp/login", json={"phone": PHONE, "code": "1234"})
        assert r.status_code == 200, r.text
        context.mini = {"Authorization": f"Bearer {r.json()['token']}"}
    return context.mini


def _decode_parent_token(token: str) -> dict:
    import jwt as pyjwt

    from backend.config import get_settings

    return pyjwt.decode(token, get_settings().SECRET_KEY, algorithms=["HS256"])


@contextmanager
def _fake_wechat(openid: str):
    """顶掉 code2session（外部边界）：同一 openid 反复签到；换 openid 即「另一个微信号」。"""

    async def _fake_session(code: str) -> dict:
        assert code, "小程序传来的 code 不应为空"
        return {"openid": openid, "session_key": "sk"}

    with patch(
        "backend.integrations.wechat.auth.WeChatAuth.code_to_session",
        staticmethod(_fake_session),
    ):
        yield


def _sms_code(context, phone: str, purpose: str = "bind") -> str:
    """真发（mock 网关）→ 真落库哈希 → 码从网关读回（同 `test_login_wechat_sms` 手法）。"""
    r = context.client.post("/api/miniapp/sms/send", json={"phone": phone, "purpose": purpose})
    assert r.status_code == 200, r.text
    code = MockSmsGateway.get_code(phone)
    assert code, "mock 网关应能读到刚生成的验证码"
    return code


def _wechat_login(context, openid: str, code: str = "wx-code") -> tuple[int, dict]:
    with _fake_wechat(openid):
        r = context.client.post("/api/miniapp/login/wechat", json={"code": code})
    return r.status_code, r.json()


def _add_child(context, name: str, birthday: str | None = None, grade: str = "") -> dict:
    """馆员为孩子建档（真实管理端端点；家长端没有建档入口）。"""
    body: dict = {"name": name, "grade": grade}
    if birthday:
        body["birthday"] = birthday
    r = context.client.post(
        f"/api/admin/members/parents/{_parent(context)['id']}/children",
        json=body,
        headers=_admin(context),
    )
    assert r.status_code == 200, r.text
    child = r.json()
    (getattr(context, "children", None) or {}).setdefault(name, child)
    return child


def _kids(context) -> dict[str, dict]:
    """家长名下孩子（本文件建的 + 服务端实取的合一份，按姓名索引）。"""
    rows = context.client.get(
        f"/api/admin/members/parents/{_parent(context)['id']}/children", headers=_admin(context)
    ).json()
    for row in rows:
        context.children.setdefault(row["name"], row)
    return context.children


def _kid(context, name: str) -> dict:
    kids = _kids(context)
    assert name in kids, f"家长名下应有孩子 {name}：实得 {list(kids)}"
    return kids[name]


def _mini_children(context) -> list[dict]:
    """小程序侧孩子列表（登录载荷与 `GET /api/miniapp/children` **同一序列化入口**）。"""
    r = context.client.get("/api/miniapp/children", headers=_mini(context))
    assert r.status_code == 200, r.text
    return r.json()["children"]


def _growth_summary(context, child_id: int) -> dict:
    r = context.client.get(f"/api/admin/children/{child_id}/growth", headers=_admin(context))
    assert r.status_code == 200, r.text
    return r.json()["summary"]


def _level_threshold(context) -> int:
    from backend.common.config_service import ConfigService

    with _db() as db:
        return int(ConfigService(db).get_value("level_up_books"))


def _deposit_order(context, child_id: int) -> int:
    r = context.client.post(
        f"/api/admin/deposits/children/{child_id}/orders", headers=_admin(context)
    )
    assert r.status_code == 200, r.text
    return int(r.json()["order_id"])


def _seed_openid(context, openid: str) -> None:
    """把「该手机号已绑过微信」这个**状态**做出来。

    为什么造数：这一型的真链路（另发一条短信码 + 绑定）在本域场景 1 已真跑；本场景要的是
    「号已被占用」的既成事实，而真实绑定一次会吃掉同号 90 秒发送间隔，再发第二条码必被限流
    拦下（限流本身另有单测），故按既有造数惯例直接写 `parents.wechat_openid`。
    """
    from backend.domain.identity.models import Parent

    with _db() as db:
        db.query(Parent).filter(Parent.id == _parent(context)["id"]).update(
            {"wechat_openid": openid}
        )
        db.commit()


def _openid_in_db(context) -> str:
    from backend.domain.identity.models import Parent

    with _db() as db:
        return db.query(Parent.wechat_openid).filter(Parent.id == _parent(context)["id"]).scalar()


def _seed_reading_history(context, child_id: int, words: int, books: int) -> None:
    """造「已读完 N 本 / 累计 M 词」的历史态。

    词数看流水、等级看本数（两处都是既有口径）；真跑阅读链（借书→完播→通过测验）见
    audio_reading / growth 两域，本域只验**各孩子数据独立**，故按既有造数惯例直接落
    流水 + 成长状态行（同 membership / withdrawal / transfer 三域的做法）。
    """
    from backend.domain.catalog.models import Book
    from backend.domain.growth.models import ChildGrowthState, WordsLedger

    with _db() as db:
        book = db.query(Book).filter(Book.isbn == SEED_ISBN).first()
        if book is None:
            book = Book(isbn=SEED_ISBN, title="独立性用例书", word_count=words)
            db.add(book)
            db.flush()
        db.add(WordsLedger(child_id=child_id, book_id=book.id, word_count=words))
        state = (
            db.query(ChildGrowthState)
            .filter(ChildGrowthState.child_id == child_id, ChildGrowthState.is_deleted == 0)
            .first()
        )
        if state is None:
            state = ChildGrowthState(child_id=child_id)
            db.add(state)
        state.words_total = words
        state.books_total = books
        db.commit()


# ---------- 背景 ----------


@given('系统中已存在家长账号 "{name}" 且已绑定手机号')
def step_parent_exists(context, name):
    parent = _parent(context)
    assert parent["name"] == name and parent["phone"] == PHONE, parent


# ---------- 1 微信登录新设备 ----------


@when("{parent}在新手机上用微信授权登录")
def step_wechat_login_new_device(context, parent):
    """新设备首登：微信主通道 → `need_bind` + 绑定凭证 → 认领手机号（短信码）→ 登录态。"""
    _parent(context)
    status, data = _wechat_login(context, WECHAT_OPENID, code="wx-new-device")
    assert status == 200, data
    assert data.get("need_bind") is True and data.get("bind_ticket"), data
    code = _sms_code(context, PHONE, purpose="bind")
    bound = context.client.post(
        "/api/miniapp/login/bind",
        json={"bind_ticket": data["bind_ticket"], "phone": PHONE, "code": code},
    )
    assert bound.status_code == 200, bound.text
    context.login = bound.json()
    context.mini = {"Authorization": f"Bearer {context.login['token']}"}
    assert _openid_in_db(context) == WECHAT_OPENID, "绑定后 openid 应落到家长档案上"


@then("登录成功返回访问令牌")
def step_login_token(context):
    token = context.login.get("token")
    assert token, context.login
    payload = _decode_parent_token(token)
    assert payload["type"] == "parent", payload
    assert payload["sub"] == str(_parent(context)["id"]), payload
    # P1-11：家长 token 必须带撤销代数（退出登录 = 代数 +1 → 旧 token 一律 401）
    assert "gen" in payload, f"家长 token 缺撤销代数：{payload}"
    # 令牌真能用（受保护端点按家长身份放行）
    r = context.client.get("/api/miniapp/children", headers=context.mini)
    assert r.status_code == 200, r.text


@then("令牌有效期为 {days:d} 天")
def step_token_ttl(context, days):
    payload = _decode_parent_token(context.login["token"])
    ttl = datetime.fromtimestamp(payload["exp"], UTC) - datetime.now(UTC)
    assert abs(ttl - timedelta(days=days)) < timedelta(minutes=5), f"实签有效期 {ttl}"


# ---------- 2 添加第一个孩子 ----------


@when('馆员为{parent}添加孩子档案 填写姓名"{name}" 生日"{birthday}" 年级"{grade}"')
def step_add_child(context, parent, name, birthday, grade):
    _parent(context)
    context.last_child = _add_child(context, name, birthday=birthday, grade=grade)


@when('馆员为{parent}再添加孩子档案 填写姓名"{name}"')
def step_add_another_child(context, parent, name):
    """同名家长再加一个孩子（第二次建档；与上一条**必须是不同句式**——同一句式的短写会
    被 behave 判成上位模式匹配（AmbiguousStep），两种写法各留一条）。"""
    _parent(context)
    context.last_child = _add_child(context, name)


@then("孩子档案创建成功")
def step_child_created(context):
    """回读管理端列表：档案确在**该家长名下**，且填写项原样落库。"""
    child = context.last_child
    rows = context.client.get(
        f"/api/admin/members/parents/{_parent(context)['id']}/children", headers=_admin(context)
    ).json()
    hit = [c for c in rows if c["id"] == child["id"]]
    assert hit, f"新建的孩子应在该家长名下：{rows}"
    assert (hit[0]["name"], hit[0]["birthday"], hit[0]["grade"]) == (
        child["name"],
        child["birthday"],
        child["grade"],
    ), hit[0]
    assert hit[0]["parent_id"] == _parent(context)["id"], hit[0]


@then('{child}的会员状态为 "{status}"')
def step_child_member_status(context, child, status):
    rows = {c["name"]: c for c in _mini_children(context)}
    assert child in rows, rows
    assert rows[child]["member_status"] == status, rows[child]


@then('{child}的等级为 "{level}" 且进度为 {progress:d}')
def step_child_level_progress(context, child, level, progress):
    """等级=成长状态档（无状态行按最低档 A）；进度=**有效词数**（流水为唯一事实源）。"""
    kid = _kid(context, child)
    rows = {c["name"]: c for c in _mini_children(context)}
    assert rows[child]["level"] == level, rows[child]
    summary = _growth_summary(context, kid["id"])
    assert summary["words_total"] == progress, summary
    assert summary["books_total"] == 0, summary


# ---------- 3 一个账号添加多个孩子 ----------


@given('{parent}名下已有孩子 "{child}"')
def step_parent_has_child(context, parent, child):
    _parent(context)
    if child not in (getattr(context, "children", None) or {}):
        _add_child(context, child)


@then("添加成功 且账号下共 {count:d} 个孩子")
def step_children_count(context, count):
    """两端同时断：家长视角孩子列表（登录载荷同源）与馆员视角列表数量一致。"""
    rows = _mini_children(context)
    assert len(rows) == count, rows
    admin_rows = _kids(context)
    assert len(admin_rows) == count, admin_rows
    assert {c["name"] for c in rows} == set(admin_rows), (rows, admin_rows)


@then("{a}与{b}的词数、等级、积分完全独立")
def step_children_independent(context, a, b):
    """给 a 造三维数据（词数流水 / 真实积分调整 / 等级重算），验 b 三项纹丝不动。"""
    ka, kb = _kid(context, a), _kid(context, b)
    assert ka["id"] != kb["id"], (ka, kb)
    _seed_reading_history(context, ka["id"], words=15000, books=_level_threshold(context))
    r = context.client.post(
        f"/api/admin/children/{ka['id']}/points/adjust",
        json={"points": 30, "reason": "独立性用例：线下奖励"},
        headers=_admin(context),
    )
    assert r.status_code == 200, r.text
    recalc = context.client.post("/api/admin/growth/levels/recalc", headers=_admin(context))
    assert recalc.status_code == 200, recalc.text

    ga, gb = _growth_summary(context, ka["id"]), _growth_summary(context, kb["id"])
    assert (ga["words_total"], ga["points_total"]) == (15000, 30), ga
    assert (gb["words_total"], gb["points_total"]) == (0, 0), gb
    # 等级看**本数**：a 满一级升 B，b 仍是初始档 A
    levels = {c["name"]: c["level"] for c in _mini_children(context)}
    assert (levels[a], levels[b]) == ("B", "A"), levels


# ---------- 4 切换当前操作的孩子 ----------


@given('{parent}名下已有两个孩子 "{a}" 和 "{b}"')
def step_parent_has_two(context, parent, a, b):
    """两个孩子的既有态（句式与单孩那条**刻意不同**：同前缀会被 behave 判成上位模式）。"""
    _parent(context)
    context.deposit_order = {}
    for name in (a, b):
        child = _add_child(context, name)
        # 各挂一张**自己名下**的押金单：跨孩子串号只有这样才验得出来
        context.deposit_order[child["id"]] = _deposit_order(context, child["id"])


@when('{parent}切换当前孩子为 "{child}"')
def step_switch_current_child(context, parent, child):
    """切换是**客户端态**（`session.js` 写 `currentChildId`，服务端不存"当前孩子"）。

    本步取到「切换栏」的数据源（孩子列表）并记下选中态；断言落在下一步——逐端点实取
    所选孩子的数据，验服务端按 `child_id` 取数、不串号。
    """
    rows = _mini_children(context)
    assert {c["name"] for c in rows} == {"小明", "小红"}, rows
    context.current = _kid(context, child)
    assert len(rows) == 2, rows


@then("小程序展示的数据均为{child}的数据")
def step_data_follows_current(context, child):
    cur = context.current
    other = next(c for c in context.children.values() if c["id"] != cur["id"])
    assert cur["name"] == child, (cur, child)

    def orders(kid: dict) -> list[dict]:
        r = context.client.get(f"/api/miniapp/orders?child_id={kid['id']}", headers=_mini(context))
        assert r.status_code == 200, r.text
        return r.json()

    mine, theirs = orders(cur), orders(other)
    assert [o["id"] for o in mine] == [context.deposit_order[cur["id"]]], mine
    assert [o["id"] for o in theirs] == [context.deposit_order[other["id"]]], theirs
    assert not ({o["id"] for o in mine} & {o["id"] for o in theirs}), "两个孩子各自的单不许串号"
    # 身份信息同源：所选孩子的 id/姓名按 child_id 取，没跟成"第一个孩子"
    assert {c["id"]: c["name"] for c in _mini_children(context)}[cur["id"]] == child


# ---------- 5 同一手机号不可重复注册 ----------


@given('手机号 "{phone}" 已绑定账号 "{name}" 且该账号已绑微信')
def step_phone_taken(context, phone, name):
    parent = _parent(context)
    assert phone == PHONE and parent["name"] == name, parent
    _seed_openid(context, WECHAT_OPENID)
    assert _openid_in_db(context) == WECHAT_OPENID


@when("另一微信用户尝试绑定同一手机号")
def step_other_wechat_binds(context):
    """另一个微信号 + 该手机号的真实短信码 → 走到绑定端点（码是真的，被拒的原因不是码）。"""
    status, data = _wechat_login(context, OTHER_OPENID, code="wx-other")
    assert status == 200, data
    assert data.get("need_bind") is True and data.get("bind_ticket"), data
    code = _sms_code(context, PHONE, purpose="bind")
    context.bind_resp = context.client.post(
        "/api/miniapp/login/bind",
        json={"bind_ticket": data["bind_ticket"], "phone": PHONE, "code": code},
    )


@then("系统拒绝并提示该手机号已绑定其他微信")
def step_phone_rebind_rejected(context):
    resp = context.bind_resp
    assert resp.status_code == 422, resp.text
    detail = resp.json()["detail"]
    assert "已绑定其他微信" in detail, detail
    # 拒绝是**真拒绝**：没发登录态，原绑定也没被顶号
    assert "token" not in resp.json(), resp.json()
    assert _openid_in_db(context) == WECHAT_OPENID, _openid_in_db(context)


# ---------- 6 孩子没有独立登录入口 ----------


@then("小程序仅支持家长微信或手机号登录")
def step_only_parent_channels(context):
    opts = context.client.get("/api/miniapp/login/options").json()
    from backend.config import get_settings

    assert opts["wechat_enabled"] is True, opts
    assert opts["sms_enabled"] == bool(get_settings().SMS_ENABLED), opts

    # 两条通道都**真签一次**：手机上短信固定码通道、微信上首登绑定通道
    phone_login = context.client.post("/api/miniapp/login", json={"phone": PHONE, "code": "1234"})
    assert phone_login.status_code == 200, phone_login.text
    status, data = _wechat_login(context, WECHAT_OPENID, code="wx-channel")
    assert status == 200 and data.get("need_bind") is True, data
    code = _sms_code(context, PHONE, purpose="bind")
    wx_login = context.client.post(
        "/api/miniapp/login/bind",
        json={"bind_ticket": data["bind_ticket"], "phone": PHONE, "code": code},
    )
    assert wx_login.status_code == 200, wx_login.text
    for payload in (phone_login.json(), wx_login.json()):
        decoded = _decode_parent_token(payload["token"])
        assert decoded["type"] == "parent", decoded

    # 登录面收敛：小程序的登录端点就是这五个（微信手机号快速验证是首登绑定的子路径，
    # 不是第五条登录通道；2026-10-29 短信退役后它取代短信成为首绑主通道）
    # 取 OpenAPI 契约面而不是 `app.routes`——本版 FastAPI 的 included router 在
    # `app.routes` 里是 `_IncludedRouter` 包装（无 path 属性），契约面才是可枚举的接口全集。
    paths = set(context.client.app.openapi()["paths"])
    login_paths = {p for p in paths if p.startswith("/api/miniapp/login")}
    assert login_paths == {
        "/api/miniapp/login",
        "/api/miniapp/login/wechat",
        "/api/miniapp/login/wechat/phone",
        "/api/miniapp/login/bind",
        "/api/miniapp/login/options",
    }, login_paths


@then("不存在孩子账号密码登录方式")
def step_no_child_login(context):
    from backend.domain.identity.models import Child

    # ① 模型层：孩子档案没有任何密码/凭证列（孩子不是账号主体，只是被管理的档案）
    cols = {c.name for c in Child.__table__.columns}
    assert not {c for c in cols if "password" in c or "passwd" in c or c == "pwd"}, cols

    # ② 路由层：全站没有任何"孩子登录"端点（契约面 = 接口全集）
    bad = [
        p
        for p in set(context.client.app.openapi()["paths"])
        if "child" in p.lower() and "login" in p.lower()
    ]
    assert bad == [], bad

    # ③ 令牌层：伪造 `type=child` 的凭证一律 401（系统只认 admin/parent/bind 三种类型）
    import jwt as pyjwt

    from backend.config import get_settings

    forged = pyjwt.encode(
        {"sub": "1", "type": "child", "exp": datetime.now(UTC) + timedelta(hours=1)},
        get_settings().SECRET_KEY,
        algorithm="HS256",
    )
    r = context.client.get("/api/miniapp/children", headers={"Authorization": f"Bearer {forged}"})
    assert r.status_code == 401, r.text
