// pages/member-pkg/leaderboard/leaderboard.js — 五榜单（WM8）
const api = require('../../../utils/api')
const session = require('../../../utils/session')

const PERIODS = [
  { key: 'week', label: '周榜' },
  { key: 'month', label: '月榜' },
  { key: 'year', label: '年榜' },
  { key: 'total', label: '总榜' },
  { key: 'progress', label: '进步榜' },
]

Page({
  data: {
    childName: '',
    periods: PERIODS,
    period: 'week',
    board: null,
    loading: false,
  },

  onLoad(options) {
    this.setData({ childName: decodeURIComponent(options.child_name || '') })
    // P3（第一百一十三批）：统一解析 + 无孩子进可见态（原先永久空白：board 为 null 时
    // 三个 wx:if 全不成立，页面只剩背景色）
    this._childId = session.resolveChildId(options)
    if (!this._childId) {
      this.setData({ noChild: true, loading: false })
      return
    }
    this.load()
  },

  onPeriod(e) {
    this.setData({ period: e.currentTarget.dataset.key })
    this.load()
  },

  onRetryLoad() { this.load() },

  async load() {
    if (!this._childId) {
      this.setData({ noChild: true, loading: false })
      return
    }
    this.setData({ loading: true, loadError: '', noChild: false })
    try {
      const b = await api.leaderboard(this.data.period, this._childId)
      this.setData({ board: b })
    } catch (e) {
      // P3（第一百一十三批）：失败必须可见（原先 catch 空转 + board 为 null → 白屏）
      this.setData({ loadError: (e && e.message) || '榜单加载失败' })
    } finally { this.setData({ loading: false }) }
  },
})
