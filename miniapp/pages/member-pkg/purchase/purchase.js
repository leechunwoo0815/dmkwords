// pages/member-pkg/purchase/purchase.js — 会员购买/续费（WM12-A：接通线上支付）
// 口径：
// ① 价格一律配置下发（GET /api/miniapp/payment/plans），本页零硬编码金额；
// ② **iOS 不给支付入口**（微信虚拟商品合规）——iOS 只见价格 + 到店引导；
// ③ 下单价以服务端下单时重算为准（本页展示价只是展示，二孩折扣按下单时刻判定）；
// ④ WM12-C（审查 P0-3，用户裁定）：**线上支付只允许已绑微信的家长**——服务端
//    `can_pay_online=false`（手机号登录且未绑微信）时本页不出现「立即开通」，
//    只给"请用微信一键登录"的引导（点了必 422，入口就不该存在）。
const api = require('../../../utils/api')
const { isIOS } = require('../../../utils/platform')

const HINT_ONLINE = '线上支付即时开通会员；价格以本页展示为准（下单时服务端复核）。'
const HINT_LOGIN = '线上开通仅支持微信一键登录的家长；当前登录方式不支持，可重新用微信一键登录或到店办理。'
const HINT_STORE = '会员方案与价格以到店公示为准；iOS 端不提供在线开通。'

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
    showPay: false,      // 非 iOS + 服务端开着线上支付 + 家长已绑微信 → 才出现「立即开通」
    canPayOnline: false, // WM12-C：服务端判据（家长是否已绑微信）
    payBlocked: '',      // '' | off（未开通道）| login（未绑微信）| ios
    paying: false,       // WM12-C（审查 P2-10）：请求期间禁止重复点击
    headHint: HINT_STORE,
    paymentEnabled: true,
    plans: [],
    loading: true,
    // P2-15（第九轮审查）：取数失败必须自成一态——原先 catch 只关 loading，页面于是只剩
    // 首屏默认文案 `HINT_STORE`（"iOS 端不提供在线开通"）+ 空方案列表：**把网络故障讲成了
    // 平台政策**（家长以为线上开通被下掉了），且没有重试入口。
    loadError: '',
  },

  onLoad(options) {
    // options 优先；缺失时回落到本地 currentChildId（登录/切孩子时已写入）
    const childId = session.resolveChildId(options)
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
      const canPay = !!res.can_pay_online // WM12-C：家长是否已绑微信（服务端判据）
      this.setData({
        plans,
        paymentEnabled: enabled,
        canPayOnline: canPay,
        showPay: enabled && canPay && !this.data.isIOS,
        payBlocked: !enabled ? 'off' : (!canPay ? 'login' : (this.data.isIOS ? 'ios' : '')),
        headHint: enabled && canPay && !this.data.isIOS ? HINT_ONLINE
          : (!enabled || !canPay ? HINT_LOGIN : HINT_STORE),
        loading: false,
        loadError: '',
      })
    } catch (e) {
      // P2-15：失败自成一态（error-view + 重试），**不准**把失败渲染成"iOS 不提供"这类语义结论
      this.setData({
        loading: false,
        loadError: (e && e.message) || '会员方案加载失败',
        plans: [],
      })
    }
    finally { wx.hideLoading() }
  },

  onRetryLoad() { this.loadPlans() },

  // 下单 → 拉起支付（两步：先建单让服务端算价，再发起支付拿 pay_params）
  async onBuy(e) {
    // 2026-10-12 审查 P1：建单阶段也要挡重复点击（`_creating` 与 `_payOrder` 的 `paying` 分开，
    // 否则置位 paying 会让 _payOrder 直接 return 跳过支付）
    // P2-46：支付结果确认期间**锁住购买入口**（确认中再建单 = 再扣一次款）
    if (this.data.paying || this._creating || this._confirming) return
    const orderType = e.currentTarget.dataset.type
    const plan = (this.data.plans || []).filter((p) => p.order_type === orderType)[0]
    if (!plan || !plan.available) {
      wx.showToast({ title: (plan && plan.reason) || '暂不可购买', icon: 'none' })
      return
    }
    // 建单请求在途时快速连点会建出两笔待支付订单（服务端建单无查重）；`mask:true` 挡住点击穿透。
    this._creating = true
    wx.showLoading({ title: '创建订单', mask: true })
    let order = null
    try {
      order = await api.createOnlineOrder(this.data.childId, orderType)
    } catch (e) {
      wx.hideLoading()
      this._creating = false
      return // toast 已弹
    }
    wx.hideLoading()
    this._creating = false
    await this._payOrder(order.id)
  },

  // 发起支付：mock 通道服务端即时到账（无 pay_params）；真通道拉起 wx.requestPayment
  async _payOrder(orderId) {
    if (this.data.paying) return // WM12-C（审查 P2-10）：双击不产生两个预支付单
    this.setData({ paying: true })
    wx.showLoading({ title: '发起支付' })
    let res = null
    try {
      res = await api.payOrder(orderId)
    } catch (e) {
      wx.hideLoading()
      this.setData({ paying: false })
      return // toast 已弹
    }
    wx.hideLoading()
    this.setData({ paying: false })
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
        // P2-46（第八轮）：**wx.requestPayment 的 success 只代表"支付动作完成"，
        // 不代表后端已入账**——真通道是异步通知，回调可能晚几秒。
        // 原先这里直接 toast「支付成功」+ 刷新方案列表：回调未到时方案仍显示可买，
        // 家长再点一次 → 建第二笔并**再扣一次款**（formal_fee 提前续费还会 +365 天）。
        // 现在改为**查单确认**：轮询订单状态直到 paid（或超时如实告知"确认中"），
        // 期间锁住购买入口，确认后再刷会员态。
        this._confirmPaid(orderId)
      },
      fail: (err) => {
        // 用户主动取消不是错误：只在真失败时提示
        if (err && err.errMsg && err.errMsg.indexOf('cancel') >= 0) return
        wx.showToast({ title: '支付未完成，可在「我的订单」继续', icon: 'none' })
      },
    })
  },

  /** P2-46：支付后**查单确认**（轮询订单状态）→ 刷新会员态与方案列表；超时如实说"确认中"。 */
  async _confirmPaid(orderId) {
    this._confirming = true
    wx.showLoading({ title: '确认支付结果', mask: true })
    const deadline = Date.now() + 15000
    let paid = false
    while (Date.now() < deadline) {
      try {
        const rows = await api.myOrders(this.data.childId)
        const mine = (rows || []).filter((o) => o.id === orderId)[0]
        if (mine && mine.status === 'paid') {
          paid = true
          break
        }
      } catch (e) {
        // 单次查询失败不致命：继续轮询到超时
      }
      await new Promise((resolve) => setTimeout(resolve, 1500))
    }
    wx.hideLoading()
    this._confirming = false
    if (paid) {
      wx.showToast({ title: '支付成功', icon: 'success' })
      // 刷会员态（会员到期日/状态变了）+ 方案列表（已购方案应由服务端判为不可再买）
      const session = require('../../../utils/session')
      await session.refreshChildren(true)
      this.loadPlans()
      return
    }
    // 超时：**不许**说"支付成功"（可能还在回调路上），如实告知去哪儿看结果
    wx.showModal({
      title: '支付结果确认中',
      content: '银行/微信回执可能稍有延迟。请稍后到「我的订单」查看结果；若已扣款但订单未更新，请联系馆员处理。',
      showCancel: false,
      confirmText: '我知道了',
      success: () => this.loadPlans(),
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
