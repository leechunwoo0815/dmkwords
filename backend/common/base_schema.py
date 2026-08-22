# backend/common/base_schema.py
"""
[What] 标准 API 请求/响应 Schema 基类
[Why] 统一分页、响应格式，消除接口用 dict 返回的问题
[How] Pydantic V2 Generic 模型

架构意图：
  - PaginatedResponse[T]: 所有列表接口统一返回 {items, total, page, page_size, has_next}
  - ApiResponse[T]: 统一响应包装 {code, message, data}
  - PaginationRequest: 统一分页参数校验

约束：
  - 所有 API 响应必须使用 Pydantic Schema，禁止用 dict
  - 分页接口必须使用 PaginatedResponse
  - page_size 上限 100，防止一次查询太多数据
"""

from typing import Generic, TypeVar

from pydantic import BaseModel, ConfigDict, Field

T = TypeVar("T")


class PaginationRequest(BaseModel):
    """统一分页请求参数"""

    page: int = Field(1, ge=1, description="页码，从1开始")
    page_size: int = Field(20, ge=1, le=100, description="每页数量，上限100")


class PaginatedResponse(BaseModel, Generic[T]):
    """统一分页响应格式

    使用方式：
      @router.get("/", response_model=PaginatedResponse[BookResponse])
      async def list_books(...):
          ...
    """

    items: list[T] = Field(default_factory=list, description="数据列表")
    total: int = Field(0, ge=0, description="总数")
    page: int = Field(1, ge=1, description="当前页码")
    page_size: int = Field(20, ge=1, le=100, description="每页数量")
    has_next: bool = Field(False, description="是否有下一页")

    @classmethod
    def create(
        cls, items: list[T], total: int, page: int, page_size: int
    ) -> "PaginatedResponse[T]":
        """便捷构造方法"""
        return cls(
            items=items,
            total=total,
            page=page,
            page_size=page_size,
            has_next=(page * page_size) < total,
        )


class ApiResponse(BaseModel, Generic[T]):
    """统一响应包装

    使用方式：
      return ApiResponse(data=result)
      return ApiResponse(code=0, message="success", data=result)
    """

    code: int = Field(0, description="状态码，0=成功")
    message: str = Field("success", description="状态信息")
    data: T | None = Field(None, description="响应数据")


class BaseSchema(BaseModel):
    """Schema 公共基类

    所有 Schema 继承此类，自动配置：
      - from_attributes=True：支持从 ORM 对象直接转换
      - populate_by_name=True：支持按字段名或别名赋值
      - extra="forbid"：禁止传入未定义的字段，增强数据验证
    """

    model_config = ConfigDict(
        from_attributes=True,
        populate_by_name=True,
        extra="forbid",
    )


class IdResponse(BaseModel):
    """仅返回 ID 的响应（创建成功等场景）"""

    id: int = Field(..., description="资源ID")
