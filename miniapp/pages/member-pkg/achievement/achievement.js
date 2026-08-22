// pages/member-pkg/achievement/achievement.js — 等级与勋章（WM7）
const api = require('../../../utils/api')

const NODE_EMOJI = ['🌱', '🌿', '🌳', '🌲', '🏔️', '👑']
const LEVEL_EMOJI = { A: '🥉', B: '🥈', C: '🥇', D: '🏅', E: '🎖️' }

Page({
  data: {
    childName: '',
    summary: null,
    levelEmoji: '🥉',
    badges: [],
    points: [],
  },

  onLoad(options) {
    this.setData({ childName: decodeURIComponent(options.child_name || '') })
    this._childId = Number(options.child_id)
  },

  onShow() { this.load() },

  async load() {
    if (!this._childId) return
    try {
      const s = await api.growthSummary(this._childId)
      const awarded = s.milestones_awarded || []
      const badges = (s.milestone_nodes || []).map((n, i) => ({
        node: n,
        label: n >= 10000 ? `${Math.round(n / 10000)}万词` : `${n}词`,
        emoji: NODE_EMOJI[i % NODE_EMOJI.length],
        unlocked: awarded.indexOf(n) !== -1,
      }))
      this.setData({
        summary: s,
        levelEmoji: LEVEL_EMOJI[s.level] || '🎖️',
        badges,
      })
    } catch (e) { /* request.js 已 toast */ }
    try {
      const pts = await api.pointsList(this._childId)
      this.setData({ points: pts.slice(0, 20) })
    } catch (e) { /* 静默 */ }
  },
})
