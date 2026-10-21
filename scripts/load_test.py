# scripts/load_test.py — **容量压测 5 组**（真实 HTTP + 真实 MySQL）
"""用法（**必须跑在一次性实例上，绝不动演示库**）：

    # 1) 建独立压测库 + 迁表 + 播种管理员
    DB_NAME=dmkwords_loadtest .venv/bin/python -m alembic upgrade head
    DB_NAME=dmkwords_loadtest .venv/bin/python -m backend.seeds.seed_admin

    # 2) 起一次性实例（**关限流**：压测量的是容量，限流另有 9 条用例 + BDD 守护）
    DB_NAME=dmkwords_loadtest SCHEDULER_ENABLED=false RATE_LIMIT_WRITE_PER_MINUTE=0 \
      .venv/bin/uvicorn backend.main:app --port 8003

    # 3) 压测（DB_NAME 必须一起给：脚本据此**拒绝**打到演示库）
    DB_NAME=dmkwords_loadtest .venv/bin/python -m scripts.load_test \
      --base-url http://127.0.0.1:8003 --json /tmp/load.json

五组（对齐 `docs/审查任务书-第七轮…` W6）：
  G1 借还并发    —— ①同一副本被 10 个孩子并发借（**恰好 1 成功**）
                    ②同一孩子并发借 10 本、额度只剩 5（**恰好 5 成功，不超卖**）
  G2 报名并发    —— 名额 5、20 个家庭并发报名（**恰好 5 成功**）
  G3 支付回调风暴 —— 同一订单 20 次并发回调（**恰好入账 1 次**，幂等）
  G4 批量导入    —— 1000 行 xlsx 单请求（耗时 + 后端 RSS 增量 + **回库核对**）
  G5 定时任务    —— 造量（书目/孩子/借阅/订单/退款/词账）后**全部任务**逐个计时

**P2-43（2026-10-20 第八轮大审查）补强**——原先它"不能支撑『容量已验』"：
  · G5 只跑 9/21 个任务 → 改为**从 `tasks.registry` 枚举全部任务**（新增任务自动纳入）；
  · 造量只有书目/孩子/借阅 → 补**订单 / 退款台账 / 词账**（对账、退款审核、榜单聚合
    这些"随流水线性增长"的任务原先根本压不到）；
  · 没有阈值 → 每个任务加**耗时上限判定**（超限即 FAIL），报告末尾给结构化汇总；
  · G4 只看接口自报的 `success_count` → 补**回库核对**（真查一遍导入落库行数）；
  · 不可重跑 → 造量按**每次运行唯一 tag** 生成手机号/副本码/书名，第二次跑不再撞唯一键；
  · `--base-url` 不校验 → `guard_target()` 先拒绝：库名须自报压测/演练库、端口不得是
    dev.sh 的端口（原先指到 :8002 就会把演示库灌成 2000 书目 / 6000 借阅）。

输出：每组 `请求数 / 成功 / 失败 / p50 / p95 / p99 / max` + `判定`（PASS/FAIL）；`--json` 落盘。
退出码：任一 `判定=FAIL` 即非 0（可进 CI）。
"""

from __future__ import annotations

import argparse
import json
import os
import statistics
import subprocess
import sys
import threading
import time
import urllib.parse
from concurrent.futures import ThreadPoolExecutor
from decimal import Decimal

import httpx

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

ADMIN = ("admin", "dmkwords123")
DEV_CODE = "1234"
CONC = 20  # 并发度

#: 压测目标端口黑名单（P2-43）：8002 = dev.sh 后端、8443 = dev.sh https、5173 = 管理端。
DEMO_PORTS = {8002, 8443, 5173}
#: 允许压测的库名特征（与 `_scratch_db` / `_seed_volume` 同一条纪律）
LOADTEST_DB_MARKERS = ("loadtest", "drill")
#: 单个定时任务的耗时上限（秒）——超限即 FAIL（"容量已验"的判据，P2-43）。
#: 依据：调度器最小间隔 300 秒（`overdue_mark`），任务必须**远快于**间隔，
#: 否则下一轮触发会被 `max_instances=1` 跳过（任务实际停摆）。
TASK_DURATION_LIMIT_S = 60.0


