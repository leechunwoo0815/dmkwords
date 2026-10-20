# tests/unit/helpers.py — 测试辅助函数（跨文件复用）
"""D1 上架强校验的测试辅助：新书默认下架入库，下游链路（reading 等）需要上架态的书。"""


def ensure_book_on(client, headers, book_id: int, reason: str = "测试上架（幂等）"):
    """幂等上架：已是上架态就不动（避免与 force_book_on 叠加成"切换两次"又变下架）。

    2026-10-12（P6 下架禁借落地后）：借阅类用例的书必须先上架——本助手可重复调用。
    """
    # 注意：BookResponse.status 是**数值**（不是 "on" 字符串）——直接读库拿模型常量最稳，
    # 否则恒不等 → 每次都 toggle → 把已上架的书切成下架（2026-10-12 实测踩到）
    from backend.database import get_session
    from backend.domain.catalog.models import Book

    db = get_session()
    try:
        row = db.query(Book).filter(Book.id == book_id).first()
        status = row.status if row else None
    finally:
        db.close()
    if status != Book.STATUS_ON:
        force_book_on(client, headers, book_id, reason)


def force_book_on(client, headers, book_id: int, reason: str = "测试上架"):
    """临时关闭上架完整性校验开关 → 上架 → 恢复开关（走真实 API 链路）。

    2026-10-12：**改成幂等**——已是上架态就什么都不做。原因：conftest 已让"建书即上架"，
    这里再 toggle 一次会把书切回下架（P6 下架禁借落地后，这类双 toggle 会让借阅/完播用例全红）。
    """
    from backend.database import get_session
    from backend.domain.catalog.models import Book

    db = get_session()
    try:
        row = db.query(Book).filter(Book.id == book_id).first()
        if row is not None and row.status == Book.STATUS_ON:

            class _Noop:  # 幂等短路：调用方惯例是断言 .status_code == 200
                status_code = 200
                text = ""

            return _Noop()
    finally:
        db.close()
    client.put(
        "/api/admin/configs/book_onboarding_check",
        json={"value": "false", "reason": reason},
        headers=headers,
    )
    resp = client.post(f"/api/admin/books/{book_id}/toggle-status", headers=headers)
    client.put(
        "/api/admin/configs/book_onboarding_check",
        json={"value": "true", "reason": "恢复默认"},
        headers=headers,
    )
    return resp


# ---------------- 并发用例的"可判定"等待（第八轮审查 P2-63） ----------------

#: 轮询间隔（秒）。**不要调小**：0.05s 的紧轮询会把被测线程挤住，导致根本看不到
#: LOCK WAIT（A 批 A7 实测假红）；0.25s 才稳定命中。
LOCK_WAIT_POLL_SECONDS = 0.25


def lock_wait_query(timeout: float = 8.0, interval: float = LOCK_WAIT_POLL_SECONDS) -> str:
    """等某个事务进入 `LOCK WAIT`，返回**被阻塞的那条语句**（空串 = 超时未见）。

    为什么并发用例要用它而不是 `time.sleep(1.0)`（第八轮审查 P2-63）：
    `sleep` 是**猜时序**——"睡 1 秒对方应该到了"。对方没到（机器慢/调度抖动）时用例会
    提前放锁，于是断言可能因为**别的原因**通过，并发守护退化成空跑；去掉 `with_for_update()`
    的变异也可能照样绿（A7/A8 两批实测）。本函数把"对方真的在等锁"变成**可判定事实**。

    为什么返回语句文本而不是布尔（A7 踩坑，如实记）：只判"有没有 LOCK WAIT"**不判别**
    ——修复前写的 `UPDATE` 同样会等行锁，照样命中。真正判别的是**阻塞发生在读还是写**：
      · 修复后：`SELECT ... FOR UPDATE` 就被挡住（`trx_query` 以 `SELECT` 开头）；
      · 修复前：普通读不阻塞，轮到 `UPDATE` 才等锁（`trx_query` 以 `UPDATE` 开头）。
    """
    import time as _time

    from sqlalchemy import text

    from backend.database import get_session

    deadline = _time.time() + timeout
    while _time.time() < deadline:
        with get_session() as db:
            row = db.execute(
                text(
                    "SELECT trx_query FROM information_schema.innodb_trx "
                    "WHERE trx_state = 'LOCK WAIT' LIMIT 1"
                )
            ).scalar()
        if row:
            return str(row)
        _time.sleep(interval)
    return ""


def assert_locked_read(blocked: str, what: str) -> None:
    """断言"对方阻塞在**锁定读**上"（`SELECT ... FOR UPDATE`）——修复前的普通读不阻塞。"""
    assert blocked, f"{what}：对方没有等在任何锁上（说明读取没加锁）"
    assert blocked.lstrip().upper().startswith("SELECT"), (
        f"{what}：被阻塞的必须是**锁定读**（SELECT ... FOR UPDATE）；"
        f"实际是 {blocked[:60]!r}——说明读没加锁，判据读的是旧快照"
    )


def assert_blocked(blocked: str, what: str) -> None:
    """断言"对方确实卡在锁上"（不断言语句形状——条件 UPDATE 类修复两种形态都是 UPDATE）。"""
    assert blocked, f"{what}：对方没有等在任何锁上（说明修复点没生效）"
