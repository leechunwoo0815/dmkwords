// pages/books/books.js — 图书馆 v5：2000 本规模检索台（搜索 + 筛选 + 排序 + 真分页）
const api = require('../../utils/api')
const session = require('../../utils/session')
const media = require('../../utils/media')

const PAGE_SIZE = 20

const GRADE_OPTIONS = [
  '全部年级', '3-4岁（幼儿园）', '5-6岁（幼儿园大班）', '7-8岁（小学低年级）',
  '9-10岁（小学中年级）', '11-12岁（小学高年级）', '13-15岁（初中）',
]
const TOPIC_OPTIONS = [
  '全部主题', '韵文启蒙', '自然认知', '想象力', '幽默桥梁书',
  '奇幻章节书', '科普百科', '成长故事', '侦探冒险',
]
const AR_OPTIONS = ['全部 AR', 'AR 0-2', 'AR 2-3', 'AR 3-4', 'AR 4 以上']
const AR_RANGES = [[null, null], [0, 2], [2, 3], [3, 4], [4, null]]
const SORT_OPTIONS = ['最新上架', 'AR 低→高', 'AR 高→低', '词数少→多', '词数多→少']
const SORT_KEYS = ['newest', 'ar_asc', 'ar_desc', 'words_asc', 'words_desc']

Page({
  data: {
    keyword: '',
    gradeOptions: GRADE_OPTIONS,
    topicOptions: TOPIC_OPTIONS,
    arOptions: AR_OPTIONS,
    sortOptions: SORT_OPTIONS,
    gradeIdx: 0,
    topicIdx: 0,
    arIdx: 0,
    sortIdx: 0,
    gradeLabel: '',
    topicLabel: '',
    arLabel: '',
    sortLabel: '',
    hasAudio: false,
    activeChips: [],
    books: [],
    total: 0,
    page: 1,
    loading: false,
    loaded: false,
    noMore: false,
    loadError: false,
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

  syncChips(filters) {
    const chips = []
    if (filters.grade) chips.push({ key: 'grade', label: filters.grade.split('（')[0] })
    if (filters.topic) chips.push({ key: 'topic', label: filters.topic })
    if (filters.ar_min !== null || filters.ar_max !== null) {
      const lo = filters.ar_min === null ? '' : filters.ar_min
      const hi = filters.ar_max === null ? '' : filters.ar_max
      chips.push({ key: 'ar', label: `AR ${lo || '0'}-${hi || '+'}` })
    }
    if (filters.sort !== 'newest') {
      chips.push({ key: 'sort', label: SORT_OPTIONS[SORT_KEYS.indexOf(filters.sort)] })
    }
    if (filters.has_audio) chips.push({ key: 'audio', label: '🎧 有音频' })
    this.setData({ activeChips: chips })
  },

  currentFilters() {
    const [ar_min, ar_max] = AR_RANGES[this.data.arIdx]
    return {
      grade: this.data.gradeIdx > 0 ? GRADE_OPTIONS[this.data.gradeIdx] : '',
      topic: this.data.topicIdx > 0 ? TOPIC_OPTIONS[this.data.topicIdx] : '',
      ar_min,
      ar_max,
      has_audio: this.data.hasAudio,
      sort: SORT_KEYS[this.data.sortIdx],
    }
  },

  onGradeChange(e) {
    const idx = Number(e.detail.value)
    this.setData({ gradeIdx: idx, gradeLabel: idx > 0 ? GRADE_OPTIONS[idx].split('（')[0] : '' })
    this.reload()
  },
  onTopicChange(e) {
    const idx = Number(e.detail.value)
    this.setData({ topicIdx: idx, topicLabel: idx > 0 ? TOPIC_OPTIONS[idx] : '' })
    this.reload()
  },
  onArChange(e) {
    const idx = Number(e.detail.value)
    this.setData({ arIdx: idx, arLabel: idx > 0 ? AR_OPTIONS[idx] : '' })
    this.reload()
  },
  onSortChange(e) {
    const idx = Number(e.detail.value)
    this.setData({ sortIdx: idx, sortLabel: idx > 0 ? SORT_OPTIONS[idx] : '' })
    this.reload()
  },
  onToggleAudio() { this.setData({ hasAudio: !this.data.hasAudio }); this.reload() },

  onRemoveChip(e) {
    const key = e.currentTarget.dataset.key
    if (key === 'grade') this.setData({ gradeIdx: 0, gradeLabel: '' })
    else if (key === 'topic') this.setData({ topicIdx: 0, topicLabel: '' })
    else if (key === 'ar') this.setData({ arIdx: 0, arLabel: '' })
    else if (key === 'sort') this.setData({ sortIdx: 0, sortLabel: '' })
    else if (key === 'audio') this.setData({ hasAudio: false })
    this.reload()
  },

  onClearAll() {
    this.setData({ gradeIdx: 0, topicIdx: 0, arIdx: 0, sortIdx: 0, hasAudio: false, gradeLabel: '', topicLabel: '', arLabel: '', sortLabel: '' })
    this.reload()
  },

  async reload() {
    const filters = this.currentFilters()
    this.syncChips(filters)
    this.setData({ page: 1, loading: true, noMore: false, loadError: false })
    try {
      const res = await api.listBooks({ keyword: this.data.keyword, page: 1, page_size: PAGE_SIZE, ...filters })
      this.setData({
        books: media.formatBooks(res.items || []),
        total: res.total || 0,
        loaded: true,
        noMore: (res.items || []).length < PAGE_SIZE,
      })
    } catch (e) {
      // F-M12/T26：fetch 失败进错误态（点击重试），不再静默置空渲染为合法空态（禁假 0）
      this.setData({ loadError: true, books: [], total: 0, loaded: true, noMore: true })
    } finally {
      this.setData({ loading: false })
    }
  },

  onRetryLoad() {
    this.reload()
  },

  async onReachBottom() {
    if (this.data.loading || this.data.noMore || !this.data.loaded) return
    const next = this.data.page + 1
    this.setData({ loading: true })
    try {
      const res = await api.listBooks({ keyword: this.data.keyword, page: next, page_size: PAGE_SIZE, ...this.currentFilters() })
      const items = media.formatBooks(res.items || [])
      this.setData({
        books: this.data.books.concat(items),
        page: next,
        noMore: items.length < PAGE_SIZE,
      })
    } catch (e) { /* 加载失败保持当前页 */ } finally {
      this.setData({ loading: false })
    }
  },

  goDetail(e) {
    const book = e.currentTarget.dataset.book
    const child = session.getCurrentChild()
    const childParam = child ? `&child_id=${child.id}&child_name=${encodeURIComponent(child.name)}` : ''
    wx.navigateTo({
      url: `/pages/reading-pkg/book-detail/book-detail?book=${encodeURIComponent(JSON.stringify(book))}${childParam}`,
    })
  },
})
