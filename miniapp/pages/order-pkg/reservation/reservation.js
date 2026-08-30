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
      const items = (res || []).map((r) => {
        const remainMs = r.status === 'active' ? new Date(String(r.expires_at).replace(/-/g, '/')).getTime() - Date.now() : 0
        let remainText = ''
        if (remainMs > 0) {
          const hours = Math.floor(remainMs / 3600000)
          remainText = hours > 0 ? `剩余 ${hours} 小时` : `剩余 ${Math.max(1, Math.floor(remainMs / 60000))} 分钟`
        }
        return {
          ...r,
          statusText: STATUS_TEXT[r.status] || r.status,
          canCancel: r.status === 'active',
          remainMs,
          remainText,
        }
      })
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
