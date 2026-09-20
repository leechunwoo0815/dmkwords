# backend/common/file_utils.py
"""本地文件工具 — 语音录音等上传文件的物理删除"""

import logging
import os
from pathlib import Path

logger = logging.getLogger(__name__)

PROJECT_DIR = Path(__file__).resolve().parent.parent.parent


def delete_voice_files(audio_urls: list[str], base_dir: Path | None = None) -> int:
    """根据 audio_url 列表删除本地音频文件。

    audio_url 可能是相对路径 (uploads/voice/xxx.wav)、绝对路径 (/uploads/...)
    或远程 URL（http 开头，跳过）。base_dir 供测试注入。
    F-024：任何路径 resolve 后必须位于 uploads 根内（deletion_service 同款防御，
    此处为同类漏改补全）——防 audio_url 含 ../ 逃逸删除任意文件。
    """
    root = base_dir or PROJECT_DIR
    uploads_root = (root / "uploads").resolve()
    deleted_count = 0
    for audio_url in audio_urls:
        if audio_url.startswith("uploads/"):
            file_path = root / audio_url
        elif audio_url.startswith("/uploads/"):
            file_path = root / audio_url[1:]
        elif audio_url.startswith("http"):
            continue  # 远程 URL，跳过本地文件删除
        else:
            file_path = root / "uploads" / "voice" / audio_url

        resolved = file_path.resolve()
        if not str(resolved).startswith(str(uploads_root) + os.sep):
            logger.warning(f"Blocked voice path traversal: {audio_url}")
            continue
        if resolved.is_file():
            resolved.unlink()
            deleted_count += 1

    return deleted_count


def media_version(cover_path: str | None) -> str:
    """从封面/媒体路径取版本 token（文件名末尾的随机段）。"""
    if not cover_path:
        return ""
    return os.path.splitext(os.path.basename(cover_path))[0].rsplit("_", 1)[-1]


#: 媒体响应 MIME 映射（2026-09-17：生成图由 PNG 改 JPEG，端点原先硬编码 image/png → 必须按路径判定）
#: 2026-09-20：由私有 `_IMAGE_MEDIA_TYPES` 改名公开，活动图文端点需要复用同一份映射
IMAGE_MEDIA_TYPES = {
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".png": "image/png",
    ".webp": "image/webp",
}


def image_media_type(path: str) -> str:
    """按文件扩展名给媒体响应的 Content-Type（未知扩展名兜底 image/jpeg）。"""
    return IMAGE_MEDIA_TYPES.get(os.path.splitext(path or "")[1].lower(), "image/jpeg")


def book_cover_url(book_id: int, cover_path: str | None) -> str | None:
    """书籍封面 URL（带 v 版本参数）。

    [Why] 2026-09-15 实测：活动封面已改成横版重生成，小程序 `<image>` 仍显示旧竖版
    裁切图 —— `<image>` 按 **URL** 缓存，URL 不变就永远吃旧的。管理端早有
    「重传后带 v 参数」的处置（LEDGER admin-web-fix 行），小程序端一直缺。
    版本号取 cover_path 文件名的随机 token：重生成必换 token → URL 必变 → 必然刷新。
    """
    if not cover_path:
        return None
    return f"/api/miniapp/covers/{book_id}?v={media_version(cover_path)}"


def activity_cover_url(activity_id: int, cover_path: str | None) -> str | None:
    """活动封面 URL（带 v 版本参数，同 book_cover_url 的理由）。"""
    if not cover_path:
        return None
    return f"/api/miniapp/activities/{activity_id}/cover?v={media_version(cover_path)}"


def activity_detail_image_url(activity_id: int, path: str | None) -> str | None:
    """活动图文详情配图 URL（带 v 版本参数，同 book_cover_url / activity_cover_url 的理由）。

    图片经 `/activities/{id}/detail-image?name=` 端点出（端点只接受 basename，
    路径穿越在端点侧再校验一次），版本 token 取文件名随机段 → 换图必换 URL。
    """
    if not path:
        return None
    name = os.path.basename(path)
    return f"/api/miniapp/activities/{activity_id}/detail-image?name={name}&v={media_version(path)}"
