// pages/order-pkg/refund-apply/refund-apply.js — 退款申请（WM10）
const api = require('../../../utils/api')
const session = require('../../../utils/session')

const TYPE_TEXT = {
  observation_fee: '观察期费', formal_fee: '年费',
  first_activity_fee: '首场活动', activity_fee: '活动费',
  deposit: '押金', deposit_supplement: '押金补缴',
}

Page({
  data: {
    childName: '',
    orders: [],
    refunds: [],
    selected: null,
    preview: null,
    reason: '',
    loading: true,
  },

  onLoad(options) {
    this.setData({ childName: decodeURIComponent(options.child_name || '') })
    this._childId = Number(options.child_id)
  },

  onShow() { this.load() },

  async load() {
    this.setData({ loading: true })
    try {
      const [orders, refunds] = await Promise.all([
        api.myOrders(this._childId),
        api.myRefunds(this._childId),
      ])
      this.setData({
        orders: (orders || []).map((o) => ({ ...o, typeText: TYPE_TEXT[o.order_type] || o.order_type })),
        refunds: refunds || [],
        selected: null, preview: null,
      })
    } catch (e) { /* toast 已弹 */ }
    finally { this.setData({ loading: false }) }
  },

  async onPick(e) {
    const order = e.currentTarget.dataset.order
    if (order.status !== 'paid') {
      wx.showToast({ title: '只有已支付订单可申请退款', icon: 'none' })
      return
    }
    this.setData({ selected: order, reason: '' })
    try {
      const pv = await api.refundPreview(this._childId, order.id)
      this.setData({ preview: pv })
    } catch (e) { /* toast 已弹 */ }
  },

  onReason(e) { this.setData({ reason: e.detail.value }) },

  async onSubmit() {
    const { selected, reason } = this.data
    if (!selected) { wx.showToast({ title: '先选择订单', icon: 'none' }); return }
    if (!reason.trim()) { wx.showToast({ title: '请填写退款原因', icon: 'none' }); return }
    try {
      await api.applyRefund(this._childId, selected.id, reason.trim())
      wx.showModal({
        title: '已提交', content: '管理员审核后执行退款；审核结果会在退款记录里通知你。',
        showCancel: false,
      })
      this.load()
    } catch (e) { /* toast 已弹（重复申请等） */ }
  },
})
