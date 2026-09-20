# backend/common/file_storage.py — 文件存储（本地磁盘 ADR-004 + 统一路径 R-316）
"""封面统一转 JPG；音频仅 MP3 并解析时长。存储根目录由 settings.UPLOADS_DIR。

**图片体积纪律（2026-09-17 用户裁定）**：服务器磁盘有限，运营上传多大都必须在服务端
**自动压缩**，目标是"小程序端看得清即够"。所有上传图一律走 `normalize_image()`
（EXIF 摆正 → 长边限幅 → JPEG → 体积兜底降质），数值全部来自 SystemConfig 不写魔数。
"""

from __future__ import annotations

import os
import secrets
import struct
from dataclasses import dataclass
from io import BytesIO

from backend.config import get_settings

ALLOWED_COVER_EXTS = {".jpg", ".jpeg", ".png", ".webp"}

#: 上传图解码前的像素上限（防解压炸弹：header 即知其尺寸，超限直接拒，不进解码）
MAX_DECODE_PIXELS = 50_000_000


def _uploads_root() -> str:
    root = get_settings().UPLOADS_DIR
    os.makedirs(root, exist_ok=True)
    return root


# ---------------- 图片体积策略（数值全配置化：宪法 §〇 铁律 6） ----------------


@dataclass(frozen=True)
class ImagePolicy:
    """一类上传图的体积口径。"""

    #: 长边上限（px）——超过才缩放，不放大
    max_edge: int
    #: JPEG 质量（1-95）
    quality: int
    #: 单文件输入上限（字节）：超过直接 422，不读进内存
    max_input_bytes: int
    #: 输出上限（字节）：达标前逐档降质/缩边
    max_output_bytes: int


#: 场景 → (长边配置键, 输出上限配置键)；质量统一取 image_jpeg_quality
_POLICY_KEYS = {
    "cover": ("image_cover_max_edge", 1080),
    "activity_cover": ("image_activity_cover_max_edge", 1200),
    # 活动图文详情/往期回顾的配图：与活动封面同档（横竖混合、手机端满宽展示）
    "activity_detail": ("image_activity_cover_max_edge", 1200),
    "doc": ("image_doc_max_edge", 1600),  # 收款凭证 / 观察报告：文档类，要看清小字
}


def read_image_policy(db, scope: str) -> ImagePolicy:
    """按场景读图片体积口径（ConfigService 带 60s 缓存）。"""
    from backend.common.config_service import ConfigService

    if scope not in _POLICY_KEYS:
        raise ValueError(f"未知图片场景: {scope}")
    edge_key, edge_default = _POLICY_KEYS[scope]
    cfg = ConfigService(db)
    return ImagePolicy(
        max_edge=cfg.get_int(edge_key, edge_default),
        quality=cfg.get_int("image_jpeg_quality", 85),
        max_input_bytes=cfg.get_int("image_upload_max_mb", 8) * 1024 * 1024,
        max_output_bytes=cfg.get_int("image_upload_max_output_kb", 600) * 1024,
    )


def generated_jpeg_quality(db=None, default: int = 85) -> int:
    """生成图（阅读圈卡片 / 周报月报）的 JPEG 质量。无 db 上下文（seed/脚本）时用默认值。"""
    if db is None:
        return default
    from backend.common.config_service import ConfigService

    return ConfigService(db).get_int("image_generated_jpeg_quality", default)


def ensure_upload_within_limit(file, policy: ImagePolicy) -> None:
    """解码前拦超大上传：Starlette 已填 `file.size`，不必先读进内存。

    体积硬上限是**防呆**（运营误传上百 MB 的原图/PDF），不是防攻击——上传端点本身要权限。
    """
    size = getattr(file, "size", None)
    if size is not None and size > policy.max_input_bytes:
        from backend.common.exceptions import ValidationError

        raise ValidationError(
            f"图片体积超限（{size / 1024 / 1024:.1f}MB > "
            f"{policy.max_input_bytes / 1024 / 1024:.0f}MB），请先压缩再传"
        )