def guard_target(base: str) -> None:
    """压测目标自检（P2-43）：**先拒绝再跑**，别把演示库灌成压测数据。

    两道闸：① 库名必须自报是压测/演练库（`DB_NAME` 由调用方给出）；
    ② 端口不得是 dev.sh 的端口（`--base-url` 指到 :8002 就是演示后端）。
    """
    db_name = os.environ.get("DB_NAME", "")
    if not any(m in db_name for m in LOADTEST_DB_MARKERS):
        raise SystemExit(
            f"拒绝压测：DB_NAME={db_name!r} 不是压测/演练库（须含 {'/'.join(LOADTEST_DB_MARKERS)}）。\n"
            "  正确姿势：DB_NAME=dmkwords_loadtest .venv/bin/python -m scripts.load_test ..."
        )
    parsed = urllib.parse.urlparse(base)
    if parsed.port in DEMO_PORTS:
        raise SystemExit(
            f"拒绝压测：{base} 用的是演示/开发端口 {sorted(DEMO_PORTS)}"
            "（dev.sh 的后端/管理端）——压测必须打在**一次性实例**上（文档示例用 :8003）。"
        )


# ---------- 基础设施 ----------


def login_admin(base: str) -> dict:
    r = httpx.post(
        f"{base}/api/admin/login", json={"username": ADMIN[0], "password": ADMIN[1]}, timeout=30
    )
    r.raise_for_status()
    return {"Authorization": f"Bearer {r.json()['token']}"}


def login_parent(base: str, phone: str) -> dict:
    r = httpx.post(f"{base}/api/miniapp/login", json={"phone": phone, "code": DEV_CODE}, timeout=30)
    r.raise_for_status()
    return {"Authorization": f"Bearer {r.json()['token']}"}


def parent_token(parent_id: int) -> dict:
    """**进程内签**家长 token（不走 `/api/miniapp/login`）。

    为什么：登录端点自带 `rate_limit(3, 60)`（按 IP，3 次/分钟）——压测要 20 个家长身份，
    走登录必被自己的防刷闸挡住（首轮实测 429）。这里用与登录**同一个签发函数**
    （同一 SECRET_KEY），等价于"20 个家长已登录"，且**不绕过任何业务校验**
    （归属校验仍按 token 里的 parent_id 走，越权该拒还是拒）。
    """
    from backend.domain.identity.auth import _parent_token
    from backend.domain.identity.models import Parent

    db = _scratch_db()
    try:
        parent = db.query(Parent).filter(Parent.id == parent_id).first()
        if parent is None:
            raise SystemExit(f"家长 {parent_id} 不存在（先建家长再签 token）")
        return {"Authorization": f"Bearer {_parent_token(parent)}"}
    finally:
        db.close()


def make_family(
    base: str, h: dict, phone: str, name: str, *, paid: bool = False
) -> tuple[int, int]:
    """建家长 + 孩子；paid=True 时缴观察期费 + 押金（借书前置）。

    **可重入**：手机号已存在（409，重复跑压测）→ 复用原家长再建孩子，
    否则同一脚本第二次跑就崩在"手机号重复"上（首轮实测）。
    """
    p = httpx.post(
        f"{base}/api/admin/members/parents", json={"name": "压测家长", "phone": phone}, headers=h
    )
    if p.status_code == 409:
        listed = httpx.get(
            f"{base}/api/admin/members/parents", params={"keyword": phone}, headers=h
        ).json()
        pid = listed[0]["id"]
    else:
        p.raise_for_status()
        pid = p.json()["id"]
    c = httpx.post(
        f"{base}/api/admin/members/parents/{pid}/children", json={"name": name}, headers=h
    )
    c.raise_for_status()
    cid = c.json()["id"]
    if paid:
        for payload in ({"child_id": cid, "order_type": "observation_fee"},):
            o = httpx.post(f"{base}/api/admin/orders", json=payload, headers=h)
            o.raise_for_status()
            httpx.post(
                f"{base}/api/admin/orders/{o.json()['id']}/confirm-payment",
                json={"pay_method": "scan"},
                headers=h,
            ).raise_for_status()
        d = httpx.post(f"{base}/api/admin/deposits/children/{cid}/orders", headers=h)
        d.raise_for_status()
        dep = d.json()
        # 押金建单返回 `order_id`（普通建单返回 `id`）——两处口径不同，取兼容
        dep_id = dep.get("id") or dep["order_id"]
        httpx.post(
            f"{base}/api/admin/orders/{dep_id}/confirm-payment",
            json={"pay_method": "scan"},
            headers=h,
        ).raise_for_status()
    return pid, cid


