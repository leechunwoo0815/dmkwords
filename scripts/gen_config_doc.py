#!/usr/bin/env python3
"""从 SystemConfig.DEFAULTS 生成《PRD/动态配置清单.md》

用法: PYTHONPATH=. venv/bin/python scripts/gen_config_doc.py [--check]
  默认重写文档；--check 仅校验文档与代码是否一致（CI 可用），不一致退出码 1。

维护纪律:
  - DEFAULTS 新增配置键后必须在本脚本 SECTIONS 中登记分节归属，否则报错
  - 管控级别自动取 backend/domain/admin/config_levels.py 的 level_of()
"""

import argparse
import sys
from datetime import date
from pathlib import Path

PROJECT_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_DIR))

from backend.domain.admin.config_levels import (  # noqa: E402
    LEVEL_FREE,
    LEVEL_LOCKED,
    LEVEL_WARNING,
    level_of,
)

from backend.domain.admin.models import SystemConfig  # noqa: E402

DOC_PATH = PROJECT_DIR / "PRD" / "动态配置清单.md"

LEVEL_LABEL = {
    LEVEL_LOCKED: "🔒 锁定级",
    LEVEL_WARNING: "⚠️ 警告级",
    LEVEL_FREE: "✅ 自由级",
}

# 分节归属（顺序即文档顺序；新增键必须登记，否则脚本报错提醒）
SECTIONS: list[tuple[str, list[str]]] = [
    (
        "一、未付费体验用户",
        [
            "trial_pages",
            "vocab_lookup_limit",
            "enable_trial_reading",
            "enable_vocab_lookup",
        ],
    ),
    (
        "二、会员管理",
        [
            "observation_days",
            "member_days",
            "member_grace_days",
            "renewal_discount",
            "multi_child_discount",
            "refund_free_days",
            "upgrade_deduct_enabled",
            "parent_course_required",
        ],
    ),
    (
        "三、借阅规则",
        [
            "borrow_limit",
            "borrow_period_days",
            "due_remind_days",
            "bookshelf_limit",
        ],
    ),
    (
        "四、逾期与损坏（B7/B9/B10）",
        [
            "overdue_fine_per_day",
            "overdue_grace_days",
            "overdue_fine_cap_ratio",
            "first_overdue_free",
            "lost_book_fine_multiplier",
            "lost_search_days",
            "damage_dual_review",
        ],
    ),
    (
        "五、押金（A2/B11/E1）",
        [
            "deposit_amount",
            "deposit_refund_auto_approve",
            "deposit_partial_refund_amount",
            "deposit_partial_refund_books",
            "deposit_pending_expire_minutes",
        ],
    ),
    (
        "六、退款与审核（A4/E1/E2/E7）",
        [
            "refund_auto_approve_max",
            "review_sla_hours",
        ],
    ),
    (
        "七、预约（B4）",
        [
            "reservation_expire_hours",
            "reservation_remind_hours",
            "waitlist_notified_ttl_hours",
        ],
    ),
    (
        "八、晋级与测验（C2/C6/D4）",
        [
            "default_required_books",
            "quiz_pass_rate",
            "quiz_total_questions",
            "quiz_pass_count",
            "require_teacher_review",
            "submission_auto_approve",
            "submission_min_minutes",
            "quiz_cooldown_minutes",
            "quiz_low_level_max_sort",
            "quiz_low_level_questions",
            "quiz_low_level_pass_count",
        ],
    ),
    (
        "九、打卡规则（C1）",
        [
            "checkin_min_minutes",
            "checkin_min_vocab",
            "daily_checkin_limit",
        ],
    ),
    (
        "十、场馆信息",
        [
            "venue_name",
            "venue_address",
            "service_wechat",
        ],
    ),
    (
        "十一、订单与价格",
        [
            "order_expire_minutes",
            "amount_override_alert_ratio",
            "price_parent_course",
            "price_observation",
            "price_official_member",
            "price_quarterly",
            "price_semi_annual",
            "original_price_parent_course",
            "original_price_official_member",
        ],
    ),
    (
        "十二、管理员与安全",
        [
            "admin_token_expire_hours",
        ],
    ),
    (
        "十三、活动",
        [
            "activity_cancel_hours",
        ],
    ),
    (
        "十四、通知提醒",
        [
            "member_expire_remind_days",
            "observation_remind_days",
        ],
    ),
    (
        "十五、数据保留（H5）",
        [
            "data_retention_finance_years",
            "data_retention_behavior_years",
            "data_retention_message_years",
            "voice_retention_months",
        ],
    ),
]

