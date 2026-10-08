# backend/domain/identity/sms_service.py — 短信验证码（登录/绑定）2026-10-08 接线
"""发送与校验的**服务端权威实现**（表 `sms_codes`）；网关只负责"把短信发出去"。

设计要点（上线前审查 P0-1 的整改）：
- **校验权在我方**：网关（mock/阿里云/腾讯）生成并发送验证码，把 code 回传给我方，
  我方只存 `sha256(phone:code:SECRET_KEY)`——多 worker / 多副本下校验结果一致，
  换网关也不改校验口径（网关的 `verify_code` 一律不用）。
- **三条闸门**：同号发送间隔（`sms_send_interval_seconds`）/ 每日上限（`sms_daily_limit`）/
  校验失败次数上限（`sms_max_attempts`，超限即作废该码）。
- **一次性**：校验通过立即写 `used_at`，同码不可复用。
- **开发态**：`SMS_PROVIDER=mock` 时验证码打在日志里（`[MockSms]`），便于本地与模拟器联调；
  生产 `SMS_PROVIDER` 必须接真实网关（`validate_production` 把关）。
"""

from __future__ import annotations

import asyncio
import hashlib
import logging
import random
from datetime import datetime, timedelta

from sqlalchemy.orm import Session

from backend.common.exceptions import ConflictError, ValidationError
from backend.config import get_settings
from backend.domain.identity.models import Parent, SmsCode

logger = logging.getLogger(__name__)

#: 短信正文里的用途文案（日志/审计用，不发给用户）
PURPOSE_LABELS = {SmsCode.PURPOSE_LOGIN: "登录", SmsCode.PURPOSE_BIND: "绑定"}


def _run(coro):
    """在同步端点（线程池）里调异步网关：每个调用独立事件循环。"""
    return asyncio.run(coro)


def hash_code(phone: str, code: str) -> str:
    """验证码落库哈希（SECRET_KEY 作 pepper：库被读走也无法离线反推 6 位码）。"""
    raw = f"{phone}:{code}:{get_settings().SECRET_KEY}".encode()
    return hashlib.sha256(raw).hexdigest()


class SmsCodeService:
    """短信验证码：发送（含限流）+ 校验（含失败次数与一次性）。"""

    def __init__(self, db: Session):
        self.db = db

    # ---------- 配置 ----------

    def _cfg_int(self, key: str, default: int) -> int:
        from backend.common.config_service import ConfigService

        return ConfigService(self.db).get_int(key, default)

    # ---------- 发送 ----------

    def send(self, phone: str, purpose: str = SmsCode.PURPOSE_LOGIN) -> dict:
        """发一条验证码。返回 {sent: True, ttl_seconds, provider}（**不回传验证码**）。"""
        from backend.integrations.sms import get_sms_gateway

        phone = (phone or "").strip()
        if not phone or len(phone) < 11 or not phone.isdigit():
            raise ValidationError("请输入 11 位手机号")
        if purpose not in PURPOSE_LABELS:
            raise ValidationError("验证码用途不合法")

        now = datetime.now()
        interval = self._cfg_int("sms_send_interval_seconds", 60)
        daily_limit = self._cfg_int("sms_daily_limit", 10)
        ttl = self._cfg_int("sms_code_ttl_seconds", 300)

        recent = (
            self.db.query(SmsCode)
            .filter(SmsCode.phone == phone, SmsCode.is_deleted == 0)
            .order_by(SmsCode.id.desc())
            .first()
        )
        # MySQL DATETIME 是**秒精度**且会四舍五入：刚插入的行读回来可能落在"未来"几百毫秒，
        # 直接相减会得到负值（实测 delta=-0.42s）。故 elapsed 夹到 ≥0——
        # "未来时间戳"一律按"刚刚发过"处理（保守方向：宁可多拦一次，也不放过连发）。
        elapsed = max(0.0, (now - recent.created_at).total_seconds()) if recent else None
        if elapsed is not None and elapsed < interval:
            wait = interval - int(elapsed)
            raise ConflictError(f"发送太频繁，请 {max(1, wait)} 秒后再试")
        today_count = (
            self.db.query(SmsCode)
            .filter(
                SmsCode.phone == phone,
                SmsCode.is_deleted == 0,
                SmsCode.created_at >= now.replace(hour=0, minute=0, second=0, microsecond=0),
            )
            .count()
        )
        if today_count >= daily_limit:
            raise ConflictError(f"今日验证码发送已达上限（{daily_limit} 条），请明天再试")

        # 网关生成并发送；它把 code 回传给我方（真实厂商路径同样回传——用于本地落哈希）
        result = _run(get_sms_gateway().send_code(phone))
        if not result.success or not result.code:
            logger.error(
                "短信发送失败 phone=%s reason=%s", phone[:3] + "****", result.error_message
            )
            raise ValidationError(f"短信发送失败：{result.error_message or '请稍后重试'}")

        self.db.add(
            SmsCode(
                phone=phone,
                purpose=purpose,
                code_hash=hash_code(phone, result.code),
                expires_at=now + timedelta(seconds=ttl),
                created_at=now,
            )
        )
        self.db.commit()
        return {
            "sent": True,
            "ttl_seconds": ttl,
            "provider": get_settings().SMS_PROVIDER,
            "purpose": purpose,
        }

    # ---------- 校验 ----------

    def verify(self, phone: str, code: str, purpose: str = SmsCode.PURPOSE_LOGIN) -> None:
        """校验通过则写 used_at；失败抛 ValidationError（附剩余机会）。"""
        phone = (phone or "").strip()
        code = (code or "").strip()
        if not code:
            raise ValidationError("请输入验证码")
        row = (
            self.db.query(SmsCode)
            .filter(
                SmsCode.phone == phone,
                SmsCode.purpose == purpose,
                SmsCode.is_deleted == 0,
                SmsCode.used_at.is_(None),
            )
            .order_by(SmsCode.id.desc())
            .first()
        )
        if not row:
            raise ValidationError("请先获取验证码")
        if row.expires_at and row.expires_at < datetime.now():
            raise ValidationError("验证码已过期，请重新获取")
        max_attempts = self._cfg_int("sms_max_attempts", 5)
        if row.attempts >= max_attempts:
            raise ValidationError("验证码错误次数过多，请重新获取")
        if row.code_hash != hash_code(phone, code):
            row.attempts += 1
            self.db.commit()
            left = max(0, max_attempts - row.attempts)
            raise ValidationError(f"验证码错误（还可尝试 {left} 次）")
        row.used_at = datetime.now()
        self.db.commit()


def random_code() -> str:
    """6 位数字验证码（网关未回传时的兜底生成，仅供测试用）。"""
    return f"{random.randint(100000, 999999)}"


def parent_by_openid(db: Session, openid: str) -> Parent | None:
    return db.query(Parent).filter(Parent.wechat_openid == openid, Parent.is_deleted == 0).first()
