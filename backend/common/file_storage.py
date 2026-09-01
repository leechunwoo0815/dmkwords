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


def save_voucher_jpg(order_no: str, data: bytes, ext: str) -> str:
    """收款凭证存储（WM3-B2）：统一转 JPG（Pillow 对齐封面口径）；
    路径 voucher/{order_no}_{token}.jpg（订单号便于归档追溯）。"""
    ext = ext.lower()
    if ext and ext not in ALLOWED_COVER_EXTS:
        from backend.common.exceptions import ValidationError

        raise ValidationError(f"凭证格式仅支持 JPG/JPEG/PNG/WebP: {ext}")
    from io import BytesIO

    from PIL import Image

    try:
        img = Image.open(BytesIO(data))
        img = img.convert("RGB")
    except Exception as e:  # noqa: BLE001 — Pillow 异常类型多，统一转业务异常
        from backend.common.exceptions import ValidationError

        raise ValidationError("凭证文件无法解析为图片") from e
    rel = os.path.join("voucher", f"{order_no}_{secrets.token_hex(6)}.jpg")
    abs_path = os.path.join(_uploads_root(), rel)
    os.makedirs(os.path.dirname(abs_path), exist_ok=True)
    img.save(abs_path, "JPEG", quality=88)
    return rel.replace(os.sep, "/")


def _mp3_duration(data: bytes) -> int:
    """粗略解析 MP3 时长（秒）：优先 Xing/Info 头帧数，否则按首帧比特率估算。
    支持 MPEG1/MPEG2/MPEG2.5 Layer III——lame 低采样率输出是 MPEG2（帧头 fff3），
    只解析 MPEG1 时返回 0，会把真实几秒的音频兜底成 60s（完播判定永远不可达）。"""

    def _frames_to_seconds(frame_count: int, samples: int, sample_rate: int) -> int:
        return int(frame_count * samples / sample_rate) if sample_rate else 0

    # 逐字节找 11 位帧同步字（\xff 后高 3 位为 1），兼容 ID3 头在前
    idx = -1
    for i in range(len(data) - 4):
        if data[i] == 0xFF and (data[i + 1] & 0xE0) == 0xE0:
            idx = i
            break
    if idx == -1:
        return 0
    b1, b2 = data[idx + 1], data[idx + 2]
    version = (b1 >> 3) & 0x03  # 3=MPEG1, 2=MPEG2, 0=MPEG2.5
    layer = (b1 >> 1) & 0x03  # 1=Layer III
    bitrate_idx = (b2 >> 4) & 0x0F
    sr_idx = (b2 >> 2) & 0x03
    if layer != 1 or version == 1 or bitrate_idx == 0 or bitrate_idx == 15 or sr_idx == 3:
        return 0
    if version == 3:  # MPEG1
        bitrate_table = [0, 32, 40, 48, 56, 64, 80, 96, 112, 128, 160, 192, 224, 256, 320, 0]
        sample_rates = [44100, 48000, 32000]
        samples_per_frame = 1152
    else:  # MPEG2 / MPEG2.5：低速率表、576 samples、采样率减半/再减半
        bitrate_table = [0, 8, 16, 24, 32, 40, 48, 56, 64, 80, 96, 112, 128, 144, 160, 0]
        sample_rates = {2: [22050, 24000, 16000], 0: [11025, 12000, 8000]}[version]
        samples_per_frame = 576
    sample_rate = sample_rates[sr_idx]
    # Xing/Info 检测（VBR 头在首帧内；帧最大 ~1440B，取 200 余量足够）
    xing = data.find(b"Xing", idx + 4)
    if xing == -1:
        xing = data.find(b"Info", idx + 4)
    if xing != -1 and xing - idx < 200:
        flags = struct.unpack(">I", data[xing + 4 : xing + 8])[0]
        if flags & 0x01:  # frames flag
            frames = struct.unpack(">I", data[xing + 8 : xing + 12])[0]
            return _frames_to_seconds(frames, samples_per_frame, sample_rate)
    # CBR 估算：从同步字起算（剔除前面的 ID3 头）
    bitrate = bitrate_table[bitrate_idx] * 1000
    if bitrate > 0:
        return int((len(data) - idx) * 8 / bitrate)
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
