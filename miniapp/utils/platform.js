// frontend/utils/platform.js — 平台判断（P1-1：统一各页重复判定）
// F-L7/T34 处置（2026-09-13 本任裁定）：本模块曾引用未实现端点 /venue/contact，
//   且 showIOSContactGuide 与 components/pay-button 的 iOS 合规分支重复实现同一语义。
//   iOS 虚拟服务合规引导唯一实现收敛在 pay-button（iOS 分支不展示价格，仅到店指引）；
//   本模块仅保留 isIOS() 平台判定。consent 三段式（PRD F3）去留待用户拍板，本模块不涉及。
//
// 2026-10-08 修复（iOS 打桩演练实测）：原实现读 `wx.getWindowInfo().platform`，而
//   `wx.getWindowInfo()` **不返回 platform 字段**（实测 keys 只有 pixelRatio/screenWidth/
//   screenHeight/windowWidth/windowHeight/statusBarHeight/safeArea/screenTop）——于是 isIOS()
//   恒为 false，**iOS 合规分支从来没生效过**（真机 iOS 会照常看到价格与「立即开通」按钮，
//   违反微信对 iOS 虚拟支付的规定）。改为官方新 API `wx.getDeviceInfo().platform`，
//   老基础库（无 getDeviceInfo）回退 `wx.getSystemInfoSync().platform`。
function isIOS() {
  try {
    if (typeof wx.getDeviceInfo === 'function') {
      const info = wx.getDeviceInfo()
      if (info && info.platform) return info.platform === 'ios'
    }
    if (typeof wx.getSystemInfoSync === 'function') {
      return wx.getSystemInfoSync().platform === 'ios'
    }
    return false
  } catch (e) {
    return false
  }
}

module.exports = { isIOS }
