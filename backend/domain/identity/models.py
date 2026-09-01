# backend/domain/identity/models.py — 家长 / 孩子（会员状态机 R-301）

from datetime import date, datetime

from sqlalchemy import Column, Date, DateTime, Index, Integer, Numeric, SmallInteger, String, Text

from backend.common.base_model import BaseModel


class Parent(BaseModel):
    """家长账号（小程序主体）。"""

    __tablename__ = "parents"

    name = Column(String(64), nullable=False, default="", comment="家长姓名")
    phone = Column(String(20), unique=True, nullable=False, index=True, comment="手机号（唯一）")
    wechat_openid = Column(String(64), unique=True, nullable=True, comment="微信 openid")
    remark = Column(String(200), nullable=False, default="")


class Child(BaseModel):
    """孩子档案（会员生命周期宿主）。"""

    __tablename__ = "children"

    # 会员状态机（R-301 六态）
    MEMBER_NONE = "none"
    MEMBER_OBSERVATION = "observation"
    MEMBER_PENDING_EVALUATION = "pending_evaluation"
    MEMBER_FORMAL = "formal"
    MEMBER_EXPIRED = "expired"
    MEMBER_WITHDRAWN = "withdrawn"

    # 允许转移（红线 8：改状态机先画矩阵；WM1 版本——评估/到期任务在 WM11 补）
    ALLOWED_TRANSITIONS = {
        MEMBER_NONE: {MEMBER_OBSERVATION, MEMBER_FORMAL},
        MEMBER_OBSERVATION: {
            MEMBER_PENDING_EVALUATION,
            MEMBER_FORMAL,
            MEMBER_EXPIRED,
            MEMBER_WITHDRAWN,
        },
        MEMBER_PENDING_EVALUATION: {MEMBER_FORMAL, MEMBER_WITHDRAWN},
        MEMBER_FORMAL: {
            MEMBER_FORMAL,
            MEMBER_EXPIRED,
            MEMBER_WITHDRAWN,
        },  # self-loop = 续费顺延（V1.1 §3.4）
        MEMBER_EXPIRED: {MEMBER_OBSERVATION, MEMBER_FORMAL, MEMBER_WITHDRAWN},
        MEMBER_WITHDRAWN: {MEMBER_OBSERVATION, MEMBER_FORMAL},  # 重新入会（R-301）
    }

    parent_id = Column(Integer, nullable=False, index=True, comment="家长ID")
    name = Column(String(64), nullable=False, comment="姓名")
    english_name = Column(String(64), nullable=True, comment="英文名（榜单展示用）")
    avatar = Column(String(255), nullable=True, comment="头像")
    gender = Column(SmallInteger, nullable=True, comment="1=男 2=女")
    birthday = Column(Date, nullable=True)
    grade = Column(String(50), nullable=False, default="")
    ar_level = Column(String(10), nullable=True, comment="当前 AR 值（老师维护，只升不降）")
    member_status = Column(
        String(24), nullable=False, default=MEMBER_NONE, index=True, comment="会员状态"
    )
    member_start = Column(Date, nullable=True, comment="会员开始日")
    member_expire = Column(Date, nullable=True, comment="会员到期日")
    withdraw_reason = Column(
        String(32),
        nullable=True,
        comment="退会原因码（user_withdrawal/user_refund/membership_transfer）",
    )
    operation_locked = Column(
        SmallInteger, nullable=False, default=0, comment="操作冻结（转让/退会审核中）"
    )

    def can_transition(self, new_status: str) -> bool:
        return new_status in self.ALLOWED_TRANSITIONS.get(self.member_status, set())

    @property
    def is_active_member(self) -> bool:
        """有效会员（R-313/术语表口径）——听全馆音频、借书/预约资格的判定口径。

        - 观察期/待评估：权益保留、无时限（R-101/决策 8），member_expire 仅作状态转换触发不作失效判定；
        - 正式会员：member_expire >= 今天（D1：读时即时判定，不依赖定时任务落库 expired）；
        - none/expired/withdrawn：无效。
        """
        if self.member_status in (self.MEMBER_OBSERVATION, self.MEMBER_PENDING_EVALUATION):
            return True
        if self.member_status == self.MEMBER_FORMAL:
            return self.member_expire is not None and self.member_expire >= date.today()
        return False

    @property
    def is_expired_member(self) -> bool:
        """过期会员：正式会员到期未续费（状态可能尚未由 WM11 定时任务落库为 expired，读时兜底判定）。"""
        if self.member_status == self.MEMBER_EXPIRED:
            return True
        return self.member_status == self.MEMBER_FORMAL and (
            self.member_expire is not None and self.member_expire < date.today()
        )


