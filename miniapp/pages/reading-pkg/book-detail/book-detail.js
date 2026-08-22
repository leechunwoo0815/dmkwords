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
    this.loadProgress()
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
