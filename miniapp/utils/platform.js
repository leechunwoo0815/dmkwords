// frontend/utils/platform.js — 平台判断（P1-1：统一各页重复判定）
// F-L7/T34 处置（2026-09-13 本任裁定）：本模块曾引用未实现端点 /venue/contact，
//   且 showIOSContactGuide 与 components/pay-button 的 iOS 合规分支重复实现同一语义。
//   iOS 虚拟服务合规引导唯一实现收敛在 pay-button（iOS 分支不展示价格，仅到店指引）；
//   本模块仅保留 isIOS() 平台判定。consent 三段式（PRD F3）去留待用户拍板，本模块不涉及。
function isIOS() {
  try {
    return wx.getWindowInfo().platform === 'ios'
  } catch (e) {
    return false
  }
}

module.exports = { isIOS }
