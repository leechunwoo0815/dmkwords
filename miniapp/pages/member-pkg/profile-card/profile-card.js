// pages/member-pkg/profile-card/profile-card.js — 阅读护照（WM8）
const api = require('../../../utils/api')

const DEFAULT_AVATAR = '/icons/avatars/cat_sun.png'

/** 六枚里程碑资产（与 milestone_nodes 顺序一一对应；不用 emoji，见 docs/15 §21.2） */
const MILESTONE_BADGES = [
  '/icons/badges/milestone_m1.png',
  '/icons/badges/milestone_m2.png',
  '/icons/badges/milestone_m3.png',
  '/icons/badges/milestone_m4.png',
  '/icons/badges/milestone_m5.png',
  '/icons/badges/milestone_m6.png',
]

/** 词数带单位（下一枚还差多少：397680 → 39.8万，避免一排大数字） */
function fmtWords(n) {
  if (n >= 10000) return `${(n / 10000).toFixed(n % 10000 === 0 ? 0 : 1)}万`
  return `${n}`
}

function fmtDate(s) {
  const m = String(s || '').match(/^(\d{4})-(\d{2})-(\d{2})/)
  return m ? `${Number(m[2])}月${Number(m[3])}日` : ''
}

Page({
  data: {
    childName: '',
    passport: null,
    avatarUrl: DEFAULT_AVATAR,
    // 里程碑（JS 侧算好两态：WXML 表达式**不支持方法调用**，原来写
    // `passport.milestones_awarded.indexOf(item) !== -1` 恒为真 → 六枚全显示"已达成"，
    // 实测 computedStyle 六格全 rgba(.92) 实线；2026-09-21 用户问"10 万词怎么表现出来"时挖出）
    milestones: [],
    unlockedCount: 0,
    nextHint: '',
  },

  onLoad(options) {
    this.setData({ childName: decodeURIComponent(options.child_name || '') })
    this._childId = Number(options.child_id)
  },

  onShow() { this.load() },

  async load() {
    if (!this._childId) return
    try {
      const p = await api.passport(this._childId)
      // 2026-09-17：护照头像此前是写死的 emoji（🧒/📚），孩子换头像后这里不跟着变
      // （用户实测「头像没同步」）。/api/miniapp/passport 本来就回 avatar，
      // 口径与「我的」「阅读名片」两页一致，不新增接口。
      const awarded = p.milestones_awarded || []
      const dateByNode = {}
      ;(p.milestone_awards || []).forEach((a) => { dateByNode[a.node] = a.awarded_at })
      const milestones = (p.milestone_nodes || []).map((n, i) => ({
        node: n,
        label: n >= 10000 ? `${Math.round(n / 10000)}万词` : `${n}词`,
        iconUrl: MILESTONE_BADGES[i % MILESTONE_BADGES.length],
        unlocked: awarded.indexOf(n) !== -1, // 判定在 JS 里做（WXML 里调方法不生效）
        dateText: dateByNode[n] ? fmtDate(dateByNode[n]) : '',
      }))
      const next = milestones.find((m) => !m.unlocked)
      this.setData({
        passport: p,
        avatarUrl: p.avatar ? `/icons/avatars/${p.avatar}.png` : DEFAULT_AVATAR,
        milestones,
        unlockedCount: milestones.filter((m) => m.unlocked).length,
        nextHint: next
          ? `下一枚 ${next.label} · 还差 ${fmtWords(Math.max(0, next.node - (p.words_total || 0)))}词`
          : '六枚里程碑全部解锁，太厉害了',
      })
    } catch (e) { /* request.js 已 toast */ }
  },

  onShareAppMessage() {
    const p = this.data.passport
    // F-L5/T34：加载未完成/失败（passport null）时分享兜底，防 null-deref
    if (!p || !p.child_name) {
      return { title: '少儿英语阅读馆', path: '/pages/index/index' }
    }
    return {
      title: `${p.child_name} 已经有效阅读 ${p.words_total} 词啦！`,
      path: '/pages/index/index',
    }
  },

  // WM14-A：我的成就（历史成就补晒入口）
  goMyCards() {
    if (!this._childId) return
    const name = this.data.childName || (this.data.passport ? this.data.passport.child_name : '')
    wx.navigateTo({
      url: `/pages/circle/share?child_id=${this._childId}&child_name=${encodeURIComponent(name || '')}`,
    })
  },
})
