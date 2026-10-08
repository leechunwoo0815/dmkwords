// pages/member-pkg/purchase/purchase.js — 会员购买/续费（WM12-A：接通线上支付）
// 口径：
// ① 价格一律配置下发（GET /api/miniapp/payment/plans），本页零硬编码金额；
// ② **iOS 不给支付入口**（微信虚拟商品合规）——iOS 只见价格 + 到店引导；
// ③ 下单价以服务端下单时重算为准（本页展示价只是展示，二孩折扣按下单时刻判定）。
const api = require('../../../utils/api')
const { isIOS } = require('../../../utils/platform')

// 方案卖点（文案固定，价格与可用性都来自服务端）
const PLAN_FEATURES = {
  observation_fee: [
    '面向新入会孩子的适应期方案',
    '到店借阅 + 线上听读测验全功能',
    '到期后可转正式会员（馆方核定）',
  ],
  formal_fee: [
    '全馆藏书借阅额度与预约',
    '等级/积分/榜单/护照完整成长体系',
    '馆内活动优先报名',
  ],
  first_activity_fee: ['每个账号限购一次', '线上支付后即完成首场活动报名'],
  deposit: ['借阅前需缴纳，退会时按规则退还', '押金扣减明细可在押金页查看'],
}

Page({
  data: {
    childId: 0,
    childName: '',
    isIOS: false,
    showPay: false,      // 非 iOS 且服务端开着线上支付 → 才出现「立即开通」
    paymentEnabled: true,
    plans: [],
    loading: true,
  },

  onLoad(options) {
    // options 优先；缺失时回落到本地 currentChildId（登录/切孩子时已写入）
    const childId = Number(options.child_id || wx.getStorageSync('currentChildId') || 0)
    this.setData({
      childId,
      childName: decodeURIComponent(options.child_name || ''),
      isIOS: isIOS(),
    })
  },

  onShow() { this.loadPlans() },

  async loadPlans() {
    if (!this.data.childId) {
      this.setData({ loading: false })
      return
    }
    wx.showLoading({ title: '加载中' })
    try {
      const res = await api.getPaymentPlans(this.data.childId)
      const enabled = !!res.payment_enabled
      const plans = (res.items || []).map((p) => ({
        order_type: p.order_type,
        label: p.label,
        // 正式会员用金色徽章（沿用改版前的视觉区分；类名由 JS 给出，wxss 里必须有定义——R16b）
        badgeCls: p.order_type === 'formal_fee' ? 'plan-badge-gold' : '',
        priceText: p.amount ? `￥${p.amount}` : '价格以到店公示为准',
        available: !!p.available,
        reason: p.reason || '',
        features: PLAN_FEATURES[p.order_type] || [],
      }))
      this.setData({
        plans,
        paymentEnabled: enabled,
        showPay: enabled && !this.data.isIOS,
        loading: false,
      })
    } catch (e) { this.setData({ loading: false }) /* toast 已弹 */ }
    finally { wx.hideLoading() }
  },

  // 下单 → 拉起支付（两步：先建单让服务端算价，再发起支付拿 pay_params）
  async onBuy(e) {
    const orderType = e.currentTarget.dataset.type
    const plan = (this.data.plans || []).filter((p) => p.order_type === orderType)[0]
    if (!plan || !plan.available) {
      wx.showToast({ title: (plan && plan.reason) || '暂不可购买', icon: 'none' })
      return
    }
    wx.showLoading({ title: '创建订单' })
    let order = null
    try {
      order = await api.createOnlineOrder(this.data.childId, orderType)
    } catch (e) {
      wx.hideLoading()
      return // toast 已弹
    }
    wx.hideLoading()
    await this._payOrder(order.id)
  },

  // 发起支付：mock 通道服务端即时到账（无 pay_params）；真通道拉起 wx.requestPayment
  async _payOrder(orderId) {
    wx.showLoading({ title: '发起支付' })
    let res = null
    try {
      res = await api.payOrder(orderId)
    } catch (e) {
      wx.hideLoading()
      return // toast 已弹
    }
    wx.hideLoading()
    if (res.already_paid || res.instant_paid) {
      wx.showToast({ title: res.already_paid ? '订单已支付' : '支付成功', icon: 'success' })
      this.loadPlans()
      return
    }
    const p = res.pay_params || {}
    wx.requestPayment({
      timeStamp: p.timeStamp,
      nonceStr: p.nonceStr,
      package: p.package,
      signType: p.signType,
      paySign: p.paySign,
      success: () => {
        wx.showToast({ title: '支付成功', icon: 'success' })
        this.loadPlans()
      },
      fail: (err) => {
        // 用户主动取消不是错误：只在真失败时提示
        if (err && err.errMsg && err.errMsg.indexOf('cancel') >= 0) return
        wx.showToast({ title: '支付未完成，可在「我的订单」继续', icon: 'none' })
      },
    })
  },

  onStoreGuide() {
    wx.showModal({
      title: '到店办理',
      content: '请携带孩子到店，由馆员核定方案与价格后办理；也可按门店公示的联系方式联系馆员。',
      showCancel: false,
      confirmText: '我知道了',
    })
  },

  goOrders() {
    wx.navigateTo({
      url: `/pages/order-pkg/order-history/order-history?child_id=${this.data.childId}` +
        `&child_name=${encodeURIComponent(this.data.childName)}`,
    })
  },
})