def normalize_image(
    data: bytes,
    *,
    max_edge: int,
    quality: int,
    max_input_bytes: int | None = None,
    max_output_bytes: int | None = None,
) -> bytes:
    """上传图统一规范化 → JPEG 字节（唯一入口，禁各处自己 `img.save`）。

    1. **EXIF 摆正**：手机竖拍照片带 Orientation 标记，不校正会被存成躺着的（历史缺陷）；
    2. **长边限幅**：`max_edge` 内不缩放（不放大、不损清晰度），超出才 LANCZOS 缩；
    3. **JPEG 输出**：`optimize` + `progressive`（体积优先，小程序端友好）；
    4. **体积兜底**：给了 `max_output_bytes` 就逐档降质（-5/-10/-15），仍超再缩边 ×0.8，最多 3 轮。

    解码前先按 **header 尺寸**拒绝超大图（像素上限），避免巨图解码打爆内存。
    """
    from PIL import Image, ImageOps

    from backend.common.exceptions import ValidationError

    if max_input_bytes is not None and len(data) > max_input_bytes:
        raise ValidationError(
            f"图片体积超限（{len(data) / 1024 / 1024:.1f}MB > {max_input_bytes / 1024 / 1024:.0f}MB）"
        )
    try:
        img = Image.open(BytesIO(data))
        img.load()
    except Exception as e:  # noqa: BLE001 — Pillow 异常类型多，统一转业务异常
        raise ValidationError("图片文件无法解析") from e
    if img.width * img.height > MAX_DECODE_PIXELS:
        raise ValidationError(f"图片像素过大（{img.width}x{img.height}），请先自行压缩")
    try:
        img = ImageOps.exif_transpose(img) or img
        img = img.convert("RGB")
    except Exception as e:  # noqa: BLE001
        raise ValidationError("图片文件无法解析") from e

    def _fit(source, edge: int):
        if max(source.size) <= edge:
            return source
        scale = edge / max(source.size)
        return source.resize(
            (max(1, int(source.width * scale)), max(1, int(source.height * scale))),
            Image.LANCZOS,
        )

    def _encode(source, q: int) -> bytes:
        buf = BytesIO()
        source.save(buf, "JPEG", quality=q, optimize=True, progressive=True)
        return buf.getvalue()

    out = _encode(_fit(img, max_edge), quality)
    if max_output_bytes is None or len(out) <= max_output_bytes:
        return out
    for step in (5, 10, 15):
        q = max(40, quality - step)
        out = _encode(_fit(img, max_edge), q)
        if len(out) <= max_output_bytes:
            return out
    edge = max_edge
    for _ in range(3):  # 极噪图（截图/扫描件）降质仍超标 → 继续缩边
        edge = int(edge * 0.8)
        out = _encode(_fit(img, edge), max(40, quality - 15))
        if len(out) <= max_output_bytes:
            return out
    return out


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


def _check_ext(ext: str, what: str) -> None:
    ext = ext.lower()
    if ext and ext not in ALLOWED_COVER_EXTS:
        from backend.common.exceptions import ValidationError

        raise ValidationError(f"{what}格式仅支持 JPG/JPEG/PNG/WebP: {ext}")


def save_cover_jpg(book, data: bytes, ext: str, policy: ImagePolicy) -> str:
    """封面存储：规范化 JPEG（长边 ≤ policy.max_edge）；路径 cover/{isbn前4位}/{code}_{token}.jpg。"""
    _check_ext(ext, "封面")
    rel = (
        os.path.join("cover", book.isbn[:4], f"{book.isbn}_{secrets.token_hex(6)}.jpg")
        if book.isbn
        else os.path.join("cover", "local", f"{book.book_code}_{secrets.token_hex(6)}.jpg")
    )
    abs_path = os.path.join(_uploads_root(), rel)
    os.makedirs(os.path.dirname(abs_path), exist_ok=True)
    with open(abs_path, "wb") as f:
        f.write(
            normalize_image(
                data,
                max_edge=policy.max_edge,
                quality=policy.quality,
                max_input_bytes=policy.max_input_bytes,
                max_output_bytes=policy.max_output_bytes,
            )
        )
    return rel.replace(os.sep, "/")


def save_activity_cover_jpg(activity_id: int, data: bytes, ext: str, policy: ImagePolicy) -> str:
    """T45（FEAT-082）：活动封面存储——规范化 JPEG；路径 cover/activity/{id}_{hex}.jpg。"""
    _check_ext(ext, "封面")
    rel = os.path.join("cover", "activity", f"{activity_id}_{secrets.token_hex(6)}.jpg")
    abs_path = os.path.join(_uploads_root(), rel)
    os.makedirs(os.path.dirname(abs_path), exist_ok=True)
    with open(abs_path, "wb") as f:
        f.write(
            normalize_image(
                data,
                max_edge=policy.max_edge,
                quality=policy.quality,
                max_input_bytes=policy.max_input_bytes,
                max_output_bytes=policy.max_output_bytes,
            )
        )
    return rel.replace(os.sep, "/")


