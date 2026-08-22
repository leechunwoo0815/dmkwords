# backend/domain/admin/models.py — 管理端账号 / 系统配置 / 审计日志
"""admin 域模型（WM1 平台基座）。

表：
  admin_users    后台账号（超管/运营专员两种角色）
  system_configs 业务配置中心（数值全配置化铁律的落点）
  audit_logs     操作审计日志（资金/状态/放行/配置变更留痕）
"""

from sqlalchemy import Column, DateTime, Integer, SmallInteger, String

from backend.common.base_model import BaseModel


class AdminUser(BaseModel):
    """后台账号。角色两种：superadmin / staff（PRD V1.1 §11.1）。"""

    __tablename__ = "admin_users"

    ROLE_SUPER_ADMIN = "superadmin"
    ROLE_STAFF = "staff"

    STATUS_ACTIVE = 1
    STATUS_DISABLED = 0

    username = Column(String(191), unique=True, nullable=False, index=True, comment="登录名")
    password_hash = Column(String(255), nullable=False, comment="密码哈希")
    display_name = Column(String(64), nullable=False, default="", comment="显示名")
    role = Column(String(20), nullable=False, default=ROLE_STAFF, comment="角色: superadmin/staff")
    status = Column(SmallInteger, nullable=False, default=STATUS_ACTIVE, comment="1=启用 0=禁用")
    token_generation = Column(Integer, nullable=False, default=0, comment="改密后+1，旧token失效")
    last_login_at = Column(DateTime, nullable=True, comment="最近登录时间")


# 兼容旧引用：配置/审计模型已上移 common（横切基础设施）
from backend.common.system_models import AuditLog, SystemConfig  # noqa: E402, F401
