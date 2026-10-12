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
