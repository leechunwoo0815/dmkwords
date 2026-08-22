// pages/books/books.js — 书目浏览与搜索（WM6）
const api = require('../../utils/api')
const session = require('../../utils/session')

Page({
  data: {
    keyword: '',
    books: [],
    total: 0,
    page: 1,
    loading: false,
    loaded: false,
  },

  onShow() {
    if (!session.ensureLogin()) return
    if (typeof this.getTabBar === 'function' && this.getTabBar()) {
      this.getTabBar().setData({ selected: 1 })
    }
    if (!this.data.loaded) this.reload()
  },

  onSearchInput(e) { this.setData({ keyword: e.detail.value }) },

  onSearchConfirm() { this.reload() },
  onClearSearch() { this.setData({ keyword: '' }); this.reload() },

  async reload() {
    this.setData({ page: 1, loading: true })
    try {
      const res = await api.listBooks(this.data.keyword, 1, 50)
      this.setData({ books: res.items || [], total: res.total || 0, loaded: true })
    } catch (e) {
      this.setData({ books: [], total: 0, loaded: true })
    } finally {
      this.setData({ loading: false })
    }
  },

  onReachBottom() { /* 单页 50 条足够演示；分页加载后续版本 */ },

  goDetail(e) {
    const book = e.currentTarget.dataset.book
    const child = session.getCurrentChild()
    const childParam = child ? `&child_id=${child.id}&child_name=${encodeURIComponent(child.name)}` : ''
    wx.navigateTo({
      url: `/pages/reading-pkg/book-detail/book-detail?book=${encodeURIComponent(JSON.stringify(book))}${childParam}`,
    })
  },
})
