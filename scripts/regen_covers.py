#!/usr/bin/env python
"""重刷全量演示封面（幂等）。

为什么需要独立入口：封面绘制在 `scripts/seed_demo_library.py::gen_cover`，而 seed 只在
「书目不存在」时才画封面——改绘图风格后老封面不会更新（书 31-35 更是一直 cover_path=NULL，
落到前端 📕 emoji 兜底）。本脚本**写入新文件名并删除旧文件**：①不留孤儿 ②文件名 token 变化 → 小程序
`<image>` 按 URL 缓存的一层才能真正刷新（原地覆盖 URL 不变，用户永远看旧图，2026-09-15 实测）。

用法：
  .venv/bin/python scripts/regen_covers.py            # 默认 dry-run：只打印将重刷的清单，不动任何文件
  .venv/bin/python scripts/regen_covers.py --apply    # 真正执行（写新文件 + 删旧封面 + 提交）

[Why 默认 dry-run] 2026-09-20（fix44 R4）：本脚本原先安全阀是 **opt-in 的 `--dry-run`**
（`action="store_true"` → 默认就是"写盘 + 删旧封面 + commit"），与全项目清理脚本纪律
（红线：清理/重写类脚本默认 dry-run + 正向 apply 开关）相反；`scripts/check_media_discipline.py`
M3c 现已机化拦截该形状，本脚本按纪律改为 `--apply` 正向开关。
"""

from __future__ import annotations

import argparse
import os
import secrets
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from backend.common.file_storage import _uploads_root  # noqa: E402
from backend.database import SessionLocal  # noqa: E402
from backend.domain.activity.models import Activity  # noqa: E402
from backend.domain.catalog.models import Book  # noqa: E402
from scripts.seed_demo_library import (  # noqa: E402
    PALETTES,
    TOPICS,
    gen_activity_banner,
    gen_cover,
)


def _drop_old(rel: str | None) -> None:
    """删掉被替换的旧封面文件（改用「写新文件」的代价是会产生旧文件，必须清）。"""
    if not rel:
        return
    abs_path = os.path.join(_uploads_root(), rel)
    if os.path.isfile(abs_path):
        os.remove(abs_path)


def _write(rel: str, data: bytes) -> None:
    abs_path = os.path.join(_uploads_root(), rel)
    os.makedirs(os.path.dirname(abs_path), exist_ok=True)
    with open(abs_path, "wb") as fh:
        fh.write(data)


def _new_rel(prefix: str, isbn: str | None, code: str | None, bid: int) -> str:
    if prefix == "cover/activity":
        return f"{prefix}/{bid}_{secrets.token_hex(6)}.jpg"
    if isbn:
        return f"cover/{isbn[:4]}/{isbn}_{secrets.token_hex(6)}.jpg"
    return f"cover/local/{code or bid}_{secrets.token_hex(6)}.jpg"


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--apply", action="store_true", help="真正写盘（默认只打印计划，不动文件）")
    args = ap.parse_args()

    db = SessionLocal()
    rewritten = added = 0
    try:
        books = db.query(Book).filter(Book.is_deleted == 0).order_by(Book.id).all()
        for b in books:
            topic = b.topic if b.topic in TOPICS else TOPICS[b.id % len(TOPICS)]
            data = gen_cover(b.title, b.author or "", b.id, topic)
            rel = _new_rel("cover", b.isbn, b.book_code, b.id)
            if args.apply:
                _write(rel, data)
                _drop_old(b.cover_path)  # 不留孤儿
                b.cover_path = rel
            rewritten += 1 if b.cover_path else 0
            added += 0 if b.cover_path else 1
        acts = db.query(Activity).filter(Activity.is_deleted == 0).order_by(Activity.id).all()
        for i, a in enumerate(acts):
            # 活动用**横版专用图**（900x320，无字）：竖版裁进横条会被切顶（用户实测）
            data = gen_activity_banner(i)
            rel = _new_rel("cover/activity", None, None, a.id)
            if args.apply:
                _write(rel, data)
                _drop_old(a.cover_path)
                a.cover_path = rel
            rewritten += 1 if a.cover_path else 0
            added += 0 if a.cover_path else 1
        if args.apply:
            db.commit()
        print(
            f"{'' if args.apply else '[dry-run] '}封面重刷完成："
            f"重写 {rewritten} 张（换新文件名→客户端缓存必失效），补缺失 {added} 张，"
            f"调色板 {len(PALETTES)} 套"
            + ("" if args.apply else "；未做任何改动（加 --apply 才执行）")
        )
        return 0
    finally:
        db.close()


if __name__ == "__main__":
    raise SystemExit(main())
