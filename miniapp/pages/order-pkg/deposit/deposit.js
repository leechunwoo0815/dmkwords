// pages/order-pkg/deposit/deposit.js — 押金（R-312：家长端查看/补缴；R-313：退会禁）
const api = require('../../../utils/api')
const session = require('../../../utils/session')

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
    loadError: '',     // 第一百三十四批：原先 catch 只靠 request.js 的瞬时 toast，toast 一过
                       // 页面就渲染成"暂无流水记录"——把故障读成"没有押金流水"（禁假空）
    supplementDisabled: false,
    supplementHint: '',
    loading: false,
  },

  onLoad(options) {
    const child = session.getCurrentChild() || {}
    this.setData({ childName: decodeURIComponent(options.child_name || '') || child.name || '' })
    // P3 统一解析（第一百三十四批补齐）：原先 `Number(options.child_id)` 缺参即 NaN → 整页空白
    this._childId = session.resolveChildId(options)
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

  onRetryLoad() { this.load() },

  async load() {
    if (!this._childId) return
    this.setData({ loading: true, loadError: '' })
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
    } catch (e) {
      // 失败**不许伪装成空**：清掉旧数据 + 落失败态（金额页尤其不能说成"没有流水"）
      this.setData({
        dep: null,
        ledger: [],
        loadError: (e && e.message) || '押金信息加载失败',
      })
    }
    this.setData({ loading: false })
  },

  async onSupplement() {
    if (this.data.supplementDisabled) return
    // 2026-10-12 审查 P1：补缴原先**完全没有** in-flight 锁 → 双击建出两笔同额补缴单。
    this.setData({ supplementDisabled: true })
    try {
      const r = await api.createSupplementOrder(this._childId)
      wx.showModal({
        title: '补缴单已提交',
        content: `订单号 ${r.order_no}，请到店完成缴费（￥${r.amount}）`,
        showCancel: false,
      })
      this.load()
    } catch (e) { /* request.js 已 toast */ } finally {
      this.setData({ supplementDisabled: false })
    }
  },
})
