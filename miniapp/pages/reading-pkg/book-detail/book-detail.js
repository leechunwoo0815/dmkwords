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
    this._inited = true
  },

  onShow() {
    // B1（插修5）：从 reader/quiz 返回即刷新三态（onLoad 只在首次进入触发——
    // "退出重进 2 次才显示已读完"根因即 onLoad-only 拉取）
    if (this._inited) {
      const childId = this.data.childId
      if (childId) {
        this.loadProgress()
        this.checkFavorite()
        this.loadBorrowState()
      }
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
      // B3（插修5）：coverage 是防刷口径（95% 阈值即判 finished），显示层以
      // finished 为权威态——读完的书进度条满格 100%（后端防刷口径不动）
      p.displayPercent = p.finished ? 100 : p.coverage_percent
      this.setData({ progress: p })
    } catch (e) { /* 静默 */ }
  },

  async checkFavorite() {
    const { book, childId } = this.data
    if (!childId) return
    try {
      const list = await api.listFavorites(childId)
      // B2（插修5）：精确比对 book_id——原 (b.id || b.book_id) 短路赌博，b.id 是收藏行自增 id，比对纯碰撞
      this.setData({ inFav: (list || []).some((b) => b.book_id === book.id) })
    } catch (e) { /* 静默（U10 同族假状态登记，修复归 F-M 家族追踪——不扩 scope） */ }
  },

  // 真实借阅状态：在借 > 已预约 > 可预约
  async loadBorrowState() {
    const { book, childId } = this.data
    if (!childId) return
    try {
      const borrows = await api.currentBorrows(childId)
      // B2 同族：current_borrows 恒返 book_id（原 || 碰巧正确），精确化消除赌博
      const mine = (borrows || []).find((b) => b.book_id === book.id)
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
      // R1 同族 sweep：此入口原也漏 child_id（quiz 页解析 NaN → 422）
      url: `/pages/reading-pkg/quiz/quiz?book_id=${book.id}&book_title=${encodeURIComponent(book.title)}` +
        `&child_id=${this.data.childId || ''}&child_name=${encodeURIComponent(this.data.childName || '')}`
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
