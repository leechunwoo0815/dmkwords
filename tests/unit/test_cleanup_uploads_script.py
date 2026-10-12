# tests/unit/test_cleanup_uploads_script.py — 清理脚本的回收站**布局契约**（2026-10-11）
"""守的是一件事：脚本搬进回收站的文件，媒体体检必须能**按原路径解析回来**。

2026-10-11 实测踩到：`--trash <uploads>/.trash` 直接铺开（少一层批次目录）时，
`media_service.trash_files_on_disk()` 会把 `cover/9780/x.jpg` 解析成
批次=`cover` / 原路径=`9780/x.jpg` → 面板"还原"把文件搬到 `uploads/9780/x.jpg`（错位、图片全挂）。

布局契约：`<uploads>/.trash/<批次>/<原相对路径>`。
"""

from __future__ import annotations

import os
import re

from backend.config import get_settings
from backend.database import SessionLocal
from backend.domain.admin.media_service import MediaHealthService
from backend.domain.catalog.models import Book


def _plant(root: str, rel: str, size: int = 1024) -> str:
    full = os.path.join(root, rel)
    os.makedirs(os.path.dirname(full), exist_ok=True)
    with open(full, "wb") as fh:
        fh.write(b"\x00" * size)
    return full


def test_script_trash_keeps_batch_level_and_service_parses_rel(tmp_path, monkeypatch):
    """`--trash <uploads>/.trash` → 自动建批次目录；体检解析出的原路径 = 完整相对路径。"""
    import sys

    from scripts import cleanup_uploads

    root = tmp_path / "uploads"
    root.mkdir()
    orphan = _plant(str(root), "cover/8888/orphan_script.jpg", 2048)
    used = _plant(str(root), "cover/8888/used_script.jpg", 4096)

    # 引用集来自 DB：造一条指向 used 的书目（软删也算引用，脚本口径）
    db = SessionLocal()
    db.add(Book(title="脚本布局用例", cover_path="cover/8888/used_script.jpg"))
    db.commit()
    db.close()

    monkeypatch.setattr(get_settings(), "UPLOADS_DIR", str(root))
    monkeypatch.setattr(sys, "argv", ["cleanup_uploads.py", "--trash", str(root / ".trash")])
    assert cleanup_uploads.main() == 0

    # ① 原路径腾空、被引用的文件原地不动
    assert not os.path.exists(orphan)
    assert os.path.isfile(used)

    # ② 落到 `<.trash>/<批次>/cover/8888/orphan_script.jpg`（批次是时间戳目录）
    trashed = [
        os.path.join(dirpath, fn)
        for dirpath, _d, files in os.walk(str(root / ".trash"))
        for fn in files
    ]
    assert len(trashed) == 1, trashed
    rel_from_trash = os.path.relpath(trashed[0], str(root / ".trash"))
    parts = rel_from_trash.split(os.sep)
    assert re.fullmatch(r"\d{8}-\d{6}", parts[0]), f"第一段必须是批次目录：{rel_from_trash}"
    assert "/".join(parts[1:]) == "cover/8888/orphan_script.jpg", rel_from_trash

    # ③ 媒体体检按同一布局解析（这是本用例的靶心：旧布局会解析成 `8888/orphan_script.jpg`）
    db = SessionLocal()
    try:
        rows = MediaHealthService(db).trash_files_on_disk()
    finally:
        db.close()
    assert [r[1] for r in rows] == ["cover/8888/orphan_script.jpg"], rows
    assert rows[0][0] == parts[0]
