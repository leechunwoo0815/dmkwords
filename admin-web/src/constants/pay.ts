// 收款方式枚举（P3 管理端族，2026-10-22 第一百一十二批）
//
// 为什么抽到这里：原先 `PAY_METHOD_OPTIONS` 只写在 MemberManage 里且**只有 5 项**，
// 缺 `wechat`（线上支付回调落库就是该值）→ 管理端订单/退款列表显示**裸英文 `wechat`**；
// 且 RefundCenter 直接渲染 `r.pay_method` 原文，连映射都没走。
// 取值与后端契约同源：`backend/domain/identity/schemas.py` 的
// `pattern="^(scan|alipay|transfer|card|cash|wechat)$"`。
export const PAY_METHOD_OPTIONS = [
  { value: "scan", label: "微信扫码" },
  { value: "alipay", label: "支付宝" },
  { value: "transfer", label: "对公转账" },
  { value: "card", label: "刷卡" },
  { value: "cash", label: "现金" },
  { value: "wechat", label: "微信支付（线上）" },
];

/** 收款方式显示名（未知值原样返回，便于发现后端新增枚举；空值显示「—」）。 */
export function payMethodLabel(v?: string | null): string {
  if (!v) return "—";
  return PAY_METHOD_OPTIONS.find((o) => o.value === v)?.label ?? v;
}
