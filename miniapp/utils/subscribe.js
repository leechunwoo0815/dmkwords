// frontend/utils/subscribe.js — 微信订阅消息授权
// 模板 ID 与后端 backend/integrations/wechat/config.py SubscribeTemplate 一一对应；
// 微信后台申请后填入，未配置（空）时跳过授权请求（不打扰用户）。
// 订阅消息为一次性授权：用户接受后仅可推送一次，需在关键操作后请求。

const TEMPLATE_IDS = {
  memberExpire: '', // MEMBER_EXPIRE_REMIND 会员续费提醒
  returnRemind: '', // RETURN_REMIND 还书提醒
  reservationReady: '', // RESERVATION_READY 预约取书通知
  refundResult: '', // REFUND_RESULT 退款审核结果
  activityRemind: '', // ACTIVITY_REMIND 活动提醒
}

/**
 * 请求一次性订阅授权（模板未配置/能力缺失时静默跳过）
 * @param {string} tmplKey TEMPLATE_IDS 键名
 */
function requestSubscribe(tmplKey) {
  const tmplId = TEMPLATE_IDS[tmplKey]
  if (!tmplId || !wx.requestSubscribeMessage) return
  wx.requestSubscribeMessage({
    tmplIds: [tmplId],
    success() {
      // 用户接受/拒绝均静默——拒绝不打扰，接受后服务端可推送一次
    },
    fail() {
      // 授权弹窗失败（如用户未触发点击事件）静默
    },
  })
}

module.exports = { requestSubscribe, TEMPLATE_IDS }
