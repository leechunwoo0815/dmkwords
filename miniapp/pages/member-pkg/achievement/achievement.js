// pages/member-pkg/achievement/achievement.js — 等级与勋章（WM7）
const api = require('../../../utils/api')
const session = require('../../../utils/session')

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
    // W7-03（第十轮 P2，2026-10-24）：两个接口失败原先都是**静默 catch** → level 卡被
    // `wx:if="{{summary}}"` 藏掉、勋章网格空、积分区落到「还没有积分记录」——孩子会以为
    // 等级/积分/勋章被清零（实测同一账号其实有 1133 分流水、10 枚勋章）。
    // 现在失败落可见态（error-view + 重试），并门控空态（`!loadError` 才说"还没有"）。
    loadError: '',
  },

  onLoad(options) {
    // W7-12：缺参回落到当前孩子
    this.setData({ childName: decodeURIComponent(options.child_name || '') || ((session.getCurrentChild() || {}).name || '') })
    // W7-03：与 checkin / leaderboard / report / profile-card 对齐，走统一解析
    // （原先裸 `Number(options.child_id)`：无参时 NaN → load() 直接 return = 永久空页）
    this._childId = session.resolveChildId(options)
  },

  onShow() { this.load() },

  onRetryLoad() { this.load() },

  async load() {
    if (!this._childId) {
      this.setData({ loadError: '没有选择孩子（请先在「我的」里添加或选择孩子）' })
      return
    }
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
      // 2026-10-30 大审查 P2-4：成功分支必须清失败态——否则一次网络失败后，
      // 重试成功仍被 wxml 的 `wx:if="{{loadError}}"` 整页挡住（看起来像重试没反应）。
      this.setData({ summary: s, badges, loadError: '' })
    } catch (e) {
      this.setData({
        summary: null, badges: [],
        loadError: (e && e.message) || '成长数据加载失败',
      })
    }
    try {
      const pts = await api.pointsList(this._childId)
      this.setData({ points: pts.slice(0, 20) })
    } catch (e) {
      this.setData({
        points: [],
        // 任一失败就整页可见失败态（绝不被渲染成"还没有积分记录"）
        loadError: this.data.loadError || (e && e.message) || '积分记录加载失败',
      })
    }
  },
})
