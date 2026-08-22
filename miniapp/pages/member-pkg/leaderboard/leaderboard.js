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
    this._childId = Number(options.child_id)
    this.load()
  },

  onPeriod(e) {
    this.setData({ period: e.currentTarget.dataset.key })
    this.load()
  },

  async load() {
    if (!this._childId) return
    this.setData({ loading: true })
    try {
      const b = await api.leaderboard(this.data.period, this._childId)
      this.setData({ board: b })
    } catch (e) { /* request.js 已 toast（未入会等） */ }
    finally { this.setData({ loading: false }) }
  },
})
