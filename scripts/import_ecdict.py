# scripts/import_ecdict.py — ECDICT 全量词库导入（C5：340 万词，来源 data/ecdict.db SQLite）
"""从 data/ecdict.db（stardict 表）批量导入 dictionary_words（INSERT IGNORE，保留已有词）。
用法：python -m scripts.import_ecdict   （约 3-8 分钟，可随时 Ctrl-C 重跑续传）

**第十轮审查 W3-03（2026-10-24）**：上面这句自述原先**不成立**——`dictionary_words.word`
只有普通索引（`unique=False`），而 `INSERT IGNORE` 只在"存在可冲突的唯一键"时才跳过重复：
没有唯一键 = 每条都真插入，第二次执行就是整库再插 340 万词。同版本还主动
`SET SESSION unique_checks=0` 把唯一性校验关掉，等于即使将来加了唯一索引也会被本会话绕过。
现在：迁移 `d5f1a83c7042` 把该列改成**唯一索引**，本脚本不再关 `unique_checks`
（`foreign_key_checks` 也去掉：本表无外键，关它只会掩盖真实错误）。
"""

import sqlite3
import sys
import time

from sqlalchemy import text

from backend.database import get_session

SRC_DB = "data/ecdict.db"
BATCH = 5000
SQL_UPSERT = text(
    "INSERT IGNORE INTO dictionary_words (word, phonetic, definition, translation, "
    "create_time, update_time, is_deleted) VALUES (:w, :p, :d, :t, NOW(), NOW(), 0)"
)


def main() -> int:
    src = sqlite3.connect(SRC_DB)
    cur = src.cursor()
    total_src = cur.execute(
        "SELECT COUNT(*) FROM stardict WHERE word IS NOT NULL AND word != ''"
    ).fetchone()[0]
    print(f"源词库 {total_src} 条，开始导入（batch={BATCH}）...", flush=True)

    imported = 0
    t0 = time.time()
    cur.execute(
        "SELECT word, phonetic, definition, translation FROM stardict "
        "WHERE word IS NOT NULL AND word != ''"
    )
    with get_session() as db:
        while True:
            rows = cur.fetchmany(BATCH)
            if not rows:
                break
            params = [
                {"w": w, "p": (p or "")[:128], "d": d or "", "t": t or ""} for w, p, d, t in rows
            ]
            res = db.execute(SQL_UPSERT, params)
            db.commit()
            imported += res.rowcount if res.rowcount and res.rowcount > 0 else 0
            elapsed = time.time() - t0
            done = imported
            print(
                f"  已导入 {done}（本批 {len(rows)}，用时 {elapsed:.0f}s，"
                f"预计剩余 {(total_src - done) / max(done, 1) * elapsed:.0f}s）",
                flush=True,
            )
    print(f"完成：新导入 {imported} 条，总词库见 SELECT COUNT(*)。", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
