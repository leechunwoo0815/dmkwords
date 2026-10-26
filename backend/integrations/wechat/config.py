# backend/integrations/wechat/config.py
"""微信配置常量"""


# 订阅消息模板 ID（在微信后台申请后填入）
class SubscribeTemplate:
    """订阅消息模板"""

    BORROW_SUCCESS = ""  # 借书成功通知
    RETURN_REMIND = ""  # 还书提醒
    OVERDUE_REMIND = ""  # 逾期提醒
    LEVEL_UP = ""  # 晋级通知
    ORDER_SUCCESS = ""  # 订单支付成功
    ACTIVITY_REMIND = ""  # 活动提醒
    RESERVATION_READY = ""  # 预约取书通知
    RESERVATION_EXPIRING = ""  # 预约即将过期
    MEMBER_EXPIRE_REMIND = ""  # 会员续费提醒（到期前 30/15/7/3/2/1/当天）
    REFUND_RESULT = ""  # 退款审核结果通知


# 支付描述模板（口径见 docs/22：线下服务费 + 图书借阅押金，禁用课程/报名类措辞）
class PayDescription:
    ORDER_TEMPLATES = {
        1: "DmkWords亲子阅读活动",
        2: "DmkWords观察期会员服务",
        3: "DmkWords年度会员服务",
    }

    DEPOSIT = "DmkWords图书借阅押金"

    ACTIVITY = "DmkWords线下活动"