def make_book(base: str, h: dict, title: str, copies: int = 1) -> tuple[int, list[int]]:
    """建书并**强制上架**：临时关掉上架完整性校验开关 → toggle → 恢复（与 tests/unit/helpers 同款）。"""
    b = httpx.post(
        f"{base}/api/admin/books",
        json={"isbn": None, "title": title, "word_count": 1000, "copy_count": copies},
        headers=h,
    )
    b.raise_for_status()
    bid = b.json()["id"]
    httpx.put(
        f"{base}/api/admin/configs/book_onboarding_check",
        json={"value": "false", "reason": "压测上架"},
        headers=h,
    )
    t = httpx.post(f"{base}/api/admin/books/{bid}/toggle-status", headers=h)
    t.raise_for_status()
    httpx.put(
        f"{base}/api/admin/configs/book_onboarding_check",
        json={"value": "true", "reason": "恢复默认"},
        headers=h,
    )
    cps = httpx.get(f"{base}/api/admin/books/{bid}/copies", headers=h)
    cps.raise_for_status()
    return bid, [c["id"] for c in cps.json()]


def burst(fn, n: int) -> list[dict]:
    """n 个线程**同时**发（Barrier 对齐起跑线），返回 [(status, ms, text)]。"""
    barrier = threading.Barrier(n)

    def one(i: int) -> dict:
        barrier.wait()
        t0 = time.perf_counter()
        try:
            r = fn(i)
            status, text = r.status_code, r.text[:200]
        except Exception as exc:  # noqa: BLE001 —— 压测要如实记失败
            status, text = -1, f"{type(exc).__name__}: {exc}"
        return {"status": status, "ms": (time.perf_counter() - t0) * 1000, "text": text}

    with ThreadPoolExecutor(max_workers=n) as pool:
        return list(pool.map(one, range(n)))


def stats(results: list[dict], ok_codes=(200,)) -> dict:
    lat = sorted(r["ms"] for r in results)
    ok = [r for r in results if r["status"] in ok_codes]
    codes: dict[str, int] = {}
    for r in results:
        codes[str(r["status"])] = codes.get(str(r["status"]), 0) + 1

    def pct(p: float) -> float:
        if not lat:
            return 0.0
        idx = min(len(lat) - 1, int(round(p / 100 * len(lat))) - 1)
        return round(lat[max(idx, 0)], 1)

    return {
        "n": len(results),
        "ok": len(ok),
        "fail": len(results) - len(ok),
        "codes": codes,
        "p50_ms": round(statistics.median(lat), 1) if lat else 0.0,
        "p95_ms": pct(95),
        "p99_ms": pct(99),
        "max_ms": round(lat[-1], 1) if lat else 0.0,
        "errors": [r["text"] for r in results if r["status"] not in ok_codes][:3],
    }


def backend_rss_kb(base: str) -> int | None:
    """后端进程 RSS（KB）——用端口反查 pid（压测实例独享端口）。"""
    port = base.rsplit(":", 1)[-1].split("/")[0]
    try:
        pid = (
            subprocess.run(["lsof", "-ti", f":{port}"], capture_output=True, text=True, check=True)
            .stdout.strip()
            .splitlines()[0]
        )
        out = subprocess.run(
            ["ps", "-o", "rss=", "-p", pid], capture_output=True, text=True, check=True
        ).stdout.strip()
        return int(out)
    except Exception:  # noqa: BLE001
        return None


def _scratch_db():
    """压测库会话（**必须**是压测/演练库——绝不默认演示库）。不变量核验只读本库。"""
    db_name = os.environ.get("DB_NAME", "")
    if "loadtest" not in db_name and "drill" not in db_name:
        raise SystemExit(f"拒绝连库：DB_NAME={db_name!r} 不是压测/演练库（须含 loadtest/drill）")
    from backend.database import get_session

    return get_session()


