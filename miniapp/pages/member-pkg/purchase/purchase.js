// pages/member-pkg/purchase/purchase.js — 会员购买/续费（FEAT-083 占位：无在线支付）
// 宪法 §五.4：价格一律配置下发——小程序侧暂无公开配置端点（挂账），本页零硬编码金额，
// 仅做方案说明与到店引导；pay-button 的 iOS 分支天然承载"虚拟商品不在线开通"合规语义。
Page({
  data: {},

  onStoreGuide() {
    wx.showModal({
      title: '到店办理',
      content: '请携带孩子到店，由馆员核定方案与价格后办理；也可按门店公示的联系方式联系馆员。',
      showCancel: false,
      confirmText: '我知道了',
    })
  },
})
