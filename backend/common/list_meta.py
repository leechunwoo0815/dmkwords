# backend/common/list_meta.py — 列表硬上限的**诚实信号**（P2-44）
"""列表类接口的"结果已到上限"信号：用**响应头**告知，不改响应体形状。

为什么要它（P2-44，2026-10-20 第八轮大审查）：多处列表接口有**硬上限**
（退款/退会/转让/预约 200、家长订单 50、活动 200/50），但响应体就是裸数组——
管理员看到 200 行会以为"这就是全部"，于是**待审单被漏看**（与 P1-7 同族：
"看起来成功"的截断）。

为什么用响应头而不是包一层 `{items, total}`：
- 这些接口的消费方（管理端 + 小程序）遍布多处，改响应体形状是**破坏性变更**
  （还要同步 OpenAPI 生成的 `schema.d.ts`）；响应头对既有消费方**零影响**；
- 头里给的是"**已达上限**"这个**可判定**的事实（`len(rows) == limit`），不是猜出来的总数——
  语义上不会撒谎（正好等于上限时会提示"可能还有更多"，方向是安全的）。

配套前端：`admin-web/src/api/client.ts` 的 `requestList()` 读这两个头，
`PaintPagination` 显示提示条。
"""

from __future__ import annotations

from fastapi import Response

#: 该列表接口生效的上限
LIST_LIMIT_HEADER = "X-List-Limit"
#: `true` = 返回行数已达上限（**可能**还有更多未返回；请缩小筛选范围）
LIST_TRUNCATED_HEADER = "X-List-Truncated"


def stamp_list_meta(response: Response, *, loaded: int, limit: int) -> None:
    """把"上限 / 是否已达上限"写进响应头（`loaded >= limit` 即认为可能被截断）。"""
    response.headers[LIST_LIMIT_HEADER] = str(limit)
    response.headers[LIST_TRUNCATED_HEADER] = "true" if loaded >= limit else "false"