def _count(model, *criteria) -> int:
    db = _scratch_db()
    try:
        return int(db.query(model).filter(*criteria).count())
    finally:
        db.close()


# ---------- G1 借还并发 ----------


def g1_borrow(base: str, h: dict) -> dict:
    out: dict = {}
    # ① 同一副本被 10 个孩子并发借 → 恰好 1 成功
    _b, copies = make_book(base, h, "压测·抢同一本", copies=1)
    copy_id = copies[0]
    kids = [make_family(base, h, f"1399000{i:04d}", f"抢书孩{i}", paid=True)[1] for i in range(10)]
    res = burst(
        lambda i: httpx.post(
            f"{base}/api/admin/circulation/borrow",
            json={"child_id": kids[i], "copy_id": copy_id},
            headers=h,
            timeout=30,
        ),
        10,
    )
    s = stats(res)
    out["同一副本并发借"] = {
        **s,
        "不变量": "恰好 1 成功",
        "判定": "PASS" if s["ok"] == 1 else "FAIL",
    }

    # ② 同一孩子并发借 10 本、额度只剩 5 → 恰好 5 成功（不超卖）
    httpx.put(
        f"{base}/api/admin/configs/borrow_limit",
        json={"value": "5", "reason": "压测设额度"},
        headers=h,
    ).raise_for_status()
    kid = make_family(base, h, "13990099999", "额度孩", paid=True)[1]
    books = [make_book(base, h, f"压测·额度书{i}")[1][0] for i in range(10)]
    res2 = burst(
        lambda i: httpx.post(
            f"{base}/api/admin/circulation/borrow",
            json={"child_id": kid, "copy_id": books[i]},
            headers=h,
            timeout=30,
        ),
        10,
    )
    s2 = stats(res2)
    from backend.domain.circulation.models import BorrowRecord

    active = _count(
        BorrowRecord,
        BorrowRecord.child_id == kid,
        BorrowRecord.status.in_([BorrowRecord.STATUS_ACTIVE, BorrowRecord.STATUS_OVERDUE]),
        BorrowRecord.is_deleted == 0,
    )
    out["同孩并发借(额度5)"] = {
        **s2,
        "在借实得": active,
        "不变量": "恰好 5 成功且在借 = 5",
        "判定": "PASS" if s2["ok"] == 5 and active == 5 else "FAIL",
    }
    httpx.put(
        f"{base}/api/admin/configs/borrow_limit",
        json={"value": "30", "reason": "压测复原"},
        headers=h,
    )
    return out


# ---------- G2 报名并发 ----------


def g2_enroll(base: str, h: dict) -> dict:
    a = httpx.post(
        f"{base}/api/admin/activities",
        json={
            "title": "压测·名额 5",
            # 远期哨兵（≥2050）：门禁 check_fake_assertions 判"时间炸弹"（2030 会到期）
            "start_at": "2050-01-01T10:00:00",
            "max_quota": 5,
            "fee": "0",
        },
        headers=h,
    )
    a.raise_for_status()
    aid = a.json()["id"]
    fams = []
    for i in range(CONC):
        phone = f"1399100{i:04d}"
        pid, cid = make_family(base, h, phone, f"报名孩{i}")
        fams.append((cid, parent_token(pid)))

    res = burst(
        lambda i: httpx.post(
            f"{base}/api/miniapp/activities/{aid}/enroll",
            json={"child_id": fams[i][0]},
            headers=fams[i][1],
            timeout=30,
        ),
        CONC,
    )
    s = stats(res)
    from backend.domain.activity.models import ActivityEnrollment

    enrolled = _count(
        ActivityEnrollment,
        ActivityEnrollment.activity_id == aid,
        ActivityEnrollment.is_deleted == 0,
    )
    return {
        "名额5/20并发": {
            **s,
            "后台已报名": enrolled,
            "不变量": "恰好 5 成功且已报名 = 5",
            "判定": "PASS" if s["ok"] == 5 and enrolled == 5 else "FAIL",
        }
    }


