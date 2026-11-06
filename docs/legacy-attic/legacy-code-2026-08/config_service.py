# backend/common/config_service.py
"""
[What] 统一配置读取服务
[Why] 消除 Service 中散落的硬编码业务数值，统一从 SystemConfig 读取
[How] 带 TTL 缓存的 classmethod，支持 int/decimal/bool/str/list 类型转换

使用方式：
    borrow_limit = ConfigService.get_int(db, "borrow_limit", 10)
    fine = ConfigService.get_decimal(db, "overdue_fine_per_day", Decimal("1"))
    pass_rate = ConfigService.get_decimal(db, "quiz_pass_rate", Decimal("0.80"))
"""

import logging
import time
from decimal import Decimal

from sqlalchemy.orm import Session

from backend.domain.admin.models import SystemConfig

logger = logging.getLogger(__name__)

# 缓存 TTL（秒）
_CACHE_TTL = 300  # 5 分钟


class ConfigService:
    """统一配置读取服务，带进程内缓存和 TTL"""

    # 缓存格式: {key: (value, timestamp)}
    _cache: dict[str, tuple[str, float]] = {}

    @classmethod
    def _get_raw(cls, db: Session, key: str) -> str | None:
        """从缓存或数据库读取原始配置值"""
        now = time.time()
        if key in cls._cache:
            val, ts = cls._cache[key]
            if now - ts < _CACHE_TTL:
                return val
            del cls._cache[key]

        config = (
            db.query(SystemConfig)
            .filter(
                SystemConfig.config_key == key,
                SystemConfig.is_deleted == 0,
            )
            .first()
        )

        val = config.config_value if config else None
        if val is not None:
            cls._cache[key] = (val, now)
        return val

    @classmethod
    def get_int(cls, db: Session, key: str, default: int) -> int:
        """读取整数配置"""
        val = cls._get_raw(db, key)
        if val is None:
            return default
        try:
            return int(val)
        except (ValueError, TypeError):
            return default

    @classmethod
    def get_str(cls, db: Session, key: str, default: str) -> str:
        """读取字符串配置"""
        val = cls._get_raw(db, key)
        return val if val is not None else default

    @classmethod
    def get_decimal(cls, db: Session, key: str, default: Decimal) -> Decimal:
        """读取金额/比率配置"""
        val = cls._get_raw(db, key)
        if val is None:
            return default
        try:
            return Decimal(val)
        except (ValueError, TypeError):
            return default

    @classmethod
    def get_bool(cls, db: Session, key: str, default: bool) -> bool:
        """读取布尔配置"""
        val = cls._get_raw(db, key)
        if val is None:
            return default
        return val.lower() in ("true", "1", "yes")

    @classmethod
    def get_int_list(cls, db: Session, key: str, default: list[int]) -> list[int]:
        """读取逗号分隔的整数列表配置"""
        val = cls._get_raw(db, key)
        if val is None:
            return default
        try:
            return [int(x.strip()) for x in val.split(",")]
        except (ValueError, TypeError):
            return default

    @classmethod
    def invalidate(cls, key: str = None):
        """配置更新后调用，清除缓存"""
        if key:
            cls._cache.pop(key, None)
        else:
            cls._cache.clear()
        logger.info(f"Config cache invalidated: {key or 'all'}")

    @classmethod
    def set_config(cls, db: Session, key: str, value: str, admin_id: int = None) -> None:
        """更新配置并记录审计日志（P2-4 范围校验统一在此执行，覆盖所有写入路径）"""
        from backend.domain.admin.config_levels import validate_config_value

        range_err = validate_config_value(key, value)
        if range_err:
            from backend.common.exceptions import ValidationError

            raise ValidationError(range_err)

        config = (
            db.query(SystemConfig)
            .filter(
                SystemConfig.config_key == key,
                SystemConfig.is_deleted == 0,
            )
            .first()
        )

        old_value = config.config_value if config else None

        if config:
            config.config_value = value
            db.flush()
        else:
            config = SystemConfig(config_key=key, config_value=value)
            db.add(config)
            db.flush()

        # 审计日志
        from backend.common.config_audit_model import ConfigAuditLog

        audit = ConfigAuditLog(
            config_key=key,
            old_value=old_value,
            new_value=value,
            changed_by=admin_id,
        )
        db.add(audit)
        db.flush()

        cls.invalidate(key)
        logger.info(f"Config updated: {key} = {value} (old: {old_value})")
