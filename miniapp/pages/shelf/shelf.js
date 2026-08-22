// pages/shelf/shelf.js — 书架（WM8：在借/收藏两个 tab）
const api = require('../../utils/api')
const session = require('../../utils/session')

Page({
  data: {
    tab: 'borrowed',
    borrows: [],
    favorites: [],
    childName: '',
    loading: true,
  },

  onShow() {
    if (!session.ensureLogin()) return
    if (typeof this.getTabBar === 'function' && this.getTabBar()) {
      this.getTabBar().setData({ selected: 2 })
    }
    const child = session.getCurrentChild()
    this._childId = child ? child.id : null
    this.setData({ childName: child ? child.name : '' })
    if (this._childId) this.load()
  },

  onTab(e) {
    this.setData({ tab: e.currentTarget.dataset.tab })
  },

  async load() {
    this.setData({ loading: true })
    try {
      const [borrows, favorites] = await Promise.all([
        api.currentBorrows(this._childId).catch(() => []),
        api.listFavorites(this._childId).catch(() => []),
      ])
      this.setData({ borrows: borrows || [], favorites: favorites || [] })
    } finally {
      this.setData({ loading: false })
    }
  },

  async onCancelFav(e) {
    const bookId = e.currentTarget.dataset.id
    try {
      await api.removeFavorite(bookId, this._childId)
      wx.showToast({ title: '已取消收藏', icon: 'none' })
      this.load()
    } catch (err) { /* toast 已弹 */ }
  },

  goDetail(e) {
    const book = e.currentTarget.dataset.book
    wx.navigateTo({
      url: `/pages/reading-pkg/book-detail/book-detail?book=${encodeURIComponent(JSON.stringify(book))}`
        + `&child_id=${this._childId}&child_name=${encodeURIComponent(this.data.childName)}`,
    })
  },
})
