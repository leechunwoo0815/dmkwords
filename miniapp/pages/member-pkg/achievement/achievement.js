// pages/member-pkg/achievement/achievement.js — 等级与勋章（WM7）
const api = require('../../../utils/api')

// 里程碑徽章用**自家资产**（miniapp/icons/badges/milestone_m1..m6.png，六色奖牌）代替 emoji：
// emoji 三端渲染不一致、颜色与令牌体系无关，而且 R13 只扫 WXML 字面量、看不见藏在 JS 数据里的
// emoji（2026-09-21 目视发现：本页原本是幼苗/树/山/皇冠 + 铜银金牌 emoji）。
const NODE_BADGE = [
  '/icons/badges/milestone_m1.png',
  '/icons/badges/milestone_m2.png',
  '/icons/badges/milestone_m3.png',
  '/icons/badges/milestone_m4.png',
  '/icons/badges/milestone_m5.png',
  '/icons/badges/milestone_m6.png',
]

Page({
  data: {
    childName: '',
    summary: null,
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
      const dateByNode = {}
      ;(s.milestone_awards || []).forEach((a) => { dateByNode[a.node] = a.awarded_at })
      const badges = (s.milestone_nodes || []).map((n, i) => ({
        node: n,
        label: n >= 10000 ? `${Math.round(n / 10000)}万词` : `${n}词`,
        iconUrl: NODE_BADGE[i % NODE_BADGE.length],
        unlocked: awarded.indexOf(n) !== -1,
        dateText: dateByNode[n] ? String(dateByNode[n]).slice(0, 10) : '',
      }))
      this.setData({ summary: s, badges })
    } catch (e) { /* request.js 已 toast */ }
    try {
      const pts = await api.pointsList(this._childId)
      this.setData({ points: pts.slice(0, 20) })
    } catch (e) { /* 静默 */ }
  },
})
