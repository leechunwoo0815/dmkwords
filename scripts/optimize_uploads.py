#!/usr/bin/env python3
"""存量图片回压（2026-09-17 用户裁定「要回压存量」）。

**背景**：图片体积纪律落地后（上传走 `normalize_image`、生成图落 JPEG），
磁盘上仍躺着按**旧口径**产出的文件：截图/照片原样直存、卡片与周报是 PNG（噪点压不动）。
本脚本把存量按新口径重做一遍。

三类处理（各自口径不同，别混）：

| 类别 | 文件 | 处理 | 为什么 |
|---|---|---|---|
| 生成图 · 阅读圈 | `circle/card_*.png`、`circle/thumb_*.png` | **重渲**（`render_card` 出新 .jpg + 新 token），删旧文件 | PNG 压不动纸纹噪点（769KB → 67KB）；新文件名 = URL 变 = 客户端缓存必刷 |
| 生成图 · 报告 | `reports/*.png` | 直接删 | 报告图**不落库**，端点按需重出（新实现按内容摘要幂等，看完不会堆文件） |
| 上传图 | 封面 / 活动封面 / 凭证 / 观察报告（**DB 引用中**） | **原地重编码**（同名覆盖，先备份原字节到 trash） | 只转格式不缩尺寸的历史产物；同名 ⇒ URL 不变 ⇒ 端上缓存里是同一张图，不会"变图" |

安全设计（对齐 `cleanup_uploads.py` 的教训 E-20260915-27）：
- 引用集复用 `cleanup_uploads.collect_referenced`（**唯一实现**，含软删行），不另写一份；
- 默认 **dry-run** 只打印；`--apply` 才落盘，且原字节先 `--trash` 备份（默认 `uploads/.trash-optimize/`）；
- **保护名单目录一律不动**（voucher/observation 里"人工上传"文件只重编码不删除，且只在 DB 引用时）。

用法::

    python -m scripts.optimize_uploads                    # 打印计划（默认，零改动）
    python -m scripts.optimize_uploads --apply            # 执行（原字节进 trash）
    python -m scripts.optimize_uploads --apply --trash /tmp/bak
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from backend.common.file_storage import (  # noqa: E402
    normalize_image,
    read_image_policy,
)
from backend.database import SessionLocal  # noqa: E402
from backend.domain.reading_circle.card_render import render_card  # noqa: E402

DEFAULT_TRASH = "uploads/.trash-optimize"

#: 上传图 → 策略场景（决定长边上限）。凭证与观察报告同为文档类。
#: 2026-09-20（fix44 R3）：**活动封面必须与书封分开取口径**（activity_cover 1200 ≠ cover 1080）——
#: 原先只有 `"cover/"` 一条前缀，`cover/activity/` 下的活动封面被按书封口径回压（降到 1080）。
#: 两者同在前缀 `cover/` 下，只能靠 DB 引用集区分"哪些文件算数"，口径则必须显式按子目录分流。
_UPLOAD_SCOPES = {
    "cover/activity/": "activity_cover",
    "cover/": "cover",
    "voucher/": "doc",
    "observation/": "doc",
}


def scope_of(rel: str) -> str | None:
    """按**最长前缀**取场景（不依赖字典顺序——顺序变了口径就会静默改错）。"""
    hit = max((p for p in _UPLOAD_SCOPES if rel.startswith(p)), key=len, default=None)
    return _UPLOAD_SCOPES[hit] if hit else None


def _mb(n: int) -> str:
    return f"{n / 1024 / 1024:.2f}MB"


def _report(label: str, before: int, after: int, note: str = "") -> None:
    saved = before - after
    pct = (saved / before * 100) if before else 0
    print(f"  {label:26s} {_mb(before):>10s} → {_mb(after):>10s}  省 {pct:5.1f}%  {note}")


def backfill_circle(root: str, apply: bool) -> tuple[int, int]:
    """重渲存量阅读圈卡片（PNG → JPEG）。返回 (文件数, 旧字节数)。"""
    from backend.domain.reading_circle.models import CirclePost

    db = SessionLocal()
    old_bytes = 0
    count = 0
    try:
        posts = db.query(CirclePost).filter(CirclePost.is_deleted == 0).all()
        for post in posts:
            legacy = [
                rel
                for rel in (post.image_path, post.thumb_path)
                if rel and rel.lower().endswith(".png")
            ]
            if not legacy:
                continue
            sizes = [
                os.path.getsize(os.path.join(root, r))
                for r in legacy
                if os.path.isfile(os.path.join(root, r))
            ]
            old_bytes += sum(sizes)
            count += len(legacy)
            print(f"  帖 {post.id}: 重渲 {os.path.basename(post.image_path or '')} + 缩略图")
            if not apply:
                continue
            data = json.loads(post.card_data or "{}")
            rendered = render_card(data)
            post.image_path = rendered["image_path"]
            post.thumb_path = rendered["thumb_path"]
            db.flush()
            for rel in legacy:
                full = os.path.join(root, rel)
                if os.path.isfile(full):
                    os.remove(full)
        if apply:
            db.commit()
    finally:
        db.close()
    return count, old_bytes


def backfill_reports(root: str, apply: bool) -> tuple[int, int]:
    """删掉存量报告图（不落库、端点按需重出）。返回 (文件数, 字节数)。"""
    out_dir = os.path.join(root, "reports")
    if not os.path.isdir(out_dir):
        return 0, 0
    total = 0
    n = 0
    for name in os.listdir(out_dir):
        if not name.lower().endswith(".png"):
            continue
        full = os.path.join(out_dir, name)
        total += os.path.getsize(full)
        n += 1
        if apply:
            os.remove(full)
    return n, total


def backfill_uploads(
    root: str, referenced: set[str], apply: bool, trash: str
) -> tuple[int, int, int]:
    """原地重编码 DB 引用中的上传图。返回 (处理数, 之前字节, 之后字节)。"""
    db = SessionLocal()
    before = after = 0
    n = 0
    try:
        targets: dict[str, str] = {}  # rel → scope
        for rel in sorted(referenced):
            scope = scope_of(rel)
            if scope:
                targets[rel] = scope
        for rel, scope in targets.items():
            full = os.path.join(root, rel)
            if not os.path.isfile(full):
                continue
            policy = read_image_policy(db, scope)
            raw = open(full, "rb").read()  # noqa: SIM115 — 读完全部即关，无需 with
            try:
                out = normalize_image(
                    raw,
                    max_edge=policy.max_edge,
                    quality=policy.quality,
                    max_output_bytes=policy.max_output_bytes,
                )
            except Exception as e:  # noqa: BLE001 — 单文件失败不阻塞整批
                print(f"  跳过 {rel}: {type(e).__name__} {e}")
                continue
            before += len(raw)
            after += len(out)
            n += 1
            print(f"  {rel}  {len(raw) / 1024:.0f}K → {len(out) / 1024:.0f}K")
            if not apply or len(out) >= len(raw):
                if apply and len(out) >= len(raw):
                    after -= len(out)
                    after += len(raw)
                continue
            os.makedirs(trash, exist_ok=True)
            shutil.copy2(full, os.path.join(trash, os.path.basename(rel)))
            with open(full, "wb") as f:
                f.write(out)
    finally:
        db.close()
    return n, before, after


def main() -> int:
    ap = argparse.ArgumentParser(description="存量图片回压（默认 dry-run）")
    ap.add_argument("--apply", action="store_true", help="真正写盘（默认只打印）")
    ap.add_argument(
        "--trash", default=DEFAULT_TRASH, help=f"原字节备份目录（默认 {DEFAULT_TRASH}）"
    )
    args = ap.parse_args()

    root = os.path.abspath("uploads")
    print(f"uploads 根: {root}")
    print(f"模式: {'执行（原字节备份到 ' + args.trash + '）' if args.apply else '仅打印计划'}")

    from scripts.cleanup_uploads import collect_referenced  # 引用集**唯一实现**，不另写

    referenced, _missing = collect_referenced(root)

    print("\n【1/3】阅读圈卡片：重渲换名（PNG → JPEG）")
    n1, before1 = backfill_circle(root, args.apply)

    print("\n【2/3】报告图：删存量（端点按需重出）")
    n2, before2 = backfill_reports(root, args.apply)

    print("\n【3/3】上传图：原地重编码（只动 DB 引用中的）")
    n3, before3, after3 = backfill_uploads(root, referenced, args.apply, args.trash)

    print("\n===== 汇总 =====")
    _report("阅读圈卡片", before1, 0, f"{n1} 个旧文件（重渲后另出新文件）")
    _report("报告图", before2, 0, f"{n2} 个")
    _report("上传图（原地）", before3, after3, f"{n3} 个")
    if not args.apply:
        print("\n（未做任何改动；加 --apply 才执行）")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
