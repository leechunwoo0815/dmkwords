// pages/reading-pkg/vocabulary/vocabulary.js — 生词本（WM8）
const api = require('../../../utils/api')

Page({
  data: {
    childName: '',
    words: [],
    loading: true,
    loadError: false,
    // 统计（2026-09-15）：生词本是孩子的成就面，先给「收录/查词/书目」三个数
    totalLookups: 0,
    bookCount: 0,
    // 闪卡（点单词弹释义；释义/音标来自后端 join 的词典）
    cardWord: '',
    cardPhonetic: '',
    cardDefinition: '',
    cardTranslation: '',
    cardSource: '',
    cardId: null,
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
      const list = words || []
      // 统计在前端汇总：列表本就整份返回，不必再加接口/改契约
      const totalLookups = list.reduce((sum, w) => sum + (Number(w.lookup_count) || 1), 0)
      const bookCount = new Set(list.map(w => w.source_title).filter(Boolean)).size
      this.setData({ words: list, totalLookups, bookCount })
    } catch (e) {
      // F-M12/T26：fetch 失败进错误态（点击重试），不再静默置空渲染为合法空态（禁假 0）
      this.setData({ words: [], totalLookups: 0, bookCount: 0, loadError: true })
    } finally {
      this.setData({ loading: false })
    }
  },

  onRetryLoad() { this.load() },

  // 点单词 → 闪卡（同一张卡里看音标/释义/来源书）
  onOpenCard(e) {
    const item = this.data.words[Number(e.currentTarget.dataset.index)]
    if (!item) return
    this.setData({
      cardWord: item.word,
      cardPhonetic: item.phonetic || '',
      cardDefinition: item.definition || '',
      cardTranslation: item.translation || '',
      cardSource: item.source_title || '',
      cardId: item.id,
    })
  },

  onCloseCard() {
    this.setData({ cardWord: '', cardPhonetic: '', cardDefinition: '', cardTranslation: '', cardSource: '', cardId: null })
  },

  // 闪卡内层吸收点击（否则点到卡片也会关掉）
  noop() {},

  onRemoveCard() {
    const id = this.data.cardId
    const word = this.data.cardWord
    this.onCloseCard()
    this._doRemove(id, word)
  },

  onRemove(e) {
    const { id, word } = e.currentTarget.dataset
    this._doRemove(id, word)
  },

  async _doRemove(id, word) {
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
