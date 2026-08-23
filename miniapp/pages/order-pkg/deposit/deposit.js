// pages/order-pkg/deposit/deposit.js — 押金（R-312：家长端查看/补缴；R-313：退会禁）
const api = require('../../../utils/api')

const STATUS_TEXT = {
  unpaid: '还未缴纳',
  paid: '已缴足',
  partially_deducted: '部分扣除',
  fully_deducted: '已扣完',
  refunding: '退款中',
  refunded: '已退款',
}
const ENTRY_TEXT = {
  pay: '缴纳', deduct: '扣除', supplement: '补缴', refund: '退款',
}

Page({
  data: {
    childName: '',
    statusText: '',
    entryTexts: ENTRY_TEXT,
    dep: null,
    ledger: [],
    supplementDisabled: false,
    supplementHint: '',
    loading: false,
  },

  onLoad(options) {
    this.setData({ childName: decodeURIComponent(options.child_name || '') })
    this._childId = Number(options.child_id)
    // R-313：退会/未入会置灰（后端守卫为准，前端镜像提示）
    const status = options.member_status || ''
    if (status === 'withdrawn' || status === 'none') {
      this.setData({
        supplementDisabled: true,
        supplementHint: status === 'withdrawn' ? '已退会，押金随退会流程处理' : '入会后可补缴押金',
      })
    }
  },

  onShow() { this.load() },

  async load() {
    if (!this._childId) return
    this.setData({ loading: true })
    try {
      const d = await api.myDeposit(this._childId)
      const ledger = (d.ledger || []).map((r) => ({
        ...r,
        created_at: (r.created_at || '').replace('T', ' ').slice(0, 16),
      }))
      this.setData({
        dep: d,
        ledger,
        statusText: STATUS_TEXT[d.status] || d.status,
      })
    } catch (e) { /* request.js 已 toast */ }
    this.setData({ loading: false })
  },

  async onSupplement() {
    if (this.data.supplementDisabled) return
    try {
      const r = await api.createSupplementOrder(this._childId)
      wx.showModal({
        title: '补缴单已提交',
        content: `订单号 ${r.order_no}，请到店完成缴费（￥${r.amount}）`,
        showCancel: false,
      })
      this.load()
    } catch (e) { /* request.js 已 toast */ }
  },
})
