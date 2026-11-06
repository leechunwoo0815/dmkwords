# backend/common/config_audit_model.py
"""配置变更审计日志"""

from sqlalchemy import BigInteger, Column, String, Text

from backend.common.base_model import BaseModel


class ConfigAuditLog(BaseModel):
    """配置变更审计"""

    __tablename__ = "config_audit_log"
    __table_args__ = {"extend_existing": True}

    config_key = Column(String(50), nullable=False, index=True, comment="配置键")
    old_value = Column(Text, nullable=True, comment="旧值")
    new_value = Column(Text, nullable=False, comment="新值")
    changed_by = Column(BigInteger, nullable=True, comment="操作人ID")
