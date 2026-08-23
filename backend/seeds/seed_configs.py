# backend/seeds/seed_configs.py — 业务配置种子（幂等，数值源自 PRD V1.1 第十二章速查表）
"""用法：python -m backend.seeds.seed_configs

所有业务数值进 SystemConfig（宪法铁律 6）。已存在的键跳过（保留运营期修改值）。
"""

from backend.database import get_session
from backend.domain.admin.models import SystemConfig
from backend.domain.admin.repository import SystemConfigRepository

# (key, value, type, category, description, display_name)
CONFIG_SEEDS = [
    # 借阅
    ("borrow_limit", "30", "int", "借阅", "可借上限（本）", "可借上限（本）"),
    ("borrow_days", "30", "int", "借阅", "借期（天）", "借期天数（天）"),
    ("renew_limit", "1", "int", "借阅", "每本书续借次数", "每本书可续借次数（次）"),
    ("renew_days", "7", "int", "借阅", "续借延长天数（从原到期日起算）", "续借延长天数（天）"),
    ("reservation_hours", "72", "int", "借阅", "预约锁定时长（小时）", "预约锁定时长（小时）"),
    (
        "allow_unpaid_offline_borrow",
        "false",
        "bool",
        "借阅",
        "未入会临时借书开关（默认关闭）",
        "允许未入会临时借书",
    ),
    (
        "ar_warning_range",
        "0.5",
        "float",
        "借阅",
        "借书 AR 超范围提示阈值（差值超过即提示，不拦截）",
        "AR 超范围提示阈值（±）",
    ),
    # 收费
    (
        "first_activity_fee",
        "99",
        "int",
        "收费",
        "首场亲子活动费（元，每账号一次）",
        "首场亲子活动费（元）",
    ),
    ("observation_fee", "500", "int", "收费", "观察期会员费（元/月）", "观察期会员费（元）"),
    ("formal_fee", "6000", "int", "收费", "正式会员年费（元/年）", "正式会员年费（元）"),
    (
        "second_child_discount_percent",
        "90",
        "int",
        "收费",
        "二孩年费折扣（%）",
        "二孩及以上年费折扣（%）",
    ),
    ("deposit_amount", "1200", "int", "收费", "图书押金（元/每个孩子）", "图书押金（元/每个孩子）"),
    # 测验与阅读
    ("quiz_questions_per_book", "5", "int", "测验", "每本书测验题量", "每本书测验题量（题）"),
    ("quiz_pass_percent", "80", "int", "测验", "测验及格线（%）", "测验及格线（%）"),
    ("quiz_max_attempts", "3", "int", "测验", "每本书终身提交机会", "每本书测验机会（次）"),
    (
        "audio_finish_threshold_percent",
        "95",
        "int",
        "阅读",
        "完播判定覆盖阈值（%）",
        "听完多少算读完（%）",
    ),
    # 积分
    (
        "words_per_point",
        "100",
        "int",
        "积分",
        "有效词数折算积分（词/分，零头保留）",
        "多少词折算 1 积分（词）",
    ),
    ("quiz_pass_bonus", "5", "int", "积分", "测验首次通过奖励分", "测验首次通过奖励（分）"),
    (
        "quiz_full_marks_bonus",
        "10",
        "int",
        "积分",
        "测验首次满分奖励分（与首过互斥取高）",
        "测验首次满分奖励（分）",
    ),
    (
        "checkin_7days_bonus",
        "10",
        "int",
        "积分",
        "连续打卡每满7天奖励分",
        "连续打卡满 7 天奖励（分）",
    ),
    (
        "checkin_30days_bonus",
        "50",
        "int",
        "积分",
        "连续打卡每满30天奖励分",
        "连续打卡满 30 天奖励（分）",
    ),
    # 成长
    (
        "level_up_books",
        "100",
        "int",
        "成长",
        "每级升级所需有效阅读本数（A-Z）",
        "每级升级所需本数（本）",
    ),
    (
        "progress_min_increment",
        "100",
        "int",
        "成长",
        "进步榜上榜最小周增量（词）",
        "进步榜上榜门槛（词/周）",
    ),
    (
        "milestone_nodes",
        "100000,500000,1000000,5000000,10000000,50000000",
        "string",
        "成长",
        "里程碑词数节点（逗号分隔）",
        "里程碑词数节点（词）",
    ),
    # 会员与转让
    (
        "transfer_review_timeout_hours",
        "72",
        "int",
        "会员",
        "权益转让审核超时（小时）",
        "权益转让审核超时（小时）",
    ),
    (
        "member_expire_remind_days",
        "30,14,7,0",
        "string",
        "会员",
        "会员到期提醒节点（天，0=当天）",
        "会员到期提醒节点（天）",
    ),
    # 活动
    (
        "activity_remind_days",
        "3,2,1,0",
        "string",
        "活动",
        "活动开始前提醒节点（天，0=当天）",
        "活动开始前提醒节点（天）",
    ),
    (
        "activity_refund_cutoff_hours",
        "2",
        "int",
        "活动",
        "线上退款关闭时点（活动开始前小时数）",
        "线上退款截止（开始前小时）",
    ),
    (
        "pending_payment_timeout_hours",
        "48",
        "int",
        "活动",
        "待支付订单超时取消（小时）",
        "待支付订单超时取消（小时）",
    ),
    # 系统
    (
        "admin_token_expire_hours",
        "8",
        "int",
        "系统",
        "后台登录有效期（小时）",
        "后台登录有效期（小时）",
    ),
]


def seed() -> list[str]:
    db = get_session()
    created: list[str] = []
    try:
        repo = SystemConfigRepository(db)
        for key, value, vtype, category, desc, display_name in CONFIG_SEEDS:
            existing = repo.get_by_key(key)
            if existing:
                # 幂等升级：老数据回填中文显示名（不覆盖运营期已改的值）
                if not existing.display_name:
                    existing.display_name = display_name
                    repo.update(existing)
                continue
            repo.create(
                SystemConfig(
                    config_key=key,
                    config_value=value,
                    default_value=value,
                    value_type=vtype,
                    category=category,
                    description=desc,
                    display_name=display_name,
                )
            )
            created.append(key)
        db.commit()
        return created
    finally:
        db.close()


if __name__ == "__main__":
    result = seed()
    print(
        f"配置种子完成，新建 {len(result)} 项"
        + ("：" + ", ".join(result) if result else "（均已存在）")
    )