HEADER = """# 后台动态配置清单

> 更新日期：{today}
> 配置总数：{count} 项（全部在 SystemConfig.DEFAULTS，由 scripts/gen_config_doc.py 从代码生成保证一致）
> 所有配置存储在 `system_config` 表，管理员可在后台直接修改，无需重启
> 默认值定义在 `backend/domain/admin/models.py` 的 `SystemConfig.DEFAULTS`
> 代码中通过 `backend/common/config_service.py` 的 `ConfigService` 统一读取
> E3 三级管控：🔒 锁定级仅超管可改；⚠️ 警告级修改需 confirmed=true 二次确认；✅ 自由级直接改


---

"""

FOOTER = """
---

## 修改接口说明（给甲方）

- 以上所有数值均可在管理后台「设置」页修改，即时生效（5 分钟缓存），无需发版；
- 若贵方对决策表中某题的选择与默认值不同（如押金金额、观察期天数、宽限期），改对应配置项即可，无需改代码；
- 锁定级配置（价格/押金/折扣）只有超级管理员能改；警告级配置修改时需二次确认并记录审计日志。
"""


def render() -> str:
    defaults = SystemConfig.DEFAULTS
    assigned = {k for _, keys in SECTIONS for k in keys}
    missing = set(defaults) - assigned
    orphan = assigned - set(defaults)
    if missing:
        raise SystemExit(
            f"以下配置键未登记分节归属（请编辑 scripts/gen_config_doc.py SECTIONS）: {sorted(missing)}"
        )
    if orphan:
        raise SystemExit(f"SECTIONS 中登记了 DEFAULTS 不存在的键: {sorted(orphan)}")

    parts = [HEADER.format(today=date.today().isoformat(), count=len(defaults))]
    for title, keys in SECTIONS:
        parts.append(f"\n## {title}\n\n")
        parts.append("| 配置键 | 默认值 | 管控级别 | 说明 |\n")
        parts.append("|--------|--------|---------|------|\n")
        for key in keys:
            value, _type, desc = defaults[key]
            parts.append(f"| `{key}` | {value} | {LEVEL_LABEL[level_of(key)]} | {desc} |\n")
        parts.append("\n")
    parts.append(FOOTER)
    return "".join(parts)


def main():
    parser = argparse.ArgumentParser(description="生成动态配置清单文档")
    parser.add_argument("--check", action="store_true", help="仅校验文档与代码一致，不写文件")
    args = parser.parse_args()

    content = render()
    if args.check:
        current = DOC_PATH.read_text(encoding="utf-8")
        # 日期行每日变化，校验时忽略
        normalize = lambda s: "\n".join(  # noqa: E731
            line for line in s.splitlines() if not line.startswith("> 更新日期")
        )
        if normalize(current) != normalize(content):
            print("FAIL: 动态配置清单.md 与 DEFAULTS 不一致，请运行 scripts/gen_config_doc.py")
            sys.exit(1)
        print("OK: 动态配置清单.md 与 DEFAULTS 一致")
        return

    DOC_PATH.write_text(content, encoding="utf-8")
    print(f"OK: 已生成 {DOC_PATH}（{len(SystemConfig.DEFAULTS)} 项）")


if __name__ == "__main__":
    main()
