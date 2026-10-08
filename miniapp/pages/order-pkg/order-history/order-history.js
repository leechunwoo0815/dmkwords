// pages/order-pkg/order-history/order-history.js — 我的订单（插修4-X7 补齐幽灵页）
const api = require('../../../utils/api')
const labels = require('../../../utils/labels')
const { isIOS } = require('../../../utils/platform')


// 状态中文映射对齐管理端 RefundCenter STATUS_LABEL 口径
const STATUS_TEXT = {
  pending_payment: '待支付',
  pending_manual_confirm: '待确认',
  paid: '已支付',
  cancelled: '已取消',
  refunded: '已退款',
}

// 退款链路状态（R-308，独立于订单主状态）——同管理端口径
const REFUND_STATUS_TEXT = {
  pending: '退款待审核', approved: '退款已通过', processing: '退款执行中',
  refunded: '已退款', failed: '退款失败',
}

const TABS = [
  { key: 'all', label: '全部' },
  { key: 'unpaid', label: '待支付' },
  { key: 'paid', label: '已支付' },
  { key: 'refunded', label: '已退款' },
]

// 订单类型 → 样式类 + 图标资产。**类名必须写全**：原来 JS 只给 'type1'，
// 而 WXSS 里是 `.order-icon-type1` → 选择器匹配不上，底色一直没生效（2026-09-21 目视发现）。
const TYPE_STYLE = {
  observation_fee: { cls: 'order-icon-type1', icon: '/icons/ui/card.png' },
  formal_fee: { cls: 'order-icon-type2', icon: '/icons/ui/card.png' },
  first_activity_fee: { cls: 'order-icon-type3', icon: '/icons/ui/ticket.png' },
  activity_fee: { cls: 'order-icon-type3', icon: '/icons/ui/ticket.png' },
  deposit: { cls: 'order-icon-type2', icon: '/icons/ui/wallet.png' },
  deposit_supplement: { cls: 'order-icon-type2', icon: '/icons/ui/wallet.png' },
}
const DEFAULT_TYPE_STYLE = { cls: 'order-icon-type1', icon: '/icons/ui/receipt.png' }

Page({
  data: {
    childName: '',
    orders: [],
    tabs: TABS,
    activeTab: 'all',
    filtered: [],
    showPay: false,   // 非 iOS + 家长已绑微信（WM12-C：登录口径见下）→ 待支付行出现「去支付」
    payHint: '',      // WM12-C（审查 P0-3）：不能支付时说明原因，不留"点了没反应"的死按钮
    paying: false,    // WM12-C（审查 P2-10）：请求期间禁止重复点击
  },

  onLoad(options) {
    this._childId = Number(options.child_id || 0)
    // WM12-C（审查 P0-3，用户裁定）：线上支付只允许**已绑微信**的家长
    // （`parent.wechat_bound` 来自登录载荷；老缓存没有该字段 → 按未绑处理，安全侧倒）
    const parent = wx.getStorageSync('parent') || {}
    const canPay = !!parent.wechat_bound
    const ios = isIOS()
    this.setData({
      childName: decodeURIComponent(options.child_name || ''),
      showPay: canPay && !ios,
      payHint: canPay
        ? (ios ? 'iOS 端不提供线上支付，可到店办理。' : '')
        : '线上支付仅支持微信一键登录的家长：可在登录页选择「微信一键登录」，或到店办理。',
    })
  },

  onShow() { this.load() },

  async load() {
    wx.showLoading({ title: '加载中' })
    try {
      const rows = await api.myOrders(this._childId)
      const orders = (rows || []).map((o) => ({
        ...o,
        typeText: labels.orderTypeText(o.order_type),  // 唯一映射源（custom 也翻成中文）
        statusText: STATUS_TEXT[o.status] || o.status,
        iconCls: (TYPE_STYLE[o.order_type] || DEFAULT_TYPE_STYLE).cls,
        iconUrl: (TYPE_STYLE[o.order_type] || DEFAULT_TYPE_STYLE).icon,
        refundText: o.refund_status ? (REFUND_STATUS_TEXT[o.refund_status] || o.refund_status) : '',
        timeText: (o.paid_at || o.created_at || '').replace('T', ' ').slice(0, 16),
        // WM12-A：待支付（线上/待人工确认）都可继续发起线上支付
        payable: o.status === 'pending_payment' || o.status === 'pending_manual_confirm',
      }))
      this.setData({ orders, filtered: this._filter(orders, this.data.activeTab) })
    } catch (e) { /* toast 已弹 */ }
    finally { wx.hideLoading() }
  },

  _filter(rows, tab) {
    if (tab === 'all') return rows
    if (tab === 'unpaid') return rows.filter((o) => o.status === 'pending_payment' || o.status === 'pending_manual_confirm')
    if (tab === 'paid') return rows.filter((o) => o.status === 'paid')
    return rows.filter((o) => o.status === 'refunded')
  },

  onTab(e) {
    const key = e.currentTarget.dataset.tab
    this.setData({ activeTab: key, filtered: this._filter(this.data.orders, key) })
  },

  // WM12-A：待支付订单继续支付（幂等键=订单号，服务端已支付会直接告知）
  async onPay(e) {
    if (this.data.paying) return // WM12-C（审查 P2-10）：双击不重复发起
    const orderId = e.currentTarget.dataset.id
    this.setData({ paying: true })
    wx.showLoading({ title: '发起支付' })
    let res = null
    try {
      res = await api.payOrder(orderId)
    } catch (err) {
      wx.hideLoading()
      this.setData({ paying: false })
      return // toast 已弹
    }
    wx.hideLoading()
    this.setData({ paying: false })
    if (res.already_paid || res.instant_paid) {
      wx.showToast({ title: res.already_paid ? '订单已支付' : '支付成功', icon: 'success' })
      this.load()
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
        this.load()
      },
      fail: (err) => {
        if (err && err.errMsg && err.errMsg.indexOf('cancel') >= 0) return
        wx.showToast({ title: '支付未完成，可再次点按重试', icon: 'none' })
      },
    })
  },

  onBack() { wx.navigateBack({ delta: 1 }) },
})
