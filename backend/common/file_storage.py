# backend/common/file_storage.py — 文件存储（本地磁盘 ADR-004 + 统一路径 R-316）
"""封面统一转 JPG；音频仅 MP3 并解析时长。存储根目录由 settings.UPLOADS_DIR。"""

from __future__ import annotations

import os
import secrets
import struct

from backend.config import get_settings

ALLOWED_COVER_EXTS = {".jpg", ".jpeg", ".png", ".webp"}


def _uploads_root() -> str:
    root = get_settings().UPLOADS_DIR
    os.makedirs(root, exist_ok=True)
    return root


def remove_book_media(cover_path: str | None, audio_path: str | None) -> None:
    """删除书目关联的媒体文件（软删联动清理）。路径穿越防护与 media 端点一致。"""
    root = os.path.abspath(get_settings().UPLOADS_DIR)
    for rel in (cover_path, audio_path):
        if not rel:
            continue
        full = os.path.abspath(os.path.join(root, rel))
        if not full.startswith(root):
            continue
        if os.path.isfile(full):
            try:
                os.remove(full)
            except OSError:
                pass


def save_cover_jpg(book, data: bytes, ext: str) -> str:
    """封面存储：统一转 JPG（Pillow）；路径 cover/{isbn前4位}/{code}.jpg；无 ISBN 走 local/。"""
    ext = ext.lower()
    if ext and ext not in ALLOWED_COVER_EXTS:
        from backend.common.exceptions import ValidationError

        raise ValidationError(f"封面格式仅支持 JPG/JPEG/PNG/WebP: {ext}")
    from io import BytesIO

    from PIL import Image

    try:
        img = Image.open(BytesIO(data))
        img = img.convert("RGB")
    except Exception as e:  # noqa: BLE001 — Pillow 异常类型多，统一转业务异常
        from backend.common.exceptions import ValidationError

        raise ValidationError("封面文件无法解析为图片") from e

    if book.isbn:
        rel = os.path.join("cover", book.isbn[:4], f"{book.isbn}_{secrets.token_hex(6)}.jpg")
    else:
        rel = os.path.join("cover", "local", f"{book.book_code}_{secrets.token_hex(6)}.jpg")
    abs_path = os.path.join(_uploads_root(), rel)
    os.makedirs(os.path.dirname(abs_path), exist_ok=True)
    img.save(abs_path, "JPEG", quality=88)
    return rel.replace(os.sep, "/")


def _mp3_duration(data: bytes) -> int:
    """粗略解析 MP3 时长（秒）：优先 Xing/Info 头帧数，否则按首帧比特率估算。"""

    def _frames_to_seconds(frame_count: int, samples: int, sample_rate: int) -> int:
        return int(frame_count * samples / sample_rate)

    # 查找第一帧同步字
    idx = data.find(b"\xff\xfb") if data.find(b"\xff\xfb") != -1 else data.find(b"\xff\xf3")
    if idx == -1:
        # 可能是 ID3+VBR，扫 Xing
        return 0
    header = data[idx : idx + 4]
    if len(header) < 4:
        return 0
    bitrate_table = [0, 32, 40, 48, 56, 64, 80, 96, 112, 128, 160, 192, 224, 256, 320, 0]
    sample_rates = [44100, 48000, 32000]
    version = (header[1] >> 3) & 0x03  # 3=MPEG1, 2=MPEG2
    layer = (header[1] >> 1) & 0x03  # 1=Layer III
    bitrate_idx = (header[2] >> 4) & 0x0F
    sr_idx = (header[2] >> 2) & 0x03
    if version == 3 and layer == 1 and 0 < bitrate_idx < 15 and sr_idx < 3:
        sample_rate = sample_rates[sr_idx]
        # Xing/Info 检测（VBR 头在帧数据开侧）
        frame_size = 144 * bitrate_table[bitrate_idx] * 1000 // sample_rate
        xing = data.find(b"Xing", idx + 4) if version == 3 else data.find(b"Info", idx + 4)
        if xing != -1 and xing - idx < frame_size:
            flags = struct.unpack(">I", data[xing + 4 : xing + 8])[0]
            if flags & 0x01:  # frames flag
                frames = struct.unpack(">I", data[xing + 8 : xing + 12])[0]
                return _frames_to_seconds(frames, 1152, sample_rate)
        # CBR 估算
        bitrate = bitrate_table[bitrate_idx] * 1000
        if bitrate > 0:
            return int(len(data) * 8 / bitrate)
    return 0


def save_audio_mp3(book, data: bytes) -> tuple[str, int]:
    """音频存储：book_audio/{code}/audio_{token}.mp3；返回 (相对路径, 时长秒)。
    C26：解析时长为 0 时拒绝——先解析后写盘（不入库不写文件）。"""
    duration = _mp3_duration(data)
    if duration <= 0:
        from backend.common.exceptions import ValidationError

        raise ValidationError("音频解析时长为 0，请检查文件")
    rel = os.path.join("book_audio", book.book_code, f"audio_{secrets.token_hex(6)}.mp3")
    abs_path = os.path.join(_uploads_root(), rel)
    os.makedirs(os.path.dirname(abs_path), exist_ok=True)
    with open(abs_path, "wb") as f:
        f.write(data)
    return rel.replace(os.sep, "/"), duration
