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

import hashlib
import logging
import random
from datetime import datetime, timedelta

from sqlalchemy.orm import Session

from backend.common.async_utils import run_coro
from backend.common.exceptions import ConflictError, ValidationError
from backend.config import get_settings
from backend.domain.identity.models import Parent, SmsCode

logger = logging.getLogger(__name__)

#: 短信正文里的用途文案（日志/审计用，不发给用户）
PURPOSE_LABELS = {SmsCode.PURPOSE_LOGIN: "登录", SmsCode.PURPOSE_BIND: "绑定"}


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
        result = run_coro(get_sms_gateway().send_code(phone))
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

    def _consumed_failure_message(self, row_id: int, max_attempts: int) -> str:
        """条件更新没吃下这一行时按**库里的事实**给准确文案（并发下失败原因不止一种）。

        用列查询而不是 ORM 实体：刚 commit 过，实体可能过期，而列查询直接取库内当前值。
        """
        fresh = (
            self.db.query(SmsCode.used_at, SmsCode.attempts).filter(SmsCode.id == row_id).first()
        )
        if fresh and fresh.used_at is not None:
            return "验证码已失效（已被使用），请重新获取"
        if fresh and int(fresh.attempts or 0) >= max_attempts:
            return "验证码错误次数过多，请重新获取"
        return "验证码已失效，请重新获取"

    def verify(self, phone: str, code: str, purpose: str = SmsCode.PURPOSE_LOGIN) -> None:
        """校验通过则写 used_at；失败抛 ValidationError（附剩余机会）。

        WM12-C（审查 P0-4）：**原子条件更新**——原先是"读 attempts → 判断上限 → 加一 → 提交"，
        并发下丢失更新（实测 20 个并发错误码只持久化 4 次，上限 5 形同虚设，码可被暴力试穿）。
        现在两条写入都带条件、看 `rowcount`：
        - 消费（正确码）：`SET used_at=? WHERE id=? AND used_at IS NULL` —— rowcount=1 才算这次赢了
          （同一正确码并发提交只有一个能消费，其余按"已失效"拒绝，防并发重放）；
        - 计数（错误码）：`SET attempts=attempts+1 WHERE id=? AND used_at IS NULL AND attempts<上限`
          —— rowcount=0 即"已被试爆/已被消费"，一律按超限拒绝。
        InnoDB 的 UPDATE 在行锁上按**最新已提交版本**重新判定条件，所以计数不会超过上限。
        """
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
        row_id = row.id
        if row.code_hash == hash_code(phone, code):
            consumed = (
                self.db.query(SmsCode)
                .filter(
                    SmsCode.id == row_id,
                    SmsCode.used_at.is_(None),
                    # 超限即作废：即使码是对的也不放行（否则"试爆上限"只挡住错码，形同虚设）
                    SmsCode.attempts < max_attempts,
                )
                .update({SmsCode.used_at: datetime.now()}, synchronize_session=False)
            )
            self.db.commit()
            if consumed:
                return
            raise ValidationError(self._consumed_failure_message(row_id, max_attempts))
        bumped = (
            self.db.query(SmsCode)
            .filter(
                SmsCode.id == row_id,
                SmsCode.used_at.is_(None),
                SmsCode.attempts < max_attempts,
            )
            .update({SmsCode.attempts: SmsCode.attempts + 1}, synchronize_session=False)
        )
        self.db.commit()
        if not bumped:
            raise ValidationError("验证码错误次数过多，请重新获取")
        left = max_attempts - int(
            self.db.query(SmsCode.attempts).filter(SmsCode.id == row_id).scalar() or 0
        )
        if left <= 0:
            raise ValidationError("验证码错误次数过多，请重新获取")
        raise ValidationError(f"验证码错误（还可尝试 {left} 次）")


def random_code() -> str:
    """6 位数字验证码（网关未回传时的兜底生成，仅供测试用）。"""
    return f"{random.randint(100000, 999999)}"


def parent_by_openid(db: Session, openid: str) -> Parent | None:
    return db.query(Parent).filter(Parent.wechat_openid == openid, Parent.is_deleted == 0).first()
