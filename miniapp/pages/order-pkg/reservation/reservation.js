// pages/order-pkg/reservation/reservation.js — 我的预约（WM6）
const api = require('../../../utils/api')

const STATUS_TEXT = {
  active: '锁定中',
  expired: '已超时',
  cancelled: '已取消',
  checked_out: '已借出',
  exception: '副本异常',
}

Page({
  data: {
    childName: '',
    items: [],
    loading: true,
    now: 0,
  },

  onLoad(options) {
    this.setData({ childName: options.child_name ? decodeURIComponent(options.child_name) : '' })
    this._childId = options.child_id ? Number(options.child_id) : null
  },

  onShow() {
    this.load()
    this.setData({ now: Date.now() })
  },

  async load() {
    if (!this._childId) return
    this.setData({ loading: true })
    try {
      const res = await api.listReservations(this._childId)
      const items = (res || []).map((r) => ({
        ...r,
        statusText: STATUS_TEXT[r.status] || r.status,
        canCancel: r.status === 'active',
        remainMs: r.status === 'active' ? new Date(r.expires_at).getTime() - Date.now() : 0,
      }))
      this.setData({ items })
    } catch (e) {
      this.setData({ items: [] })
    } finally {
      this.setData({ loading: false })
    }
  },

  async onCancel(e) {
    const id = e.currentTarget.dataset.id
    const res = await wx.showModal({
      title: '取消预约',
      content: '取消后锁定的副本立即释放，确定取消？',
      confirmText: '取消预约',
      cancelText: '再想想',
    })
    if (!res.confirm) return
    try {
      await api.cancelReservation(id, this._childId)
      wx.showToast({ title: '已取消', icon: 'success' })
      this.load()
    } catch (err) { /* request.js 已 toast */ }
  },
})
