# scripts/flow_smoke.py — **四条主链冒烟**（真实 HTTP + 真实 MySQL，一次性库/实例）
"""用法（**必须跑在一次性库 + 一次性实例上，绝不动演示库**）：

    # 1) 建一次性库 + 迁表 + 播种管理员**与业务配置**
    DB_NAME=dmkwords_flowsmoke .venv/bin/python -m alembic upgrade head
    DB_NAME=dmkwords_flowsmoke .venv/bin/python -m backend.seeds.seed_admin
    DB_NAME=dmkwords_flowsmoke .venv/bin/python -m backend.seeds.seed_configs   # 不播则建书/建单缺配置

    # 2) 起一次性实例（:8003；关调度器——链内断言看"当场落库"，不能被后台任务改写；
    #    关全局写限流——四条链写请求密集，限流另有专项用例；上传落临时目录，别污染仓库 uploads/）
    DB_NAME=dmkwords_flowsmoke SCHEDULER_ENABLED=false RATE_LIMIT_WRITE_PER_MINUTE=0 \
      UPLOADS_DIR=/tmp/dmkwords-flowsmoke-uploads \
      .venv/bin/uvicorn backend.main:app --port 8003

    # 3) 跑四条链（DB_NAME 必须一起给：脚本据此**拒绝**打到演示库）
    DB_NAME=dmkwords_flowsmoke .venv/bin/python -m scripts.flow_smoke \
      --base-url http://127.0.0.1:8003

    单跑一条：--chain money|circulation|quiz|activity

四条链：
  1 资金      建档 → 观察期费下单 → 收款确认 → 会员态 observation → 家长退款申请
              → 超管审核通过 → 执行退款（原单扫码=线下渠道）→ 退款单 refunded + 订单 refunded + 退会完成
  2 借还预约  缴观察期费 + 押金 → 建书上架 → 借书 → 续借 → 还书 → 家长预约 → 取消预约
  3 Quiz成长  上音频 → 完播（时间回拨模拟真实流逝）→ 加 3 题 → 取卷解锁 → 全对提交
              → 管理端成长页 best_score=3 / total_questions=3
  4 活动      建付费活动 → 报名+确认收款 → 签到 → 第二个家庭报名 → 门店取消整场
              （已付未签到 → 退款待审 + 退款单）→ 另建免费活动验证家长自助取消报名

**为什么必须用一次性库（而不是演示库 dmkwords）**：
  ① 四条链会**真实出款**（退款执行成功）、**真实退会**（孩子 → withdrawn），并留下已上架书目、
     已缴押金、已签到报名——跑在演示库上会把演示数据打花，且退款/退会不可逆；
  ② 链内断言依赖"这是本次运行独有的一笔观察期费/一次借阅"，演示库里同类行很多，断言会撞历史数据；
  ③ 造数（手机号/书名/活动名）都带**本次运行 tag**，重复跑不撞唯一键；DB 断言仍按本次 tag 过滤，
     所以同一个一次性库上重复跑也安全（首轮建议 drop 重建保持基线干净）。

两道安全闸（`guard_target`，与 `scripts/load_test.py` 同款）：`DB_NAME` 必须含
flowsmoke/loadtest/drill 之一；`--base-url` 端口不得是 dev.sh 的 8002/8443/5173。
"""

from __future__ import annotations

import argparse
import io
import os
import sys
import time
import urllib.parse
from datetime import datetime, timedelta
from decimal import Decimal
from typing import Any

import httpx

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

ADMIN = ("admin", "dmkwords123")
DEV_CODE = "1234"

#: 演示/开发端口黑名单（与 `scripts/load_test.py` 同一条纪律）：8002 = dev.sh 后端、
#: 8443 = dev.sh https 镜像、5173 = 管理端前端。冒烟会真出款/真退会，打到演示库不可逆。
DEMO_PORTS = {8002, 8443, 5173}
#: 允许冒烟的库名特征（与 `_scratch_db()` 同一道闸）
FLOWSMOKE_DB_MARKERS = ("flowsmoke", "loadtest", "drill")

#: 本次运行唯一 tag：手机号/书名/活动名都带它，保证脚本可重跑不撞唯一键。
RUN_TAG = time.strftime("%m%d%H%M%S") + f"{int.from_bytes(os.urandom(2), 'big') % 100:02d}"
_seq = 0


