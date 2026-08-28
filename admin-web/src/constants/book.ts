// 图书相关共享校验规则
// R7：AR 值三路统一校验的前端侧（与 backend schemas validate_ar_level 同规则）
export const AR_LEVEL_RULE = {
  validator: (_rule: unknown, value: string | undefined | null) =>
    !value || (/^\d+(\.\d+)?$/.test(value.trim()) && parseFloat(value) <= 12.9)
      ? Promise.resolve()
      : Promise.reject(new Error("AR 值需为 0-12.9 的数字")),
};
