# backend/common/exceptions.py
"""
[What] 统一业务异常体系
[Why] 当前所有 service 都 raise ValueError，Router 里 try/except ValueError 转 HTTPException
[How] BusinessException 体系 + FastAPI 全局异常处理器

架构意图：
  - 异常自带状态码语义：NotFoundError=404, ForbiddenError=403, ConflictError=409
  - 全局异常处理器统一转换，Router 层不再需要 try/except
  - ValueError 是 Python 内置异常，容易被意外捕获，BusinessException 不会被误捕

约束：
  - Service 层只抛 BusinessException 体系的异常
  - Router 层不写 try/except（除非需要特殊的错误响应格式）
  - 新增异常类型必须继承 BusinessException
"""

from fastapi import Request
from fastapi.responses import JSONResponse


class BusinessException(Exception):
    """业务异常基类

    所有业务异常必须继承此类。
    全局异常处理器会将其转换为对应的 HTTP 响应。
    """

    def __init__(
        self,
        message: str = "业务异常",
        code: int = 400,
        error_code: str | None = None,
    ):
        self.message = message
        self.code = code
        self.error_code = error_code
        super().__init__(self.message)


class NotFoundError(BusinessException):
    """资源不存在 (404)"""

    def __init__(self, message: str = "资源不存在"):
        super().__init__(message, 404)


class ForbiddenError(BusinessException):
    """权限不足 (403)

    error_code: 可选结构化错误码（如 consent_required），前端据此触发对应交互
    """

    def __init__(self, message: str = "权限不足", error_code: str | None = None):
        super().__init__(message, 403, error_code=error_code)


class ConflictError(BusinessException):
    """数据冲突 (409) — 重复创建、状态冲突等"""

    def __init__(self, message: str = "数据冲突"):
        super().__init__(message, 409)


class ValidationError(BusinessException):
    """参数校验失败 (422) — 业务层面的校验，非 Pydantic 校验"""

    def __init__(self, message: str = "参数校验失败"):
        super().__init__(message, 422)


class PaymentError(BusinessException):
    """支付相关错误 (402)"""

    def __init__(self, message: str = "支付失败"):
        super().__init__(message, 402)


class RateLimitError(BusinessException):
    """请求频率超限 (429)"""

    def __init__(self, message: str = "请求过于频繁，请稍后再试"):
        super().__init__(message, 429)


class OwnershipError(BusinessException):
    """归属权校验失败 (403) — 专用于 ownership 层"""

    def __init__(self, message: str = "无权操作该资源"):
        super().__init__(message, 403)


class BadRequestError(BusinessException):
    """参数错误 (400)"""

    def __init__(self, message: str = "请求参数错误"):
        super().__init__(message, 400)


class UnauthorizedError(BusinessException):
    """未认证 (401)"""

    def __init__(self, message: str = "未认证"):
        super().__init__(message, 401)


async def business_exception_handler(request: Request, exc: BusinessException) -> JSONResponse:
    """
    FastAPI 全局异常处理器

    注册方式（在 main.py 中）：
      app.add_exception_handler(BusinessException, business_exception_handler)

    效果：
      Service 层 raise NotFoundError("订单不存在")
      → 自动转换为 HTTP 404 {"detail": "订单不存在"}
    """
    content: dict = {"detail": exc.message}
    if exc.error_code is not None:
        content["error_code"] = exc.error_code
    return JSONResponse(
        status_code=exc.code,
        content=content,
    )
