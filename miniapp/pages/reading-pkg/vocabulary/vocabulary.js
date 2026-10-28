// pages/reading-pkg/vocabulary/vocabulary.js — 生词本（WM8）
// 2026-09-21 呈现重排（用户裁定「分不清哪个是我查过的词 / 颜色和书名一样 / 没有分行 / 字体不大 /
// 要有统计次数 / 点词弹闪卡」）：成就头卡 + 四指标 + 每行书封锚点 + 三层信息 + 长按移除。
// 2026-10-16（第六十四批）：统计三口径（收录词数/累计查词/来源书目）改**服务端聚合**
// （`GET /api/miniapp/vocabulary/summary`，PRD §8.1.1 实现说明）——前端不再 reduce/Set 自算。
// P3（第一百一十六批）：列表改**分页**（后端同步分页；原实现整表回吐 + 整表 setData），
// 并把「本周新增/查过多次」也下沉服务端——分页后前端滤波只剩当前页结果，口径必漂移。
// 口径：docs/15 §二十一 成就类页面呈现规范。
const api = require('../../../utils/api')
const session = require('../../../utils/session')

/** 后端给 `str(datetime)`，列表只显示到日（成就页看"攒了多久"，不看时分秒）。 */
function fmtDate(s) {
  const m = String(s || '').match(/^(\d{4})-(\d{2})-(\d{2})/)
  return m ? `${Number(m[2])}月${Number(m[3])}日` : ''
}

Page({
  data: {
    childName: '',
    words: [],
    loading: true,
    loadError: false,
    // W7-11（2026-10-28）：无 child_id 时必须落到可见态（原先早退在 loading 之前 → 永久"加载中"）
    noChild: false,
    // 统计五口径（收录词数 / 累计查词 / 来源书目 / 本周新增 / 查过多次）**全部服务端聚合**
    // （PRD §8.1.1 实现说明）：列表分页后，前端 reduce 出来的数只剩当前页结果，
    // 与真实口径漂移（P3 第一百一十六批把「本周新增/查过多次」也下沉）。
    wordCount: 0,
    totalLookups: 0,
    bookCount: 0,
    weekCount: 0,
    repeatCount: 0,
    // 分页游标（生词本只增不减：原实现整表 setData，重度用户会卡帧）
    page: 1,
    hasMore: false,
    loadingMore: false,
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
    // W7-11：统一走 session 解析（缺参/非法值回落当前孩子），再决定是否进入加载
    const me = session.getCurrentChild ? session.getCurrentChild() : null
    const fromUrl = Number(options.child_id)
    this._childId = fromUrl || (me && me.id ? me.id : 0) || null
    this.setData({
      childName: decodeURIComponent(options.child_name || '') || (me ? me.name : ''),
      noChild: !this._childId,
      loading: !!this._childId,
    })
  },

  onShow() { if (this._childId) this.load(); else this.setData({ loading: false, noChild: true }) },

  // 触底加载下一页（页数由 `wordCount` 服务端口径判定到底，不猜）
  onReachBottom() { this.loadMore() },

  async loadMore() {
    if (!this.data.hasMore || this.data.loadingMore) return
    this.setData({ loadingMore: true })
    try {
      const next = this.data.page + 1
      const rows = await api.listVocabulary(this._childId, next)
      const more = (rows || []).map((w) => ({ ...w, dateText: fmtDate(w.created_at) }))
      const words = this.data.words.concat(more)
      this.setData({
        words,
        page: next,
        hasMore: more.length > 0 && words.length < this.data.wordCount,
      })
    } catch (e) {
      // 加载更多失败：**不清空已加载内容**，如实提示（"禁假 0"同族：不许把失败画成"没有了"）
      wx.showToast({ title: '加载更多失败，请上滑重试', icon: 'none' })
    } finally {
      this.setData({ loadingMore: false })
    }
  },

  async load() {
    if (!this._childId) {
      this.setData({ loading: false, noChild: true })
      return
    }
    this.setData({ loading: true, loadError: false })
    try {
      // 五口径走服务端聚合（两次请求并发，少一个 RTT）
      const [rows, stats] = await Promise.all([
        api.listVocabulary(this._childId, 1),
        api.summaryVocabulary(this._childId),
      ])
      const list = (rows || []).map((w) => ({ ...w, dateText: fmtDate(w.created_at) }))
      const wordCount = Number(stats.word_count) || 0
      this.setData({
        words: list,
        page: 1,
        hasMore: list.length < wordCount,
        wordCount,
        totalLookups: Number(stats.lookup_total) || 0,
        bookCount: Number(stats.book_count) || 0,
        weekCount: Number(stats.week_count) || 0,
        repeatCount: Number(stats.repeat_count) || 0,
        heroSub: this._heroSub(wordCount),
      })
    } catch (e) {
      // F-M12/T26：fetch 失败进错误态（点击重试），不再静默置空渲染为合法空态（禁假 0）
      this.setData({
        words: [], wordCount: 0, totalLookups: 0, bookCount: 0,
        weekCount: 0, repeatCount: 0, page: 1, hasMore: false, loadError: true,
      })
    } finally {
      this.setData({ loading: false })
    }
  },

  /** 鼓励语按收录量分档——成就页的"第一句话"不该是干巴巴的计数。 */
  _heroSub(n) {
    if (!n) return '阅读时查过的词会自动攒到这里'
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
