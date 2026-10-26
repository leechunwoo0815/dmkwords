# backend/common/sql_utils.py
"""SQL 小工具：LIKE 通配符转义 / 唯一约束兜底插入。"""

from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session


def escape_like(keyword: str) -> str:
    """转义 LIKE 通配符（配合 .like(..., escape="\\") 使用）。

    防止用户输入的 % / _ 被当作通配符，导致搜索结果失真或被用于信息探测。
    """
    return keyword.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


def add_with_unique_fallback(db: Session, obj: object) -> bool:
    """SAVEPOINT 插入；唯一约束冲突（并发重复）时静默跳过。

    用于"先查后插"场景的 DB 层兜底：代码层查重存在 TOCTOU 竞态，
    并发下两个请求可能同时通过查重。有 DB 唯一约束时，后到的插入
    会抛 IntegrityError —— 用 SAVEPOINT 包住，冲突即回滚到保存点，
    不污染外层事务。

    返回 True=插入成功，False=冲突跳过。
    """
    try:
        with db.begin_nested():
            db.add(obj)
        return True
    except IntegrityError as exc:
        # R11-c/C5：只有**唯一键冲突**（并发重复）才静默跳过；FK / NOT NULL 等违约
        # 照抛——原先宽捕获会把"数据本身不合法"误报成"已存在"。
        if _is_duplicate_key(exc):
            return False
        raise


def _is_duplicate_key(exc: IntegrityError) -> bool:
    """MySQL ER_DUP_ENTRY=1062；其它后端报 UNIQUE constraint 时按文本兜底。"""
    code = getattr(getattr(exc, "orig", None), "args", [None])[0]
    if code == 1062:
        return True
    text = str(exc)
    return "Duplicate entry" in text or "UNIQUE constraint failed" in text
