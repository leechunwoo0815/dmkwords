# backend/config.py — DmkWords 配置（MySQL-only，Pydantic Settings）
from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    # 应用
    APP_NAME: str = "DmkWords API"
    APP_VERSION: str = "1.0.0"
    DEBUG: bool = False
    # P0-F2：开发期固定验证码（.env 可覆盖）；生产必须置空（validate_production 硬校验），
    # 置空后 login 对任何 code 全拒（fail-closed），倒逼 WM12 接微信 code2session/SMS
    LOGIN_DEV_CODE: str = "1234"
    ENABLE_TEST_TOKEN: bool = False

    # 数据库（MySQL 8.0 only，单一数据库铁律）
    DB_HOST: str = "127.0.0.1"
    DB_PORT: int = 3306
    DB_USER: str = "root"
    DB_PASSWORD: str = ""
    DB_NAME: str = "dmkwords"

    # JWT
    SECRET_KEY: str = "change-in-production"
    ALGORITHM: str = "HS256"
    ACCESS_TOKEN_EXPIRE_MINUTES: int = 120
    ADMIN_TOKEN_EXPIRE_HOURS: int = 8

    # 微信开放平台
    WECHAT_APP_ID: str = ""
    WECHAT_APP_SECRET: str = ""

    # 微信订阅消息（WM11 通知双通道：站内必达 + 订阅尽力）
    WECHAT_SUBSCRIBE_ENABLED: bool = False

    # 定时任务调度器（WM11/F5：验收期与排障可经 .env 置 false 关闭，进程内任务不自动跑）
    SCHEDULER_ENABLED: bool = True

    # 微信支付 V3
    WECHAT_MCH_ID: str = ""
    WECHAT_API_KEY_V3: str = ""
    WECHAT_CERT_SERIAL_NO: str = ""
    WECHAT_PRIVATE_KEY_PATH: str = ""
    WECHAT_PLATFORM_CERT_PATH: str = ""
    WECHAT_PAY_NOTIFY_URL: str = ""
    WECHAT_REFUND_NOTIFY_URL: str = ""

    # 服务器
    BACKEND_PORT: int = 8002
    UPLOADS_DIR: str = "uploads"

    @property
    def database_url(self) -> str:
        return (
            f"mysql+pymysql://{self.DB_USER}:{self.DB_PASSWORD}"
            f"@{self.DB_HOST}:{self.DB_PORT}/{self.DB_NAME}?charset=utf8mb4"
        )

    def validate_production(self) -> None:
        """生产环境硬校验（宪法红线）：违规直接启动失败。"""
        if self.DEBUG:
            return
        problems: list[str] = []
        if self.SECRET_KEY == "change-in-production":
            problems.append("SECRET_KEY 未更换")
        if not self.DB_PASSWORD:
            problems.append("DB_PASSWORD 为空")
        if not self.WECHAT_APP_ID or not self.WECHAT_APP_SECRET:
            problems.append("微信配置缺失")
        if self.LOGIN_DEV_CODE:
            problems.append("生产环境 LOGIN_DEV_CODE 必须置空（禁用固定验证码）")
        if problems:
            raise RuntimeError(f"生产环境配置校验失败: {'; '.join(problems)}")


@lru_cache
def get_settings() -> Settings:
    return Settings()
