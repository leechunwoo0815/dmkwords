// pages/reading-pkg/vocabulary/vocabulary.js — 生词本（WM8）
const api = require('../../../utils/api')

Page({
  data: {
    childName: '',
    words: [],
    loading: true,
    loadError: false,
  },

  onLoad(options) {
    this.setData({ childName: decodeURIComponent(options.child_name || '') })
    this._childId = Number(options.child_id)
  },

  onShow() { this.load() },

  async load() {
    if (!this._childId) return
    this.setData({ loading: true, loadError: false })
    try {
      const words = await api.listVocabulary(this._childId)
      this.setData({ words: words || [] })
    } catch (e) {
      // F-M12/T26：fetch 失败进错误态（点击重试），不再静默置空渲染为合法空态（禁假 0）
      this.setData({ words: [], loadError: true })
    } finally {
      this.setData({ loading: false })
    }
  },

  onRetryLoad() { this.load() },

  async onRemove(e) {
    const id = e.currentTarget.dataset.id
    const word = e.currentTarget.dataset.word
    const res = await wx.showModal({
      title: '删除生词', content: `把「${word}」从生词本删除？`, confirmText: '删除',
    })
    if (!res.confirm) return
    try {
      await api.removeVocabulary(id, this._childId)
      this.load()
    } catch (err) { /* toast 已弹 */ }
  },
})