class Order(BaseModel):
    """订单（billing 语义，宿主放 identity 域避免域循环；WM5 后如膨胀再拆 billing 域目录）。"""

    __tablename__ = "orders"
    __table_args__ = (Index("uq_order_no", "order_no", "is_deleted", unique=True),)

    TYPE_FIRST_ACTIVITY = "first_activity_fee"
    TYPE_OBSERVATION = "observation_fee"
    TYPE_FORMAL = "formal_fee"
    TYPE_ACTIVITY = "activity_fee"
    TYPE_DEPOSIT = "deposit"  # 押金（WM4）
    TYPE_DEPOSIT_SUPPLEMENT = "deposit_supplement"  # 押金补缴（WM4，R-312）

    STATUS_PENDING_PAYMENT = "pending_payment"  # 线上待支付（WM12）
    STATUS_PENDING_MANUAL = "pending_manual_confirm"  # 待人工确认收款
    STATUS_PAID = "paid"
    STATUS_CANCELLED = "cancelled"
    STATUS_REFUNDED = "refunded"  # 已退款（WM9/WM10：退款执行成功后置）

    # 退款链路状态（R-308；独立于订单主状态，供 99 元资格等"未全额退款"判定）
    REFUND_STATUS_NONE = ""
    REFUND_STATUS_PENDING = "pending"
    REFUND_STATUS_APPROVED = "approved"
    REFUND_STATUS_PROCESSING = "processing"
    REFUND_STATUS_REFUNDED = "refunded"
    REFUND_STATUS_FAILED = "failed"

    ALLOWED_TRANSITIONS = {
        STATUS_PENDING_PAYMENT: {STATUS_PAID, STATUS_CANCELLED},
        STATUS_PENDING_MANUAL: {STATUS_PAID, STATUS_CANCELLED},
        STATUS_PAID: {STATUS_REFUNDED},
        STATUS_CANCELLED: set(),
    }

    order_no = Column(String(32), nullable=False, comment="订单号")
    order_type = Column(String(24), nullable=False, index=True, comment="订单类型")
    parent_id = Column(Integer, nullable=False, index=True)
    child_id = Column(Integer, nullable=True, index=True, comment="孩子ID（活动费可能家长级）")
    amount = Column(Numeric(10, 2), nullable=False, comment="实收金额（Decimal 铁律）")
    status = Column(String(24), nullable=False, default=STATUS_PENDING_MANUAL, index=True)
    refund_status = Column(
        String(24), nullable=False, default="", server_default="", comment="退款链路状态（R-308）"
    )
    pay_method = Column(
        String(24), nullable=True, comment="收款方式（wechat/scan/alipay/transfer/card/cash）"
    )
    voucher_path = Column(
        String(255), nullable=True, comment="收款凭证图路径（WM3-B2，voucher/ 目录）"
    )
    paid_at = Column(DateTime, nullable=True, comment="收款确认时间")
    paid_by = Column(Integer, nullable=True, comment="确认收款的管理员ID")
    remark = Column(String(200), nullable=False, default="", comment="备注/凭证说明")

    def can_transition(self, new_status: str) -> bool:
        return new_status in self.ALLOWED_TRANSITIONS.get(self.status, set())


