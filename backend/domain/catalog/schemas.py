# backend/domain/catalog/schemas.py — catalog 域 API Schema
from pydantic import Field, field_validator

from backend.common.base_schema import BaseSchema


class BookCreateRequest(BaseSchema):
    isbn: str | None = Field(None, max_length=20, description="ISBN；无 ISBN 书目传空")
    title: str = Field(..., min_length=1, max_length=200)
    author: str = Field("", max_length=100)
    word_count: int = Field(..., ge=0)
    ar_level: str | None = Field(None, max_length=10)
    topic: str = Field("", max_length=50)
    grade: str = Field("", max_length=50)
    description: str | None = Field(None, max_length=2000)
    copy_count: int = Field(1, ge=1, le=99, description="入库副本数（默认1）")


class BookUpdateRequest(BaseSchema):
    isbn: str | None = Field(None, max_length=20, description="ISBN；可后补或修改")
    title: str = Field(..., min_length=1, max_length=200)
    author: str = Field("", max_length=100)
    word_count: int = Field(..., ge=0)
    ar_level: str | None = Field(None, max_length=10)
    topic: str = Field("", max_length=50)
    grade: str = Field("", max_length=50)
    description: str | None = Field(None, max_length=2000)


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
    question_count: int = Field(0, description="测验题目数量")


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


class ImportResultResponse(BaseSchema):
    total_rows: int
    success_count: int
    failed_count: int
    errors: list[str] = Field(default_factory=list, description="行号+原因")