def save_activity_detail_image(activity_id: int, data: bytes, ext: str, policy: ImagePolicy) -> str:
    """活动图文详情配图存储（2026-09-20 B 批）：规范化 JPEG；路径 activity_detail/{id}_{token}.jpg。

    独立目录而非塞进 `cover/`：`cover/` 是清理脚本的**可再生目录**白名单，图集是运营上传的
    不可再生内容，混进去迟早被当孤儿删（`cleanup_uploads.py` 的教训 E-20260915-27 同族）。
    """
    _check_ext(ext, "配图")
    rel = os.path.join("activity_detail", f"{activity_id}_{secrets.token_hex(6)}.jpg")
    abs_path = os.path.join(_uploads_root(), rel)
    os.makedirs(os.path.dirname(abs_path), exist_ok=True)
    with open(abs_path, "wb") as f:
        f.write(
            normalize_image(
                data,
                max_edge=policy.max_edge,
                quality=policy.quality,
                max_input_bytes=policy.max_input_bytes,
                max_output_bytes=policy.max_output_bytes,
            )
        )
    return rel.replace(os.sep, "/")


def remove_activity_detail_images(rels: list[str]) -> int:
    """删除被移出图文的配图（编排层传入"新块集合里不再出现"的路径）。

    只删 `activity_detail/` 下的文件且路径必须落在 uploads 内（防路径穿越，同 delete_voice_files 口径）。
    返回值 = 实际删除数。删除失败不抛（孤儿留给清理脚本兜底，不能因为删文件失败让编辑失败）。
    """
    root = os.path.abspath(_uploads_root())
    deleted = 0
    for rel in rels:
        full = os.path.abspath(os.path.join(root, rel or ""))
        if not full.startswith(root + os.sep):
            continue
        if not rel.replace(os.sep, "/").startswith("activity_detail/"):
            continue
        try:
            if os.path.isfile(full):
                os.remove(full)
                deleted += 1
        except OSError:  # pragma: no cover - 删不掉不算错误
            continue
    return deleted


def save_voucher_jpg(order_no: str, data: bytes, ext: str, policy: ImagePolicy) -> str:
    """收款凭证存储（WM3-B2）：规范化 JPEG（凭证要放大看小字 → 独立 doc 口径）；
    路径 voucher/{order_no}_{token}.jpg（订单号便于归档追溯）。"""
    _check_ext(ext, "凭证")
    rel = os.path.join("voucher", f"{order_no}_{secrets.token_hex(6)}.jpg")
    abs_path = os.path.join(_uploads_root(), rel)
    os.makedirs(os.path.dirname(abs_path), exist_ok=True)
    with open(abs_path, "wb") as f:
        f.write(
            normalize_image(
                data,
                max_edge=policy.max_edge,
                quality=policy.quality,
                max_input_bytes=policy.max_input_bytes,
                max_output_bytes=policy.max_output_bytes,
            )
        )
    return rel.replace(os.sep, "/")


def save_observation_image(child_id: int, data: bytes, ext: str, policy: ImagePolicy) -> str:
    """观察期评估报告图（WM10/FEAT-066）：**2026-09-17 起走统一管线**。

    此前是 `open(...,"wb")` 原始字节直存 → 运营传 9 张手机原图可落几十 MB。
    现与凭证同口径（doc 类：家长端要看清报告小字），路径 observation/child_{id}/{uuid}.jpg。
    """
    _check_ext(ext, "图片")
    rel_dir = os.path.join("observation", f"child_{child_id}")
    out_dir = os.path.join(_uploads_root(), rel_dir)
    os.makedirs(out_dir, exist_ok=True)
    name = f"{secrets.token_hex(16)}.jpg"
    with open(os.path.join(out_dir, name), "wb") as f:
        f.write(
            normalize_image(
                data,
                max_edge=policy.max_edge,
                quality=policy.quality,
                max_input_bytes=policy.max_input_bytes,
                max_output_bytes=policy.max_output_bytes,
            )
        )
    return os.path.join(rel_dir, name).replace(os.sep, "/")


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
