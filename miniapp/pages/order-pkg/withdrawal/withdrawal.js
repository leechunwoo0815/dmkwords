// pages/order-pkg/withdrawal/withdrawal.js — 退会申请（独立页，2026-09-15）
// 为什么独立成页：原先「退会」是退款页里的一个区块，用户面对两个提交按钮分不清
// 该点哪个（用户原话："我都不知道该点哪个"）。现在——
//   退款页 = 只办退款（观察期费/会员费/活动费/其他订单费用）
//   本页   = 只办退会，**不预先罗列费用**；审核通过后由后端把自动排查出的可退费用
//            明细展示给家长（数据来自审核时真实生成的退款单）
const api = require('../../../utils/api')
const session = require('../../../utils/session')

Page({
  data: {
    childName: '',
    list: [],
    reason: '',
    loading: true,
    hasActive: false, // 有进行中的申请 → 隐藏表单
  },

  onLoad(options) {
    this.setData({ childName: decodeURIComponent(options.child_name || '') })
    this._childId = Number(options.child_id)
  },

  onShow() {
    if (!session.ensureLogin()) return
    this.load()
  },

  async load() {
    this.setData({ loading: true })
    try {
      const raw = (await api.myWithdrawals(this._childId)) || []
      // 审核通过后的单子才有结算明细（审核前后端不罗列费用）
      const list = await Promise.all(
        raw.map(async (w) => {
          if (w.status === 'applying' || w.status === 'rejected' || w.status === 'cancelled') {
            return { ...w, settlement: null }
          }
          const settlement = await api
            .withdrawalSettlement(w.id, this._childId)
            .catch(() => null)
          return { ...w, settlement }
        })
      )
      this.setData({ list, hasActive: raw.some((w) => w.can_cancel) })
    } catch (e) {
      /* toast 已弹 */
    } finally {
      this.setData({ loading: false })
    }
  },

  onReason(e) {
    this.setData({ reason: e.detail.value })
  },

  async onSubmit() {
    const reason = (this.data.reason || '').trim()
    if (!reason) {
      wx.showToast({ title: '请填写退会原因', icon: 'none' })
      return
    }
    wx.showModal({
      title: '确认申请退会',
      content: '退会后会员权益终止、审核期间借书/预约冻结。审核通过后系统会自动排查可退费用并在本页列出。确定提交？',
      success: async (res) => {
        if (!res.confirm) return
        try {
          await api.applyWithdrawal(this._childId, reason)
          wx.showToast({ title: '已提交，等待审核', icon: 'success' })
          this.setData({ reason: '' })
          this.load()
        } catch (e) {
          /* toast 已弹（前提不满足会带具体原因） */
        }
      },
    })
  },

  onCancel(e) {
    const id = Number(e.currentTarget.dataset.id)
    wx.showModal({
      title: '撤销退会申请',
      content: '撤销后如仍需退会，需要重新申请。确定撤销？',
      confirmText: '撤销',
      confirmColor: '#d46b08',
      success: async (res) => {
        if (!res.confirm) return
        try {
          await api.cancelWithdrawal(id, this._childId)
          this.load()
        } catch (err) {
          /* toast 已弹 */
        }
      },
    })
  },
})
