# backend/domain/admin/schemas.py — admin 域 API Schema
from datetime import datetime

from pydantic import Field

from backend.common.base_schema import BaseSchema


class NotificationReadStatusRequest(BaseSchema):
    read: bool = Field(..., description="true=标记已读 false=标记未读")
    reason: str = Field("", max_length=200, description="运营介入原因（可选留痕）")


class AdminNotificationHandleRequest(BaseSchema):
    """WM13 管理待办手动兜底（S4：reason 必填留痕）。"""

    reason: str = Field(..., min_length=1, max_length=200, description="处理原因（必填，审计留痕）")


class LoginRequest(BaseSchema):
    username: str = Field(..., min_length=1, max_length=191)
    password: str = Field(..., min_length=1, max_length=128)


class AdminUserResponse(BaseSchema):
    id: int
    username: str
    display_name: str
    role: str
    status: int = 1


class LoginResponse(BaseSchema):
    token: str
    user: AdminUserResponse


class StaffCreateRequest(BaseSchema):
    username: str = Field(..., min_length=2, max_length=191, pattern=r"^[A-Za-z0-9_.-]+$")
    password: str = Field(..., min_length=8, max_length=128)
    display_name: str = Field(..., min_length=1, max_length=64)
    role: str = Field("staff", pattern=r"^(superadmin|staff)$")


class StaffUpdateRequest(BaseSchema):
    display_name: str | None = Field(None, min_length=1, max_length=64)
    role: str | None = Field(None, pattern=r"^(superadmin|staff)$")


class StaffStatusRequest(BaseSchema):
    status: int = Field(..., ge=0, le=1)


class StaffResetPasswordRequest(BaseSchema):
    new_password: str = Field(..., min_length=8, max_length=128)


class MeResponse(BaseSchema):
    user: AdminUserResponse
    permissions: list[str]


class SystemConfigResponse(BaseSchema):
    id: int
    config_key: str
    display_name: str
    config_value: str
    default_value: str
    value_type: str
    category: str
    description: str


class SystemConfigUpdateRequest(BaseSchema):
    value: str = Field(
        ..., min_length=1, max_length=500, description="新值（字符串，服务端按类型解析）"
    )
    reason: str = Field(..., min_length=1, max_length=500, description="变更原因（必填留痕）")


class AuditLogResponse(BaseSchema):
    id: int
    actor_id: int
    actor_name: str
    action: str
    target_type: str
    target_id: str
    detail: str
    reason: str
    created_at: datetime


class DashboardRecentChange(BaseSchema):
    config_name: str
    change: str
    actor_name: str
    created_at: str


class DashboardOverviewResponse(BaseSchema):
    admin_count: int
    today_logins: int
    config_count: int
    recent_config_changes: list[DashboardRecentChange]
    copy_total: int = 0
    copy_available: int = 0
    copy_borrowed: int = 0
    today_borrowed: int = 0
    today_returned: int = 0
    overdue_active: int = 0
    member_total: int = 0
    member_new_week: int = 0
    activity_enroll_recent: int = 0
    copy_maintenance: int = 0
    copy_lost: int = 0
    renew_rate: float = 0.0
    withdrawal_rate: float = 0.0
    quiz_pass_rate: float = 0.0
    milestone_count: int = 0
    pending_evaluation_count: int = 0