# ---------- G3 支付回调风暴 ----------


def _notify_body(order_no: str, amount_yuan: str) -> str:
    from backend.common.gateways.payment.types import yuan_to_cents

    payload = {
        "out_trade_no": order_no,
        "transaction_id": f"txn_load_{order_no}",
        "trade_state": "SUCCESS",
        "amount": int(yuan_to_cents(Decimal(amount_yuan))),
    }
    return json.dumps(
        {
            "id": "evt_load",
            "event_type": "TRANSACTION.SUCCESS",
            "resource_type": "encrypt-resource",
            "resource": {
                "algorithm": "AEAD_AES_256_GCM",
                "ciphertext": json.dumps(payload),
                "associated_data": "transaction",
                "nonce": "nonce123456",
            },
        }
    )


def g3_notify(base: str, h: dict) -> dict:
    _p, cid = make_family(base, h, "13992000001", "回调孩")
    o = httpx.post(
        f"{base}/api/admin/orders",
        json={"child_id": cid, "order_type": "observation_fee"},
        headers=h,
    )
    o.raise_for_status()
    order = o.json()
    body = _notify_body(order["order_no"], str(order["amount"]))
    hdr = {
        "content-type": "application/json",
        "Wechatpay-Signature": "sig",
        "Wechatpay-Timestamp": str(int(time.time())),
        "Wechatpay-Nonce": "nonce123456",
    }
    res = burst(
        lambda i: httpx.post(
            f"{base}/api/pay/wechat/notify", content=body, headers=hdr, timeout=30
        ),
        CONC,
    )
    s = stats(res, ok_codes=(200,))
    from backend.domain.identity.models import Child, Order

    db = _scratch_db()
    try:
        order_row = db.query(Order).filter(Order.id == order["id"]).first()
        paid = order_row.status if order_row else None
        order_no = order["order_no"]
        order_rows = db.query(Order).filter(Order.order_no == order_no).count()
        kid = db.query(Child).filter(Child.id == cid).first()
        status, expire = (kid.member_status, kid.member_expire) if kid else (None, None)
    finally:
        db.close()
    from datetime import datetime, timedelta

    # 幂等的硬判据：会员只被"入账一次"——到期日应在 +30 天附近（双入账会变成 +60）
    today = datetime.now().date()  # member_expire 是 Date 列
    expire_ok = bool(expire and today + timedelta(days=29) < expire < today + timedelta(days=31))
    return {
        "同单20次回调": {
            **s,
            "订单状态": paid,
            "同单号订单行数": order_rows,
            "会员状态": status,
            "到期日": expire.isoformat() if expire else None,
            "不变量": "恰好入账 1 次（订单 paid + 唯一订单行 + 到期日只 +30 天 + 其余回调幂等 200）",
            "判定": (
                "PASS"
                if s["ok"] == CONC and paid == "paid" and order_rows == 1 and expire_ok
                else "FAIL"
            ),
        }
    }


# ---------- G4 批量导入 1000 行 ----------


def _isbn13(i: int) -> str:
    """合法 ISBN-13（**校验位真算**）：压测要 1000 行导入，随便编的数字会被行级校验拒掉
    （首轮实测：`978700000000` → "ISBN 格式不正确" 1000 行全失败）。"""
    body = f"978{i:09d}"[:12]
    total = sum(int(d) * (1 if k % 2 == 0 else 3) for k, d in enumerate(body))
    return body + str((10 - total % 10) % 10)


def _xlsx(rows: int, offset: int = 0) -> bytes:
    """offset：ISBN 起点偏移——**每次跑用不同号段**，否则第二次跑会走"同 ISBN 并入原书目"
    （导入语义正确，但"1000 行新建"的不变量就不成立了）。"""
    import io

    from openpyxl import Workbook

    wb = Workbook()
    ws = wb.active
    ws.append(["ISBN", "书名", "作者", "AR", "词数", "主题", "年级", "副本数"])
    for i in range(rows):
        ws.append(
            [_isbn13(offset + i), f"压测导入书{offset + i}", "load", "1.5", "300", "", "", "1"]
        )
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


