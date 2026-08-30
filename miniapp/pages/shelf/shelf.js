// pages/shelf/shelf.js — 书架 v5：真实资产页（在借中 / 收藏 / 预约中，全部后端数据）
const api = require('../../utils/api')
const session = require('../../utils/session')
const media = require('../../utils/media')

const RES_STATUS_TEXT = {
  active: '锁定中 · 72h 内到店取书',
  expired: '已超时释放',
  cancelled: '已取消',
  checked_out: '已核销转借阅',
  exception: '副本异常，请联系馆员',
}

Page({
  data: {
    tab: 'borrows',
    borrows: [],
    favorites: [],
    reservations: [],
    borrowCount: 0,
    favCount: 0,
    resCount: 0,
    childId: null,
    childName: '',
    loading: true,
  },

  onShow() {
    if (!session.ensureLogin()) return
    if (typeof this.getTabBar === 'function' && this.getTabBar()) {
      this.getTabBar().setData({ selected: 2 })
    }
    const child = session.getCurrentChild()
    const childId = child ? child.id : null
    this.setData({ childId, childName: child ? child.name : '' })
    if (childId) this.load()
  },

  onTab(e) {
    this.setData({ tab: e.currentTarget.dataset.tab })
  },

  async load() {
    this.setData({ loading: true })
    try {
      const [borrows, favorites, reservations] = await Promise.all([
        api.currentBorrows(this.data.childId).catch(() => []),
        api.listFavorites(this.data.childId).catch(() => []),
        api.listReservations(this.data.childId).catch(() => []),
      ])
      this.setData({
        borrows: (borrows || []).map((b) => ({
          ...media.formatBook(b),
          record_id: b.record_id,
          dueText: this.dueText(b),
          overdue: !!b.overdue,
        })),
        borrowCount: (borrows || []).length,
        favorites: media.formatBooks(favorites || []),
        favCount: (favorites || []).length,
        reservations: (reservations || []).map((r) => ({
          ...media.formatBook(r),
          statusText: RES_STATUS_TEXT[r.status] || r.status,
          active: r.status === 'active',
        })),
        resCount: (reservations || []).length,
      })
    } finally {
      this.setData({ loading: false })
    }
  },

  dueText(borrow) {
    if (!borrow.due_at) return ''
    const days = Math.ceil((new Date(String(borrow.due_at).replace(/-/g, '/')) - new Date()) / 86400000)
    if (days < 0) return `已逾期 ${-days} 天`
    if (days === 0) return '今天到期'
    return `${days} 天后到期`
  },

  async onCancelFav(e) {
    const bookId = e.currentTarget.dataset.id
    try {
      await api.removeFavorite(bookId, this.data.childId)
      wx.showToast({ title: '已取消收藏', icon: 'none' })
      this.load()
    } catch (err) { /* toast 已弹 */ }
  },

  async onCancelReservation(e) {
    const id = e.currentTarget.dataset.id
    const that = this
    wx.showModal({
      title: '取消预约',
      content: '确定取消这条预约吗？副本将释放给其他读者。',
      confirmColor: '#FF6B35',
      success(res) {
        if (!res.confirm) return
        api.cancelReservation(id, that.data.childId).then(function () {
          wx.showToast({ title: '已取消', icon: 'none' })
          that.load()
        })
      },
    })
  },

  goListen(e) {
    const b = e.currentTarget.dataset.book
    const c = session.getCurrentChild()
    if (!b || !c) return
    wx.navigateTo({
      url: `/pages/reading-pkg/reader/reader?book=${encodeURIComponent(JSON.stringify(b))}&child_id=${c.id}`,
    })
  },

  goDetail(e) {
    const book = e.currentTarget.dataset.book
    if (!book) return
    wx.navigateTo({
      url: `/pages/reading-pkg/book-detail/book-detail?book=${encodeURIComponent(JSON.stringify(book))}`
        + `&child_id=${this.data.childId}&child_name=${encodeURIComponent(this.data.childName)}`,
    })
  },

  goBooks() { wx.switchTab({ url: '/pages/books/books' }) },
})
