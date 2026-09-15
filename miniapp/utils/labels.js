// utils/labels.js — 后端枚举 → 中文展示的唯一映射源
// [Why] 2026-09-15：权益转让页直接把 `item.member_status` 渲染进 WXML，
// 未成年家长会看到「演示孩 formal / 小红 none」这种裸枚举；而 member.js 里
// 早已有一份正确映射。抽到这里，避免第二份、第三份各写各的。
const MEMBER_STATUS_TEXT = {
  none: '未入会',
  observation: '观察期',
  pending_evaluation: '待评估',
  formal: '正式会员',
  expired: '已过期',
  withdrawn: '已退会',
}

/** 会员状态中文；未知值兜底 '未入会'（不把英文枚举漏给用户）。 */
function memberStatusText(status) {
  return MEMBER_STATUS_TEXT[status] || '未入会'
}

// 订单类型中文。订单历史页与退款页各写了一份、且都漏了 custom（FEAT-080 自定义单）
// ——家长会在退款页看到裸英文「custom」，故收编成唯一源。
const ORDER_TYPE_TEXT = {
  observation_fee: '观察期费',
  formal_fee: '年费',
  first_activity_fee: '首场活动',
  activity_fee: '活动费',
  deposit: '押金',
  deposit_supplement: '押金补缴',
  custom: '其他费用',
}

/** 订单类型中文；未知值兜底 '其他费用'（宁可笼统，也不漏英文枚举）。 */
function orderTypeText(type) {
  return ORDER_TYPE_TEXT[type] || '其他费用'
}

module.exports = { MEMBER_STATUS_TEXT, memberStatusText, ORDER_TYPE_TEXT, orderTypeText }