def g4_import(base: str, h: dict, rows: int = 1000) -> dict:
    offset = int(time.time()) % 900_000_000
    data = _xlsx(rows, offset)
    rss0 = backend_rss_kb(base)
    t0 = time.perf_counter()
    r = httpx.post(
        f"{base}/api/admin/books/import",
        headers=h,
        files={"file": ("load.xlsx", data, "application/vnd.ms-excel")},
        timeout=300,
    )
    ms = (time.perf_counter() - t0) * 1000
    rss1 = backend_rss_kb(base)
    body = r.json() if r.status_code == 200 else {"error": r.text[:200]}
    # P2-43：**回库核对**——原先只信接口自报的 success_count（自证绿：接口说成功就算成功，
    # 真没落库也看不出来）。这里按本次号段真查一遍库里落了几个书目。
    from backend.domain.catalog.models import Book

    landed = _count(Book, Book.title.like(f"压测导入书{offset}%"))
    ok = r.status_code == 200 and body.get("success_count") == rows and landed >= rows
    return {
        f"导入{rows}行": {
            "http": r.status_code,
            "成功行": body.get("success_count"),
            "失败行": body.get("failed_count"),
            "回库核对行": landed,
            "耗时_s": round(ms / 1000, 2),
            "行每秒": round(rows / (ms / 1000), 1) if ms else 0,
            "后端RSS_增量_MB": (round((rss1 - rss0) / 1024, 1) if rss0 and rss1 else None),
            "判定": "PASS" if ok else "FAIL",
        }
    }


# ---------- G5 定时任务在数据量增长下的耗时 ----------