def guard_target(base: str) -> None:
    """冒烟目标自检：**先拒绝再跑**（同 `scripts/load_test.py guard_target` 两道闸）。

    ① `DB_NAME` 必须自报是一次性库；② `--base-url` 端口不得是 dev.sh 的端口——
    四条链会真出款，指到 :8002 就是把演示库的钱和会员状态改掉。
    """
    db_name = os.environ.get("DB_NAME", "")
    if not any(m in db_name for m in FLOWSMOKE_DB_MARKERS):
        raise SystemExit(
            f"拒绝冒烟：DB_NAME={db_name!r} 不是一次性库（须含 {'/'.join(FLOWSMOKE_DB_MARKERS)}）。\n"
            "  正确姿势：DB_NAME=dmkwords_flowsmoke .venv/bin/python -m scripts.flow_smoke ..."
        )
    parsed = urllib.parse.urlparse(base)
    if parsed.port in DEMO_PORTS:
        raise SystemExit(
            f"拒绝冒烟：{base} 用的是演示/开发端口 {sorted(DEMO_PORTS)}（dev.sh 的后端/管理端）"
            "——冒烟必须打在**一次性实例**上（文档示例用 :8003）。"
        )


# ---------- 基础设施 ----------


class StepFailed(RuntimeError):
    """单步失败：该链立即中止（后续步骤依赖前一步），由主流程记为"该链失败"。"""


class ChainRun:
    """一条链的运行上下文：发 HTTP、记断言、记关键 DB 行、计时。"""

    def __init__(self, title: str, base: str, admin_h: dict[str, str]) -> None:
        self.title = title
        self.base = base
        self.admin_h = admin_h
        self.steps = 0
        self.checks: list[tuple[str, bool, str]] = []
        self.facts: list[tuple[str, str]] = []
        self.t0 = time.perf_counter()
        self.abort = ""

    @property
    def seconds(self) -> float:
        return time.perf_counter() - self.t0

    @property
    def passed(self) -> bool:
        return bool(self.checks) and all(ok for _, ok, _ in self.checks) and not self.abort

    def step(self, label: str) -> None:
        self.steps += 1
        print(f"  [{self.steps:02d}] {label}")

    def check(self, label: str, ok: Any, detail: Any = "") -> bool:
        ok = bool(ok)
        note = "" if ok else str(detail)[:300].replace("\n", " ")
        self.checks.append((label, ok, note))
        print(f"       {'✓' if ok else '✗'} {label}" + ("" if ok else f"   ← {note}"))
        return ok

    def fact(self, label: str, value: Any) -> None:
        self.facts.append((label, str(value)))

    def call(self, method: str, path: str, *, expect: tuple[int, ...] = (200,), **kwargs: Any):
        """发一次真实 HTTP；非预期状态码 → 记 ✗ 后抛 `StepFailed`（链立即中止，不继续猜测）。"""
        resp = httpx.request(method, f"{self.base}{path}", timeout=60, **kwargs)
        if not self.check(
            f"HTTP {method} {path} → {resp.status_code}", resp.status_code in expect, resp.text
        ):
            raise StepFailed(
                f"第 {self.steps} 步 {method} {path} 返回 {resp.status_code}: {resp.text[:300]}"
            )
        return resp


def _scratch_db():
    """一次性库会话（**必须**是一次性库——绝不默认演示库）。所有 DB 断言只读本库。"""
    db_name = os.environ.get("DB_NAME", "")
    if not any(m in db_name for m in FLOWSMOKE_DB_MARKERS):
        raise SystemExit(
            f"拒绝连库：DB_NAME={db_name!r} 不是一次性库（须含 flowsmoke/loadtest/drill）"
        )
    from backend.database import get_session

    return get_session()


def _first(model: Any, *criteria: Any) -> Any:
    """取一行（列值读完即会话关闭：断言只读库、不写库）。"""
    db = _scratch_db()
    try:
        return db.query(model).filter(*criteria).first()
    finally:
        db.close()


def _count(model: Any, *criteria: Any) -> int:
    db = _scratch_db()
    try:
        return int(db.query(model).filter(*criteria).count())
    finally:
        db.close()


def _mutate(fn) -> None:
    """对一次性库做**必要的造数**（音频时长/心跳回拨）——链内没有对应 HTTP 入口的字段。"""
    db = _scratch_db()
    try:
        fn(db)
        db.commit()
    finally:
        db.close()


def login_admin(base: str) -> dict[str, str]:
    r = httpx.post(
        f"{base}/api/admin/login", json={"username": ADMIN[0], "password": ADMIN[1]}, timeout=30
    )
    r.raise_for_status()
    return {"Authorization": f"Bearer {r.json()['token']}"}


def parent_headers(run: ChainRun, phone: str) -> dict[str, str]:
    """家长 token：优先走**真实登录**；被 3 次/分钟限流挡下时退回进程内签发。

    为什么要有退路：`/api/miniapp/login` 带 `rate_limit(3, 60)`（按 IP），四条链 4 个家长身份
    必然在第二轮跑时撞 429。进程内签发用**与登录同一个签发函数**（同 SECRET_KEY），
    等价于"家长已登录"，业务校验（归属/会员态）一条不绕——`scripts/load_test.py` 同款先例。
    """
    r = httpx.post(
        f"{run.base}/api/miniapp/login", json={"phone": phone, "code": DEV_CODE}, timeout=30
    )
    if r.status_code == 200:
        run.check(f"家长登录 {phone}", True)
        return {"Authorization": f"Bearer {r.json()['token']}"}
    if r.status_code == 429:
        print("       · 家长登录被 3 次/分钟限流挡下 → 进程内签发同款 token（load_test 同先例）")
        run.check(f"家长登录 {phone}（限流退路）", True)
        from backend.domain.identity.auth import _parent_token
        from backend.domain.identity.models import Parent

        db = _scratch_db()
        try:
            parent = db.query(Parent).filter(Parent.phone == phone).first()
            if parent is None:
                raise StepFailed(f"家长 {phone} 不存在，无法签发 token")
            return {"Authorization": f"Bearer {_parent_token(parent)}"}
        finally:
            db.close()
    raise StepFailed(f"家长登录 {phone} 失败 {r.status_code}: {r.text[:200]}")


