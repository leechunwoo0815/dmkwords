// pages/reading-pkg/book-detail/book-detail.js — 详情 v5：决策页（真实借阅状态 + 简介 + 状态化按钮）
const api = require('../../../utils/api')
const media = require('../../../utils/media')

function durText(sec) {
  if (!sec || sec <= 0) return ''
  const m = Math.floor(sec / 60)
  const s = Math.round(sec % 60)
  return m > 0 ? `${m} 分 ${s ? s + ' 秒' : ''}` : `${s} 秒`
}

Page({
  data: {
    book: null,
    childId: null,
    childName: '',
    progress: null,
    reserving: false,
    inFav: false,
    durationText: '',
    // 真实借阅状态：borrowing=在借中 / reserved=已预约 / none=可预约
    borrowState: 'none',
    borrowStateText: '',
    dueText: '',
  },

  onLoad(options) {
    let book = {}
    try { book = JSON.parse(decodeURIComponent(options.book || '{}')) } catch (e) { /* ignore */ }
    if (!book.id && book.book_id) book.id = Number(book.book_id)
    book = media.formatBook(book)
    const childId = options.child_id ? Number(options.child_id) : null
    this.setData({
      book,
      childId,
      childName: options.child_name ? decodeURIComponent(options.child_name) : '',
      durationText: durText(book.audio_duration),
    })
    this.loadDetail()
    if (childId) {
      this.loadProgress()
      this.checkFavorite()
      this.loadBorrowState()
    }
  },

  async loadDetail() {
    try {
      const fresh = await api.getBookDetail(this.data.book.id)
      const merged = media.formatBook({ ...this.data.book, ...fresh })
      this.setData({ book: merged, durationText: durText(merged.audio_duration) })
    } catch (e) { /* 保留传入数据兜底 */ }
  },

  async loadProgress() {
    const { book, childId } = this.data
    if (!childId) return
    try {
      const p = await api.getProgress(book.id, childId)
      this.setData({ progress: p })
    } catch (e) { /* 静默 */ }
  },

  async checkFavorite() {
    const { book, childId } = this.data
    if (!childId) return
    try {
      const list = await api.listFavorites(childId)
      this.setData({ inFav: (list || []).some((b) => (b.id || b.book_id) === book.id) })
    } catch (e) { /* 静默 */ }
  },

  // 真实借阅状态：在借 > 已预约 > 可预约
  async loadBorrowState() {
    const { book, childId } = this.data
    if (!childId) return
    try {
      const borrows = await api.currentBorrows(childId)
      const mine = (borrows || []).find((b) => (b.book_id || b.id) === book.id)
      if (mine) {
        const days = Math.ceil((new Date(String(mine.due_at).replace(/-/g, '/')) - new Date()) / 86400000)
        const due = days < 0 ? `已逾期 ${-days} 天` : days === 0 ? '今天到期' : `${days} 天后到期`
        this.setData({ borrowState: 'borrowing', borrowStateText: '这本书正在', dueText: due })
        return
      }
      const rs = await api.listReservations(childId)
      const resv = (rs || []).find((r) => r.book_id === book.id && r.status === 'active')
      if (resv) {
        this.setData({ borrowState: 'reserved', borrowStateText: '已预约成功', dueText: '72 小时内到店取书' })
        return
      }
      this.setData({ borrowState: 'none' })
    } catch (e) { /* 静默：按可预约处理 */ }
  },

  async onReserve() {
    const { book, childId, reserving, borrowState } = this.data
    if (reserving || !childId) return
    if (borrowState === 'borrowing') {
      wx.showToast({ title: '这本书已经在手上了哦', icon: 'none' }); return
    }
    if (borrowState === 'reserved') {
      wx.showToast({ title: '已预约过啦，72 小时内到店取书', icon: 'none' }); return
    }
    this.setData({ reserving: true })
    try {
      await api.createReservation(childId, book.id)
      wx.showToast({ title: '预约成功，72 小时内到店取书', icon: 'none', duration: 2500 })
      this.setData({ borrowState: 'reserved', borrowStateText: '已预约成功', dueText: '72 小时内到店取书' })
    } catch (e) { /* request.js 已 toast */ }
    finally { this.setData({ reserving: false }) }
  },

  async onFav() {
    const { book, childId, inFav } = this.data
    if (!childId) { wx.showToast({ title: '请先选择孩子', icon: 'none' }); return }
    try {
      if (inFav) {
        await api.removeFavorite(book.id, childId)
        wx.showToast({ title: '已取消收藏', icon: 'none' })
        this.setData({ inFav: false })
      } else {
        await api.addFavorite(childId, book.id)
        wx.showToast({ title: '已加入收藏（想读）', icon: 'none' })
        this.setData({ inFav: true })
      }
    } catch (e) { /* toast 已弹 */ }
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

  goShelf() { wx.switchTab({ url: '/pages/shelf/shelf' }) },
})
