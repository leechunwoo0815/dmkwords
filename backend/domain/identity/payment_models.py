# backend/domain/identity/payment_models.py — 支付对账报表（WM12-B）
"""为什么单开文件：`models.py` 已经装了会员/订单/退款/退会/评估五组表，再塞对账会让它继续膨胀
（镜像 `admin/media_models.py` 的先例：新表进新文件，老表原地不动）。

**只增不改**：对账报表是留痕，任何"改历史报告"的行为都要先被人看见（口径见 `docs/09` WM12-B §二.5）。
"""

from sqlalchemy import Column, Date, DateTime, Integer, String, Text

from backend.common.base_model import BaseModel


class PaymentReconciliation(BaseModel):
    """一轮对账的结果（本地一致性审计 / 微信账单比对各一条）。

    - `status`：`ok`（无差异）/ `diff`（有差异，看 detail）/ `skipped`（缺凭据或当日无账单）/
      `failed`（对账自身出错）
    - `detail`：JSON 文本数组，每条 = `{kind, ref, message}`（kind 见 reconcile_service 的差异分类）
    """

    __tablename__ = "payment_reconciliations"

    SOURCE_LOCAL = "local"  # 本地一致性审计（不依赖微信）
    SOURCE_WECHAT = "wechat"  # 微信对账单比对（真通道）

    STATUS_OK = "ok"
    STATUS_DIFF = "diff"
    STATUS_SKIPPED = "skipped"
    STATUS_FAILED = "failed"

    TRIGGER_SCHEDULED = "scheduled"
    TRIGGER_MANUAL = "manual"

    bill_date = Column(
        Date, nullable=False, index=True, comment="对账日（微信账单日；本地审计=当天）"
    )
    source = Column(String(16), nullable=False, comment="local/wechat")
    status = Column(String(12), nullable=False, index=True, comment="ok/diff/skipped/failed")
    checked_count = Column(
        Integer, nullable=False, default=0, server_default="0", comment="核对笔数"
    )
    diff_count = Column(Integer, nullable=False, default=0, server_default="0", comment="差异笔数")
    detail = Column(Text, nullable=True, comment="差异明细 JSON 数组 [{kind,ref,message}]")
    trigger = Column(
        String(16),
        nullable=False,
        default=TRIGGER_MANUAL,
        server_default="manual",
        comment="scheduled/manual",
    )
    # 手动触发时的操作人；定时任务跑的轮次为 NULL（说明写这里而不是写进列注释：
    # 列注释必须与迁移文件逐字一致，否则 alembic check 会把"注释不同"当成待迁移变更）
    actor_id = Column(Integer, nullable=True, comment="手动触发时的操作人")
    finished_at = Column(DateTime, nullable=True, comment="完成时间")
