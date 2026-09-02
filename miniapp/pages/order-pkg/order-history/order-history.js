// pages/order-pkg/order-history/order-history.js — 我的订单（插修4-X7 补齐幽灵页）
const api = require('../../../utils/api')

const TYPE_TEXT = {
  observation_fee: '观察期费', formal_fee: '年费',
  first_activity_fee: '首场活动', activity_fee: '活动费',
  deposit: '押金', deposit_supplement: '押金补缴',
}

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

const TYPE_ICON = {
  observation_fee: 'type1', formal_fee: 'type2',
  first_activity_fee: 'type3', activity_fee: 'type3',
  deposit: 'type2', deposit_supplement: 'type2',
}

Page({
  data: {
    childName: '',
    orders: [],
    tabs: TABS,
    activeTab: 'all',
    filtered: [],
  },

  onLoad(options) {
    this._childId = Number(options.child_id || 0)
    this.setData({ childName: decodeURIComponent(options.child_name || '') })
  },

  onShow() { this.load() },

  async load() {
    wx.showLoading({ title: '加载中' })
    try {
      const rows = await api.myOrders(this._childId)
      const orders = (rows || []).map((o) => ({
        ...o,
        typeText: TYPE_TEXT[o.order_type] || o.order_type,
        statusText: STATUS_TEXT[o.status] || o.status,
        icon: TYPE_ICON[o.order_type] || 'type1',
        refundText: o.refund_status ? (REFUND_STATUS_TEXT[o.refund_status] || o.refund_status) : '',
        timeText: (o.paid_at || o.created_at || '').replace('T', ' ').slice(0, 16),
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

  onBack() { wx.navigateBack({ delta: 1 }) },
})