# ---------- 造数助手（各链共用） ----------


def make_family(run: ChainRun, child_name: str) -> tuple[int, int, str]:
    """建家长 + 孩子（手机号带运行 tag，保证可重跑）。返回 (parent_id, child_id, phone)。"""
    global _seq
    _seq += 1
    phone = f"139{RUN_TAG[-8:]}{_seq:02d}"
    p = run.call(
        "POST",
        "/api/admin/members/parents",
        json={"name": f"冒烟家长{RUN_TAG}", "phone": phone},
        headers=run.admin_h,
    )
    pid = p.json()["id"]
    c = run.call(
        "POST",
        f"/api/admin/members/parents/{pid}/children",
        json={"name": child_name},
        headers=run.admin_h,
    )
    cid = c.json()["id"]
    run.fact("parent", f"id={pid} phone={phone}")
    run.fact("child", f"id={cid} name={child_name}")
    return pid, cid, phone


def pay_observation(run: ChainRun, cid: int) -> int:
    """缴观察期费并确认收款（扫码 = 线下），返回订单 id。"""
    order = run.call(
        "POST",
        "/api/admin/orders",
        json={"child_id": cid, "order_type": "observation_fee"},
        headers=run.admin_h,
    ).json()
    run.call(
        "POST",
        f"/api/admin/orders/{order['id']}/confirm-payment",
        json={"pay_method": "scan"},
        headers=run.admin_h,
    )
    return int(order["id"])


def make_book(run: ChainRun, title: str, copies: int = 1) -> tuple[int, list[int]]:
    """建书并**强制上架**：临时关掉上架完整性开关 → toggle → 恢复（与 load_test.make_book 同款）。"""
    book = run.call(
        "POST",
        "/api/admin/books",
        json={"isbn": None, "title": title, "word_count": 1000, "copy_count": copies},
        headers=run.admin_h,
    ).json()
    bid = int(book["id"])
    run.call(
        "PUT",
        "/api/admin/configs/book_onboarding_check",
        json={"value": "false", "reason": "冒烟上架"},
        headers=run.admin_h,
    )
    try:
        run.call("POST", f"/api/admin/books/{bid}/toggle-status", headers=run.admin_h)
    finally:
        run.call(
            "PUT",
            "/api/admin/configs/book_onboarding_check",
            json={"value": "true", "reason": "恢复默认"},
            headers=run.admin_h,
        )
    rows = run.call("GET", f"/api/admin/books/{bid}/copies", headers=run.admin_h).json()
    return bid, [int(c["id"]) for c in rows]


# ---------- 链 1：资金 ----------


