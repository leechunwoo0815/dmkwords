// pages/order-pkg/benefit-transfer/benefit-transfer.js — 权益转让（WM10）
const api = require('../../../utils/api')
const session = require('../../../utils/session')

Page({
  data: {
    children: [],
    sourceId: null,
    targetId: null,
    conditions: null,
    allOk: false,
    records: [],
    loading: true,
  },

  onShow() {
    if (!session.ensureLogin()) return
    this.load()
  },

  async load() {
    this.setData({ loading: true })
    const children = session.getChildren()
    this.setData({ children })
    try {
      const records = await api.myTransfers()
      this.setData({ records: records || [] })
    } catch (e) { /* 静默 */ }
    this.setData({ loading: false })
  },

  onPickSource(e) {
    const id = Number(e.currentTarget.dataset.id)
    this.setData({ sourceId: id })
    this.check()
  },

  onPickTarget(e) {
    const id = Number(e.currentTarget.dataset.id)
    this.setData({ targetId: id })
    this.check()
  },

  async check() {
    const { sourceId, targetId } = this.data
    if (!sourceId || !targetId || sourceId === targetId) {
      this.setData({ conditions: null, allOk: false })
      return
    }
    try {
      const r = await api.transferConditions(sourceId, targetId)
      const conditions = r.conditions || []
      this.setData({
        conditions,
        allOk: conditions.length > 0 && conditions.every((c) => c.ok),
      })
    } catch (e) {
      this.setData({ conditions: null, allOk: false })
    }
  },

  async onSubmit() {
    const { sourceId, targetId, allOk } = this.data
    if (!allOk) {
      wx.showToast({ title: '还有条件不满足，看上方列表', icon: 'none' })
      return
    }
    const res = await wx.showModal({
      title: '发起转让',
      content: '提交后两个孩子相关操作冻结，管理员 72 小时内审核；通过后立即生效。',
      confirmText: '提交申请',
    })
    if (!res.confirm) return
    try {
      await api.applyTransfer(sourceId, targetId)
      wx.showToast({ title: '已提交', icon: 'success' })
      this.load()
    } catch (e) { /* toast 已弹（差什么会逐条说明） */ }
  },

  async onCancel(e) {
    const id = e.currentTarget.dataset.id
    const res = await wx.showModal({ title: '撤销转让', content: '确定撤销？双方立即解锁。' })
    if (!res.confirm) return
    try {
      await api.cancelTransfer(id)
      this.load()
    } catch (err) { /* toast 已弹 */ }
  },
})
