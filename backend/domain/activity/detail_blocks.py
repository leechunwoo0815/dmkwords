"""活动图文详情块（2026-09-20 客户需求「像公众号一样」）——**序列化的单一来源**。

从 `service.py` 拆出（可读性 + god-file 800 行上限）：管理端编辑器、小程序渲染、seed 演示数据
三处都要解析同一份 JSON，各写一份必然漂移（同 `children_payload` 单出口纪律）。
"""

from __future__ import annotations

import json
import os
from typing import Any

from backend.common.exceptions import NotFoundError
from backend.common.file_utils import activity_detail_image_url

#: 块类型与落盘目录（与 `schemas` / `file_storage` 同源；改这里必须同步那两处）
BLOCK_PARAGRAPH = "paragraph"
BLOCK_IMAGE = "image"
DETAIL_IMAGE_DIR = "activity_detail/"


def raw_blocks(activity: Any) -> list[dict]:
    """原始块（管理端编辑器 / seed 用）：与落库 JSON 同构，脏数据降级为空列表。"""
    try:
        raw = json.loads(getattr(activity, "detail_blocks", None) or "[]")
    except (TypeError, ValueError):
        return []
    return [b for b in raw if isinstance(b, dict)] if isinstance(raw, list) else []


def blocks_view(activity: Any) -> list[dict]:
    """小程序可渲染结构：图片块转成带 token 的 URL，空段落与脏块丢弃。

    脏数据一律降级为空（`card_data` 同款口径）：绝不因为一块脏数据让家长打不开活动详情。
    """
    out: list[dict] = []
    for b in raw_blocks(activity):
        if b.get("type") == BLOCK_IMAGE and b.get("path"):
            out.append(
                {
                    "type": BLOCK_IMAGE,
                    "image_url": activity_detail_image_url(activity.id, b["path"]),
                    "caption": b.get("caption") or "",
                }
            )
        elif b.get("type") == BLOCK_PARAGRAPH and str(b.get("text") or "").strip():
            out.append({"type": BLOCK_PARAGRAPH, "text": b["text"]})
    return out


def image_paths(activity: Any) -> set[str]:
    """当前图文里的配图相对路径集合（用于算"被移出"的图）。"""
    return {
        str(b["path"])
        for b in raw_blocks(activity)
        if b.get("type") == BLOCK_IMAGE and b.get("path")
    }


def resolve_image_file(activity_id: int, name: str) -> str:
    """把 URL 里的 `name` 解析成磁盘绝对路径（**管理端与小程序共用同一套校验**）。

    安全口径（任一条不满足即 404，绝不落到文件系统）：
      ① 只接受 basename（`name` 必须等于它自己的 basename，封死 `../` 与子目录）；
      ② 文件名必须形如「活动id_十六进制.ext」——前缀 = 归属校验（越权引用别家活动的图直接拒），
        且不带任何用户可控的目录拼接（目录由服务端写死为 activity_detail/）；
      ③ 扩展名白名单 + 字符白名单（字母数字下划线）；
      ④ 解析后的绝对路径必须仍在 uploads 根内。
    """
    from backend.config import get_settings

    base = name or ""
    stem, ext = os.path.splitext(base)
    ok = (
        base == os.path.basename(base)
        and base.startswith(f"{activity_id}_")
        and ext.lower() in (".jpg", ".jpeg", ".png")
        and 0 < len(stem) <= 64
        and all(c.isalnum() or c == "_" for c in stem)
    )
    if not ok:
        raise NotFoundError("配图不存在")
    root = os.path.abspath(get_settings().UPLOADS_DIR)
    full = os.path.abspath(os.path.join(root, DETAIL_IMAGE_DIR, base))
    if not full.startswith(root + os.sep) or not os.path.isfile(full):
        raise NotFoundError("配图文件不存在")
    return full