def chain_money(run: ChainRun) -> None:
    """家长建档 → 观察期费下单 → 收款确认 → 退款申请 → 审核 → 执行退款。"""
    from backend.domain.identity.models import Child, Order, RefundRequest, WithdrawalRequest

    run.step("建家长 + 孩子")
    _pid, cid, phone = make_family(run, f"冒烟资金孩{RUN_TAG}")

    run.step("下观察期费订单（待支付）")
    order = run.call(
        "POST",
        "/api/admin/orders",
        json={"child_id": cid, "order_type": "observation_fee"},
        headers=run.admin_h,
    ).json()
    oid = int(order["id"])
    run.check(
        "订单待支付且金额 > 0",
        order["status"] == Order.STATUS_PENDING_PAYMENT and Decimal(str(order["amount"])) > 0,
        order,
    )

    run.step("管理端确认收款（pay_method=scan）")
    run.call(
        "POST",
        f"/api/admin/orders/{oid}/confirm-payment",
        json={"pay_method": "scan"},
        headers=run.admin_h,
    )
    row = _first(Order, Order.id == oid)
    kid = _first(Child, Child.id == cid)
    run.fact("order", f"id={oid} no={row.order_no} status={row.status}")
    run.fact("child", f"id={cid} member_status={kid.member_status} expire={kid.member_expire}")
    run.check("订单落 paid", row.status == Order.STATUS_PAID, row.status)
    run.check(
        "孩子会员状态变 observation",
        kid.member_status == Child.MEMBER_OBSERVATION,
        kid.member_status,
    )

    run.step("家长端发起退款申请")
    mini = parent_headers(run, phone)
    req = run.call(
        "POST",
        "/api/miniapp/refund-requests",
        json={"child_id": cid, "order_id": oid, "reason": f"冒烟退款{RUN_TAG}"},
        headers=mini,
    ).json()
    rid = int(req["id"])
    rr = _first(RefundRequest, RefundRequest.id == rid)
    kid = _first(Child, Child.id == cid)
    run.fact("refund_request", f"id={rid} status={rr.status} amount={rr.amount}")
    run.fact("child", f"id={cid} operation_locked={kid.operation_locked}")
    run.check("退款单落 pending", rr.status == RefundRequest.STATUS_PENDING, rr.status)
    run.check(
        "订单 refund_status 同步 pending",
        _first(Order, Order.id == oid).refund_status == Order.REFUND_STATUS_PENDING,
        _first(Order, Order.id == oid).refund_status,
    )
    run.check("会员费退款联动退会 → 孩子被冻结", kid.operation_locked == 1, kid.operation_locked)

    run.step("管理端审核通过")
    run.call(
        "POST",
        f"/api/admin/refund-requests/{rid}/review",
        json={"approve": True, "remark": "冒烟审核通过"},
        headers=run.admin_h,
    )
    rr = _first(RefundRequest, RefundRequest.id == rid)
    run.check("退款单 approved", rr.status == RefundRequest.STATUS_APPROVED, rr.status)
    run.check(
        "订单 refund_status=approved",
        _first(Order, Order.id == oid).refund_status == Order.REFUND_STATUS_APPROVED,
        _first(Order, Order.id == oid).refund_status,
    )

    run.step("管理端执行退款（原单扫码 → 线下打款登记）")
    done = run.call(
        "POST",
        f"/api/admin/refund-requests/{rid}/execute",
        json={"success": True, "remark": "冒烟线下打款"},
        headers=run.admin_h,
    ).json()
    run.check("渠道落在 offline（扫码单不走微信原路退）", done.get("channel") == "offline", done)

    rr = _first(RefundRequest, RefundRequest.id == rid)
    row = _first(Order, Order.id == oid)
    kid = _first(Child, Child.id == cid)
    wd = _first(WithdrawalRequest, WithdrawalRequest.child_id == cid)
    run.fact("refund_request", f"id={rid} status={rr.status}")
    run.fact("order", f"id={oid} status={row.status} refund_status={row.refund_status}")
    run.fact("child", f"id={cid} member_status={kid.member_status} locked={kid.operation_locked}")
    run.fact("withdrawal", f"id={wd.id if wd else None} status={wd.status if wd else None}")
    run.check("退款单 refunded", rr.status == RefundRequest.STATUS_REFUNDED, rr.status)
    run.check("订单 status 随退款更新为 refunded", row.status == Order.STATUS_REFUNDED, row.status)
    run.check(
        "订单 refund_status=refunded",
        row.refund_status == Order.REFUND_STATUS_REFUNDED,
        row.refund_status,
    )
    run.check(
        "孩子会员状态 withdrawn（联动退会）",
        kid.member_status == Child.MEMBER_WITHDRAWN,
        kid.member_status,
    )
    run.check(
        "退会单 completed 且已解锁",
        wd is not None and wd.status == "completed" and kid.operation_locked == 0,
        wd,
    )


# ---------- 链 2：借还预约 ----------