class RefundRequest(BaseModel):
    """退款申请（订单类 + 押金类统一；超管逐单审核）。R-308 七态。"""

    __tablename__ = "refund_requests"

    KIND_ORDER = "order"
    KIND_DEPOSIT = "deposit"

    STATUS_PENDING = "pending"  # 家长/系统申请，待审核
    STATUS_APPROVED = "approved"  # 审核通过，待执行
    STATUS_PROCESSING = "processing"  # 退款执行中（线下打款/线上原路）
    STATUS_REFUNDED = "refunded"  # 退款成功（终态）
    STATUS_FAILED = "failed"  # 执行失败（可重试）
    STATUS_REJECTED = "rejected"  # 审核拒绝
    STATUS_CANCELLED = "cancelled"  # 家长撤销

    kind = Column(String(10), nullable=False, comment="order/deposit")
    order_id = Column(Integer, nullable=True, comment="关联订单（order 类）")
    deposit_id = Column(Integer, nullable=True, comment="关联押金（deposit 类）")
    withdrawal_id = Column(Integer, nullable=True, comment="关联退会申请（退会/退款联动结算生成）")
    child_id = Column(Integer, nullable=False, index=True)
    amount = Column(Numeric(10, 2), nullable=False, comment="申请退款金额")
    reason = Column(String(200), nullable=False, default="", comment="家长申请原因")
    status = Column(String(20), nullable=False, default=STATUS_PENDING, index=True)
    review_remark = Column(String(200), nullable=True, comment="审核备注（拒绝时给家长看）")
    reviewed_by = Column(Integer, nullable=True)
    reviewed_at = Column(DateTime, nullable=True)
    created_at = Column(DateTime, nullable=False, default=datetime.now)


class WithdrawalRequest(BaseModel):
    """退会申请（审核期间孩子操作冻结）。R-311 六态。"""

    __tablename__ = "withdrawal_requests"

    STATUS_APPLYING = "applying"  # 家长已提交，待审核
    STATUS_PENDING_SETTLE = "pending_settle"  # 审核通过，待结算（计算可退金额并生成退款单）
    STATUS_REFUNDING = "refunding"  # 退款单执行中
    STATUS_COMPLETED = "completed"  # 全部退款完成，退会生效
    STATUS_REJECTED = "rejected"
    STATUS_CANCELLED = "cancelled"

    # 来源：主动退会 / 会员费退款联动（R-309）/ 权益转让联动（R-305）
    SOURCE_NORMAL = "normal"
    SOURCE_REFUND = "refund_linked"
    SOURCE_TRANSFER = "transfer_linked"

    child_id = Column(Integer, nullable=False, index=True)
    source = Column(
        String(24),
        nullable=False,
        default=SOURCE_NORMAL,
        server_default="normal",
        comment="来源（normal/refund_linked/transfer_linked）",
    )
    reason = Column(String(200), nullable=False, default="")
    status = Column(String(20), nullable=False, default=STATUS_APPLYING, index=True)
    review_remark = Column(String(200), nullable=True)
    reviewed_by = Column(Integer, nullable=True)
    reviewed_at = Column(DateTime, nullable=True)
    created_at = Column(DateTime, nullable=False, default=datetime.now)


class TransferRequest(BaseModel):
    """权益转让（16 条件；双方冻结；72h 超时自动 expired）。"""

    __tablename__ = "transfer_requests"

    STATUS_PENDING = "pending"
    STATUS_APPROVED = "approved"
    STATUS_REJECTED = "rejected"
    STATUS_CANCELLED = "cancelled"
    STATUS_EXPIRED = "expired"

    source_child_id = Column(Integer, nullable=False, index=True)
    target_child_id = Column(Integer, nullable=False, index=True)
    status = Column(String(20), nullable=False, default=STATUS_PENDING, index=True)
    expires_at = Column(DateTime, nullable=False, comment="审核截止（超时 expired）")
    review_remark = Column(String(200), nullable=True)
    reviewed_by = Column(Integer, nullable=True)
    reviewed_at = Column(DateTime, nullable=True)
    created_at = Column(DateTime, nullable=False, default=datetime.now)


class ObservationReport(BaseModel):
    """观察期评估报告（馆员上传图片 ≤9 张；家长孩子可见）。"""

    __tablename__ = "observation_reports"

    child_id = Column(Integer, nullable=False, index=True)
    images = Column(Text, nullable=False, default="[]", comment="图片路径 JSON 数组")
    remark = Column(String(500), nullable=True, comment="馆员备注")
    uploaded_by = Column(Integer, nullable=True)
    created_at = Column(DateTime, nullable=False, default=datetime.now)