def _seed_volume(
    books: int = 2000,
    children: int = 2000,
    borrows: int = 6000,
    orders: int = 2000,
    refunds: int = 800,
    words: int = 60_000,
) -> dict:
    """造量：直连**压测库**（`DB_NAME` 由环境给出，脚本只认 env，绝不默认演示库）。

    P2-43（2026-10-20 第八轮）两处补强：
    · **补 orders / refund_requests / words_ledgers**——原先只造书目/孩子/借阅，
      于是"支付对账、退款审核队列、榜单/横幅聚合"这些**随流水线性增长**的任务在压测里
      根本压不到（它们在真实库里的耗时才是容量风险）；
    · **可重跑**：所有唯一键（手机号 / 副本码 / 书名 / 订单号）带**本次运行 tag**，
      第二次跑不再撞唯一约束（原先第二次跑必然报重复键）。
    """
    db_name = os.environ.get("DB_NAME", "")
    if not any(m in db_name for m in LOADTEST_DB_MARKERS):
        raise SystemExit(
            f"拒绝造量：DB_NAME={db_name!r} 不是压测/演练库"
            f"（必须含 {'/'.join(LOADTEST_DB_MARKERS)}）"
        )
    from datetime import datetime, timedelta
    from decimal import Decimal

    from sqlalchemy import insert

    from backend.database import get_session
    from backend.domain.catalog.models import Book, BookCopy
    from backend.domain.circulation.models import BorrowRecord
    from backend.domain.growth.models import WordsLedger
    from backend.domain.identity.models import Child, Order, Parent, RefundRequest

    tag = os.environ.get("LOAD_RUN_TAG") or time.strftime("%m%d%H%M%S")
    now = datetime.now()
    db = get_session()
    try:
        db.execute(
            insert(Parent),
            [
                {"name": f"压量家长{tag}-{i}", "phone": f"1398{tag[-6:]}{i:04d}", "is_deleted": 0}
                for i in range(children)
            ],
        )
        db.commit()
        pids = [
            r[0] for r in db.query(Parent.id).filter(Parent.name.like(f"压量家长{tag}-%")).all()
        ]
        db.execute(
            insert(Child),
            [
                {
                    "name": f"压量孩{tag}-{i}",
                    "parent_id": pids[i % len(pids)],
                    "member_status": Child.MEMBER_FORMAL,
                    "member_expire": now + timedelta(days=(i % 40) - 5),
                    "is_deleted": 0,
                }
                for i in range(children)
            ],
        )
        db.execute(
            insert(Book),
            [
                {
                    "title": f"压量书{tag}-{i}",
                    "author": "load",
                    "word_count": 1000,
                    "status": Book.STATUS_ON,
                    "is_deleted": 0,
                    "create_time": now,
                    "update_time": now,
                }
                for i in range(books)
            ],
        )
        db.commit()
        bids = [r[0] for r in db.query(Book.id).filter(Book.title.like(f"压量书{tag}-%")).all()]
        db.execute(
            insert(BookCopy),
            [
                {
                    "book_id": b,
                    "copy_code": f"LOAD-{tag}-{b}-{k}",
                    "status": BookCopy.STATUS_AVAILABLE,
                }
                for b in bids
                for k in range(5)
            ],
        )
        db.commit()
        cids = [r[0] for r in db.query(Child.id).filter(Child.name.like(f"压量孩{tag}-%")).all()]
        # 副本 → 书目（每本书 5 个副本）——`borrow_records.book_id` 是 NOT NULL 无默认值
        copy_rows = (
            db.query(BookCopy.id, BookCopy.book_id)
            .filter(BookCopy.copy_code.like(f"LOAD-{tag}-%"))
            .order_by(BookCopy.id)
            .limit(borrows)
            .all()
        )
        db.execute(
            insert(BorrowRecord),
            [
                {
                    "book_id": book_id,
                    "child_id": cids[i % len(cids)],
                    "copy_id": copy_id,
                    "borrowed_at": now - timedelta(days=40 + (i % 10)),
                    "due_at": now - timedelta(days=10 - (i % 10)),
                    "status": BorrowRecord.STATUS_ACTIVE,
                    "is_deleted": 0,
                }
                for i, (copy_id, book_id) in enumerate(copy_rows)
            ],
        )
        # 已支付订单（对账/导出/列表类任务的输入）
        db.execute(
            insert(Order),
            [
                {
                    "order_no": f"LOAD{tag}{i:06d}",
                    "order_type": Order.TYPE_OBSERVATION,
                    "parent_id": pids[i % len(pids)],
                    "child_id": cids[i % len(cids)],
                    "amount": Decimal("99.00"),
                    "status": Order.STATUS_PAID,
                    "paid_at": now - timedelta(days=i % 60),
                    "pay_method": "scan",
                    "is_deleted": 0,
                }
                for i in range(orders)
            ],
        )
        # 退款台账（退款审核队列/汇总的输入）
        db.execute(
            insert(RefundRequest),
            [
                {
                    "kind": RefundRequest.KIND_ORDER,
                    "child_id": cids[i % len(cids)],
                    "amount": Decimal("99.00"),
                    "reason": f"压测退款{tag}-{i}",
                    "status": RefundRequest.STATUS_PENDING,
                    "is_deleted": 0,
                }
                for i in range(refunds)
            ],
        )
        # 词账（榜单/横幅聚合的输入；分布到近 30 天，覆盖周/月榜窗口）
        db.execute(
            insert(WordsLedger),
            [
                {
                    "child_id": cids[i % len(cids)],
                    "book_id": bids[i % len(bids)],
                    "word_count": 100 + (i % 400),
                    "source": "load",
                    "created_at": now - timedelta(days=i % 30),
                    "is_deleted": 0,
                }
                for i in range(words)
            ],
        )
        db.commit()
        return {
            "运行tag": tag,
            "书目": books,
            "孩子": children,
            "借阅": borrows,
            "订单": orders,
            "退款": refunds,
            "词账": words,
        }
    finally:
        db.close()


def all_task_names() -> list[str]:
    """**全部**定时任务（P2-43：从注册表枚举，不再手抄 9 个）。

    原先手抄的 9 个任务漏掉了 12 个（含"随流水线性增长"的榜单快照/对账/清理类），
    而"容量已验"的前提是**每个任务都在目标数据量下跑过**。这里读 `tasks.registry.TASKS`
    ——以后新增任务自动纳入压测（不会再次漂移）。
    """
    from backend.tasks.registry import TASKS

    return sorted(TASKS)


