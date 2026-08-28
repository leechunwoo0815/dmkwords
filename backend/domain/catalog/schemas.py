# backend/domain/catalog/schemas.py — catalog 域 API Schema
import re

from pydantic import Field, field_validator

from backend.common.base_schema import BaseSchema, PaginatedResponse

# R7：AR 值三路统一校验（schema/导入/前端共用同一规则）
# 上限 12.9 = 美国常规学校标准最高值（用户 2026-08-28 裁定）
AR_LEVEL_RE = re.compile(r"^\d+(\.\d+)?$")
AR_LEVEL_MAX = 12.9


def validate_ar_level(value: str | None) -> str | None:
    """空/None 放行（AR 可后补）；否则校验格式与范围，非法抛 ValueError → 422。"""
    if value is None:
        return value
    s = value.strip()
    if not s:
        return None
    if not AR_LEVEL_RE.match(s):
        raise ValueError(f"AR 值格式不正确（{s}），需为 0-{AR_LEVEL_MAX} 的数字")
    if float(s) > AR_LEVEL_MAX:
        raise ValueError(f"AR 值超出范围（{s}），上限 {AR_LEVEL_MAX}")
    return s


class BookCreateRequest(BaseSchema):
    isbn: str | None = Field(None, max_length=20, description="ISBN；无 ISBN 书目传空")
    title: str = Field(..., min_length=1, max_length=200)
    author: str = Field("", max_length=100)
    # P2-12：word_count 是 WM7 词数入账依据，0 词书目无业务意义（用户 2026-08-28 裁定 min 1）
    word_count: int = Field(..., ge=1)
    ar_level: str | None = Field(None, max_length=10)
    topic: str = Field("", max_length=50)
    grade: str = Field("", max_length=50)
    description: str | None = Field(None, max_length=2000)
    copy_count: int = Field(1, ge=1, le=99, description="入库副本数（默认1）")

    @field_validator("ar_level")
    @classmethod
    def _ar_level_ok(cls, v: str | None) -> str | None:
        return validate_ar_level(v)


class BookUpdateRequest(BaseSchema):
    isbn: str | None = Field(None, max_length=20, description="ISBN；可后补或修改")
    title: str = Field(..., min_length=1, max_length=200)
    author: str = Field("", max_length=100)
    word_count: int = Field(..., ge=1)
    ar_level: str | None = Field(None, max_length=10)
    topic: str = Field("", max_length=50)
    grade: str = Field("", max_length=50)
    description: str | None = Field(None, max_length=2000)

    @field_validator("ar_level")
    @classmethod
    def _ar_level_ok(cls, v: str | None) -> str | None:
        return validate_ar_level(v)


class BookResponse(BaseSchema):
    id: int
    isbn: str | None
    internal_code: str | None
    title: str
    author: str
    cover_path: str | None
    audio_path: str | None
    audio_duration_seconds: int | None
    word_count: int
    ar_level: str | None
    topic: str
    grade: str
    description: str | None
    status: int
    copy_count: int = Field(0, description="在册副本总数")
    question_count: int = Field(0, description="测验题目数量（含停用）")
    # P2-5：启用题数——与「测验未满 5 道」Tab 同口径（is_active=1）
    question_active_count: int = Field(0, description="启用题目数量")


class BookListQuery(BaseSchema):
    page: int = Field(1, ge=1)
    page_size: int = Field(20, ge=1, le=100)
    keyword: str | None = Field(None, max_length=100, description="书名/作者/ISBN 模糊")
    ar_pending: bool = Field(False, description="AR 待配置筛选")
    status: int | None = Field(None, description="上下架筛选")
    no_cover: bool = Field(False, description="未传封面筛选")
    no_audio: bool = Field(False, description="未传音频筛选")
    quiz_incomplete: bool = Field(False, description="测验题目少于 5 道筛选")


class CopyResponse(BaseSchema):
    id: int
    book_id: int
    copy_code: str
    status: str


class CopyStatusUpdateRequest(BaseSchema):
    status: str = Field(..., description="新状态")
    reason: str = Field(..., min_length=1, max_length=200, description="操作原因（留痕）")


class QuizQuestionCreateRequest(BaseSchema):
    question_type: str = Field("single", pattern="^(single|boolean)$")
    question_text: str = Field(..., min_length=1, max_length=500)
    options: list[str] = Field(..., min_length=2, max_length=6)
    answer: str = Field(..., max_length=200)


class QuizQuestionUpdateRequest(BaseSchema):
    question_type: str = Field("single", pattern="^(single|boolean)$")
    question_text: str = Field(..., min_length=1, max_length=500)
    options: list[str] = Field(..., min_length=2, max_length=6)
    answer: str = Field(..., max_length=200)


class QuizQuestionResponse(BaseSchema):
    id: int
    book_id: int
    question_type: str
    question_text: str
    options: list[str]
    answer: str
    sort_order: int
    is_active: int

    @field_validator("options", mode="before")
    @classmethod
    def _parse_options(cls, v):
        if isinstance(v, str):
            import json as _json

            return _json.loads(v)
        return v


class BatchDeleteRequest(BaseSchema):
    ids: list[int] = Field(..., min_length=1, description="待删除书目 ID 列表")


class BatchToggleStatusRequest(BaseSchema):
    ids: list[int] = Field(..., min_length=1, description="待上下架书目 ID 列表")
    status: int = Field(..., ge=0, le=1, description="目标状态：1=上架，0=下架")


class ImportResultResponse(BaseSchema):
    total_rows: int
    success_count: int
    failed_count: int
    errors: list[str] = Field(default_factory=list, description="行号+原因")


class BookListResponse(PaginatedResponse[BookResponse]):
    """C3：图书列表响应 = 分页结构 + 7 个筛选 Tab 的计数。"""

    counts: dict[str, int] = Field(default_factory=dict, description="Tab 计数（与列表筛选同口径）")
