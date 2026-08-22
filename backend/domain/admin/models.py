# backend/domain/admin/models.py — 管理端账号 / 系统配置 / 审计日志
"""admin 域模型（WM1 平台基座）。

表：
  admin_users    后台账号（超管/运营专员两种角色）
  system_configs 业务配置中心（数值全配置化铁律的落点）
  audit_logs     操作审计日志（资金/状态/放行/配置变更留痕）
"""

from datetime import datetime

from sqlalchemy import BigInteger, Column, DateTime, Integer, SmallInteger, String, Text

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


class SystemConfig(BaseModel):
    """业务配置项。所有业务数值（价格/上限/天数/阈值/开关）必须落此表。"""

    __tablename__ = "system_configs"

    TYPE_INT = "int"
    TYPE_FLOAT = "float"
    TYPE_BOOL = "bool"
    TYPE_STRING = "string"

    config_key = Column(String(191), unique=True, nullable=False, index=True, comment="配置键（系统标识）")
    display_name = Column(String(100), nullable=False, default="", comment="中文显示名（店长视角）")
    config_value = Column(String(500), nullable=False, comment="配置值（字符串存储，按类型解析）")
    default_value = Column(String(500), nullable=False, default="", comment="默认值")
    value_type = Column(String(10), nullable=False, default=TYPE_STRING, comment="值类型")
    category = Column(String(50), nullable=False, default="通用", comment="分类（借阅/收费/测验…）")
    description = Column(String(200), nullable=False, default="", comment="说明")


class AuditLog(BaseModel):
    """操作审计日志。谁、何时、对谁、做了什么、原因、前后值。只增不改。"""

    __tablename__ = "audit_logs"

    ACTION_LOGIN = "login"
    ACTION_CONFIG_UPDATE = "config.update"

    actor_id = Column(BigInteger, nullable=False, index=True, comment="操作人ID")
    actor_name = Column(String(64), nullable=False, default="", comment="操作人名")
    action = Column(String(100), nullable=False, index=True, comment="动作")
    target_type = Column(String(50), nullable=False, default="", comment="对象类型")
    target_id = Column(String(64), nullable=False, default="", comment="对象标识")
    detail = Column(Text, nullable=True, comment="详情JSON（含变更前后值）")
    reason = Column(String(500), nullable=False, default="", comment="操作原因")
    created_at = Column(DateTime, nullable=False, default=datetime.now, comment="操作时间")
