# backend/common/types.py
"""
[What] 共享枚举和类型定义
[Why] 业务常量分散在各 model 中，跨域使用时需要互相 import
[How] 在 common 层定义共享枚举，各 model 引用

架构意图：
  - OrderType、PayStatus 等枚举被多个域使用，不应定义在单个 model 中
  - 统一定义在 common/types.py，各 model/service/router 引用
  - 新增枚举类型优先放在此处，如果只被一个域使用，也可以放在该域的 models.py 中
"""

from decimal import Decimal
from enum import IntEnum

# ============================================================
# 测评通过阈值（跨域共享常量）
# ============================================================

PASS_THRESHOLD = Decimal("0.80")


# ============================================================
# 订单相关枚举
# ============================================================


class OrderType(IntEnum):
    """订单类型"""

    PARENT_COURSE = 1  # 亲子课
    OBSERVATION = 2  # 观察期
    OFFICIAL_MEMBER = 3  # 正式会员
    QUARTERLY = 4  # 季度会员
    SEMI_ANNUAL = 5  # 半年会员


class PayStatus(IntEnum):
    """支付状态"""

    PENDING = 0  # 待支付
    PAID = 1  # 已支付
    FAILED = 2  # 支付失败
    REFUNDING = 3  # 退款中
    REFUNDED = 4  # 已退款
    CLOSED = 5  # 已关闭


# ============================================================
# 会员状态枚举
# ============================================================


class MemberStatus(IntEnum):
    """孩子会员状态"""

    TRIAL = 0  # 体验用户
    OBSERVATION = 1  # 观察期会员
    OFFICIAL = 2  # 正式会员
    EXPIRED = 3  # 已过期
    EXITED = 4  # 已退出
    ALUMNI = 5  # 校友（F2：满15岁毕业，不可借书可查看历史）


# ============================================================
# 借阅相关枚举（V3.1）
# ============================================================


class BorrowStatus(IntEnum):
    """借阅状态"""

    BORROWING = 0  # 借阅中
    RETURNED = 1  # 已归还
    OVERDUE = 2  # 已逾期
    LOST = 3  # 丢失


class BookCopyStatus(IntEnum):
    """实体书副本状态（六类：可借/已借出/维修中/报废/损坏/丢失）"""

    AVAILABLE = 0  # 可借
    BORROWED = 1  # 已借出
    MAINTENANCE = 2  # 维修中
    SCRAPPED = 3  # 报废
    DAMAGED = 4  # 损坏（T3.6a 引入）
    LOST = 5  # 丢失（T3.6a 引入）


# ============================================================
# 押金相关枚举（V3.1）
# ============================================================


class DepositStatus(IntEnum):
    """押金状态"""

    UNPAID = 0  # 未支付
    PENDING = 5  # 支付中（已调起支付，等待回调确认）
    PAID = 1  # 已支付
    REFUNDED = 2  # 已退款
    DEDUCTED = 3  # 已扣除
    REFUNDING = 4  # 退款中（已通过审核，正在退款）
    REFUND_PENDING = 6  # 退款待审核（新增 V3.8：用户申请退款，需管理员审批）


# ============================================================
# 预约相关枚举（V3.1）
# ============================================================


class ReservationStatus(IntEnum):
    """预约状态"""

    PENDING = 0  # 待取书
    FULFILLED = 1  # 已取书
    EXPIRED = 2  # 已过期
    CANCELLED = 3  # 已取消


# ============================================================
# 书架相关枚举
# ============================================================


class BookshelfStatus(IntEnum):
    """书架（想读清单）状态"""

    WANT_READ = 0  # 想读
    FINISHED = 1  # 已读完
    REMOVED = 2  # 手动移除


# ============================================================
# 管理员角色枚举
# ============================================================


class AdminRole(IntEnum):
    """管理员角色"""

    ADMIN = 0  # 超级管理员
    STAFF = 1  # 运营人员
    TEACHER = 2  # 教师
