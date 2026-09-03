# backend/domain/admin/router.py — 管理端 API（Router 只做注入与调用，无业务逻辑）
from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from backend.common.base_schema import PaginatedResponse
from backend.database import get_db
from backend.middleware.rate_limit import rate_limit
from backend.domain.admin.models import AdminUser
from backend.domain.admin.schemas import (
    AdminNotificationHandleRequest,
    AdminUserResponse,
    AuditLogResponse,
    DashboardOverviewResponse,
    LoginRequest,
    LoginResponse,
    MeResponse,
    NotificationReadStatusRequest,
    StaffCreateRequest,
    StaffResetPasswordRequest,
    StaffStatusRequest,
    StaffUpdateRequest,
    SystemConfigResponse,
    SystemConfigUpdateRequest,
)
from backend.domain.admin.service import (
    AuditExportService,
    AuditService,
    AuthService,
    ConfigService,
    DashboardExportService,
    DashboardService,
    NotifyAdminService,
    StaffService,
    TaskAdminService,
    permissions_for_role,
)
from backend.middleware.admin_auth import get_current_admin
from backend.middleware.admin_rbac import require_perm, require_super_admin

router = APIRouter(tags=["admin"])


@router.post("/login", response_model=LoginResponse, dependencies=[Depends(rate_limit(5, 60))])
def login(body: LoginRequest, db: Session = Depends(get_db)):
    token, user = AuthService(db).login(body.username, body.password)
    return LoginResponse(
        token=token,
        user=AdminUserResponse.model_validate(user),
    )


@router.get("/me", response_model=MeResponse)
def me(admin: AdminUser = Depends(get_current_admin)):
    return MeResponse(
        user=AdminUserResponse.model_validate(admin),
        permissions=permissions_for_role(admin.role),
    )


@router.get("/configs", response_model=list[SystemConfigResponse])
def list_configs(
    admin: AdminUser = Depends(require_perm("config.view")),
    db: Session = Depends(get_db),
):
    return [SystemConfigResponse.model_validate(c) for c in ConfigService(db).list_configs()]


@router.put("/configs/{key}", response_model=SystemConfigResponse)
def update_config(
    key: str,
    body: SystemConfigUpdateRequest,
    admin: AdminUser = Depends(require_perm("config.update")),
    db: Session = Depends(get_db),
):
    config = ConfigService(db).update_config(admin, key, body.value, body.reason)
    return SystemConfigResponse.model_validate(config)


@router.get("/audit-logs", response_model=PaginatedResponse[AuditLogResponse])
def list_audit_logs(
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    actor_id: int | None = Query(None),
    action: str | None = Query(None),
    admin: AdminUser = Depends(require_perm("audit.view")),
    db: Session = Depends(get_db),
):
    items, total = AuditService(db).list_logs(page, page_size, actor_id, action)
    return PaginatedResponse[AuditLogResponse].create(
        items=[AuditLogResponse.model_validate(i) for i in items],
        total=total,
        page=page,
        page_size=page_size,
    )


@router.get("/dashboard", response_model=DashboardOverviewResponse)
def dashboard_overview(
    admin: AdminUser = Depends(require_perm("dashboard.view")),
    db: Session = Depends(get_db),
):
    return DashboardOverviewResponse.model_validate(DashboardService(db).overview())


# ---------- WM11 通知中心 / 定时任务看板 / 导出 ----------


@router.get("/admin-notifications")
def list_admin_notifications(
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=200),
    status_filter: str | None = Query(None),
    scene: str | None = Query(None),
    keyword: str | None = Query(None),
    admin: AdminUser = Depends(require_perm("dashboard.view")),
    db: Session = Depends(get_db),
):
    """WM13 管理待办收件箱（显示态实时算；S2：非超管返回空数据不 403）。"""
    from backend.domain.admin.todo_service import AdminTodoService

    return AdminTodoService(db).list_inbox(
        page,
        page_size,
        status_filter=status_filter,
        scene=scene,
        keyword=keyword,
        viewer_is_super=(admin.role == AdminUser.ROLE_SUPER_ADMIN),
    )


@router.post("/admin-notifications/{notification_id}/handle")
def handle_admin_notification(
    notification_id: int,
    body: AdminNotificationHandleRequest,
    admin: AdminUser = Depends(require_super_admin()),
    db: Session = Depends(get_db),
):
    """WM13 手动兜底标记已处理（S4：reason 必填 + publish_audit 留痕；Q8 幂等）。"""
    from backend.domain.admin.todo_service import AdminTodoService

    return AdminTodoService(db).handle(notification_id, admin, body.reason)


@router.get("/todo-counts")
def todo_counts(
    admin: AdminUser = Depends(require_perm("dashboard.view")),
    db: Session = Depends(get_db),
):
    """WM13 感知层聚合（Q9 权限粒度：审计五类仅超管；order_pending_manual 跟 member.manage）。"""
    from backend.domain.admin.todo_service import AdminTodoService

    return AdminTodoService(db).todo_counts(admin)


