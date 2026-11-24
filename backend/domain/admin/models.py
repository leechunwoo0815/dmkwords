# backend/domain/admin/models.py — 管理端账号 / 系统配置 / 审计日志 / 服务器指标
"""admin 域模型（WM1 平台基座）。

表：
  admin_users    后台账号（超管/运营专员两种角色）
  system_configs 业务配置中心（数值全配置化铁律的落点）
  audit_logs     操作审计日志（资金/状态/放行/配置变更留痕）
  system_metrics 服务器资源采样（FEAT-091；append-only，24h 保留）
"""

from datetime import datetime

from sqlalchemy import BigInteger, Column, DateTime, Float, Integer, SmallInteger, String

from backend.common.base_model import BaseModel
from backend.database import Base as SQLAlchemyBase


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


class SystemMetric(SQLAlchemyBase):
    """服务器资源采样（FEAT-091）。

    append-only：不继承 BaseModel（不需要软删除/更新时间）；采集任务每 60 秒写一行，
    只保留 24 小时。字段单位写在列名里（_mb/_gb/_kbps/_seconds），避免前端猜。
    """

    __tablename__ = "system_metrics"

    id = Column(BigInteger, primary_key=True, autoincrement=True, comment="主键")
    collected_at = Column(
        DateTime, nullable=False, default=datetime.now, index=True, comment="采样时间（服务器本地）"
    )

    cpu_percent = Column(Float, nullable=False, default=0.0, comment="CPU 总使用率 %")
    load1 = Column(Float, nullable=False, default=0.0, comment="1 分钟负载")
    load5 = Column(Float, nullable=False, default=0.0, comment="5 分钟负载")
    load15 = Column(Float, nullable=False, default=0.0, comment="15 分钟负载")

    mem_total_mb = Column(Integer, nullable=False, default=0, comment="内存总量 MB")
    mem_used_mb = Column(Integer, nullable=False, default=0, comment="内存已用 MB")
    mem_available_mb = Column(Integer, nullable=False, default=0, comment="内存可用 MB")
    mem_cached_mb = Column(Integer, nullable=False, default=0, comment="缓存 MB")
    swap_total_mb = Column(Integer, nullable=False, default=0, comment="Swap 总量 MB")
    swap_used_mb = Column(Integer, nullable=False, default=0, comment="Swap 已用 MB")

    disk_total_gb = Column(Float, nullable=False, default=0.0, comment="根分区总量 GB")
    disk_used_gb = Column(Float, nullable=False, default=0.0, comment="根分区已用 GB")
    disk_percent = Column(Float, nullable=False, default=0.0, comment="根分区使用率 %")
    inode_percent = Column(Float, nullable=False, default=0.0, comment="根分区 inode 使用率 %")
    disk_read_kbps = Column(Float, nullable=False, default=0.0, comment="磁盘读 KB/s")
    disk_write_kbps = Column(Float, nullable=False, default=0.0, comment="磁盘写 KB/s")

    net_rx_kbps = Column(Float, nullable=False, default=0.0, comment="网络入站 KB/s")
    net_tx_kbps = Column(Float, nullable=False, default=0.0, comment="网络出站 KB/s")

    process_count = Column(Integer, nullable=False, default=0, comment="进程数")
    uptime_seconds = Column(BigInteger, nullable=False, default=0, comment="主机运行秒数")

    mysql_ok = Column(SmallInteger, nullable=False, default=1, comment="MySQL 可达 1/0")
    mysql_connections = Column(Integer, nullable=False, default=0, comment="MySQL 连接数")
    mysql_running = Column(Integer, nullable=False, default=0, comment="MySQL 运行中线程")
    mysql_qps = Column(Float, nullable=False, default=0.0, comment="MySQL QPS（差值）")
    mysql_slow_queries = Column(BigInteger, nullable=False, default=0, comment="慢查询累计")

    app_requests = Column(Integer, nullable=False, default=0, comment="当窗口 API 请求数")
    app_4xx = Column(Integer, nullable=False, default=0, comment="当窗口 4xx")
    app_5xx = Column(Integer, nullable=False, default=0, comment="当窗口 5xx")
    app_p95_ms = Column(Integer, nullable=False, default=0, comment="当窗口 P95 延迟 ms")
