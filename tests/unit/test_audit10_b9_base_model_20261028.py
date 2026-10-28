# tests/unit/test_audit10_b9_base_model_20261028.py — W3-09：公共三列 NOT NULL
"""第十轮 W3-09（B9）：41 张业务表的 `is_deleted/create_time/update_time` 必须在 DB 层收口。

背景：三列原先 nullable=YES 且无 DB 默认（默认值只在 ORM 侧）。任何绕过 ORM 的写口插入的行
会得到 `is_deleted=NULL`，而全仓查询谓词是 `is_deleted = 0` → 该行对全站永久不可见且不报错。

判据：
1. `BaseModel` 元数据三列 nullable=False；`is_deleted` 带 server_default；
2. 真实库里带这三列的表，**0 个可空**，且 `is_deleted` 的 DB 默认是 0。
"""

from __future__ import annotations

from sqlalchemy import text

from tests.unit.test_p1_audit_20261015 import _db


def test_base_model_metadata_is_not_null():
    """Mixin 无独立 __table__：从任一子类（books）读模型元数据。"""
    from backend.domain.catalog.models import Book

    for name in ("is_deleted", "create_time", "update_time"):
        assert Book.__table__.c[name].nullable is False, f"模型列 {name} 仍可空"
    assert Book.__table__.c["is_deleted"].server_default is not None


def test_database_has_zero_nullable_base_columns():
    with _db() as db:
        bad = db.execute(
            text(
                """SELECT COUNT(*) FROM information_schema.COLUMNS
                   WHERE TABLE_SCHEMA=DATABASE()
                     AND COLUMN_NAME IN ('is_deleted','create_time','update_time')
                     AND (IS_NULLABLE='YES'
                          OR (COLUMN_NAME='is_deleted' AND COLUMN_DEFAULT IS NULL))"""
            )
        ).scalar()
        assert bad == 0, f"仍有 {bad} 个公共列可空或缺默认"
        default = db.execute(
            text(
                """SELECT COLUMN_DEFAULT FROM information_schema.COLUMNS
                   WHERE TABLE_SCHEMA=DATABASE() AND TABLE_NAME='books'
                     AND COLUMN_NAME='is_deleted'"""
            )
        ).scalar()
        assert str(default) == "0"
