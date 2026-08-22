# backend/common/config_service.py — 配置中心（全域共用；数值全配置化铁律的执行点）
"""TTL 缓存 + 类型强转 + 变更审计（审计依赖由 admin 域注入回调，避免 common→admin 反向依赖）。"""

from __future__ import annotations

import json
import time

from sqlalchemy.orm import Session

from backend.common.exceptions import NotFoundError, ValidationError
from backend.common.system_models import AuditLog, SystemConfig

# ---------- 配置缓存 ----------
_CACHE_TTL_SECONDS = 60
_config_cache: dict[str, tuple[str, float]] = {}


def _cache_get(key: str) -> str | None:
    entry = _config_cache.get(key)
    if entry is None:
        return None
    value, expires_at = entry
    if time.monotonic() > expires_at:
        _config_cache.pop(key, None)
        return None
    return value


def _cache_set(key: str, value: str) -> None:
    _config_cache[key] = (value, time.monotonic() + _CACHE_TTL_SECONDS)


def invalidate_config_cache(key: str | None = None) -> None:
    if key is None:
        _config_cache.clear()
    else:
        _config_cache.pop(key, None)


class ConfigService:
    def __init__(self, db: Session):
        self.db = db
        self._db = db

    def list_configs(self) -> list[SystemConfig]:
        return (
            self.db.query(SystemConfig)
            .filter(SystemConfig.is_deleted == 0)
            .order_by(SystemConfig.category, SystemConfig.config_key)
            .all()
        )

    def get_value(self, key: str, default: str | None = None) -> str:
        cached = _cache_get(key)
        if cached is not None:
            return cached
        config = (
            self._db.query(SystemConfig)
            .filter(SystemConfig.config_key == key, SystemConfig.is_deleted == 0)
            .first()
        )
        if not config:
            if default is not None:
                return default
            raise NotFoundError(f"配置项不存在: {key}")
        _cache_set(key, config.config_value)
        return config.config_value

    def get_int(self, key: str, default: int | None = None) -> int:
        raw = self.get_value(key, str(default) if default is not None else None)
        return int(raw)

    def update_config(self, admin, key: str, value: str, reason: str) -> SystemConfig:
        config = (
            self._db.query(SystemConfig)
            .filter(SystemConfig.config_key == key, SystemConfig.is_deleted == 0)
            .first()
        )
        if not config:
            raise NotFoundError(f"配置项不存在: {key}")

        coerced = self._coerce(config.value_type, value, config.config_key)
        old_value = config.config_value
        if old_value == coerced:
            raise ValidationError("新值与当前值相同，无需修改")

        config.config_value = coerced
        self._db.flush()
        self._db.add(
            AuditLog(
                actor_id=admin.id,
                actor_name=admin.display_name or admin.username,
                action=AuditLog.ACTION_CONFIG_UPDATE,
                target_type="system_config",
                target_id=key,
                detail=json.dumps(
                    {"old": old_value, "new": coerced, "value_type": config.value_type},
                    ensure_ascii=False,
                ),
                reason=reason,
            )
        )
        self.db.commit()
        invalidate_config_cache(key)
        return config

    @staticmethod
    def _coerce(value_type: str, value: str, key: str) -> str:
        """按声明类型解析并回写规范字符串；解析失败抛 ValidationError。"""
        if value_type == SystemConfig.TYPE_INT:
            try:
                return str(int(value.strip()))
            except (ValueError, TypeError) as e:
                raise ValidationError(f"配置 {key} 为整数类型，无法解析: {value!r}") from e
        if value_type == SystemConfig.TYPE_FLOAT:
            try:
                return str(float(value.strip()))
            except (ValueError, TypeError) as e:
                raise ValidationError(f"配置 {key} 为数值类型，无法解析: {value!r}") from e
        if value_type == SystemConfig.TYPE_BOOL:
            normalized = value.strip().lower()
            if normalized in ("true", "1", "yes", "on"):
                return "true"
            if normalized in ("false", "0", "no", "off"):
                return "false"
            raise ValidationError(f"配置 {key} 为布尔类型，仅接受 true/false: {value!r}")
        return value.strip()
