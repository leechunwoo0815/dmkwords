# backend/common/system_models.py — 系统级横切模型（配置中心 / 审计日志）
"""基础设施表：任何域都可能读写配置与审计，故归 common（架构关零违规）。"""

from datetime import datetime

from sqlalchemy import BigInteger, Column, DateTime, String, Text

from backend.common.base_model import BaseModel


class SystemConfig(BaseModel):
    """业务配置项。所有业务数值（价格/上限/天数/阈值/开关）必须落此表。"""

    __tablename__ = "system_configs"

    TYPE_INT = "int"
    TYPE_FLOAT = "float"
    TYPE_BOOL = "bool"
    TYPE_STRING = "string"

    config_key = Column(
        String(191), unique=True, nullable=False, index=True, comment="配置键（系统标识）"
    )
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
