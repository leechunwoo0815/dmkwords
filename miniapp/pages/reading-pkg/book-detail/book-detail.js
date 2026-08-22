// pages/reading-pkg/book-detail/book-detail.js — 书籍详情（WM6：进度/测验解锁标识/预约）
const api = require('../../../utils/api')

Page({
  data: {
    book: null,
    childId: null,
    childName: '',
    progress: null,
    reserving: false,
    hasActiveReservation: false,
  },

  onLoad(options) {
    let book = {}
    try { book = JSON.parse(decodeURIComponent(options.book || '{}')) } catch (e) { /* ignore */ }
    this.setData({
      book,
      childId: options.child_id ? Number(options.child_id) : null,
      childName: options.child_name ? decodeURIComponent(options.child_name) : '',
    })
    this.loadDetail()
    this.loadProgress()
  },

  async loadDetail() {
    // 以服务端详情为准（书架收藏/在借进入时 URL 参数缺 audio_url 等字段）
    try {
      const fresh = await api.getBookDetail(this.data.book.id)
      this.setData({ book: { ...this.data.book, ...fresh } })
    } catch (e) { /* 保留传入数据兜底 */ }
  },

  async loadProgress() {
    const { book, childId } = this.data
    if (!childId) return
    try {
      const p = await api.getProgress(book.id, childId)
      this.setData({ progress: p })
    } catch (e) { /* 静默：新书无进度 */ }
  },

  async onReserve() {
    const { book, childId, reserving } = this.data
    if (reserving || !childId) return
    this.setData({ reserving: true })
    try {
      await api.createReservation(childId, book.id)
      wx.showToast({ title: '预约成功，72 小时内到店取书', icon: 'none', duration: 2500 })
    } catch (e) { /* request.js 已 toast 原因（无副本/额度满/重复预约等） */ }
    finally { this.setData({ reserving: false }) }
  },

  async onFav() {
    const { book, childId } = this.data
    if (!childId) { wx.showToast({ title: '请先选择孩子', icon: 'none' }); return }
    try {
      await api.addFavorite(childId, book.id)
      wx.showToast({ title: '已加入收藏夹（想读）', icon: 'none' })
    } catch (e) { /* toast 已弹（重复收藏等） */ }
  },

  onQuiz() {
    const { book, childId, childName, progress } = this.data
    if (!childId) { wx.showToast({ title: '请先选择孩子', icon: 'none' }); return }
    if (!progress || !progress.finished) {
      wx.showToast({ title: '需先听完音频（95%）才解锁测验', icon: 'none' }); return
    }
    wx.navigateTo({
      url: `/pages/reading-pkg/quiz/quiz?book_id=${book.id}&book_title=${encodeURIComponent(book.title)}`
        + `&child_id=${childId}&child_name=${encodeURIComponent(childName)}`,
    })
  },

  onPlay() {
    const { book, childId, childName } = this.data
    if (!childId) { wx.showToast({ title: '请先选择孩子', icon: 'none' }); return }
    if (!book.has_audio) { wx.showToast({ title: '该书暂无音频', icon: 'none' }); return }
    wx.navigateTo({
      url: `/pages/reading-pkg/reader/reader?book=${encodeURIComponent(JSON.stringify(book))}`
        + `&child_id=${childId}&child_name=${encodeURIComponent(childName)}`,
    })
  },
})
