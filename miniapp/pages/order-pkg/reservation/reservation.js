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
    // P3（第一百一十六批）：含历史时的 50 条上限信号（后端响应头下发）
    truncated: false,
    historyLimit: 0,
  },

  onLoad(options) {
    // W7-12（2026-10-28）：缺参回落到当前孩子（各页统一口径）
    this.setData({ childName: options.child_name ? decodeURIComponent(options.child_name) : ((session.getCurrentChild() || {}).name || '') })
    // P3（第一百一十三批）：走统一解析（URL 参数合法才用，否则回落当前孩子）
    // **必须在 await 之前赋值**：onShow 紧随 onLoad 触发并调用 load()，冷启动时
    // loadConfigs() 是真请求（首个页面未缓存）→ 若等它回来再设 `_childId`，load()
    // 先跑一步就落进「还没有选择孩子」，且此后无人重拉（第一百三十四批 IDE 实测）。
    this._childId = session.resolveChildId(options)
    loadConfigs().then(({ configs }) => this.setData({ cfg: configs }))
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
      // P3（第一百一十六批）：`includeClosed` — 把已核销/已取消/已超时/副本异常的单子也取回来
      // （docs/04 S35：「家长端我的预约显示已核销」；原先后端只回 active，那三支徽标是死代码）。
      // `meta` 拿响应头里的 50 条上限信号。书架/首页/详情页仍用默认的"只回锁定中"口径。
      const { rows, meta } = await api.listReservations(this._childId, {
        includeClosed: true,
        meta: true,
      })
      const items = (rows || []).map((r) => {
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
      this.setData({
        items,
        truncated: !!meta.truncated,
        historyLimit: meta.limit || 0,
      })
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