def g5_tasks(base: str, h: dict, volume: dict) -> dict:
    """逐个任务计时 + **耗时上限判定**（P2-43：原先只报数，没有 PASS/FAIL）。"""
    out: dict = {"造量": volume, "耗时上限_s": TASK_DURATION_LIMIT_S, "任务": {}}
    names = all_task_names()
    out["任务数"] = len(names)
    for name in names:
        t0 = time.perf_counter()
        try:
            r = httpx.post(f"{base}/api/admin/tasks/{name}/run", headers=h, timeout=600)
            body = r.json() if r.status_code == 200 else {"error": r.text[:200]}
            code = r.status_code
        except Exception as exc:  # noqa: BLE001 — 单任务异常不该中断整轮压测
            body, code = {"error": f"{type(exc).__name__}: {str(exc)[:160]}"}, 0
        ms = (time.perf_counter() - t0) * 1000
        secs = ms / 1000
        ok = code == 200 and body.get("status") == "success" and secs <= TASK_DURATION_LIMIT_S
        out["任务"][name] = {
            "http": code,
            "耗时_s": round(secs, 2),
            "处理数": body.get("processed"),
            "状态": body.get("status"),
            "错误": body.get("error"),
            "判定": "PASS" if ok else "FAIL",
        }
    failed = [k for k, v in out["任务"].items() if v["判定"] == "FAIL"]
    out["判定"] = "PASS" if not failed else "FAIL"
    out["未通过任务"] = failed
    return out


# ---------- main ----------


def collect_verdicts(node, path: str = "") -> list[tuple[str, str]]:
    """递归收集报告里所有 `判定` 字段（P2-43：原先靠 `text.count('"判定": "FAIL"')` 数失败——
    字符串口径脆弱（格式化/换行一变就数不到），且只在**最终 JSON 文本**上判）。"""
    out: list[tuple[str, str]] = []
    if isinstance(node, dict):
        if "判定" in node:
            out.append((path or "根", str(node["判定"])))
        for k, v in node.items():
            if k == "判定":
                continue
            out.extend(collect_verdicts(v, f"{path}/{k}" if path else str(k)))
    elif isinstance(node, list):
        for i, v in enumerate(node):
            out.extend(collect_verdicts(v, f"{path}[{i}]"))
    return out


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--base-url", default="http://127.0.0.1:8003")
    ap.add_argument("--group", default="all", choices=["all", "g1", "g2", "g3", "g4", "g5"])
    ap.add_argument("--json", default="")
    args = ap.parse_args()
    base = args.base_url.rstrip("/")

    guard_target(base)  # P2-43：先拒绝演示库/演示端口，再开始任何压测动作
    h = login_admin(base)
    report: dict = {
        "base_url": base,
        "conc": CONC,
        "started_at": time.strftime("%Y-%m-%d %H:%M:%S"),
    }
    groups = ["g1", "g2", "g3", "g4", "g5"] if args.group == "all" else [args.group]

    if "g1" in groups:
        report["G1_借还并发"] = g1_borrow(base, h)
    if "g2" in groups:
        report["G2_报名并发"] = g2_enroll(base, h)
    if "g3" in groups:
        report["G3_支付回调风暴"] = g3_notify(base, h)
    if "g4" in groups:
        report["G4_批量导入"] = g4_import(base, h)
    if "g5" in groups:
        report["G5_定时任务"] = g5_tasks(base, h, _seed_volume())

    report["finished_at"] = time.strftime("%Y-%m-%d %H:%M:%S")
    verdicts = collect_verdicts(report)
    failed = [p for p, v in verdicts if v == "FAIL"]
    report["判定汇总"] = {
        "项数": len(verdicts),
        "PASS": len(verdicts) - len(failed),
        "FAIL": len(failed),
        "未通过": failed,
        "结论": "PASS" if not failed else "FAIL",
    }
    text = json.dumps(report, ensure_ascii=False, indent=2)
    print(text)
    if args.json:
        with open(args.json, "w", encoding="utf-8") as fh:
            fh.write(text + "\n")
    print(
        f"\n[load] 判定 {report['判定汇总']['PASS']}/{len(verdicts)} PASS"
        f"；FAIL={report['判定汇总']['FAIL']}"
    )
    for p in failed:
        print(f"  · FAIL {p}")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
