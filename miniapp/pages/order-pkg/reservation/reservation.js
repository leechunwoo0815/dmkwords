// pages/order-pkg/reservation/reservation.js — 我的预约（WM6）
const api = require('../../../utils/api')
const session = require('../../../utils/session')
const { loadConfigs, getConfigs } = require('../../../utils/configs')

const STATUS_TEXT = {
  active: '锁定中',
  expired: '已超时',
  cancelled: '已取消',
  checked_out: '已借出',
  exception: '副本异常',
}

Page({
  data: {
    cfg: getConfigs(),  // 业务口径（后端下发：预约保留小时）
    childName: '',
    items: [],
    loading: true,
    now: 0,
  },

  async onLoad(options) {
    const { configs } = await loadConfigs()
    this.setData({ cfg: configs })
    this.setData({ childName: options.child_name ? decodeURIComponent(options.child_name) : '' })
    // P3（第一百一十三批）：走统一解析（URL 参数合法才用，否则回落当前孩子）
    this._childId = session.resolveChildId(options)
  },

  onShow() {
    this.load()
    this.setData({ now: Date.now() })
  },

  onRetryLoad() { this.load() },

  async load() {
    if (!this._childId) {
      // 无孩子：**必须落 loading=false**（原先早退在置 loading 之前 → 永久「加载中…」）
      this.setData({ loading: false, noChild: true, items: [] })
      return
    }
    this.setData({ loading: true, noChild: false, loadError: '' })
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
      // P2-45 同族：失败**不许伪装成空列表**（原先把错误吞成 items:[]）
      this.setData({ items: [], loadError: (e && e.message) || '加载失败' })
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
