"""activity 域 schemas。

图文详情块（2026-09-20 客户需求「像公众号一样」）的结构与边界口径集中在这里——
管理端写入、小程序读取、清理脚本对账三处都用同一份常量，避免"三处各定一套"。
"""

from __future__ import annotations

from pydantic import Field

from backend.common.base_schema import BaseSchema

#: 块类型（用户拍板：只做"段落块 / 图片块"两种，不引富文本）
BLOCK_PARAGRAPH = "paragraph"
BLOCK_IMAGE = "image"
BLOCK_TYPES = (BLOCK_PARAGRAPH, BLOCK_IMAGE)

#: 边界（管理端 422 拦截；上限存在的理由是手机端渲染性能与磁盘，不是技术限制）
MAX_BLOCKS = 60
MAX_IMAGES = 30
MAX_PARAGRAPH_LEN = 1000
MAX_CAPTION_LEN = 60

#: 配图落盘目录（与 `file_storage.save_activity_detail_image` 一致；服务端校验路径必须在此前缀下）
DETAIL_IMAGE_DIR = "activity_detail/"


class DetailBlock(BaseSchema):
    """一个图文块：`paragraph` 用 text；`image` 用 path（相对 uploads）+ 可选 caption。"""

    type: str = Field(..., description="paragraph / image")
    text: str | None = Field(None, max_length=MAX_PARAGRAPH_LEN, description="段落正文")
    path: str | None = Field(None, max_length=255, description="图片相对路径（image 块必填）")
    caption: str | None = Field(None, max_length=MAX_CAPTION_LEN, description="图注（可选）")


class ActivityDetailBlocksRequest(BaseSchema):
    """图文详情全量覆盖写（顺序即展示顺序）。"""

    blocks: list[DetailBlock] = Field(default_factory=list, max_length=MAX_BLOCKS)
