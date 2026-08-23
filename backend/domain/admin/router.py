# backend/domain/admin/router.py — 管理端 API（Router 只做注入与调用，无业务逻辑）
from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from backend.common.base_schema import PaginatedResponse
from backend.database import get_db
from backend.domain.admin.models import AdminUser
from backend.domain.admin.schemas import (
    AdminUserResponse,
    AuditLogResponse,
    DashboardOverviewResponse,
    LoginRequest,
    LoginResponse,
    MeResponse,
    StaffCreateRequest,
    StaffResetPasswordRequest,
    StaffStatusRequest,
    StaffUpdateRequest,
    SystemConfigResponse,
    SystemConfigUpdateRequest,
)
from backend.domain.admin.service import (
    AuditService,
    AuthService,
    ConfigService,
    DashboardService,
    StaffService,
    permissions_for_role,
)
from backend.middleware.admin_auth import get_current_admin
from backend.middleware.admin_rbac import require_perm, require_super_admin

router = APIRouter(tags=["admin"])


@router.post("/login", response_model=LoginResponse)
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
