# backend/domain/identity/models.py — 家长 / 孩子（会员状态机 R-301）

from sqlalchemy import Column, Date, DateTime, Index, Integer, Numeric, SmallInteger, String

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
        MEMBER_FORMAL: {MEMBER_FORMAL, MEMBER_EXPIRED, MEMBER_WITHDRAWN},  # self-loop = 续费顺延（V1.1 §3.4）
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

    def can_transition(self, new_status: str) -> bool:
        return new_status in self.ALLOWED_TRANSITIONS.get(self.member_status, set())

    @property
    def is_active_member(self) -> bool:
        """有效会员（观察期/待评估/正式）——听全馆音频、借书资格的判定口径。"""
        return self.member_status in (
            self.MEMBER_OBSERVATION,
            self.MEMBER_PENDING_EVALUATION,
            self.MEMBER_FORMAL,
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

    ALLOWED_TRANSITIONS = {
        STATUS_PENDING_PAYMENT: {STATUS_PAID, STATUS_CANCELLED},
        STATUS_PENDING_MANUAL: {STATUS_PAID, STATUS_CANCELLED},
        STATUS_PAID: set(),
        STATUS_CANCELLED: set(),
    }

    order_no = Column(String(32), nullable=False, comment="订单号")
    order_type = Column(String(24), nullable=False, index=True, comment="订单类型")
    parent_id = Column(Integer, nullable=False, index=True)
    child_id = Column(Integer, nullable=True, index=True, comment="孩子ID（活动费可能家长级）")
    amount = Column(Numeric(10, 2), nullable=False, comment="实收金额（Decimal 铁律）")
    status = Column(String(24), nullable=False, default=STATUS_PENDING_MANUAL, index=True)
    pay_method = Column(
        String(24), nullable=True, comment="收款方式（wechat/scan/alipay/transfer/card/cash）"
    )
    paid_at = Column(DateTime, nullable=True, comment="收款确认时间")
    paid_by = Column(Integer, nullable=True, comment="确认收款的管理员ID")
    remark = Column(String(200), nullable=False, default="", comment="备注/凭证说明")

    def can_transition(self, new_status: str) -> bool:
        return new_status in self.ALLOWED_TRANSITIONS.get(self.status, set())
