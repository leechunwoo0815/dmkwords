// frontend/utils/platform.js — 平台判断与 iOS 支付引导（P1-1：统一各页重复判定）
const req = require('./request')

/** 是否 iOS 平台（统一封装，各页不再重复 wx.getWindowInfo/getDeviceInfo） */
function isIOS() {
  try {
    return wx.getWindowInfo().platform === 'ios'
  } catch (e) {
    return false
  }
}

/**
 * iOS 虚拟服务办理指引弹窗（A5/P1-1/终审 P1-4）
 * 微信对 iOS 虚拟商品限制严于 Apple：禁价格展示、购买按钮与购买指引文案。
 * 因此 iOS 端不展示价格与支付按钮，仅提供门店/客服联系入口。
 * @param {string} productName 产品名（如 观察期/正式会员）
 */
async function showIOSContactGuide(productName) {
  let contact = {}
  try {
    contact = await req.get('/venue/contact', null, { auth: false })
  } catch (e) { /* 联系信息获取失败不阻塞 */ }
  const lines = [`iOS 端暂不支持在线办理${productName || '该服务'}，请联系门店或客服完成办理。`]
  if (contact.venue_name) lines.push(`办理门店：${contact.venue_name}`)
  if (contact.phone) lines.push(`门店电话：${contact.phone}`)
  if (contact.wechat) lines.push(`客服微信：${contact.wechat}`)
  wx.showModal({
    title: '联系门店办理',
    content: lines.join('\n'),
    showCancel: false,
    confirmText: '我知道了',
  })
}

module.exports = { isIOS, showIOSContactGuide }