def chain_circulation(run: ChainRun) -> None:
    """缴观察期费 + 押金 → 建书上架 → 借 → 续 → 还 → 预约 → 取消预约。"""
    from backend.domain.billing.models import Deposit
    from backend.domain.catalog.models import BookCopy
    from backend.domain.circulation.models import BorrowRecord
    from backend.domain.identity.models import Child
    from backend.domain.reading.models import Reservation

    run.step("建家长 + 孩子")
    _pid, cid, phone = make_family(run, f"冒烟借还孩{RUN_TAG}")

    run.step("缴观察期费（入会前置）")
    pay_observation(run, cid)
    kid = _first(Child, Child.id == cid)
    run.fact("child", f"id={cid} member_status={kid.member_status}")
    run.check(
        "孩子已入会（observation）",
        kid.member_status == Child.MEMBER_OBSERVATION,
        kid.member_status,
    )

    run.step("缴押金（建押金单 → 确认收款）")
    dep_order = run.call(
        "POST", f"/api/admin/deposits/children/{cid}/orders", headers=run.admin_h
    ).json()
    dep_oid = int(dep_order["order_id"])
    run.call(
        "POST",
        f"/api/admin/orders/{dep_oid}/confirm-payment",
        json={"pay_method": "scan"},
        headers=run.admin_h,
    )
    dep = _first(Deposit, Deposit.child_id == cid)
    run.fact(
        "deposit",
        f"id={dep.id} status={dep.status} amount={dep.amount} available={dep.available_amount}",
    )
    run.check(
        "押金已缴（paid，可用额=押金额）",
        dep.status == Deposit.STATUS_PAID and dep.available_amount == dep.amount,
        dep.status,
    )

    run.step("建书并上架（1 册）")
    bid, copies = make_book(run, f"冒烟借还书{RUN_TAG}", copies=1)
    copy_id = copies[0]
    run.fact("book", f"id={bid} copy_id={copy_id}")
    run.check(
        "书目在馆可借", _first(BookCopy, BookCopy.id == copy_id).status == BookCopy.STATUS_AVAILABLE
    )

    run.step("借书")
    rec = run.call(
        "POST",
        "/api/admin/circulation/borrow",
        json={"child_id": cid, "copy_id": copy_id},
        headers=run.admin_h,
    ).json()
    rid = int(rec["id"])
    br = _first(BorrowRecord, BorrowRecord.id == rid)
    due_before = br.due_at
    run.fact("borrow", f"id={rid} status={br.status} due_at={due_before}")
    run.check("借阅记录 active", br.status == BorrowRecord.STATUS_ACTIVE, br.status)
    run.check(
        "副本状态 borrowed",
        _first(BookCopy, BookCopy.id == copy_id).status == BookCopy.STATUS_BORROWED,
    )

    run.step("续借")
    run.call(
        "POST",
        "/api/admin/circulation/renew",
        json={"record_id": rid},
        headers=run.admin_h,
    )
    br = _first(BorrowRecord, BorrowRecord.id == rid)
    run.fact("borrow", f"id={rid} status={br.status} renew_used={br.renew_used} due_at={br.due_at}")
    run.check("续借次数 +1", br.renew_used == 1, br.renew_used)
    run.check("到期日顺延", br.due_at > due_before, f"{due_before} → {br.due_at}")

    run.step("还书")
    run.call(
        "POST",
        "/api/admin/circulation/return",
        json={"copy_id": copy_id, "condition": "normal"},
        headers=run.admin_h,
    )
    br = _first(BorrowRecord, BorrowRecord.id == rid)
    run.fact("borrow", f"id={rid} status={br.status} returned_at={br.returned_at}")
    run.check("借阅记录 returned", br.status == BorrowRecord.STATUS_RETURNED, br.status)
    run.check(
        "副本回到 available",
        _first(BookCopy, BookCopy.id == copy_id).status == BookCopy.STATUS_AVAILABLE,
    )

    run.step("家长端预约该书")
    mini = parent_headers(run, phone)
    res = run.call(
        "POST",
        "/api/miniapp/reservations",
        json={"child_id": cid, "book_id": bid},
        headers=mini,
    ).json()
    res_id = int(res["id"])
    row = _first(Reservation, Reservation.id == res_id)
    run.fact("reservation", f"id={res_id} status={row.status} expires_at={row.expires_at}")
    run.check("预约 active", row.status == Reservation.STATUS_ACTIVE, row.status)
    run.check(
        "副本被预约锁定",
        _first(BookCopy, BookCopy.id == copy_id).status == BookCopy.STATUS_RESERVED,
    )

    run.step("家长取消预约")
    run.call(
        "POST",
        f"/api/miniapp/reservations/{res_id}/cancel",
        json={"child_id": cid},
        headers=mini,
    )
    row = _first(Reservation, Reservation.id == res_id)
    run.fact("reservation", f"id={res_id} status={row.status}")
    run.check("预约 cancelled", row.status == Reservation.STATUS_CANCELLED, row.status)
    run.check(
        "副本释放回 available",
        _first(BookCopy, BookCopy.id == copy_id).status == BookCopy.STATUS_AVAILABLE,
    )


# ---------- 链 3：Quiz 成长 ----------