@router.get("/notifications")
def list_notifications(
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=200),
    category: str | None = Query(None),
    scene: str | None = Query(None),
    parent_name: str | None = Query(None),
    unread: bool | None = Query(None),
    read: bool | None = Query(None),
    admin: AdminUser = Depends(require_perm("dashboard.view")),
    db: Session = Depends(get_db),
):
    items, total, unread_count, all_count = NotifyAdminService(db).list_notifications(
        page, page_size, category, scene, parent_name, unread, read
    )
    return {
        "items": items,
        "total": total,
        "unread": unread_count,
        "all_count": all_count,
        "page": page,
        "page_size": page_size,
    }


@router.post("/notifications/{notification_id}/read-status")
def toggle_notification_read(
    notification_id: int,
    body: NotificationReadStatusRequest,
    admin: AdminUser = Depends(require_perm("dashboard.view")),
    db: Session = Depends(get_db),
):
    """管理端代家长标记已读/未读（运营介入，审计留痕）。"""
    return NotifyAdminService(db).toggle_read(admin, notification_id, body.read, body.reason)


@router.get("/notifications/export")
def export_notifications(
    admin: AdminUser = Depends(require_perm("dashboard.view")),
    db: Session = Depends(get_db),
):
    from fastapi.responses import StreamingResponse

    content = NotifyAdminService(db).export_excel()
    return StreamingResponse(
        iter([content]),
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": 'attachment; filename="notifications.xlsx"'},
    )


@router.get("/tasks")
def task_specs(
    admin: AdminUser = Depends(require_perm("dashboard.view")),
    db: Session = Depends(get_db),
):
    return {"items": TaskAdminService(db).specs()}


@router.get("/tasks/runs")
def task_runs(
    limit: int = Query(20, ge=1, le=100),
    admin: AdminUser = Depends(require_perm("dashboard.view")),
    db: Session = Depends(get_db),
):
    return {"items": TaskAdminService(db).recent_runs(limit)}


@router.post("/tasks/{task_name}/run")
def task_run(
    task_name: str,
    admin: AdminUser = Depends(require_perm("dashboard.view")),
    db: Session = Depends(get_db),
):
    from backend.common.exceptions import NotFoundError

    if task_name not in {s["name"] for s in TaskAdminService(db).specs()}:
        raise NotFoundError(f"任务不存在: {task_name}")
    return TaskAdminService(db).run(task_name, manual=True, admin=admin)


@router.get("/audit-logs/export")
def export_audit_logs(
    admin: AdminUser = Depends(require_perm("audit.view")),
    db: Session = Depends(get_db),
):
    from fastapi.responses import StreamingResponse

    content = AuditExportService(db).export_excel()
    return StreamingResponse(
        iter([content]),
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": 'attachment; filename="audit-logs.xlsx"'},
    )


@router.get("/dashboard/export")
def export_dashboard(
    admin: AdminUser = Depends(require_perm("dashboard.view")),
    db: Session = Depends(get_db),
):
    from fastapi.responses import StreamingResponse

    content = DashboardExportService(db).export_excel()
    return StreamingResponse(
        iter([content]),
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": 'attachment; filename="dashboard.xlsx"'},
    )


# ---------- 员工管理（WM1 §11.1：超管账号/角色/密码） ----------


@router.get("/staff", response_model=list[AdminUserResponse])
def staff_list(
    admin: AdminUser = Depends(require_super_admin()),
    db: Session = Depends(get_db),
):
    return StaffService(db).list()


@router.post("/staff", response_model=AdminUserResponse)
def staff_create(
    body: StaffCreateRequest,
    admin: AdminUser = Depends(require_super_admin()),
    db: Session = Depends(get_db),
):
    return StaffService(db).create(
        admin, body.username, body.password, body.display_name, body.role
    )


@router.put("/staff/{user_id}", response_model=AdminUserResponse)
def staff_update(
    user_id: int,
    body: StaffUpdateRequest,
    admin: AdminUser = Depends(require_super_admin()),
    db: Session = Depends(get_db),
):
    return StaffService(db).update(admin, user_id, body.display_name, body.role)


@router.put("/staff/{user_id}/status", response_model=AdminUserResponse)
def staff_status(
    user_id: int,
    body: StaffStatusRequest,
    admin: AdminUser = Depends(require_super_admin()),
    db: Session = Depends(get_db),
):
    return StaffService(db).set_status(admin, user_id, body.status)


@router.post("/staff/{user_id}/reset-password", response_model=dict)
def staff_reset_password(
    user_id: int,
    body: StaffResetPasswordRequest,
    admin: AdminUser = Depends(require_super_admin()),
    db: Session = Depends(get_db),
):
    StaffService(db).reset_password(admin, user_id, body.new_password)
    return {"ok": True}
