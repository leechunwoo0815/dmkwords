// pages/reading-pkg/vocabulary/vocabulary.js — 生词本（WM8）
// 2026-09-21 呈现重排（用户裁定「分不清哪个是我查过的词 / 颜色和书名一样 / 没有分行 / 字体不大 /
// 要有统计次数 / 点词弹闪卡」）：成就头卡 + 四指标 + 每行书封锚点 + 三层信息 + 长按移除。
// 口径：docs/15 §二十一 成就类页面呈现规范。
const api = require('../../../utils/api')

/** 后端给 `str(datetime)`，列表只显示到日（成就页看"攒了多久"，不看时分秒）。 */
function fmtDate(s) {
  const m = String(s || '').match(/^(\d{4})-(\d{2})-(\d{2})/)
  return m ? `${Number(m[2])}月${Number(m[3])}日` : ''
}

/** 一周内收录 = 本周新增（纯前端现算，不加接口/不加表）。 */
function isThisWeek(s) {
  const t = Date.parse(String(s || '').replace(' ', 'T'))
  if (Number.isNaN(t)) return false
  return Date.now() - t < 7 * 24 * 3600 * 1000
}

Page({
  data: {
    childName: '',
    words: [],
    loading: true,
    loadError: false,
    // 统计：收录数在 words.length；其余前端汇总（列表整份返回，不必加接口/改契约）
    totalLookups: 0,
    bookCount: 0,
    weekCount: 0,
    repeatCount: 0,
    heroSub: '',
    // 闪卡（点单词弹释义；释义/音标来自后端 join 的词典）
    cardWord: '',
    cardPhonetic: '',
    cardDefinition: '',
    cardTranslation: '',
    cardSource: '',
    cardDate: '',
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
      const rows = await api.listVocabulary(this._childId)
      const list = (rows || []).map((w) => ({ ...w, dateText: fmtDate(w.created_at) }))
      const totalLookups = list.reduce((sum, w) => sum + (Number(w.lookup_count) || 1), 0)
      const bookCount = new Set(list.map((w) => w.source_title).filter(Boolean)).size
      const weekCount = list.filter((w) => isThisWeek(w.created_at)).length
      const repeatCount = list.filter((w) => (Number(w.lookup_count) || 1) > 1).length
      this.setData({
        words: list,
        totalLookups,
        bookCount,
        weekCount,
        repeatCount,
        heroSub: this._heroSub(list.length),
      })
    } catch (e) {
      // F-M12/T26：fetch 失败进错误态（点击重试），不再静默置空渲染为合法空态（禁假 0）
      this.setData({ words: [], totalLookups: 0, bookCount: 0, weekCount: 0, repeatCount: 0, loadError: true })
    } finally {
      this.setData({ loading: false })
    }
  },

  /** 鼓励语按收录量分档——成就页的"第一句话"不该是干巴巴的计数。 */
  _heroSub(n) {
    if (!n) return '听书时查过的词会自动攒到这里'
    if (n < 5) return '刚刚起步，每查一个词都算数'
    if (n < 20) return '已经攒出一小本了，继续加油'
    if (n < 50) return '词汇量在悄悄长大，很厉害'
    return '这是你自己的词典，了不起'
  },

  onRetryLoad() { this.load() },

  // 点单词 → 闪卡（同一张卡里看音标/中英释义/来源书）
  onOpenCard(e) {
    // 长按后 600ms 内的 tap 一律忽略：bindlongpress 与 bindtap 挂在同一节点上，
    // 部分基础库在长按结束时仍补一次 tap → 会出现"操作表后面又浮出闪卡"的叠层
    if (Date.now() - (this._lpAt || 0) < 600) return
    const item = this.data.words[Number(e.currentTarget.dataset.index)]
    if (!item) return
    this.setData({
      cardWord: item.word,
      cardPhonetic: item.phonetic || '',
      cardDefinition: item.definition || '',
      cardTranslation: item.translation || '',
      cardSource: item.source_title || '',
      cardDate: item.dateText || '',
      cardId: item.id,
    })
  },

  onCloseCard() {
    this.setData({
      cardWord: '', cardPhonetic: '', cardDefinition: '', cardTranslation: '',
      cardSource: '', cardDate: '', cardId: null,
    })
  },

  // 闪卡内层吸收点击（否则点到卡片也会关掉）
  noop() {},

  onRemoveCard() {
    const id = this.data.cardId
    const word = this.data.cardWord
    this.onCloseCard()
    this._doRemove(id, word)
  },

  /** 长按词条 → 操作表（行内不放删除按钮：成就页每行一个删除＝噪音+误触，docs/15 §二十一 红线） */
  onLongPress(e) {
    this._lpAt = Date.now() // 供 onOpenCard 挡掉长按尾部补发的 tap
    const item = this.data.words[Number(e.currentTarget.dataset.index)]
    if (!item) return
    wx.showActionSheet({
      itemList: ['移出生词本'],
      success: (res) => {
        if (res.tapIndex === 0) this._doRemove(item.id, item.word)
      },
      fail: () => {}, // 用户取消不报错
    })
  },

  async _doRemove(id, word) {
    const res = await wx.showModal({
      title: '移出生词本', content: `把「${word}」移出生词本？之后再查还会自动收回来。`, confirmText: '移出',
    })
    if (!res.confirm) return
    try {
      await api.removeVocabulary(id, this._childId)
      this.load()
    } catch (err) { /* toast 已弹 */ }
  },
})