def chain_quiz(run: ChainRun) -> None:
    """上音频 → 完播 → 加 3 题 → 取卷解锁 → 全对提交 → 管理端成长页分数与分母。"""
    from backend.domain.catalog.models import Book
    from backend.domain.growth.models import QuizAttempt
    from backend.domain.reading.models import ReadingProgress

    run.step("建家长 + 孩子 + 入会")
    _pid, cid, phone = make_family(run, f"冒烟测验孩{RUN_TAG}")
    pay_observation(run, cid)

    run.step("建书上架")
    bid, _copies = make_book(run, f"冒烟测验书{RUN_TAG}")

    run.step("给书上音频（multipart，4 字节 MP3 头 + 静音填充）")
    mp3 = b"\xff\xfb\x90\x00" + b"\x00" * 125000
    run.call(
        "POST",
        f"/api/admin/books/{bid}/audio",
        files={"file": ("smoke.mp3", io.BytesIO(mp3), "audio/mpeg")},
        headers=run.admin_h,
    )
    book = _first(Book, Book.id == bid)
    run.check("音频已落库（audio_path 非空）", bool(book.audio_path), book.audio_path)

    run.step("把音频时长钉成 600 秒（上传解析出的时长不可控，链内要确定分母）")
    _mutate(
        lambda db: setattr(
            db.query(Book).filter(Book.id == bid).first(), "audio_duration_seconds", 600
        )
    )
    run.check(
        "audio_duration_seconds=600",
        _first(Book, Book.id == bid).audio_duration_seconds == 600,
        _first(Book, Book.id == bid).audio_duration_seconds,
    )

    run.step("第一次心跳（position=10）")
    mini = parent_headers(run, phone)
    run.call(
        "POST",
        "/api/miniapp/reading/progress",
        json={"child_id": cid, "book_id": bid, "position": 10, "session_start": 0},
        headers=mini,
    )

    run.step("把 last_report_at 回拨 590 秒（模拟真实听完，防「瞬间听满」被判异常）")

    def backdate(db) -> None:
        prog = (
            db.query(ReadingProgress)
            .filter(ReadingProgress.child_id == cid, ReadingProgress.book_id == bid)
            .first()
        )
        prog.last_report_at = datetime.now() - timedelta(seconds=590)

    _mutate(backdate)

    run.step("第二次心跳（position=600，区间 [10,600] 拼满全片）")
    run.call(
        "POST",
        "/api/miniapp/reading/progress",
        json={"child_id": cid, "book_id": bid, "position": 600, "session_start": 10},
        headers=mini,
    )
    prog = _first(ReadingProgress, ReadingProgress.child_id == cid, ReadingProgress.book_id == bid)
    run.fact("reading_progress", f"finished={prog.finished} coverage={prog.coverage}")
    run.check("完播（finished=1 → 测验解锁前置）", prog.finished == 1, prog.finished)

    run.step("加 3 道题（答案全 A）")
    for i in range(3):
        run.call(
            "POST",
            f"/api/admin/books/{bid}/questions",
            json={
                "question_type": "single",
                "question_text": f"Q{i + 1}（{RUN_TAG}）?",
                "options": ["A", "B", "C", "D"],
                "answer": "A",
            },
            headers=run.admin_h,
        )

    run.step("取卷：应 unlocked=true 且 3 题")
    quiz = run.call(
        "GET",
        f"/api/miniapp/quiz/{bid}",
        params={"child_id": cid},
        headers=mini,
    ).json()
    run.check("unlocked=true", quiz["unlocked"] is True, quiz.get("status"))
    run.check(
        "卷面 3 题（题库 3 < 配置题量 5）", len(quiz["questions"]) == 3, len(quiz["questions"])
    )

    run.step("提交答卷（全对）")
    sub = run.call(
        "POST",
        f"/api/miniapp/quiz/{bid}/submit",
        json={"child_id": cid, "answers": ["A", "A", "A"]},
        headers=mini,
    ).json()
    run.fact("quiz_attempt", f"score={sub['score']} total={sub['total']} passed={sub['passed']}")
    run.check("得分 3/3", sub["score"] == 3 and sub["total"] == 3, sub)
    attempt = _first(QuizAttempt, QuizAttempt.child_id == cid, QuizAttempt.book_id == bid)
    run.check("作答快照题量=3（分母来源）", attempt.total_questions == 3, attempt.total_questions)

    run.step("管理端成长页核对最高分与分母")
    growth = run.call("GET", f"/api/admin/children/{cid}/growth", headers=run.admin_h).json()
    row = next((r for r in growth["quiz_overview"] if r["book_id"] == bid), None)
    run.fact(
        "growth.quiz_overview",
        f"book_id={bid} best_score={row and row['best_score']} total_questions={row and row['total_questions']}",
    )
    run.check("成长页 best_score=3", row is not None and row["best_score"] == 3, row)
    run.check(
        "成长页 total_questions=3",
        row is not None and row["total_questions"] == 3,
        row,
    )


# ---------- 链 4：活动 ----------


