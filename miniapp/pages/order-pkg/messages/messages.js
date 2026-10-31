// pages/order-pkg/messages/messages.js — 消息中心（WM11 返工：真分页/详情/分类后端过滤/下拉刷新）
const api = require('../../../utils/api')

const CATEGORIES = [
  { key: '', label: '全部' },
  { key: '资金', label: '资金' },
  { key: '借阅', label: '借阅' },
  { key: '阅读', label: '阅读' },
  { key: '会员', label: '会员' },
  { key: '活动', label: '活动' },
  { key: '预约', label: '预约' },
  { key: '报告', label: '报告' },
  // P3（第一百一十三批）：后端 `Notification.CATEGORY_OTHER = "其他"` 真实存在，
  // 前端分类原先**没有这一档** → 这类消息（评估报告上传/转让结果/押金到账等）筛不出来
  { key: '其他', label: '其他' },
]

const PAGE_SIZE = 20

const CAT_LABEL = {
  资金: '资金', 借阅: '借阅', 阅读: '阅读', 会员: '会员',
  活动: '活动', 预约: '预约', 报告: '报告', 其他: '其他',
}

function timeText(raw) {
  if (!raw) return ''
  const d = new Date(String(raw).replace(/-/g, '/'))
  if (isNaN(d.getTime())) return String(raw).slice(5, 16)
  const now = new Date()
  const pad = (n) => (n < 10 ? '0' + n : '' + n)
  const hm = pad(d.getHours()) + ':' + pad(d.getMinutes())
  const sameDay = d.toDateString() === now.toDateString()
  if (sameDay) return hm
  const yesterday = new Date(now.getTime() - 86400000)
  if (d.toDateString() === yesterday.toDateString()) return '昨天 ' + hm
  return pad(d.getMonth() + 1) + '-' + pad(d.getDate()) + ' ' + hm
}

const CAT_KEY = {
  资金: 'money', 借阅: 'borrow', 阅读: 'reading', 会员: 'member',
  活动: 'activity', 预约: 'reserve', 报告: 'report', 其他: 'other',
}

// WM15-A1：scene + ref → 路由（小程序侧此前**完全无跳转机制**，本表从零建）
// 目前只接 circle.liked（被赞 → TA 的名片页）；其余 scene 留白不跳（前端无页面可去）
function refTarget(n) {
  if (n.scene === 'circle.liked' && n.ref_type === 'child' && n.ref_id) {
    return { label: '去看 TA 的阅读名片 ›', type: 'child', id: Number(n.ref_id) }
  }
  if (n.scene === 'circle.liked' && n.ref_type === 'circle_admin') {
    return { label: '馆长亲赞！去阅读圈看看 ›', type: 'circle', id: 0 }
  }
  if (n.scene === 'circle.liked') {
    return { label: '去阅读圈看看 ›', type: 'circle', id: 0 }
  }
  return null
}

function decorate(items) {
  return (items || []).map((i) => ({
    ...i,
    categoryLabel: CAT_LABEL[i.category] || i.category || '通知',
    catKey: CAT_KEY[i.category] || 'other',
    timeText: timeText(i.created_at),
    ref: refTarget(i),
  }))
}

Page({
  data: {
    categories: CATEGORIES,
    activeCat: '',
    list: [],
    unread: 0,
    total: 0,
    loading: true,
    error: false,
    listError: '',
    expandedId: null,
  },

  onShow() {
    this._page = 1
    this.load(true)
  },

  // N4：下拉刷新
  onPullDownRefresh() {
    this._page = 1
    this.load(true).finally(() => wx.stopPullDownRefresh())
  },

  // N2：真分页（onReachBottom 加载下一页，旧列表拼接）
  onReachBottom() {
    if (this.data.loading || this.data.list.length >= this.data.total) return
    this._page += 1
    this.load(false)
  },

  switchCat(e) {
    const key = e.currentTarget.dataset.key
    if (key === this.data.activeCat) return
    // R11-d2/M4：切分类先清列表——原先失败时旧分类消息挂在新分类下
    this.setData({ activeCat: key, list: [], total: 0, listError: '' })
    this._page = 1
    this.load(true)
  },

  // W7-01：刷新/翻页失败后点提示重试（页码已在 onReachBottom 里 +1，重试即重拉该页；
  // 行内提示场景下列表非空，语义与"加载更多"一致）
  onRetryMore() {
    this.load(this.data.list.length === 0)
  },

  async toggleExpand(e) {
    const id = e.currentTarget.dataset.id
    const expanding = this.data.expandedId !== id
    this.setData({ expandedId: expanding ? id : null })
    // C46：展开未读消息即标记已读（单条），不再只靠「全部已读」
    if (expanding) {
      const target = this.data.list.find((i) => i.id === id)
      if (target && !target.read) {
        try {
          await api.markNotificationsRead([id], false)
          const list = this.data.list.map((i) => (i.id === id ? { ...i, read: true } : i))
          this.setData({ list, unread: Math.max(0, this.data.unread - 1) })
        } catch (err) { /* request.js 已 toast；已读态由下次刷新兜底 */ }
      }
    }
  },

  async load(reset) {
    // B9（2026-10-31 第一百五十二批）：请求序号——切分类/翻页共用，迟到旧响应丢弃
    // （原先切分类后旧分类响应覆盖新列表与未读数）。
    const seq = (this._loadSeq = (this._loadSeq || 0) + 1)
    this.setData({ loading: true, error: false, listError: '' })
    try {
      const r = await api.notifications(this._page, PAGE_SIZE, this.data.activeCat)
      if (seq !== this._loadSeq) return // 过期响应：分类已切换，丢弃
      const items = decorate(reset ? r.items : this.data.list.concat(r.items))
      this.setData({
        list: items,
        total: r.total,
        unread: r.unread,
        loading: false,
      })
    } catch (e) {
      if (seq !== this._loadSeq) return
      // W7-01（第十轮 P2，2026-10-24）：原先写的是 `error: !reset`——**判据取反**：
      // 首屏/切分类（reset=true）失败会置 `error=false` → 渲染成「暂无消息」（把故障说成"没消息"）；
      // 翻页失败（reset=false）反而置 `error=true`，而 wxml 的失败分支要求 `list.length===0`
      // → 翻到底失败时**永不显示**（用户零反馈）。现在：reset 失败且列表为空 → 整页失败态；
      // 列表已有数据时 → 行内「刷新/加载更多失败」提示（不清空已有数据）。
      if (reset && this.data.list.length === 0) {
        this.setData({ loading: false, error: true })
      } else {
        this.setData({
          loading: false,
          listError: reset ? '列表刷新失败' : '加载更多失败',
        })
      }
    }
  },

  // WM15-R6：通知深链——circle.liked 点「去看名片」→ 孩子名片页；
  // 历史通知（无 liker_child_id → ref_type=circle_post）降级跳阅读圈列表页
  onGoRef(e) {
    const ref = e.currentTarget.dataset.ref
    if (!ref) return
    if (ref.type === 'child') {
      wx.navigateTo({ url: `/pages/circle/profile?child_id=${ref.id}` })
    } else {
      wx.switchTab({ url: '/pages/circle/circle' })
    }
  },

  async markAllRead() {
    try {
      await api.markNotificationsRead([], true)
      const list = this.data.list.map((i) => ({ ...i, read: true }))
      this.setData({ list, unread: 0 })
      wx.showToast({ title: '已全部标记已读', icon: 'success' })
    } catch (e) { /* request.js 已 toast */ }
  },
})