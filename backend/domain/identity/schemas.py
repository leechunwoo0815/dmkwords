# backend/domain/identity/schemas.py
from datetime import date, datetime

from pydantic import Field, field_validator

from backend.common.base_schema import BaseSchema


class ParentCreateRequest(BaseSchema):
    name: str = Field(..., min_length=1, max_length=64)
    phone: str = Field(..., pattern=r"^\d{11}$", description="手机号（11位）")
    remark: str = Field("", max_length=200)


class ParentResponse(BaseSchema):
    id: int
    name: str
    phone: str
    remark: str


class ChildCreateRequest(BaseSchema):
    name: str = Field(..., min_length=1, max_length=64)
    english_name: str | None = Field(None, max_length=64)
    gender: int | None = Field(None, ge=1, le=2)
    birthday: date | None = None
    grade: str = Field("", max_length=50)


class ChildUpdateRequest(BaseSchema):
    # WM3-B1 扩展：姓名/性别/生日全开（编辑弹窗统一端点；AR 只升不降保留）
    name: str | None = Field(None, min_length=1, max_length=64)
    english_name: str | None = Field(None, max_length=64)
    gender: int | None = Field(None, ge=1, le=2)
    birthday: date | None = None
    grade: str | None = Field(None, max_length=50)
    ar_level: str | None = Field(None, max_length=10, description="AR 值（老师评估，只升不降）")


class ChildResponse(BaseSchema):
    id: int
    parent_id: int
    name: str
    english_name: str | None
    gender: int | None
    birthday: date | None
    grade: str
    member_status: str
    member_start: date | None
    member_expire: date | None
    ar_level: str | None
    has_orders: bool = Field(False, description="存在未删订单（WM3-B1 守卫）")


class ChildWithParentResponse(ChildResponse):
    parent_name: str = ""
    parent_phone: str = ""


class ParentUpdateRequest(BaseSchema):
    name: str | None = Field(None, min_length=1, max_length=64)
    phone: str | None = Field(None, pattern=r"^\d{11}$", description="手机号（登录标识）")
    remark: str | None = Field(None, max_length=200)


class ParentWithStatsResponse(BaseSchema):
    """家长管理 tab 行（WM3-B1）：含孩子数与订单守卫标志。"""

    id: int
    name: str
    phone: str
    remark: str
    children_count: int = 0
    has_orders: bool = Field(False, description="名下任一孩子存在订单（禁改禁删守卫）")
    # F2：家长 tab 创建时间列（前端 dataIndex 就绪等后端；alias 对齐 OrderResponse 同款）
    created_at: datetime = Field(alias="create_time")


class OrderCreateRequest(BaseSchema):
    order_type: str = Field(..., pattern="^(first_activity_fee|observation_fee|formal_fee)$")
    child_id: int
    remark: str = Field("", max_length=200)


class OrderConfirmRequest(BaseSchema):
    pay_method: str = Field(
        ..., pattern="^(scan|alipay|transfer|card|cash|wechat)$", description="收款方式"
    )
    remark: str = Field("", max_length=200, description="凭证说明（留痕）")


class VoucherUploadResponse(BaseSchema):
    id: int
    order_no: str
    voucher_path: str


class OrderResponse(BaseSchema):
    id: int
    order_no: str
    order_type: str
    parent_id: int
    child_id: int | None
    amount: str
    status: str
    pay_method: str | None
    paid_at: datetime | None
    remark: str
    voucher_path: str | None = None
    created_at: datetime = Field(alias="create_time")
    child_name: str | None = None
    parent_name: str | None = None

    @field_validator("amount", mode="before")
    @classmethod
    def _amount_to_str(cls, v):
        return str(v)


class ChildCardResponse(ChildWithParentResponse):
    """借阅操作台的孩子卡片（WM5 也用）。"""

    active_borrows: int = Field(0, description="当前在借数")
    overdue_count: int = Field(0, description="逾期未还数")
    deposit_status: str | None = Field(None, description="押金状态（WM4 填充）")