def chain_activity(run: ChainRun) -> None:
    """建付费活动 → 报名+收款 → 签到 → 第二家报名 → 门店取消整场（退款待审+退款单）；免费活动自助取消。"""
    from backend.domain.activity.models import Activity, ActivityEnrollment
    from backend.domain.identity.models import Order, RefundRequest

    run.step("建两个家庭并各自入会（活动 member_only 默认 true）")
    _p1, cid1, phone1 = make_family(run, f"冒烟活动孩甲{RUN_TAG}")
    pay_observation(run, cid1)
    _p2, cid2, phone2 = make_family(run, f"冒烟活动孩乙{RUN_TAG}")
    pay_observation(run, cid2)

    run.step("管理端建付费活动（30 分钟后开始：报名窗口开着、签到窗口也开着）")
    start_at = (datetime.now() + timedelta(minutes=30)).replace(microsecond=0)
    act = run.call(
        "POST",
        "/api/admin/activities",
        json={
            "title": f"冒烟付费活动{RUN_TAG}",
            "start_at": start_at.isoformat(),
            "end_at": (start_at + timedelta(hours=3)).isoformat(),
            "location": "冒烟馆",
            "max_quota": 3,
            "fee": "99.00",
        },
        headers=run.admin_h,
    ).json()
    aid = int(act["id"])
    run.fact("activity", f"id={aid} status={act['status']} start_at={start_at}")
    run.check("活动已发布", act["status"] == Activity.STATUS_PUBLISHED, act["status"])

    run.step("孩子甲报名（付费 → 待支付 + 建单）")
    e1 = run.call(
        "POST",
        f"/api/miniapp/activities/{aid}/enroll",
        json={"child_id": cid1},
        headers=parent_headers(run, phone1),
    ).json()
    eid1 = int(e1["enrollment"]["id"])
    ticket1 = e1["enrollment"]["ticket_code"]
    order1 = int(e1["order_id"])
    run.fact("enrollment", f"id={eid1} status={e1['enrollment']['status']} ticket={ticket1}")
    run.check(
        "报名待支付", e1["enrollment"]["status"] == ActivityEnrollment.STATUS_PENDING_PAYMENT, e1
    )

    run.step("确认收款 → 报名转正")
    run.call(
        "POST",
        f"/api/admin/orders/{order1}/confirm-payment",
        json={"pay_method": "scan"},
        headers=run.admin_h,
    )
    enr1 = _first(ActivityEnrollment, ActivityEnrollment.id == eid1)
    run.fact("enrollment", f"id={eid1} status={enr1.status}")
    run.check("报名 enrolled", enr1.status == ActivityEnrollment.STATUS_ENROLLED, enr1.status)
    run.check("活动订单 paid", _first(Order, Order.id == order1).status == Order.STATUS_PAID)

    run.step("馆员扫券签到（活动开始前 60 分钟窗口内）")
    run.call(
        "POST",
        "/api/admin/activity-signin",
        json={"ticket_code": ticket1},
        headers=run.admin_h,
    )
    enr1 = _first(ActivityEnrollment, ActivityEnrollment.id == eid1)
    run.fact("enrollment", f"id={eid1} status={enr1.status} checked_in_at={enr1.checked_in_at}")
    run.check("报名 checked_in", enr1.status == ActivityEnrollment.STATUS_CHECKED_IN, enr1.status)

    run.step("孩子乙报名并确认收款（用于验证取消整场的退款联动）")
    e2 = run.call(
        "POST",
        f"/api/miniapp/activities/{aid}/enroll",
        json={"child_id": cid2},
        headers=parent_headers(run, phone2),
    ).json()
    eid2 = int(e2["enrollment"]["id"])
    order2 = int(e2["order_id"])
    run.call(
        "POST",
        f"/api/admin/orders/{order2}/confirm-payment",
        json={"pay_method": "scan"},
        headers=run.admin_h,
    )
    run.check(
        "孩子乙报名 enrolled",
        _first(ActivityEnrollment, ActivityEnrollment.id == eid2).status
        == ActivityEnrollment.STATUS_ENROLLED,
    )

    run.step("门店取消整场活动")
    cancel = run.call("POST", f"/api/admin/activities/{aid}/cancel", headers=run.admin_h).json()
    run.fact("cancel_result", cancel)
    run.check("已付未签到 1 笔转退款待审", cancel.get("refund_pending") == 1, cancel)

    act_row = _first(Activity, Activity.id == aid)
    enr1 = _first(ActivityEnrollment, ActivityEnrollment.id == eid1)
    enr2 = _first(ActivityEnrollment, ActivityEnrollment.id == eid2)
    o2 = _first(Order, Order.id == order2)
    rr2 = _first(RefundRequest, RefundRequest.order_id == order2, RefundRequest.is_deleted == 0)
    run.fact("activity", f"id={aid} status={act_row.status}")
    run.fact("enrollment", f"甲 id={eid1} status={enr1.status}")
    run.fact("enrollment", f"乙 id={eid2} status={enr2.status}")
    run.fact("order", f"甲 id={order1} status={_first(Order, Order.id == order1).status}")
    run.fact("order", f"乙 id={order2} status={o2.status} refund_status={o2.refund_status}")
    run.fact(
        "refund_request",
        f"id={rr2.id if rr2 else None} status={rr2.status if rr2 else None} amount={rr2.amount if rr2 else None}",
    )
    run.check("活动落库为 cancelled", act_row.status == Activity.STATUS_CANCELLED, act_row.status)
    run.check(
        "已签到报名不受影响（checked_in）",
        enr1.status == ActivityEnrollment.STATUS_CHECKED_IN,
        enr1.status,
    )
    run.check(
        "已付未签到报名转 refund_pending",
        enr2.status == ActivityEnrollment.STATUS_REFUND_PENDING,
        enr2.status,
    )
    run.check(
        "报名订单 refund_status=pending",
        o2.refund_status == Order.REFUND_STATUS_PENDING,
        o2.refund_status,
    )
    run.check(
        "退款单已生成（pending，金额=活动费）",
        rr2 is not None and rr2.status == RefundRequest.STATUS_PENDING,
        rr2,
    )
    run.check(
        "已签到者的订单不生成退款单",
        _count(RefundRequest, RefundRequest.order_id == order1, RefundRequest.is_deleted == 0) == 0,
    )

    run.step("另建免费活动（验证家长自助取消报名）")
    free = run.call(
        "POST",
        "/api/admin/activities",
        json={
            "title": f"冒烟免费活动{RUN_TAG}",
            "start_at": (datetime.now() + timedelta(days=7)).replace(microsecond=0).isoformat(),
            "max_quota": 2,
            "fee": "0",
        },
        headers=run.admin_h,
    ).json()
    faid = int(free["id"])
    fe = run.call(
        "POST",
        f"/api/miniapp/activities/{faid}/enroll",
        json={"child_id": cid2},
        headers=parent_headers(run, phone2),
    ).json()
    feid = int(fe["enrollment"]["id"])
    run.fact(
        "enrollment",
        f"免费 id={feid} status={fe['enrollment']['status']} order_id={fe['order_id']}",
    )
    run.check(
        "免费报名直接 enrolled 且不建订单",
        fe["enrollment"]["status"] == ActivityEnrollment.STATUS_ENROLLED and fe["order_id"] is None,
        fe,
    )

    run.step("家长自助取消报名")
    run.call(
        "POST",
        f"/api/miniapp/enrollments/{feid}/cancel",
        json={"child_id": cid2},
        headers=parent_headers(run, phone2),
    )
    frow = _first(ActivityEnrollment, ActivityEnrollment.id == feid)
    run.fact(
        "enrollment", f"免费 id={feid} status={frow.status} cancel_reason={frow.cancel_reason}"
    )
    run.check("报名 cancelled", frow.status == ActivityEnrollment.STATUS_CANCELLED, frow.status)


