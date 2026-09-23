# backend/domain/admin/schemas.py — admin 域 API Schema
from datetime import datetime

from pydantic import Field, field_validator

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
    detail: str = ""
    reason: str
    created_at: datetime

    @field_validator("detail", mode="before")
    @classmethod
    def _none_detail_to_empty(cls, v: object) -> object:
        """fix29-R3：detail 列可为 NULL（audit_handlers 对 falsy detail 落 NULL），
        但本 Schema 契约是字符串——None 归一为 ""，否则列表/分页校验炸 500
        （用户实锤「点击审计日志 500」；WM14-A circle.pin/admin_unlike 传 detail={} 首发）。
        前端 renderDetail 已对空串回落「—」，无需改前端。"""
        return "" if v is None else v


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


class BorrowTrendPoint(BaseSchema):
    date: str = Field(..., description="MM-DD")
    borrowed: int
    returned: int


class HotBookItem(BaseSchema):
    book_id: int
    title: str
    borrow_count: int


class MemberStatusItem(BaseSchema):
    status: str
    count: int


class DashboardChartsResponse(BaseSchema):
    """仪表盘图形区数据（2026-09-21）：一次返回多张图所需聚合，前端 60s 轮询。"""

    borrow_trend: list[BorrowTrendPoint] = Field(default_factory=list)
    hot_books: list[HotBookItem] = Field(default_factory=list)
    member_status: list[MemberStatusItem] = Field(default_factory=list)


# ---------- 媒体体检（docs/15 §二十二） ----------


class MediaBreakdownItem(BaseSchema):
    """按一级目录的孤儿明细（cover/ circle/ reports/ book_audio/）。"""

    bucket: str
    files: int
    bytes: int


class MediaCensusResponse(BaseSchema):
    """一次盘点的报告——运营在任务看板看到的"孤儿图 N 张 / X MB"就是这里的两个字段。"""

    id: int
    trigger: str = Field(..., description="scheduled/manual/after_trash/after_restore")
    created_at: str
    files_total: int
    referenced_total: int
    protected_total: int
    orphan_files: int
    orphan_bytes: int
    missing_refs: int
    breakdown: list[MediaBreakdownItem] = Field(default_factory=list)


class MediaTrashItem(BaseSchema):
    id: int
    rel_path: str
    bucket: str
    bytes: int
    batch: str
    created_at: str
    restore_until: str
    actor_name: str


class MediaTrashSummary(BaseSchema):
    files: int
    bytes: int
    unmanaged: int = Field(
        0,
        description="回收站里没有记账的文件数（清场重建等外部清库造成；下次盘点自动接管，不删文件）",
    )
    min_age_minutes: int = Field(..., description="最小年龄闸门（分钟）")
    retain_days: int = Field(..., description="回收站保留天数（到期复检后清除）")
    items: list[MediaTrashItem] = Field(default_factory=list)


class MediaHealthResponse(BaseSchema):
    census: MediaCensusResponse | None = None
    trash: MediaTrashSummary


class MediaTrashRequest(BaseSchema):
    paths: list[str] = Field(
        default_factory=list,
        max_length=500,
        description="要清理的相对路径（与 all_orphans 二选一）",
    )
    all_orphans: bool = Field(
        False, description="true = 清理当前盘点出的全部孤儿（运营按钮主路径）"
    )
    reason: str = Field(..., min_length=1, max_length=200, description="清理原因（必填，审计留痕）")


class MediaTrashSkippedItem(BaseSchema):
    path: str
    reason: str


class MediaTrashResponse(BaseSchema):
    batch: str
    moved: int
    moved_bytes: int
    skipped: list[MediaTrashSkippedItem] = Field(default_factory=list)
    census: MediaCensusResponse


class MediaRestoreResponse(BaseSchema):
    rel_path: str
    census: MediaCensusResponse


class MediaEmptyTrashRequest(BaseSchema):
    reason: str = Field(
        ...,
        min_length=1,
        max_length=200,
        description="清空原因（必填；永久删除不可恢复，审计留痕）",
    )


class MediaEmptyTrashResponse(BaseSchema):
    """清空回收站的结果：`restored > 0` 说明复检闸门当场救回了"又被引用"的图。"""

    purged: int
    purged_bytes: int
    restored: int
    census: MediaCensusResponse
