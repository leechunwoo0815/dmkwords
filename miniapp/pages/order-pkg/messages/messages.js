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
]

const PAGE_SIZE = 20

Page({
  data: {
    categories: CATEGORIES,
    activeCat: '',
    list: [],
    unread: 0,
    total: 0,
    loading: true,
    error: false,
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
    this.setData({ activeCat: key })
    this._page = 1
    this.load(true)
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
    this.setData({ loading: true, error: false })
    try {
      const r = await api.notifications(this._page, PAGE_SIZE, this.data.activeCat)
      const items = reset ? r.items : this.data.list.concat(r.items)
      this.setData({
        list: items,
        total: r.total,
        unread: r.unread,
        loading: false,
      })
    } catch (e) {
      this.setData({ loading: false, error: !reset })
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