# ---------- main ----------

CHAINS: list[tuple[str, str, Any]] = [
    ("money", "资金链（下单→收款→退款申请→审核→执行）", chain_money),
    ("circulation", "借还预约链（借→续→还→约→取消）", chain_circulation),
    ("quiz", "Quiz 成长链（完播→解锁→答题→成长页）", chain_quiz),
    ("activity", "活动链（报名→签到→取消整场→自助取消）", chain_activity),
]


def main() -> int:
    ap = argparse.ArgumentParser(description="四条主链冒烟（一次性库/实例）")
    ap.add_argument("--base-url", default="http://127.0.0.1:8003")
    ap.add_argument("--chain", default="all", choices=["all"] + [k for k, _, _ in CHAINS])
    args = ap.parse_args()
    base = args.base_url.rstrip("/")

    guard_target(base)  # 先拒绝演示库/演示端口，再开始任何写动作
    admin_h = login_admin(base)

    print(f"[flow] base={base} DB_NAME={os.environ.get('DB_NAME')} run_tag={RUN_TAG}")
    selected = CHAINS if args.chain == "all" else [c for c in CHAINS if c[0] == args.chain]

    results: list[tuple[str, ChainRun]] = []
    for _key, title, fn in selected:
        print(f"\n══ 链：{title}")
        run = ChainRun(title, base, admin_h)
        try:
            fn(run)
        except StepFailed as exc:
            run.abort = str(exc)
            print(f"       ✗ 中止：{exc}")
        except Exception as exc:  # noqa: BLE001 —— 单链异常不该吞，但要继续跑其余链并如实报
            run.abort = f"{type(exc).__name__}: {exc}"
            print(f"       ✗ 异常中止：{run.abort}")
        print(
            f"\n  ── {title} · {'✓ 通过' if run.passed else '✗ 失败'}"
            f" · 步骤 {run.steps} · 断言 {len(run.checks)} · 耗时 {run.seconds:.2f}s"
        )
        for label, value in run.facts:
            print(f"     DB {label}: {value}")
        results.append((title, run))

    print("\n══ 汇总")
    print(f"{'链':<34}{'结果':<6}{'步骤':>4}{'断言':>6}{'通过':>6}{'耗时':>9}")
    for title, run in results:
        passed = sum(1 for _, ok, _ in run.checks if ok)
        print(
            f"{title:<34}{'✓' if run.passed else '✗':<6}{run.steps:>4}"
            f"{len(run.checks):>6}{passed:>6}{run.seconds:>8.2f}s"
        )
    failed = [title for title, run in results if not run.passed]
    print(f"\n[flow] {len(results) - len(failed)}/{len(results)} 条链通过")
    for title, run in results:
        if run.passed:
            continue
        print(f"  · ✗ {title}")
        for label, ok, note in run.checks:
            if not ok:
                print(f"      - ✗ {label}" + (f" ← {note}" if note else ""))
        if run.abort:
            print(f"      - 中止于：{run.abort}")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
