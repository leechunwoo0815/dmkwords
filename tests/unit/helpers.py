# tests/unit/helpers.py — 测试辅助函数（跨文件复用）
"""D1 上架强校验的测试辅助：新书默认下架入库，下游链路（reading 等）需要上架态的书。"""


def force_book_on(client, headers, book_id: int, reason: str = "测试上架"):
    """临时关闭上架完整性校验开关 → 上架 → 恢复开关（走真实 API 链路）。"""
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
